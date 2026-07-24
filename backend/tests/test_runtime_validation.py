import os
from unittest.mock import patch

import pytest

from app.core.runtime_validation import RuntimeConfigurationError, validate_runtime_configuration


PRODUCTION_ENV = {
    "APP_ENV": "production",
    "PUBLIC_FRONTEND_URL": "https://timsum.example.org",
    "PUBLIC_HOST": "timsum.example.org",
    "TLS_MODE": "acme",
    "JWT_SECRET_KEY": "j" * 32,
    "MONGO_PASS": "m" * 24,
    "REDIS_PASSWORD": "r" * 24,
    "MINIO_PASS": "s" * 24,
    "CONSENT_AUDIT_KEY": "c" * 32,
    "TRUSTED_PROXY_CIDRS": "172.30.0.0/24",
    "ALLOWED_ORIGINS": "https://timsum.example.org",
    "ALLOWED_ORIGIN_REGEX": "",
    "RAW_AUDIO_FAILSAFE_HOURS": "48",
    "TRANSCRIPT_ARTIFACT_RETENTION_DAYS": "7",
    "SPEAKER_CLIP_RETENTION_DAYS": "30",
    "SESSION_RETENTION_DAYS": "365",
    "JOB_RETENTION_DAYS": "30",
    "SUMMARY_STATE_RETENTION_DAYS": "30",
    "ACTIVITY_LOG_RETENTION_DAYS": "90",
    "CONSENT_AUDIT_RETENTION_DAYS": "365",
    "EMAIL_OUTBOX_RETENTION_DAYS": "30",
    "DELETION_GRACE_HOURS": "24",
    "DELETION_MAX_RETRIES": "8",
    "JOB_INITIALIZATION_TIMEOUT_MINUTES": "15",
    "MAINTENANCE_RECONCILE_BATCH_SIZE": "100",
}


def test_development_is_permissive():
    with patch.dict(os.environ, {"APP_ENV": "development"}, clear=True):
        validate_runtime_configuration()


def test_production_configuration_passes_locked_defaults():
    with patch.dict(os.environ, PRODUCTION_ENV, clear=True):
        validate_runtime_configuration()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("PUBLIC_FRONTEND_URL", "http://timsum.example.org"),
        ("JWT_SECRET_KEY", "CHANGE_ME"),
        ("ALLOWED_ORIGIN_REGEX", r"^https?://.*$"),
        ("ADMIN_PASS", "published-bootstrap-password"),
        ("SUMMARY_PIPELINE_MODE", "unknown"),
    ],
)
def test_production_rejects_unsafe_values(name, value):
    env = {**PRODUCTION_ENV, name: value}
    with patch.dict(os.environ, env, clear=True), pytest.raises((RuntimeConfigurationError, RuntimeError)):
        validate_runtime_configuration()


def test_pii_cutover_requires_encryption_and_off_host_backup():
    env = {**PRODUCTION_ENV, "PII_CUTOVER_COMPLETE": "true"}
    with patch.dict(os.environ, env, clear=True), pytest.raises(RuntimeConfigurationError):
        validate_runtime_configuration()
