#!/bin/sh
# PID 1 of the backup container (Story 8.3). Two modes and nothing else:
#
#   entrypoint.sh          the scheduler: a WAL receiver plus a minute-resolution
#                          timer that fires `backup.sh` once a day at BACKUP_AT
#   entrypoint.sh once     exactly one run, no receiver, no loop; the container's
#                          exit code *is* the run's
#
# `once` is what `docker compose run --rm backup once` invokes, and it is the
# only surface the drill, the e2e spec and `tests/test_backup_restore.py` use.
# It `exec`s rather than calls, so nothing in this file can alter the exit code
# an operator or a test is reading.
#
# ## Why a 60-second poll loop and not cron
#
# Debian's `cron` does not see the container's environment. Every knob this job
# has arrives as an environment variable from a compose file, and the classic
# fix — dumping `env` to a file at container start and sourcing it from the
# crontab — is a second copy of the configuration that goes stale silently. In a
# story whose entire subject is eliminating silent failure that is the wrong
# trade twice over. `supercronic` solves it properly and arrives as a pinned
# binary downloaded from GitHub at image build, which this project has no
# precedent for and which the standing "locally built images break pull-only
# deployments" deferral already argues against widening.
#
# A loop that wakes each minute, compares `%H:%M` to BACKUP_AT and checks
# whether today has already run needs no package, no environment plumbing and no
# cron syntax. It is also stop-friendly, which matters more than it sounds: a
# `sleep` long enough to span a night ignores SIGTERM until it returns, so
# `docker compose down` on a container sleeping until 02:30 waits for the ten
# second kill timeout and then SIGKILLs it — during a backup, if the timing is
# unlucky. The sleep below runs in the background with an explicit `wait`, so a
# signal is handled the second it arrives.
#
# And the failure this design is most often accused of — "what if the loop
# stops?" — is precisely the one `healthcheck.sh` catches, by measuring the age
# of the last *successful run* rather than by trusting any scheduler to report
# on itself.
set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/lineworker/backup}"
WAL_SPOOL="$BACKUP_ROOT/wal"
START_MARKER="$BACKUP_ROOT/container_started_at"
LAST_RUN_DATE="$BACKUP_ROOT/last_run_date"
WAL_STATE="$BACKUP_ROOT/wal_state"
WAL_RESTARTS="$BACKUP_ROOT/wal_restarts"

BACKUP_AT="${BACKUP_AT:-02:30}"
# Validated, because an unvalidated schedule is a scheduler that never fires and
# says nothing about it. `2:30`, `26:00` and a trailing space all compare
# unequal to every `date` output for ever; the first symptom would be the
# healthcheck going stale twenty-six hours later, with nothing anywhere naming
# the cause. Exit 78 (EX_CONFIG), the same code `backup.sh` uses for the same
# class of mistake.
case "$BACKUP_AT" in
    [0-1][0-9]:[0-5][0-9] | 2[0-3]:[0-5][0-9]) ;;
    *)
        printf 'backup: BACKUP_AT=%s is not a 24-hour HH:MM time\n' "$BACKUP_AT" >&2
        exit 78
        ;;
esac
# `BACKUP_AT` as minutes since midnight, resolved once. Validated above, so the
# leading-zero strip below cannot fail.
_at_h="${BACKUP_AT%%:*}"
_at_m="${BACKUP_AT##*:}"
_at_h="${_at_h#0}"
_at_m="${_at_m#0}"
[ -n "$_at_h" ] || _at_h=0
[ -n "$_at_m" ] || _at_m=0
AT_ABS="$((_at_h * 60 + _at_m))"
# How wide the nightly window is. Ten minutes, so a loop iteration that overruns
# its sixty seconds does not step over the whole night; `last_run_date` is what
# keeps it to one run per day regardless.
WINDOW_MINUTES=10

WAL_MODE="${BACKUP_WAL:-off}"
DB_URL="${BACKUP_DATABASE_URL:-}"
SLOT="${BACKUP_SLOT:-lineworker_backup}"

mkdir -p "$BACKUP_ROOT" "$WAL_SPOOL"

# **The container-start marker is written in both modes**, and Design Note 6 is
# why. The healthcheck's staleness window is measured from
# `max(last_success, container_start)`, so without this marker a freshly started
# container with no run yet is indistinguishable from one that has been up for a
# week and missed every backup — and the literal reading ("unhealthy when no
# success in 26 hours") makes every fresh stack unhealthy and `docker compose up
# --wait` fail. Writing it in `once` mode too costs one `date` and makes the
# healthcheck meaningful for a container that only ever ran once, which is
# exactly what `tests/test_backup_restore.py`'s failure-path test checks.
# Review asked whether `once` should stop writing it, on the grounds that an
# ad-hoc run would reset the staleness reference of a scheduler sharing the
# volume. It should not, and the reason is that the reference is
# `max(last_success, container_start)`: a *successful* ad-hoc run resets it
# through `status.json` anyway, and legitimately, because a backup was in fact
# taken; a *failed* one leaves `result: "failure"`, which `healthcheck.sh`
# reports unhealthy on before it looks at any clock. So the marker write changes
# no outcome, while removing it would leave every `once`-only volume — the
# pytest round trip and the e2e spec both — with no marker at all and a
# healthcheck that refuses for a reason that has nothing to do with backups.
date -u +%s >"$START_MARKER"

