#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

log() {
  printf '%s %s\n' "$(date --iso-8601=seconds)" "$*"
}

fail() {
  log "ERROR: $*" >&2
  exit 1
}

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || fail "$name is required"
}

yaml_single_quote() {
  printf '%s' "$1" | sed "s/'/''/g"
}

MONGO_BACKUP_HOST="${MONGO_BACKUP_HOST:-mongo}"
MONGO_BACKUP_PORT="${MONGO_BACKUP_PORT:-27017}"
MONGO_BACKUP_USER="${MONGO_BACKUP_USER:-${MONGO_USER:-}}"
MONGO_BACKUP_PASS="${MONGO_BACKUP_PASS:-${MONGO_PASS:-}}"
MONGO_BACKUP_AUTH_DB="${MONGO_BACKUP_AUTH_DB:-admin}"
MONGO_DB_NAME="${MONGO_DB_NAME:-timsumv3}"
MONGO_BACKUP_USE_OPLOG="${MONGO_BACKUP_USE_OPLOG:-false}"

MINIO_ENDPOINT="${MINIO_ENDPOINT:-http://minio:9000}"
MINIO_ACCESS_KEY="${BACKUP_MINIO_ACCESS_KEY:-${MINIO_ACCESS_KEY:-${MINIO_USER:-}}}"
MINIO_SECRET_KEY="${BACKUP_MINIO_SECRET_KEY:-${MINIO_SECRET_KEY:-${MINIO_PASS:-}}}"
BACKUP_BUCKET="${BACKUP_BUCKET:-db-backups}"
BACKUP_RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-30}"
BACKUP_CONFIGURE_BUCKET="${BACKUP_CONFIGURE_BUCKET:-true}"

require_value MONGO_BACKUP_USER
require_value MONGO_BACKUP_PASS
require_value MINIO_ACCESS_KEY
require_value MINIO_SECRET_KEY
require_value BACKUP_AGE_RECIPIENT

[[ "$BACKUP_RETENTION_DAYS" =~ ^[1-9][0-9]*$ ]] || fail "BACKUP_RETENTION_DAYS must be a positive integer"

lock_dir="/tmp/timsum-mongodb-backup.lock"
if ! mkdir "$lock_dir" 2>/dev/null; then
  fail "another backup appears to be running"
fi

work_dir="$(mktemp -d /tmp/timsum-backup.XXXXXX)"
cleanup() {
  rm -rf "$work_dir" "$lock_dir"
}
trap cleanup EXIT INT TERM

archive_name="timsumv3_$(date +%Y%m%dT%H%M%S%z).archive.gz"
archive_path="$work_dir/$archive_name"
encrypted_path="$archive_path.age"
checksum_path="$encrypted_path.sha256"
mongo_config="$work_dir/mongodump.yml"
object_prefix="daily/$(date +%Y/%m)"

mongo_uri="mongodb://${MONGO_BACKUP_HOST}:${MONGO_BACKUP_PORT}/?authSource=${MONGO_BACKUP_AUTH_DB}"
{
  printf "uri: '%s'\n" "$(yaml_single_quote "$mongo_uri")"
  printf "password: '%s'\n" "$(yaml_single_quote "$MONGO_BACKUP_PASS")"
} > "$mongo_config"

dump_args=(
  --config "$mongo_config"
  --username "$MONGO_BACKUP_USER"
  --authenticationDatabase "$MONGO_BACKUP_AUTH_DB"
  --archive="$archive_path"
  --gzip
)

if [[ "${MONGO_BACKUP_USE_OPLOG,,}" == "true" ]]; then
  dump_args+=(--oplog)
  log "Starting full MongoDB replica-set dump with oplog"
else
  dump_args+=(--db "$MONGO_DB_NAME")
  log "Starting MongoDB dump for database $MONGO_DB_NAME"
fi

mongodump "${dump_args[@]}"
[[ -s "$archive_path" ]] || fail "mongodump produced an empty archive"

log "Encrypting archive with age"
age --recipient "$BACKUP_AGE_RECIPIENT" --output "$encrypted_path" "$archive_path"
[[ -s "$encrypted_path" ]] || fail "age produced an empty encrypted archive"
rm -f "$archive_path" "$mongo_config"

(
  cd "$work_dir"
  sha256sum "$(basename "$encrypted_path")" > "$(basename "$checksum_path")"
)

mc alias set timsum-backup "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"

if [[ "${BACKUP_CONFIGURE_BUCKET,,}" == "true" ]]; then
  if ! mc stat "timsum-backup/$BACKUP_BUCKET" >/dev/null 2>&1; then
    log "Creating locked MinIO bucket $BACKUP_BUCKET"
    mc mb --with-lock "timsum-backup/$BACKUP_BUCKET"
  fi
  mc version enable "timsum-backup/$BACKUP_BUCKET" >/dev/null

  config_marker="retention-${BACKUP_RETENTION_DAYS}-v1"
  if ! mc stat "timsum-backup/$BACKUP_BUCKET/.config/$config_marker" >/dev/null 2>&1; then
    log "Configuring ${BACKUP_RETENTION_DAYS}-day WORM retention and lifecycle"
    mc retention set --default GOVERNANCE "${BACKUP_RETENTION_DAYS}d" \
      "timsum-backup/$BACKUP_BUCKET"
    mc ilm rule add \
      --purge-all-object-versions-days "$((BACKUP_RETENTION_DAYS + 1))" \
      --purge-all-object-versions-delete-marker \
      "timsum-backup/$BACKUP_BUCKET"
    printf '%s\n' "configured=$(date --iso-8601=seconds)" > "$work_dir/$config_marker"
    mc cp "$work_dir/$config_marker" \
      "timsum-backup/$BACKUP_BUCKET/.config/$config_marker" >/dev/null
  fi
else
  mc stat "timsum-backup/$BACKUP_BUCKET" >/dev/null 2>&1 \
    || fail "backup bucket does not exist and BACKUP_CONFIGURE_BUCKET=false"
fi

remote_base="timsum-backup/$BACKUP_BUCKET/$object_prefix"
log "Uploading encrypted backup to $BACKUP_BUCKET/$object_prefix"
mc cp "$encrypted_path" "$remote_base/$(basename "$encrypted_path")"
mc cp "$checksum_path" "$remote_base/$(basename "$checksum_path")"

# Verify the actual remote bytes, not only the response from the PUT request.
verify_dir="$work_dir/verify"
mkdir -p "$verify_dir"
mc cp "$remote_base/$(basename "$encrypted_path")" "$verify_dir/" >/dev/null
mc cp "$remote_base/$(basename "$checksum_path")" "$verify_dir/" >/dev/null
(
  cd "$verify_dir"
  sha256sum --check "$(basename "$checksum_path")"
)

log "Backup completed and verified: $BACKUP_BUCKET/$object_prefix/$(basename "$encrypted_path")"
