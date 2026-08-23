"""The deploy files say what Story 8.3 requires them to say (AC 1, AC 3, NFR-8).

The backup pipeline's correctness is proved twice, from two directions, because
neither direction can carry the whole load. `tests/test_backup_restore.py` runs
the shipped container against a real database and restores what it produced,
which proves the *pipeline* — but it proves it for whatever configuration that
test supplies, not for the three profiles this repository actually ships. This
file is the other half: the same text-level posture check Story 8.2 established,
asserting that dev, e2e and prod configure the same service by the same
mechanism and differ only where they must.

That matters most for the one profile nothing can boot. `compose.prod.yaml` has
no certificates in version control, cannot be started by CI or by any test, and
is the profile with the real claim book behind it — so for prod, *this file is
the only check there is* on whether the backup service is enabled, whether it
publishes a port, and whether its two mandatory variables are guarded.

## Matched as text, not parsed

`tests/test_deploy_tls_posture.py`'s module docstring states the rule and this
file inherits its helpers wholesale: a YAML parser is a dependency this suite
does not declare, and `docker compose config` is a docker daemon it does not
require. The consequence is the convention those helpers encode —
**presence checks may use `read`, absence and count checks must use
`directives`** — because the deploy files in this repository argue for every
setting they carry and the prose therefore quotes the settings.

## What is deliberately NOT asserted here

Whether `profiles: !override []` actually clears the inherited profile list.
That is a fact about compose's merge semantics, not about the file's words, and
a misspelled tag (`!overide`) is silently ignored by compose while passing every
string assertion below. `.github/workflows/ci.yaml` renders the prod profile and
asserts the *rendered document* contains the service — the text check catches
deletion, the render catches the misspelling. Story 8.2 learned that distinction
about `ports: !override` and it applies unchanged.
"""

import re
from pathlib import Path
from typing import Final

import pytest

from tests.test_deploy_tls_posture import DEPLOY, HARDENED_PROFILES, directives, read

BACKUP_DIR: Final[Path] = DEPLOY / "backup"

#: The two flags Story 8.3 adds to the postgres `command:` in every profile.
#:
#: Driven off one tuple across three files for `POSTGRES_HARDENING_FLAGS`'s
#: reason: the list is restated because compose *replaces* a `command:` rather
#: than merging it, and a flag added to `compose.yaml` and forgotten in
#: `compose.prod.yaml` is exactly the drift the restatement invites.
BACKUP_POSTGRES_FLAGS: Final[tuple[str, ...]] = (
    "max_slot_wal_keep_size=2GB",
    "hba_file=/etc/postgresql/pg_hba.conf",
)

#: Every `BACKUP_*` name a compose file interpolates, i.e. every knob a
#: deployment can actually set. Derived from the files rather than listed, so
#: this file cannot go stale against them.
KNOB_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$\{(BACKUP_[A-Z0-9_]+)[:?}-]")


#: `${BACKUP_X:-default}` / `${BACKUP_X}` as the shell scripts read it, which is
#: how the set of knobs the *container* honours is derived rather than listed.
SCRIPT_KNOB_PATTERN: Final[re.Pattern[str]] = re.compile(r"\$\{(BACKUP_[A-Z0-9_]+)[:}-]")
#: How `.env.example` writes a knob, commented out or live.
ENV_ENTRY_PATTERN: Final[re.Pattern[str]] = re.compile(r"^#?\s*(BACKUP_[A-Z0-9_]+)=", re.MULTILINE)

BACKUP_SECTION_HEADER: Final[str] = "# --- Nightly encrypted backups (Story 8.3) ---"

#: Strings that would mean an identity, a private key or a credential had been
#: committed under `deploy/`. The recipient (`age1…`) is public and is expected;
#: its private half is not.
SECRET_MARKERS: Final[tuple[str, ...]] = (
    "AGE-SECRET-KEY-",
    "-----BEGIN OPENSSH PRIVATE KEY-----",
    "-----BEGIN RSA PRIVATE KEY-----",
    "-----BEGIN EC PRIVATE KEY-----",
    "-----BEGIN PRIVATE KEY-----",
)


