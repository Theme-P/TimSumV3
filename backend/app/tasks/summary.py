"""Celery tasks for segment-based async meeting summarization."""

from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from bson import ObjectId
from loguru import logger

from app.celery_app import celery_app
from app.models.meeting import MEETING_TYPES
from app.services.cancellation import JobCancelled
from app.services.db import get_worker_db
from app.services.storage import BUCKET_ARTIFACTS, StorageService
from app.services.summarizer import _call_llm_with_fallback, _get_template_for_meeting
from app.services.summary_pipeline import (
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
    reduce_records,
    render_record,
    transcript_fallback_text,
    _evidence_index,
    _record_has_content,
    _split_failed_chunk,
)


SUMMARY_ASYNC_MAX_RETRY_DEPTH = int(os.getenv("SUMMARY_ASYNC_MAX_RETRY_DEPTH", "2"))
SUMMARY_ASYNC_MIN_CHUNK_TOKENS = int(os.getenv("SUMMARY_ASYNC_MIN_CHUNK_TOKENS", "1000"))
SUMMARY_ASYNC_LOCK_TTL_SECONDS = int(os.getenv("SUMMARY_ASYNC_LOCK_TTL_SECONDS", "600"))

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
            _SUMMARY_INDEX_READY = True
        except Exception as exc:
            logger.warning("Could not ensure summary_state indexes: %s", exc)
    return collection


def _is_job_cancelled(db, job_id: str) -> bool:
    try:
        doc = db.job.find_one({"_id": ObjectId(job_id)}, {"status": 1})
    except Exception:
        return False
    return bool(doc and doc.get("status") == "cancelled")


def _ensure_not_cancelled(db, job_id: str):
    if _is_job_cancelled(db, job_id):
        raise JobCancelled(f"Job {job_id} was cancelled")


def _update_job(db, job_id: str, update: dict, *, allow_cancelled: bool = False):
    query = {"_id": ObjectId(job_id)}
    if not allow_cancelled:
        query["status"] = {"$ne": "cancelled"}
    return db.job.update_one(query, {"$set": update})


def _refund_job_quota_once(db, job_id: str) -> bool:
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
        logger.warning("Job %s: summary Redis lock unavailable, continuing unlocked: %s", job_id, exc)
        return None, token
    if not acquired:
        logger.info("Job %s: another summary worker owns the lock", job_id)
        return client, None
    return client, token


def _release_lock(job_id: str, client, token: Optional[str]):
    if not client or not token:
        return
    key = f"summary:{job_id}:lock"
    try:
        if client.get(key) == token:
            client.delete(key)
    except Exception as exc:
        logger.warning("Job %s: could not release summary lock: %s", job_id, exc)


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


def _failed_ranges(chunks: list[dict], failed_chunks: list[int], covered_ids: set[int]) -> list[dict]:
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
        })
    return ranges


def _completion_snapshot(
    records: list[dict],
    chunks: list[dict],
    expected_ids: set[int],
) -> dict:
    merged = deterministic_merge(records) if records else normalize_record({})
    covered_ids = {int(item) for item in merged.get("coverage", [])}
    completed_chunks = [
        chunk["chunk_number"]
        for chunk in chunks
        if set(chunk.get("segment_ids") or []).issubset(covered_ids)
    ]
    failed_ranges = _failed_ranges(chunks, merged.get("failed_chunks", []), covered_ids)
    return {
        "merged": merged,
        "covered_ids": covered_ids,
        "completed_chunks": completed_chunks,
        "partial_chunks": sorted(set(int(item) for item in merged.get("partial_chunks", []))),
        "failed_ranges": failed_ranges,
        "covered_segments": len(expected_ids & covered_ids),
        "total_segments": len(expected_ids),
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
        "status": "queued",
        "artifact_object": artifact_object,
        "total_segments": len(expected_ids),
        "covered_segments": 0,
        "total_chunks": len(chunks),
        "chunk_ranges": _chunk_ranges(chunks),
        "completed_chunks": [],
        "partial_chunks": [],
        "failed_ranges": [],
        "attempts": {},
        "records": [],
        "rolling_memory": None,
        "next_chunk_index": 0,
        "coverage_complete": False,
        "final_summary": None,
        "created_at": _utcnow(),
        "updated_at": _utcnow(),
    }
    collection = _summary_state_collection(db)
    collection.update_one(
        {"job_id": job_id},
        {"$setOnInsert": state},
        upsert=True,
    )
    return collection.find_one({"job_id": job_id}) or state


