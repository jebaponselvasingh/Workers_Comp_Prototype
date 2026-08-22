"""The purge cascade's only invocation surface (Story 8.1, AD-11).

    uv run python -m scripts.purge_claim WC-20017
    uv run python -m scripts.purge_claim --user 7
    docker compose -f deploy/compose.e2e.yaml exec -T api \\
        uv run --no-dev python -m scripts.purge_claim WC-20017

`scripts/dump_openapi.py` is the shape — a `main()` and an `if __name__`. What
is different is that this one has a database, a checkpointer and (optionally) a
blob store behind it, which makes it the composition root for the cascade rather
than a thin wrapper over one.

## Why there is no HTTP endpoint and no UI

Story 8.1's Dev Notes are explicit: the deliverable is the `services/audit`
command plus its jobs, and admin exposure "rides the deferred IdP decision".
That decision is why `api/app.py` refuses to boot under `ENV=prod` at all —
persona login mints a session from an unauthenticated id — and a purge route
behind that authentication would be a delete-everything button behind a
dropdown. A management command run by somebody with a shell on the host is the
authorisation model until there is a real one.

## Why the composition root is here rather than in `services/audit/purge.py`

The story's task list writes the invocation as `python -m
server.services.audit.purge WC-nnnn`. That path is wrong twice over — module
paths in this project are rooted at `server/`, so the `server.` prefix does not
resolve — and the second reason is the load-bearing one: wiring a checkpointer
means importing `agents/`, and `tests/test_layering.py::test_no_module_under_
services_imports_agents` forbids that inside `services/`. `scripts/` is outside
that rule and already holds this project's other management command, so the
cascade stays a pure service function and this file is the one place that knows
how to build a saver.

## Resources, and why every one of them is closed in a `finally`

Four things are opened here — a SQLAlchemy engine, a psycopg pool, the saver
over it, and (inside the cascade) a second engine for the redactor connection —
and a management command that leaked any of them would leak them on a host, in a
shell, under an operator who has just been told a purge succeeded. `lifespan`'s
teardown order is copied deliberately: the checkpoint pool closes before the
engine disposes, because a pool closed after its database handles were torn down
surfaces as a psycopg error at exit with no obvious cause.

## Output

One JSON line on stdout, from `PurgeRun.as_json`, which is content-free by
construction: counts and store names, never a value. Errors go to stderr as one
sentence and a non-zero exit — and the sentence for an unknown claim is the
business id and nothing else, because a command that answered "no such claim,
did you mean…" would be an enumeration oracle on a host where somebody is
already worried about what is in the database.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agents.threads import discard_thread
from config import Settings, get_settings
from logging_config import configure_logging
from services.audit.purge import (
    BlobStoreRequired,
    ClaimNotFound,
    PurgeRun,
    RedactorUnavailable,
    SystemActorRefused,
    UserNotFound,
    purge_claim,
    purge_user,
)
from services.blobstore import BlobStore, VolumeBlobStore
from services.financials.batch import system_context

#: How many psycopg connections the saver's pool may open **for one purge**.
#:
#: One, where `api/app.py::COPILOT_POOL_MAX_SIZE` is four. That constant sizes a
#: pool serving concurrent handlers streaming answers; this one serves a single
#: sequential loop over one subject's threads, so a second connection would be a
#: backend opened to sit idle for the length of the command.
_POOL_SIZE = 1

#: Exit code for every refusal this command can report.
#:
#: One code rather than one per exception, and deliberately: the three refusals
#: (`ClaimNotFound`, `RedactorUnavailable`, `BlobStoreRequired`) all mean "the
#: purge did not run and nothing was deleted", which is the only distinction a
#: shell script wrapping this needs. What went wrong is on stderr, where a
#: person reads it.
_REFUSED = 2


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """`WC-nnnn` or `--user <id>`, and never both.

    A mutually exclusive group rather than two positional forms, so that
    `purge_claim WC-20017 --user 7` is an argparse error rather than a coin
    toss about which subject gets destroyed. The claim form is positional
    because it is the overwhelmingly common one and because the command is
    named after it.

    **`--blob-root` is checked to be an existing directory, and the check is
    not tidiness.** `VolumeBlobStore` resolves keys against whatever `Path` it
    is handed and its `delete` is `unlink(missing_ok=True)` — so a root that is
    a typo, an unmounted volume or a plain file produces a store whose every
    delete is a silent no-op. The cascade would then report "blobsDeleted: 4",
    the `document` rows that held those keys would be gone, and the four objects
    would still be on disk with nothing left in the database able to name them.
    An irreversible command must not be able to say it removed bytes it never
    touched, and the one moment this is cheaply detectable is before it runs.
    """
    parser = argparse.ArgumentParser(
        prog="python -m scripts.purge_claim",
        description=(
            "Purge every PHI-class store for one claim or one user, and redact "
            "that subject's audit diffs in place. Irreversible."
        ),
    )
    subject = parser.add_mutually_exclusive_group(required=True)
    subject.add_argument(
        "business_id",
        nargs="?",
        help="the claim's business id, e.g. WC-20017",
    )
    subject.add_argument(
        "--user",
        type=int,
        dest="user_id",
        help=(
            "purge one app_user's artifacts — diary notes, meetings, email log, "
            "conversations and sessions. The account row itself is never deleted."
        ),
    )
    parser.add_argument(
        "--blob-root",
        type=Path,
        default=None,
        help=(
            "directory backing the BlobStore. Required only when the subject has "
            "rows carrying a blob_key; the seeded portfolio has none, and the "
            "volume-versus-object-store decision is deferred, so the store is "
            "named at invocation rather than in configuration."
        ),
    )
    args = parser.parse_args(argv)
    if args.blob_root is not None and not args.blob_root.is_dir():
        parser.error(
            f"--blob-root {str(args.blob_root)!r} is not an existing directory. A volume "
            "store over a path that is not there deletes nothing and says it did, and the "
            "rows holding the keys would already be gone by the time anybody looked."
        )
    return args


async def _run(args: argparse.Namespace, settings: Settings) -> PurgeRun:
    """Build every dependency, run one cascade, tear everything down.

    The order below is `api/app.py::lifespan`'s, shortened: engine, then the
    saver's own psycopg pool (it cannot be handed an asyncpg connection — it
    uses server-side binary parameters and its own pipeline mode), then the
    command, then teardown in reverse.

    **`saver.setup()` is never called**, here or anywhere: migration 0043
    vendored the DDL and this process has no schema rights.
    `tests/test_layering.py` reads the AST for the call, so the absence is
    checked rather than remembered.

    The system actor is resolved per run through
    `services/financials/batch.py::system_context` — the one machine identity in
    this build, reused rather than duplicated, and its `scope_all` is why the
    audit rows this cascade writes have an actor at all.
    """
    engine = create_async_engine(settings.async_database_url)
    pool: AsyncConnectionPool[Any] = AsyncConnectionPool(
        conninfo=settings.database_url,
        min_size=1,
        max_size=_POOL_SIZE,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    )
    try:
        # **Inside the `try`, not above it.** `AsyncConnectionPool(open=False)`
        # is inert until this call, but the call itself can fail — a database
        # that is up but not accepting connections, a DSN the api container
        # resolves and this shell does not — and an `open()` outside the block
        # that owns the `finally` would leak the SQLAlchemy engine created two
        # lines earlier on exactly that path. `pool.close()` on a pool that
        # never opened is a no-op, so moving it in costs nothing.
        await pool.open()
        saver = AsyncPostgresSaver(pool)

        async def delete_thread(thread_id: str) -> None:
            # See `api/app.py`'s copy: `discard_thread` takes `thread_id` by
            # keyword and `ThreadDeleter` is positional, so the two are joined
            # by a closure rather than by `functools.partial`.
            await discard_thread(saver, thread_id=thread_id)

        blobs: BlobStore | None = (
            VolumeBlobStore(args.blob_root) if args.blob_root is not None else None
        )

        sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        async with sessionmaker() as session:
            ctx = await system_context(session)
            if args.user_id is not None:
                return await purge_user(
                    session,
                    ctx,
                    user_id=args.user_id,
                    settings=settings,
                    blobs=blobs,
                    delete_thread=delete_thread,
                )
            return await purge_claim(
                session,
                ctx,
                business_id=args.business_id,
                settings=settings,
                blobs=blobs,
                delete_thread=delete_thread,
            )
    finally:
        # The pool before the engine, `lifespan`'s ordering and its reason: a
        # pool closed after its database handles were torn down surfaces as a
        # psycopg error with no obvious cause.
        await pool.close()
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    """Parse, purge, print. Returns the process's exit code.

    Returning rather than calling `sys.exit` so the whole command is one
    testable function — `dump_openapi.py`'s `main()` is the shape and this adds
    only the exit code, which is the part a caller most wants to assert.

    The refusals are caught by type rather than swallowed broadly: anything else
    that goes wrong here is a bug or an outage, and a management command that
    turned an unexpected exception into a tidy message would hide the one class
    of failure whose traceback is worth reading.

    `SystemActorRefused` joins the second group rather than the first: the id
    resolved and the row is there, so "no such subject" would be a false
    statement about it, and the exception's own sentence already says which row
    it was and why it is not purgeable.
    """
    args = _parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        run = asyncio.run(_run(args, settings))
    except (ClaimNotFound, UserNotFound) as absent:
        # The subject and nothing else — see the module docstring on why this
        # message does not elaborate.
        print(f"no such subject: {absent}", file=sys.stderr)
        return _REFUSED
    except (RedactorUnavailable, BlobStoreRequired, SystemActorRefused) as refused:
        print(str(refused), file=sys.stderr)
        return _REFUSED
    json.dump(run.as_json(), sys.stdout, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
