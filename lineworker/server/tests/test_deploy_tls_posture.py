"""AC 3 and the compose half of AC 1: the deploy files say what they must (NFR-5).

TLS at the ingress and on the API-to-Postgres connection cannot be booted by
CI. There are no certificates in version control — deliberately, and it is the
one thing about this story that is not negotiable — so the prod profile is
never started by anything automated, and "verified in configuration" is what
Story 8.2's AC 3 asks for in as many words. This file is that verification,
plus the two manual commands `deploy/.env.example` documents for a real
deployment.

It also carries the load the pgaudit posture cannot carry from inside the
database. `tests/test_pgaudit_posture.py` asks a *running* server what it is
configured to do, which proves the profile the suite happens to be pointed at.
The rule Story 8.2 actually states is stronger — the same posture, applied by
the same mechanism, in dev, e2e and prod, so drift cannot hide — and the only
way to check "the same" is to read all three files. That is why the flag list
below is one tuple driven across three paths rather than three assertions: a
flag added to `compose.yaml` and forgotten in `compose.e2e.yaml` fails here,
which is the exact mistake the restatement invites.

## Matched as text, not parsed

`tests/test_ai_insights.py::test_every_compose_profile_closes_all_six_tracing_
doors` states the reason and it holds here: a YAML parser is a dependency this
suite does not declare, and `docker compose config` is a docker daemon this
suite does not require. The cost is that these assertions are about the file's
words rather than about the merged document; the mitigation is that the words
being checked are the ones an operator reads in review.
"""

import re
from pathlib import Path
from typing import Final

import pytest

DEPLOY: Final[Path] = Path(__file__).resolve().parents[2] / "deploy"

#: The three profiles that must start postgres with the identical posture.
#:
#: `compose.gpu.yaml` is absent and stays absent: its header argues that it
#: holds exactly the GPU delta, and a postgres service appearing in it would be
#: the drift that argument exists to prevent.
HARDENED_PROFILES: Final[tuple[str, ...]] = (
    "compose.yaml",
    "compose.e2e.yaml",
    "compose.prod.yaml",
)

#: Every flag in AD-11's database-audit posture, exactly as a compose
#: `command:` entry spells it.
#:
#: One tuple, three files. The alternative — writing the assertions out per
#: profile — is how two of them come to say `pgaudit.log=ddl,role` and the third
#: `pgaudit.log=ddl,role,write`, with nothing to notice, which is the failure
#: the "identical mechanism in dev and prod" rule names.
POSTGRES_HARDENING_FLAGS: Final[tuple[str, ...]] = (
    "shared_preload_libraries=pgaudit",
    "pgaudit.log=ddl,role",
    "pgaudit.log_catalog=off",
    "pgaudit.log_parameter=off",
    "pgaudit.log_relation=off",
    "pgaudit.log_statement_once=on",
    "log_statement=none",
    "log_duration=off",
    "log_min_duration_statement=-1",
    "log_min_error_statement=panic",
    "log_parameter_max_length=0",
    "log_parameter_max_length_on_error=0",
    "logging_collector=on",
    "log_destination=stderr",
    "log_directory=log",
    "log_filename=postgresql-%a.log",
    "log_rotation_age=1d",
    "log_rotation_size=0",
    "log_truncate_on_rotation=on",
)

#: pgaudit classes that would put statement text or bound parameters in the log.
#: Searched for as `pgaudit.log=` values rather than as bare words, because
#: "read" and "write" appear in every one of these files' prose.
FORBIDDEN_PGAUDIT_CLASSES: Final[tuple[str, ...]] = (
    "pgaudit.log=all",
    "pgaudit.log=read",
    "pgaudit.log=write",
    "pgaudit.log=function",
    "pgaudit.log_parameter=on",
)

#: Directives that terminate TLS at Postgres. Prod-only by Story 1.1's standing
#: decision: dev and e2e stay plain, and the e2e suite drives http://localhost:8081.
POSTGRES_TLS_FLAGS: Final[tuple[str, ...]] = (
    "ssl=on",
    "ssl_cert_file=/etc/postgresql/tls/server.crt",
    "ssl_key_file=/etc/postgresql/tls/server.key",
)

