"""Durable maintenance tasks for workflow reconciliation and data lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from loguru import logger
from pymongo import ReturnDocument

from app.celery_app import celery_app
from app.services.db import get_worker_db
from app.services.mongo import (
    JOB_RETENTION_DAYS,
    _reservation_key,
    settle_job_quota_db,
    usage_period,
)
from app.services.storage import (
    BUCKET_ARTIFACTS,
    BUCKET_AUDIO,
    BUCKET_CLIPS,
    BUCKET_VOICE_SAMPLES,
    StorageService,
)


TERMINAL_JOB_STATUSES = {"completed", "partially_completed", "failed", "cancelled"}
DELETION_MAX_RETRIES = int(os.getenv("DELETION_MAX_RETRIES", "8"))
DELETION_GRACE_HOURS = int(os.getenv("DELETION_GRACE_HOURS", "24"))
RECONCILE_BATCH_SIZE = int(os.getenv("MAINTENANCE_RECONCILE_BATCH_SIZE", "100"))
EMAIL_OUTBOX_RETENTION_DAYS = int(os.getenv("EMAIL_OUTBOX_RETENTION_DAYS", "30"))
EMAIL_OUTBOX_MAX_ATTEMPTS = int(os.getenv("EMAIL_OUTBOX_MAX_ATTEMPTS", "5"))
PUBLISH_RECOVERY_GRACE_SECONDS = int(os.getenv("PUBLISH_RECOVERY_GRACE_SECONDS", "900"))
SUMMARY_PUBLISH_RECOVERY_GRACE_SECONDS = int(os.getenv("SUMMARY_PUBLISH_RECOVERY_GRACE_SECONDS", "300"))
SUMMARY_RECONCILE_LEASE_SECONDS = int(os.getenv("SUMMARY_RECONCILE_LEASE_SECONDS", "600"))


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _object_id(value: str):
    try:
        return ObjectId(str(value))
    except Exception:
        return None


def _delete_if_present(storage: StorageService, bucket: str, object_name: str | None) -> None:
    if object_name:
        storage.delete_object(bucket, str(object_name))


def _terminal_outbox_update(status: str, *, error: str | None = None) -> dict:
    """Build a terminal outbox update that removes all delivery PII."""
    now = _utcnow()
    return {
        "$set": {
            "status": status,
            "updated_at": now,
            "completed_at": now,
            "last_error": error,
        },
        "$unset": {
            "recipient": "",
            "result_payload": "",
            "original_filename": "",
        },
    }


def enqueue_result_email(
    db,
    *,
    job_id: str,
    recipient: str,
    result_payload: dict,
    meeting_type_id: int,
    original_filename: str,
) -> str:
    """Create one durable result-email event per job and enqueue its delivery."""
    event_key = f"job:{job_id}:result"
    now = _utcnow()
    db.email_outbox.update_one(
        {"event_key": event_key},
        {"$setOnInsert": {
            "event_key": event_key,
            "job_id": str(job_id),
            "recipient": str(recipient),
            "result_payload": result_payload,
            "meeting_type_id": int(meeting_type_id),
            "original_filename": str(original_filename),
            "status": "pending",
            "attempts": 0,
            "created_at": now,
            "updated_at": now,
            "next_attempt_at": now,
            "expires_at": now + timedelta(days=EMAIL_OUTBOX_RETENTION_DAYS),
        }},
        upsert=True,
    )
    event = db.email_outbox.find_one({"event_key": event_key}, {"_id": 1, "status": 1})
    if not event:
        raise RuntimeError(f"Could not create email outbox event for job {job_id}")
    if event.get("status") == "pending":
        send_result_email.apply_async(args=[str(event["_id"])], queue="maintenance")
    return str(event["_id"])


@celery_app.task(bind=True, name="maintenance.send_result_email")
def send_result_email(self, outbox_id: str):
    """Claim and deliver an email event once; ambiguous crashes require review."""
    del self
    db = get_worker_db()
    object_id = _object_id(outbox_id)
    if not object_id:
        return {"outbox_id": outbox_id, "status": "invalid"}
    now = _utcnow()
    event = db.email_outbox.find_one_and_update(
        {
            "_id": object_id,
            "status": "pending",
            "$or": [
                {"next_attempt_at": {"$lte": now}},
                {"next_attempt_at": {"$exists": False}},
            ],
        },
        {"$set": {"status": "sending", "started_at": now, "updated_at": now}, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not event:
        existing = db.email_outbox.find_one({"_id": object_id}, {"status": 1})
        return {"outbox_id": outbox_id, "status": (existing or {}).get("status", "missing")}

    from app.tasks.transcription import _auto_send_result_email, _is_job_cancelled

    if _is_job_cancelled(db, event["job_id"]):
        db.email_outbox.update_one(
            {"_id": object_id, "status": "sending"},
            _terminal_outbox_update("cancelled", error="job cancelled or account unavailable"),
        )
        return {"outbox_id": outbox_id, "status": "cancelled"}

    delivered = _auto_send_result_email(
        db=db,
        job_id=event["job_id"],
        recipient=event["recipient"],
        result_payload=event["result_payload"],
        meeting_type_id=int(event.get("meeting_type_id") or 0),
        original_filename=event["original_filename"],
    )
    if delivered:
        status = "sent"
        db.email_outbox.update_one(
            {"_id": object_id, "status": "sending"},
            _terminal_outbox_update("sent"),
        )
    elif _is_job_cancelled(db, event["job_id"]):
        status = "cancelled"
        db.email_outbox.update_one(
            {"_id": object_id, "status": "sending"},
            _terminal_outbox_update(status, error="job cancelled before SMTP delivery"),
        )
    elif int(event.get("attempts") or 0) >= EMAIL_OUTBOX_MAX_ATTEMPTS:
        status = "dead"
        db.email_outbox.update_one(
            {"_id": object_id, "status": "sending"},
            _terminal_outbox_update(status, error="email delivery exhausted retries"),
        )
    else:
        status = "pending"
        delay = min(3600, 30 * (2 ** max(int(event.get("attempts") or 1) - 1, 0)))
        next_attempt = _utcnow() + timedelta(seconds=delay)
        db.email_outbox.update_one(
            {"_id": object_id, "status": "sending"},
            {"$set": {
                "status": "pending",
                "updated_at": _utcnow(),
                "next_attempt_at": next_attempt,
                "last_error": "transient email delivery failure",
            }},
        )
        send_result_email.apply_async(args=[outbox_id], queue="maintenance", countdown=delay)
    return {"outbox_id": outbox_id, "status": status}


@celery_app.task(name="maintenance.cleanup_job_storage")
def cleanup_job_storage(job_id: str, force: bool = False):
    """Idempotently remove terminal-job raw audio and transcript artifacts."""
    db = get_worker_db()
    job_obj_id = _object_id(job_id)
    if not job_obj_id:
        return {"job_id": job_id, "status": "invalid"}
    job = db.job.find_one({"_id": job_obj_id})
    if not job:
        return {"job_id": job_id, "status": "missing"}
    if job.get("status") not in TERMINAL_JOB_STATUSES:
        return {"job_id": job_id, "status": "not_terminal"}

    storage = StorageService()
    now = _utcnow()
    audio_after = job.get("audio_cleanup_after")
    if force or not audio_after or audio_after <= now:
        _delete_if_present(storage, BUCKET_AUDIO, job.get("audio_object") or job.get("audio_path"))
        db.job.update_one({"_id": job_obj_id}, {"$set": {
            "audio_cleanup_state": "completed",
            "audio_cleaned_at": now,
            "audio_cleanup_error": None,
        }})

    artifact = job.get("transcript_artifact_object")
    if not artifact and job.get("status") in {"failed", "cancelled"}:
        # The worker can be cancelled after upload_json but before the Mongo
        # checkpoint.  Artifact names are deterministic specifically so the
        # lifecycle worker can still remove that race-created object.
        artifact = f"artifacts/{job_id}/transcript.json"
    if artifact and (force or job.get("artifact_cleanup_state") in {"pending", "failed"}):
        _delete_if_present(storage, BUCKET_ARTIFACTS, artifact)
        db.job.update_one({"_id": job_obj_id}, {"$set": {
            "artifact_cleanup_state": "completed",
            "artifact_cleaned_at": now,
            "artifact_cleanup_error": None,
        }})
    if job.get("status") in {"failed", "cancelled"}:
        storage.delete_prefix(BUCKET_CLIPS, f"{job_id}/")
        session = db.session.find_one({"job_id": job_id}, {"clip_prefix": 1})
        if session and session.get("clip_prefix"):
            storage.delete_prefix(BUCKET_CLIPS, f"{session['clip_prefix']}/")
        db.job.update_one({"_id": job_obj_id}, {"$set": {
            "clip_cleanup_state": "completed",
            "clips_cleaned_at": now,
            "clip_cleanup_error": None,
        }})
    return {"job_id": job_id, "status": "completed"}


def _voice_samples_for_replay(db, user_id) -> list | None:
    samples = []
    for sample in db.voice_sample.find({"user_id": user_id}).limit(50):
        item = dict(sample)
        item["_id"] = str(item["_id"])
        item["user_id"] = str(item["user_id"])
        # Celery uses JSON serialization; timestamps are irrelevant to voice
        # matching and cannot be serialized safely.
        item.pop("created_at", None)
        item.pop("updated_at", None)
        samples.append(item)
    return samples or None


def _transcription_replay_kwargs(db, job: dict) -> dict:
    replay = dict(job.get("transcription_task_kwargs") or {})
    replay.update({
        "job_id": str(job["_id"]),
        "audio_object": str(
            replay.get("audio_object")
            or job.get("audio_object")
            or job.get("audio_path")
            or ""
        ),
        "original_filename": str(replay.get("original_filename") or job.get("audio_file") or "meeting"),
        "meeting_type_id": int(replay.get("meeting_type_id", job.get("meeting_type_id") or 0)),
        "user_id": str(job["user_id"]),
        "email_recipient": str(replay.get("email_recipient") or job.get("email_recipient") or ""),
        "custom_prompt": str(replay.get("custom_prompt") or job.get("custom_prompt") or ""),
    })
    use_voice_matching = bool(
        replay.pop("use_voice_matching", job.get("use_voice_matching", False))
    )
    replay["voice_samples"] = (
        _voice_samples_for_replay(db, job["user_id"]) if use_voice_matching else None
    )
    return replay


def _reconcile_quota_ledger(db, now: datetime) -> tuple[int, int]:
    """Close stale billing periods and prune old terminal ledger entries."""
    current_period = usage_period(now)
    cutoff = now - timedelta(days=JOB_RETENTION_DAYS)
    closed = 0
    pruned = 0
    packages = db.user_package.find(
        {"quota_reservations": {"$exists": True, "$ne": {}}},
        {"quota_reservations": 1},
    ).limit(RECONCILE_BATCH_SIZE)
    for package in packages:
        for key, reservation in (package.get("quota_reservations") or {}).items():
            if not isinstance(reservation, dict):
                continue
            path = f"quota_reservations.{key}"
            state = reservation.get("state")
            period = reservation.get("period")
            if state == "reserved" and period and period != current_period:
                result = db.user_package.update_one(
                    {
                        "_id": package["_id"],
                        f"{path}.state": "reserved",
                        f"{path}.period": period,
                    },
                    {"$set": {
                        f"{path}.state": "period_closed",
                        f"{path}.settled_at": now,
                    }},
                )
                modified = int(getattr(result, "modified_count", 0) or 0)
                closed += modified
                if modified and reservation.get("reservation_id"):
                    reservation_job_id = _object_id(reservation["reservation_id"])
                    if reservation_job_id:
                        db.job.update_one(
                            {
                                "_id": reservation_job_id,
                                "quota_settlement": "reserved",
                            },
                            {"$set": {
                                "quota_settlement": "period_closed",
                                "quota_settled_at": now,
                            }},
                        )
                # Retain a newly closed record for the normal job-retention
                # window; it can be pruned on a later reconcile pass.
                continue

            if state not in {"consumed", "refunded", "period_closed"}:
                continue
            terminal_at = reservation.get("settled_at") or reservation.get("created_at")
            if not isinstance(terminal_at, datetime):
                continue
            if terminal_at.tzinfo is None:
                terminal_at = terminal_at.replace(tzinfo=timezone.utc)
            if terminal_at > cutoff:
                continue
            result = db.user_package.update_one(
                {
                    "_id": package["_id"],
                    f"{path}.state": state,
                },
                {"$unset": {path: ""}},
            )
            pruned += int(getattr(result, "modified_count", 0) or 0)
    return closed, pruned


@celery_app.task(bind=True, name="maintenance.recover_transcription_publish", max_retries=8)
def recover_transcription_publish(self, job_id: str):
    """Replay an uploaded-but-unconfirmed transcription publication safely."""
    db = get_worker_db()
    job_obj_id = _object_id(job_id)
    if not job_obj_id:
        return {"job_id": job_id, "status": "invalid"}
    now = _utcnow()
    job = db.job.find_one({"_id": job_obj_id})
    if not job:
        return {"job_id": job_id, "status": "missing"}
    stale_processing = bool(
        job.get("status") == "processing"
        and job.get("transcription_state") == "running"
        and job.get("transcription_lease_expires_at")
        and job["transcription_lease_expires_at"] <= now
    )
    if (
        (job.get("publish_state") == "published" and not stale_processing)
        or job.get("status") not in {"initializing", "queued", "processing"}
    ):
        return {"job_id": job_id, "status": "already_started"}
    if not (
        job.get("upload_state") == "uploaded"
        or job.get("transcript_artifact_object")
    ):
        return {"job_id": job_id, "status": "upload_not_durable"}

    user = db.user.find_one({"_id": job.get("user_id")}, {"deletion_pending": 1})
    if not user or user.get("deletion_pending"):
        return {"job_id": job_id, "status": "account_unavailable"}

    task_id = str(uuid.uuid4() if stale_processing else (job.get("celery_task_id") or uuid.uuid4()))
    lease_until = now + timedelta(minutes=5)
    claim_query = {
        "_id": job_obj_id,
        "status": {"$in": ["initializing", "queued"]},
        "publish_state": {"$in": ["pending", "publishing"]},
        "$or": [
            {"publish_recovery_lease_expires_at": {"$lt": now}},
            {"publish_recovery_lease_expires_at": {"$exists": False}},
        ],
    }
    if stale_processing:
        claim_query = {
            "_id": job_obj_id,
            "status": "processing",
            "transcription_state": "running",
            "transcription_lease_expires_at": {"$lte": now},
        }
    claimed = db.job.find_one_and_update(
        claim_query,
        {"$set": {
            "status": "queued",
            "current_step": "queued",
            "transcription_state": "queued",
            "celery_task_id": task_id,
            "publish_state": "publishing",
            "publish_recovery_lease_expires_at": lease_until,
            "publish_recovery_started_at": now,
        }, "$inc": {"publish_attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return {"job_id": job_id, "status": "busy"}

    kwargs = _transcription_replay_kwargs(db, claimed)
    if not kwargs["audio_object"] and not claimed.get("transcript_artifact_object"):
        return {"job_id": job_id, "status": "missing_audio_checkpoint"}
    try:
        from app.tasks.transcription import process_audio

        process_audio.apply_async(
            kwargs=kwargs,
            task_id=task_id,
            queue="transcription",
        )
        db.job.update_one(
            {
                "_id": job_obj_id,
                "celery_task_id": task_id,
                "status": {"$nin": list(TERMINAL_JOB_STATUSES)},
            },
            {"$set": {
                "publish_state": "published",
                "published_at": _utcnow(),
                "publish_recovered": True,
                "publish_last_error": None,
            }, "$unset": {"publish_recovery_lease_expires_at": ""}},
        )
        return {"job_id": job_id, "status": "published", "task_id": task_id}
    except Exception as exc:
        db.job.update_one(
            {"_id": job_obj_id, "celery_task_id": task_id},
            {"$set": {
                "publish_last_error": str(exc)[:1000],
                "publish_recovery_lease_expires_at": _utcnow(),
            }},
        )
        raise self.retry(exc=exc, countdown=min(300, 2 ** min(self.request.retries + 1, 8)))


@celery_app.task(name="maintenance.cleanup_session_clips")
def cleanup_session_clips(session_id: str, force: bool = False):
    """Expire speaker clips while retaining transcript/session metadata."""
    db = get_worker_db()
    session_obj_id = _object_id(session_id)
    if not session_obj_id:
        return {"session_id": session_id, "status": "invalid"}
    session = db.session.find_one({"_id": session_obj_id})
    if not session:
        return {"session_id": session_id, "status": "missing"}
    now = _utcnow()
    if not force and session.get("clips_expires_at") and session["clips_expires_at"] > now:
        return {"session_id": session_id, "status": "not_expired"}
    prefix = session.get("clip_prefix")
    if prefix:
        StorageService().delete_prefix(BUCKET_CLIPS, f"{prefix}/")
    db.session.update_one(
        {"_id": session_obj_id},
        {"$set": {"clips_available": False, "clips_expired_at": now}},
    )
    return {"session_id": session_id, "status": "completed"}


@celery_app.task(name="maintenance.finalize_abandoned_initializing_job")
def finalize_abandoned_initializing_job(job_id: str):
    """Fail/refund a stale upload handoff that was never durably published."""
    db = get_worker_db()
    job_obj_id = _object_id(job_id)
    if not job_obj_id:
        return {"job_id": job_id, "status": "invalid"}
    job = db.job.find_one({"_id": job_obj_id})
    if not job:
        return {"job_id": job_id, "status": "missing"}
    if job.get("status") not in {"initializing", "queued"} or job.get("publish_state") == "published":
        return {"job_id": job_id, "status": "not_abandoned"}

    # Uploaded audio or a transcript artifact is recoverable durable work.  It
    # must be republished, never failed/refunded by the initialization reaper.
    if job.get("upload_state") == "uploaded" or job.get("transcript_artifact_object"):
        recover_transcription_publish.apply_async(args=[job_id], queue="maintenance")
        return {"job_id": job_id, "status": "recovery_queued"}
    if job.get("status") != "initializing":
        return {"job_id": job_id, "status": "manual_reconciliation_required"}

    # A crash can occur after the ledger reservation but before its job
    # checkpoint.  The reservation key is the preallocated job ID, so recover
    # that link before settling it.
    if not job.get("quota_reservation_id"):
        key = _reservation_key(job_id)
        package = db.user_package.find_one(
            {"user_id": job["user_id"], f"quota_reservations.{key}": {"$exists": True}},
            {f"quota_reservations.{key}": 1},
        )
        if package:
            db.job.update_one(
                {"_id": job_obj_id},
                {"$set": {
                    "quota_reserved": True,
                    "quota_reservation_id": job_id,
                    "quota_settlement": "reserved",
                }},
            )
    settled = settle_job_quota_db(db, job_id, "refunded")
    now = _utcnow()
    result = db.job.update_one(
        {
            "_id": job_obj_id,
            "status": {"$in": ["initializing", "queued"]},
            "publish_state": {"$ne": "published"},
        },
        {"$set": {
            "status": "failed",
            "current_step": "error",
            "error": "Upload initialization expired before worker publication",
            "result_available": False,
            "quota_settlement": "refunded" if settled else "not_reserved",
            "completed_at": now,
            "audio_cleanup_state": "pending",
            "audio_cleanup_after": now,
        }},
    )
    if result.matched_count == 0:
        return {"job_id": job_id, "status": "raced"}
    cleanup_job_storage.apply_async(args=[job_id, True], queue="maintenance")
    return {"job_id": job_id, "status": "failed", "quota_refunded": settled}


@celery_app.task(
    bind=True,
    name="maintenance.finalize_cancelled_job",
    max_retries=8,
)
def finalize_cancelled_job(self, job_id: str):
    """Refund and remove every cancelled-job artifact exactly once."""
    db = get_worker_db()
    job_obj_id = _object_id(job_id)
    if not job_obj_id:
        return {"job_id": job_id, "status": "invalid"}
    job = db.job.find_one({"_id": job_obj_id})
    if not job:
        return {"job_id": job_id, "status": "missing"}
    if job.get("status") != "cancelled":
        return {"job_id": job_id, "status": "not_cancelled"}
    if job.get("cancellation_cleanup_status") == "completed":
        return {"job_id": job_id, "status": "completed", "idempotent": True}

    try:
        refunded = bool(job.get("quota_refunded"))
        refunded = settle_job_quota_db(db, job_id, "refunded") or refunded
        if not refunded and not job.get("quota_reservation_id"):
            from app.tasks.transcription import _refund_job_quota_once

            refunded = _refund_job_quota_once(db, job_id)

        storage = StorageService()
        _delete_if_present(storage, BUCKET_AUDIO, job.get("audio_object") or job.get("audio_path"))
        _delete_if_present(storage, BUCKET_ARTIFACTS, job.get("transcript_artifact_object"))
        storage.delete_prefix(BUCKET_CLIPS, f"{job_id}/")
        session = db.session.find_one({"job_id": job_id}, {"_id": 1, "clip_prefix": 1})
        if session and session.get("clip_prefix"):
            storage.delete_prefix(BUCKET_CLIPS, f"{session['clip_prefix']}/")
        db.session.delete_many({"job_id": job_id})
        db.summary_state.update_one(
            {"job_id": job_id},
            {"$set": {
                "status": "cancelled",
                "stop_reason": "cancelled",
                "expires_at": _utcnow() + timedelta(days=30),
                "updated_at": _utcnow(),
            }},
            upsert=True,
        )
        db.email_outbox.update_many(
            {"job_id": job_id, "status": {"$nin": ["sent", "cancelled"]}},
            _terminal_outbox_update("cancelled", error="job cancelled"),
        )
        db.job.update_one(
            {"_id": job_obj_id, "status": "cancelled"},
            {"$set": {
                "cancellation_state": "completed",
                "cancellation_cleanup_status": "completed",
                "audio_cleanup_state": "completed",
                "artifact_cleanup_state": "completed",
                "result_available": False,
                "quota_settlement": "refunded" if refunded else job.get("quota_settlement"),
                "cleanup_completed_at": _utcnow(),
                "cancellation_cleanup_error": None,
            }},
        )
        return {"job_id": job_id, "status": "completed", "quota_refunded": refunded}
    except Exception as exc:
        db.job.update_one(
            {"_id": job_obj_id, "status": "cancelled"},
            {"$set": {
                "cancellation_cleanup_status": "failed",
                "cancellation_cleanup_error": str(exc)[:1000],
            }},
        )
        raise self.retry(exc=exc, countdown=min(300, 2 ** min(self.request.retries + 1, 8)))


def _deletion_snapshot(db, user_id) -> tuple[dict, list[dict]]:
    jobs = list(db.job.find({"user_id": user_id}))
    sessions = list(db.session.find({"user_id": user_id}, {"clip_prefix": 1}))
    samples = list(db.voice_sample.find({"user_id": user_id}, {"audio_path": 1}))
    snapshot = {
        "job_ids": [str(job["_id"]) for job in jobs],
        "audio_objects": sorted({
            str(job.get("audio_object") or job.get("audio_path"))
            for job in jobs if job.get("audio_object") or job.get("audio_path")
        }),
        "artifact_objects": sorted({
            str(job["transcript_artifact_object"])
            for job in jobs if job.get("transcript_artifact_object")
        } | {f"artifacts/{job['_id']}/transcript.json" for job in jobs}),
        "clip_prefixes": sorted(
            {str(job["_id"]) for job in jobs}
            | {str(item["clip_prefix"]) for item in sessions if item.get("clip_prefix")}
        ),
        "voice_objects": sorted({
            str(sample["audio_path"]) for sample in samples if sample.get("audio_path")
        }),
        "counts": {
            "jobs": len(jobs),
            "sessions": len(sessions),
            "voice_samples": len(samples),
            "email_outbox": db.email_outbox.count_documents({
                "job_id": {"$in": [str(job["_id"]) for job in jobs]}
            }),
        },
        "captured_at": _utcnow(),
    }
    return snapshot, jobs


def _merge_snapshots(left: dict | None, right: dict) -> dict:
    left = left or {}
    merged = dict(right)
    for field in ("job_ids", "audio_objects", "artifact_objects", "clip_prefixes", "voice_objects"):
        merged[field] = sorted(set(left.get(field) or []) | set(right.get(field) or []))
    merged["counts"] = {
        key: max(int((left.get("counts") or {}).get(key, 0)), int(value))
        for key, value in (right.get("counts") or {}).items()
    }
    return merged


def _delete_snapshot_storage(storage: StorageService, snapshot: dict) -> None:
    for object_name in snapshot.get("audio_objects") or []:
        _delete_if_present(storage, BUCKET_AUDIO, object_name)
    for object_name in snapshot.get("artifact_objects") or []:
        _delete_if_present(storage, BUCKET_ARTIFACTS, object_name)
    for prefix in snapshot.get("clip_prefixes") or []:
        storage.delete_prefix(BUCKET_CLIPS, f"{prefix}/")
    for object_name in snapshot.get("voice_objects") or []:
        _delete_if_present(storage, BUCKET_VOICE_SAMPLES, object_name)


def _pseudonymous_subject(user_id) -> str:
    audit_key = (
        os.getenv("CONSENT_AUDIT_KEY")
        or os.getenv("JWT_SECRET_KEY")
        or "timsumv3-development-consent-key"
    ).encode("utf-8")
    return hmac.new(audit_key, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()


def _delete_related_records(db, user_id, job_ids: list[str]) -> None:
    user_id_string = str(user_id)
    if job_ids:
        db.summary_state.delete_many({"job_id": {"$in": job_ids}})
        db.email_outbox.delete_many({"job_id": {"$in": job_ids}})
    db.password_reset.delete_many({"user_id": user_id_string})
    db.quota.delete_many({"user_id": user_id})
    db.user_package.delete_many({"user_id": user_id})
    db.session.delete_many({"user_id": user_id})
    db.voice_sample.delete_many({"user_id": user_id})
    db.consent_record.delete_many({"user_id": user_id_string})
    db.activity_log.delete_many({"user_id": user_id_string})
    db.package_request.delete_many({"user_id": user_id})
    db.package_assignment_history.delete_many({"user_id": user_id})
    db.job.delete_many({"user_id": user_id})


@celery_app.task(
    bind=True,
    name="maintenance.delete_account",
    max_retries=DELETION_MAX_RETRIES,
)
def delete_account(self, deletion_id: str):
    """Execute two-pass deletion with an idempotent grace-period reconciliation."""
    db = get_worker_db()
    manifest = db.data_deletion.find_one({"deletion_id": deletion_id})
    if not manifest:
        return {"deletion_id": deletion_id, "status": "missing"}
    if manifest.get("status") == "completed":
        return {"deletion_id": deletion_id, "status": "completed", "idempotent": True}
    user_id = manifest.get("user_id")
    if not user_id:
        return {"deletion_id": deletion_id, "status": manifest.get("status", "invalid")}

    now = _utcnow()
    second_pass = manifest.get("phase") == "grace_period" or manifest.get("deletion_pass") == 2
    if second_pass and manifest.get("reconcile_after") and manifest["reconcile_after"] > now:
        return {
            "deletion_id": deletion_id,
            "status": "reconciling",
            "reconcile_after": manifest["reconcile_after"].isoformat(),
        }
    claimed = db.data_deletion.find_one_and_update(
        {
            "deletion_id": deletion_id,
            "active": {"$ne": False},
            "$or": [
                {"status": {"$in": ["pending", "failed", "reconciling"]}},
                {"status": "running", "lease_expires_at": {"$lt": now}},
            ],
        },
        {"$set": {
            "status": "running",
            "deletion_pass": 2 if second_pass else 1,
            "lease_expires_at": now + timedelta(minutes=30),
            "updated_at": now,
        }, "$inc": {"attempts": 1}},
        return_document=ReturnDocument.AFTER,
    )
    if not claimed:
        return {"deletion_id": deletion_id, "status": "busy"}
    manifest = claimed

    try:
        db.user.update_one(
            {"_id": user_id},
            {"$set": {"deletion_pending": True, "deletion_requested_at": manifest.get("created_at", now)}},
        )
        current_snapshot, jobs = _deletion_snapshot(db, user_id)
        snapshot = _merge_snapshots(manifest.get("resource_snapshot"), current_snapshot)
        db.data_deletion.update_one(
            {"deletion_id": deletion_id},
            {"$set": {
                "phase": "cancel_jobs" if not second_pass else "reconcile_resources",
                "resource_snapshot": snapshot,
                "updated_at": _utcnow(),
            }},
        )

        db.job.update_many(
            {"user_id": user_id, "status": {"$nin": list(TERMINAL_JOB_STATUSES)}},
            {"$set": {
                "status": "cancelled",
                "current_step": "cancelled",
                "cancellation_state": "requested",
                "cancellation_cleanup_status": "pending",
                "email_status": "cancelled",
                "cancelled_at": now,
                "completed_at": now,
            }},
        )
        for job in jobs:
            for task_field in ("celery_task_id", "summary_celery_task_id"):
                if job.get(task_field):
                    celery_app.control.revoke(str(job[task_field]), terminate=False)
            settle_job_quota_db(db, str(job["_id"]), "refunded")

        db.data_deletion.update_one(
            {"deletion_id": deletion_id},
            {"$set": {"phase": "delete_storage", "updated_at": _utcnow()}},
        )
        _delete_snapshot_storage(StorageService(), snapshot)

        subject_id = _pseudonymous_subject(user_id)
        # consent_event is append-only and already pseudonymous; never mutate
        # it during account deletion.
        _delete_related_records(db, user_id, snapshot.get("job_ids") or [])

        if not second_pass:
            reconcile_after = _utcnow() + timedelta(hours=DELETION_GRACE_HOURS)
            db.data_deletion.update_one(
                {"deletion_id": deletion_id},
                {"$set": {
                    "status": "reconciling",
                    "phase": "grace_period",
                    "active": True,
                    "subject_id": subject_id,
                    "reconcile_after": reconcile_after,
                    "last_error": None,
                    "updated_at": _utcnow(),
                }},
            )
            return {
                "deletion_id": deletion_id,
                "status": "reconciling",
                "reconcile_after": reconcile_after.isoformat(),
            }

        # The second pass rescanned resources and repeated cleanup.  Only now is
        # the blocked identity removed; this is the final irreversible step.
        db.user.delete_one({"_id": user_id})
        finished = _utcnow()
        db.data_deletion.update_one(
            {"deletion_id": deletion_id},
            {
                "$set": {
                    "status": "completed",
                    "phase": "completed",
                    "active": False,
                    "subject_id": subject_id,
                    "completed_at": finished,
                    "updated_at": finished,
                    "last_error": None,
                },
                "$unset": {"user_id": "", "requested_by": "", "resource_snapshot": ""},
            },
        )
        return {"deletion_id": deletion_id, "status": "completed"}
    except Exception as exc:
        logger.exception("Account deletion {} failed", deletion_id)
        db.data_deletion.update_one(
            {"deletion_id": deletion_id},
            {"$set": {
                "status": "failed",
                "active": True,
                "last_error": f"{type(exc).__name__}: deletion step failed",
                "updated_at": _utcnow(),
            }},
        )
        raise self.retry(exc=exc, countdown=min(300, 2 ** min(self.request.retries + 1, 8)))


@celery_app.task(name="maintenance.reconcile")
def reconcile():
    """Requeue stale leases, lifecycle cleanup, deletion and quota settlement."""
    db = get_worker_db()
    now = _utcnow()
    counters = {
        "summaries": 0,
        "cleanup": 0,
        "clip_cleanup": 0,
        "cancel_cleanup": 0,
        "abandoned_initializing": 0,
        "publish_recovery": 0,
        "deletions": 0,
        "quota": 0,
        "quota_period_closed": 0,
        "quota_pruned": 0,
        "outbox_review": 0,
        "email_recovered": 0,
        "package_request_recovered": 0,
    }

    stale_queued_before = now - timedelta(seconds=SUMMARY_PUBLISH_RECOVERY_GRACE_SECONDS)
    stale_states = db.summary_state.find({
        "$or": [
            {
                "status": {"$in": ["running", "finalizing"]},
                "lease_expires_at": {"$lt": now},
            },
            {
                "status": "queued",
                "updated_at": {"$lte": stale_queued_before},
                "$or": [
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lt": now}},
                ],
            },
        ],
    }).limit(RECONCILE_BATCH_SIZE)
    from app.tasks.summary import finalize, process_next_chunk
    for state in stale_states:
        reconcile_token = uuid.uuid4().hex
        claimed = db.summary_state.find_one_and_update(
            {
                "_id": state["_id"],
                "status": state.get("status"),
                "$or": [
                    {"lease_expires_at": None},
                    {"lease_expires_at": {"$lt": now}},
                ],
            },
            {"$set": {
                "reconcile_token": reconcile_token,
                "reconcile_enqueued_at": now,
                "lease_expires_at": now + timedelta(seconds=SUMMARY_RECONCILE_LEASE_SECONDS),
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            continue
        task = finalize if claimed.get("status") == "finalizing" else process_next_chunk
        try:
            task.apply_async(args=[claimed["job_id"]], queue="summary")
            counters["summaries"] += 1
        except Exception:
            db.summary_state.update_one(
                {"_id": claimed["_id"], "reconcile_token": reconcile_token},
                {"$set": {"lease_expires_at": now}},
            )
            raise

    abandoned_jobs = db.job.find({
        "status": "initializing",
        "publish_state": {"$ne": "published"},
        "initializing_expires_at": {"$lte": now},
        "upload_state": {"$ne": "uploaded"},
        "transcript_artifact_object": {"$exists": False},
    }).limit(RECONCILE_BATCH_SIZE)
    for job in abandoned_jobs:
        finalize_abandoned_initializing_job.apply_async(
            args=[str(job["_id"])], queue="maintenance"
        )
        counters["abandoned_initializing"] += 1

    stale_publish_before = now - timedelta(seconds=PUBLISH_RECOVERY_GRACE_SECONDS)
    stale_publications = db.job.find({
        "status": {"$in": ["initializing", "queued"]},
        "publish_state": {"$in": ["pending", "publishing"]},
        "$or": [
            {"upload_state": "uploaded"},
            {"transcript_artifact_object": {"$exists": True, "$ne": None}},
        ],
        "$and": [{"$or": [
            {"publish_intent_at": {"$lte": stale_publish_before}},
            {
                "publish_intent_at": {"$exists": False},
                "initializing_expires_at": {"$lte": now},
            },
        ]}],
    }).limit(RECONCILE_BATCH_SIZE)
    for job in stale_publications:
        recover_transcription_publish.apply_async(
            args=[str(job["_id"])], queue="maintenance"
        )
        counters["publish_recovery"] += 1

    stale_transcriptions = db.job.find({
        "status": "processing",
        "transcription_state": "running",
        "transcription_lease_expires_at": {"$lte": now},
        "result_available": {"$ne": True},
    }).limit(RECONCILE_BATCH_SIZE)
    for job in stale_transcriptions:
        recover_transcription_publish.apply_async(
            args=[str(job["_id"])], queue="maintenance"
        )
        counters["publish_recovery"] += 1

    cleanup_jobs = db.job.find({
        "status": {"$in": list(TERMINAL_JOB_STATUSES)},
        "$or": [
            {"audio_cleanup_state": {"$in": ["pending", "failed"]}, "audio_cleanup_after": {"$lte": now}},
            {"artifact_cleanup_state": {"$in": ["pending", "failed"]}},
            {
                "status": {"$in": ["failed", "cancelled"]},
                "clip_cleanup_state": {"$ne": "completed"},
            },
        ],
    }).limit(RECONCILE_BATCH_SIZE)
    for job in cleanup_jobs:
        cleanup_job_storage.apply_async(args=[str(job["_id"])], queue="maintenance")
        counters["cleanup"] += 1

    # Quota settlement is independent from object cleanup.  A crash after the
    # terminal job CAS but before settlement must not leave a reservation open.
    terminal_unsettled = db.job.find({
        "status": {"$in": list(TERMINAL_JOB_STATUSES)},
        "quota_reservation_id": {"$exists": True, "$ne": None},
        "quota_settlement": "reserved",
    }).limit(RECONCILE_BATCH_SIZE)
    for job in terminal_unsettled:
        outcome = "refunded" if job.get("status") in {"failed", "cancelled"} else "consumed"
        if settle_job_quota_db(db, str(job["_id"]), outcome):
            counters["quota"] += 1

    closed, pruned = _reconcile_quota_ledger(db, now)
    counters["quota_period_closed"] = closed
    counters["quota_pruned"] = pruned

    stale_package_requests = db.package_request.update_many(
        {
            "status": "applying",
            "apply_lease_expires_at": {"$lte": now},
        },
        {
            "$set": {
                "status": "pending",
                "last_error": "PackageApplyLeaseExpired: safe retry required",
            },
            "$unset": {"apply_token": "", "apply_lease_expires_at": ""},
        },
    )
    counters["package_request_recovered"] = int(
        getattr(stale_package_requests, "modified_count", 0) or 0
    )

    expired_sessions = db.session.find({
        "clips_expires_at": {"$lte": now},
        "clips_expired_at": {"$exists": False},
    }).limit(RECONCILE_BATCH_SIZE)
    for session in expired_sessions:
        cleanup_session_clips.apply_async(args=[str(session["_id"])], queue="maintenance")
        counters["clip_cleanup"] += 1

    cancelled_jobs = db.job.find({
        "status": "cancelled",
        "cancellation_cleanup_status": {"$ne": "completed"},
    }).limit(RECONCILE_BATCH_SIZE)
    for job in cancelled_jobs:
        finalize_cancelled_job.apply_async(args=[str(job["_id"])], queue="maintenance")
        counters["cancel_cleanup"] += 1

    manifests = db.data_deletion.find({
        "active": {"$ne": False},
        "$or": [
            {"status": {"$in": ["pending", "failed"]}},
            {"status": "reconciling", "reconcile_after": {"$lte": now}},
            {"status": "running", "lease_expires_at": {"$lt": now}},
        ],
    }).limit(RECONCILE_BATCH_SIZE)
    for manifest in manifests:
        delete_account.apply_async(args=[manifest["deletion_id"]], queue="maintenance")
        counters["deletions"] += 1

    # Recover the crash boundary after a terminal job/state CAS but before the
    # idempotent email outbox insert.
    terminal_email_jobs = db.job.find({
        "status": {"$in": ["completed", "partially_completed", "failed"]},
        "result_available": True,
        "email_status": "queued",
        "email_recipient": {"$type": "string", "$ne": ""},
    }).limit(RECONCILE_BATCH_SIZE)
    for job in terminal_email_jobs:
        event_key = f"job:{job['_id']}:result"
        if db.email_outbox.find_one({"event_key": event_key}, {"_id": 1}):
            continue
        result_payload = job.get("result") or {}
        enqueue_result_email(
            db,
            job_id=str(job["_id"]),
            recipient=job["email_recipient"],
            result_payload=result_payload,
            meeting_type_id=int(job.get("meeting_type_id") or 0),
            original_filename=str(
                result_payload.get("audio_file") or job.get("audio_file") or "meeting"
            ),
        )
        counters["email_recovered"] += 1

    pending_outbox = db.email_outbox.find({
        "status": "pending",
        "next_attempt_at": {"$lte": now},
    }).limit(RECONCILE_BATCH_SIZE)
    for event in pending_outbox:
        send_result_email.apply_async(args=[str(event["_id"])], queue="maintenance")

    ambiguous_before = now - timedelta(minutes=15)
    result = db.email_outbox.update_many(
        {"status": "sending", "started_at": {"$lt": ambiguous_before}},
        _terminal_outbox_update(
            "needs_review",
            error="worker stopped during an ambiguous SMTP delivery",
        ),
    )
    counters["outbox_review"] = result.modified_count
    return counters
