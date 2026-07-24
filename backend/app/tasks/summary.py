"""Celery tasks for segment-based async meeting summarization."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from bson import ObjectId
from loguru import logger
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.celery_app import celery_app
from app.models.meeting import MEETING_TYPES
from app.services.cancellation import JobCancelled
from app.services.db import get_worker_db
from app.services.mongo import settle_job_quota_db
from app.services.storage import BUCKET_ARTIFACTS, StorageService
from app.services.summarizer import _call_llm_with_fallback, _get_template_for_meeting
from app.services.summary_budget import (
    SUMMARY_ALLOW_PARTIAL_RESULT,
    SUMMARY_TOTAL_TIMEOUT_SECONDS,
    SUMMARY_USE_DETERMINISTIC_FINAL_FALLBACK,
    SummaryBudget,
    SummaryBudgetExhausted,
)
from app.services.summary_pipeline import (
    CHUNK_INPUT_TOKENS,
    CRITICAL_FIELDS,
    SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    SUMMARY_GEMMA_EMPTY_WARNING,
    SUMMARY_GEMMA_PARTIAL_WARNING,
    append_user_warning,
    build_token_check,
    chunk_segments,
    deterministic_merge,
    estimate_tokens,
    extract_chunk_record,
    normalize_record,
    record_to_text,
    reduce_records,
    render_record,
    transcript_fallback_text,
    unresolved_chunk_numbers,
    _evidence_index,
    _record_has_content,
    _split_failed_chunk,
)


SUMMARY_ASYNC_MAX_RETRY_DEPTH = int(os.getenv("SUMMARY_ASYNC_MAX_RETRY_DEPTH", "2"))
SUMMARY_ASYNC_MIN_CHUNK_TOKENS = int(os.getenv("SUMMARY_ASYNC_MIN_CHUNK_TOKENS", "1000"))
SUMMARY_ASYNC_LOCK_TTL_SECONDS = int(os.getenv("SUMMARY_ASYNC_LOCK_TTL_SECONDS", "600"))
SUMMARY_ASYNC_LOCK_RETRY_SECONDS = int(os.getenv("SUMMARY_ASYNC_LOCK_RETRY_SECONDS", "5"))
SUMMARY_ASYNC_LOCK_MAX_RETRIES = int(os.getenv("SUMMARY_ASYNC_LOCK_MAX_RETRIES", "120"))
SUMMARY_STATE_RETENTION_DAYS = int(os.getenv("SUMMARY_STATE_RETENTION_DAYS", "30"))
SPEAKER_CLIP_RETENTION_DAYS = int(os.getenv("SPEAKER_CLIP_RETENTION_DAYS", "30"))
SESSION_RETENTION_DAYS = int(os.getenv("SESSION_RETENTION_DAYS", "365"))

_LOCK_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
end
return 0
"""
_LOCK_RENEW_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


class SummaryLockUnavailable(RuntimeError):
    """Redis could not provide the correctness lock; the task must retry."""


class SummaryLockLost(RuntimeError):
    """The lease changed while this worker was running."""

_SUMMARY_INDEX_READY = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _get_storage() -> StorageService:
    return StorageService()


def _summary_state_collection(db):
    global _SUMMARY_INDEX_READY
    collection = db.summary_state
    if not _SUMMARY_INDEX_READY:
        try:
            collection.create_index("job_id", unique=True, background=True)
            collection.create_index([("status", 1), ("updated_at", -1)], background=True)
            collection.create_index("expires_at", expireAfterSeconds=0, background=True)
            _SUMMARY_INDEX_READY = True
        except Exception as exc:
            logger.warning("Could not ensure summary_state indexes: {}", exc)
    return collection


def _is_job_cancelled(db, job_id: str) -> bool:
    try:
        doc = db.job.find_one({"_id": ObjectId(job_id)}, {"status": 1, "user_id": 1})
    except Exception:
        return True
    if not doc or doc.get("status") == "cancelled":
        return True
    user = db.user.find_one({"_id": doc.get("user_id")}, {"deletion_pending": 1})
    return not user or bool(user.get("deletion_pending"))


def _ensure_not_cancelled(db, job_id: str):
    if _is_job_cancelled(db, job_id):
        raise JobCancelled(f"Job {job_id} was cancelled")


def _update_job(
    db,
    job_id: str,
    update: dict,
    *,
    run_id: Optional[str] = None,
    allow_cancelled: bool = False,
):
    query = {"_id": ObjectId(job_id)}
    if run_id:
        query["summary_active_run_id"] = run_id
    if not allow_cancelled:
        query["status"] = {"$ne": "cancelled"}
    result = db.job.update_one(query, {"$set": update})
    if run_id and getattr(result, "matched_count", 0) == 0:
        raise SummaryLockLost("summary_job_checkpoint_superseded")
    return result


def _enqueue_cancel_cleanup(job_id: str) -> None:
    try:
        from app.tasks.maintenance import finalize_cancelled_job

        finalize_cancelled_job.apply_async(args=[job_id], queue="maintenance")
    except Exception as exc:
        logger.warning("Job {}: could not enqueue cancellation cleanup: {}", job_id, exc)


def _refund_job_quota_once(db, job_id: str) -> bool:
    job = db.job.find_one({"_id": ObjectId(job_id)}, {"quota_reservation_id": 1})
    if job and job.get("quota_reservation_id"):
        return settle_job_quota_db(db, job_id, "refunded")
    job = db.job.find_one_and_update(
        {
            "_id": ObjectId(job_id),
            "quota_reserved": True,
            "quota_refunded": {"$ne": True},
        },
        {"$set": {"quota_refunded": True}},
    )
    if not job:
        return False

    minutes = max(float(job.get("quota_minutes") or 0), 0)
    db.user_package.update_one(
        {"user_id": job["user_id"]},
        {"$inc": {
            "usage.files_this_month": -1,
            "usage.ai_summaries_this_month": -1,
            "usage.transcription_minutes_this_month": -minutes,
        }},
    )
    try:
        import redis
        broker_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        cache_url = broker_url.rsplit("/", 1)[0] + "/1"
        redis.from_url(cache_url, decode_responses=True).delete(f"user_pkg:{job['user_id']}")
    except Exception:
        pass
    return True


def _redis_client():
    import redis

    return redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"), decode_responses=True)


def _acquire_lock(job_id: str) -> tuple[Any, Optional[str]]:
    token = uuid.uuid4().hex
    key = f"summary:{job_id}:lock"
    try:
        client = _redis_client()
        acquired = client.set(key, token, nx=True, ex=SUMMARY_ASYNC_LOCK_TTL_SECONDS)
    except Exception as exc:
        logger.error("Job {}: summary Redis lock unavailable; retrying safely: {}", job_id, exc)
        raise SummaryLockUnavailable("summary_lock_unavailable") from exc
    if not acquired:
        logger.info("Job {}: another summary worker owns the lock", job_id)
        return client, None
    return client, token


def _release_lock(job_id: str, client, token: Optional[str]):
    if not client or not token:
        return
    key = f"summary:{job_id}:lock"
    try:
        client.eval(_LOCK_RELEASE_SCRIPT, 1, key, token)
    except Exception as exc:
        logger.warning("Job {}: could not release summary lock: {}", job_id, exc)


def _renew_lock(job_id: str, client, token: Optional[str]) -> bool:
    if not client or not token:
        return False
    key = f"summary:{job_id}:lock"
    try:
        return bool(client.eval(
            _LOCK_RENEW_SCRIPT,
            1,
            key,
            token,
            SUMMARY_ASYNC_LOCK_TTL_SECONDS,
        ))
    except Exception as exc:
        logger.warning("Job {}: could not renew summary lock: {}", job_id, exc)
        return False


def _state_query(job_id: str, run_id: Optional[str] = None) -> dict:
    query = {"job_id": job_id}
    if run_id:
        query["active_run_id"] = run_id
    return query


def _fenced_state_update(
    collection,
    job_id: str,
    run_id: Optional[str],
    update: dict,
    *,
    reason: str,
):
    """Apply a summary-state mutation only for the currently fenced run."""
    if not run_id:
        raise SummaryLockLost(f"{reason}:run_not_claimed")
    result = collection.update_one(_state_query(job_id, run_id), update)
    if getattr(result, "matched_count", 0) == 0:
        raise SummaryLockLost(f"{reason}:superseded")
    return result