#: The SSE settings the copilot stream depends on. Copied from `default.conf`
#: into `tls.conf` rather than included, so they are the thing most likely to
#: drift between the twins — and the drift would show up as a copilot answer
#: that arrives all at once at the end of a run, or not at all.
SSE_PROXY_SETTINGS: Final[tuple[str, ...]] = (
    "proxy_buffering off;",
    "proxy_cache off;",
    "proxy_read_timeout 3600s;",
    "proxy_http_version 1.1;",
    'proxy_set_header Connection "";',
)


def read(name: str) -> str:
    """One file under `deploy/`, with the same vacuity guard every scan here has."""
    path = DEPLOY / name
    assert path.is_file(), f"{path} does not exist — did the deploy layout move?"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty"
    return text


def directives(name: str) -> str:
    """`read`, with the comment lines removed — for assertions that count.

    The deploy files in this repository argue for every setting they carry, at
    length, which means the prose quotes the settings. `compose.prod.yaml`'s
    header explains why the Postgres certificate must name `postgres` and says
    `sslmode=verify-full` while doing it, and explains that `ENV: prod` is
    deliberately absent by writing `ENV: prod`. A whole-file `count(…) == 2` or
    `"…" not in text` then measures the documentation instead of the
    configuration, and the failure it produces is a test breaking because
    somebody explained something — which is `tests/test_layering.py`'s lesson
    and the reason `scripts/lint_log_phi.py` reads an AST rather than grepping.

    Presence checks are safe on the raw text (a flag named only in a comment is
    a flag the reviewer still sees, and the `command:` list is what the
    hardening tests are reading). Counts and absence checks are not, so those
    read this instead.

    Full-line comments only, which is all these files use; an inline `# …` after
    a value would still be counted, and is left alone rather than parsed,
    because stripping one correctly means knowing where the quotes are.
    """
    return "\n".join(line for line in read(name).splitlines() if not line.lstrip().startswith("#"))


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_every_profile_starts_postgres_with_the_same_hardening_flags(profile: str) -> None:
    """AC 1's "identical mechanism in dev and prod so drift can't hide".

    The list is restated in three files because a compose overlay *replaces* a
    `command:` rather than merging it — an overlay carrying only the TLS flags
    would silently drop pgaudit and every logging GUC with it, which is the one
    failure this story could not detect from inside the database. That cost is
    paid here rather than by a comment asking people to remember.
    """
    text = read(profile)
    missing = [flag for flag in POSTGRES_HARDENING_FLAGS if flag not in text]
    assert missing == [], (
        f"deploy/{profile} does not start postgres with {missing}. The posture is "
        "applied in every profile by the same mechanism (AD-11); a flag in one file "
        "and not the others is exactly the drift this test exists to find."
    )


def pgaudit_classes(text: str) -> list[frozenset[str]]:
    """The class set behind every `pgaudit.log=` in a profile, one entry each.

    The only value in these files that is *parsed* rather than matched, and the
    reason is that this one flag's value is a list. Every other assertion here
    is a presence check on a whole `name=value` string, which is exactly strong
    enough for a scalar: `log_statement=none` either appears or it does not.
    `pgaudit.log=ddl,role`, matched the same way, is a **prefix** of
    `pgaudit.log=ddl,role,write` — so the string check passes on a profile that
    logs every write, and `FORBIDDEN_PGAUDIT_CLASSES` only catches the spellings
    somebody thought to enumerate.

    A list rather than a set of the whole file, because "the value is right"
    has to hold for each occurrence: a second `-c pgaudit.log=` later in a
    `command:` wins at run time, and a check that unioned them would read a
    weakened profile as a widened one.
    """
    found: list[frozenset[str]] = []
    for line in text.splitlines():
        _, separator, value = line.partition("pgaudit.log=")
        if not separator:
            continue
        found.append(frozenset(part.strip() for part in value.strip().split(",") if part.strip()))
    return found


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_no_profile_asks_pgaudit_to_log_a_dml_class(profile: str) -> None:
    """The negative half, and it is the one that matters.

    Every assertion above is satisfied by a file that *also* says
    `pgaudit.log=ddl,role,write` two lines further down — the flag list would
    still be present and the last `-c` would win. Value-level history belongs to
    `audit_event`, whose diffs the Story 8.1 cascade redacts in place; a
    database log line is a file on a volume that nothing in this system can go
    back and edit.

    So the class list is read as a **set and compared for equality**, not
    searched for forbidden words. `tests/test_pgaudit_posture.py` does the same
    against a running server and would catch a widened profile in dev, e2e and
    CI — but `compose.prod.yaml` is never booted by anything, so for the profile
    with the real claim book behind it this text check is the only check there
    is, and a substring match is not one.
    """
    text = read(profile)
    present = [flag for flag in FORBIDDEN_PGAUDIT_CLASSES if flag in text]
    assert present == [], (
        f"deploy/{profile} enables {present}. That would make the DB log a second, "
        "unredactable copy of the claim book (AD-11 / AD-4)."
    )

    # `directives`, because this half is an equality rather than a search: a
    # comment quoting `pgaudit.log=ddl,role` while explaining the posture would
    # otherwise be read as a second, agreeing setting today and as a
    # *disagreeing* one the day somebody's prose used a different order.
    classes = pgaudit_classes(directives(profile))
    assert classes, f"deploy/{profile} sets no pgaudit.log at all — nothing is captured"
    assert classes == [frozenset({"ddl", "role"})] * len(classes), (
        f"deploy/{profile} sets pgaudit.log to {[sorted(entry) for entry in classes]}. "
        "The posture is exactly ddl and role: anything more logs statement text into a "
        "file the Story 8.1 purge cascade cannot reach, and anything less stops "
        "capturing the schema and privilege changes AD-11 asks for."
    )


