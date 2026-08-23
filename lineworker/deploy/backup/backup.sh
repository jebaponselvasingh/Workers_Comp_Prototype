#!/bin/sh
# One backup run (Story 8.3, AD-3, AD-11). Called by `entrypoint.sh` — once per
# night from the scheduler loop, or exactly once from `docker compose run --rm
# backup once`.
#
# ## The order below is the story, and it is load-bearing
#
#   validate config
#     -> pg_basebackup   (physical, the thing WAL replays onto)
#     -> pg_dump         (logical, the thing the drill restores)
#     -> pg_dumpall      (the roles, without which that dump is unrestorable)
#     -> tar the blobs   (the binaries a restored claim needs)
#     -> age -r          <- EVERYTHING becomes ciphertext HERE
#     -> manifest.json   (names, byte counts, sha256 — never content)
#     -> rsync           <- and only now does anything leave the host
#     -> retire shipped WAL
#     -> prune to BACKUP_KEEP_RUNS, locally and remotely
#     -> status.json
#
# **`age` before `rsync`, with nothing between them that could reorder.** AD-11
# says backups are encrypted *before* they leave the host, and the only way to
# make that a property rather than an intention is for the copy step to have
# nothing but ciphertext available to copy. The plaintext staging directory is
# removed before the manifest is written, so by the time `rsync` runs the run
# directory physically contains no plaintext — a bug in the destination path, a
# `--include` somebody adds later, an operator running rsync by hand against the
# same directory, none of them can leak a claim.
#
# **Config is validated before `pg_dump`, not after.** An empty
# `BACKUP_AGE_RECIPIENT` discovered after the dump has already been written
# means a plaintext copy of the entire claim book exists on disk while the job
# reports failure. So the recipient is not merely checked for emptiness: an
# empty message is encrypted to it as a probe, which is the only way to find out
# that a *malformed* recipient will be rejected — and it costs one process and
# no bytes.
#
# ## One stream, not one per store (AD-3)
#
# `pg_dump -Fc` of database `lineworker` carries every PHI-class store this
# system has: the relational entities, `claim_embedding` and the `knowledge_*`
# vectors, the vendored LangGraph checkpoint tables, `ai_insight` and
# `audit_event`. That is the load-bearing rationale of the single-database
# decision and the reason this file has three artifact-producing stages rather
# than eleven. The blob volume is the one thing not inside Postgres, so it is
# the one thing tarred separately — see Design Note 5 of the spec for why the
# volume is declared here before a single byte has ever been written to it.
#
# ## What this script is allowed to say (AD-11)
#
# Names, byte counts, row-free counts, timestamps, durations and exit codes. Not
# a claim id, not a table's contents, not a line of `pg_dump`'s output. A failed
# command's stderr is truncated to its **first line** and scrubbed of anything
# unprintable before it reaches `status.json`, because the second line of a
# libpq error is where the connection string lives and the second line of a
# `tar` error is where a filename lives.
set -eu

# ---------------------------------------------------------------------------
# Configuration. Every knob is env, resolved once, with the deployment defaults
# that `deploy/.env.example` documents. Paths are container paths and are
# deliberately not knobs: the compose files decide what is mounted where, and a
# second place to configure that is a second place for it to be wrong.
# ---------------------------------------------------------------------------
BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/lineworker/backup}"
BLOB_ROOT="${BACKUP_BLOB_ROOT:-/var/lib/lineworker/blobs}"
KEEP_RUNS="${BACKUP_KEEP_RUNS:-7}"
RECIPIENT="${BACKUP_AGE_RECIPIENT:-}"
REMOTE="${BACKUP_REMOTE:-}"
DB_URL="${BACKUP_DATABASE_URL:-}"
SSH_KEY="${BACKUP_SSH_KEY:-}"
WAL_MODE="${BACKUP_WAL:-off}"
# I/O inactivity bound for the off-host transfer, in seconds. Not a knob in
# `.env.example` on purpose — it is not a tuning parameter, it is the difference
# between "tonight's copy failed" and "the scheduler is blocked for ever". A
# half-open TCP connection to a destination that went away gives `rsync` no
# error and no data, and the run holds the lock and the loop behind it: every
# subsequent night is missed too, and the healthcheck reports the *stale*
# window rather than the stuck run. Ten minutes of complete silence on a
# transfer that normally takes seconds is a dead connection.
IO_TIMEOUT=600
# Ceiling on unshipped WAL segments in the spool, in 16 MB units (512 = 8 GB).
# Not a tuning knob either: it is the bound that keeps a broken off-host copy
# from filling the volume the database is on. See the ship_wal stage.
MAX_SPOOL_SEGMENTS="${BACKUP_MAX_SPOOL_SEGMENTS:-512}"
case "$MAX_SPOOL_SEGMENTS" in
    '' | *[!0-9]*) MAX_SPOOL_SEGMENTS=512 ;;