def script(name: str) -> str:
    """One file under `deploy/backup/`, with the same vacuity guard `read` has."""
    path = BACKUP_DIR / name
    assert path.is_file(), f"{path} does not exist — did deploy/backup/ move?"
    text = path.read_text(encoding="utf-8")
    assert text.strip(), f"{path} is empty"
    return text


def script_directives(name: str) -> str:
    """`script`, with the comment lines removed — `directives`' argument, for shell.

    The ordering assertions below measure *where a command runs*, and
    `backup.sh` opens with a forty-line header that draws the very pipeline it
    is being asserted about:

        #     -> pg_basebackup   (physical, the thing WAL replays onto)
        #     -> age -r          <- EVERYTHING becomes ciphertext HERE
        #     -> rsync           <- and only now does anything leave the host

    A raw `text.find("pg_basebackup")` therefore returns a byte offset in the
    documentation, and the test measures the comment instead of the code — which
    is how `test_the_recipient_is_validated_before_any_plaintext_exists` failed
    on its first run against a script that was perfectly correct. Same lesson,
    same fix, as `test_deploy_tls_posture.directives`.
    """
    return "\n".join(
        line for line in script(name).splitlines() if not line.lstrip().startswith("#")
    )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_every_profile_declares_the_same_backup_service(profile: str) -> None:
    """One service, one image, in all three profiles (AC 1's anti-drift rule).

    Story 8.2's rule — the same posture, by the same mechanism, in dev, e2e and
    prod, so drift cannot hide — applies to the backup pipeline for a sharper
    reason than it applied to pgaudit: a backup that differs between the profile
    that is tested and the profile that holds real claims is a backup nobody has
    tested. `compose.prod.yaml` is an overlay and inherits `build:`/`image:`
    from the base file, which is what an overlay is for, so only the two
    declaring profiles are checked for those.
    """
    text = read(profile)
    assert "  backup:" in text, (
        f"deploy/{profile} declares no backup service. The `bak` container has been in "
        "the deployment view since Story 1.1 and Story 8.3 is what builds it."
    )
    if profile != "compose.prod.yaml":
        assert "lineworker/backup:pg18" in text, f"deploy/{profile} does not name the backup image."
        assert "context: ./backup" in text, (
            f"deploy/{profile} names the backup image but does not build it, so a clean "
            "checkout has nothing to run."
        )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_the_backup_service_publishes_no_port_in_any_profile(profile: str) -> None:
    """The rule `postgres` and `ollama` already keep, for a service that holds more.

    This container holds a connection that can read the entire claim book and a
    directory of encrypted copies of it. There is nothing it could usefully
    publish. Asserted per profile because the failure it prevents — a `ports:`
    line added "just to look at the artifacts" — reviews as a one-line diff.

    `directives`, not `read`: `compose.yaml`'s comment block above the service
    explains the rule by writing `ports:` while doing it, and a whole-file
    search would make explaining the rule the thing that breaks the test.
    """
    text = directives(profile)
    start = text.find("  backup:")
    assert start >= 0, f"deploy/{profile} has no backup service to check"
    # Bounded at the next top-level-ish key so this reads one service, not the
    # rest of the file: `web` legitimately publishes ports two services up.
    after = text[start + len("  backup:") :]
    # A *sibling service key*, not any two-space indent. `"\n  "` matched at
    # index 0 — the service's own first nested line is a newline followed by
    # two spaces — so this block was the empty string in all three profiles and
    # the assertion below was vacuous: it passed with `ports: ["5432:5432"]`
    # under `backup:`. Found in review; the only thing actually enforcing the
    # rule was CI's rendered-document check, which a developer running pytest
    # locally never sees.
    sibling = re.compile(r"\n {2}[a-z][a-z0-9_-]*:|\nvolumes:|\nnetworks:")
    match = sibling.search(after)
    block = after[: match.start()] if match else after
    assert block.strip(), f"deploy/{profile}'s backup service block came out empty"
    assert "ports:" not in block, (
        f"deploy/{profile} publishes a port for the backup service. It reaches postgres "
        "on the internal network and has nothing to serve; a published port on the "
        "container holding the encrypted claim book is the one mapping this stack "
        "must never grow."
    )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_every_profile_starts_postgres_with_the_backup_flags(profile: str) -> None:
    """`max_slot_wal_keep_size` and `hba_file`, in all three, or not at all.

    Both are load-bearing and both are invisible when missing until something
    else breaks. Without `max_slot_wal_keep_size` an abandoned replication slot
    pins WAL until the data volume fills and the database stops accepting
    writes — the WAL receiver's failure mode becoming a database outage, which
    is strictly worse than the missed backup it was protecting. Without
    `hba_file` the mounted `pg_hba.conf` is not read and `pg_basebackup` fails
    with "no pg_hba.conf entry for replication connection", because the official
    image's `host all all all` record does not match a replication connection.
    """
    text = read(profile)
    missing = [flag for flag in BACKUP_POSTGRES_FLAGS if flag not in text]
    assert missing == [], (
        f"deploy/{profile} does not start postgres with {missing}. Both are restated in "
        "all three profiles because compose replaces a `command:` rather than merging "
        "it, and this test is what pays for the restatement."
    )