@pytest.mark.parametrize("profile", ("compose.yaml", "compose.e2e.yaml"))
def test_every_declaring_profile_builds_the_pgaudit_image(profile: str) -> None:
    """`shared_preload_libraries=pgaudit` on an image without pgaudit is a
    postmaster that refuses to start.

    So the flag list and the image are one decision, and asserting the first
    without the second would let a profile fail at `up` with a "could not access
    file" error while passing every other test in this file.

    Only the two profiles that *declare* the postgres service are checked.
    `compose.prod.yaml` is an overlay: it inherits `build:` and `image:` from
    `compose.yaml` unchanged, which is what an overlay is for, and restating
    them there would be a second place to keep the tag correct — the very drift
    `compose.gpu.yaml`'s header argues against. What prod cannot inherit is the
    `command:`, because compose replaces it rather than merging, which is why
    that one *is* restated and is checked above in all three.
    """
    text = read(profile)
    assert "lineworker/postgres:pg18-pgaudit" in text, (
        f"deploy/{profile} does not name the pgaudit image; the stock "
        "pgvector/pgvector:pg18 has no pgaudit to preload and will not start under "
        "these flags."
    )
    assert "context: ./postgres" in text, (
        f"deploy/{profile} names the pgaudit image but does not build it, so a "
        "clean checkout has nothing to run."
    )
    assert "pgvector/pgvector:pg18" not in text, (
        f"deploy/{profile} still names the stock image somewhere; two image lines is "
        "how a profile ends up starting the wrong one."
    )


def test_the_postgres_tls_flags_are_in_the_prod_profile_and_nowhere_else() -> None:
    """AC 3's database half, and Story 1.1's plain-HTTP decision in the same test.

    Both directions, because each alone is half a rule. TLS missing from prod is
    the compliance failure; TLS *present* in dev or e2e is a stack that cannot
    start — there are no certificates in version control to mount, so `ssl=on`
    in `compose.e2e.yaml` would break the gate for everybody with an error
    about a missing file rather than about a bad idea.
    """
    prod = read("compose.prod.yaml")
    missing = [flag for flag in POSTGRES_TLS_FLAGS if flag not in prod]
    assert missing == [], f"deploy/compose.prod.yaml does not terminate TLS at postgres: {missing}"

    for profile in ("compose.yaml", "compose.e2e.yaml", "compose.gpu.yaml"):
        text = read(profile)
        present = [flag for flag in POSTGRES_TLS_FLAGS if flag in text]
        assert present == [], (
            f"deploy/{profile} carries {present}. TLS material is prod-only (Story 1.1's "
            "plain-HTTP decision stands for dev and e2e, and the e2e suite drives "
            "http://localhost:8081)."
        )


