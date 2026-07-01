#!/usr/bin/env bash
set -Eeuo pipefail

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

run_backup() {
  if /usr/local/bin/backup-mongodb; then
    return 0
  fi
  log "Backup failed; the scheduler will retry at the next scheduled run" >&2
  return 1
}

if [[ "${BACKUP_ONCE:-false}" == "true" ]]; then
  exec /usr/local/bin/backup-mongodb
fi

schedule_hour="${BACKUP_SCHEDULE_HOUR:-2}"
schedule_minute="${BACKUP_SCHEDULE_MINUTE:-0}"
[[ "$schedule_hour" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || {
  log "BACKUP_SCHEDULE_HOUR must be 0-23" >&2
  exit 2
}
[[ "$schedule_minute" =~ ^([0-9]|[1-5][0-9])$ ]] || {
  log "BACKUP_SCHEDULE_MINUTE must be 0-59" >&2
  exit 2
}

touch /tmp/backup-scheduler-ready

if [[ "${BACKUP_RUN_ON_STARTUP:-false}" == "true" ]]; then
  run_backup || true
fi

while true; do
  now_epoch="$(date +%s)"
  today_target="$(date -d "today ${schedule_hour}:${schedule_minute}:00" +%s)"
  if (( now_epoch < today_target )); then
    next_epoch="$today_target"
  else
    next_epoch="$(date -d "tomorrow ${schedule_hour}:${schedule_minute}:00" +%s)"
  fi
  wait_seconds="$((next_epoch - now_epoch))"
  log "Next backup: $(date -d "@$next_epoch" --iso-8601=seconds)"
  sleep "$wait_seconds"
  run_backup || true
done
