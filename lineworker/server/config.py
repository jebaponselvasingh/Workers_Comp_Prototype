"""The single 12-factor configuration surface (AC 4).

Every runtime knob — now and in every later story — is a field on
``Settings``. Nothing else in the server may read ``os.environ``.
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Env(StrEnum):
    dev = "dev"
    e2e = "e2e"
    prod = "prod"


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

    # --- Derived-value parameters (Story 1.4) ------------------------
    # TODO(JDM): AD-8 gives ZEN JDM documents ownership of every threshold,
    # band and weight, with Python keeping the formulas. ZEN is not
    # installed until its first consuming story in Epic 2, so the `risk`
    # bands live here in the meantime — as parameters, never as literals in
    # code (the same rule the conventions state for SLA targets). Migrating
    # means changing where `services/derivations/risk.py` reads these two
    # numbers from; no consumer of the derivation changes.
    risk_high_min: int = Field(default=65, ge=0, le=100)
    risk_med_min: int = Field(default=35, ge=0, le=100)

    # --- SLA targets (Story 1.5) -------------------------------------
    # TODO(JDM): AD-8 assigns SLA targets to the ZEN JDM tier — the same
    # migration the risk bands above are waiting for. Until ZEN lands
    # (Epic 2/3) they live here, and `services/worklist/sla.py` reads them
    # by name; moving them to a JDM document changes `targets_for()` and
    # nothing else. What is *not* negotiable either way: the aggregation
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

    @model_validator(mode="after")
    def _bands_must_not_overlap(self) -> "Settings":
        # An inverted pair would put scores in two bands at once, and the
        # derivation reads the bands in order — so it would silently answer
        # "high" for everything above med_min. Refuse at startup instead.
        if self.risk_med_min > self.risk_high_min:
            raise ValueError(
                f"risk_med_min ({self.risk_med_min}) must not exceed "
                f"risk_high_min ({self.risk_high_min})"
            )
        return self

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