def test_the_prod_profile_reaches_postgres_over_verified_tls() -> None:
    """AC 3's API half: every URL, and the CA through the environment.

    `verify-full` rather than `require`, because `require` encrypts and
    verifies nothing — it defeats a passive listener and not an impostor that
    has joined the compose network. `PGSSLROOTCERT` rather than a `sslrootcert`
    URL parameter because asyncpg has no keyword for it and reads it from the
    environment (`config.py::_PERMITTED_URL_QUERY_PARAMS` admits `sslmode` and
    refuses every other query parameter outright, naming `PGSSLROOTCERT` for
    this one), and because psycopg honours the same name — one mechanism, both
    drivers.

    **Derived from the file rather than counted.** This assertion was
    `count(…) == 2` for the two api URLs, and Story 8.4 added a third database
    URL to this overlay — the backup job's, which under the `hostssl` records
    that story mounts is refused outright without it. A fixed count fails on the
    URL being *added*, which is the wrong direction: the rule is "every
    connection string in this overlay is verified", so every one of them is
    found and checked and a fourth is covered on the day it appears.
    """
    prod = directives("compose.prod.yaml")
    # To end of line, not `\S+`: a `${VAR:?message}` guard puts spaces inside
    # the URL, so a non-whitespace match stops at the first word of the message
    # and every URL then looks unverified. Found by this test failing against a
    # file that was correct — the same class of mistake `directives` exists for.
    urls = re.findall(r"^\s*\w+:\s*(postgresql://.*)$", prod, re.MULTILINE)
    assert len(urls) >= 3, (
        f"deploy/compose.prod.yaml carries {len(urls)} database URLs; the overlay restates "
        "at least DATABASE_URL, ALEMBIC_DATABASE_URL and BACKUP_DATABASE_URL. A missing one "
        "means the base file's unencrypted default survived into the prod render."
    )
    unverified = [url for url in urls if "sslmode=verify-full" not in url]
    assert unverified == [], (
        f"deploy/compose.prod.yaml carries {unverified} without sslmode=verify-full. The "
        "migration connection and the backup connection are as much PHI channels as the "
        "runtime one — the backup's carries a byte-for-byte copy of the database."
    )
    assert "PGSSLROOTCERT: /etc/postgresql/tls/ca.crt" in prod
    assert "sslrootcert=" not in prod, (
        "the CA must not be a URL parameter: asyncpg has no keyword for it and "
        "`Settings` refuses it, so this would fail at the first connection in prod."
    )


def test_the_api_container_gets_the_ca_and_not_the_databases_private_key() -> None:
    """Least privilege on the one mount that carries a signing key.

    `${PG_TLS_DIR}` holds `server.crt`, `server.key` and `ca.crt`, and postgres
    needs all three. The api needs exactly one of them: `PGSSLROOTCERT` points
    at `ca.crt` and the chain check is the whole of its interest in that
    directory. Mounting the directory into `api` as well — which is what this
    file did first, because it is the shorter line — hands the container that
    parses handler input, model output and uploaded documents the private key
    that signs the database's certificate. Anybody with a foothold there can
    then impersonate Postgres to the application, which is a strictly worse
    position than the one `verify-full` was added to prevent.

    Asserted in both directions: the narrow mount is present, and the directory
    mount appears exactly once in the file, which is postgres's.

    Story 8.4 added a **second** single-file CA mount, for `backup` — its
    connection now carries `sslmode=verify-full` and needs an authority to
    verify against, and it is the container that also holds an SSH key to
    another host, so the argument above applies to it more sharply rather than
    less. The count assertion below is unchanged and still says what it always
    said: however many services need `ca.crt`, exactly one gets the directory.
    """
    prod = directives("compose.prod.yaml")
    assert "/ca.crt:/etc/postgresql/tls/ca.crt:ro" in prod, (
        "deploy/compose.prod.yaml must mount the single CA file into api, not the "
        "directory that also holds the database's private key."
    )
    assert prod.count(":/etc/postgresql/tls:ro") == 1, (
        "the whole PG_TLS_DIR is mounted into more than one service. Only postgres "
        "needs server.key; api needs ca.crt and nothing else."
    )


def test_the_deployment_is_told_the_postgres_certificate_must_name_postgres() -> None:
    """`verify-full` checks the name in the URL, and the URL says `postgres`.

    asyncpg sets `check_hostname` for `verify-full`, and both prod URLs connect
    to `postgres` — the compose service name on the internal network. So the
    subjectAltName that has to match is `postgres`, *not* the hostname the
    deployment is reached at, and the ingress certificate and the Postgres
    certificate are therefore two different certificates.

    Nothing can check that from here: the certificates are not in version
    control and never will be. What can be checked is that the two files an
    operator reads before issuing them say so — this is the likeliest
    first-deployment failure the profile has, it surfaces as an api that starts
    and cannot reach its database, and a certificate is not a thing anybody
    re-issues casually.
    """
    for name, text in (
        ("compose.prod.yaml", read("compose.prod.yaml")),
        (".env.example", read(".env.example")),
    ):
        assert "subjectAltName" in text or "subject alternative name" in text.lower(), (
            f"deploy/{name} does not say the Postgres certificate must carry `postgres` "
            "in its SAN. verify-full fails at the first connection without it."
        )