esac

ARTIFACTS="$BACKUP_ROOT/artifacts"
WAL_SPOOL="$BACKUP_ROOT/wal"
STATUS="$BACKUP_ROOT/status.json"
STATUS_TMP="$BACKUP_ROOT/status.json.tmp"
ERR_FILE="$BACKUP_ROOT/.last-stderr"
WAL_STATE="$BACKUP_ROOT/wal_state"
WAL_RESTARTS="$BACKUP_ROOT/wal_restarts"

# `ssh` options for every remote invocation, in one place so the copy and the
# remote prune cannot disagree. BatchMode: a nightly job must never sit at a
# passphrase prompt — it fails instead, loudly, which is the whole point of this
# story. accept-new rather than `no`: an unknown host key is accepted the first
# time and *pinned*, so a substituted host on night two is refused; `no` would
# accept a different key every night, which is not host verification at all.
#
# **`UserKnownHostsFile` in the backup volume is what makes that pin real.**
# ssh's default is `/root/.ssh/known_hosts`, which in a container lives in the
# writable layer and dies with it -- so every `up -d`, rebuild or restart
# re-accepted whatever key the destination presented, and `accept-new` was `no`
# with extra steps while an encrypted copy of the whole claim book went over it.
# The volume outlives the container, so the pin does too. A review finding, and
# one no test here could have caught: nothing in this repository has an SSH
# destination to point at.
SSH_OPTS="-o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15 -o UserKnownHostsFile=$BACKUP_ROOT/known_hosts"

# ---------------------------------------------------------------------------
# Run state. Every one of these is read by `write_status`, which runs from the
# EXIT trap and therefore has to be safe under `set -u` no matter how early the
# script died.
# ---------------------------------------------------------------------------
stage="config"
run_id=""
run_dir=""
plain_dir=""
err_line=""
wal_streaming="false"
wal_restarts="0"
wal_segments="0"
wal_dropped="0"
runs_local="0"
artifacts_json=""
shipped_wal=""
started_epoch="$(date -u +%s)"
started_at="$(date -u -d "@$started_epoch" +%Y-%m-%dT%H:%M:%SZ)"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Make a string safe to embed in the one-line JSON strings this script writes,
# and cap it. Three transformations, each for a different failure:
#   * `"` and `\` would terminate or escape their way out of the string and
#     produce a status file nothing can parse — including `healthcheck.sh`,
#     which would then report the container healthy while the run had failed.
#   * anything non-printable (a NUL from a binary payload, a raw newline from a
#     multi-line driver error) does the same to the *document*.
#   * the cut is the AD-11 bound: a first line of stderr is a diagnosis, a
#     paragraph of it is an unbounded quotation of whatever the failing command
#     was holding.
json_scrub() {
    printf '%s' "$1" | tr -d '"\\' | tr -cd '[:print:]' | cut -c1-300
}