def _load_artifact(storage: StorageService, object_name: str) -> dict:
    return storage.get_json(BUCKET_ARTIFACTS, object_name)


def _template_prompt(meeting_type_id: int, db) -> str:
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=db)
    return str(template_data.get("system_prompt") or "")


def _llm_call(db, job_id: str):
    def call(system_prompt, user_prompt, **kwargs):
        _ensure_not_cancelled(db, job_id)
        return _call_llm_with_fallback(
            system_prompt,
            user_prompt,
            mongo_service=db,
            cancel_check=lambda: _ensure_not_cancelled(db, job_id),
            **kwargs,
        )

    return call


def _process_chunk_balanced(
    chunk: dict,
    previous_record: Optional[dict],
    llm_call,
    scope_label: str,
    custom_prompt: str,
    template_prompt: str,
    depth: int = 0,
) -> list[dict]:
    can_split = (
        depth < SUMMARY_ASYNC_MAX_RETRY_DEPTH
        and int(chunk.get("estimated_tokens") or 0) > SUMMARY_ASYNC_MIN_CHUNK_TOKENS
        and bool(chunk.get("_segments"))
    )
    record = extract_chunk_record(
        chunk,
        previous_record,
        llm_call,
        scope_label,
        custom_prompt,
        template_prompt=template_prompt,
        primary_only=can_split or SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    )
    if not record.get("failed_chunks"):
        return [record]

    records: list[dict] = []
    if record.get("partial_chunks") and str(record.get("summary") or "").strip():
        partial_record = dict(record)
        partial_record["coverage"] = []
        partial_record["failed_chunks"] = []
        partial_record["partial_only"] = True
        records.append(partial_record)

    if not can_split:
        return records or [record]

    target_tokens = max(
        SUMMARY_ASYNC_MIN_CHUNK_TOKENS,
        int(chunk.get("estimated_tokens") or SUMMARY_ASYNC_MIN_CHUNK_TOKENS) // 2,
    )
    children = _split_failed_chunk(chunk, target_tokens)
    if len(children) <= 1:
        return records or [record]

    logger.warning(
        "Async summary split chunk %s into %s parts at target_tokens=%s depth=%s",
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
        )
        records.extend(child_records)
        successful = [item for item in child_records if not item.get("failed_chunks")]
        if successful:
            context = deterministic_merge(([context] if context else []) + successful)
    return records or [record]


