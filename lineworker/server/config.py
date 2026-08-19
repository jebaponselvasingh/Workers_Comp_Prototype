"""The single 12-factor configuration surface (AC 4).

Every runtime knob — now and in every later story — is a field on
``Settings``. Nothing else in the server may read ``os.environ``.

That last sentence is also why the LangSmith tripwire below lives here and
nowhere else: it is a write to the process environment, and this module is the
only one allowed to touch it.
"""

import os
from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

#: Every environment variable that can turn LangChain's LangSmith tracer on,
#: forced to `"false"` at import. **This is an AD-5/AD-16 containment control,
#: not a preference.**
#:
#: `langchain-ollama` depends on `langchain-core`, which hard-depends on
#: `langsmith` — not as an optional extra, as a required distribution in
#: `uv.lock`. That tracer needs no code to activate: it reads these names off
#: the environment at the first completion and, when any of them says `true`,
#: POSTs the **whole prompt body and the whole completion** to
#: `api.smith.langchain.com`. For this build that is a claim's clinical
#: narrative and a model's answer about it leaving the network (AD-11), through
#: a code path nobody wrote and no grep for `ollama_base_url` can see —
#: `tests/test_ai_insights.py::test_exactly_two_modules_read_the_ollama_base_url`
#: greps a string and is blind to it by construction.
#:
#: AD-5 says there is no cloud code path and AD-16's containment floor rests on
#: "no egress". A dependency that can open one from an env file is exactly the
#: kind of thing those two rules exist to refuse, so it is refused here rather
#: than documented as a deployment caveat.
#:
#: Both vendor namespaces are named because `langsmith.utils.get_env_var` reads
#: `LANGSMITH_*` first and falls back to `LANGCHAIN_*`, and both spellings of
#: the switch are honoured. `*_OTEL_ENABLED` is the second exporter the same
#: package grew and would otherwise be a second door.
_TRACING_ENV_OFF: dict[str, str] = {
    "LANGSMITH_TRACING": "false",
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGCHAIN_OTEL_ENABLED": "false",
}


def force_local_only_tracing() -> None:
    """Turn the LangSmith exporter off, overriding whatever the operator set.

    **Overwrite rather than `setdefault`**, which is the whole point: a
    deployment that inherited `LANGCHAIN_TRACING_V2=true` from a shared env file
    or a base image would otherwise start exporting prompts, and the failure
    would be silent, remote and retroactive. There is no supported way to enable
    it — a story that wanted tracing would be proposing a cloud code path, which
    AD-5 refuses outright.

    Called at import so it runs before any LangChain object is constructed:
    `langsmith` caches its environment lookups (`functools.lru_cache` on
    `get_env_var`), so the first read wins for the life of the process, and
    every module that touches a model imports `config` on the way to
    `Settings`. `tests/test_ai_insights.py` asserts the outcome rather than the
    ordering — it calls the vendor's own `tracing_is_enabled()`.

    Exported rather than private so that test can re-run it after poisoning the
    environment, which is the only way to assert "an operator cannot switch this
    back on" rather than "nobody has switched it on".
    """
    os.environ.update(_TRACING_ENV_OFF)


force_local_only_tracing()

# Knobs that were real fields once and are not any more. `extra="ignore"` is
# right for a stray variable nobody meant — and exactly wrong for one an
# operator set on purpose, which would be dropped in silence and leave the
# app running on a default they thought they had overridden. That is the
# failure `deploy/compose.yaml`'s env_file comment exists to describe; it
# would be a poor joke to reintroduce it while retiring a setting.
#
# Story 2.1 moved the risk bands into the `derivation_thresholds` JDM
# document, where a change is a new document version rather than an
# environment variable.
RETIRED_SETTINGS: dict[str, str] = {
    "risk_high_min": (
        "the severity bands moved to the derivation_thresholds rule document "
        "in Story 2.1; seed a new document version instead"
    ),
    "risk_med_min": (
        "the severity bands moved to the derivation_thresholds rule document "
        "in Story 2.1; seed a new document version instead"
    ),
}


class Env(StrEnum):
    dev = "dev"
    e2e = "e2e"
    prod = "prod"


#: Three-letter day names → `date.weekday()`, which is Monday-first. Note the
#: offset from JavaScript's `Date.getDay()`, which the prototype uses and which
#: is Sunday-first: its `c.getDay()===2||c.getDay()===5` is Tuesday and Friday,
#: the same two days these defaults name, reached from a different origin.
_WEEKDAY_NUMBERS: dict[str, int] = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