# Emit one structured-ish log line. Names, counts, durations, exit codes.
log() {
    printf '%s backup.%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# Refuse before anything exists. Exit 78 (EX_CONFIG) rather than 1, so the
# "somebody has not finished configuring this deployment" case is
# distinguishable from "the database was down" in a process table or a CI log.
die_config() {
    err_line="$(json_scrub "$1")"
    printf 'backup: refusing to run: %s\n' "$1" >&2
    exit 78
}

# Run one command as a named stage, capturing its stderr.
#
# The stderr capture is what makes a failure diagnosable without making it a
# leak: the file holds everything the command said, `err_line` takes the first
# line only, and the file is overwritten by the next stage rather than kept.
# Note that `attempt` deliberately does NOT swallow the failure — it returns the
# command's exit code and `set -e` ends the script, which is what fires the EXIT
# trap that writes `status.json` and removes the partial run directory.
#
# **`|| _code=$?` and not `if "$@"; then return 0; fi; _code=$?`.** The obvious
# spelling is the broken one and it was written here first: POSIX says an `if`
# whose condition is false and which has no `else` branch exits **zero**, so
# `$?` after the `fi` is 0, the function returns 0, `set -e` never fires and the
# run reports success with a failed stage's stderr sitting in its own status
# file. Reproduced before it was fixed: a `BACKUP_REMOTE` that rsync could not
# write to produced `"result": "success"` beside `"error": "rsync: … failed"`,
# which is the precise failure AC 3 exists to make impossible. Assigning inside
# the `||` captures the *command's* status, and a command on the left of `||` is
# exempt from `set -e` by construction.
attempt() {
    stage="$1"
    shift
    _code=0
    "$@" 2>"$ERR_FILE" || _code=$?
    if [ "$_code" -ne 0 ]; then
        err_line="$(json_scrub "$(head -n 1 "$ERR_FILE" 2>/dev/null || true)")"
    fi
    return "$_code"
}

# The machine-readable contract Story 8.3 AC 3 rests on, and the only thing
# `healthcheck.sh` reads. Written to a temporary file and `mv`d into place
# because the healthcheck can run at any instant: a half-written status file is
# a container that reports healthy (or crashes) for reasons that have nothing to
# do with the backup, and `mv` within one filesystem is atomic.
write_status() {
    _result="$1"
    _exit_code="$2"
    _finished_epoch="$(date -u +%s)"
    _finished_at="$(date -u -d "@$_finished_epoch" +%Y-%m-%dT%H:%M:%SZ)"
    if [ -n "$err_line" ]; then
        _error="\"$err_line\""
    else
        _error="null"
    fi
    {
        printf '{\n'
        printf '  "result": "%s",\n' "$_result"
        printf '  "stage": "%s",\n' "$stage"
        printf '  "run_id": "%s",\n' "$run_id"
        printf '  "started_at": "%s",\n' "$started_at"
        printf '  "finished_at": "%s",\n' "$_finished_at"
        printf '  "finished_at_epoch": %s,\n' "$_finished_epoch"
        printf '  "duration_seconds": %s,\n' "$((_finished_epoch - started_epoch))"
        printf '  "exit_code": %s,\n' "$_exit_code"
        printf '  "error": %s,\n' "$_error"
        printf '  "wal_streaming": %s,\n' "$wal_streaming"
        printf '  "wal_restarts": %s,\n' "$wal_restarts"
        printf '  "wal_segments": %s,\n' "$wal_segments"
        printf '  "wal_dropped": %s,\n' "$wal_dropped"
        printf '  "keep_runs": %s,\n' "$KEEP_RUNS"
        printf '  "runs_local": %s,\n' "$runs_local"
        printf '  "artifacts": [%s]\n' "$artifacts_json"
        printf '}\n'
    } >"$STATUS_TMP"
    mv "$STATUS_TMP" "$STATUS"
}

# **Every** failure path goes through here, including the ones nobody wrote a
# handler for — a `set -e` abort from a command that was never wrapped, a
# SIGTERM during a two-hour base backup, a disk that filled between two stages.
# That is the difference between "the failure surfaces non-silently" as a
# property and as an intention: there is no exit from this script that leaves
# `status.json` describing the previous, successful run.
#
# **The run directory is removed when it is *partial*, and kept when it is
# complete**, and `manifest.json` is what distinguishes the two — it is written
# last of all the artifacts, so its presence means every stage that produces
# bytes finished and every one of those bytes is ciphertext.
#
# Removing a partial run is not tidiness: a half-written `base.tar.gz` is not a
# backup, and leaving it would make the next prune count it towards
# BACKUP_KEEP_RUNS and retire a real one to make room for it. Keeping a complete
# one is the I/O matrix's "off-host destination unreachable -> encrypted
# artifacts stay local": the artifacts are finished and encrypted, the only
# thing that failed is the transfer, and destroying them because the network was
# down would turn a copy failure into a data loss. They are pruned by age like
# any other run.
on_exit() {
    _code=$?
    trap - EXIT
    if [ "$_code" -ne 0 ]; then
        if [ -n "$run_dir" ] && [ -d "$run_dir" ] && [ ! -f "$run_dir/manifest.json" ]; then
            rm -rf "$run_dir"
        fi
        write_status "failure" "$_code"
        log "run_failed stage=$stage exit=$_code"
    fi
    rm -f "$ERR_FILE"
    exit "$_code"
}
trap on_exit EXIT

# **A signal is a failed run, and it has to say so.** Without these three, a
# `docker stop` mid-run leaves `status.json` describing the *previous* run — so
# a container killed every night at 02:31 reports a successful backup for ever
# while never finishing one. `sh` runs a trap only after the foreground command
# returns, so the run is not interrupted instantly; what matters is that when it
# does return, control reaches `on_exit` through a non-zero exit rather than
# through the kernel. 128+signo is the shell's own convention for it.
trap 'exit 143' TERM
trap 'exit 130' INT
trap 'exit 129' HUP

# Keep the newest $KEEP_RUNS entries of a directory of run directories and
# delete the rest. Run ids are UTC timestamps in `%Y%m%dT%H%M%SZ`, so a
# lexicographic sort is a chronological one — which is why the format is what it
# is rather than something friendlier to read.
# **Only directories whose names this pipeline produced are counted or
# deleted.** `rm -rf` driven by an unfiltered `ls` of a directory somebody else
# also writes to is how a retention job deletes a `README`, a `lost+found`, or
# the previous system's archive — and the off-host destination is exactly the
# kind of place that has one of those in it. The filter is the run-id format,
# which is also what makes the lexicographic sort chronological.
RUN_ID_GLOB='[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]T[0-9][0-9][0-9][0-9][0-9][0-9]Z'

prune_dir() {
    _dir="$1"
    [ -d "$_dir" ] || return 0
    _runs="$(cd "$_dir" && ls -1d $RUN_ID_GLOB 2>/dev/null | sort || true)"
    [ -n "$_runs" ] || return 0
    _total="$(printf '%s\n' "$_runs" | wc -l | tr -d ' ')"
    [ "$_total" -gt "$KEEP_RUNS" ] || return 0
    printf '%s\n' "$_runs" | head -n "$((_total - KEEP_RUNS))" | while read -r _old; do
        [ -n "$_old" ] || continue
        rm -rf "$_dir/$_old"
    done
}

# Remove run directories that no run will ever finish: a `plain` staging
# directory (which holds **plaintext** — an unencrypted `pg_dump` of the whole
# claim book) or a run with no `manifest.json`. Both are what a run killed
# between stages leaves behind, and both are actively harmful rather than
# untidy: the first is the one thing AD-11 forbids sitting on disk, and the
# second inflates the count `prune_dir` retires real backups by.
#
# This runs at the start of every run rather than in the trap, because the trap
# is exactly what does not get to run when the container is killed.
sweep_incomplete() {
    [ -d "$ARTIFACTS" ] || return 0
    for _run in "$ARTIFACTS"/$RUN_ID_GLOB; do
        [ -d "$_run" ] || continue
        if [ -d "$_run/plain" ] || [ ! -f "$_run/manifest.json" ]; then
            log "sweeping_incomplete_run run_id=$(basename "$_run")"
            rm -rf "$_run"
        fi
    done
}

# ---------------------------------------------------------------------------
# Stage: config
#
# Nothing below this block may run against an unconfigured deployment, because
# everything below this block produces plaintext.
# ---------------------------------------------------------------------------
mkdir -p "$BACKUP_ROOT" "$ARTIFACTS" "$WAL_SPOOL"

[ -n "$DB_URL" ] || die_config "BACKUP_DATABASE_URL is empty; there is nothing to back up"
[ -n "$REMOTE" ] || die_config "BACKUP_REMOTE is empty; a local-only backup is not a backup"
[ -d "$BLOB_ROOT" ] || die_config "BACKUP_BLOB_ROOT '$BLOB_ROOT' is not a directory; the blob volume is not mounted"

# `KEEP_RUNS` is arithmetic in three places and a JSON number in a fourth, so a
# typo in it is not a bad default — it is `[ "$_total" -gt "" ]` aborting the
# run, or `"keep_runs": abc` making the status file unparseable, or (for `0`)
# the prune deleting the run that was just taken, at both ends, leaving the
# deployment with no backups at all and a status file saying success.
case "$KEEP_RUNS" in
    '' | *[!0-9]*) die_config "BACKUP_KEEP_RUNS='$KEEP_RUNS' is not a number" ;;