@celery_app.task(bind=True, name="summary.process_next_chunk")
def process_next_chunk(self, job_id: str):
    """Process one root transcript chunk and enqueue the next chunk/finalizer."""
    db = get_worker_db()
    storage = _get_storage()
    lock_client, lock_token = _acquire_lock(job_id)
    if lock_client is not None and lock_token is None:
        return {"job_id": job_id, "skipped": "duplicate_locked"}

    enqueue_next = False
    enqueue_finalize = False
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

        if state.get("status") in {"completed", "cancelled"}:
            return {"job_id": job_id, "status": state.get("status")}

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

        chunk = chunks[next_index]
        records = list(state.get("records") or [])
        previous = state.get("rolling_memory")
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
        })

        logger.info(
            "Job %s: async summary chunk %s/%s S%s-S%s estimated_tokens=%s",
            job_id,
            chunk["chunk_number"],
            len(chunks),
            chunk["start_segment_idx"],
            chunk["end_segment_idx"],
            chunk["estimated_tokens"],
        )
        chunk_records = _process_chunk_balanced(
            chunk,
            previous,
            _llm_call(db, job_id),
            "การประชุม",
            custom_prompt,
            template_prompt,
        )
        records.extend(chunk_records)
        successful = [record for record in chunk_records if not record.get("failed_chunks")]
        rolling_memory = previous
        if successful:
            rolling_memory = deterministic_merge(([previous] if previous else []) + successful)

        attempts = dict(state.get("attempts") or {})
        attempts[str(chunk["chunk_number"])] = int(attempts.get(str(chunk["chunk_number"]), 0)) + 1
        next_index += 1
        snapshot = _completion_snapshot(records, chunks, expected_ids)
        summary_percent = int((next_index / max(len(chunks), 1)) * 100)
        collection.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "running",
                "total_segments": len(expected_ids),
                "covered_segments": snapshot["covered_segments"],
                "total_chunks": len(chunks),
                "chunk_ranges": _chunk_ranges(chunks),
                "completed_chunks": snapshot["completed_chunks"],
                "partial_chunks": snapshot["partial_chunks"],
                "failed_ranges": snapshot["failed_ranges"],
                "attempts": attempts,
                "records": records,
                "rolling_memory": rolling_memory,
                "next_chunk_index": next_index,
                "coverage_complete": snapshot["coverage_complete"],
                "updated_at": _utcnow(),
            }},
        )
        _update_job(db, job_id, {
            "current_step": "summarizing_chunk",
            "progress": min(94, 75 + int(summary_percent * 0.19)),
            "summary_status": "running",
            "summary_progress": summary_percent,
            "summary_completed_chunks": len(snapshot["completed_chunks"]),
            "summary_total_chunks": len(chunks),
            "covered_segments": snapshot["covered_segments"],
            "total_segments": len(expected_ids),
            "partial_chunks": snapshot["partial_chunks"],
            "coverage_complete": snapshot["coverage_complete"],
        })

        enqueue_finalize = next_index >= len(chunks)
        enqueue_next = not enqueue_finalize
        return {
            "job_id": job_id,
            "processed_chunk": chunk["chunk_number"],
            "next_chunk_index": next_index,
            "summary_finalizing": enqueue_finalize,
        }
    except JobCancelled as exc:
        logger.info("Job %s: async summary stopped after cancellation: %s", job_id, exc)
        _summary_state_collection(db).update_one(
            {"job_id": job_id},
            {"$set": {"status": "cancelled", "updated_at": _utcnow()}},
            upsert=True,
        )
        _update_job(db, job_id, {
            "status": "cancelled",
            "current_step": "cancelled",
            "summary_status": "cancelled",
            "error": "Cancelled by admin",
            "cancelled_at": _utcnow(),
            "completed_at": _utcnow(),
        }, allow_cancelled=True)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job %s: quota refund failed after summary cancellation: %s", job_id, refund_exc)
        return {"job_id": job_id, "cancelled": True}
    except Exception as exc:
        logger.exception("Job %s: async summary chunk failed", job_id)
        _summary_state_collection(db).update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": str(exc), "updated_at": _utcnow()}},
            upsert=True,
        )
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "summary_status": "failed",
            "progress": 0,
            "error": str(exc),
            "completed_at": _utcnow(),
        })
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job %s: quota refund failed after summary failure: %s", job_id, refund_exc)
        raise
    finally:
        _release_lock(job_id, lock_client, lock_token)
        if enqueue_next:
            process_next_chunk.apply_async(args=[job_id], queue="summary", countdown=1)
        elif enqueue_finalize:
            finalize.apply_async(args=[job_id], queue="summary", countdown=1)