def _with_driver(url: str, driver: str) -> str:
    """Force the DBAPI driver on a database URL.

    Parsed rather than string-replaced so the aliases people actually set —
    ``postgres://…``, or an already-qualified ``postgresql+asyncpg://…`` —
    are normalized here instead of reaching the engine as an opaque dialect
    error at startup.
    """
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    if backend not in ("postgresql", "postgres"):
        raise ValueError(
            f"unsupported database backend {backend!r}: LINEWORKER requires postgresql"
        )
    return parsed.set(drivername=f"postgresql+{driver}").render_as_string(hide_password=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def _refuse_retired_settings(cls, data: Any) -> Any:
        """Fail loudly on a knob that used to exist.

        `mode="before"` because this is the only point at which the retired
        name is still visible: `extra="ignore"` drops it a moment later, and
        a dropped override is indistinguishable from one that was never set.
        Refusing at startup means an upgrade that would have silently
        reverted a tuned band stops instead, naming where the value went.
        """
        if not isinstance(data, dict):
            return data
        retired = sorted(
            f"{name.upper()} ({reason})"
            for key in data
            if (name := str(key).lower()) in RETIRED_SETTINGS
            for reason in [RETIRED_SETTINGS[name]]
        )
        if retired:
            raise ValueError("retired setting(s) present in the environment: " + "; ".join(retired))
        return data

    # Runtime connection — the app role (no DDL rights from Story 1.2 on).
    database_url: str = "postgresql://lineworker:lineworker@localhost:5432/lineworker"
    # Migration connection — the owner role Alembic runs under; falls back
    # to database_url when unset (single-role setups, e.g. the CI job).
    alembic_database_url: str | None = None
    # Password the roles migration assigns to lineworker_app (synthetic dev
    # default; deployments override via env — never committed real secrets).
    app_db_password: str = "lineworker_app_dev"
    # AD-4/AD-11 retention floor (years) baked into the audit_event RLS
    # delete policy for the audit_redactor role.
    audit_retention_years: int = 7
    env: Env = Env.dev
    log_level: str = "INFO"

    # --- Session auth (Story 1.3) ------------------------------------
    # The cookie carries an opaque session id and nothing else; role and
    # employer scope are re-resolved from app_user on every request (AD-7),
    # so these knobs govern the reference's lifetime, never its contents.
    session_cookie_name: str = "lw_session"
    # gt=0: a zero or negative TTL mints sessions that are already expired
    # and cookies with Max-Age<=0, so login "succeeds" and every following
    # request 401s — a silent login loop with nothing to show the user.
    session_ttl_hours: int = Field(default=12, gt=0)
    # None => derive from env (Secure in prod, off in dev/e2e where the
    # stack is plain http and a Secure cookie would silently never be sent).
    session_cookie_secure: bool | None = None

    # --- SLA targets (Story 1.5) -------------------------------------
    # TODO(JDM): AD-8 assigns SLA targets to the ZEN JDM tier. The engine
    # landed with Story 2.1 and took the risk bands with it, so the move is
    # now a known quantity rather than a plan: a `sla_targets` rule document
    # beside the other two, and `services/worklist/sla.py` reading a typed
    # block from `rules/parameters.py` instead of these four fields. It is
    # deferred rather than blocked — no acceptance criterion in Epic 2
    # touches them, and each move should carry its own migration and its own
    # version. What is *not* negotiable either way: the aggregation
    # names no target of its own (BRD §7.1 — pick <1d, approve <5d,
    # settle <30d, RTW >80%).
    #
    # gt=0 on the day targets: a zero or negative target can never be met
    # (the comparison is strict), so every tile would warn for ever with no
    # way to tell a missed target from a misconfigured one.
    sla_pick_target_days: float = Field(default=1.0, gt=0)
    sla_approve_target_days: float = Field(default=5.0, gt=0)
    sla_settle_target_days: float = Field(default=30.0, gt=0)
    # A rate target of 100 is likewise unreachable (strictly above), and one
    # above 100 or below 0 is not a percentage at all.
    sla_rtw_target_pct: float = Field(default=80.0, ge=0, lt=100)

    # --- The payment batch (Story 3.4) -------------------------------
    # The prototype's `nextBatchDate` runs batches on Tuesdays and Fridays
    # (line 808). That cadence is a *deployment* fact — which days this
    # carrier's disbursement file goes out — so it is config rather than a
    # JDM parameter: AD-8 puts business thresholds in the rules tier, and a
    # bank's cut-off is not one. The conventions row is explicit that a
    # schedule comes "from config/DB — never hardcoded".
    #
    # Day *names* rather than integers, because this is a value an operator
    # reads and edits in an env file, and `[1,4]` is a value nobody can
    # check at a glance. Parsed and validated at startup by
    # `payment_batch_weekday_numbers` below.
    payment_batch_weekdays: str = "tue,fri"
    # The scheduler's poll interval, not the batch's cadence — the two are
    # different knobs and conflating them is how a "runs twice a week" job
    # ends up running every five minutes. A tick only *checks* whether a due
    # day has arrived; `services/jobs.py` decides.
    scheduler_tick_seconds: float = Field(default=300.0, gt=0)
    # None => derive from env. Off under `e2e`, and that is a determinism
    # property rather than a deployment preference: a suite whose stack pays
    # invoices on a wall clock cannot assert what a claim's figures are. The
    # e2e profile triggers the batch explicitly instead (see the admin route
    # in `api/routers/admin.py`). `cookie_secure` derives the same way.
    scheduler_enabled: bool | None = None

    # --- Local model serving and embeddings (Story 6.1) ---------------
    # AD-5: all inference is local, the Ollama port is never published, and
    # **model names are deployment configuration rather than literals in
    # code**. That last clause is the whole reason these are fields: the
    # architecture's Deferred list keeps the exact `qwen3` tag open pending a
    # re-benchmark, and the CPU-dev pairing it names is a different pair of
    # models entirely. A tag pinned in a Python constant would make "which
    # model does this deployment run?" a code change with a review and a
    # rebuild behind it, on a decision whose whole point is that it moves.
    #
    # The default is the internal compose service name, not a localhost URL:
    # `api` reaches `ollama` over the compose network, and a default of
    # 127.0.0.1 would work on a developer's laptop and fail in the only
    # topology this project ships.
    ollama_base_url: str = "http://ollama:11434"
    # Pulled and served since Story 6.1; **read by application code since
    # Story 6.2**, which is the first thing in the build to issue a chat
    # request. `agents/client.py` is the reader — the second and last module in
    # the tree that names `ollama_base_url` — and it records the value it was
    # given on every `ai_insight` row it writes, so a card can say which model
    # produced its narrative. Until 6.2 this knob existed only so the compose
    # entrypoint pulled the right thing before reporting healthy; that
    # arrangement is why the name the puller reads and the name the client
    # reads have always been one field.
    chat_model: str = "qwen3:14b"
    # bge-m3 emits 1024 floats, which is what `vector(1024)` and
    # `services/rag/client.EMBEDDING_DIMENSIONS` both say. **Changing this to
    # a model of a different dimensionality is a migration, not a config
    # flip** — the architecture's CPU-dev pairing names `nomic-embed-text`,
    # which emits 768, and pointing this at it against the current column
    # raises `EmbeddingDimensionMismatch` on the first refresh rather than
    # writing vectors that retrieve nonsense.
    embedding_model: str = "bge-m3"
    # How often the refresh job *checks* — the scheduler's tick is the poll
    # rate for every job, and this is this job's cadence, the same two-knob
    # split `payment_batch_weekdays` and `scheduler_tick_seconds` keep. Fifteen
    # minutes because staleness is a retrieval-quality property rather than a
    # correctness one: a similar-case search that ranks against a claim's
    # pre-edit clinical summary for a few minutes is a slightly worse answer,
    # not a wrong one, and Story 6.4 discloses `embeddedAt` so the reader can
    # see it. `gt=0` for `scheduler_tick_seconds`' reason — a zero interval is
    # a job that either never fires or fires on every tick, and neither is
    # what anybody typed it to mean.
    embedding_refresh_interval_seconds: float = Field(default=900.0, gt=0)
    # How many rows one run embeds. A bound rather than "everything pending",
    # because the first run against a fresh database has 100 claims plus the
    # knowledge corpus waiting and a single model server serving one request
    # at a time: an unbounded first tick would hold the job for minutes and
    # (once 6.3 lands) queue behind a handler's chat. Pending work is not lost
    # — it is still pending on the next tick, which is what makes a batch bound
    # safe here and would not make it safe for a payment run.
    embedding_refresh_batch_size: int = Field(default=25, gt=0)
    # The per-request HTTP timeout on the embeddings call. Generous because a
    # cold model on a CPU-only dev box takes tens of seconds to answer its
    # first request and the alternative is a refresh that can never succeed on
    # the hardware the architecture explicitly permits for dev. `gt=0` because
    # httpx reads 0 as "no timeout" on some transports and as "fail
    # immediately" on others, and neither is a value to reach by accident.
    embedding_request_timeout_seconds: float = Field(default=60.0, gt=0)

    # --- The AI insight cache (Story 6.2) -----------------------------
    # How often the insight refresh job *checks*. An hour rather than the
    # embedding refresh's fifteen minutes, and the difference is the cost of
    # the work rather than a preference: an embedding tick is one batched
    # request over short summaries, an insight tick is four chat completions
    # per claim on a server that answers one at a time. An insight is also a
    # cache with its generation timestamp on screen (AD-10), so a claim whose
    # narrative is an hour behind is a card that says so rather than a wrong
    # answer. `gt=0` for `embedding_refresh_interval_seconds`' reason.
    insight_refresh_interval_seconds: float = Field(default=3600.0, gt=0)
    # How many *claims* one scheduled run generates for — not how many rows,
    # which is four times this. Much smaller than the embedding batch for the
    # same reason the interval is longer: each claim costs four completions,
    # so a tick of twenty-five would hold the model for minutes and (once 6.3
    # lands) queue behind a handler's chat. Pending work is not lost — the
    # next tick picks it up — which is what makes a bound safe here.
    insight_refresh_batch_size: int = Field(default=5, gt=0)
    # The per-request timeout on a chat completion. Twice the embeddings
    # timeout because generation is a different order of work: an embedding is
    # one forward pass over a short summary, a structured narrative is tens of
    # tokens decoded one at a time, and the architecture explicitly permits a
    # CPU-only dev box. `gt=0` for `embedding_request_timeout_seconds`' reason
    # — 0 reads as "no timeout" on some transports and "fail immediately" on
    # others.
    chat_request_timeout_seconds: float = Field(default=120.0, gt=0)
    # How old a neighbour's vector may be before the similar-case insight says
    # so. AD-12 requires retrieval to carry `embedded_at` and answers to
    # disclose staleness past a configured threshold; this is that threshold,
    # and it is deployment config rather than a rules-tier parameter because
    # it is a statement about how often *this* deployment's refresh job runs,
    # not about claims. Seven days: long enough that an ordinary fifteen-minute
    # refresh cadence never trips it, short enough that a job that has been
    # failing for a week is visible on the card rather than only in a log.
    insight_staleness_disclosure_days: int = Field(default=7, gt=0)

    @property
    def payment_batch_weekday_numbers(self) -> frozenset[int]:
        """The configured cadence as `date.weekday()` values (Mon=0 … Sun=6).

        **Validated here rather than by a Pydantic field type**, because the
        useful failure is at *read* time with the offending token named: a
        `list[int]` field would take `[9]` happily and the batch would then
        never run, silently, for ever. An unparseable day stops the process
        with the word the operator typed.
        """
        numbers = set()
        for raw in self.payment_batch_weekdays.split(","):
            token = raw.strip().lower()[:3]
            if not token:
                continue
            if token not in _WEEKDAY_NUMBERS:
                raise ValueError(
                    f"PAYMENT_BATCH_WEEKDAYS contains {raw.strip()!r}; "
                    f"expected day names from {sorted(_WEEKDAY_NUMBERS)}"
                )
            numbers.add(_WEEKDAY_NUMBERS[token])
        if not numbers:
            raise ValueError("PAYMENT_BATCH_WEEKDAYS names no days; the batch would never run")
        return frozenset(numbers)

    @property
    def scheduler_runs(self) -> bool:
        if self.scheduler_enabled is not None:
            return self.scheduler_enabled
        return self.env is not Env.e2e

    @property
    def cookie_secure(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.env is Env.prod

    @property
    def async_database_url(self) -> str:
        """SQLAlchemy async engine URL (asyncpg driver)."""
        return _with_driver(self.database_url, "asyncpg")

    @property
    def sync_database_url(self) -> str:
        """Sync engine URL (psycopg driver)."""
        return _with_driver(self.database_url, "psycopg")

    @property
    def sync_alembic_database_url(self) -> str:
        """Alembic connection URL (owner role, psycopg driver)."""
        return _with_driver(self.alembic_database_url or self.database_url, "psycopg")


@lru_cache
def get_settings() -> Settings:
    return Settings()
