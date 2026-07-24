#!/usr/bin/env bash
set -Eeuo pipefail

state_dir="${BACKUP_STATE_DIR:-/var/lib/timsum-backup}"
max_age_hours="${BACKUP_MAX_AGE_HOURS:-26}"

[[ "$max_age_hours" =~ ^[1-9][0-9]*$ ]] || exit 2
[[ -f "$state_dir/scheduler_started_at" ]] || exit 1
[[ -f "$state_dir/status" ]] || exit 1
[[ "$(<"$state_dir/status")" == "success" ]] || exit 1
[[ -f "$state_dir/last_attempt_at" ]] || exit 1
[[ -f "$state_dir/last_success_at" ]] || exit 1

last_attempt="$(<"$state_dir/last_attempt_at")"
last_success="$(<"$state_dir/last_success_at")"
[[ "$last_attempt" =~ ^[0-9]+$ ]] || exit 1
[[ "$last_success" =~ ^[0-9]+$ ]] || exit 1

now="$(date +%s)"
max_age_seconds="$((max_age_hours * 3600))"
(( now >= last_success && now - last_success <= max_age_seconds )) || exit 1
(( last_success >= last_attempt )) || exit 1