def _claim_summary_run(db, collection, job_id: str, run_id: str) -> dict:
    """Fence older workers by rotating the durable run ID under the Redis lock."""
    now = _utcnow()
    job_result = db.job.update_one(
        {
            "_id": ObjectId(job_id),
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
            "$or": [
                {"summary_commit_state": {"$ne": "committing"}},
                {"summary_commit_run_id": run_id},
                {"summary_commit_lease_expires_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "summary_active_run_id": run_id,
            "summary_run_claimed_at": now,
        }},
    )
    if getattr(job_result, "matched_count", 0) == 0:
        raise SummaryLockLost("summary_job_run_claim_lost")
    result = collection.update_one(
        {
            "job_id": job_id,
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
        },
        {"$set": {
            "active_run_id": run_id,
            "lease_owner": run_id,
            "lease_expires_at": now + timedelta(seconds=SUMMARY_ASYNC_LOCK_TTL_SECONDS),
            "updated_at": now,
        }},
    )
    state = collection.find_one({"job_id": job_id})
    if not state:
        raise RuntimeError("Missing summary_state")
    if result.matched_count == 0 or state.get("active_run_id") != run_id:
        raise SummaryLockLost("summary_run_claim_lost")
    if not db.job.find_one(
        {"_id": ObjectId(job_id), "summary_active_run_id": run_id},
        {"_id": 1},
    ):
        raise SummaryLockLost("summary_job_run_rotated")
    return state


def _renew_summary_lease(db, job_id: str, client, run_id: Optional[str]) -> bool:
    """Renew Redis ownership and the Mongo fencing lease as one guard."""
    if not _renew_lock(job_id, client, run_id):
        return False
    now = _utcnow()
    result = _summary_state_collection(db).update_one(
        _state_query(job_id, run_id),
        {"$set": {
            "lease_expires_at": now + timedelta(seconds=SUMMARY_ASYNC_LOCK_TTL_SECONDS),
            "updated_at": now,
        }},
    )
    return getattr(result, "matched_count", 0) > 0


def _source_segments(artifact: dict) -> list[dict]:
    transcript = artifact.get("full_transcript") or {}
    segments = transcript.get("segments") or []
    return [
        {**segment, "_source_index": int(segment.get("_source_index", index))}
        for index, segment in enumerate(segments)
    ]


def _transcript_text(artifact: dict) -> str:
    transcript = artifact.get("full_transcript") or {}
    return (
        transcript.get("transcript_with_speakers")
        or transcript.get("combined_text")
        or "\n".join(str(seg.get("text") or "") for seg in transcript.get("segments") or [])
    )


def _expected_segment_ids(segments: list[dict]) -> set[int]:
    return {
        int(segment.get("_source_index", index))
        for index, segment in enumerate(segments)
        if str(segment.get("text") or "").strip()
    }


def _chunk_ranges(chunks: list[dict]) -> list[dict]:
    return [
        {
            "chunk_number": chunk["chunk_number"],
            "start_segment_idx": chunk["start_segment_idx"],
            "end_segment_idx": chunk["end_segment_idx"],
            "segment_ids": chunk["segment_ids"],
            "estimated_tokens": chunk["estimated_tokens"],
        }
        for chunk in chunks
    ]


def _failed_ranges(
    chunks: list[dict],
    failed_chunks: list[int],
    covered_ids: set[int],
    *,
    reason: str = "unresolved_partial_chunks",
) -> list[dict]:
    failed = set(int(number) for number in failed_chunks or [])
    ranges: list[dict] = []
    for chunk in chunks:
        if int(chunk["chunk_number"]) not in failed:
            continue
        segment_ids = {int(item) for item in chunk.get("segment_ids") or []}
        uncovered = sorted(segment_ids - covered_ids)
        if not uncovered:
            continue
        ranges.append({
            "chunk_number": chunk["chunk_number"],
            "start_segment_idx": min(uncovered),
            "end_segment_idx": max(uncovered),
            "uncovered_segments": uncovered,
            "reason": reason,
        })
    return ranges


def _uncovered_ranges(chunks: list[dict], covered_ids: set[int], reason: str) -> list[dict]:
    return _failed_ranges(
        chunks,
        [int(chunk["chunk_number"]) for chunk in chunks],
        covered_ids,
        reason=reason,
    )


def _coverage_percentage(covered_segments: int, total_segments: int) -> float:
    if total_segments <= 0:
        return 0.0
    return round((covered_segments / total_segments) * 100, 1)


def _completion_snapshot(
    records: list[dict],
    chunks: list[dict],
    expected_ids: set[int],
) -> dict:
    merged = deterministic_merge(records) if records else normalize_record({})
    covered_ids = {int(item) for item in merged.get("coverage", [])}
    historical_failed_chunks = sorted(
        set(int(item) for item in merged.get("failed_chunks", []))
    )
    historical_partial_chunks = sorted(
        set(int(item) for item in merged.get("partial_chunks", []))
    )
    failed_chunks = unresolved_chunk_numbers(chunks, historical_failed_chunks, covered_ids)
    partial_chunks = unresolved_chunk_numbers(chunks, historical_partial_chunks, covered_ids)
    completed_chunks = [
        chunk["chunk_number"]
        for chunk in chunks
        if set(chunk.get("segment_ids") or []).issubset(covered_ids)
    ]
    failed_ranges = _failed_ranges(chunks, failed_chunks, covered_ids)
    covered_segments = len(expected_ids & covered_ids)
    return {
        "merged": merged,
        "covered_ids": covered_ids,
        "completed_chunks": completed_chunks,
        "failed_chunks": failed_chunks,
        "partial_chunks": partial_chunks,
        "recovered_failed_chunks": sorted(set(historical_failed_chunks) - set(failed_chunks)),
        "recovered_partial_chunks": sorted(set(historical_partial_chunks) - set(partial_chunks)),
        "failed_ranges": failed_ranges,
        "covered_segment_ids": sorted(expected_ids & covered_ids),
        "covered_segments": covered_segments,
        "total_segments": len(expected_ids),
        "coverage_percentage": _coverage_percentage(covered_segments, len(expected_ids)),
        "last_covered_segment": max(expected_ids & covered_ids) if expected_ids & covered_ids else None,
        "coverage_complete": expected_ids.issubset(covered_ids),
    }


def initialize_summary_state(
    db,
    job_id: str,
    artifact_object: str,
    segments: list[dict],
) -> dict:
    """Create the durable async summary_state document if it does not exist."""
    source_segments = [
        {**segment, "_source_index": int(segment.get("_source_index", index))}
        for index, segment in enumerate(segments)
    ]
    chunks = chunk_segments(source_segments)
    expected_ids = _expected_segment_ids(source_segments)
    state = {
        "job_id": job_id,
        "active_run_id": uuid.uuid4().hex,
        "lease_owner": None,
        "lease_expires_at": None,
        "status": "queued",
        "artifact_object": artifact_object,
        "total_segments": len(expected_ids),
        "covered_segments": 0,
        "covered_segment_ids": [],
        "coverage_percentage": 0.0,
        "last_covered_segment": None,
        "total_chunks": len(chunks),
        "chunk_ranges": _chunk_ranges(chunks),
        "completed_chunks": [],
        "partial_chunks": [],
        "recovered_failed_chunks": [],
        "recovered_partial_chunks": [],
        "failed_ranges": [],
        "attempts": {},
        "records": [],
        "rolling_memory": None,
        "next_chunk_index": 0,
        "coverage_complete": False,
        "final_summary": None,
        "summary_started_at": None,
        "summary_deadline_at": None,
        "summary_extraction_deadline_at": None,
        "summary_finished_at": None,
        "summary_elapsed_seconds": 0.0,
        "summary_time_limit_seconds": SUMMARY_TOTAL_TIMEOUT_SECONDS,
        "stop_reason": None,
        "final_render_status": "not_started",
        "model_timeout_seen": False,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
        "expires_at": _utcnow() + timedelta(days=SUMMARY_STATE_RETENTION_DAYS),
    }
    collection = _summary_state_collection(db)
    collection.update_one(
        {"job_id": job_id},
        {"$setOnInsert": state},
        upsert=True,
    )
    return collection.find_one({"job_id": job_id}) or state


def _recover_terminal_job_checkpoint(db, storage, job_id: str, state: dict) -> Optional[dict]:
    """Repair state/outbox after a crash immediately following the job CAS."""
    job = db.job.find_one(
        {
            "_id": ObjectId(job_id),
            "status": {"$in": ["completed", "partially_completed", "failed"]},
            "result_available": True,
        },
        {"status": 1, "result": 1, "session_id": 1, "completed_at": 1},
    )
    if not job:
        return None
    finished_at = job.get("completed_at") or _utcnow()
    _summary_state_collection(db).update_one(
        {
            "job_id": job_id,
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
        },
        {"$set": {
            "status": job["status"],
            "result_available": True,
            "summary_finished_at": finished_at,
            "updated_at": finished_at,
            "expires_at": finished_at + timedelta(days=SUMMARY_STATE_RETENTION_DAYS),
        }},
    )
    try:
        artifact = _load_artifact(storage, state["artifact_object"])
        _enqueue_post_commit(db, job_id, artifact, job.get("result") or {})
    except Exception:
        logger.exception("Job {}: terminal checkpoint post-commit recovery failed", job_id)
    return {
        "job_id": job_id,
        "session_id": str(job.get("session_id") or "") or None,
        "status": job["status"],
        "result_available": True,
        "recovered_terminal_checkpoint": True,
    }


def _ensure_summary_budget(
    collection,
    job_id: str,
    run_id: Optional[str] = None,
) -> tuple[dict, SummaryBudget]:
    state = collection.find_one(_state_query(job_id, run_id))
    if not state:
        raise RuntimeError("Missing summary_state")
    if not state.get("summary_started_at"):
        started_at = _utcnow()
        budget = SummaryBudget(started_at=started_at)
        budget_result = collection.update_one(
            {
                **_state_query(job_id, run_id),
                "$or": [
                    {"summary_started_at": {"$exists": False}},
                    {"summary_started_at": None},
                ],
            },
            {"$set": {**budget.state_fields(started_at), "updated_at": started_at}},
        )
        if run_id and getattr(budget_result, "matched_count", 0) == 0:
            raise SummaryLockLost("summary_budget_superseded")
        state = collection.find_one(_state_query(job_id, run_id)) or state
        logger.info(
            "Job {}: summary budget started total={}s extraction={}s reserve={}s",
            job_id,
            budget.total_seconds,
            budget.total_seconds - budget.final_reserved_seconds,
            budget.final_reserved_seconds,
        )
    budget = SummaryBudget.from_state(state)
    return state, budget


def _load_artifact(storage: StorageService, object_name: str) -> dict:
    return storage.get_json(BUCKET_ARTIFACTS, object_name)


def _template_prompt(meeting_type_id: int, db) -> str:
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=db)
    return str(template_data.get("system_prompt") or "")


