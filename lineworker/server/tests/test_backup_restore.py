"""The backup pipeline, end to end, against a real database (Story 8.3, AC 1/AC 3).

`tests/test_backup_posture.py` asserts what the deploy files *say*. This asserts
what the shipped container *does*: it builds `deploy/backup`, runs one real
backup against the migration test database, proves the artifact that would have
gone off-host is `age` ciphertext and not a plaintext dump, decrypts it with a
throwaway identity, restores it into a scratch database and compares the result
to the source row by row. Then it breaks the copy on purpose and proves the
failure reaches `status.json` and the healthcheck.

**The first test in this suite that shells out to docker**, and that is a real
cost — a test that needs a daemon is a test that skips on some machines and is
slow on the rest. It is paid because there is no honest alternative. The thing
under test is a shell script running PostgreSQL client binaries inside an image;
reimplementing its steps in Python would test a Python translation of the
pipeline, and the failures this story exists to prevent (a client version that
cannot read the server, an encryption step that silently produced nothing, a
`pg_restore` that "succeeded" into an empty database) all live in exactly the
part a translation would replace.

## How it reaches the database

The database is wherever `MIGRATION_TEST_DATABASE_URL` points, which on a
developer's machine is `localhost:55432` and in CI is `localhost:5432` — neither
of which means anything inside a container. The URL's host is rewritten to
`host.docker.internal` and the container is given
`--add-host=host.docker.internal:host-gateway`, which Docker Desktop provides
natively and which the flag supplies on Linux. That is the one piece of
environment-specific plumbing here, and it is the same recipe
`conftest.py`'s docstring uses from the other direction.

**The test database must be started with the `pg_hba.conf` mount** — see that
docstring. `pg_basebackup` opens a physical replication connection, and the
official image's `host all all all` record does not match one, so without the
mount this module fails with "no pg_hba.conf entry for replication connection".

## No bind mounts

Everything the container writes goes to a named docker volume and is read back
by starting a second container against the same volume. Bind-mounting a host
directory would be shorter and does not work reliably: on Docker Desktop a
source path outside the configured file-sharing set is silently *created* as an
empty directory inside the VM instead of being mounted, so the test would pass
against nothing. Volumes have no such failure mode on any platform.

## The identity is minted per run and never written to the repository

`age-keygen` runs inside the image, the identity is held in this process's
memory and passed to the decrypting container through the environment, and both
halves are gone when the test ends. `deploy/compose.e2e.yaml`'s committed
recipient is the opposite arrangement and deliberately so — it has no identity
anywhere, which is why the e2e spec asserts non-plaintext and never decrypts.
"""

import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from typing import Final

import pytest
import sqlalchemy as sa

from tests.conftest import MIGRATION_DB_URL, requires_db

#: The image tag `deploy/compose.yaml` names. Built by the fixture below rather
#: than assumed present: a stale image is how a test passes against last week's
#: script.
IMAGE: Final[str] = "lineworker/backup:pg18"

#: Where the container keeps its state, matching every compose profile's mount.
CONTAINER_BACKUP_ROOT: Final[str] = "/var/lib/lineworker/backup"
CONTAINER_BLOB_ROOT: Final[str] = "/var/lib/lineworker/blobs"

#: The "off-host" destination for these runs: a directory inside the same volume.
#: It exercises the whole `rsync` invocation and per-run layout without a second
#: host — the same limitation the e2e profile and the executed drill have, and
#: `deploy/RESTORE-DRILL.md` says so rather than implying the transport was
#: proven.
REMOTE: Final[str] = f"{CONTAINER_BACKUP_ROOT}/offhost"

#: `age`'s file header. The first line of every ciphertext it writes.
AGE_HEADER: Final[bytes] = b"age-encryption.org/v1"

#: `pg_dump -Fc`'s magic. Its presence at the destination would mean an
#: unencrypted copy of the claim book had left the host.
PGDUMP_MAGIC: Final[bytes] = b"PGDMP"

