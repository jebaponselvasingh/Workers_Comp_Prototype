"""The single 12-factor configuration surface (AC 4).

Every runtime knob — now and in every later story — is a field on
``Settings``. Nothing else in the server may read ``os.environ``.
"""

from enum import StrEnum
from functools import lru_cache
from typing import Any

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

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
    # Pulled and served from this story on; its first *application* caller
    # arrives with 6.2/6.3. Nothing in this build issues a chat request —
    # `services/rag` speaks to the embeddings endpoint and nothing else — so
    # this knob exists here rather than in 6.3 for one reason: the compose
    # entrypoint pulls both models before the container reports healthy, and
    # the name it pulls has to come from the same place the eventual client
    # will read it from.
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