def test_the_prod_profile_does_not_set_env_prod_because_the_api_refuses_to_boot_under_it() -> None:
    """The one line this overlay must NOT carry, and it looks like an omission.

    `api/app.py::create_app` raises on `settings.env is Env.prod` — Story 1.3's
    decision, unchanged: persona login *is* the authentication mechanism, so
    anybody who can reach the API can post a persona id and receive a full
    session, and the flag that declares production is the flag that has to
    refuse until the Deferred IdP decision lands. An overlay setting it would
    crash-loop the api and leave `web` (which waits on `service_healthy`)
    permanently down, so the whole profile would be unbootable.

    Asserted rather than commented because `ENV: prod` in a file named
    `compose.prod.yaml` is the most obvious-looking omission in this repository:
    the next reader adds it as an oversight fix, every other test here stays
    green, and the failure arrives at somebody's first deployment. TLS is
    orthogonal to the refusal — what this overlay delivers today is the
    NFR-5 configuration, verified by this file and waiting for a profile that
    can be started (Story 8.4).
    """
    # `directives`, not `read`: the paragraph in that file explaining why the
    # line is absent has to say `ENV: prod` in order to explain it, and a
    # whole-file `not in` would make writing the explanation the thing that
    # fails the test.
    prod = directives("compose.prod.yaml")
    assert "ENV: prod" not in prod, (
        "deploy/compose.prod.yaml sets ENV: prod, which api/app.py::create_app "
        "refuses to boot under while persona login is the auth mechanism (Story "
        "1.3's Deferred IdP decision). The api would crash-loop and web would "
        "never become healthy. Resolve the IdP decision first."
    )


def test_the_prod_profile_overrides_the_published_ports_rather_than_appending() -> None:
    """The one line whose absence would be invisible until somebody port-scanned.

    Compose *appends* port lists across files, so `compose.prod.yaml` without
    `!override` publishes 8080:80 beside 443 — a prod stack still answering
    plain HTTP on the dev port, with a healthy `docker compose ps` and no error
    anywhere. `443` on its own is not enough to assert; the tag is.
    """
    prod = read("compose.prod.yaml")
    assert "ports: !override" in prod, (
        "deploy/compose.prod.yaml must use `ports: !override` on web — compose "
        "appends port lists, so without it prod keeps publishing 8080:80 beside 443."
    )
    assert '"443:443"' in prod


def test_the_prod_ingress_terminates_tls_and_redirects_plain_http() -> None:
    """AC 3's ingress half, read out of `nginx/tls.conf`.

    The certificate paths are asserted alongside `listen 443 ssl` because an
    nginx that listens on 443 without them does not start, and the 308 is
    asserted because an ingress that serves both schemes has not moved anybody
    to TLS — it has added an option. 308 specifically: the method and body must
    survive, and a `POST /api/auth/login` that silently became a GET would fail
    in a way nobody attributes to a redirect.
    """
    tls = read("nginx/tls.conf")
    assert "listen 443 ssl;" in tls
    assert "ssl_certificate /etc/nginx/tls/server.crt;" in tls
    assert "ssl_certificate_key /etc/nginx/tls/server.key;" in tls
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in tls
    assert "ssl_session_cache" in tls
    assert "absolute_redirect off;" in tls
    assert "return 308 https://$host$request_uri;" in tls, (
        "nginx/tls.conf must send plain HTTP to HTTPS; an ingress that answers both "
        "has added an option rather than moved anybody."
    )

    # …and HSTS, because the redirect alone is not a control. It acts on a
    # request that has already crossed the network in plaintext, and an active
    # attacker in that position strips it rather than following it — so the
    # first request of every session is unprotected and the redirect never runs.
    # The header is what makes the *second* session, and every one after it,
    # impossible to start in plaintext. `always` because nginx otherwise omits
    # it from 4xx and 5xx responses, which is where a session often begins.
    hsts = 'add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;'
    assert hsts in tls, (
        "nginx/tls.conf must send HSTS. The 308 moves a client that already spoke "
        "plaintext to us once; only this stops the next first request doing the same, "
        "and a stripped redirect is exactly what an active MITM does."
    )


