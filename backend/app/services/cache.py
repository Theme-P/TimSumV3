"""
Redis caching layer for frequently accessed data.
Uses Redis DB 1 (DB 0 is reserved for Celery broker).
"""
import os
import json
import logging
from typing import Optional

import redis

logger = logging.getLogger(__name__)

# Parse Redis URL and switch to DB 1 for cache
_broker_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
CACHE_REDIS_URL = _broker_url.rsplit("/", 1)[0] + "/1"

# Default TTL
DEFAULT_TTL = 300  # 5 minutes


class CacheService:
    """Thin Redis caching wrapper with JSON serialization."""

    def __init__(self, url: str = CACHE_REDIS_URL):
        self._redis: Optional[redis.Redis] = None
        self._url = url
        try:
            self._redis = redis.from_url(url, decode_responses=True)
            self._redis.ping()
            logger.info("Redis cache connected (%s)", url)
        except Exception as e:
            logger.warning("Redis cache unavailable — running without cache: %s", e)
            self._redis = None

    # ── Low-level helpers ──

    def get(self, key: str) -> Optional[dict]:
        if not self._redis:
            return None
        try:
            raw = self._redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    def set(self, key: str, value: dict, ttl: int = DEFAULT_TTL) -> None:
        if not self._redis:
            return
        try:
            self._redis.setex(key, ttl, json.dumps(value, default=str))
        except Exception:
            pass

    def delete(self, key: str) -> None:
        if not self._redis:
            return
        try:
            self._redis.delete(key)
        except Exception:
            pass

    def delete_pattern(self, pattern: str) -> None:
        if not self._redis:
            return
        try:
            keys = self._redis.keys(pattern)
            if keys:
                self._redis.delete(*keys)
        except Exception:
            pass

    # ── Domain-specific keys ──

    @staticmethod
    def _user_package_key(user_id: str) -> str:
        return f"user_pkg:{user_id}"

    @staticmethod
    def _package_key(package_id: str) -> str:
        return f"pkg:{package_id}"

    @staticmethod
    def _all_packages_key() -> str:
        return "pkg:all"

    # ── Domain helpers ──

    def get_user_package(self, user_id: str) -> Optional[dict]:
        return self.get(self._user_package_key(user_id))

    def set_user_package(self, user_id: str, data: dict, ttl: int = DEFAULT_TTL) -> None:
        self.set(self._user_package_key(user_id), data, ttl)

    def invalidate_user_package(self, user_id: str) -> None:
        self.delete(self._user_package_key(user_id))

    def get_all_packages(self) -> Optional[list]:
        return self.get(self._all_packages_key())

    def set_all_packages(self, data: list, ttl: int = DEFAULT_TTL) -> None:
        self.set(self._all_packages_key(), data, ttl)

    def invalidate_packages(self) -> None:
        self.delete_pattern("pkg:*")
