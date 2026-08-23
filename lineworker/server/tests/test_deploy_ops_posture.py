"""NFR-8's operational posture, read out of the deploy files (Story 8.4, AC 2).

Story 8.2 asserted that the profiles terminate TLS and Story 8.3 that they take
backups. What neither could assert is the half AC 2 actually asks for: that
every container has a health surface, that the healthchecks *gate startup
order* in dev and prod, that the prod profile survives a reboot, that it cannot
render on the credentials this repository publishes, and that the database
refuses an unencrypted connection rather than merely offering an encrypted one.
Those are five properties of five files, and until this story none of them was
checked by anything.

## Matched as text, not parsed

Inherited wholesale from `tests/test_deploy_tls_posture.py`, whose module
docstring argues it: a YAML parser is a dependency this suite does not declare,
and `docker compose config` is a docker daemon it does not require. With it
comes that file's convention, which is not a style rule but the thing that keeps
these tests honest — **presence checks may use `read`, absence and count checks
must use `directives`** — because the deploy files here argue for every setting
they carry and the prose therefore quotes the settings. `compose.prod.yaml`
explains why `ENV: prod` is absent by writing `ENV: prod`; this file's own
subject matter includes a comment block explaining why `restart:` is *not* in
the dev base, which a whole-file `"restart:" not in text` would read as the
opposite of what it says.

## Anti-vacuity

Story 8.3's review found `after.find("\\n  ")` matching at index 0 — a service's
own first nested line is a newline followed by two spaces — so three profile
checks were slicing the empty string and passing against a file that contained
the very thing they forbade. Every block this file slices is therefore bounded
on a **sibling key at a known indent** and guarded with a non-empty assertion
before anything is asserted about its contents, and every set derived from a
file is asserted non-empty before it is compared. A check that cannot fail is
worse than no check, because it also occupies the place where a real one would
go (AD-15).

## What is deliberately NOT asserted here

That the merged document compose actually produces has these properties. These
are assertions about the files' words. `.github/workflows/ci.yaml` renders all
four profiles and asserts against the rendered JSON, which is what catches the
class of mistake text cannot see — a misspelled `!override` tag, a key at the
wrong indent, an overlay that appends where it meant to replace. The two layers
are complementary and neither is sufficient: the render needs a daemon, and the
text check is what a reviewer reads.
"""

import re
from pathlib import Path
from typing import Final

import pytest

from tests.test_deploy_tls_posture import DEPLOY, HARDENED_PROFILES, directives, read

#: The profiles that *declare* services rather than overlaying them. Everything
#: about a service that prod does not restate, prod inherits from `compose.yaml`
#: — which is what an overlay is for, and why several assertions below check the
#: declaring profiles for a directive and check prod only for the service.
DECLARING_PROFILES: Final[tuple[str, ...]] = ("compose.yaml", "compose.e2e.yaml")

#: A service key: two spaces, a name, a colon, end of line. Top-level keys
#: (`name:`, `services:`, `volumes:`) have no indent and everything inside a
#: service has four or more, so this matches services and nothing else.
SERVICE_KEY: Final[re.Pattern[str]] = re.compile(
    r"^ {2}([A-Za-z][A-Za-z0-9_-]*):[ \t]*(?:#.*)?$", re.MULTILINE
)

#: Where a service block ends: the next sibling service, or the next top-level
#: key. **Not** `"\n  "` — see the anti-vacuity note in the module docstring.
SERVICE_BOUNDARY: Final[re.Pattern[str]] = re.compile(
    r"\n {2}[a-z][a-z0-9_-]*:|\n[a-z][a-z0-9_-]*:"
)

#: Where a four-space key's block ends: the next four-space key.
NESTED_BOUNDARY: Final[re.Pattern[str]] = re.compile(r"\n {4}[a-z][a-z0-9_-]*:")

#: `NAME: value` as a compose `environment:` mapping writes it.
ENV_ASSIGNMENT: Final[re.Pattern[str]] = re.compile(
    r"^\s*([A-Z][A-Z0-9_]*):\s*(\S.*)$", re.MULTILINE
)

#: `${VAR:?message}` — a variable the render refuses to proceed without.
GUARDED_VARIABLE: Final[re.Pattern[str]] = re.compile(r"\$\{([A-Z][A-Z0-9_]*):\?")

