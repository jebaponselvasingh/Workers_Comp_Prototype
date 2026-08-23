#!/bin/sh
# The backup container's health, derived from its own last run (Story 8.3, AC 3).
#
# This is the whole of AC 3's binding surface. There is no monitoring stack and
# no alerting integration — both are deferred by decision — so "a failed nightly
# run surfaces non-silently rather than being discovered at restore time" means
# exactly one thing: `docker compose ps` shows the backup container `unhealthy`,
# beside every other service's health, and it does so for a run that failed *and*
# for a scheduler that quietly stopped.
#
# ## The rule
#
#   unhealthy  <-  the last run failed
#   unhealthy  <-  now - max(last_success, container_start) > BACKUP_MAX_AGE_HOURS
#   healthy    <-  otherwise, including "no run yet, and this container is young"
#
# **`max(last_success, container_start)`, not `last_success` alone**, and Design
# Note 6 is the argument. The literal reading of "unhealthy when there has been
# no successful run in the last 26 hours" is right for a container that has been
# up for days and wrong for one that started a minute ago: it makes every fresh
# stack unhealthy and `docker compose up --wait` fail on a deployment where
# nothing whatever is wrong. Measuring from container start says what is
# actually meant — *this container has not missed a backup it was responsible
# for* — and a container that has been up for two days with no successful run
# has missed one whichever way you count.
#
# **A missing status file with a young container is healthy; a missing start
# marker is not.** The first is the normal state of a stack that came up ten
# minutes ago. The second means `entrypoint.sh` never ran, which is a broken
# container reporting on itself, and the honest answer to that is a failure
# rather than a shrug.
#
# ## Reading status.json with `sed`
#
# There is no `jq` in this image and adding one for two scalars would be a
# package to keep patched for the sake of a file this repository writes itself,
# in a fixed shape, one field per line. `backup.sh` builds it with `printf` and
# `mv`s it into place atomically, so the two `sed` expressions below are reading
# a format with exactly one producer. If that ever stops being true — a second
# writer, a re-indent — this file is the other half of the change.
set -eu

BACKUP_ROOT="${BACKUP_ROOT:-/var/lib/lineworker/backup}"
STATUS="$BACKUP_ROOT/status.json"
START_MARKER="$BACKUP_ROOT/container_started_at"
MAX_AGE_HOURS="${BACKUP_MAX_AGE_HOURS:-26}"

window="$((MAX_AGE_HOURS * 3600))"
now="$(date -u +%s)"

started="$(cat "$START_MARKER" 2>/dev/null || true)"
if [ -z "$started" ]; then
    printf 'unhealthy: no container-start marker at %s; entrypoint.sh did not run\n' \
        "$START_MARKER" >&2
    exit 1
fi

if [ ! -f "$STATUS" ]; then
    if [ "$((now - started))" -le "$window" ]; then
        printf 'healthy: no run yet, container is %ss old (window %ss)\n' \
            "$((now - started))" "$window"
        exit 0
    fi
    printf 'unhealthy: no backup run at all in %s hours since this container started\n' \
        "$MAX_AGE_HOURS" >&2
    exit 1
fi

result="$(sed -n 's/^[[:space:]]*"result": "\([a-z]*\)".*/\1/p' "$STATUS" | head -n 1)"
finished="$(sed -n 's/^[[:space:]]*"finished_at_epoch": \([0-9]*\).*/\1/p' "$STATUS" | head -n 1)"
stage="$(sed -n 's/^[[:space:]]*"stage": "\([a-z_]*\)".*/\1/p' "$STATUS" | head -n 1)"

if [ -z "$result" ] || [ -z "$finished" ]; then
    printf 'unhealthy: %s is unreadable; result=%s finished_at_epoch=%s\n' \
        "$STATUS" "${result:-<none>}" "${finished:-<none>}" >&2
    exit 1
fi

if [ "$result" != "success" ]; then
    printf 'unhealthy: the last backup run failed at stage %s\n' "${stage:-<unknown>}" >&2
    exit 1
fi

# The later of the two, which is the point of this whole file.
reference="$started"
if [ "$finished" -gt "$reference" ]; then
    reference="$finished"
fi

age="$((now - reference))"
if [ "$age" -gt "$window" ]; then
    printf 'unhealthy: last successful backup was %ss ago, window is %ss (%s hours)\n' \
        "$age" "$window" "$MAX_AGE_HOURS" >&2
    exit 1
fi

# **A dead WAL receiver is an unhealthy container, when WAL is what the
# deployment's recovery-point objective rests on.**
#
# Everything above this line is about the nightly dump, whose RPO is 24 hours.
# `compose.prod.yaml` turns the WAL stream on precisely to make that number
# seconds instead — so a receiver that has been down since Tuesday, while the
# nightly dump keeps succeeding, is a deployment whose stated RPO silently
# degraded by four orders of magnitude with every surface still green. Both
# `recreate_slot` and `start_receiver` end in `|| true`, so nothing else would
# ever have said so.
#
# Read from `wal_state`, which `entrypoint.sh` writes live, rather than from
# `status.json`'s `wal_streaming`, which is only as fresh as the last run — a
# stream that died an hour after the nightly dump would look fine for a day.
if [ "${BACKUP_WAL:-off}" = "on" ]; then
    wal_state="$(cat "$BACKUP_ROOT/wal_state" 2>/dev/null || true)"
    if [ "$wal_state" != "streaming" ]; then
        printf 'unhealthy: BACKUP_WAL=on but the WAL receiver is %s; the archive has a gap and the RPO is the nightly dump\n' \
            "${wal_state:-<unknown>}" >&2
        exit 1
    fi
fi

printf 'healthy: last successful backup %ss ago (window %ss)\n' "$age" "$window"
exit 0
