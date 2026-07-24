"""Fail-fast validation for production security and lifecycle configuration."""

from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlsplit

from app.services.security import get_public_frontend_url


class RuntimeConfigurationError(RuntimeError):
    """Raised before external services start when production config is unsafe."""


_PLACEHOLDER_MARKERS = (
    "change_me",
    "changeme",
    "replace_me",
    "your_",
    "example",
    "timsum@admin",
    "timsum@superadmin",
)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _require_secret(name: str, minimum_length: int = 24) -> None:
    value = os.getenv(name, "")
    normalized = value.strip().casefold()
    if len(value) < minimum_length or any(marker in normalized for marker in _PLACEHOLDER_MARKERS):
        raise RuntimeConfigurationError(
            f"{name} must be a rotated, non-placeholder secret of at least "
            f"{minimum_length} characters"
        )


def _require_positive_int(name: str) -> None:
    raw = os.getenv(name, "")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigurationError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise RuntimeConfigurationError(f"{name} must be a positive integer")


def validate_runtime_configuration() -> None:
    """Validate locked production invariants; development remains permissive."""
    app_env = os.getenv("APP_ENV", "development").strip().lower()
    if app_env not in {"development", "test", "staging", "production"}:
        raise RuntimeConfigurationError("APP_ENV must be development, test, staging, or production")
    if app_env != "production":
        return

    public_url = get_public_frontend_url()
    parsed_public_url = urlsplit(public_url)
    if parsed_public_url.scheme != "https":
        raise RuntimeConfigurationError("PUBLIC_FRONTEND_URL must use HTTPS in production")

    public_host = os.getenv("PUBLIC_HOST", "").strip().lower().rstrip(".")
    if not public_host or not re.fullmatch(r"[a-z0-9.-]+", public_host):
        raise RuntimeConfigurationError("PUBLIC_HOST must be a hostname without scheme or port")
    if public_host != (parsed_public_url.hostname or "").lower().rstrip("."):
        raise RuntimeConfigurationError("PUBLIC_HOST must match PUBLIC_FRONTEND_URL")

    tls_mode = os.getenv("TLS_MODE", "").strip().lower()
    if tls_mode not in {"internal", "acme"}:
        raise RuntimeConfigurationError("TLS_MODE must be internal or acme")

    summary_mode = os.getenv("SUMMARY_PIPELINE_MODE", "async").strip().lower()
    if summary_mode not in {"async", "inline"}:
        raise RuntimeConfigurationError("SUMMARY_PIPELINE_MODE must be async or inline")

    _require_secret("JWT_SECRET_KEY", 32)
    _require_secret("MONGO_PASS")
    _require_secret("REDIS_PASSWORD")
    _require_secret("MINIO_PASS")
    _require_secret("CONSENT_AUDIT_KEY", 32)

    for removed_name in ("ADMIN_PASS", "SUPERADMIN_PASS"):
        if os.getenv(removed_name, "").strip():
            raise RuntimeConfigurationError(
                f"{removed_name} is no longer supported; use the one-time admin CLI"
            )

    trusted_cidrs = os.getenv("TRUSTED_PROXY_CIDRS", "").split(",")
    parsed_cidrs = []
    for raw_cidr in trusted_cidrs:
        raw_cidr = raw_cidr.strip()
        if not raw_cidr:
            continue
        try:
            parsed_cidrs.append(ipaddress.ip_network(raw_cidr, strict=False))
        except ValueError as exc:
            raise RuntimeConfigurationError(
                f"TRUSTED_PROXY_CIDRS contains invalid CIDR: {raw_cidr}"
            ) from exc
    if not parsed_cidrs:
        raise RuntimeConfigurationError("TRUSTED_PROXY_CIDRS must contain the Caddy proxy CIDR")

    configured_origins = {
        origin.strip().rstrip("/")
        for origin in os.getenv("ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    }
    if public_url.rstrip("/") not in configured_origins:
        raise RuntimeConfigurationError("ALLOWED_ORIGINS must include PUBLIC_FRONTEND_URL")
    if any(not origin.startswith("https://") for origin in configured_origins):
        raise RuntimeConfigurationError("Production ALLOWED_ORIGINS may contain HTTPS origins only")
    if os.getenv("ALLOWED_ORIGIN_REGEX", "").strip():
        raise RuntimeConfigurationError("ALLOWED_ORIGIN_REGEX must be empty in production")

    for retention_name in (
        "RAW_AUDIO_FAILSAFE_HOURS",
        "TRANSCRIPT_ARTIFACT_RETENTION_DAYS",
        "SPEAKER_CLIP_RETENTION_DAYS",
        "SESSION_RETENTION_DAYS",
        "JOB_RETENTION_DAYS",
        "SUMMARY_STATE_RETENTION_DAYS",
        "ACTIVITY_LOG_RETENTION_DAYS",
        "CONSENT_AUDIT_RETENTION_DAYS",
        "EMAIL_OUTBOX_RETENTION_DAYS",
        "DELETION_GRACE_HOURS",
        "DELETION_MAX_RETRIES",
        "JOB_INITIALIZATION_TIMEOUT_MINUTES",
        "MAINTENANCE_RECONCILE_BATCH_SIZE",
    ):
        _require_positive_int(retention_name)

    if _env_bool("PII_CUTOVER_COMPLETE"):
        if not _env_bool("PII_ENCRYPTION_ENABLED"):
            raise RuntimeConfigurationError("PII encryption cannot be disabled after cutover")
        if _env_bool("PII_ALLOW_LEGACY_PLAINTEXT", True):
            raise RuntimeConfigurationError("PII_ALLOW_LEGACY_PLAINTEXT must be false after cutover")
        endpoint = os.getenv("BACKUP_S3_ENDPOINT", "").strip()
        if not endpoint.startswith("https://"):
            raise RuntimeConfigurationError("PII cutover requires an HTTPS off-host backup endpoint")
        for backup_name in (
            "MONGO_BACKUP_USER",
            "MONGO_BACKUP_PASS",
            "BACKUP_MINIO_ACCESS_KEY",
            "BACKUP_MINIO_SECRET_KEY",
            "BACKUP_AGE_RECIPIENT",
        ):
            if not os.getenv(backup_name, "").strip():
                raise RuntimeConfigurationError(f"PII cutover requires {backup_name}")