esac
[ "$KEEP_RUNS" -ge 1 ] || die_config "BACKUP_KEEP_RUNS must be at least 1; 0 would delete the run it just took"

# An SSH destination needs a key that is a *file*. `[ -s ]` is true for a
# directory, so a `BACKUP_SSH_KEY` pointed at the secret store's folder rather
# than at the key inside it passes every naive check and then fails `rsync`
# every night with an ssh error three layers down.
case "$REMOTE" in
    *:*)
        [ -n "$SSH_KEY" ] || die_config "BACKUP_REMOTE is an SSH destination but BACKUP_SSH_KEY is empty"
        [ -f "$SSH_KEY" ] || die_config "BACKUP_SSH_KEY '$SSH_KEY' is not a regular file"
        [ -n "${REMOTE#*:}" ] || die_config "BACKUP_REMOTE '$REMOTE' names a host with no path"
        ;;
esac

# The refusal AD-11 actually turns on. Checked here, before `pg_basebackup`, so
# that a deployment which has never set a recipient produces **no plaintext
# artifact at all** rather than one it then fails to encrypt.
[ -n "$RECIPIENT" ] || die_config "BACKUP_AGE_RECIPIENT is empty; refusing to create a plaintext artifact"

# …and that the recipient is one `age` will actually accept. An empty message
# encrypted to it: the same code path the real artifacts take, over zero bytes.
# Without this a typo'd recipient is discovered at the encrypt stage, by which
# time a full `pg_dump` of the claim book is sitting on disk in the clear.
if printf '' | age -r "$RECIPIENT" >/dev/null 2>"$ERR_FILE"; then
    :