def _llm_call(
    db,
    job_id: str,
    budget: SummaryBudget,
    phase: str,
    lock_client=None,
    lock_token: Optional[str] = None,
):
    def call(system_prompt, user_prompt, **kwargs):
        _ensure_not_cancelled(db, job_id)
        if not _renew_summary_lease(db, job_id, lock_client, lock_token):
            raise SummaryLockLost("summary_lock_lost")

        request_meta = dict(kwargs.pop("request_meta", {}) or {})
        configured_timeout = kwargs.get("timeout")

        def timeout_provider(model: str, attempt: int, gateway_timeout: Optional[int]) -> int:
            _ensure_not_cancelled(db, job_id)
            if not _renew_summary_lease(db, job_id, lock_client, lock_token):
                raise SummaryLockLost("summary_lock_lost")
            remaining_timeout = budget.request_timeout(phase)
            candidates = [remaining_timeout]
            for value in (configured_timeout, gateway_timeout):
                if value is not None:
                    candidates.append(int(value))
            effective = max(1, min(candidates))
            attempt_type = "retry" if attempt > 1 else request_meta.get("request_type", "unknown")
            logger.info(
                "Job {}: summary request attempt type={} phase={} model={} attempt={} "
                "elapsed={:.1f}s remaining={:.1f}s timeout={}s chunk={} S{}-S{}",
                job_id,
                attempt_type,
                phase,
                model,
                attempt,
                budget.elapsed_seconds(),
                budget.remaining_seconds(phase),
                effective,
                request_meta.get("chunk_label", "-"),
                request_meta.get("start_segment_idx", "-"),
                request_meta.get("end_segment_idx", "-"),
            )
            return effective

        initial_timeout = budget.request_timeout(phase)
        if configured_timeout is not None:
            initial_timeout = min(initial_timeout, int(configured_timeout))
        kwargs["timeout"] = max(1, initial_timeout)
        started = time.monotonic()
        result = _call_llm_with_fallback(
            system_prompt,
            user_prompt,
            mongo_service=db,
            cancel_check=lambda: _ensure_not_cancelled(db, job_id),
            return_diagnostics=True,
            attempt_timeout_provider=timeout_provider,
            request_meta=request_meta,
            **kwargs,
        )
        outcome = {
            "request_type": request_meta.get("request_type", "unknown"),
            "chunk_label": request_meta.get("chunk_label"),
            "start_segment_idx": request_meta.get("start_segment_idx"),
            "end_segment_idx": request_meta.get("end_segment_idx"),
            "model": getattr(result, "model", None),
            "attempts": int(getattr(result, "attempts", 0) or 0),
            "timed_out": bool(getattr(result, "timed_out", False)),
            "error_kind": getattr(result, "error_kind", None),
            "duration_seconds": round(time.monotonic() - started, 3),
            "finished_at": _utcnow(),
        }
        outcome_update = {
            "last_llm_outcome": outcome,
            "last_model_timeout": outcome["timed_out"],
            "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
            "updated_at": _utcnow(),
        }
        if outcome["timed_out"]:
            outcome_update["model_timeout_seen"] = True
        outcome_result = _summary_state_collection(db).update_one(
            _state_query(job_id, lock_token),
            {"$set": outcome_update},
        )
        if getattr(outcome_result, "matched_count", 0) == 0:
            raise SummaryLockLost("summary_outcome_superseded")
        logger.info(
            "Job {}: summary request finished type={} chunk={} duration={:.1f}s "
            "timed_out={} error={} output_chars={}",
            job_id,
            outcome["request_type"],
            outcome.get("chunk_label") or "-",
            outcome["duration_seconds"],
            outcome["timed_out"],
            outcome["error_kind"],
            len(str(getattr(result, "content", "") or "")),
        )
        return result

    return call


def _process_chunk_balanced(
    chunk: dict,
    previous_record: Optional[dict],
    llm_call,
    scope_label: str,
    custom_prompt: str,
    template_prompt: str,
    depth: int = 0,
    checkpoint_record=None,
) -> list[dict]:
    can_split = (
        depth < SUMMARY_ASYNC_MAX_RETRY_DEPTH
        and int(chunk.get("estimated_tokens") or 0) > SUMMARY_ASYNC_MIN_CHUNK_TOKENS
        and bool(chunk.get("_segments"))
    )
    chunk_label = str(chunk.get("_chunk_label", chunk["chunk_number"]))
    request_type = "root" if depth == 0 else "child"
    record = extract_chunk_record(
        chunk,
        previous_record,
        llm_call,
        scope_label,
        custom_prompt,
        template_prompt=template_prompt,
        primary_only=can_split or SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        request_meta={
            "request_type": request_type,
            "chunk_label": chunk_label,
            "start_segment_idx": chunk["start_segment_idx"],
            "end_segment_idx": chunk["end_segment_idx"],
            "depth": depth,
        },
    )
    if not record.get("failed_chunks"):
        record["_record_id"] = f"{chunk_label}:structured"
        if checkpoint_record:
            checkpoint_record(record)
        return [record]

    records: list[dict] = []
    if record.get("partial_chunks") and str(record.get("summary") or "").strip():
        partial_record = dict(record)
        partial_record["coverage"] = []
        partial_record["failed_chunks"] = []
        partial_record["partial_only"] = True
        partial_record["_record_id"] = f"{chunk_label}:partial"
        records.append(partial_record)
        if checkpoint_record:
            checkpoint_record(partial_record)

    if not can_split:
        if not records:
            record["_record_id"] = f"{chunk_label}:failed"
            if checkpoint_record:
                checkpoint_record(record)
        return records or [record]

    target_tokens = max(
        SUMMARY_ASYNC_MIN_CHUNK_TOKENS,
        int(chunk.get("estimated_tokens") or SUMMARY_ASYNC_MIN_CHUNK_TOKENS) // 2,
    )
    children = _split_failed_chunk(chunk, target_tokens)
    if len(children) <= 1:
        if not records:
            record["_record_id"] = f"{chunk_label}:failed"
            if checkpoint_record:
                checkpoint_record(record)
        return records or [record]

    logger.warning(
        "Async summary split chunk {} into {} parts at target_tokens={} depth={}",
        chunk.get("_chunk_label", chunk["chunk_number"]),
        len(children),
        target_tokens,
        depth + 1,
    )
    context = previous_record
    for child in children:
        child_records = _process_chunk_balanced(
            child,
            context,
            llm_call,
            scope_label,
            custom_prompt,
            template_prompt,
            depth=depth + 1,
            checkpoint_record=checkpoint_record,
        )
        records.extend(child_records)
        successful = [item for item in child_records if not item.get("failed_chunks")]
        if successful:
            context = deterministic_merge(([context] if context else []) + successful)
    return records or [record]