@pytest.mark.parametrize("profile", ("compose.yaml", "compose.e2e.yaml"))
def test_the_declaring_profiles_mount_the_committed_hba_file(profile: str) -> None:
    """`-c hba_file=` without the file is a postmaster that will not start.

    The flag and the mount are one decision, exactly as
    `shared_preload_libraries=pgaudit` and the pgaudit image were one decision
    in Story 8.2. Only the two profiles that *declare* the postgres service are
    checked: `compose.prod.yaml` inherits the mount from `compose.yaml` because
    compose merges volume lists, which is the one thing it does merge.
    """
    text = read(profile)
    assert "./postgres/pg_hba.conf:/etc/postgresql/pg_hba.conf:ro" in text, (
        f"deploy/{profile} sets hba_file but does not mount the file at that path; "
        "postgres refuses to start when it cannot load its hba file."
    )


def test_the_committed_hba_file_permits_replication_and_nothing_looser() -> None:
    """The one record Story 8.3 added, and the two it must not have added.

    The record exists because the official image appends `host all all all
    <method>` and PostgreSQL does not match a physical replication connection
    against `all` — verified, not reasoned about: without it `pg_receivewal`
    from another container fails outright.

    Asserted in both directions. Missing, and the backup job has no base backup
    and no WAL archive. Widened to `trust`, and this file has handed anybody who
    can reach the internal network an unauthenticated copy of the claim book,
    which is a far larger hole than the one it was opened to close.
    """
    text = (DEPLOY / "postgres" / "pg_hba.conf").read_text(encoding="utf-8")
    records = [
        line.split()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert any(
        record[:3] == ["host", "replication", "all"] and record[-1] == "scram-sha-256"
        for record in records
    ), (
        "deploy/postgres/pg_hba.conf must permit a network replication connection with "
        "scram-sha-256; pg_basebackup and pg_receivewal are the whole of the backup "
        "container's physical half."
    )

    # An allowlist, not a `trust` denylist. `host all all all password` — a
    # cleartext password over a link these records do not require TLS on — and
    # `md5` both passed the old "nothing looser than trust" check, and so did
    # anything carrying an options suffix, because `record[-1]` is then the
    # option rather than the method. This file is now the only authentication
    # policy this deployment has; what it may say is a short list.
    network = [
        record
        for record in records
        if record[0].startswith("host") and not record[3].startswith(("127.0.0.1", "::1"))
    ]
    assert network, "deploy/postgres/pg_hba.conf has no non-loopback network records at all"
    for record in network:
        method = record[4] if len(record) > 4 else ""
        assert method == "scram-sha-256", (
            f"deploy/postgres/pg_hba.conf authenticates a network connection with "
            f"{method!r}: {record}. Every non-loopback record must be scram-sha-256 — "
            "anything else is either unauthenticated access to every PHI store or a "
            "password on the wire."
        )

    # The loopback records are the image's own and the entrypoint needs them —
    # `pg_isready` in every compose healthcheck and `/docker-entrypoint-initdb.d`
    # both arrive that way. They may be `trust`; nothing else may.
    loopback = [
        record
        for record in records
        if record[0].startswith("host") and record[3].startswith(("127.0.0.1", "::1"))
    ]
    for record in loopback:
        method = record[4] if len(record) > 4 else ""
        assert method in {"trust", "scram-sha-256"}, (
            f"deploy/postgres/pg_hba.conf authenticates loopback with {method!r}: {record}."
        )


def test_the_encryption_happens_before_anything_leaves_the_host() -> None:
    """AD-11 as an ordering property of the script, not as an intention.

    "Encrypted before it leaves the host" is only true if the copy step has
    nothing but ciphertext available to copy. `backup.sh` produces its three
    plaintext artifacts inside a staging directory, encrypts each with `age -r`,
    removes the plaintext, removes the staging directory, and only then runs
    `rsync` — so by the time the transfer starts the run directory physically
    contains no plaintext byte.

    Checked as source order because that is what the property *is*. A test that
    ran the script and inspected the output would prove it for one run; the line
    numbers prove it for every run, including the ones that fail half way.
    """
    text = script_directives("backup.sh")
    encrypt_at = text.find("age -r")
    copy_at = text.find("rsync -a")
    assert encrypt_at > 0, "deploy/backup/backup.sh does not encrypt with `age -r` at all"
    assert copy_at > 0, "deploy/backup/backup.sh does not copy with rsync at all"
    assert encrypt_at < copy_at, (
        "deploy/backup/backup.sh runs rsync before `age -r`. AD-11 requires that a "
        "plaintext artifact never reaches the off-host destination, and the only way "
        "that is a property rather than a hope is for encryption to come first."
    )

    # …and the staging directory is gone before the copy, which is what makes
    # the ordering above sufficient rather than merely suggestive.
    purge_at = text.find('rm -rf "$plain_dir"')
    assert 0 < purge_at < copy_at, (
        "deploy/backup/backup.sh must remove the plaintext staging directory before "
        "rsync runs, so the directory being copied cannot contain plaintext even if "
        "an encrypt stage were skipped."
    )


def test_the_recipient_is_validated_before_any_plaintext_exists() -> None:
    """The refusal that makes "no recipient" safe rather than merely loud.

    An empty or malformed `BACKUP_AGE_RECIPIENT` discovered at the encrypt stage
    means a full `pg_dump` of the claim book is already sitting on disk in the
    clear. So the check is at the config stage, before `pg_basebackup`, and it
    is not a mere emptiness test: an empty message is encrypted to the recipient
    as a probe, because that is the only way to learn that a *typo'd* recipient
    will be rejected.
    """
    text = script_directives("backup.sh")
    refusal_at = text.find("BACKUP_AGE_RECIPIENT is empty")
    probe_at = text.find("printf '' | age -r \"$RECIPIENT\"")
    base_at = text.find("pg_basebackup")
    dump_at = text.find("pg_dump ")
    assert refusal_at > 0, "backup.sh does not refuse an empty BACKUP_AGE_RECIPIENT"
    assert probe_at > 0, "backup.sh does not probe that the recipient is usable"
    assert refusal_at < base_at, "the recipient refusal must precede pg_basebackup"
    assert probe_at < base_at, "the recipient probe must precede pg_basebackup"
    assert probe_at < dump_at, "the recipient probe must precede pg_dump"


def test_the_wal_setting_differs_only_where_it_must() -> None:
    """`off` in e2e, `on` in prod, and the difference is the whole difference.

    `pg_receivewal` holds a physical replication slot. The e2e profile's
    database has its schema dropped and re-migrated before every spec file and
    its backup container is a one-shot `run --rm`, so a slot there would be
    abandoned by construction — which is the exact condition
    `max_slot_wal_keep_size` exists to bound and not a state to create
    deliberately. In prod the stream is what turns a 24-hour recovery point into
    a seconds-to-minutes one, and Procedure B of the runbook depends on it.
    """
    e2e = directives("compose.e2e.yaml")
    assert 'BACKUP_WAL: "off"' in e2e, (
        "deploy/compose.e2e.yaml must set BACKUP_WAL off: a replication slot against a "
        "database whose schema is dropped before every spec file is an abandoned slot."
    )
    prod = directives("compose.prod.yaml")
    assert 'BACKUP_WAL: "on"' in prod, (
        "deploy/compose.prod.yaml must set BACKUP_WAL on — the WAL stream is the whole "
        "difference between a 24-hour recovery point and a minutes-long one."
    )


def test_the_prod_profile_guards_the_two_variables_with_no_safe_default() -> None:
    """`${…:?}` on both, matching Story 8.2's TLS_CERT_DIR / PG_TLS_DIR.

    A recipient cannot have a default: any value shipped in a compose file would
    be a public key whose identity somebody else holds, so a deployment that
    forgot to set it would write backups it cannot read and a stranger can. A
    destination cannot have one either — the dev default is a directory inside
    the backup volume, which is off-host in shape and not in fact.

    A guard fails the *render*, which is the earliest possible point: the
    alternative is a container that starts, refuses at its config stage and is
    noticed at 02:30 by nobody.
    """
    prod = read("compose.prod.yaml")
    for variable in ("BACKUP_AGE_RECIPIENT", "BACKUP_REMOTE"):
        assert f"${{{variable}:?" in prod, (
            f"deploy/compose.prod.yaml does not guard {variable} with `${{…:?}}`. It has "
            "no safe default and a deployment that misses it must fail at `config` time, "
            "not at 02:30."
        )


def test_the_prod_profile_enables_the_backup_service_without_a_profile_flag() -> None:
    """Backups that run only when somebody remembers a flag are not backups.

    The gate exists in dev and e2e so that Story 1.1's "a clean checkout just
    works" survives a service with no safe default recipient. In prod it is
    exactly wrong, and `profiles: !override []` is what clears it — `!override`
    on a sequence *replaces* rather than appends, the same tag and the same
    reason as `web.ports`.

    This asserts the tag is present, which catches its deletion. It cannot catch
    a misspelling (`!overide` is a tag compose ignores while every string here
    still passes), which is why `.github/workflows/ci.yaml` renders the prod
    profile and asserts the service is in the rendered document.
    """
    prod = read("compose.prod.yaml")
    assert "profiles: !override []" in prod, (
        'deploy/compose.prod.yaml must clear the inherited `profiles: ["backup"]` gate '
        "with `profiles: !override []`, or a prod stack takes backups only when an "
        "operator remembers `--profile backup`."
    )


def test_the_prod_ssh_key_is_mounted_read_only_as_a_single_file() -> None:
    """Least privilege on the one mount that carries a private key.

    The same argument `test_deploy_tls_posture.py` makes about `ca.crt`, one
    step sharper: this is the private half. Mounting the directory that holds it
    would hand this container every other key in the operator's SSH store, and
    this is the container whose whole job is to reach another host.
    """
    prod = directives("compose.prod.yaml")
    assert "/etc/lineworker/backup-ssh/id_backup:ro" in prod, (
        "deploy/compose.prod.yaml must mount the backup SSH key read-only as a single "
        "file, not as the directory it lives in."
    )
    assert "/etc/lineworker/backup-ssh:ro" not in prod, (
        "the whole SSH key directory is mounted into the backup container; one file is "
        "all it needs."
    )


@pytest.mark.parametrize("profile", HARDENED_PROFILES)
def test_the_healthcheck_is_wired_in_every_profile(profile: str) -> None:
    """AC 3's surfacing, present wherever the service is.

    The status file is only half of "a failed nightly run surfaces
    non-silently": something has to read it. There is no monitoring integration
    and no alerting — both deferred by decision — so the healthcheck *is* the
    binding surface, and a profile that declared the service without it would
    have a backup job whose failures are a file nobody opens.

    `compose.prod.yaml` inherits the healthcheck from the base file, which is
    correct and is why prod is checked for the service rather than for the
    directive.
    """
    text = read(profile)
    if profile == "compose.prod.yaml":
        assert "  backup:" in text
        return
    assert "/usr/local/bin/healthcheck.sh" in text, (
        f"deploy/{profile} declares the backup service without a healthcheck. The "
        "status file plus this check are the whole of AC 3's binding surface."
    )


def test_no_identity_or_private_key_is_committed_anywhere_under_deploy() -> None:
    """The property the whole design rests on, asserted over the tree.

    `age` public-key encryption is load-bearing precisely because the backup
    host holds only the *recipient*. An identity committed here — even a
    "throwaway" one for a test — would make every backup encrypted to it
    readable by anybody with a checkout, and would teach the next story that
    committing one is a thing this repository does.

    `deploy/compose.e2e.yaml` commits a real `age1…` recipient in the clear and
    that is deliberate and safe: it is the public half, no identity for it
    exists anywhere, and the e2e spec asserts non-plaintext without ever
    decrypting.
    """
    offenders: list[str] = []
    for path in sorted(DEPLOY.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in SECRET_MARKERS:
            if marker in text:
                offenders.append(f"{path.relative_to(DEPLOY)}: {marker}")
    assert offenders == [], (
        f"key material is committed under deploy/: {offenders}. The identity lives in "
        "the operator's secret store and nowhere else — that is the property that makes "
        "compromising the backup host survivable."
    )


def test_the_env_template_documents_exactly_the_knobs_the_compose_files_use() -> None:
    """Set equality, both ways, because each direction fails differently.

    A knob a compose file interpolates and `.env.example` does not document is a
    setting an operator cannot discover — they meet it as an unexplained
    `${…:?}` refusal or, worse, as a default they never knew was there. A knob
    `.env.example` documents and no compose file reads is worse still: it is
    advice that does nothing, and somebody will set it, restart the stack and
    believe the behaviour changed.

    Derived from the files on both sides rather than listed here, so this test
    cannot go stale against either.
    """
    compose_knobs = {
        match
        for profile in HARDENED_PROFILES
        for match in KNOB_PATTERN.findall(directives(profile))
    }
    assert compose_knobs, "no BACKUP_* interpolation found in any compose file"

    # …and every knob the **scripts** read, which is the larger set and the one
    # the docstring above actually claims. Review found this test covering only
    # the compose-interpolated subset, so `BACKUP_ROOT`, `BACKUP_BLOB_ROOT` and
    # `BACKUP_SLOT` were read by the shipped container and documented nowhere.
    script_knobs: set[str] = set()
    for script in ("backup.sh", "entrypoint.sh", "healthcheck.sh"):
        text = (DEPLOY / "backup" / script).read_text(encoding="utf-8")
        script_knobs |= set(SCRIPT_KNOB_PATTERN.findall(text))
    assert script_knobs, "no BACKUP_* environment read found in any backup script"
    expected = compose_knobs | script_knobs

    example = read(".env.example")
    assert BACKUP_SECTION_HEADER in example, (
        "deploy/.env.example has no Story 8.3 section; the house style is one banner "
        "per story and the tests read it."
    )
    section = example[example.index(BACKUP_SECTION_HEADER) :]
    documented = set(ENV_ENTRY_PATTERN.findall(section))

    undocumented = sorted(expected - documented)
    assert undocumented == [], (
        f"deploy/.env.example does not document {undocumented}, which the compose files "
        "interpolate or the shipped scripts read. An operator meets an undocumented knob "
        "as an unexplained refusal, or never meets it at all."
    )
    unused = sorted(documented - expected)
    assert unused == [], (
        f"deploy/.env.example documents {unused}, which nothing reads. Advice that does "
        "nothing is worse than no advice: somebody will set it and believe it."
    )


def test_the_two_required_variables_are_written_live_in_the_template() -> None:
    """The lesson Story 8.2's review recorded, applied to this story's two.

    Every other knob in `.env.example` is a default the build already has,
    commented out so that copying the template changes nothing. These two have
    no default and the prod overlay refuses to render without them, so a copied
    file whose only mandatory settings are inert fails at `docker compose config`
    after the deployment is otherwise assembled — the furthest possible point
    from where the fix is.
    """
    example = read(".env.example")
    live = [
        line.split("=", 1)[0]
        for line in example.splitlines()
        if not line.lstrip().startswith("#") and "=" in line
    ]
    for required in ("BACKUP_AGE_RECIPIENT", "BACKUP_REMOTE"):
        assert required in live, (
            f"deploy/.env.example writes {required} as a commented-out example. It has "
            "no default and the prod overlay's `${…:?}` guard refuses to render without "
            "it, so a copied template must carry it as a real line."
        )


def test_the_template_says_where_the_identity_must_not_be() -> None:
    """The one instruction whose omission is unrecoverable.

    Everything else in this section is a knob with a default. This is a
    procedure, and getting it wrong has no failure mode until the day of a
    restore: an identity left on the backup host makes every off-host copy
    readable by whoever took the host, which is the exact scenario the
    encryption exists for, and a lost identity makes every backup ever taken
    permanently unreadable with no escrow and no support path. Both belong in
    the file an operator copies, in words, not in a comment in a shell script
    they will never open.
    """
    example = read(".env.example")
    assert "age-keygen" in example, "deploy/.env.example must say how the keypair is generated."
    assert "identity" in example, (
        "deploy/.env.example must name the identity (the private half) as the thing "
        "that must leave the backup host."
    )
    assert "public" in example, (
        "deploy/.env.example must say the recipient is public and safe to commit — "
        "otherwise an operator treats it as a secret and stores it with the identity."
    )
    for caveat in ("purge", "RESTORE-DRILL.md"):
        assert caveat in example, (
            f"deploy/.env.example does not mention {caveat!r}. A restored backup "
            "resurrects PHI the Story 8.1 cascade removed; the mitigation is the "
            "retention bound plus the runbook's caveat, and both have to be findable."
        )


def test_the_runbook_exists_and_has_the_sections_the_story_requires() -> None:
    """An unexecuted runbook does not satisfy AC 2, and neither does a missing one.

    The `§ Executed drill` heading is asserted here rather than its contents:
    this test runs in CI on every commit and the drill is executed once, by a
    person, against a booted stack. What it can hold is that the document has
    somewhere for that record to go and that both procedures are written — a
    runbook with only the logical restore would leave the WAL stream shipping an
    archive with no documented way to replay it.
    """
    runbook = (DEPLOY / "RESTORE-DRILL.md").read_text(encoding="utf-8")
    for heading in (
        "Procedure A",
        "Procedure B",
        "Executed drill",
        "Verification checklist",
    ):
        assert heading in runbook, f"deploy/RESTORE-DRILL.md has no {heading!r} section"
    assert "purge" in runbook.lower(), (
        "deploy/RESTORE-DRILL.md must carry the purge caveat: a restored backup "
        "resurrects PHI that Story 8.1's cascade removed, and the mitigation is bounded "
        "retention rather than purge-aware filtering."
    )
