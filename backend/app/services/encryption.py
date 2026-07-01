"""Application-level encryption helpers for personally identifiable data.

The database stores encrypted values as small BSON documents while the rest of
the application continues to work with ordinary strings.  Email equality
lookups use a keyed blind index; ciphertext is randomized and is never queried
directly.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import dataclass
from typing import Any, Mapping, MutableMapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


PII_USER_FIELDS = (
    "email",
    "username",
    "first_name",
    "last_name",
    "phone",
    "organization",
)


class PIIEncryptionError(RuntimeError):
    """Raised when PII configuration or ciphertext is invalid."""


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _decode_key(value: str, name: str) -> bytes:
    try:
        padded = value.strip() + "=" * (-len(value.strip()) % 4)
        key = base64.urlsafe_b64decode(padded.encode("ascii"))
    except Exception as exc:
        raise PIIEncryptionError(f"{name} must be URL-safe base64") from exc
    if len(key) != 32:
        raise PIIEncryptionError(f"{name} must decode to exactly 32 bytes")
    return key


@dataclass(frozen=True)
class EncryptedValue:
    ciphertext: str
    nonce: str
    version: int
    alg: str = "AES-256-GCM"

    def as_dict(self) -> dict[str, Any]:
        return {
            "alg": self.alg,
            "ciphertext": self.ciphertext,
            "nonce": self.nonce,
            "version": self.version,
        }


class PIIEncryptor:
    """Encrypt/decrypt PII fields and create deterministic blind indexes."""

    def __init__(
        self,
        keys: Mapping[int, bytes] | None = None,
        active_version: int = 1,
        blind_index_key: bytes | None = None,
        enabled: bool = False,
        allow_legacy_plaintext: bool = True,
    ) -> None:
        self.enabled = enabled
        self.keys = dict(keys or {})
        self.active_version = active_version
        self.blind_index_key = blind_index_key
        self.allow_legacy_plaintext = allow_legacy_plaintext

        if not self.enabled:
            return
        if self.active_version not in self.keys:
            raise PIIEncryptionError(
                f"Active PII key version {self.active_version} is not configured"
            )
        for version, key in self.keys.items():
            if not isinstance(version, int) or version < 1:
                raise PIIEncryptionError("PII key versions must be positive integers")
            if len(key) != 32:
                raise PIIEncryptionError(f"PII key version {version} must be 32 bytes")
        if self.blind_index_key is None or len(self.blind_index_key) != 32:
            raise PIIEncryptionError("PII_BLIND_INDEX_KEY must decode to 32 bytes")

    @classmethod
    def from_env(cls, require_enabled: bool = False) -> "PIIEncryptor":
        enabled = _env_bool("PII_ENCRYPTION_ENABLED", False)
        if require_enabled and not enabled:
            raise PIIEncryptionError(
                "PII_ENCRYPTION_ENABLED must be true for this operation"
            )
        if not enabled:
            return cls(enabled=False)

        active_version = int(os.getenv("PII_ACTIVE_KEY_VERSION", "1"))
        raw_keys = os.getenv("PII_ENCRYPTION_KEYS", "").strip()
        legacy_key = os.getenv("PII_ENCRYPTION_KEY", "").strip()
        keys: dict[int, bytes] = {}

        if raw_keys:
            try:
                parsed = json.loads(raw_keys)
            except json.JSONDecodeError as exc:
                raise PIIEncryptionError("PII_ENCRYPTION_KEYS must be valid JSON") from exc
            if not isinstance(parsed, dict):
                raise PIIEncryptionError("PII_ENCRYPTION_KEYS must be a JSON object")
            for version, value in parsed.items():
                try:
                    version_number = int(version)
                except (TypeError, ValueError) as exc:
                    raise PIIEncryptionError("Invalid PII key version") from exc
                keys[version_number] = _decode_key(
                    str(value), f"PII_ENCRYPTION_KEYS[{version}]"
                )
        elif legacy_key:
            keys[active_version] = _decode_key(legacy_key, "PII_ENCRYPTION_KEY")

        blind_key_raw = os.getenv("PII_BLIND_INDEX_KEY", "").strip()
        blind_key = _decode_key(blind_key_raw, "PII_BLIND_INDEX_KEY") if blind_key_raw else None

        return cls(
            keys=keys,
            active_version=active_version,
            blind_index_key=blind_key,
            enabled=True,
            allow_legacy_plaintext=_env_bool("PII_ALLOW_LEGACY_PLAINTEXT", True),
        )

    @staticmethod
    def normalize_email(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def is_encrypted(value: Any) -> bool:
        return (
            isinstance(value, Mapping)
            and value.get("alg") == "AES-256-GCM"
            and isinstance(value.get("ciphertext"), str)
            and isinstance(value.get("nonce"), str)
            and isinstance(value.get("version"), int)
        )

    def blind_index(self, value: str) -> str:
        if not self.enabled or self.blind_index_key is None:
            raise PIIEncryptionError("PII encryption is not enabled")
        normalized = self.normalize_email(value)
        return hmac.new(
            self.blind_index_key,
            normalized.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def encrypt(self, value: str | None, *, context: str) -> str | dict[str, Any] | None:
        if value is None or not self.enabled:
            return value
        if not isinstance(value, str):
            raise PIIEncryptionError(f"PII value for {context} must be a string")

        nonce = os.urandom(12)
        aes = AESGCM(self.keys[self.active_version])
        ciphertext = aes.encrypt(nonce, value.encode("utf-8"), context.encode("utf-8"))
        return EncryptedValue(
            ciphertext=base64.urlsafe_b64encode(ciphertext).decode("ascii"),
            nonce=base64.urlsafe_b64encode(nonce).decode("ascii"),
            version=self.active_version,
        ).as_dict()

    def decrypt(self, value: Any, *, context: str) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            if self.enabled and not self.allow_legacy_plaintext:
                raise PIIEncryptionError(f"Plaintext PII is not allowed for {context}")
            return value
        if not self.is_encrypted(value):
            raise PIIEncryptionError(f"Invalid encrypted PII value for {context}")

        version = value["version"]
        key = self.keys.get(version)
        if key is None:
            raise PIIEncryptionError(f"PII key version {version} is not available")
        try:
            nonce = base64.urlsafe_b64decode(value["nonce"].encode("ascii"))
            ciphertext = base64.urlsafe_b64decode(value["ciphertext"].encode("ascii"))
            plaintext = AESGCM(key).decrypt(
                nonce,
                ciphertext,
                context.encode("utf-8"),
            )
        except (InvalidTag, ValueError, TypeError) as exc:
            raise PIIEncryptionError(f"Could not decrypt or authenticate {context}") from exc
        return plaintext.decode("utf-8")

    @staticmethod
    def _context(user_id: Any, field: str) -> str:
        return f"user:{user_id}:{field}"

    def decrypt_user_document(self, document: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(document)
        user_id = result.get("_id")
        for field in PII_USER_FIELDS:
            if field in result:
                result[field] = self.decrypt(
                    result[field], context=self._context(user_id, field)
                )
        return result

    def encrypt_user_fields(
        self,
        user_id: Any,
        fields: Mapping[str, Any],
        *,
        reencrypt: bool = False,
    ) -> dict[str, Any]:
        result = dict(fields)
        if not self.enabled:
            if isinstance(result.get("email"), str):
                result["email"] = self.normalize_email(result["email"])
            return result

        for field in PII_USER_FIELDS:
            if field not in result or result[field] is None:
                continue
            value = result[field]
            context = self._context(user_id, field)
            if self.is_encrypted(value):
                if not reencrypt and value.get("version") == self.active_version:
                    continue
                value = self.decrypt(value, context=context)
            if field == "email":
                value = self.normalize_email(value)
                result["email_bidx"] = self.blind_index(value)
            result[field] = self.encrypt(value, context=context)
        return result

    def encrypt_user_document(
        self,
        document: Mapping[str, Any],
        *,
        reencrypt: bool = False,
    ) -> dict[str, Any]:
        if "_id" not in document:
            raise PIIEncryptionError("User document must have an _id before encryption")
        return self.encrypt_user_fields(
            document["_id"], document, reencrypt=reencrypt
        )

    def user_document_needs_migration(self, document: Mapping[str, Any]) -> bool:
        if not self.enabled:
            return False
        for field in PII_USER_FIELDS:
            value = document.get(field)
            if value is None:
                continue
            if not self.is_encrypted(value) or value.get("version") != self.active_version:
                return True
        email = document.get("email")
        return email is not None and not isinstance(document.get("email_bidx"), str)

    def encrypted_update_fields(
        self,
        document: Mapping[str, Any],
        *,
        reencrypt: bool = False,
    ) -> MutableMapping[str, Any]:
        encrypted = self.encrypt_user_document(document, reencrypt=reencrypt)
        fields: dict[str, Any] = {
            field: encrypted[field]
            for field in PII_USER_FIELDS
            if field in encrypted
        }
        if "email_bidx" in encrypted:
            fields["email_bidx"] = encrypted["email_bidx"]
        return fields