def _checkpoint_summary_record(
    db,
    job_id: str,
    record: dict,
    chunks: list[dict],
    expected_ids: set[int],
    budget: SummaryBudget,
    summary_percent: int,
    run_id: Optional[str] = None,
) -> dict:
    collection = _summary_state_collection(db)
    state = collection.find_one({"job_id": job_id}) or {}
    records = list(state.get("records") or [])
    record_id = str(record.get("_record_id") or uuid.uuid4().hex)
    record = {**record, "_record_id": record_id}
    replaced = False
    for index, existing in enumerate(records):
        if str(existing.get("_record_id") or "") == record_id:
            records[index] = record
            replaced = True
            break
    if not replaced:
        records.append(record)

    snapshot = _completion_snapshot(records, chunks, expected_ids)
    memory_records = [
        item
        for item in records
        if _record_has_content(item) and not item.get("failed_chunks")
    ]
    rolling_memory = deterministic_merge(memory_records) if memory_records else None
    attempts = dict(state.get("attempts") or {})
    attempts[record_id] = int(attempts.get(record_id, 0)) + 1
    now = _utcnow()
    state_update = {
        "status": "running",
        "total_segments": len(expected_ids),
        "covered_segments": snapshot["covered_segments"],
        "covered_segment_ids": snapshot["covered_segment_ids"],
        "coverage_percentage": snapshot["coverage_percentage"],
        "last_covered_segment": snapshot["last_covered_segment"],
        "total_chunks": len(chunks),
        "chunk_ranges": _chunk_ranges(chunks),
        "completed_chunks": snapshot["completed_chunks"],
        "partial_chunks": snapshot["partial_chunks"],
        "recovered_failed_chunks": snapshot["recovered_failed_chunks"],
        "recovered_partial_chunks": snapshot["recovered_partial_chunks"],
        "failed_ranges": snapshot["failed_ranges"],
        "attempts": attempts,
        "records": records,
        "rolling_memory": rolling_memory,
        "coverage_complete": snapshot["coverage_complete"],
        "summary_elapsed_seconds": round(budget.elapsed_seconds(now), 3),
        "lease_expires_at": now + timedelta(seconds=SUMMARY_ASYNC_LOCK_TTL_SECONDS),
        "updated_at": now,
    }
    result = collection.update_one(_state_query(job_id, run_id), {"$set": state_update})
    if run_id and getattr(result, "matched_count", 0) == 0:
        raise SummaryLockLost("summary_checkpoint_superseded")
    _update_job(db, job_id, {
        "status": "processing",
        "current_step": "summarizing_chunk",
        "progress": min(94, 75 + int(summary_percent * 0.19)),
        "summary_status": "running",
        "summary_progress": summary_percent,
        "summary_completed_chunks": len(snapshot["completed_chunks"]),
        "summary_total_chunks": len(chunks),
        "covered_segments": snapshot["covered_segments"],
        "total_segments": len(expected_ids),
        "coverage_percentage": snapshot["coverage_percentage"],
        "last_covered_segment": snapshot["last_covered_segment"],
        "partial_chunks": snapshot["partial_chunks"],
        "failed_ranges": snapshot["failed_ranges"],
        "coverage_complete": snapshot["coverage_complete"],
        "summary_started_at": budget.started_at,
        "summary_elapsed_seconds": round(budget.elapsed_seconds(now), 3),
        "summary_time_limit_seconds": budget.total_seconds,
    }, run_id=run_id)
    logger.info(
        "Job {}: summary checkpoint record={} coverage={}/{} ({:.1f}%) partial={} failed_ranges={}",
        job_id,
        record_id,
        snapshot["covered_segments"],
        len(expected_ids),
        snapshot["coverage_percentage"],
        len(snapshot["partial_chunks"]),
        len(snapshot["failed_ranges"]),
    )
    return snapshot


def _remaining_root_parts(chunk: dict, covered_ids: set[int]) -> list[dict]:
    remaining_segments = [
        segment
        for segment in chunk.get("_segments") or []
        if int(segment.get("_source_index", -1)) not in covered_ids
        and str(segment.get("text") or "").strip()
    ]
    if not remaining_segments:
        return []
    parts = chunk_segments(
        remaining_segments,
        max_tokens=CHUNK_INPUT_TOKENS,
        overlap_tokens=0,
    )
    root_number = int(chunk["chunk_number"])
    for index, part in enumerate(parts, start=1):
        part["chunk_number"] = root_number
        part["total_chunks"] = chunk["total_chunks"]
        part["_root_chunk_number"] = root_number
        if len(parts) > 1:
            part["_chunk_label"] = f"{root_number}.resume{index}"
    return parts


