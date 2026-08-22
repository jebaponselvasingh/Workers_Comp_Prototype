"""Settings URL derivation — the single config surface (Story 1.1 AC 4)."""

import pytest
from pydantic import ValidationError

from config import RETIRED_SETTINGS, Env, Settings


def test_bare_postgresql_url_gets_each_driver() -> None:
    settings = Settings(database_url="postgresql://u:p@h:5432/db")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"
    assert settings.sync_database_url == "postgresql+psycopg://u:p@h:5432/db"


def test_postgres_alias_is_normalized() -> None:
    """`postgres://` is a common form; it must not reach the engine as-is."""
    settings = Settings(database_url="postgres://u:p@h:5432/db")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_already_qualified_driver_is_replaced_not_kept() -> None:
    """An asyncpg URL must still yield psycopg on the sync/Alembic paths."""
    settings = Settings(database_url="postgresql+asyncpg://u:p@h:5432/db")
    assert settings.sync_database_url == "postgresql+psycopg://u:p@h:5432/db"
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"


def test_alembic_url_overrides_database_url() -> None:
    settings = Settings(
        database_url="postgresql://app:p@h:5432/db",
        alembic_database_url="postgresql://owner:o@h:5432/db",
    )
    assert settings.sync_alembic_database_url == "postgresql+psycopg://owner:o@h:5432/db"


def test_non_postgres_backend_is_rejected() -> None:
    settings = Settings(database_url="mysql://u:p@h:3306/db")
    with pytest.raises(ValueError, match="requires postgresql"):
        _ = settings.async_database_url


def test_a_retired_setting_is_refused_rather_than_ignored() -> None:
    """Story 2.1 moved the risk bands into a rule document.

    `extra="ignore"` is the right policy for a stray variable nobody meant,
    and exactly the wrong one for an override an operator set on purpose:
    the value would be dropped in silence and the app would run on the
    default they believed they had replaced. The regression this guards is
    an upgrade that quietly reverts a tuned band to 65/35 — the same silent
    failure `deploy/compose.yaml`'s env_file comment describes.

    Built through `model_validate` rather than as keyword arguments because
    a retired name is no longer a field: passing it as a kwarg is a type
    error, while a dict is exactly the shape the settings sources hand in.
    """
    for name in RETIRED_SETTINGS:
        with pytest.raises(ValidationError, match="retired setting"):
            Settings.model_validate({name: 80})


def test_the_refusal_names_where_the_setting_went() -> None:
    """A refusal that only says "no" makes the operator go looking."""
    with pytest.raises(ValidationError, match="derivation_thresholds rule document"):
        Settings.model_validate({"risk_high_min": 80})


def test_a_retired_name_is_refused_whatever_its_case() -> None:
    """Environment variables arrive upper-cased; the check is on the name."""
    with pytest.raises(ValidationError, match="retired setting"):
        Settings.model_validate({"RISK_MED_MIN": 35})


def test_an_unrelated_unknown_variable_is_still_ignored() -> None:
    """Only *retired* names are refused.

    The environment of a real deployment is full of variables that are none
    of this app's business; refusing those would make `Settings` unusable
    anywhere but a clean room.
    """
    assert Settings.model_validate({"some_other_teams_variable": "whatever"}).env is Env.dev


# --- Story 8.2: what a database URL may carry in its query ----------------


def test_sslmode_survives_the_url_and_becomes_ssl_for_asyncpg_only() -> None:
    """NFR-5's parameter, and the one translation the two driver paths need.

    `asyncpg.connect()` has no `sslmode` keyword and psycopg has nothing else,
    so the same URL has to mean the same thing under both names. The prod
    overlay writes the spelling an operator knows and `_with_driver` renames it
    for the async engines; asserting both halves here is what keeps the rename
    from being applied to psycopg too, which would fail at the *other* first
    connection.
    """
    settings = Settings(database_url="postgresql://u:p@h:5432/db?sslmode=verify-full")
    assert settings.async_database_url.endswith("?ssl=verify-full")
    assert settings.sync_database_url.endswith("?sslmode=verify-full")


@pytest.mark.parametrize(
    "parameter",
    [
        "sslrootcert=/etc/postgresql/tls/ca.crt",
        "sslcert=/etc/postgresql/tls/client.crt",
        "sslkey=/etc/postgresql/tls/client.key",
        "sslcrl=/etc/postgresql/tls/root.crl",
        "sslpassword=hunter2",
        "gssencmode=disable",
        "channel_binding=require",
        "sslnegotiation=direct",
        "sslcompression=0",
        "require_auth=scram-sha-256",
        "application_name=lineworker",
        "connect_timeout=10",
    ],
)
def test_a_query_parameter_asyncpg_cannot_take_is_refused_at_construction(parameter: str) -> None:
    """Every one of these fails at the first connection, in prod, and nowhere else.

    SQLAlchemy's asyncpg dialect does `opts.update(url.query)` and hands the
    result to `asyncpg.connect()`, whose keyword set is fixed and much smaller
    than libpq's parameter list. So a URL carrying any of these starts the two
    psycopg engines perfectly — psycopg speaks libpq natively — and raises
    `TypeError: unexpected keyword argument` on the async one, at the moment the
    first request needs the database, on the only profile that speaks TLS.

    The list is deliberately not five TLS file parameters. That is what the
    guard refused first, and the six names after them are the reason it was
    inverted: they are the same failure with different spellings, they are all
    things a security-minded operator would plausibly add, and a denylist of the
    ones somebody thought of reads exactly like a rule that covers the class.

    Refusing at construction is the point. The alternative — dropping them —
    would let `?sslrootcert=…` produce a `verify-full` connection with no CA,
    which verifies nothing while reading in review as though it verifies the
    chain.
    """
    with pytest.raises(ValidationError, match="cannot accept"):
        Settings.model_validate({"database_url": f"postgresql://u:p@h:5432/db?{parameter}"})


