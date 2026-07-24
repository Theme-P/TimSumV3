"""Password hashing with Argon2id and one-way legacy PBKDF2 migration."""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import Any


LEGACY_PBKDF2_ITERATIONS = 100_000


@dataclass(frozen=True)
class PasswordVerification:
    valid: bool
    upgraded_hash: str | None = None
    upgraded_salt: str | None = None

    @property
    def needs_rehash(self) -> bool:
        return self.valid and self.upgraded_hash is not None


class PasswordManager:
    """Hash new passwords with Argon2id and verify legacy PBKDF2 hashes.

    The Argon2 profile follows the OWASP minimum Argon2id profile: 19 MiB of
    memory, two iterations, and one lane.  Successful verification of either a
    legacy PBKDF2 hash or an outdated Argon2 profile returns a replacement hash
    that the caller should persist immediately.
    """

    def __init__(self, hasher: Any | None = None) -> None:
        if hasher is None:
            try:
                from argon2 import PasswordHasher
                from argon2.low_level import Type
            except ImportError as exc:
                raise RuntimeError(
                    "argon2-cffi is required for password hashing"
                ) from exc

            hasher = PasswordHasher(
                time_cost=2,
                memory_cost=19 * 1024,
                parallelism=1,
                hash_len=32,
                salt_len=16,
                type=Type.ID,
            )
        self._hasher = hasher

    def hash(self, password: str) -> tuple[str, None]:
        """Return an encoded Argon2id hash and no separately stored salt."""
        if not isinstance(password, str) or not password:
            raise ValueError("Password must not be empty")
        return self._hasher.hash(password), None

    def verify(
        self,
        password: str,
        stored_hash: str,
        legacy_salt: str | None = None,
    ) -> PasswordVerification:
        """Verify a stored password and describe a safe in-place upgrade."""
        if not password or not stored_hash:
            return PasswordVerification(valid=False)

        if stored_hash.startswith("$argon2id$"):
            try:
                valid = bool(self._hasher.verify(stored_hash, password))
            except Exception as exc:
                if self._is_argon2_verification_error(exc):
                    return PasswordVerification(valid=False)
                raise

            if not valid:
                return PasswordVerification(valid=False)
            if self._hasher.check_needs_rehash(stored_hash):
                upgraded_hash, upgraded_salt = self.hash(password)
                return PasswordVerification(
                    valid=True,
                    upgraded_hash=upgraded_hash,
                    upgraded_salt=upgraded_salt,
                )
            return PasswordVerification(valid=True)

        if not legacy_salt:
            return PasswordVerification(valid=False)

        try:
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                password.encode("utf-8"),
                legacy_salt.encode("utf-8"),
                LEGACY_PBKDF2_ITERATIONS,
            ).hex()
        except (AttributeError, UnicodeError):
            return PasswordVerification(valid=False)

        if not secrets.compare_digest(candidate, stored_hash):
            return PasswordVerification(valid=False)

        upgraded_hash, upgraded_salt = self.hash(password)
        return PasswordVerification(
            valid=True,
            upgraded_hash=upgraded_hash,
            upgraded_salt=upgraded_salt,
        )

    @staticmethod
    def _is_argon2_verification_error(exc: Exception) -> bool:
        try:
            from argon2 import exceptions
        except ImportError:
            return False
        error_types = [exceptions.VerificationError]
        invalid_hash_error = getattr(exceptions, "InvalidHashError", None)
        if invalid_hash_error is not None:
            error_types.append(invalid_hash_error)
        return isinstance(exc, tuple(error_types))