#: `${VAR` in any form — the set of names a compose file interpolates at all.
INTERPOLATED_VARIABLE: Final[re.Pattern[str]] = re.compile(r"\$\{([A-Z][A-Z0-9_]*)[:?}-]")

#: The two credentials whose dev defaults are published in this repository.
DEV_CREDENTIAL_DEFAULTS: Final[tuple[str, ...]] = ("lineworker_app_dev", "lineworker_dev")

#: The prod-only client-authentication file and the path it is mounted at.
PROD_HBA: Final[str] = "pg_hba.prod.conf"
HBA_MOUNT_TARGET: Final[str] = "/etc/postgresql/pg_hba.conf"

#: The banner Story 8.4 adds to `.env.example`, in the file's house style.
DEPLOYMENT_SECTION_HEADER: Final[str] = (
    "# --- Production boot and required credentials (Story 8.4) ---"
)


def services_section(profile: str) -> str:
    """Everything under `services:`, and nothing under `volumes:`.

    Bounded because the top-level `volumes:` mapping indents its names by two
    spaces exactly as service names are, so a whole-file scan for a two-space
    key reports `pgdata` and `blobdata` as services — and then slices a "service
    block" out of a one-line mapping entry and comes back with the empty string.
    Which is the Story 8.3 vacuity failure arriving by a different route, in the
    same file, the first time this test was run.
    """
    text = directives(profile)
    match = re.search(r"^services:$", text, re.MULTILINE)
    assert match is not None, f"deploy/{profile} has no `services:` key"
    after = text[match.end() :]
    boundary = re.search(r"\n[a-z][a-z0-9_-]*:", after)
    section = after[: boundary.start()] if boundary else after
    assert section.strip(), f"deploy/{profile}'s services section sliced to nothing"
    return section


def service_names(profile: str) -> tuple[str, ...]:
    """Every service a profile declares, derived from the file.

    Derived rather than listed, and that is the whole point: a hardcoded tuple
    passes for ever on the day somebody adds a sixth service, which is exactly
    the moment the "every container has a healthcheck" rule needs to fire.
    """
    names = tuple(SERVICE_KEY.findall(services_section(profile)))
    assert names, f"deploy/{profile} declares no services at all — did the layout move?"
    return names


def service_block(profile: str, name: str) -> str:
    """One service's directives, bounded at the next sibling key.

    Bounded on a **sibling service key**, not on any two-space indent: the
    service's own first nested line is a newline followed by two spaces, so
    `find("\\n  ")` returns 0 and the block is the empty string — which is how
    Story 8.3 shipped three assertions that passed against `ports:` under the
    one service that must never have one. The non-empty guard below is the
    second half of that fix: it fails loudly if the slice ever comes out empty
    again, rather than letting every assertion built on it silently succeed.
    """
    text = services_section(profile)
    match = re.search(rf"^ {{2}}{re.escape(name)}:$", text, re.MULTILINE)
    assert match, f"deploy/{profile} declares no service {name!r}"
    after = text[match.end() :]
    boundary = SERVICE_BOUNDARY.search(after)
    block = after[: boundary.start()] if boundary else after
    assert block.strip(), f"deploy/{profile}'s {name} block sliced to nothing"
    return block


def depends_on_block(profile: str, name: str) -> str:
    """The `depends_on:` mapping of one service, or `""` when it declares none.

    Empty is a legitimate answer — `postgres` waits for nothing — so this one
    cannot carry a non-empty guard. Every caller therefore states which of the
    two it is asserting: that a chain exists, or that a condition it contains is
    not `service_healthy`.
    """
    block = service_block(profile, name)
    match = re.search(r"^ {4}depends_on:$", block, re.MULTILINE)
    if match is None:
        return ""
    after = block[match.end() :]
    boundary = NESTED_BOUNDARY.search(after)
    section = after[: boundary.start()] if boundary else after
    assert section.strip(), f"deploy/{profile}'s {name} declares an empty depends_on"
    return section


def depends_on_conditions(profile: str, name: str) -> dict[str, str]:
    """`{dependency: condition}` for one service, read out of its block."""
    section = depends_on_block(profile, name)
    return {
        dependency: condition
        for dependency, condition in re.findall(
            r"^ {6}([a-z][a-z0-9_-]*):\n {8}condition:\s*(\S+)$", section, re.MULTILINE
        )
    }