case "${1:-}" in
    once)
        exec /usr/local/bin/backup.sh
        ;;
    "")
        ;;
    *)
        printf 'usage: entrypoint.sh [once]\n' >&2
        exit 64
        ;;
esac

# ---------------------------------------------------------------------------
# Scheduler mode
# ---------------------------------------------------------------------------

receiver_pid=""
wal_backoff=5
wal_next_attempt=0

log() {
    printf '%s backup.%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"
}

# SIGTERM has to reach the WAL receiver as well as this loop. Without the
# explicit kill, `docker compose down` stops PID 1 and the receiver is reaped by
# the kernel mid-segment; with it, `pg_receivewal` flushes and closes the
# `.partial` segment it was writing, which is the difference between a partial
# segment the next run skips and a partial segment nothing ever completes.
on_term() {
    log "stopping"
    if [ -n "$receiver_pid" ]; then
        kill "$receiver_pid" 2>/dev/null || true
        wait "$receiver_pid" 2>/dev/null || true
    fi
    printf 'down\n' >"$WAL_STATE"
    exit 0
}
trap on_term TERM INT

# Start `pg_receivewal` in the background, creating the slot if it is not there.
#
# Two invocations because the tool is two tools: `--create-slot` creates and
# exits, it does not create-then-stream. `--if-not-exists` makes the first
# invocation idempotent across restarts, and its failure is logged rather than
# fatal — an existing slot is the normal case and the streaming invocation is
# the one whose failure matters.
#
# `--no-loop` is deliberate and is the opposite of what it sounds like. The
# default is for `pg_receivewal` to retry a lost connection for ever, silently;
# with `--no-loop` it exits, this loop notices, `wal_state` goes to `down`, the
# restart counter increments and the next `status.json` says
# `wal_streaming: false`. A gap that is visible is the whole requirement.
start_receiver() {
    if pg_receivewal --dbname="$DB_URL" --directory="$WAL_SPOOL" --slot="$SLOT" \
        --create-slot --if-not-exists --no-password >/dev/null 2>&1; then
        :
    else
        log "wal_slot_create_failed slot=$SLOT"
    fi
    pg_receivewal --dbname="$DB_URL" --directory="$WAL_SPOOL" --slot="$SLOT" \
        --no-loop --no-password &
    receiver_pid=$!
    # Backgrounding always succeeds, so `streaming` written here unconditionally
    # was a claim about having *started a process*, not about having a stream: a
    # receiver that exits immediately (bad credentials, an invalidated slot, no
    # route) left `wal_state` saying `streaming` until the next loop iteration
    # noticed, and any run in that window recorded `wal_streaming: true` for a
    # stream that never existed. One second is longer than any of those failures
    # takes and shorter than the loop it sits in.
    sleep 1
    if kill -0 "$receiver_pid" 2>/dev/null; then
        printf 'streaming\n' >"$WAL_STATE"
        log "wal_receiver_started slot=$SLOT"
    else
        receiver_pid=""
        printf 'down\n' >"$WAL_STATE"
        log "wal_receiver_exited_immediately slot=$SLOT"
    fi
}

# The recovery path for a slot Postgres invalidated.
#
# `max_slot_wal_keep_size=2GB` on the server (added to every profile by this
# story) means a slot whose consumer has been away long enough is invalidated
# rather than allowed to pin WAL until the data volume fills — the database
# keeps serving, which is the right trade, and the cost is that the slot cannot
# simply be resumed. `--create-slot --if-not-exists` will not fix it: the slot
# row still exists, so "not exists" is false and the streaming connection keeps
# failing for ever.
#
# Dropping and recreating is therefore the only way back, and it is done only at
# the *ceiling* of the backoff rather than on the first failure — a transient
# network blip resumes losslessly from an intact slot, and dropping one on every
# hiccup would manufacture the WAL gap this function exists to recover from.
recreate_slot() {
    log "wal_slot_recreating slot=$SLOT"
    pg_receivewal --dbname="$DB_URL" --directory="$WAL_SPOOL" --slot="$SLOT" \
        --drop-slot --no-password >/dev/null 2>&1 || true
    pg_receivewal --dbname="$DB_URL" --directory="$WAL_SPOOL" --slot="$SLOT" \
        --create-slot --no-password >/dev/null 2>&1 || true
}

if [ ! -f "$WAL_RESTARTS" ]; then
    printf '0\n' >"$WAL_RESTARTS"
fi

if [ "$WAL_MODE" = "on" ]; then
    start_receiver
else
    printf 'off\n' >"$WAL_STATE"
    log "wal_disabled"
fi