def _final_metadata(
    artifact: dict,
    state: dict,
    records: list[dict],
    chunks: list[dict],
    segments: list[dict],
    llm_call,
    summary_seconds: float,
) -> tuple[str, dict]:
    transcript = _transcript_text(artifact)
    input_tokens = estimate_tokens(transcript)
    expected_ids = _expected_segment_ids(segments)
    token_check = build_token_check(input_tokens, len(chunks))

    if all(record.get("failed_chunks") for record in records) and not any(
        _record_has_content(record) for record in records
    ):
        summary = append_user_warning(transcript_fallback_text(segments), SUMMARY_GEMMA_EMPTY_WARNING)
        return summary, {
            "version": "async-segment",
            "estimated_input_tokens": input_tokens,
            "chunk_count": len(chunks),
            "token_check": token_check,
            "coverage_complete": False,
            "covered_segments": 0,
            "total_segments": len(expected_ids),
            "completed_chunks": [],
            "failed_chunks": [chunk["chunk_number"] for chunk in chunks],
            "partial_chunks": [],
            "failed_ranges": _chunk_ranges(chunks),
            "extraction_complete": False,
            "degraded": True,
            "user_warning": SUMMARY_GEMMA_EMPTY_WARNING,
            "fallback_strategy": "transcript_fallback_no_gemma_chunks_completed",
            "summary_elapsed_seconds": summary_seconds,
        }

    merged = reduce_records(records, llm_call, "การประชุม")
    template_prompt = _template_prompt(
        int(artifact.get("effective_meeting_type_id") or artifact.get("meeting_type_id") or 0),
        get_worker_db(),
    )
    summary, final_max_tokens, render_degraded = render_record(
        merged,
        llm_call,
        int(artifact.get("effective_meeting_type_id") or artifact.get("meeting_type_id") or 0),
        template_prompt,
        str(artifact.get("custom_prompt") or ""),
        "การประชุม",
        input_tokens,
    )
    snapshot = _completion_snapshot(records, chunks, expected_ids)
    failed_chunks = merged.get("failed_chunks", [])
    partial_chunks = snapshot["partial_chunks"]
    degraded = bool(
        not snapshot["coverage_complete"]
        or partial_chunks
        or failed_chunks
        or merged.get("reduce_degraded")
        or render_degraded
    )
    metadata = {
        "version": "async-segment",
        "estimated_input_tokens": input_tokens,
        "chunk_count": len(chunks),
        "token_check": token_check,
        "extraction_call_count": len(records),
        "final_max_tokens": final_max_tokens,
        "fast_degrade_enabled": SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        "render_degraded": render_degraded,
        "reduce_degraded": bool(merged.get("reduce_degraded")),
        "reduce_skipped_for_speed": bool(merged.get("reduce_skipped_for_speed")),
        "coverage_complete": snapshot["coverage_complete"],
        "covered_segments": snapshot["covered_segments"],
        "total_segments": len(expected_ids),
        "completed_chunks": snapshot["completed_chunks"],
        "failed_chunks": failed_chunks,
        "partial_chunks": partial_chunks,
        "failed_ranges": snapshot["failed_ranges"],
        "extraction_complete": not failed_chunks,
        "degraded": degraded,
        "evidence_counts": {field: len(merged.get(field, [])) for field in CRITICAL_FIELDS},
        "evidence_index": _evidence_index(merged),
        "summary_elapsed_seconds": summary_seconds,
        "retry_policy": {
            "max_retry_depth": SUMMARY_ASYNC_MAX_RETRY_DEPTH,
            "min_chunk_tokens": SUMMARY_ASYNC_MIN_CHUNK_TOKENS,
        },
    }
    if degraded:
        metadata["user_warning"] = SUMMARY_GEMMA_PARTIAL_WARNING
        metadata["fallback_strategy"] = "gemma_partial_summary"
        summary = append_user_warning(summary, SUMMARY_GEMMA_PARTIAL_WARNING)
    return summary, metadata


def _complete_job(db, job_id: str, artifact: dict, summary: str, summary_metadata: dict):
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

    session_doc = {
        "user_id": ObjectId(artifact["user_id"]),
        "audio_file": original_filename,
        "audio_length_seconds": artifact["audio_length_seconds"],
        "meeting_type_id": meeting_type_id,
        "meeting_type_name": meeting_type_info.get("thai", "ตรวจจับอัตโนมัติ"),
        "summary": summary,
        "summary_metadata": summary_metadata,
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
        "created_at": _utcnow(),
    }
    session_result = db.session.insert_one(session_doc)
    session_id = str(session_result.inserted_id)

    job_result = {
        "success": True,
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
        "speaker_clips": speaker_clips_response,
        "clip_prefix": clip_prefix,
        "suggested_names": artifact.get("suggested_names", {}),
        "agendas": agendas,
        "detection_mode": artifact.get("detection_mode", "single_topic"),
        "detected_language": artifact.get("detected_language", "th"),
    }
    _update_job(db, job_id, {
        "status": "completed",
        "current_step": "done",
        "progress": 100,
        "summary_status": "completed",
        "summary_progress": 100,
        "result": job_result,
        "session_id": session_id,
        "completed_at": _utcnow(),
    })
    return session_id, job_result