else
    die_config "BACKUP_AGE_RECIPIENT is not a recipient age can encrypt to"
fi

# The WAL receiver's state, as `entrypoint.sh` last recorded it. Read here so a
# run that happens while the stream is down says so in its own status file — the
# I/O matrix's "a run that finds it down still dumps", and the reason a WAL gap
# is visible rather than silent.
if [ "$WAL_MODE" = "on" ] && [ "$(cat "$WAL_STATE" 2>/dev/null || echo down)" = "streaming" ]; then
    wal_streaming="true"
fi
if [ -f "$WAL_RESTARTS" ]; then
    wal_restarts="$(cat "$WAL_RESTARTS")"
fi
# `wal_restarts` is written into `status.json` **unquoted**, as a JSON number.
# `entrypoint.sh` truncates that file with `>` before rewriting it, so a run
# that reads it in that instant gets an empty string and emits
# `"wal_restarts": ,` — a status file that `healthcheck.sh`, the e2e spec and
# any future monitor all fail to parse, for a run that otherwise succeeded.
case "$wal_restarts" in
    '' | *[!0-9]*) wal_restarts="0" ;;
esac

# Nothing below may run twice at once against this volume. `docker compose run
# --rm backup once` during a scheduled run (a drill, a test, an operator) would
# otherwise share `$plain_dir`, `$ERR_FILE`, the WAL spool and `status.json`
# with it — and, at one-second run granularity, possibly the run id itself.
# `flock` releases on process exit, including a kill, so there is no stale lock
# to clean up.
exec 9>"$BACKUP_ROOT/.lock"
flock -n 9 || die_config "another backup run holds the lock on $BACKUP_ROOT"

sweep_incomplete

# **A hard ceiling on the WAL spool, because an unshippable archive must not
# become a database outage.**
#
# `pg_receivewal` writes 16 MB segments continuously and segments leave the
# spool only after a successful copy. So a destination that is down for a week —
# or a recipient nobody ever configured — used to mean the spool grew without
# bound, on a volume that by default shares a filesystem with `pgdata`. The
# database then stops accepting writes, which is exactly the outage
# `max_slot_wal_keep_size` was added to this story to prevent, relocated one
# directory to the left.
#
# The trade is deliberate and it is the same one the server-side bound makes: a
# **recorded** gap in the WAL archive beats an unrecoverable host. The oldest
# segments go first, the count goes into `status.json` as `wal_dropped`, and a
# log line names it. Procedure B's archive is discontinuous from that point;
# Procedure A's nightly dump is untouched, which is why the dump is the drill.
#
# **Enforced here, before anything else, and not after the copy.** It was written
# after `ship_wal` first, which is the one place it could never fire: segments
# leave the spool only on a *successful* copy, so a run whose copy failed exits
# through the trap long before reaching it — and a failing copy is the entire
# scenario this ceiling exists for. Dropping the oldest segments up front is also
# the right semantic: they are the ones that have been unshippable longest.
wal_dropped=0
if [ "$WAL_MODE" = "on" ]; then
    _spool_segments="$(ls -1 "$WAL_SPOOL" 2>/dev/null | wc -l | tr -d ' ')"
    if [ "$_spool_segments" -gt "$MAX_SPOOL_SEGMENTS" ]; then
        _over="$((_spool_segments - MAX_SPOOL_SEGMENTS))"
        ls -1 "$WAL_SPOOL" | sort | head -n "$_over" | while read -r _old; do
            [ -n "$_old" ] || continue
            rm -f "$WAL_SPOOL/$_old"
        done
        wal_dropped="$_over"
        log "wal_spool_over_ceiling dropped=$_over ceiling=$MAX_SPOOL_SEGMENTS"
    fi
fi


run_id="$(date -u +%Y%m%dT%H%M%SZ)"
run_dir="$ARTIFACTS/$run_id"
plain_dir="$run_dir/plain"
mkdir -p "$plain_dir" "$run_dir/wal"
log "run_started run_id=$run_id wal_streaming=$wal_streaming"

# ---------------------------------------------------------------------------
# Stage: base — the physical backup.
#
# `-Ft -z`: one compressed tar per tablespace rather than a directory tree, so
# the artifact set is a handful of files to encrypt and ship instead of a
# thousand. `-X fetch`: the WAL generated *during* the backup is collected at
# the end over the same connection and written into `base.tar.gz`, which makes
# the base backup self-consistent on its own — `-X none` would produce a base
# that cannot be started without the archive, and a base that depends on the
# archive being complete is a base that fails exactly when the archive did.
#
# `pg_basebackup` also writes `backup_manifest`, which is what `pg_verifybackup`
# reads. It is encrypted and shipped with everything else: verifying a restored
# base against its own manifest is the cheapest check in the runbook and
# throwing the manifest away to keep the artifact list tidy would remove it.
# ---------------------------------------------------------------------------
attempt "base" pg_basebackup --dbname="$DB_URL" --pgdata="$plain_dir/base" \
    --format=tar --gzip --wal-method=stream --no-password --checkpoint=fast