# **Sweep plaintext before anything else, at every container start.**
#
# `backup.sh` stages plaintext in `artifacts/<runId>/plain/` and removes it in
# its EXIT trap — which is precisely what does not run when the container is
# SIGKILLed, OOM-killed, or stopped past its grace period during a base backup
# that takes minutes. Without this line, one such kill leaves an unencrypted
# `pg_dump` of the entire claim book in the backup volume until the *next* run
# happens to sweep it, and in the e2e profile that volume is a bind mount on a
# developer's filesystem. `backup.sh` sweeps at the start of each run too; this
# is the half that covers the container that starts and then idles until 02:30.
for _stale in "$BACKUP_ROOT"/artifacts/*/plain; do
    [ -d "$_stale" ] || continue
    log "sweeping_plaintext run_id=$(basename "$(dirname "$_stale")")"
    rm -rf "$(dirname "$_stale")"
done

log "scheduler_started at=$BACKUP_AT wal=$WAL_MODE"

while :; do
    if [ "$WAL_MODE" = "on" ]; then
        if [ -z "$receiver_pid" ] || ! kill -0 "$receiver_pid" 2>/dev/null; then
            printf 'down\n' >"$WAL_STATE"
            _now="$(date -u +%s)"
            if [ "$_now" -ge "$wal_next_attempt" ]; then
                # Read defensively: an empty or non-numeric counter file makes
                # this arithmetic a syntax error, and `set -e` would end PID 1
                # over a corrupt integer — the container exits and, with no
                # restart policy on the service, backups never run again.
                _prev="$(cat "$WAL_RESTARTS" 2>/dev/null || true)"
                case "$_prev" in
                    '' | *[!0-9]*) _prev=0 ;;
                esac
                _restarts="$((_prev + 1))"
                printf '%s\n' "$_restarts" >"$WAL_RESTARTS"
                log "wal_receiver_down restarts=$_restarts backoff_seconds=$wal_backoff"
                if [ "$wal_backoff" -ge 300 ]; then
                    recreate_slot
                fi
                start_receiver
                wal_next_attempt="$((_now + wal_backoff))"
                if [ "$wal_backoff" -lt 300 ]; then
                    wal_backoff="$((wal_backoff * 2))"
                fi
            fi
        else
            # Healthy for a full cycle: reset the backoff so the *next* outage
            # gets a fast first retry rather than inheriting the last one's
            # ceiling. Without this a stream that recovers after a long outage
            # waits five minutes to notice its next one.
            wal_backoff=5
        fi
    fi

    # **UTC, and a window rather than an exact minute.** Two failures the
    # equality test had:
    #
    #   * one loop iteration that takes longer than sixty seconds — a blocking
    #     `pg_receivewal --create-slot` against a database that is down is
    #     enough — steps straight over the single matching minute, and the next
    #     attempt is twenty-four hours later;
    #   * in a zone with daylight saving, the hour that is skipped every spring
    #     never contains any minute at all, so a `BACKUP_AT` inside it never
    #     fires and one night a year is silently missed.
    #
    # `date -u` removes the second entirely (UTC has no such hour) and the ten
    # minute window removes the first. `last_run_date` still bounds it to one
    # run per day, so a window does not mean ten runs.
    # Minutes since UTC midnight, from the epoch rather than by parsing `date`
    # output: `$((10#$x))` is the usual way to defuse a leading zero and it is a
    # bash extension, while this container's `/bin/sh` is dash, where `08` is an
    # invalid octal constant and the arithmetic aborts PID 1 under `set -e` at
    # exactly two times of day.
    _now_abs="$((($(date -u +%s) % 86400) / 60))"
    _today="$(date -u +%F)"
    _last="$(cat "$LAST_RUN_DATE" 2>/dev/null || true)"
    # Modulo the whole day so a BACKUP_AT close to midnight gets its full window
    # instead of a truncated one.
    if [ "$(((_now_abs - AT_ABS + 1440) % 1440))" -lt "$WINDOW_MINUTES" ] &&
        [ "$_last" != "$_today" ]; then
        # The date is recorded **before** the run, not after. A run that fails
        # must not be retried every sixty seconds for the rest of the minute (or
        # for the rest of the night, if it fails fast) — the failure is already
        # surfaced by `status.json` and the healthcheck, and a tight retry loop
        # against a database that is down is how a backup job becomes the
        # outage. Tomorrow's run is the retry.
        printf '%s\n' "$_today" >"$LAST_RUN_DATE"
        if /usr/local/bin/backup.sh; then
            :
        else
            # Swallowed on purpose: `set -e` would end PID 1 here, the container
            # would exit, and compose's restart policy — or its absence — would
            # decide whether backups ever run again. A failed run is a failed
            # run; the scheduler outliving it is what makes tomorrow's attempt
            # happen, and the healthcheck is what makes today's failure visible.
            log "scheduled_run_failed"
        fi
    fi

    # Backgrounded so the TERM trap fires immediately rather than up to sixty
    # seconds later. `|| true` because `wait` returns non-zero when a signal
    # interrupts it, and `set -e` would otherwise turn a clean shutdown into a
    # crash.
    sleep 60 &
    wait "$!" || true
done