@celery_app.task(bind=True, name="summary.process_next_chunk")
def process_next_chunk(self, job_id: str):
    """Process one root transcript chunk and enqueue the next chunk/finalizer."""
    db = get_worker_db()
    try:
        lock_client, lock_token = _acquire_lock(job_id)
    except SummaryLockUnavailable as exc:
        raise self.retry(
            exc=exc,
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    if lock_token is None:
        raise self.retry(
            exc=SummaryLockUnavailable("summary_lock_busy"),
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    storage = _get_storage()

    enqueue_next = False
    enqueue_finalize = False
    run_id = None
    try:
        _ensure_not_cancelled(db, job_id)
        collection = _summary_state_collection(db)
        state = collection.find_one({"job_id": job_id})
        if not state:
            job = db.job.find_one({"_id": ObjectId(job_id)}, {"transcript_artifact_object": 1})
            artifact_object = job.get("transcript_artifact_object") if job else None
            if not artifact_object:
                raise RuntimeError("Missing transcript artifact for async summary")
            artifact = _load_artifact(storage, artifact_object)
            state = initialize_summary_state(db, job_id, artifact_object, _source_segments(artifact))

        recovered_terminal = _recover_terminal_job_checkpoint(db, storage, job_id, state)
        if recovered_terminal:
            return recovered_terminal
        if state.get("status") in {"completed", "partially_completed", "failed", "cancelled"}:
            return {"job_id": job_id, "status": state.get("status")}
        if state.get("status") == "finalizing":
            enqueue_finalize = True
            return {"job_id": job_id, "summary_finalizing": True}

        state = _claim_summary_run(db, collection, job_id, lock_token)
        run_id = lock_token
        state, budget = _ensure_summary_budget(collection, job_id, run_id)
        budget.request_timeout("extraction")

        artifact = _load_artifact(storage, state["artifact_object"])
        segments = _source_segments(artifact)
        chunks = chunk_segments(segments)
        expected_ids = _expected_segment_ids(segments)
        if not chunks:
            enqueue_finalize = True
            return {"job_id": job_id, "summary_finalizing": True}

        next_index = int(state.get("next_chunk_index") or 0)
        if next_index >= len(chunks):
            enqueue_finalize = True
            return {"job_id": job_id, "summary_finalizing": True}

        snapshot = _completion_snapshot(list(state.get("records") or []), chunks, expected_ids)
        root_chunk = chunks[next_index]
        remaining_parts = _remaining_root_parts(root_chunk, snapshot["covered_ids"])
        previous = state.get("rolling_memory") or snapshot["merged"]
        template_prompt = _template_prompt(
            int(artifact.get("effective_meeting_type_id") or artifact.get("meeting_type_id") or 0),
            db,
        )
        custom_prompt = str(artifact.get("custom_prompt") or "")
        summary_percent = int((next_index / max(len(chunks), 1)) * 100)
        _update_job(db, job_id, {
            "status": "processing",
            "current_step": "summarizing_chunk",
            "progress": min(94, 75 + int(summary_percent * 0.19)),
            "summary_status": "running",
            "summary_progress": summary_percent,
            "summary_completed_chunks": len(state.get("completed_chunks") or []),
            "summary_total_chunks": len(chunks),
            "summary_celery_task_id": self.request.id,
            "summary_started_at": budget.started_at,
            "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
            "summary_time_limit_seconds": budget.total_seconds,
            "coverage_percentage": snapshot["coverage_percentage"],
            "result_available": False,
        }, run_id=run_id)
        _fenced_state_update(
            collection,
            job_id,
            run_id,
            {"$set": {
                "status": "running",
                "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
                "updated_at": _utcnow(),
            }},
            reason="summary_running_checkpoint",
        )

        logger.info(
            "Job {}: async summary chunk {}/{} S{}-S{} estimated_tokens={} "
            "remaining_parts={} elapsed={:.1f}s remaining={:.1f}s",
            job_id,
            root_chunk["chunk_number"],
            len(chunks),
            root_chunk["start_segment_idx"],
            root_chunk["end_segment_idx"],
            root_chunk["estimated_tokens"],
            len(remaining_parts),
            budget.elapsed_seconds(),
            budget.remaining_seconds("extraction"),
        )

        llm_call = _llm_call(
            db,
            job_id,
            budget,
            "extraction",
            lock_client,
            lock_token,
        )
        for part in remaining_parts:
            part_records = _process_chunk_balanced(
                part,
                previous,
                llm_call,
                "การประชุม",
                custom_prompt,
                template_prompt,
                checkpoint_record=lambda record: _checkpoint_summary_record(
                    db,
                    job_id,
                    record,
                    chunks,
                    expected_ids,
                    budget,
                    summary_percent,
                    run_id,
                ),
            )
            usable_context = [
                record for record in part_records
                if _record_has_content(record) and not record.get("failed_chunks")
            ]
            if usable_context:
                previous = deterministic_merge(([previous] if previous else []) + usable_context)

        next_index += 1
        state = collection.find_one(_state_query(job_id, run_id))
        if not state:
            raise SummaryLockLost("summary_progress_read_superseded")
        records = list(state.get("records") or [])
        snapshot = _completion_snapshot(records, chunks, expected_ids)
        summary_percent = int((next_index / max(len(chunks), 1)) * 100)
        progress_result = collection.update_one(
            _state_query(job_id, run_id),
            {"$set": {
                "status": "running",
                "total_segments": len(expected_ids),
                "covered_segments": snapshot["covered_segments"],
                "covered_segment_ids": snapshot["covered_segment_ids"],
                "coverage_percentage": snapshot["coverage_percentage"],
                "last_covered_segment": snapshot["last_covered_segment"],
                "total_chunks": len(chunks),
                "chunk_ranges": _chunk_ranges(chunks),
                "completed_chunks": snapshot["completed_chunks"],
                "partial_chunks": snapshot["partial_chunks"],
                "recovered_failed_chunks": snapshot["recovered_failed_chunks"],
                "recovered_partial_chunks": snapshot["recovered_partial_chunks"],
                "failed_ranges": snapshot["failed_ranges"],
                "records": records,
                "rolling_memory": state.get("rolling_memory"),
                "next_chunk_index": next_index,
                "coverage_complete": snapshot["coverage_complete"],
                "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
                "updated_at": _utcnow(),
            }},
        )
        if getattr(progress_result, "matched_count", 0) == 0:
            raise SummaryLockLost("summary_progress_superseded")
        _update_job(db, job_id, {
            "current_step": "summarizing_chunk",
            "progress": min(94, 75 + int(summary_percent * 0.19)),
            "summary_status": "running",
            "summary_progress": summary_percent,
            "summary_completed_chunks": len(snapshot["completed_chunks"]),
            "summary_total_chunks": len(chunks),
            "covered_segments": snapshot["covered_segments"],
            "total_segments": len(expected_ids),
            "coverage_percentage": snapshot["coverage_percentage"],
            "last_covered_segment": snapshot["last_covered_segment"],
            "partial_chunks": snapshot["partial_chunks"],
            "recovered_partial_chunks": snapshot["recovered_partial_chunks"],
            "failed_ranges": snapshot["failed_ranges"],
            "coverage_complete": snapshot["coverage_complete"],
            "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
        }, run_id=run_id)

        enqueue_finalize = next_index >= len(chunks)
        enqueue_next = not enqueue_finalize
        return {
            "job_id": job_id,
            "processed_chunk": root_chunk["chunk_number"],
            "next_chunk_index": next_index,
            "summary_finalizing": enqueue_finalize,
        }
    except SummaryLockLost as exc:
        logger.warning("Job {}: summary lease lost; retrying checkpoint safely: {}", job_id, exc)
        raise self.retry(
            exc=exc,
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    except SummaryBudgetExhausted as exc:
        logger.warning(
            "Job {}: summary extraction stopped reason={} elapsed={:.1f}s remaining={:.1f}s",
            job_id,
            exc.reason,
            budget.elapsed_seconds() if "budget" in locals() else 0,
            exc.remaining_seconds,
        )
        now = _utcnow()
        _fenced_state_update(
            _summary_state_collection(db),
            job_id,
            run_id,
            {"$set": {
                "status": "finalizing",
                "stop_reason": exc.reason,
                "summary_elapsed_seconds": round(budget.elapsed_seconds(now), 3) if "budget" in locals() else 0,
                "updated_at": now,
            }},
            reason="summary_budget_finalizing",
        )
        _update_job(db, job_id, {
            "status": "processing",
            "current_step": "summary_finalizing",
            "progress": 96,
            "summary_status": "finalizing",
            "stop_reason": exc.reason,
            "summary_elapsed_seconds": round(budget.elapsed_seconds(now), 3) if "budget" in locals() else 0,
        }, run_id=run_id)
        enqueue_finalize = True
        return {"job_id": job_id, "summary_finalizing": True, "stop_reason": exc.reason}
    except JobCancelled as exc:
        logger.info("Job {}: async summary stopped after cancellation: {}", job_id, exc)
        if run_id:
            cancel_result = _summary_state_collection(db).update_one(
                _state_query(job_id, run_id),
                {"$set": {
                    "status": "cancelled",
                    "stop_reason": "cancelled",
                    "summary_finished_at": _utcnow(),
                    "updated_at": _utcnow(),
                }},
            )
            if getattr(cancel_result, "matched_count", 0) == 0:
                logger.info("Job {}: stale summary run skipped cancellation checkpoint", job_id)
        _update_job(db, job_id, {
            "status": "cancelled",
            "current_step": "cancelled",
            "summary_status": "cancelled",
            "cancellation_state": "requested",
            "cancellation_cleanup_status": "pending",
            "email_status": "cancelled",
            "stop_reason": "cancelled",
            "result_available": False,
            "error": "Cancelled by admin",
            "cancelled_at": _utcnow(),
            "completed_at": _utcnow(),
        }, allow_cancelled=True)
        _enqueue_cancel_cleanup(job_id)
        return {"job_id": job_id, "cancelled": True}
    except Exception as exc:
        logger.exception("Job {}: async summary chunk failed", job_id)
        if not run_id:
            raise self.retry(
                exc=SummaryLockLost("summary_error_before_run_claim"),
                countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
                max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
            )
        state = (
            _summary_state_collection(db).find_one(_state_query(job_id, run_id))
            if run_id else None
        )
        if run_id and state is None:
            raise SummaryLockLost("summary_error_handler_superseded")
        if state and state.get("artifact_object"):
            _fenced_state_update(
                _summary_state_collection(db),
                job_id,
                run_id,
                {"$set": {
                    "status": "finalizing",
                    "stop_reason": "fatal_error",
                    "error": str(exc),
                    "updated_at": _utcnow(),
                }},
                reason="summary_fatal_finalizing",
            )
            _update_job(db, job_id, {
                "status": "processing",
                "current_step": "summary_finalizing",
                "summary_status": "finalizing",
                "progress": 96,
                "stop_reason": "fatal_error",
                "error": str(exc),
            }, run_id=run_id)
            enqueue_finalize = True
            return {"job_id": job_id, "summary_finalizing": True, "stop_reason": "fatal_error"}

        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "summary_status": "failed",
            "progress": 100,
            "result_available": False,
            "stop_reason": "fatal_error",
            "error": str(exc),
            "completed_at": _utcnow(),
        }, run_id=run_id)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job {}: quota refund failed after summary failure: {}", job_id, refund_exc)
        return {"job_id": job_id, "failed": True, "error": str(exc)}
    finally:
        _release_lock(job_id, lock_client, lock_token)
        if enqueue_next:
            process_next_chunk.apply_async(args=[job_id], queue="summary", countdown=1)
        elif enqueue_finalize:
            finalize.apply_async(args=[job_id], queue="summary", countdown=1)


def _final_metadata(
    db,
    artifact: dict,
    state: dict,
    records: list[dict],
    chunks: list[dict],
    segments: list[dict],
    llm_call,
    budget: SummaryBudget,
) -> tuple[str, dict, str]:
    transcript = _transcript_text(artifact)
    input_tokens = estimate_tokens(transcript)
    expected_ids = _expected_segment_ids(segments)
    token_check = build_token_check(input_tokens, len(chunks))
    snapshot = _completion_snapshot(records, chunks, expected_ids)
    structured_records = [
        record
        for record in records
        if bool(record.get("coverage")) and _record_has_content(record)
    ]
    stop_reason = str(state.get("stop_reason") or "").strip() or None
    final_render_status = "not_started"
    final_max_tokens = 0
    render_degraded = False
    merged = deterministic_merge(structured_records) if structured_records else normalize_record({})
    summary = ""
    render_outcome: dict = {}

    if structured_records:
        try:
            merged = reduce_records(structured_records, llm_call, "การประชุม")
            template_prompt = _template_prompt(
                int(artifact.get("effective_meeting_type_id") or artifact.get("meeting_type_id") or 0),
                db,
            )
            summary, final_max_tokens, render_degraded, render_outcome = render_record(
                merged,
                llm_call,
                int(artifact.get("effective_meeting_type_id") or artifact.get("meeting_type_id") or 0),
                template_prompt,
                str(artifact.get("custom_prompt") or ""),
                "การประชุม",
                input_tokens,
                return_diagnostics=True,
                include_warning=False,
            )
            if not render_degraded and summary and not render_outcome.get("timed_out"):
                final_render_status = "llm_completed"
            elif SUMMARY_USE_DETERMINISTIC_FINAL_FALLBACK:
                summary = record_to_text(merged, heading="สรุปการประชุม")
                final_render_status = "deterministic_fallback"
                render_degraded = True
                if render_outcome.get("timed_out"):
                    stop_reason = "final_render_timeout"
                else:
                    stop_reason = stop_reason or "fatal_error"
            else:
                summary = ""
                final_render_status = "unavailable"
                render_degraded = True
                stop_reason = "final_render_timeout" if render_outcome.get("timed_out") else "fatal_error"
        except SummaryBudgetExhausted as exc:
            stop_reason = stop_reason or exc.reason
            render_degraded = True
            if SUMMARY_USE_DETERMINISTIC_FINAL_FALLBACK:
                summary = record_to_text(merged, heading="สรุปการประชุม")
                final_render_status = "deterministic_fallback"
            else:
                final_render_status = "unavailable"

    if not structured_records:
        stop_reason = stop_reason or (
            "model_timeout" if state.get("model_timeout_seen") else "unresolved_partial_chunks"
        )
        final_render_status = "unavailable"

    failed_chunks = snapshot["failed_chunks"]
    partial_chunks = snapshot["partial_chunks"]
    if not snapshot["coverage_complete"]:
        unresolved_reason = stop_reason or (
            "model_timeout" if state.get("model_timeout_seen") else "unresolved_partial_chunks"
        )
        failed_ranges = _uncovered_ranges(chunks, snapshot["covered_ids"], unresolved_reason)
    else:
        failed_ranges = []

    degraded = bool(
        not snapshot["coverage_complete"]
        or partial_chunks
        or failed_chunks
        or merged.get("reduce_degraded")
        or render_degraded
    )
    if (
        structured_records
        and snapshot["coverage_complete"]
        and final_render_status == "llm_completed"
        and not render_degraded
    ):
        terminal_status = "completed"
        stop_reason = "completed"
    elif structured_records and summary and SUMMARY_ALLOW_PARTIAL_RESULT:
        terminal_status = "partially_completed"
        stop_reason = stop_reason or (
            "model_timeout" if state.get("model_timeout_seen") else "unresolved_partial_chunks"
        )
    else:
        terminal_status = "failed"
        summary = ""
        final_render_status = "unavailable" if not summary else final_render_status
        stop_reason = stop_reason or "fatal_error"

    is_partial_summary = terminal_status == "partially_completed"
    user_warning = None
    if is_partial_summary:
        if snapshot["coverage_complete"]:
            user_warning = (
                "ระบบอ่าน Transcript ครบแล้ว แต่ Final Render ของโมเดลไม่สำเร็จภายในเวลา "
                "จึงจัดรูปแบบรายงานจาก Structured Records ที่บันทึกไว้"
            )
        else:
            user_warning = (
                f"สรุปครอบคลุม Transcript {snapshot['coverage_percentage']:.1f}% "
                "ระบบแสดงเฉพาะส่วนที่สรุปสำเร็จ กรุณาตรวจ Transcript สำหรับช่วงที่เหลือ"
            )
    elif terminal_status == "failed":
        user_warning = SUMMARY_GEMMA_EMPTY_WARNING

    metadata = {
        "version": "async-segment",
        "estimated_input_tokens": input_tokens,
        "chunk_count": len(chunks),
        "token_check": token_check,
        "extraction_call_count": len(records),
        "final_max_tokens": final_max_tokens,
        "fast_degrade_enabled": SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        "render_degraded": render_degraded,
        "render_outcome": render_outcome,
        "final_render_status": final_render_status,
        "reduce_degraded": bool(merged.get("reduce_degraded")),
        "reduce_skipped_for_speed": bool(merged.get("reduce_skipped_for_speed")),
        "coverage_complete": snapshot["coverage_complete"],
        "covered_segments": snapshot["covered_segments"],
        "covered_segment_ids": snapshot["covered_segment_ids"],
        "total_segments": len(expected_ids),
        "coverage_percentage": snapshot["coverage_percentage"],
        "last_covered_segment": snapshot["last_covered_segment"],
        "completed_chunks": snapshot["completed_chunks"],
        "failed_chunks": failed_chunks,
        "partial_chunks": partial_chunks,
        "recovered_failed_chunks": snapshot["recovered_failed_chunks"],
        "recovered_partial_chunks": snapshot["recovered_partial_chunks"],
        "failed_ranges": failed_ranges,
        "extraction_complete": snapshot["coverage_complete"],
        "degraded": degraded,
        "summary_status": terminal_status,
        "is_partial_summary": is_partial_summary,
        "stop_reason": stop_reason,
        "summary_started_at": budget.started_at.isoformat(),
        "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
        "summary_time_limit_seconds": budget.total_seconds,
        "evidence_counts": {field: len(merged.get(field, [])) for field in CRITICAL_FIELDS},
        "evidence_index": _evidence_index(merged),
        "retry_policy": {
            "max_retry_depth": SUMMARY_ASYNC_MAX_RETRY_DEPTH,
            "min_chunk_tokens": SUMMARY_ASYNC_MIN_CHUNK_TOKENS,
        },
    }
    if user_warning:
        metadata["user_warning"] = user_warning
    if final_render_status == "deterministic_fallback":
        metadata["fallback_strategy"] = "deterministic_structured_records"
    elif terminal_status == "failed":
        metadata["fallback_strategy"] = "transcript_only_no_structured_summary"
    return summary, metadata, terminal_status


def _complete_job(
    db,
    job_id: str,
    run_id: str,
    artifact: dict,
    summary: str,
    summary_metadata: dict,
    terminal_status: str,
    finished_at: datetime,
):
    _ensure_not_cancelled(db, job_id)
    existing_job = db.job.find_one(
        {"_id": ObjectId(job_id)},
        {"result_available": 1, "result": 1, "session_id": 1, "status": 1},
    )
    if existing_job and existing_job.get("result_available") and existing_job.get("session_id"):
        return str(existing_job["session_id"]), existing_job.get("result") or {}

    now = _utcnow()
    active_state = db.summary_state.find_one(
        {
            "job_id": job_id,
            "active_run_id": run_id,
            "lease_expires_at": {"$gt": now},
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
        },
        {"_id": 1},
    )
    if not active_state:
        raise SummaryLockLost("summary_final_commit_state_superseded")

    # Hold a short durable commit lease across the standalone-Mongo
    # session->job boundary.  A new summary run cannot rotate ownership while
    # this finalizer is writing; after a crash the lease expires and a retry
    # deterministically overwrites the one session row for this job.
    commit_lease_expires_at = now + timedelta(minutes=2)
    commit_token = uuid.uuid4().hex
    commit_claim = db.job.find_one_and_update(
        {
            "_id": ObjectId(job_id),
            "summary_active_run_id": run_id,
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
            "$or": [
                {"summary_commit_state": {"$ne": "committing"}},
                {"summary_commit_run_id": run_id},
                {"summary_commit_lease_expires_at": {"$lte": now}},
            ],
        },
        {"$set": {
            "summary_commit_state": "committing",
            "summary_commit_run_id": run_id,
            "summary_commit_token": commit_token,
            "summary_commit_lease_expires_at": commit_lease_expires_at,
        }, "$inc": {"summary_commit_generation": 1}},
        projection={"summary_commit_generation": 1},
        return_document=ReturnDocument.AFTER,
    )
    if not commit_claim:
        raise SummaryLockLost("summary_final_commit_job_superseded")
    commit_generation = int(commit_claim.get("summary_commit_generation") or 0)
    if not db.summary_state.find_one(
        {"job_id": job_id, "active_run_id": run_id, "lease_expires_at": {"$gt": _utcnow()}},
        {"_id": 1},
    ):
        raise SummaryLockLost("summary_final_commit_lease_lost")

    original_filename = artifact["original_filename"]
    meeting_type_id = int(artifact.get("meeting_type_id") or 0)
    meeting_type_info = MEETING_TYPES.get(meeting_type_id, {})
    processing_time = dict(artifact.get("processing_time") or {})
    processing_time["summarization"] = summary_metadata.get("summary_elapsed_seconds", 0)
    processing_time["total"] = float(processing_time.get("total") or 0) + float(processing_time["summarization"] or 0)
    full_transcript = artifact.get("full_transcript") or {}
    speaker_clips_response = artifact.get("speaker_clips_response") or {}
    clip_prefix = artifact.get("clip_prefix") or job_id
    agendas = artifact.get("agendas") or []

    session_created_at = _utcnow()
    session_doc = {
        "job_id": job_id,
        "user_id": ObjectId(artifact["user_id"]),
        "audio_file": original_filename,
        "audio_length_seconds": artifact["audio_length_seconds"],
        "meeting_type_id": meeting_type_id,
        "meeting_type_name": meeting_type_info.get("thai", "ตรวจจับอัตโนมัติ"),
        "summary": summary,
        "summary_metadata": summary_metadata,
        "summary_status": terminal_status,
        "is_partial_summary": terminal_status == "partially_completed",
        "coverage_percentage": summary_metadata.get("coverage_percentage", 0),
        "transcript": {
            "segments": full_transcript.get("segments", []),
            "combined_text": full_transcript.get("combined_text", ""),
            "speaker_summary": full_transcript.get("speaker_summary", {}),
        },
        "processing_time": processing_time,
        "segment_count": len(full_transcript.get("segments", [])),
        "speaker_count": len((full_transcript.get("speaker_summary") or {}).get("speaking_time", {})),
        "speaker_clips": speaker_clips_response,
        "clip_prefix": clip_prefix,
        "suggested_names": artifact.get("suggested_names", {}),
        "agendas": agendas,
        "detection_mode": artifact.get("detection_mode", "single_topic"),
        "detected_language": artifact.get("detected_language", "th"),
        "created_at": session_created_at,
        "clips_expires_at": session_created_at + timedelta(days=SPEAKER_CLIP_RETENTION_DAYS),
        "expires_at": session_created_at + timedelta(days=SESSION_RETENTION_DAYS),
        "retention": {
            "speaker_clips_days": SPEAKER_CLIP_RETENTION_DAYS,
            "session_days": SESSION_RETENTION_DAYS,
        },
        "summary_run_id": run_id,
        "summary_commit_token": commit_token,
        "summary_commit_generation": commit_generation,
    }
    _ensure_not_cancelled(db, job_id)
    # Revalidate immediately before touching History.  This catches an old
    # finalizer that paused beyond its commit lease while a newer run won.
    commit_owner = db.job.find_one(
        {
            "_id": ObjectId(job_id),
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
            "summary_active_run_id": run_id,
            "summary_commit_run_id": run_id,
            "summary_commit_token": commit_token,
            "summary_commit_generation": commit_generation,
            "summary_commit_state": "committing",
            "summary_commit_lease_expires_at": {"$gt": _utcnow()},
        },
        {"_id": 1},
    )
    if not commit_owner:
        raise SummaryLockLost("summary_session_commit_owner_superseded")
    mutable_session_doc = dict(session_doc)
    mutable_session_doc.pop("created_at", None)
    try:
        session_write = db.session.update_one(
            {
                "job_id": job_id,
                "$or": [
                    {"summary_commit_generation": {"$exists": False}},
                    {"summary_commit_generation": {"$lt": commit_generation}},
                    {
                        "summary_commit_generation": commit_generation,
                        "summary_commit_token": commit_token,
                    },
                ],
            },
            {
                "$set": mutable_session_doc,
                "$setOnInsert": {"created_at": session_created_at},
            },
            upsert=True,
        )
    except DuplicateKeyError as exc:
        raise SummaryLockLost("summary_session_generation_superseded") from exc
    if getattr(session_write, "matched_count", 0) == 0 and not getattr(
        session_write, "upserted_id", None
    ):
        raise SummaryLockLost("summary_session_generation_superseded")
    stored_session = db.session.find_one({"job_id": job_id}, {"_id": 1})
    if not stored_session:
        raise RuntimeError(f"Session upsert failed for job {job_id}")
    session_id = str(stored_session["_id"])

    job_result = {
        "success": terminal_status != "failed",
        "audio_file": original_filename,
        "audio_length_seconds": artifact["audio_length_seconds"],
        "processing_time": processing_time,
        "transcript": {
            "segments": full_transcript.get("segments", []),
            "combined_text": full_transcript.get("combined_text", ""),
            "speaker_summary": full_transcript.get("speaker_summary", {}),
        },
        "summary": summary,
        "summary_metadata": summary_metadata,
        "summary_status": terminal_status,
        "is_partial_summary": terminal_status == "partially_completed",
        "coverage_percentage": summary_metadata.get("coverage_percentage", 0),
        "speaker_clips": speaker_clips_response,
        "clip_prefix": clip_prefix,
        "suggested_names": artifact.get("suggested_names", {}),
        "agendas": agendas,
        "detection_mode": artifact.get("detection_mode", "single_topic"),
        "detected_language": artifact.get("detected_language", "th"),
        "clips_expires_at": session_doc["clips_expires_at"],
    }
    job_error = None
    if terminal_status == "failed":
        job_error = "Summary model did not produce a usable structured result"
    summary_started_at = summary_metadata.get("summary_started_at")
    if isinstance(summary_started_at, str):
        summary_started_at = datetime.fromisoformat(summary_started_at.replace("Z", "+00:00"))
    job_update = {
        "status": terminal_status,
        "current_step": "error" if terminal_status == "failed" else "done",
        "progress": 100,
        "summary_status": terminal_status,
        "is_partial_summary": terminal_status == "partially_completed",
        "summary_progress": 100,
        "summary_completed_chunks": len(summary_metadata.get("completed_chunks", [])),
        "summary_total_chunks": int(summary_metadata.get("chunk_count", 0) or 0),
        "summary_started_at": summary_started_at,
        "summary_finished_at": finished_at,
        "summary_elapsed_seconds": summary_metadata.get("summary_elapsed_seconds", 0),
        "summary_time_limit_seconds": summary_metadata.get("summary_time_limit_seconds", SUMMARY_TOTAL_TIMEOUT_SECONDS),
        "covered_segments": summary_metadata.get("covered_segments", 0),
        "total_segments": summary_metadata.get("total_segments", 0),
        "coverage_percentage": summary_metadata.get("coverage_percentage", 0),
        "completed_chunks": summary_metadata.get("completed_chunks", []),
        "partial_chunks": summary_metadata.get("partial_chunks", []),
        "failed_ranges": summary_metadata.get("failed_ranges", []),
        "last_covered_segment": summary_metadata.get("last_covered_segment"),
        "coverage_complete": summary_metadata.get("coverage_complete", False),
        "stop_reason": summary_metadata.get("stop_reason"),
        "final_render_status": summary_metadata.get("final_render_status"),
        "result_available": True,
        "result": job_result,
        "session_id": session_id,
        "error": job_error,
        "completed_at": finished_at,
        "artifact_cleanup_state": "pending",
        "artifact_cleanup_after": finished_at,
        "summary_commit_state": "completed",
        "summary_commit_run_id": run_id,
        "summary_commit_token": commit_token,
        "summary_commit_generation": commit_generation,
    }
    _ensure_not_cancelled(db, job_id)
    result = db.job.update_one(
        {
            "_id": ObjectId(job_id),
            "summary_active_run_id": run_id,
            "summary_commit_run_id": run_id,
            "summary_commit_token": commit_token,
            "summary_commit_generation": commit_generation,
            "summary_commit_state": "committing",
            "summary_commit_lease_expires_at": {"$gt": _utcnow()},
            "status": {"$ne": "cancelled"},
            "result_available": {"$ne": True},
        },
        {"$set": job_update},
    )
    if result.matched_count == 0:
        existing_job = db.job.find_one(
            {"_id": ObjectId(job_id)},
            {"result_available": 1, "result": 1, "session_id": 1, "status": 1},
        )
        if existing_job and existing_job.get("result_available"):
            return str(existing_job.get("session_id") or session_id), existing_job.get("result") or job_result
        if existing_job and existing_job.get("status") == "cancelled":
            db.session.delete_one({
                "job_id": job_id,
                "summary_commit_token": commit_token,
                "summary_commit_generation": commit_generation,
            })
            raise JobCancelled(f"Job {job_id} was cancelled before final commit")
        db.session.delete_one({
            "job_id": job_id,
            "summary_commit_token": commit_token,
            "summary_commit_generation": commit_generation,
        })
        raise SummaryLockLost("job_terminal_compare_and_set_failed")
    settle_job_quota_db(db, job_id, "consumed")
    return session_id, job_result


def _enqueue_post_commit(db, job_id: str, artifact: dict, job_result: dict) -> None:
    """Schedule non-critical outbox/storage work without rolling back the result."""
    from app.tasks.maintenance import cleanup_job_storage, enqueue_result_email

    try:
        _ensure_not_cancelled(db, job_id)
    except JobCancelled:
        return
    email_recipient = artifact.get("email_recipient")
    if email_recipient:
        try:
            enqueue_result_email(
                db=db,
                job_id=job_id,
                recipient=email_recipient,
                result_payload=job_result,
                meeting_type_id=int(artifact.get("meeting_type_id") or 0),
                original_filename=artifact["original_filename"],
            )
        except Exception:
            logger.exception("Job {}: could not enqueue result email outbox", job_id)
    try:
        cleanup_job_storage.apply_async(args=[job_id], queue="maintenance")
    except Exception:
        logger.exception("Job {}: could not enqueue artifact cleanup", job_id)


@celery_app.task(bind=True, name="summary.finalize")
def finalize(self, job_id: str):
    """Render final summary and complete the job document/session."""
    db = get_worker_db()
    try:
        lock_client, lock_token = _acquire_lock(job_id)
    except SummaryLockUnavailable as exc:
        raise self.retry(
            exc=exc,
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    if lock_token is None:
        raise self.retry(
            exc=SummaryLockUnavailable("summary_lock_busy"),
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    storage = _get_storage()

    session_id = None
    artifact = None
    run_id = None
    try:
        _ensure_not_cancelled(db, job_id)
        collection = _summary_state_collection(db)
        state = collection.find_one({"job_id": job_id})
        if not state:
            raise RuntimeError("Missing summary_state for finalize")
        recovered_terminal = _recover_terminal_job_checkpoint(db, storage, job_id, state)
        if recovered_terminal:
            return recovered_terminal
        if state.get("status") in {"completed", "partially_completed", "failed", "cancelled"}:
            return {"job_id": job_id, "status": state.get("status")}

        state = _claim_summary_run(db, collection, job_id, lock_token)
        run_id = lock_token
        state, budget = _ensure_summary_budget(collection, job_id, run_id)

        artifact = _load_artifact(storage, state["artifact_object"])
        segments = _source_segments(artifact)
        chunks = chunk_segments(segments)
        records = list(state.get("records") or [])

        _update_job(db, job_id, {
            "current_step": "summary_finalizing",
            "progress": 96,
            "summary_status": "finalizing",
            "summary_progress": 100 if chunks else 0,
            "summary_celery_task_id": self.request.id,
            "summary_started_at": budget.started_at,
            "summary_elapsed_seconds": round(budget.elapsed_seconds(), 3),
            "summary_time_limit_seconds": budget.total_seconds,
        }, run_id=run_id)
        _fenced_state_update(
            collection,
            job_id,
            run_id,
            {"$set": {"status": "finalizing", "updated_at": _utcnow()}},
            reason="summary_finalize_started",
        )

        summary, metadata, terminal_status = _final_metadata(
            db,
            artifact,
            state,
            records,
            chunks,
            segments,
            _llm_call(db, job_id, budget, "finalization", lock_client, lock_token),
            budget,
        )
        finished_at = _utcnow()
        metadata["summary_finished_at"] = finished_at.isoformat()
        metadata["summary_elapsed_seconds"] = round(budget.elapsed_seconds(finished_at), 3)
        metadata = {
            **metadata,
            "pipeline_mode": "async",
            "meeting_style_id": artifact.get("effective_meeting_type_id"),
            "meeting_style_source": artifact.get("meeting_style_source"),
            "meeting_style_key": (artifact.get("meeting_style_classification") or {}).get("meeting_type", "general_meeting"),
            "agenda_detection_mode": artifact.get("detection_mode", "single_topic"),
            "agenda_count": len(artifact.get("agendas") or []),
            "agenda_split_reasons": (artifact.get("summary_metadata") or {}).get("agenda_split_reasons", []),
        }

        if not _renew_summary_lease(db, job_id, lock_client, run_id):
            raise SummaryLockLost("summary_lock_lost_before_commit")
        session_id, job_result = _complete_job(
            db,
            job_id,
            run_id,
            artifact,
            summary,
            metadata,
            terminal_status,
            finished_at,
        )
        terminal_result = collection.update_one(
            _state_query(job_id, run_id),
            {"$set": {
                "status": terminal_status,
                "final_summary": summary,
                "coverage_complete": metadata.get("coverage_complete", False),
                "covered_segments": metadata.get("covered_segments", 0),
                "covered_segment_ids": metadata.get("covered_segment_ids", []),
                "coverage_percentage": metadata.get("coverage_percentage", 0),
                "last_covered_segment": metadata.get("last_covered_segment"),
                "completed_chunks": metadata.get("completed_chunks", []),
                "partial_chunks": metadata.get("partial_chunks", []),
                "failed_ranges": metadata.get("failed_ranges", []),
                "stop_reason": metadata.get("stop_reason"),
                "final_render_status": metadata.get("final_render_status"),
                "summary_finished_at": finished_at,
                "summary_elapsed_seconds": metadata.get("summary_elapsed_seconds", 0),
                "result_available": True,
                "updated_at": finished_at,
            }},
        )
        if getattr(terminal_result, "matched_count", 0) == 0:
            raise SummaryLockLost("summary_terminal_checkpoint_superseded")
        logger.info(
            "Job {}: async summary finished status={} stop_reason={} coverage={}/{} ({:.1f}%) "
            "final_render={} elapsed={:.1f}s",
            job_id,
            terminal_status,
            metadata.get("stop_reason"),
            metadata.get("covered_segments", 0),
            metadata.get("total_segments", 0),
            metadata.get("coverage_percentage", 0),
            metadata.get("final_render_status"),
            metadata.get("summary_elapsed_seconds", 0),
        )

        _enqueue_post_commit(db, job_id, artifact, job_result)
        return {
            "job_id": job_id,
            "session_id": session_id,
            "status": terminal_status,
            "coverage_percentage": metadata.get("coverage_percentage", 0),
        }
    except SummaryLockLost as exc:
        logger.warning("Job {}: finalizer lease lost; retrying safely: {}", job_id, exc)
        raise self.retry(
            exc=exc,
            countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
            max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
        )
    except JobCancelled as exc:
        logger.info("Job {}: async summary finalize stopped after cancellation: {}", job_id, exc)
        if session_id:
            try:
                db.session.delete_one({"_id": ObjectId(session_id)})
            except Exception:
                pass
        if run_id:
            cancel_result = _summary_state_collection(db).update_one(
                _state_query(job_id, run_id),
                {"$set": {
                    "status": "cancelled",
                    "stop_reason": "cancelled",
                    "summary_finished_at": _utcnow(),
                    "updated_at": _utcnow(),
                }},
            )
            if getattr(cancel_result, "matched_count", 0) == 0:
                logger.info("Job {}: stale finalizer skipped cancellation checkpoint", job_id)
        _update_job(db, job_id, {
            "status": "cancelled",
            "current_step": "cancelled",
            "summary_status": "cancelled",
            "cancellation_state": "requested",
            "cancellation_cleanup_status": "pending",
            "email_status": "cancelled",
            "stop_reason": "cancelled",
            "result_available": False,
            "error": "Cancelled by admin",
            "cancelled_at": _utcnow(),
            "completed_at": _utcnow(),
        }, allow_cancelled=True)
        _enqueue_cancel_cleanup(job_id)
        return {"job_id": job_id, "cancelled": True}
    except Exception as exc:
        logger.exception("Job {}: async summary finalize failed", job_id)
        if not run_id:
            raise self.retry(
                exc=SummaryLockLost("summary_finalize_error_before_run_claim"),
                countdown=SUMMARY_ASYNC_LOCK_RETRY_SECONDS,
                max_retries=SUMMARY_ASYNC_LOCK_MAX_RETRIES,
            )
        existing_job = db.job.find_one({"_id": ObjectId(job_id)}, {"result_available": 1})
        if existing_job and existing_job.get("result_available"):
            return {"job_id": job_id, "status": "failed", "result_available": True}

        if artifact is not None:
            finished_at = _utcnow()
            try:
                state = (
                    _summary_state_collection(db).find_one(_state_query(job_id, run_id))
                    if run_id else None
                ) or {}
                if not state:
                    raise SummaryLockLost("summary_failure_fallback_superseded")
                budget = SummaryBudget.from_state(state) if state.get("summary_started_at") else SummaryBudget(finished_at)
                segments = _source_segments(artifact)
                chunks = chunk_segments(segments)
                expected_ids = _expected_segment_ids(segments)
                snapshot = _completion_snapshot(list(state.get("records") or []), chunks, expected_ids)
                metadata = {
                    "version": "async-segment",
                    "pipeline_mode": "async",
                    "summary_status": "failed",
                    "is_partial_summary": False,
                    "coverage_complete": snapshot["coverage_complete"],
                    "covered_segments": snapshot["covered_segments"],
                    "covered_segment_ids": snapshot["covered_segment_ids"],
                    "total_segments": len(expected_ids),
                    "coverage_percentage": snapshot["coverage_percentage"],
                    "last_covered_segment": snapshot["last_covered_segment"],
                    "completed_chunks": snapshot["completed_chunks"],
                    "partial_chunks": snapshot["partial_chunks"],
                    "failed_ranges": _uncovered_ranges(chunks, snapshot["covered_ids"], "fatal_error"),
                    "stop_reason": "fatal_error",
                    "final_render_status": "unavailable",
                    "summary_started_at": budget.started_at.isoformat(),
                    "summary_finished_at": finished_at.isoformat(),
                    "summary_elapsed_seconds": round(budget.elapsed_seconds(finished_at), 3),
                    "summary_time_limit_seconds": budget.total_seconds,
                    "user_warning": SUMMARY_GEMMA_EMPTY_WARNING,
                    "fallback_strategy": "transcript_only_fatal_summary_error",
                    "error": str(exc),
                }
                if not _renew_summary_lease(db, job_id, lock_client, run_id):
                    raise SummaryLockLost("summary_lock_lost_before_failure_commit")
                session_id, job_result = _complete_job(
                    db,
                    job_id,
                    run_id,
                    artifact,
                    "",
                    metadata,
                    "failed",
                    finished_at,
                )
                _fenced_state_update(
                    _summary_state_collection(db),
                    job_id,
                    run_id,
                    {"$set": {
                        "status": "failed",
                        "stop_reason": "fatal_error",
                        "final_render_status": "unavailable",
                        "summary_finished_at": finished_at,
                        "summary_elapsed_seconds": metadata["summary_elapsed_seconds"],
                        "result_available": True,
                        "error": str(exc),
                        "updated_at": finished_at,
                    }},
                    reason="summary_failure_result",
                )
                _enqueue_post_commit(db, job_id, artifact, job_result)
                return {"job_id": job_id, "status": "failed", "result_available": True}
            except (SummaryLockLost, JobCancelled):
                raise
            except Exception:
                logger.exception("Job {}: transcript-only failure result could not be saved", job_id)

        if run_id:
            failed_result = _summary_state_collection(db).update_one(
                _state_query(job_id, run_id),
                {"$set": {
                    "status": "failed",
                    "stop_reason": "fatal_error",
                    "final_render_status": "unavailable",
                    "error": str(exc),
                    "updated_at": _utcnow(),
                }},
            )
            if getattr(failed_result, "matched_count", 0) == 0:
                raise SummaryLockLost("summary_failure_checkpoint_superseded")
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "summary_status": "failed",
            "progress": 100,
            "result_available": False,
            "stop_reason": "fatal_error",
            "error": str(exc),
            "completed_at": _utcnow(),
        }, run_id=run_id)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job {}: quota refund failed after summary failure: {}", job_id, refund_exc)
        return {"job_id": job_id, "failed": True, "result_available": False}
    finally:
        _release_lock(job_id, lock_client, lock_token)