# ---------------------------------------------------------------------------
# Stage: dump — the logical backup, and the one the drill restores.
#
# `-Fc` (custom): compressed, and `pg_restore`-able selectively and in parallel,
# which a plain SQL script is not. This is the artifact that survives a major
# version change — the base backup above does not, because a PostgreSQL 19 data
# directory cannot be started from a PostgreSQL 18 one — so it is the artifact
# Procedure A of `deploy/RESTORE-DRILL.md` uses and the one AC 1 is about.
#
# One database, and that is AD-3's whole point: every PHI-class store in this
# system lives in `lineworker`, so this single command covers the relational
# entities, the pgvector embeddings, the vendored checkpoint tables, the insight
# cache and the audit log. There is deliberately no second dump path anywhere.
# ---------------------------------------------------------------------------
attempt "dump" pg_dump --dbname="$DB_URL" --format=custom --no-password \
    --file="$plain_dir/dump"

# ---------------------------------------------------------------------------
# Stage: globals — the roles, without which the dump above restores into a
# database the application cannot open.
#
# **This stage exists because the executed restore drill found its absence.**
# `pg_dump` dumps a *database*; roles are cluster-level objects and are not in
# it. Restoring `dump` alone therefore replays 69 `GRANT … TO lineworker_app`
# statements against a cluster where that role does not exist, every one of them
# fails, and `pg_restore` finishes with `errors ignored on restore: 69` and exit
# status 0 — a restore that looks successful and produces a database whose data
# is complete, whose `lineworker_app` role is missing (so the api cannot
# connect at all), and whose `audit_redactor` grants and row-level-security
# policies — the invariant the whole AD-4 audit design rests on — are silently
# gone. Reproduced on 2026-08-23 against the e2e profile; see
# `deploy/RESTORE-DRILL.md § Executed drill`.
#
# `--globals-only` is roles and tablespaces and nothing else, so this is not a
# second copy of the data and does not weaken AD-3's one-stream rule. It does
# carry the roles' SCRAM password verifiers, which is credential material and
# exactly why it is encrypted with everything else and never written anywhere
# but `$plain_dir` on its way there.
#
# Procedure B does not need it: `pg_basebackup` is a physical copy of the
# cluster and `pg_authid` is inside it.
# ---------------------------------------------------------------------------
attempt "globals" pg_dumpall --dbname="$DB_URL" --globals-only --no-password \
    --file="$plain_dir/globals.sql"

# ---------------------------------------------------------------------------
# Stage: blobs — the one PHI-class store that is not inside Postgres.
#
# `VolumeBlobStore` keys are flat names by construction (the store refuses a key
# containing a separator), so the volume is a single directory of files and a
# plain `tar -C … .` is the whole of it. It is normal and expected for this to
# be an empty archive today: `document.blob_key` and `photo.blob_key` are NULL
# on every seeded row and nothing writes one yet. Covering the volume from birth
# is the point — a backup that covered only what existed on the day it was
# written would silently stop being complete the first time a real PDF landed.
# ---------------------------------------------------------------------------
# `tar` exits **1** for "file changed as we read it" and 2 for a real error.
# The api writing a blob during the nightly window is normal and would otherwise
# throw away a finished dump and a finished base backup over one file that will
# be in tomorrow's archive anyway. 2 still fails the run.
attempt "blobs" sh -c '
    tar -cf "$1" -C "$2" . || [ $? -eq 1 ]
' _ "$plain_dir/blobs.tar" "$BLOB_ROOT"

# ---------------------------------------------------------------------------
# Stage: encrypt — age -r, and nothing plaintext survives it.
#
# This is the line AD-11 draws. Everything above produced plaintext inside
# `$plain_dir`; everything below sees only `$run_dir`'s ciphertext. Each source
# is removed as soon as its `.age` exists, and `$plain_dir` itself is removed at
# the end of the stage, so the directory `rsync` is pointed at in the copy stage
# does not physically contain a plaintext byte.
# ---------------------------------------------------------------------------
encrypt_into() {
    _src="$1"
    _dest="$2"
    attempt "encrypt" age -r "$RECIPIENT" -o "$run_dir/$_dest" "$_src"
    rm -f "$_src"
}

