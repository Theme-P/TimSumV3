#!/usr/bin/env bash
# Build-first production deployment with a retained application-image rollback tag.

set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$project_dir"

compose=(docker compose -f docker-compose.yml)
state_dir="${DEPLOY_STATE_DIR:-$project_dir/.deploy-state}"
mkdir -p "$state_dir"

read_env() {
  local name="$1"
  local value="${!name:-}"
  if [[ -z "$value" && -f .env ]]; then
    value="$(sed -n "s/^${name}=//p" .env | tail -n 1 | tr -d '\r')"
  fi
  printf '%s' "$value"
}

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -f .env ]] || fail ".env is required"
env_mode="$(stat -c '%a' .env)"
[[ "$env_mode" == "600" ]] || fail ".env must have mode 0600 (current: $env_mode)"

app_env="$(read_env APP_ENV)"
public_url="$(read_env PUBLIC_FRONTEND_URL)"
public_host="$(read_env PUBLIC_HOST)"
tls_mode="$(read_env TLS_MODE)"
[[ "$app_env" == "production" ]] || fail "APP_ENV must be production"
[[ "$public_url" == https://* ]] || fail "PUBLIC_FRONTEND_URL must use https://"
[[ -n "$public_host" ]] || fail "PUBLIC_HOST is required"
[[ "$tls_mode" == "internal" || "$tls_mode" == "acme" ]] || fail "TLS_MODE must be internal or acme"

for secret_name in JWT_SECRET_KEY MONGO_PASS REDIS_PASSWORD MINIO_PASS CONSENT_AUDIT_KEY; do
  secret_value="$(read_env "$secret_name")"
  [[ ${#secret_value} -ge 24 ]] || fail "$secret_name must contain at least 24 characters"
  [[ "$secret_value" != *CHANGE_ME* && "$secret_value" != *TimSum@* ]] \
    || fail "$secret_name still contains a published/default value"
done

# Production base images must be immutable. Development may use the tagged
# defaults in Compose, but the deploy path requires explicit digest references.
for image_name in MONGO_IMAGE REDIS_IMAGE MINIO_IMAGE CADDY_IMAGE BACKEND_BASE_IMAGE NODE_IMAGE NGINX_IMAGE MINIO_MC_IMAGE MONGO_TOOLS_IMAGE; do
  image_ref="$(read_env "$image_name")"
  [[ "$image_ref" =~ @sha256:[0-9a-f]{64}$ ]] \
    || fail "$image_name must be explicitly pinned by sha256 digest"
done

whisperx_commit="$(read_env WHISPERX_COMMIT)"
[[ "$whisperx_commit" =~ ^[0-9a-f]{40}$ && "$whisperx_commit" != "0000000000000000000000000000000000000000" ]] \
  || fail "WHISPERX_COMMIT must be the audited 40-hex commit from staging"

python3 -m compileall -q backend
"${compose[@]}" config --quiet

release_tag="${RELEASE_TAG:-$(git rev-parse --short=12 HEAD)-$(date +%Y%m%d%H%M%S)}"
previous_tag=""
if [[ -f "$state_dir/current-release" ]]; then
  previous_tag="$(<"$state_dir/current-release")"
fi

if "${compose[@]}" ps --status running --format json > "$state_dir/previous-containers.json" 2>/dev/null; then
  :
else
  : > "$state_dir/previous-containers.json"
fi

printf 'Building application images for release %s\n' "$release_tag"
export TIMSUM_IMAGE_TAG="$release_tag"
"${compose[@]}" build backend frontend

printf 'Cutting over without stopping healthy services first\n'
if "${compose[@]}" up -d --wait --remove-orphans; then
  printf '%s\n' "$release_tag" > "$state_dir/current-release"
  printf 'Deployment complete: %s\n' "$release_tag"
  "${compose[@]}" ps
  printf 'Application: %s\n' "$public_url"
  printf 'MinIO console: ssh -L 9001:127.0.0.1:9001 <server>, then open http://127.0.0.1:9001\n'
  exit 0
fi

printf 'Cutover failed.\n' >&2
if [[ -n "$previous_tag" ]]; then
  printf 'Rolling application images back to %s\n' "$previous_tag" >&2
  export TIMSUM_IMAGE_TAG="$previous_tag"
  "${compose[@]}" up -d --wait --remove-orphans
  printf 'Rollback completed. Investigate the failed release before retrying.\n' >&2
else
  printf 'No previous release tag is recorded; automatic rollback is unavailable.\n' >&2
fi
exit 1
