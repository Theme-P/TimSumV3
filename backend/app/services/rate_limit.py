"""Small Redis-backed rate limiter shared by API routers.

The limiter uses an atomic Redis script and fails closed when Redis is not
available.  Identifiers are SHA-256 fingerprints so email addresses, reset
tokens, and user IDs are not stored in Redis keys as plaintext.
"""

from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Any, Iterable

from fastapi import HTTPException, Request

from app.services.security import get_client_ip


_INCREMENT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""


@dataclass(frozen=True)
class RateLimitRule:
    namespace: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.namespace or self.limit < 1 or self.window_seconds < 1:
            raise ValueError("Invalid rate-limit rule")


LOGIN_IP = RateLimitRule("auth:login:ip", 5, 60)
LOGIN_EMAIL = RateLimitRule("auth:login:email", 10, 15 * 60)
REGISTER_IP = RateLimitRule("auth:register:ip", 3, 60 * 60)
FORGOT_IP = RateLimitRule("auth:forgot:ip", 3, 15 * 60)
FORGOT_EMAIL = RateLimitRule("auth:forgot:email", 5, 15 * 60)
RESET_IP = RateLimitRule("auth:reset:ip", 5, 15 * 60)
RESET_TOKEN = RateLimitRule("auth:reset:token", 5, 15 * 60)

# Public integration hooks for routers outside this module.
UPLOAD_USER = RateLimitRule("api:upload:user", 10, 60)
VOICE_USER = RateLimitRule("api:voice:user", 5, 60 * 60)
EMAIL_USER = RateLimitRule("api:email:user", 5, 60 * 60)


class RedisRateLimiter:
    """Evaluate rate-limit rules against Redis."""

    def __init__(self, redis_client: Any | None = None, redis_url: str | None = None) -> None:
        self._redis = redis_client
        self._redis_url = redis_url

    def _get_client(self) -> Any:
        if self._redis is None:
            try:
                import redis

                url = (
                    self._redis_url
                    or os.getenv("RATE_LIMIT_REDIS_URL")
                    or os.getenv("REDIS_URL", "redis://redis:6379/0")
                )
                self._redis = redis.from_url(
                    url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
            except Exception as exc:
                raise RuntimeError("Rate-limit backend is unavailable") from exc
        return self._redis

    @staticmethod
    def _key(rule: RateLimitRule, identifier: str) -> str:
        # Callers normalize case-insensitive identifiers (such as email)
        # before invoking the limiter.  Tokens remain case-sensitive here.
        normalized = str(identifier).strip()
        key_secret = (
            os.getenv("RATE_LIMIT_KEY_SECRET")
            or os.getenv("JWT_SECRET_KEY")
            or "timsumv3-development-rate-limit-key"
        ).encode("utf-8")
        fingerprint = hmac.new(
            key_secret,
            f"{rule.namespace}:{normalized}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return f"timsum:rate:{rule.namespace}:{fingerprint}"

    def hit(self, rule: RateLimitRule, identifier: str) -> tuple[bool, int]:
        if not str(identifier).strip():
            identifier = "unknown"
        try:
            result = self._get_client().eval(
                _INCREMENT_SCRIPT,
                1,
                self._key(rule, identifier),
                rule.window_seconds,
            )
            count, ttl = int(result[0]), max(1, int(result[1]))
        except Exception as exc:
            raise RuntimeError("Rate-limit backend is unavailable") from exc
        return count <= rule.limit, ttl


_default_limiter = RedisRateLimiter()


def enforce_rate_limit(
    request: Request,
    rule: RateLimitRule,
    identifier: str | None = None,
    *,
    limiter: RedisRateLimiter | None = None,
) -> None:
    """Enforce one rule and return HTTP 429 with Retry-After when exceeded.

    If no identifier is supplied, the validated client IP is used.  Callers
    limiting per user should pass ``str(user.id)`` explicitly.
    """
    selected_identifier = identifier if identifier is not None else get_client_ip(request)
    try:
        allowed, retry_after = (limiter or _default_limiter).hit(
            rule,
            selected_identifier,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=503,
            detail="Rate-limit service is unavailable",
            headers={"Retry-After": "5"},
        ) from exc

    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(retry_after)},
        )


def enforce_rate_limits(
    request: Request,
    rules: Iterable[tuple[RateLimitRule, str | None]],
    *,
    limiter: RedisRateLimiter | None = None,
) -> None:
    """Enforce multiple rules in order, such as per-IP plus per-account."""
    for rule, identifier in rules:
        enforce_rate_limit(request, rule, identifier, limiter=limiter)