def hba_records(name: str) -> list[list[str]]:
    """The non-comment, non-blank records of one `pg_hba` file, as token lists."""
    path = DEPLOY / "postgres" / name
    assert path.is_file(), f"{path} does not exist"
    records = [
        line.split()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert records, f"deploy/postgres/{name} contains no records at all"
    return records


def is_loopback(record: list[str]) -> bool:
    """A record the container's own entrypoint and healthcheck use.

    `local` is the unix socket; the two loopback addresses are what
    `/docker-entrypoint-initdb.d` and `pg_isready` reach the server on. None of
    them crosses the compose bridge, which is what the `hostssl` rule is about.
    """
    if record[0] == "local":
        return True
    return len(record) > 3 and record[3].startswith(("127.0.0.1", "::1"))


# --------------------------------------------------------------------------
# AC 2, first half: a health surface on every container, gating startup order.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("profile", DECLARING_PROFILES)
def test_every_service_in_every_declaring_profile_declares_a_healthcheck(profile: str) -> None:
    """AC 2's "every container exposes a health endpoint", as a property of the files.

    Driven off `service_names`, which reads the file, rather than off a list —
    a hardcoded tuple of five services is a test that goes on passing on the day
    a sixth arrives, which is the only day this rule has any work to do. The
    healthchecks are what `depends_on: condition: service_healthy` and
    `up -d --wait` both rest on, so a service without one is a service the boot
    cannot tell apart from a service that has crashed.

    Only the declaring profiles: `compose.prod.yaml` is an overlay and inherits
    every healthcheck it does not restate (it restates exactly one, `web`'s,
    because the base check asks for `http://` and the prod ingress answers 308
    there). The prod side is the test below.
    """
    missing = [
        name
        for name in service_names(profile)
        if "healthcheck:" not in service_block(profile, name)
    ]
    assert missing == [], (
        f"deploy/{profile} declares {missing} with no healthcheck. AC 2 requires a health "
        "surface on every container, and without one `depends_on: service_healthy` and "
        "`up --wait` cannot distinguish a starting container from a broken one."
    )


def test_the_prod_overlay_adds_no_service_that_has_no_healthcheck_to_inherit() -> None:
    """The overlay half of the rule above, and it is the one that can be gamed.

    An overlay may legitimately declare a service with nothing but a `restart:`
    policy — `ollama` does exactly that here — and such a service is *not*
    missing a healthcheck, it is inheriting one. What would be missing is a
    service that exists **only** in the overlay: it would inherit nothing, boot
    with no health surface, and pass the test above because that test does not
    read this file.
    """
    base = set(service_names("compose.yaml"))
    # Both overlays, not just prod. `compose.gpu.yaml` is an overlay too, and a
    # service declared only there would inherit no healthcheck, no image and no
    # build context exactly as a prod-only one would — the identical hole, one
    # file over, left open because the first version of this test named prod.
    for overlay in ("compose.prod.yaml", "compose.gpu.yaml"):
        orphans = [name for name in service_names(overlay) if name not in base]
        assert orphans == [], (
            f"deploy/{overlay} declares {orphans}, which deploy/compose.yaml does not. "
            "An overlay-only service inherits no healthcheck, no image and no build "
            "context — it belongs in the base file with the rest of the stack."
        )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_the_startup_chain_gates_postgres_then_api_then_web(profile: str) -> None:
    """AC 2's "compose healthchecks gate startup order in dev and prod".

    `web` serves a shell that calls the api on first paint and `api` runs
    `alembic upgrade head` in its entrypoint, so the order is not cosmetic:
    without the gate, `up` starts all three at once, alembic races an
    initialising cluster and the ingress serves a shell whose first request
    502s. `service_healthy` rather than `service_started` on both links,
    because "the container was created" says nothing about whether the database
    accepts connections — that distinction is the entire content of the rule.

    Checked in prod as well as dev, which is where the assertion has to be
    careful: the overlay restates neither `depends_on`, so prod's chain *is* the
    base's, and the honest check is that prod has not overridden it into
    something weaker. A restated chain must still say `service_healthy`; an
    absent one inherits, which is correct.
    """
    asserted = 0
    for service, dependency in (("api", "postgres"), ("web", "api")):
        conditions = depends_on_conditions(profile, service)
        if profile == "compose.prod.yaml" and not conditions:
            # Inherits the base chain, which is asserted on its own file. Counted
            # rather than silently `continue`d: with both links inherited this
            # loop used to execute no assertion at all and still show a green
            # parametrized tick — a pass occupying the place a check would go,
            # which is the vacuity this file's own docstring refuses.
            continue
        asserted += 1
        assert conditions.get(dependency) == "service_healthy", (
            f"deploy/{profile}: {service} does not wait for {dependency} to be healthy "
            f"(got {conditions.get(dependency)!r}). The chain is postgres → api → web and "
            "every link is `service_healthy` — `service_started` would let alembic race an "
            "initialising cluster and the ingress serve a shell whose first request 502s."
        )

    # …and the chain is anchored: postgres waits for nothing, so the ordering is
    # a chain rather than a cycle nobody notices until `up` hangs.
    assert "postgres" not in depends_on_conditions(profile, "postgres")

    if asserted == 0:
        pytest.skip(
            f"deploy/{profile} restates no depends_on link; the chain it runs is "
            "deploy/compose.yaml's, asserted there. Skipped rather than passed so this "
            "profile does not report a green check it never performed."
        )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_the_api_never_waits_for_the_model_server_to_be_healthy(profile: str) -> None:
    """AD-14, asserted as an absence, because restoring it looks like a fix.

    `ollama`'s healthcheck means "both models are pulled" — that is deliberate
    and unchanged, and it is the AI-availability signal the copilot's
    degradation path reads. What it must never be again is a **gate on the
    console**: with `condition: service_healthy` and a 30-minute `start_period`
    behind it, a host that cannot reach the model registry gets no claim system
    at all, which is the hard boot dependency AD-14 forbids and the reason
    Story 6.6 built `agents/degradation.py`.

    Written as an absence and parametrized across every profile because
    `service_healthy` under `ollama:` is the most natural-looking line in this
    repository — it is what every other dependency here says, it is what this
    one said until Story 8.4, and every other test would stay green.
    """
    conditions = depends_on_conditions(profile, "api")
    if "ollama" not in conditions:
        # compose.e2e.yaml has no ollama at all (a deterministic stub instead),
        # and compose.prod.yaml inherits the base file's depends_on. Skipped
        # rather than returned: one real assertion presented as three green ticks
        # overstates the coverage of the single most re-addable line in this repo.
        pytest.skip(
            f"deploy/{profile} declares no api→ollama dependency to check "
            "(no ollama service, or the base file's depends_on is inherited unchanged)."
        )
    assert conditions["ollama"] != "service_healthy", (
        f"deploy/{profile} makes the api wait for ollama to be healthy. That healthcheck "
        "means 'both models are pulled', so this is a boot-time dependency on a "
        "multi-gigabyte download — AD-14 forbids exactly that, and Story 6.6's "
        "degradation seam exists so it is unnecessary. Use `service_started`."
    )
    assert conditions["ollama"] == "service_started", (
        f"deploy/{profile} gives the api an unexpected condition on ollama: "
        f"{conditions['ollama']!r}. Ordering is kept (`service_started`); readiness is a "
        "signal, not a gate."
    )


# --------------------------------------------------------------------------
# AC 2, second half: the prod profile is a production profile.
# --------------------------------------------------------------------------


def test_every_prod_service_restarts_unless_stopped() -> None:
    """A stack with no restart policy does not survive a host reboot.

    Compose's default is `no`: a container that exits stays exited, and a 03:00
    kernel update leaves the claims console down until a handler notices at
    08:00. `unless-stopped` rather than `always` so that a deliberate
    `docker compose stop` — every maintenance step in `DEPLOYMENT.md` and
    `RESTORE-DRILL.md` begins with one — is not silently undone the next time
    the daemon restarts.

    Derived from the profile's own service list, so a service added to the
    overlay without a policy fails here rather than being the one container
    that does not come back.
    """
    missing = [
        name
        for name in service_names("compose.prod.yaml")
        if "restart: unless-stopped" not in service_block("compose.prod.yaml", name)
    ]
    assert missing == [], (
        f"deploy/compose.prod.yaml declares {missing} without `restart: unless-stopped`. "
        "A production stack with no restart policy does not survive a host reboot, and a "
        "backup container that stayed down would simply stop taking backups — the "
        "healthcheck that would say so is inside the container that is not running."
    )


@pytest.mark.parametrize("profile", ("compose.yaml", "compose.e2e.yaml", "compose.gpu.yaml"))
def test_no_restart_policy_leaks_into_the_dev_or_e2e_profiles(profile: str) -> None:
    """The other direction, and it is not symmetry for its own sake.

    `docker compose up` on a laptop must leave nothing behind that starts itself
    again after a reboot, and a crash-looping container in dev is a thing you
    want to stay crashed so you can read why. In the e2e profile it would be
    worse than untidy: a spec that stops a container to assert a degradation
    path would find it back up before the assertion ran.

    `directives`, not `read`: `compose.prod.yaml`'s policy block explains at
    length why the dev base deliberately has none, and a whole-file search would
    make writing that explanation the thing that fails this test.
    """
    assert "restart:" not in directives(profile), (
        f"deploy/{profile} carries a restart policy. It is prod-only: a dev stack that "
        "restarts itself after a reboot is a surprise, and a crash loop you want to read "
        "is a crash loop that must stay crashed."
    )


def test_the_prod_render_cannot_fall_back_to_the_published_dev_passwords() -> None:
    """The hazard `compose.prod.yaml:132–137` deferred to this story by name.

    `${APP_DB_PASSWORD:-lineworker_app_dev}` in a prod render produces a stack
    that comes up, initialises its database with a password published in this
    repository, and serves the claim book behind it — with every healthcheck
    green and nothing anywhere to notice. A `:?` guard fails `docker compose
    config` instead, before an image is pulled, naming the variable.

    Derived from the base file rather than listed, which is the load-bearing
    part: the two URLs were the obvious places and there were **five**.
    `APP_DB_PASSWORD` is passed to the api as a variable in its own right (the
    entrypoint reconciles the role's password to it on every boot),
    `POSTGRES_PASSWORD` initialises the cluster, and `BACKUP_DATABASE_URL`
    interpolates it inside a URL where a fallback does not look like one. Any
    sixth place added to the base file later fails here until prod restates it.
    """
    base_assignments = ENV_ASSIGNMENT.findall(directives("compose.yaml"))
    unguarded = {
        key
        for key, value in base_assignments
        if any(f"${{{name}:-" in value for name in ("APP_DB_PASSWORD", "POSTGRES_PASSWORD"))
    }
    assert unguarded, (
        "no dev-defaulted credential interpolation found in deploy/compose.yaml at all — "
        "this test derives its own subject from that file and has just derived nothing."
    )

    # A list of every occurrence, not a `dict`. The same variable name is set under
    # more than one service here — `POSTGRES_PASSWORD` under `api`, `postgres` and
    # `backup` — and `dict(...)` keeps only the last, so an earlier occurrence that
    # had lost its guard would never be looked at while the final one carried it.
    prod_pairs = ENV_ASSIGNMENT.findall(directives("compose.prod.yaml"))
    for key in sorted(unguarded):
        values = [value for name, value in prod_pairs if name == key]
        assert values, (
            f"deploy/compose.yaml sets {key} from a dev-defaulted credential and "
            "deploy/compose.prod.yaml does not restate it, so the prod render inherits the "
            "published password. Restate it with a `${…:?}` guard."
        )
        for value in values:
            assert ":?" in value, (
                f"deploy/compose.prod.yaml restates {key} without a `${{…:?}}` guard: "
                f"{value!r}. A prod render that silently supplies the dev password is the "
                "hazard this story exists to close."
            )

    survivors = [
        default for default in DEV_CREDENTIAL_DEFAULTS if default in directives("compose.prod.yaml")
    ]
    assert survivors == [], (
        f"deploy/compose.prod.yaml still names {survivors}. Those are the synthetic dev "
        "credentials this repository publishes; a prod profile must not be able to render "
        "with one."
    )


def test_the_prod_profile_sets_every_setting_whose_default_keys_off_env() -> None:
    """The overlay renders `ENV: dev`, so nothing may be inherited from it.

    `api/app.py::create_app` refuses `Env.prod` while persona login is the
    authentication mechanism (Story 1.3's standing decision, and the Deferred
    IdP work is what lifts it), so this profile declares `ENV: dev` and will go
    on doing so. A profile named prod that renders `ENV: dev` is telling two
    stories, and every default that derives from `settings.env` resolves to the
    dev branch under it — `SESSION_COOKIE_SECURE` off behind a TLS ingress being
    the one that is a security control rather than a tidy-up.

    **The list is read out of `config.py`, not written here.** A tuple of two
    names in this file would pass for ever on the day a third `env`-keyed
    default is added, which is precisely the day it would be inherited from a
    flag the file itself admits is wrong. The mapping from property name to
    environment variable is the field each property falls back to, which is why
    the property source is searched for the field rather than for the property.
    """
    config = (Path(__file__).resolve().parents[1] / "config.py").read_text(encoding="utf-8")
    env_keyed = re.findall(
        r"def (\w+)\(self\) -> bool:\n"
        r"\s*if self\.(\w+) is not None:\n"
        r"\s*return self\.\w+\n"
        r"\s*return self\.env is",
        config,
    )
    assert env_keyed, (
        "no `env`-keyed setting found in server/config.py — this test derives its subject "
        "from that file and has derived nothing, so it is asserting about the empty set. "
        "If the derivation shape changed, fix the pattern; do not delete the test."
    )

    prod = directives("compose.prod.yaml")
    for prop, field in env_keyed:
        variable = field.upper()
        assert re.search(rf"^ +{variable}:", prod, re.MULTILINE), (
            f"deploy/compose.prod.yaml does not set {variable}. `Settings.{prop}` defaults "
            "off `settings.env`, and this overlay renders `ENV: dev` — so leaving it "
            "unset resolves it to the development branch behind a TLS ingress. Set it "
            "explicitly, and if the dev value is the one prod wants, say so in a comment."
        )


# --------------------------------------------------------------------------
# AC 2, third half: the database refuses plaintext rather than tolerating it.
# --------------------------------------------------------------------------


def test_the_prod_profile_mounts_the_hostssl_hba_and_no_other_profile_does() -> None:
    """Design Note 5: one file cannot be two policies, so there are two files.

    The mount swap is the mechanism `nginx/tls.conf` already uses over the baked
    `nginx/default.conf`, and it is chosen for the same reason: dev and e2e have
    no certificates, so a shared `hostssl` file would refuse every connection on
    every laptop and take the whole e2e gate with it.

    Both directions. Missing from prod, and `ssl=on` goes back to permitting TLS
    without requiring it — the deferral this story closes. Present in dev or
    e2e, and `docker compose up` stops working on a clean checkout, which is
    Story 1.1's standing promise.
    """
    prod = directives("compose.prod.yaml")
    assert f"./postgres/{PROD_HBA}:{HBA_MOUNT_TARGET}:ro" in prod, (
        "deploy/compose.prod.yaml does not mount the prod pg_hba over the dev one. Without "
        "it `ssl=on` permits TLS and refuses nothing, so a client that omits `sslmode` "
        "reaches the claim book in plaintext."
    )
    for profile in ("compose.yaml", "compose.e2e.yaml", "compose.gpu.yaml"):
        assert PROD_HBA not in directives(profile), (
            f"deploy/{profile} references {PROD_HBA}. Its records are `hostssl` and there "
            "are no certificates in dev or e2e, so every connection would be refused."
        )
        if profile != "compose.gpu.yaml":
            assert f"./postgres/pg_hba.conf:{HBA_MOUNT_TARGET}:ro" in directives(profile), (
                f"deploy/{profile} no longer mounts the dev pg_hba; `-c hba_file=` names a "
                "path with nothing at it and the postmaster refuses to start."
            )


def test_every_non_loopback_record_in_the_prod_hba_requires_tls() -> None:
    """The server-side half of "TLS to Postgres is enforced, not permitted".

    Every connection that crosses the compose bridge is here: the api's two
    pools, the copilot's psycopg pool, `alembic upgrade head` at boot, the daily
    audit-redaction sweep, and the backup container's `pg_basebackup`,
    `pg_receivewal` and `pg_dump` — the last of which moves a byte-for-byte copy
    of the database. `hostssl` is what refuses an unencrypted one instead of
    accepting it silently.

    The loopback rows are asserted to be **unchanged**, which is the half that
    is easy to get wrong in the safe-looking direction: the image's entrypoint
    bootstraps the cluster and every compose healthcheck runs `pg_isready` over
    them, before `ssl=on` means anything and before the certificate has been
    read. `hostssl` there is a container that cannot initialise itself and a
    healthcheck that never passes, with no log line explaining why.
    """
    records = hba_records(PROD_HBA)
    network = [record for record in records if not is_loopback(record)]
    assert network, f"deploy/postgres/{PROD_HBA} has no non-loopback records at all"
    for record in network:
        assert record[0] == "hostssl", (
            f"deploy/postgres/{PROD_HBA} authenticates a network connection with "
            f"{record[0]!r}: {record}. Every non-loopback record must be `hostssl` — "
            "`host` accepts a client that simply omitted `sslmode`, which is the whole "
            "gap this file exists to close."
        )
        method = record[4] if len(record) > 4 else ""
        assert method == "scram-sha-256", (
            f"deploy/postgres/{PROD_HBA} uses {method!r} for {record}. TLS is the transport; "
            "it is not an authentication method."
        )

    for record in (record for record in records if is_loopback(record)):
        assert record[0] in {"local", "host"}, (
            f"deploy/postgres/{PROD_HBA} makes a loopback record {record[0]!r}: {record}. The "
            "image's entrypoint and every `pg_isready` healthcheck run over these before "
            "the server certificate has been read; requiring TLS there is a cluster that "
            "cannot initialise itself."
        )


def test_the_two_hba_files_differ_in_nothing_but_the_hostssl_keyword() -> None:
    """Two files at one path is a copy, and a copy's cost is drift.

    The same trade `tls.conf`/`default.conf` and the restated postgres
    `command:` both make, and paid for the same way. A record added to the dev
    file and forgotten here is a prod database that authenticates differently
    from the one every test in this repository runs against — and it fails
    *closed*, at somebody's deployment, with `no pg_hba.conf entry`.

    Compared record by record in order, because `pg_hba` is first-match-wins: a
    file with the same set of records in a different order is a different
    policy.
    """
    dev = hba_records("pg_hba.conf")
    prod = hba_records(PROD_HBA)
    assert len(dev) == len(prod), (
        f"deploy/postgres/pg_hba.conf has {len(dev)} records and {PROD_HBA} has {len(prod)}. "
        "The prod file is the dev file with `host` replaced by `hostssl` on the network "
        "records and nothing else; a record in one and not the other is drift."
    )
    for index, (dev_record, prod_record) in enumerate(zip(dev, prod, strict=True)):
        normalised = ["host" if token == "hostssl" else token for token in prod_record]
        assert normalised == dev_record, (
            f"record {index} differs beyond the keyword: {dev_record} vs {prod_record}. "
            "Only `host` → `hostssl` may differ between these two files."
        )


def test_the_backup_job_reaches_postgres_over_verified_tls_in_prod() -> None:
    """The one connection that carries a byte-for-byte copy of the database.

    Under the `hostssl` records above, a `BACKUP_DATABASE_URL` with no
    `sslmode` is refused outright — so this is not only a hardening step, it is
    what keeps the prod backup job working at all. `verify-full` rather than
    `require` for the reason both api URLs carry it: `require` encrypts and
    authenticates nothing, and an impostor on the compose network that this
    connection trusts receives the schema owner's password and answers a
    base-backup request with whatever it likes.

    The CA arrives as `PGSSLROOTCERT` and as a **single file** mount, matching
    the api's: `pg_basebackup`, `pg_dump` and `pg_receivewal` are all libpq
    clients and read it from the environment, and the container that holds an
    SSH key to another host has no business also holding the key that signs the
    database's certificate.
    """
    prod = directives("compose.prod.yaml")
    match = re.search(r"^\s*BACKUP_DATABASE_URL:\s*(\S.*)$", prod, re.MULTILINE)
    assert match is not None, (
        "deploy/compose.prod.yaml does not restate BACKUP_DATABASE_URL. The base file's "
        "default carries no sslmode, which the prod pg_hba refuses outright."
    )
    url = match.group(1)
    assert "sslmode=verify-full" in url, (
        f"deploy/compose.prod.yaml's BACKUP_DATABASE_URL is {url!r}. It must carry "
        "sslmode=verify-full: this connection moves the entire data directory and every "
        "subsequent write, and `hostssl` refuses it without one."
    )
    assert "PGSSLROOTCERT: /etc/postgresql/tls/ca.crt" in service_block(
        "compose.prod.yaml", "backup"
    ), (
        "the backup container has no PGSSLROOTCERT, so `verify-full` has no authority to "
        "verify against and every backup connection fails at the chain check."
    )
    assert "/ca.crt:/etc/postgresql/tls/ca.crt:ro" in service_block(
        "compose.prod.yaml", "backup"
    ), (
        "the backup container is not given the CA file. Mount the single ca.crt, never the "
        "directory — it also holds the database's private key."
    )


# --------------------------------------------------------------------------
# The template and the runbook: a knob nobody can find is not a knob.
# --------------------------------------------------------------------------


def test_the_template_writes_every_guarded_prod_variable_as_a_live_entry() -> None:
    """Set equality both ways, extending Story 8.3's rule to this story's knobs.

    Story 8.3's check covers the `BACKUP_*` namespace only, which is why the two
    database credentials could have acquired `${…:?}` guards with nothing
    holding `.env.example` to them. The rule is the file's own and it is stated
    in the file: a variable the prod overlay refuses to render without is
    written **uncommented**, because every other line here is a default the
    build already has and a copied template whose only mandatory settings are
    inert fails at `docker compose config` at the *end* of a deployment.

    The other direction matters as much: a live line nothing interpolates is
    advice that does nothing, and somebody will set it, restart the stack and
    believe the behaviour changed.
    """
    example = read(".env.example")
    assert DEPLOYMENT_SECTION_HEADER in example, (
        "deploy/.env.example has no Story 8.4 section; the house style is one banner per "
        "story and the tests read it."
    )

    guarded = set(GUARDED_VARIABLE.findall(directives("compose.prod.yaml")))
    assert guarded, "deploy/compose.prod.yaml guards no variable at all — nothing to check"

    live = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if not line.lstrip().startswith("#") and "=" in line
    }
    assert live, "deploy/.env.example has no live entries at all"

    missing = sorted(guarded - live)
    assert missing == [], (
        f"deploy/.env.example writes {missing} commented out or not at all. The prod "
        "overlay's `${…:?}` guard refuses to render without them, so a copied template "
        "must carry them as real lines."
    )

    interpolated = {
        name
        for profile in (*HARDENED_PROFILES, "compose.gpu.yaml")
        for name in INTERPOLATED_VARIABLE.findall(directives(profile))
    }
    inert = sorted(live - interpolated)
    assert inert == [], (
        f"deploy/.env.example writes {inert} as live settings that no compose file "
        "interpolates. Advice that does nothing is worse than no advice: somebody will "
        "set it and believe it."
    )


