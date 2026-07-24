"""Wall-clock budget controls for the async summary pipeline."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional


logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Integer env %s=%r below %s; using %s", name, raw, minimum, minimum)
        return minimum
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    logger.warning("Invalid boolean env %s=%r; using %s", name, raw, default)
    return default


SUMMARY_TOTAL_TIMEOUT_SECONDS = _env_int("SUMMARY_TOTAL_TIMEOUT_SECONDS", 1200, minimum=60)
SUMMARY_REQUEST_TIMEOUT_SECONDS = _env_int(
    "SUMMARY_REQUEST_TIMEOUT_SECONDS",
    _env_int("SUMMARY_LLM_TIMEOUT_SECONDS", 300),
)
SUMMARY_MIN_REMAINING_TIME_SECONDS = _env_int("SUMMARY_MIN_REMAINING_TIME_SECONDS", 30)
SUMMARY_FINAL_RENDER_RESERVED_SECONDS = min(
    _env_int("SUMMARY_FINAL_RENDER_RESERVED_SECONDS", 120, minimum=0),
    max(0, SUMMARY_TOTAL_TIMEOUT_SECONDS - SUMMARY_MIN_REMAINING_TIME_SECONDS),
)
SUMMARY_ALLOW_PARTIAL_RESULT = _env_bool("SUMMARY_ALLOW_PARTIAL_RESULT", True)
SUMMARY_USE_DETERMINISTIC_FINAL_FALLBACK = _env_bool(
    "SUMMARY_USE_DETERMINISTIC_FINAL_FALLBACK",
    True,
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class SummaryBudgetExhausted(RuntimeError):
    """Raised when another model request must not be started."""

    def __init__(self, reason: str, *, phase: str, remaining_seconds: float):
        super().__init__(reason)
        self.reason = reason
        self.phase = phase
        self.remaining_seconds = max(0.0, float(remaining_seconds))


@dataclass(frozen=True)
class SummaryBudget:
    started_at: datetime
    total_seconds: int = SUMMARY_TOTAL_TIMEOUT_SECONDS
    request_seconds: int = SUMMARY_REQUEST_TIMEOUT_SECONDS
    min_remaining_seconds: int = SUMMARY_MIN_REMAINING_TIME_SECONDS
    final_reserved_seconds: int = SUMMARY_FINAL_RENDER_RESERVED_SECONDS
    now_fn: Callable[[], datetime] = utcnow
    deadline_override: Optional[datetime] = None
    extraction_deadline_override: Optional[datetime] = None

    @property
    def deadline_at(self) -> datetime:
        return self.deadline_override or (self.started_at + timedelta(seconds=self.total_seconds))

    @property
    def extraction_deadline_at(self) -> datetime:
        return self.extraction_deadline_override or (
            self.deadline_at - timedelta(seconds=self.final_reserved_seconds)
        )

    def now(self) -> datetime:
        return _as_utc(self.now_fn()) or utcnow()

    def elapsed_seconds(self, now: datetime | None = None) -> float:
        current = _as_utc(now) or self.now()
        return max(0.0, (current - self.started_at).total_seconds())

    def remaining_seconds(self, phase: str, now: datetime | None = None) -> float:
        current = _as_utc(now) or self.now()
        deadline = self.extraction_deadline_at if phase == "extraction" else self.deadline_at
        return max(0.0, (deadline - current).total_seconds())

    def request_timeout(self, phase: str, now: datetime | None = None) -> int:
        current = _as_utc(now) or self.now()
        total_remaining = (self.deadline_at - current).total_seconds()
        if total_remaining <= 0:
            raise SummaryBudgetExhausted(
                "total_time_limit_reached",
                phase=phase,
                remaining_seconds=0,
            )

        phase_remaining = self.remaining_seconds(phase, current)
        if phase_remaining < self.min_remaining_seconds:
            raise SummaryBudgetExhausted(
                "insufficient_time_for_next_request",
                phase=phase,
                remaining_seconds=phase_remaining,
            )
        return max(1, int(min(self.request_seconds, phase_remaining, total_remaining)))

    def state_fields(self, now: datetime | None = None) -> dict:
        current = _as_utc(now) or self.now()
        return {
            "summary_started_at": self.started_at,
            "summary_deadline_at": self.deadline_at,
            "summary_extraction_deadline_at": self.extraction_deadline_at,
            "summary_elapsed_seconds": round(self.elapsed_seconds(current), 3),
            "summary_time_limit_seconds": self.total_seconds,
        }

    @classmethod
    def from_state(
        cls,
        state: dict,
        *,
        now_fn: Callable[[], datetime] = utcnow,
    ) -> "SummaryBudget":
        started_at = _as_utc(state.get("summary_started_at"))
        if started_at is None:
            raise ValueError("summary_started_at is required")
        return cls(
            started_at=started_at,
            total_seconds=int(state.get("summary_time_limit_seconds") or SUMMARY_TOTAL_TIMEOUT_SECONDS),
            request_seconds=SUMMARY_REQUEST_TIMEOUT_SECONDS,
            min_remaining_seconds=SUMMARY_MIN_REMAINING_TIME_SECONDS,
            final_reserved_seconds=SUMMARY_FINAL_RENDER_RESERVED_SECONDS,
            now_fn=now_fn,
            deadline_override=_as_utc(state.get("summary_deadline_at")),
            extraction_deadline_override=_as_utc(state.get("summary_extraction_deadline_at")),
        )