#: The stores the restored database is compared on, one per PHI-class family
#: (AD-3's "one stream covers everything" is what is actually being checked).
COMPARED_TABLES: Final[tuple[str, ...]] = (
    "claim",
    "app_user",
    "document",
    "photo",
    "bill",
    "timeline_event",
    "claim_embedding",
    "audit_event",
)

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None,
    reason="docker is not on PATH (this module runs the shipped backup image)",
)

pytestmark = [requires_db, requires_docker]


def run(*args: str, timeout: int, check: bool = True) -> subprocess.CompletedProcess[bytes]:
    """One subprocess, always with an explicit timeout.

    Every call in this module goes through here so that no invocation can be
    added without one. A docker command that hangs — a build waiting on a
    registry, a container whose entrypoint blocks — turns a failing test into a
    stalled CI job, and the pytest run has no timeout of its own.
    """
    return subprocess.run(args, capture_output=True, timeout=timeout, check=check)


def container_url(database: str) -> str:
    """`MIGRATION_TEST_DATABASE_URL`, as seen from inside a container.

    Only the host is rewritten. The credentials, port and query string are the
    caller's, because a test that reconstructed the URL would stop testing the
    one the developer actually pointed it at.
    """
    assert MIGRATION_DB_URL is not None
    url = re.sub(r"@(localhost|127\.0\.0\.1)([:/])", r"@host.docker.internal\2", MIGRATION_DB_URL)
    # Split the query off first. `rpartition("/")` on
    # `…/lineworker?sslmode=verify-full` yields a *database name* of
    # `lineworker?sslmode=verify-full`, so the rewritten URL asks for a database
    # that does not exist — or, worse, connects with the TLS parameter silently
    # attached to the name instead of applied.
    path, sep, query = url.partition("?")
    base, _, _ = path.rpartition("/")
    return f"{base}/{database}{sep}{query}"


def sibling_db(url: str, database: str) -> str:
    """The same connection URL, pointed at a different database on that server.

    Query-string aware for the same reason `container_url` is: a URL carrying
    `?sslmode=…` splits on the wrong `/` otherwise and names a database that
    does not exist.
    """
    path, sep, query = url.partition("?")
    base, _, _ = path.rpartition("/")
    return f"{base}/{database}{sep}{query}"


@pytest.fixture(scope="module")
def backup_image() -> str:
    """Build `deploy/backup` before anything runs against it."""
    context = os.path.join(os.path.dirname(__file__), "..", "..", "deploy", "backup")
    run("docker", "build", "-t", IMAGE, os.path.abspath(context), timeout=900)
    return IMAGE


@pytest.fixture(scope="module")
def age_keypair(backup_image: str) -> tuple[str, str]:
    """A throwaway `age` identity and its recipient, minted inside the image.

    Returned as a pair and held in memory. `age-keygen` writes the identity on
    stdout with the recipient as a `# public key:` comment line, so both halves
    come from one invocation and cannot disagree.
    """
    result = run("docker", "run", "--rm", "--entrypoint", "age-keygen", backup_image, timeout=120)
    identity = result.stdout.decode()
    recipient = ""
    for line in identity.splitlines():
        if line.startswith("# public key:"):
            recipient = line.split(":", 1)[1].strip()
    assert recipient.startswith("age1"), f"age-keygen produced no recipient: {identity!r}"
    return identity, recipient


@pytest.fixture
def volumes() -> Iterator[tuple[str, str]]:
    """A fresh backup volume and blob volume per test, removed afterwards."""
    suffix = uuid.uuid4().hex[:12]
    backup_volume = f"lw-test-backup-{suffix}"
    blob_volume = f"lw-test-blobs-{suffix}"
    for name in (backup_volume, blob_volume):
        run("docker", "volume", "create", name, timeout=60)
    try:
        yield backup_volume, blob_volume
    finally:
        for name in (backup_volume, blob_volume):
            run("docker", "volume", "rm", "-f", name, timeout=60, check=False)