# **Every** tar the base backup produced, not just `base.tar.gz`. A cluster
# with a second tablespace makes `pg_basebackup -Ft` write one `<oid>.tar.gz`
# per tablespace beside it; naming only `base.tar.gz` would encrypt and ship a
# base backup that silently omits whatever lives in them, and delete the rest
# with `$plain_dir` a few lines below. There is no second tablespace in this
# deployment today, which is exactly why the omission would go unnoticed.
for _tar in "$plain_dir"/base/*.tar.gz; do
    [ -f "$_tar" ] || continue
    encrypt_into "$_tar" "$(basename "$_tar").age"
done
encrypt_into "$plain_dir/base/backup_manifest" "base_manifest.age"
encrypt_into "$plain_dir/dump" "dump.age"
encrypt_into "$plain_dir/globals.sql" "globals.sql.age"
encrypt_into "$plain_dir/blobs.tar" "blobs.tar.age"

# The WAL segments `pg_receivewal` has completed since the last run. `.partial`
# is the segment currently being written and is deliberately skipped: it is
# still growing, and shipping a torn copy of it would put a file in the archive
# that replay refuses. It is picked up by the next run, complete.
#
# The segments are encrypted into this run and **not** deleted from the spool
# yet — that happens after the copy succeeds, so a failed `rsync` leaves them
# where the next run will find them rather than dropping them on the floor.
for _seg in "$WAL_SPOOL"/*; do
    [ -f "$_seg" ] || continue
    _name="$(basename "$_seg")"
    case "$_name" in
        *.partial) continue ;;
    esac
    attempt "encrypt" age -r "$RECIPIENT" -o "$run_dir/wal/$_name.age" "$_seg"
    wal_segments="$((wal_segments + 1))"
    shipped_wal="$shipped_wal $_name"
done

rm -rf "$plain_dir"

# ---------------------------------------------------------------------------
# Stage: manifest — names, byte counts, sha256. Never content.
#
# The checksums are what make "the artifact that arrived off-host is the
# artifact that was written" answerable at restore time, which is the question
# `rsync`'s own exit code does not answer for a copy that succeeded months ago.
# They are checksums of *ciphertext*, computed after encryption, so verifying
# one needs no identity and can be done on the destination host by somebody who
# cannot read a byte of what they are verifying.
# ---------------------------------------------------------------------------
stage="manifest"
_entries="$run_dir/.entries"
_status_entries="$run_dir/.status-entries"
: >"$_entries"
: >"$_status_entries"
for _rel in $(cd "$run_dir" && find . -type f ! -name '.entries' ! -name '.status-entries' | sed 's|^\./||' | sort); do
    _bytes="$(stat -c %s "$run_dir/$_rel")"
    _sha="$(sha256sum "$run_dir/$_rel" | cut -d' ' -f1)"
    printf '    {"name": "%s", "bytes": %s, "sha256": "%s"},\n' "$_rel" "$_bytes" "$_sha" >>"$_entries"
    printf '{"name": "%s", "bytes": %s},' "$_rel" "$_bytes" >>"$_status_entries"
done
{
    printf '{\n'
    printf '  "run_id": "%s",\n' "$run_id"
    printf '  "created_at": "%s",\n' "$started_at"
    printf '  "wal_segments": %s,\n' "$wal_segments"
    printf '  "artifacts": [\n'
    sed '$ s/,$//' "$_entries"
    printf '  ]\n'
    printf '}\n'
} >"$run_dir/manifest.json"
artifacts_json="$(sed 's/,$//' "$_status_entries")"
rm -f "$_entries" "$_status_entries"

# ---------------------------------------------------------------------------
# Stage: copy — and only now does anything leave the host.
#
# Two transports behind one variable, distinguished the way `scp` and `rsync`
# have always distinguished them: a destination containing a colon is
# `host:path` and goes over SSH, anything else is a filesystem path. The local
# form is not a toy — it is how a destination on a mounted NFS export or a
# second physical disk is configured, and it is what the e2e profile and the
# executed drill use, which means the `rsync` invocation itself is exercised by
# CI even though the transport under it is not.
#
# `-s` on the private key file rather than `-n` on the variable: the prod
# overlay mounts `/dev/null` over the key path when no key was configured, so
# "a key was supplied" is a question about the file's size, not about whether
# the mount exists.
# ---------------------------------------------------------------------------
stage="copy"

# **Every unshipped run goes, not just this one.** A run that fails at `copy`
# keeps its finished, encrypted artifacts locally (the I/O matrix says so) — but
# nothing re-sent them, so seven nights of an unreachable destination used to
# mean seven local runs, zero off-host copies, and then the retention loop
# rotating the oldest of them away unshipped. A `.shipped` marker per run is the
# whole mechanism: it is written after a successful copy, it is not shipped
# itself (it is written *after* the manifest, so it is not in it), and the list
# below is every run that does not have one, oldest first.
copy_pending="$(cd "$ARTIFACTS" && ls -1d $RUN_ID_GLOB 2>/dev/null | sort || true)"
ship_run() {
    _rid="$1"
    _dir="$ARTIFACTS/$_rid"
    case "$REMOTE" in
        *:*)
            if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
                attempt "copy" rsync -a --mkpath --timeout="$IO_TIMEOUT" \
                    -e "ssh -i $SSH_KEY $SSH_OPTS" "$_dir/" "$REMOTE/$_rid/"
            else
                attempt "copy" rsync -a --mkpath --timeout="$IO_TIMEOUT" \
                    -e "ssh $SSH_OPTS" "$_dir/" "$REMOTE/$_rid/"
            fi
            ;;
        *)
            # `mkdir -p` inside the copy stage on purpose: an unwritable or
            # non-existent destination fails *here*, with `stage: copy` in the
            # status file, rather than at the config stage where the message
            # would be about a variable rather than about a disk.
            attempt "copy" mkdir -p "$REMOTE/$_rid"
            attempt "copy" rsync -a --timeout="$IO_TIMEOUT" "$_dir/" "$REMOTE/$_rid/"
            ;;
    esac
    # After the copy and never before: this marker is what stops a later run
    # re-sending this one, so writing it early would record a failed transfer
    # as delivered. It is written after `manifest.json`, so it is not in the
    # manifest and is not itself shipped.
    : >"$_dir/.shipped"
}

for _pending in $copy_pending; do
    [ -d "$ARTIFACTS/$_pending" ] || continue
    [ -f "$ARTIFACTS/$_pending/manifest.json" ] || continue
    if [ -f "$ARTIFACTS/$_pending/.shipped" ]; then
        continue
    fi
    if [ "$_pending" != "$run_id" ]; then
        log "shipping_backlog run_id=$_pending"
    fi
    ship_run "$_pending"
done

# ---------------------------------------------------------------------------
# Stage: ship_wal — the shipped segments leave the spool.
#
# After the copy, never before. The spool is `pg_receivewal`'s working
# directory: a segment removed from it is a segment that exists only in this
# run's artifact set, so removing one before it has been copied off-host is how
# a failed transfer becomes a hole in the archive. The receiver does not care
# that files disappear behind it — it writes forward.
# ---------------------------------------------------------------------------
stage="ship_wal"
for _name in $shipped_wal; do
    rm -f "$WAL_SPOOL/$_name"
done

# ---------------------------------------------------------------------------
# Stage: prune — bounded retention, both sides.
#
# Bounded retention is also the *only* mitigation this story ships for the
# purge/backup interaction: a restored backup resurrects PHI that Story 8.1's
# cascade removed, and the story's explicit instruction is that the answer is a
# retention bound plus a stated caveat in the runbook, never purge-aware
# filtering of the backup. So this number is a compliance control, not a disk
# budget, and lowering or raising it is a decision about how long purged data
# stays recoverable. See `deploy/RESTORE-DRILL.md`.
# ---------------------------------------------------------------------------
stage="prune"
prune_dir "$ARTIFACTS"
case "$REMOTE" in
    *:*)
        _host="${REMOTE%%:*}"
        _path="${REMOTE#*:}"
        # Deliberately tolerant of a destination that does not yet hold more
        # than KEEP_RUNS runs, and deliberately not tolerant of an unreachable
        # host: the copy above already proved reachability, so a failure here is
        # a real one and belongs in the status file.
        # Same run-id filter as `prune_dir`, and for the same reason with more
        # at stake: this `rm -rf` runs on somebody else's machine, in a
        # directory that may hold anything. `ls -1d <glob>` lists only what this
        # pipeline created, so a `README`, a `lost+found` or the previous
        # system's archive is neither deleted nor counted towards the retention
        # bound.
        attempt "prune" ssh $SSH_OPTS "$_host" \
            "cd '$_path' && runs=\$(ls -1d $RUN_ID_GLOB 2>/dev/null | sort) && n=\$(printf '%s\\n' \"\$runs\" | grep -c . ) && if [ \$n -gt $KEEP_RUNS ]; then printf '%s\\n' \"\$runs\" | head -n \$((n - $KEEP_RUNS)) | xargs -r rm -rf; fi"
        ;;
    *)
        prune_dir "$REMOTE"
        ;;
esac

# ---------------------------------------------------------------------------
# Done.
# ---------------------------------------------------------------------------
stage="done"
runs_local="$(ls -1 "$ARTIFACTS" | wc -l | tr -d ' ')"
write_status "success" "0"
log "run_succeeded run_id=$run_id wal_segments=$wal_segments runs_local=$runs_local duration_seconds=$(($(date -u +%s) - started_epoch))"