def test_the_refusal_names_the_environment_variable_for_a_file_parameter() -> None:
    """A refusal that only says "no" makes the operator go looking.

    The five file parameters are the ones this build can answer for: both
    drivers read them from `PGSSLROOTCERT` and friends, `deploy/compose.prod
    .yaml` sets the first, and `deploy/.env.example` documents the arrangement.
    Naming the variable in the error is what turns a refusal into a fix.
    """
    with pytest.raises(ValidationError, match="PGSSLROOTCERT"):
        Settings.model_validate(
            {"database_url": "postgresql://u:p@h:5432/db?sslrootcert=/tmp/ca.crt"}
        )


def test_the_alembic_url_is_held_to_the_same_rule() -> None:
    """The migration connection is as much a PHI channel as the runtime one.

    It is also the one an operator is likelier to hand-edit — a one-off upgrade
    against a different host — and `sync_alembic_database_url` is not the only
    consumer: `async_alembic_database_url` builds an asyncpg engine from the
    same string, so the failure mode is identical.
    """
    with pytest.raises(ValidationError, match="ALEMBIC_DATABASE_URL"):
        Settings.model_validate(
            {
                "database_url": "postgresql://u:p@h:5432/db",
                "alembic_database_url": "postgresql://o:o@h:5432/db?gssencmode=prefer",
            }
        )


def test_a_url_with_no_query_at_all_is_untouched() -> None:
    """The negative control: the guard is a filter, not a refusal to have a URL.

    Every dev and e2e profile in the repository sets a bare URL, so a guard with
    an inverted condition would fail every boot rather than the one it is about
    — and would do it identically to a real misconfiguration.
    """
    settings = Settings(database_url="postgresql://u:p@h:5432/db")
    assert settings.async_database_url == "postgresql+asyncpg://u:p@h:5432/db"


# --- Story 3.4: the payment batch's cadence -------------------------------


def test_the_default_cadence_is_the_prototypes_tuesdays_and_fridays() -> None:
    """`date.weekday()` is Monday-first, so Tuesday is 1 and Friday is 4.

    Worth an assertion because the prototype counts from a *different* origin:
    JavaScript's `getDay()` is Sunday-first, and its `getDay()===2||===5` names
    the same two days from 2 and 5. Transliterating those integers would have
    shifted the whole schedule by a day.
    """
    assert Settings().payment_batch_weekday_numbers == frozenset({1, 4})


@pytest.mark.parametrize(
    "configured, expected",
    [
        ("mon", {0}),
        ("Mon, Thu", {0, 3}),
        ("monday,thursday", {0, 3}),
        ("sun", {6}),
        ("tue, tue", {1}),
    ],
)
def test_the_cadence_is_parsed_from_day_names(configured: str, expected: set[int]) -> None:
    """Day names rather than integers, because this is a value an operator
    reads and edits: `[1,4]` is a value nobody can check at a glance. Full
    names and abbreviations both work, and a repeat is a set."""
    assert Settings(payment_batch_weekdays=configured).payment_batch_weekday_numbers == frozenset(
        expected
    )


def test_an_unparseable_day_stops_the_process_naming_what_was_typed() -> None:
    """A `list[int]` field would take `[9]` happily and the batch would then
    never run, silently, for ever. This fails at read time with the token."""
    settings = Settings(payment_batch_weekdays="mon,Funday")
    with pytest.raises(ValueError, match="Funday"):
        _ = settings.payment_batch_weekday_numbers


def test_an_empty_cadence_is_refused_rather_than_meaning_never() -> None:
    settings = Settings(payment_batch_weekdays=" , ")
    with pytest.raises(ValueError, match="never run"):
        _ = settings.payment_batch_weekday_numbers


def test_the_scheduler_is_off_under_e2e_and_on_elsewhere() -> None:
    """A determinism property, not a deployment preference: a suite whose stack
    pays invoices on a wall clock cannot assert what a claim's figures are. The
    e2e profile triggers the batch explicitly instead."""
    assert Settings(env=Env.e2e).scheduler_runs is False
    assert Settings(env=Env.dev).scheduler_runs is True
    # An explicit setting still wins in both directions, so a developer can
    # watch the real thing run.
    assert Settings(env=Env.e2e, scheduler_enabled=True).scheduler_runs is True
    assert Settings(env=Env.dev, scheduler_enabled=False).scheduler_runs is False