def backup_once(
    image: str,
    recipient: str,
    volumes: tuple[str, str],
    *,
    remote: str = REMOTE,
    database: str = "lineworker",
) -> subprocess.CompletedProcess[bytes]:
    """`docker compose run --rm backup once`, spelled as a plain `docker run`.

    The same entrypoint, the same argument and the same environment the compose
    profiles set — compose is not involved because this suite has no compose
    project, and inventing one would make the test depend on a file it is not
    testing.
    """
    backup_volume, blob_volume = volumes
    return run(
        "docker",
        "run",
        "--rm",
        "--add-host=host.docker.internal:host-gateway",
        "-e",
        f"BACKUP_DATABASE_URL={container_url(database)}",
        "-e",
        f"BACKUP_AGE_RECIPIENT={recipient}",
        "-e",
        f"BACKUP_REMOTE={remote}",
        "-e",
        "BACKUP_WAL=off",
        "-v",
        f"{backup_volume}:{CONTAINER_BACKUP_ROOT}",
        "-v",
        f"{blob_volume}:{CONTAINER_BLOB_ROOT}:ro",
        image,
        "once",
        timeout=600,
        check=False,
    )


def read_from_volume(
    image: str, backup_volume: str, *command: str, env: dict[str, str] | None = None
) -> bytes:
    """Run one command against the backup volume and hand back its stdout.

    `env` exists so a secret never reaches argv: `run` uses `check=True`, and a
    `CalledProcessError` prints `cmd` — so an `age` identity interpolated into a
    `sh -c` string ends up in a CI log the moment the command fails.
    """
    env_args: list[str] = []
    for name, value in (env or {}).items():
        env_args += ["-e", f"{name}={value}"]
    result = run(
        "docker",
        "run",
        "--rm",
        *env_args,
        "-v",
        f"{backup_volume}:{CONTAINER_BACKUP_ROOT}",
        "--entrypoint",
        command[0],
        image,
        *command[1:],
        timeout=120,
    )
    return result.stdout


def status_json(image: str, backup_volume: str) -> dict[str, object]:
    import json

    raw = read_from_volume(image, backup_volume, "cat", f"{CONTAINER_BACKUP_ROOT}/status.json")
    parsed = json.loads(raw.decode())
    assert isinstance(parsed, dict)
    return parsed


def shipped_run_id(image: str, backup_volume: str) -> str:
    """The single run directory at the off-host destination."""
    listing = read_from_volume(image, backup_volume, "ls", "-1", REMOTE).decode().split()
    assert len(listing) == 1, f"expected exactly one shipped run, got {listing}"
    return listing[0]


def engine_for(url: str) -> sa.Engine:
    return sa.create_engine(
        url.replace("postgresql://", "postgresql+psycopg://", 1), isolation_level="AUTOCOMMIT"
    )


def row_counts(url: str) -> dict[str, int]:
    engine = engine_for(url)
    try:
        with engine.connect() as conn:
            return {
                table: int(conn.execute(sa.text(f"SELECT count(*) FROM {table}")).scalar_one())
                for table in COMPARED_TABLES
            }
    finally:
        engine.dispose()