def test_the_boot_runbook_exists_and_has_the_sections_the_story_requires() -> None:
    """AC 2 and AC 3, held to the shape `RESTORE-DRILL.md` established.

    The `§ Executed boot` heading is asserted here rather than its contents, for
    the reason its sibling test in `test_backup_posture.py` gives: this runs in
    CI on every commit and the boot is executed once, by a person, against a
    real host. What CI can hold is that the document has somewhere for that
    record to go.

    § 8 is the AC 3 deliverable in full — five deferred decisions, each with the
    trigger that reopens it — and it is asserted item by item because a registry
    that lists four of five is worse than none: it reads as complete.
    """
    runbook = (DEPLOY / "DEPLOYMENT.md").read_text(encoding="utf-8")
    assert runbook.strip(), "deploy/DEPLOYMENT.md is empty"
    # Headings, not substrings. `"Smoke" in runbook` is satisfied by the word
    # appearing in any sentence of seven hundred lines, so a deleted § 6 would go
    # on passing as soon as anything else mentioned smoke — a check that stops
    # checking without ever going red. Anchored on the markdown heading itself.
    headings = {line.lstrip("#").strip() for line in runbook.splitlines() if line.startswith("##")}
    assert headings, "deploy/DEPLOYMENT.md has no markdown headings at all"
    for heading in (
        "What this deploys",
        "Prerequisites",
        "Volume encryption pre-flight",
        "Secrets and env",
        "Boot",
        "Health verification",
        "Smoke",
        "Operating notes",
        "Deliberately not built",
        "Executed boot",
    ):
        assert any(heading in found for found in headings), (
            f"deploy/DEPLOYMENT.md has no heading containing {heading!r} "
            f"(headings found: {sorted(headings)})"
        )

    for deferred in (
        "Identity provider",
        "Binary-store backend",
        "scheduler mechanism",
        "Observability",
        "Ollama capacity",
    ):
        assert deferred in runbook, (
            f"deploy/DEPLOYMENT.md § 8 does not name {deferred!r}. AC 3 lists five "
            "deliberately-not-built decisions and a registry missing one reads as complete."
        )

    assert "ARCHITECTURE-SPINE.md" in runbook, (
        "deploy/DEPLOYMENT.md must link the spine as the fuller Deferred registry rather "
        "than duplicating its prose — two copies of a list are two lists that drift."
    )
    assert "LUKS" in runbook or "dm-crypt" in runbook, (
        "deploy/DEPLOYMENT.md must carry the volume-encryption prerequisite (NFR-5). "
        "Compose cannot enforce it, which is exactly why it is a documented pre-flight "
        "step rather than a setting."
    )