@celery_app.task(bind=True, name="summary.finalize")
def finalize(self, job_id: str):
    """Render final summary and complete the job document/session."""
    db = get_worker_db()
    storage = _get_storage()
    lock_client, lock_token = _acquire_lock(job_id)
    if lock_client is not None and lock_token is None:
        return {"job_id": job_id, "skipped": "duplicate_locked"}

    session_id = None
    try:
        _ensure_not_cancelled(db, job_id)
        collection = _summary_state_collection(db)
        state = collection.find_one({"job_id": job_id})
        if not state:
            raise RuntimeError("Missing summary_state for finalize")
        if state.get("status") == "completed":
            return {"job_id": job_id, "status": "completed"}

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
        })
        collection.update_one(
            {"job_id": job_id},
            {"$set": {"status": "finalizing", "updated_at": _utcnow()}},
        )

        start = time.time()
        summary, metadata = _final_metadata(
            artifact,
            state,
            records,
            chunks,
            segments,
            _llm_call(db, job_id),
            0,
        )
        metadata["summary_elapsed_seconds"] = time.time() - start
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

        session_id, job_result = _complete_job(db, job_id, artifact, summary, metadata)
        collection.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "completed",
                "final_summary": summary,
                "coverage_complete": metadata.get("coverage_complete", False),
                "covered_segments": metadata.get("covered_segments", 0),
                "partial_chunks": metadata.get("partial_chunks", []),
                "failed_ranges": metadata.get("failed_ranges", []),
                "updated_at": _utcnow(),
            }},
        )
        logger.info("Job %s: async summary completed", job_id)

        email_recipient = artifact.get("email_recipient")
        if email_recipient:
            from app.tasks.transcription import _auto_send_result_email

            _auto_send_result_email(
                db=db,
                job_id=job_id,
                recipient=email_recipient,
                result_payload=job_result,
                meeting_type_id=int(artifact.get("meeting_type_id") or 0),
                original_filename=artifact["original_filename"],
            )
        return {"job_id": job_id, "session_id": session_id}
    except JobCancelled as exc:
        logger.info("Job %s: async summary finalize stopped after cancellation: %s", job_id, exc)
        if session_id:
            try:
                db.session.delete_one({"_id": ObjectId(session_id)})
            except Exception:
                pass
        _summary_state_collection(db).update_one(
            {"job_id": job_id},
            {"$set": {"status": "cancelled", "updated_at": _utcnow()}},
            upsert=True,
        )
        _update_job(db, job_id, {
            "status": "cancelled",
            "current_step": "cancelled",
            "summary_status": "cancelled",
            "error": "Cancelled by admin",
            "cancelled_at": _utcnow(),
            "completed_at": _utcnow(),
        }, allow_cancelled=True)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job %s: quota refund failed after summary cancellation: %s", job_id, refund_exc)
        return {"job_id": job_id, "cancelled": True}
    except Exception as exc:
        logger.exception("Job %s: async summary finalize failed", job_id)
        if session_id:
            try:
                db.session.delete_one({"_id": ObjectId(session_id)})
            except Exception:
                pass
        _summary_state_collection(db).update_one(
            {"job_id": job_id},
            {"$set": {"status": "failed", "error": str(exc), "updated_at": _utcnow()}},
            upsert=True,
        )
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "summary_status": "failed",
            "progress": 0,
            "error": str(exc),
            "completed_at": _utcnow(),
        })
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning("Job %s: quota refund failed after summary failure: %s", job_id, refund_exc)
        raise
    finally:
        _release_lock(job_id, lock_client, lock_token)