def test_a_backup_run_ships_age_ciphertext_and_restores_to_the_same_data(
    backup_image: str,
    age_keypair: tuple[str, str],
    volumes: tuple[str, str],
    seeded_db_url: str,
) -> None:
    """The whole of AC 1, as a round trip.

    Four claims in one test, deliberately, because each is worthless without the
    others:

      1. The run succeeds and `status.json` names artifacts with real byte
         counts. (A run that produced nothing would satisfy every assertion
         below about what is *absent* from the artifact.)
      2. What arrived at the off-host destination begins with `age`'s header and
         does **not** contain `PGDMP`. This is AD-11's promise, checked at the
         only place it can be checked — the destination.
      3. Decrypting with the identity yields something that *does* begin with
         `PGDMP`, which is what makes claim 2 a statement about encryption
         rather than about a corrupt file.
      4. Restoring it reproduces the source's row counts across every PHI-class
         store, and one known claim's fields survive verbatim. AD-3's "one
         stream covers the whole instance" is exactly this assertion.
    """
    backup_volume, _ = volumes
    source_counts = row_counts(seeded_db_url)
    assert source_counts["claim"] > 0, "the seeded database is empty; nothing would be proved"

    engine = engine_for(seeded_db_url)
    try:
        with engine.connect() as conn:
            known = conn.execute(
                sa.text(
                    "SELECT claim_id, injury_type, employer_id FROM claim ORDER BY claim_id LIMIT 1"
                )
            ).one()
    finally:
        engine.dispose()

    _, recipient = age_keypair
    result = backup_once(backup_image, recipient, volumes)
    assert result.returncode == 0, (
        f"the backup run failed: {result.stdout.decode()[-2000:]}\n{result.stderr.decode()[-2000:]}"
    )

    status = status_json(backup_image, backup_volume)
    assert status["result"] == "success", status
    assert status["stage"] == "done", status
    artifacts = status["artifacts"]
    assert isinstance(artifacts, list)
    by_name = {entry["name"]: entry["bytes"] for entry in artifacts}
    for expected in ("dump.age", "globals.sql.age", "base.tar.gz.age", "blobs.tar.age"):
        assert expected in by_name, f"{expected} is missing from {sorted(by_name)}"
        assert by_name[expected] > 0, f"{expected} is zero bytes — a backup of nothing"

    # --- AD-11, checked at the destination -----------------------------------
    run_id = shipped_run_id(backup_image, backup_volume)
    shipped = f"{REMOTE}/{run_id}/dump.age"
    # Bounded read: the point is the header, and `capture_output` on a hundred
    # megabytes of ciphertext is a memory profile no assertion needs.
    head = read_from_volume(backup_image, backup_volume, "head", "-c", "64", shipped)
    assert head.startswith(AGE_HEADER), (
        f"the shipped artifact does not begin with age's header: {head[:32]!r}"
    )
    assert PGDUMP_MAGIC not in head, (
        "a plaintext pg_dump header reached the off-host destination. This is the one "
        "outcome AD-11 forbids outright."
    )

    # …and nothing at the destination is plaintext, not merely this one file.
    listing = (
        read_from_volume(backup_image, backup_volume, "ls", "-1", f"{REMOTE}/{run_id}")
        .decode()
        .split()
    )
    assert sorted(listing) == sorted(
        [
            "base.tar.gz.age",
            "base_manifest.age",
            "blobs.tar.age",
            "dump.age",
            "globals.sql.age",
            "manifest.json",
            "pg_wal.tar.gz.age",
            "wal",
        ]
    ), listing

    # --- the manifest is the integrity mechanism, so check that it works -----
    # `manifest.json` is what a restore verifies an artifact against months
    # later, and nothing proved its checksums were computed over the files they
    # name. Recomputing them at the destination is the same check the runbook
    # tells an operator to run, over the same bytes.
    import json as _json

    manifest = _json.loads(
        read_from_volume(
            backup_image, backup_volume, "cat", f"{REMOTE}/{run_id}/manifest.json"
        ).decode()
    )
    entries = manifest["artifacts"]
    assert entries, "the manifest lists no artifacts"
    sums = (
        read_from_volume(
            backup_image,
            backup_volume,
            "sh",
            "-c",
            f"cd {REMOTE}/{run_id} && sha256sum *.age",
        )
        .decode()
        .split()
    )
    actual = dict(zip(sums[1::2], sums[0::2], strict=True))
    for entry in entries:
        name = entry["name"]
        if not name.endswith(".age"):
            continue
        assert actual.get(name) == entry["sha256"], (
            f"manifest.json's sha256 for {name} does not match the shipped bytes. "
            "The manifest is the only integrity check a restore has months later; "
            "a path bug in the loop that writes it would otherwise be undetectable."
        )

    # --- the round trip ------------------------------------------------------
    identity, _ = age_keypair
    scratch = f"lw_restore_{uuid.uuid4().hex[:10]}"
    admin = engine_for(seeded_db_url)
    try:
        with admin.connect() as conn:
            conn.execute(sa.text(f'CREATE DATABASE "{scratch}"'))
    finally:
        admin.dispose()

    try:
        # Decrypt and restore inside one container: the plaintext dump exists
        # only in that container's ephemeral filesystem and is gone with it,
        # which is the same discipline `deploy/RESTORE-DRILL.md` asks of an
        # operator ("the decrypted files are PHI").
        restore = run(
            "docker",
            "run",
            "--rm",
            "--add-host=host.docker.internal:host-gateway",
            "-e",
            f"AGE_IDENTITY={identity}",
            "-e",
            f"RESTORE_URL={container_url(scratch)}",
            "-v",
            f"{backup_volume}:{CONTAINER_BACKUP_ROOT}",
            "--entrypoint",
            "sh",
            backup_image,
            "-c",
            'set -eu; printf "%s" "$AGE_IDENTITY" > /tmp/id; '
            f'age -d -i /tmp/id -o /tmp/dump "{shipped}"; '
            "head -c 5 /tmp/dump; "
            'pg_restore --dbname="$RESTORE_URL" --no-owner /tmp/dump',
            timeout=600,
            check=False,
        )
        assert restore.returncode == 0, (
            f"decrypt+restore failed: {restore.stdout.decode()[-3000:]}\n"
            f"{restore.stderr.decode()[-3000:]}"
        )
        # The positive control for the two absence assertions above: the
        # decrypted bytes ARE a pg_dump, so "no PGDMP at the destination" is a
        # statement about encryption rather than about a broken file.
        assert restore.stdout.startswith(PGDUMP_MAGIC), (
            f"the decrypted artifact is not a pg_dump archive: {restore.stdout[:32]!r}"
        )

        restored_counts = row_counts(sibling_db(seeded_db_url, scratch))
        assert restored_counts == source_counts, (
            "the restored database does not match the source. AD-3's single backup "
            "stream is supposed to carry every PHI-class store in one artifact; a "
            "mismatch here names the family it did not carry."
        )

        restored = engine_for(sibling_db(seeded_db_url, scratch))
        try:
            with restored.connect() as conn:
                found = conn.execute(
                    sa.text(
                        "SELECT claim_id, injury_type, employer_id FROM claim "
                        "WHERE claim_id = :claim_id"
                    ),
                    {"claim_id": known.claim_id},
                ).one()
        finally:
            restored.dispose()
        assert tuple(found) == tuple(known), (
            "a known claim did not survive the round trip verbatim. Row counts alone "
            "would pass against a dump that restored the right number of wrong rows."
        )
    finally:
        admin = engine_for(seeded_db_url)
        try:
            with admin.connect() as conn:
                conn.execute(sa.text(f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)'))
        finally:
            admin.dispose()


def test_the_shipped_globals_artifact_carries_the_roles_the_dump_does_not(
    backup_image: str,
    age_keypair: tuple[str, str],
    volumes: tuple[str, str],
    seeded_db_url: str,
) -> None:
    """The drill's own finding, pinned.

    On 2026-08-23 the executed restore drill restored `dump.age` on its own into
    a fresh cluster and got a database with all its data, no `lineworker_app`
    role, and every one of 69 `GRANT` statements failed — while `pg_restore`
    exited 0. `pg_dump` dumps a database; roles are cluster objects and are not
    in it. `globals.sql.age` is the artifact that closes that, and this asserts
    it is present, decryptable, and actually contains the two roles AD-4's audit
    posture is built out of.

    It also asserts the artifact is globals-*only*: `--globals-only` must not
    start carrying table data, because the manifest, the retention count and the
    AD-3 one-stream argument all assume `dump.age` is the only copy of it.
    """
    del seeded_db_url  # the fixture is required for a migrated database to exist
    backup_volume, _ = volumes
    identity, recipient = age_keypair
    result = backup_once(backup_image, recipient, volumes)
    assert result.returncode == 0, (
        f"the backup run failed: {result.stdout.decode()[-2000:]}\n{result.stderr.decode()[-2000:]}"
    )

    run_id = shipped_run_id(backup_image, backup_volume)
    globals_sql = read_from_volume(
        backup_image,
        backup_volume,
        "sh",
        "-c",
        'printf "%s" "$AGE_IDENTITY" > /tmp/id && '
        f'age -d -i /tmp/id "{REMOTE}/{run_id}/globals.sql.age"',
        env={"AGE_IDENTITY": identity},
    ).decode()

    for role in ("lineworker_app", "audit_redactor"):
        assert f"CREATE ROLE {role}" in globals_sql, (
            f"{role} is not in the globals artifact, so a restore from this backup "
            f"produces a cluster without it — a database the api cannot open, or an "
            f"audit log with no redaction role and no RLS policy. Both are silent: "
            f"pg_restore exits 0 either way."
        )
    assert "COPY public." not in globals_sql, (
        "the globals artifact contains table data. --globals-only is supposed to be "
        "roles and tablespaces; a second copy of the claim book here would be a "
        "second PHI store that nothing in this pipeline accounts for."
    )


def test_a_failed_off_host_copy_is_recorded_and_makes_the_container_unhealthy(
    backup_image: str,
    age_keypair: tuple[str, str],
    volumes: tuple[str, str],
    seeded_db_url: str,
) -> None:
    """AC 3: the failure surfaces, rather than being discovered at restore time.

    The destination is pointed inside the **read-only** blob mount, which is an
    unwritable path that exists in every profile — a more honest simulation than
    a made-up hostname, because it fails the way a full disk or a revoked
    permission fails rather than the way DNS fails.

    Four assertions, and the last two are the ones that matter. That the run
    exits non-zero is table stakes. That `status.json` names the stage is what a
    human reads at 08:00. That `healthcheck.sh` exits 1 is what
    `docker compose ps` shows without anybody reading anything, which is the
    whole of AC 3's binding surface.

    This test also guards a bug that shipped and was caught here first: `attempt`
    originally read `$?` after an `if … fi`, which POSIX defines as zero when
    the condition failed and there is no `else`, so a failed `rsync` produced
    `"result": "success"` beside `"error": "rsync: … failed"`. Every assertion
    below passed vacuously against that script except the two on `result`.
    """
    backup_volume, _ = volumes
    _, recipient = age_keypair

    result = backup_once(
        backup_image,
        recipient,
        volumes,
        remote=f"{CONTAINER_BLOB_ROOT}/unwritable",
    )
    assert result.returncode != 0, (
        "the backup run reported success against an unwritable destination. A run "
        "whose artifacts never left the host is a failed run."
    )

    status = status_json(backup_image, backup_volume)
    assert status["result"] == "failure", status
    assert status["stage"] == "copy", status
    assert status["exit_code"] != 0, status
    assert isinstance(status["error"], str) and status["error"], (
        "a failed run must record why, in one line"
    )

    health = run(
        "docker",
        "run",
        "--rm",
        "-v",
        f"{backup_volume}:{CONTAINER_BACKUP_ROOT}",
        "--entrypoint",
        "/usr/local/bin/healthcheck.sh",
        backup_image,
        timeout=120,
        check=False,
    )
    assert health.returncode == 1, (
        "healthcheck.sh reported healthy after a failed run. The status file plus this "
        "check are the whole of AC 3's binding surface — nothing else in this system "
        "would ever mention the failure."
    )


def test_an_unconfigured_recipient_is_refused_before_any_plaintext_exists(
    backup_image: str,
    volumes: tuple[str, str],
    seeded_db_url: str,
) -> None:
    """The refusal that makes an unconfigured deployment safe rather than merely loud.

    `test_backup_posture.py` asserts the check is *written* before
    `pg_basebackup` in the script's source. This asserts it *ran* there: after a
    refusal the artifacts directory is empty, so no plaintext dump of the claim
    book was created and then abandoned.

    An empty recipient rather than a malformed one, because empty is what a
    clean checkout has — `compose.yaml` interpolates
    `${BACKUP_AGE_RECIPIENT:-}` precisely so that this is the failure mode
    rather than a default somebody else holds the identity for.
    """
    backup_volume, _ = volumes
    result = backup_once(backup_image, "", volumes)
    assert result.returncode != 0
    assert b"BACKUP_AGE_RECIPIENT" in result.stderr, result.stderr[-2000:]

    status = status_json(backup_image, backup_volume)
    assert status["result"] == "failure", status
    assert status["stage"] == "config", status

    artifacts = read_from_volume(
        backup_image, backup_volume, "ls", "-A", f"{CONTAINER_BACKUP_ROOT}/artifacts"
    )
    assert artifacts.strip() == b"", (
        f"a refused run left {artifacts!r} behind. The whole point of validating the "
        "recipient before pg_dump is that no plaintext artifact is ever created."
    )