def test_the_two_nginx_configs_still_agree_about_the_api_proxy() -> None:
    """The copy stays honest, and this is what keeps it so.

    `tls.conf`'s `/api/` block is `default.conf`'s, copied verbatim rather than
    included — an `include` would put a fragment in the image that only one
    profile ever uses and split one server block across two files. The cost of a
    copy is drift, and the drift that would hurt is the SSE settings: without
    `proxy_buffering off` the copilot's stream arrives all at once at the end of
    a run, which looks like a slow model rather than like an ingress change.

    So both files are held to the same list, and the SPA fallback and the `/api`
    rewrite with it. What is deliberately *not* compared is the whole block:
    `listen`, the TLS directives and `X-Forwarded-Proto`'s value legitimately
    differ, and a whole-block equality would fail on exactly the lines that are
    supposed to.
    """
    for name in ("nginx/default.conf", "nginx/tls.conf"):
        text = read(name)
        missing = [setting for setting in SSE_PROXY_SETTINGS if setting not in text]
        assert missing == [], f"deploy/{name} is missing {missing}"
        assert "rewrite ^/api/?(.*)$ /$1 break;" in text, name
        assert "proxy_pass http://$api_upstream:8000;" in text, name
        assert "resolver 127.0.0.11 valid=10s ipv6=off;" in text, name
        assert "return 308 /api/;" in text, name
        assert "try_files $uri /index.html;" in text, name


def test_the_dev_ingress_is_still_plain_http() -> None:
    """Story 1.1's decision, asserted rather than assumed.

    `default.conf` is baked into the web image by `web/Dockerfile`, so it is
    what dev and e2e serve. A TLS directive finding its way in there would
    break `docker compose up` on every laptop in the project — there is no
    certificate to serve — and would break the e2e gate, which drives
    `http://localhost:8081`.
    """
    default = read("nginx/default.conf")
    assert "listen 80;" in default

    # Anchored on the directives, not on the substring `ssl`. `tls.conf`'s
    # header tells whoever edits either file to keep the two in step, so the
    # first comment in this one that mentions `ssl_certificate` while
    # explaining that decision would fail the suite — and a guard that fires on
    # the prose explaining the guard is a guard somebody deletes
    # (`tests/test_layering.py` learned this the expensive way). Every other
    # assertion in this file already matches a whole directive string; this one
    # was the exception.
    forbidden = ("ssl_certificate", "ssl_protocols", "listen 443", "ssl on")
    present = [directive for directive in forbidden if directive in default]
    assert present == [], (
        f"deploy/nginx/default.conf carries {present}. It is baked into the web image "
        "and serves dev and e2e, where there is no certificate — the prod ingress is "
        "deploy/nginx/tls.conf, mounted over it by compose.prod.yaml."
    )


def test_the_env_template_documents_both_certificate_directories() -> None:
    """The deployment's side of the bargain, in the file an operator copies.

    Both are `${…:?}`-guarded in the overlay, so a deployment that misses them
    gets a refusal at `config` time — but a refusal naming a variable nobody
    documented is a puzzle. The manual verification commands are asserted for
    the same reason they exist: CI cannot boot this profile, so the only proof
    a real deployment terminates TLS is somebody running them.
    """
    example = read(".env.example")
    assert "# --- TLS at ingress and to Postgres (Story 8.2) ---" in example
    assert "TLS_CERT_DIR=" in example
    assert "PG_TLS_DIR=" in example

    # …and they are *live* lines rather than commented examples. Every other
    # knob in this file is a default the build already has, commented out so
    # that copying the template changes nothing. These two have no default: a
    # copied file whose only two mandatory settings are inert fails at
    # `docker compose config` after the deployment is otherwise assembled,
    # which is the furthest possible point from where the fix is.
    live = [
        line.split("=", 1)[0]
        for line in example.splitlines()
        if not line.lstrip().startswith("#") and "=" in line
    ]
    for required in ("TLS_CERT_DIR", "PG_TLS_DIR"):
        assert required in live, (
            f"deploy/.env.example writes {required} as a commented-out example. It has "
            "no default and the prod overlay's `${…:?}` guard refuses to render without "
            "it, so a copied template must carry it as a real line."
        )
    assert "openssl s_client" in example, (
        ".env.example must document the ingress verification command — CI cannot boot "
        "the prod profile, so this is the only proof there is."
    )
    assert "pg_stat_ssl" in example, (
        ".env.example must document the API-to-Postgres verification query for the same reason."
    )
