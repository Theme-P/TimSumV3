"""
Celery task for audio transcription and summarization.
Runs on GPU worker — one task at a time.

Flow:
  1. Download audio from MinIO (audio-uploads bucket)
  2. Run WhisperX pipeline (GPU)
  3. Upload speaker clips to MinIO (speaker-clips bucket)
  4. Save results to MongoDB
  5. Cleanup local temp files
"""
import os
import re
import tempfile
import shutil
import time
import uuid
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from loguru import logger

from app.celery_app import celery_app
from app.services.pipeline import TranscribeSummaryPipeline
from app.services.storage import StorageService, BUCKET_ARTIFACTS, BUCKET_AUDIO, BUCKET_CLIPS
from app.services.db import get_worker_db
from app.services.email_service import EmailService
from app.services.mongo import settle_job_quota_db
from app.services.cancellation import JobCancelled
from app.utils.export import export_transcript_to_docx, export_summary_to_docx
from app.models.meeting import MEETING_TYPES


TASK_MAX_RETRIES = int(os.getenv("TRANSCRIBE_MAX_RETRIES", "2"))
TASK_RETRY_DELAY_SECONDS = int(os.getenv("TRANSCRIBE_RETRY_DELAY_SECONDS", "60"))
TASK_SOFT_TIME_LIMIT_SECONDS = int(os.getenv("TRANSCRIBE_SOFT_TIME_LIMIT_SECONDS", "1800"))
TASK_TIME_LIMIT_SECONDS = int(os.getenv("TRANSCRIBE_TIME_LIMIT_SECONDS", "2100"))
SPEAKER_CLIP_RETENTION_DAYS = int(os.getenv("SPEAKER_CLIP_RETENTION_DAYS", "30"))
SESSION_RETENTION_DAYS = int(os.getenv("SESSION_RETENTION_DAYS", "365"))
RAW_AUDIO_FAILSAFE_HOURS = int(os.getenv("RAW_AUDIO_FAILSAFE_HOURS", "48"))
TRANSCRIPTION_LEASE_SECONDS = int(
    os.getenv("TRANSCRIPTION_LEASE_SECONDS", str(TASK_TIME_LIMIT_SECONDS + 300))
)
TRANSCRIPTION_LEASE_RENEW_SECONDS = int(os.getenv("TRANSCRIPTION_LEASE_RENEW_SECONDS", "60"))


class TranscriptionLeaseLost(RuntimeError):
    """Raised when another delivery owns this job's transcription run."""


def _summary_pipeline_mode() -> str:
    default_mode = (
        "async"
        if os.getenv("APP_ENV", "development").strip().lower() in {"staging", "production"}
        else "inline"
    )
    return os.getenv("SUMMARY_PIPELINE_MODE", default_mode).strip().lower()


def _async_summary_enabled() -> bool:
    return _summary_pipeline_mode() == "async"


def _safe_export_stem(filename: str) -> str:
    """Create a display-only attachment stem; filesystem paths stay server-generated."""
    basename = os.path.basename(str(filename or ""))
    stem = os.path.splitext(basename)[0]
    stem = re.sub(r"[\x00-\x1f\x7f/\\]+", "_", stem).strip(" ._")
    return stem[:100] or "meeting"


def _get_storage() -> StorageService:
    """Get MinIO connection for the worker process."""
    return StorageService()


def _is_job_cancelled(db, job_id: str) -> bool:
    """Fail closed for cancelled jobs or identities pending/after deletion."""
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
    run_id: str | None = None,
    allow_cancelled: bool = False,
):
    """Update job document in MongoDB."""
    query = {"_id": ObjectId(job_id)}
    if not allow_cancelled:
        query["status"] = {"$ne": "cancelled"}
    if run_id:
        query["transcription_run_id"] = run_id
    result = db.job.update_one(query, {"$set": update})
    if run_id and getattr(result, "matched_count", 0) == 0:
        if _is_job_cancelled(db, job_id):
            raise JobCancelled(f"Job {job_id} was cancelled")
        raise TranscriptionLeaseLost(f"Transcription run for {job_id} was superseded")
    return result


def _renew_transcription_lease(db, job_id: str, run_id: str) -> None:
    now = datetime.now(timezone.utc)
    result = db.job.update_one(
        {
            "_id": ObjectId(job_id),
            "transcription_run_id": run_id,
            "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
        },
        {"$set": {
            "transcription_lease_expires_at": now + timedelta(seconds=TRANSCRIPTION_LEASE_SECONDS),
            "transcription_lease_renewed_at": now,
        }},
    )
    if getattr(result, "matched_count", 0) == 0:
        if _is_job_cancelled(db, job_id):
            raise JobCancelled(f"Job {job_id} was cancelled")
        raise TranscriptionLeaseLost(f"Transcription lease for {job_id} was superseded")


def _refund_job_quota_once(db, job_id: str) -> bool:
    """Refund a job's reserved package quota once."""
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


def _cleanup_source_audio(storage, db, job_id: str, audio_object: str) -> bool:
    """Delete source audio only after a durable downstream checkpoint exists."""
    try:
        storage.delete_object(BUCKET_AUDIO, audio_object)
    except Exception as exc:
        _update_job(db, job_id, {
            "audio_cleanup_state": "pending",
            "audio_cleanup_after": datetime.now(timezone.utc),
            "audio_cleanup_error": str(exc)[:1000],
        }, allow_cancelled=True)
        return False
    _update_job(db, job_id, {
        "audio_cleanup_state": "completed",
        "audio_cleanup_error": None,
        "audio_cleaned_at": datetime.now(timezone.utc),
    }, allow_cancelled=True)
    return True


def _enqueue_cancel_cleanup(job_id: str) -> None:
    try:
        from app.tasks.maintenance import finalize_cancelled_job

        finalize_cancelled_job.apply_async(args=[job_id], queue="maintenance")
    except Exception as exc:
        # The periodic reconciler will recover cancellation_cleanup_status=pending.
        logger.warning("Job {}: could not enqueue cancellation cleanup: {}", job_id, exc)


def _resume_async_summary_checkpoint(
    db,
    storage,
    job_id: str,
    audio_object: str,
    user_id: str,
    *,
    run_id: str,
):
    """Resume from a durable transcript artifact without running WhisperX again."""
    if not _async_summary_enabled():
        return None
    job = db.job.find_one({"_id": ObjectId(job_id)}) or {}
    artifact_object = job.get("transcript_artifact_object") or f"artifacts/{job_id}/transcript.json"
    if not storage.object_exists(BUCKET_ARTIFACTS, artifact_object):
        return None

    from app.tasks.summary import initialize_summary_state, process_next_chunk

    artifact = storage.get_json(BUCKET_ARTIFACTS, artifact_object)
    if str(artifact.get("job_id")) != str(job_id) or str(artifact.get("user_id")) != str(user_id):
        raise RuntimeError("Transcript artifact ownership mismatch")
    segments = (artifact.get("full_transcript") or {}).get("segments") or []
    state = initialize_summary_state(db, job_id, artifact_object, segments)
    _update_job(db, job_id, {
        "status": "processing",
        "current_step": "summary_queued",
        "progress": 75,
        "transcript_artifact_object": artifact_object,
        "artifact_cleanup_state": "active",
        "summary_status": state.get("status", "queued"),
        "summary_total_chunks": state.get("total_chunks", 0),
        "total_segments": state.get("total_segments", 0),
        "result_available": False,
        "transcription_state": "completed",
        "transcription_completed_at": datetime.now(timezone.utc),
    }, run_id=run_id)
    process_next_chunk.apply_async(args=[job_id], queue="summary")
    _cleanup_source_audio(storage, db, job_id, audio_object)
    logger.info("Job {} resumed from durable transcript artifact", job_id)
    return {"job_id": job_id, "summary_queued": True, "resumed_from_artifact": True}


def _auto_send_result_email(
    db,
    job_id: str,
    recipient: str,
    result_payload: dict,
    meeting_type_id: int,
    original_filename: str,
) -> bool:
    """
    Generate DOCX files and email them to the recipient.
    Failure here must NOT fail the job — we only update email_status.
    """
    if _is_job_cancelled(db, job_id):
        logger.info(f"Job {job_id}: skipping result email because job is cancelled")
        return False

    _update_job(db, job_id, {"email_status": "sending"})

    email_svc = EmailService()
    if not email_svc.is_configured:
        logger.warning(f"Job {job_id}: SMTP not configured, skipping auto-send")
        _update_job(db, job_id, {
            "email_status": "failed",
            "email_error": "SMTP not configured on server",
        })
        return False

    temp_dir = tempfile.mkdtemp(prefix="timsumv3_email_")
    try:
        file_name_no_ext = _safe_export_stem(original_filename)

        summary_metadata = result_payload.get("summary_metadata") or {}
        summary_status = result_payload.get("summary_status") or summary_metadata.get("summary_status") or "completed"
        is_partial_summary = bool(
            result_payload.get("is_partial_summary")
            or summary_metadata.get("is_partial_summary")
        )
        coverage_percentage = float(
            result_payload.get("coverage_percentage")
            or summary_metadata.get("coverage_percentage")
            or 0
        )
        summary_warning = str(summary_metadata.get("user_warning") or "").strip()
        is_summary_valid = (
            summary_status != "failed"
            and bool(result_payload.get("summary"))
            and "เกิดข้อผิดพลาดในการสรุปผล" not in result_payload["summary"]
        )

        docx_files = []
        
        # Generate transcript DOCX
        transcript_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_transcript.docx")
        export_transcript_to_docx(
            segments=result_payload["transcript"]["segments"],
            output_path=transcript_path,
            audio_file=result_payload["audio_file"],
            audio_length=result_payload["audio_length_seconds"],
        )
        docx_files.append((transcript_path, f"{file_name_no_ext}_Transcription"))

        if is_summary_valid:
            # Generate summary DOCX
            summary_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}_summary.docx")
            export_summary_to_docx(
                summary_text=(
                    f"หมายเหตุ: {summary_warning}\n\n{result_payload['summary']}"
                    if summary_warning else result_payload["summary"]
                ),
                output_path=summary_path,
                speaker_summary=result_payload["transcript"]["speaker_summary"],
                meeting_type_id=meeting_type_id,
                agendas=result_payload.get("agendas"),
            )
            docx_files.append((summary_path, f"{file_name_no_ext}_Summary"))

            status_note = (
                f"Summary ครอบคลุม Transcript {coverage_percentage:.1f}% "
                "กรุณาตรวจ Transcript สำหรับช่วงที่เหลือ\n"
                if is_partial_summary else "Summary และ Transcript ประมวลผลสำเร็จ\n"
            )
            body = (
                f"เรียน คุณผู้ใช้งาน\n\n"
                f"เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว และระบบได้ส่งผลลัพธ์มาให้โดยอัตโนมัติ\n\n"
                f"รายละเอียด:\n"
                f"- ชื่อไฟล์: {original_filename}\n"
                f"- การประมวลผล: บันทึกเสียงและสรุปเอกสาร\n"
                f"- จำนวนไฟล์แนบ: {len(docx_files)} ไฟล์ (Summary + Transcript)\n\n"
                f"สถานะ: {status_note}\n"
                f"หมายเหตุ: หากต้องการผลลัพธ์พร้อมชื่อ Speaker ที่แก้ไขแล้ว "
                f"ให้เข้าไปที่หน้า 'ประวัติ' เปิดรายการอัปโหลดนี้ แล้วกด 'ส่งซ้ำ'\n\n"
                f"ขอบคุณที่ใช้บริการ TimSum V3"
            )
        else:
            body = (
                f"เรียน คุณผู้ใช้งาน\n\n"
                f"เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว\n\n"
                f"รายละเอียด:\n"
                f"- ชื่อไฟล์: {original_filename}\n"
                f"- การประมวลผล: บันทึกเสียง\n"
                f"- ไฟล์ที่แนบมา: {file_name_no_ext}_Transcription.docx\n\n"
                f"หมายเหตุ: ระบบสรุปเอกสารไม่สำเร็จ จึงส่งเฉพาะ Transcript\n"
                f"คุณสามารถตรวจสอบไฟล์ Transcription เพื่อดูรายละเอียดครบถ้วน\n\n"
                f"ขอบคุณที่ใช้บริการ TimSum V3"
            )

        # Re-check immediately before the irreversible external side effect.
        # Account deletion/cancellation may have started while DOCX files were
        # being generated.
        if _is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id}: skipping result email after cancellation race")
            return False

        ok = email_svc.send_email_with_attachments(
            recipient_email=recipient,
            subject=f"Document Processing Complete - {original_filename}",
            body_text=body,
            docx_files=docx_files,
        )

        if ok:
            _update_job(db, job_id, {
                "email_status": "sent",
                "email_sent_at": datetime.now(timezone.utc),
                "email_error": None,
            })
            logger.info(f"Job {job_id}: result email sent to {recipient}")
        else:
            _update_job(db, job_id, {
                "email_status": "failed",
                "email_error": "send_email_with_attachments returned False (check worker logs)",
            })
        return bool(ok)
    except Exception as e:
        logger.exception(f"Job {job_id}: email auto-send failed")
        _update_job(db, job_id, {
            "email_status": "failed",
            "email_error": str(e),
        })
        return False
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


@celery_app.task(
    bind=True,
    name="transcription.process_audio",
    max_retries=TASK_MAX_RETRIES,
    default_retry_delay=TASK_RETRY_DELAY_SECONDS,
    soft_time_limit=TASK_SOFT_TIME_LIMIT_SECONDS,
    time_limit=TASK_TIME_LIMIT_SECONDS,
)
def process_audio(
    self,
    job_id: str,
    audio_object: str,
    original_filename: str,
    meeting_type_id: int,
    user_id: str,
    email_recipient: str = "",
    custom_prompt: str = "",
    voice_samples: list = None,
):
    """
    Process audio file: transcribe + diarize + summarize.

    Args:
        job_id: MongoDB job document ID
        audio_object: MinIO object name in audio-uploads bucket
        original_filename: Original filename from user upload
        meeting_type_id: Meeting type (0=auto, 1-11=specific)
        user_id: User ID who submitted the job
    """
    db = get_worker_db()
    storage = _get_storage()
    temp_dir = tempfile.mkdtemp(prefix="timsumv3_worker_")
    pipeline = None
    session_id = None
    task_run_id = uuid.uuid4().hex
    lease_renewed_monotonic = time.monotonic()

    try:
        started_at = datetime.now(timezone.utc)
        queue_wait_seconds = None
        job_doc = db.job.find_one({"_id": ObjectId(job_id)})
        if not job_doc:
            raise RuntimeError(f"Job {job_id} does not exist")
        if job_doc.get("status") in {"completed", "partially_completed", "failed"}:
            return {
                "job_id": job_id,
                "session_id": str(job_doc.get("session_id") or "") or None,
                "status": job_doc.get("status"),
                "idempotent": True,
            }
        _ensure_not_cancelled(db, job_id)
        claim_now = datetime.now(timezone.utc)
        claim = db.job.update_one(
            {
                "_id": ObjectId(job_id),
                "status": {"$in": ["initializing", "queued", "processing"]},
                "$or": [
                    {"transcription_lease_expires_at": {"$lte": claim_now}},
                    {"transcription_lease_expires_at": {"$exists": False}},
                    {"transcription_lease_expires_at": None},
                ],
            },
            {"$set": {
                "status": "processing",
                "transcription_state": "running",
                "transcription_run_id": task_run_id,
                "transcription_lease_expires_at": claim_now + timedelta(seconds=TRANSCRIPTION_LEASE_SECONDS),
                "transcription_lease_renewed_at": claim_now,
                "publish_state": "published",
                "published_at": claim_now,
            }},
        )
        if getattr(claim, "matched_count", 0) == 0:
            current = db.job.find_one({"_id": ObjectId(job_id)}, {"status": 1, "session_id": 1}) or {}
            if current.get("status") in {"completed", "partially_completed", "failed", "cancelled"}:
                return {
                    "job_id": job_id,
                    "session_id": str(current.get("session_id") or "") or None,
                    "status": current.get("status"),
                    "idempotent": True,
                }
            # A second delivery is expected after ambiguous broker recovery.
            # The active owner will finish; maintenance only republishes after
            # its durable lease expires.
            return {"job_id": job_id, "status": "duplicate_active", "idempotent": True}

        def guard(*, force_renew: bool = False) -> None:
            nonlocal lease_renewed_monotonic
            _ensure_not_cancelled(db, job_id)
            if force_renew or (
                time.monotonic() - lease_renewed_monotonic >= TRANSCRIPTION_LEASE_RENEW_SECONDS
            ):
                _renew_transcription_lease(db, job_id, task_run_id)
                lease_renewed_monotonic = time.monotonic()

        resumed = _resume_async_summary_checkpoint(
            db, storage, job_id, audio_object, user_id, run_id=task_run_id
        )
        if resumed is not None:
            return resumed
        created_at = job_doc.get("created_at") if job_doc else None
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            queue_wait_seconds = max((started_at - created_at).total_seconds(), 0)

        guard(force_renew=True)

        # Mark job as processing
        _update_job(db, job_id, {
            "status": "processing",
            "current_step": "model_load",
            "progress": 5,
            "celery_task_id": self.request.id,
            "publish_state": "published",
            "published_at": started_at,
            "audio_object": audio_object,
            "started_at": started_at,
            "queue_wait_seconds": queue_wait_seconds,
        }, run_id=task_run_id)
        guard()

        # Download audio from MinIO to temp file
        file_ext = os.path.splitext(audio_object)[1]
        local_audio = os.path.join(temp_dir, f"audio{file_ext}")
        storage.download_file(BUCKET_AUDIO, audio_object, local_audio)
        guard()

        # Progress callback — pipeline calls this at each step
        def on_progress(step: str, progress: int):
            guard()
            _update_job(
                db,
                job_id,
                {"current_step": step, "progress": progress},
                run_id=task_run_id,
            )
            guard()

        # Run the pipeline with live progress reporting. In async summary mode
        # this stops after transcript/agenda preparation and releases the GPU
        # worker before the LLM summarization phase.
        async_summary = _async_summary_enabled()
        pipeline = TranscribeSummaryPipeline()
        result = pipeline.process(
            local_audio,
            meeting_type_id=meeting_type_id,
            on_progress=on_progress,
            custom_prompt=custom_prompt,
            voice_samples=voice_samples,
            mongo_service=db,
            cancellation_checker=guard,
            run_summary=not async_summary,
        )
        guard(force_renew=True)

        # Pipeline completed — upload clips to MinIO
        _update_job(
            db,
            job_id,
            {"current_step": "saving", "progress": 95},
            run_id=task_run_id,
        )
        guard()

        clip_dir = result.get("clip_dir", "")
        clip_prefix = job_id  # clips stored under speaker-clips/{job_id}/
        speaker_clips_response = {}

        for speaker, clip_info in result.get("speaker_clips", {}).items():
            guard()
            clip_filename = clip_info["clip_filename"]
            local_clip_path = os.path.join(clip_dir, clip_filename)

            if os.path.exists(local_clip_path):
                object_name = f"{clip_prefix}/{clip_filename}"
                storage.upload_file(BUCKET_CLIPS, object_name, local_clip_path, content_type="audio/mpeg")
                guard()

            speaker_clips_response[speaker] = {
                "clip_filename": clip_filename,
                "start": clip_info["start"],
                "end": clip_info["end"],
                "duration": clip_info["duration"],
            }

        if async_summary:
            from app.tasks.summary import initialize_summary_state, process_next_chunk

            artifact_object = f"artifacts/{job_id}/transcript.json"
            guard(force_renew=True)
            artifact_payload = {
                "job_id": job_id,
                "user_id": user_id,
                "original_filename": original_filename,
                "meeting_type_id": meeting_type_id,
                "email_recipient": email_recipient,
                "custom_prompt": custom_prompt,
                "audio_length_seconds": result["audio_length_seconds"],
                "processing_time": result["processing_time"],
                "full_transcript": result["full_transcript"],
                "speaker_clips_response": speaker_clips_response,
                "clip_prefix": clip_prefix,
                "suggested_names": result.get("suggested_names", {}),
                "agendas": result.get("agendas", []),
                "detection_mode": result.get("detection_mode", "single_topic"),
                "detected_language": result.get("detected_language", "th"),
                "effective_meeting_type_id": result.get("effective_meeting_type_id", meeting_type_id),
                "meeting_style_source": result.get("meeting_style_source"),
                "meeting_style_classification": result.get("meeting_style_classification", {}),
                "summary_metadata": result.get("summary_metadata", {}),
                "audio_object": audio_object,
            }
            storage.upload_json(BUCKET_ARTIFACTS, artifact_object, artifact_payload)
            guard()
            state = initialize_summary_state(
                db,
                job_id,
                artifact_object,
                result["full_transcript"]["segments"],
            )
            guard()
            summary_task_id = uuid.uuid4().hex
            db.summary_state.update_one(
                {"job_id": job_id, "status": "queued"},
                {"$set": {
                    "publish_state": "publishing",
                    "celery_task_id": summary_task_id,
                    "publish_intent_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            _update_job(db, job_id, {
                "status": "processing",
                "current_step": "summary_queued",
                "progress": 75,
                "transcript_artifact_object": artifact_object,
                "artifact_cleanup_state": "active",
                "summary_status": "queued",
                "summary_progress": 0,
                "summary_completed_chunks": 0,
                "summary_total_chunks": state.get("total_chunks", 0),
                "covered_segments": 0,
                "total_segments": state.get("total_segments", 0),
                "coverage_percentage": 0.0,
                "last_covered_segment": None,
                "partial_chunks": [],
                "failed_ranges": [],
                "coverage_complete": False,
                "is_partial_summary": False,
                "summary_started_at": None,
                "summary_finished_at": None,
                "summary_elapsed_seconds": 0.0,
                "summary_time_limit_seconds": state.get("summary_time_limit_seconds", 1200),
                "stop_reason": None,
                "final_render_status": "not_started",
                "result_available": False,
                "transcription_state": "completed",
                "transcription_completed_at": datetime.now(timezone.utc),
                "summary_celery_task_id": summary_task_id,
                "summary_publish_state": "publishing",
            }, run_id=task_run_id)
            guard()
            process_next_chunk.apply_async(
                args=[job_id], queue="summary", task_id=summary_task_id
            )
            guard()
            db.summary_state.update_one(
                {"job_id": job_id, "celery_task_id": summary_task_id},
                {"$set": {
                    "publish_state": "published",
                    "published_at": datetime.now(timezone.utc),
                    "updated_at": datetime.now(timezone.utc),
                }},
            )
            _update_job(
                db,
                job_id,
                {"summary_publish_state": "published"},
                run_id=task_run_id,
            )
            # The artifact/state/job checkpoint and broker publish are durable;
            # a redelivery can now resume without the raw source audio.
            _cleanup_source_audio(storage, db, job_id, audio_object)
            logger.info(
                "Job %s transcription completed; async summary queued with %s chunks",
                job_id,
                state.get("total_chunks", 0),
            )
            return {"job_id": job_id, "summary_queued": True}

        # Save session to history
        guard(force_renew=True)
        meeting_type_info = MEETING_TYPES.get(meeting_type_id, {})
        session_created_at = datetime.now(timezone.utc)
        session_doc = {
            "job_id": job_id,
            "user_id": ObjectId(user_id),
            "audio_file": original_filename,
            "audio_length_seconds": result["audio_length_seconds"],
            "meeting_type_id": meeting_type_id,
            "meeting_type_name": meeting_type_info.get("thai", "ตรวจจับอัตโนมัติ"),
            "summary": result["summary"],
            "summary_metadata": result.get("summary_metadata", {}),
            "transcript": {
                "segments": result["full_transcript"]["segments"],
                "combined_text": result["full_transcript"]["combined_text"],
                "speaker_summary": result["full_transcript"]["speaker_summary"],
            },
            "processing_time": result["processing_time"],
            "segment_count": len(result["full_transcript"]["segments"]),
            "speaker_count": len(result["full_transcript"]["speaker_summary"]["speaking_time"]),
            "speaker_clips": speaker_clips_response,
            "clip_prefix": clip_prefix,
            "suggested_names": result.get("suggested_names", {}),
            "agendas": result.get("agendas", []),
            "detection_mode": result.get("detection_mode", "single_topic"),
            "detected_language": result.get("detected_language", "th"),
            "created_at": session_created_at,
            "clips_expires_at": session_created_at + timedelta(days=SPEAKER_CLIP_RETENTION_DAYS),
            "expires_at": session_created_at + timedelta(days=SESSION_RETENTION_DAYS),
            "retention": {
                "speaker_clips_days": SPEAKER_CLIP_RETENTION_DAYS,
                "session_days": SESSION_RETENTION_DAYS,
            },
            "transcription_run_id": task_run_id,
        }
        db.session.update_one(
            {"job_id": job_id},
            {"$setOnInsert": session_doc},
            upsert=True,
        )
        session_result = db.session.find_one({"job_id": job_id}, {"_id": 1})
        if not session_result:
            raise RuntimeError(f"Session upsert failed for job {job_id}")
        session_id = str(session_result["_id"])
        guard()

        # Build the result payload for the job
        job_result = {
            "success": True,
            "audio_file": original_filename,
            "audio_length_seconds": result["audio_length_seconds"],
            "processing_time": result["processing_time"],
            "transcript": {
                "segments": result["full_transcript"]["segments"],
                "combined_text": result["full_transcript"]["combined_text"],
                "speaker_summary": result["full_transcript"]["speaker_summary"],
            },
            "summary": result["summary"],
            "summary_metadata": result.get("summary_metadata", {}),
            "speaker_clips": speaker_clips_response,
            "clip_prefix": clip_prefix,
            "suggested_names": result.get("suggested_names", {}),
            "agendas": result.get("agendas", []),
            "detection_mode": result.get("detection_mode", "single_topic"),
            "detected_language": result.get("detected_language", "th"),
            "clips_expires_at": session_doc["clips_expires_at"],
        }

        terminal_update = db.job.update_one({
            "_id": ObjectId(job_id),
            "transcription_run_id": task_run_id,
            "status": {"$ne": "cancelled"},
            "result_available": {"$ne": True},
        }, {"$set": {
            "status": "completed",
            "current_step": "done",
            "progress": 100,
            "result": job_result,
            "result_available": True,
            "session_id": session_id,
            "completed_at": datetime.now(timezone.utc),
            "transcription_state": "completed",
        }})
        if terminal_update.matched_count == 0:
            existing = db.job.find_one({"_id": ObjectId(job_id)}) or {}
            if existing.get("status") == "cancelled":
                db.session.delete_one({"job_id": job_id, "transcription_run_id": task_run_id})
                raise JobCancelled(f"Job {job_id} was cancelled before final commit")
            if not existing.get("result_available"):
                db.session.delete_one({"job_id": job_id, "transcription_run_id": task_run_id})
                raise TranscriptionLeaseLost("Job terminal compare-and-set lost transcription ownership")
        settle_job_quota_db(db, job_id, "consumed")
        _cleanup_source_audio(storage, db, job_id, audio_object)
        _ensure_not_cancelled(db, job_id)

        logger.info(f"Job {job_id} completed successfully")

        # Auto-send results via email if requested. This runs AFTER the job is
        # marked completed so email failures cannot prevent the user from seeing
        # results in the UI.
        if email_recipient:
            _ensure_not_cancelled(db, job_id)
            from app.tasks.maintenance import enqueue_result_email

            enqueue_result_email(
                db=db,
                job_id=job_id,
                recipient=email_recipient,
                result_payload=job_result,
                meeting_type_id=meeting_type_id,
                original_filename=original_filename,
            )

        return {"job_id": job_id, "session_id": session_id}

    except TranscriptionLeaseLost as exc:
        logger.warning("Job {}: stale transcription delivery stopped: {}", job_id, exc)
        return {"job_id": job_id, "status": "superseded", "idempotent": True}

    except JobCancelled as exc:
        logger.info(f"Job {job_id} stopped after cancellation: {exc}")
        if session_id:
            try:
                db.session.delete_one({"_id": ObjectId(session_id)})
            except Exception:
                pass
        _update_job(db, job_id, {
            "status": "cancelled",
            "current_step": "cancelled",
            "cancellation_state": "requested",
            "cancellation_cleanup_status": "pending",
            "result_available": False,
            "email_status": "cancelled",
            "error": "Cancelled by admin",
            "cancelled_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }, allow_cancelled=True)
        _enqueue_cancel_cleanup(job_id)
        return {"job_id": job_id, "cancelled": True}

    except Exception as exc:
        if _is_job_cancelled(db, job_id):
            logger.info(f"Job {job_id} stopped after cancellation during error handling: {exc}")
            if session_id:
                try:
                    db.session.delete_one({"_id": ObjectId(session_id)})
                except Exception:
                    pass
            _update_job(db, job_id, {
                "status": "cancelled",
                "current_step": "cancelled",
                "cancellation_state": "requested",
                "cancellation_cleanup_status": "pending",
                "result_available": False,
                "email_status": "cancelled",
                "error": "Cancelled by admin",
                "cancelled_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
            }, allow_cancelled=True)
            _enqueue_cancel_cleanup(job_id)
            return {"job_id": job_id, "cancelled": True}

        logger.error(f"Job {job_id} failed: {exc}")

        # Check retry count FIRST — avoid briefly flipping to "failed" before
        # going back to "queued", which confuses the frontend status display.
        if self.request.retries < self.max_retries:
            logger.info(f"Job {job_id} retrying ({self.request.retries + 1}/{self.max_retries})")
            _update_job(db, job_id, {
                "status": "queued",
                "current_step": "retry",
                "progress": 0,
                "error": None,
                "transcription_state": "retrying",
                "transcription_lease_expires_at": datetime.now(timezone.utc),
            }, run_id=task_run_id)
            raise self.retry(exc=exc)

        # All retries exhausted — mark as permanently failed and refund quota.
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "progress": 0,
            "error": str(exc),
            "completed_at": datetime.now(timezone.utc),
            "audio_cleanup_state": "pending",
            "audio_cleanup_after": datetime.now(timezone.utc) + timedelta(hours=RAW_AUDIO_FAILSAFE_HOURS),
            "transcription_state": "failed",
            "transcription_lease_expires_at": datetime.now(timezone.utc),
        }, run_id=task_run_id)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning(f"Job {job_id}: quota refund failed after task failure: {refund_exc}")
        raise

    finally:
        if pipeline is not None:
            try:
                pipeline.close()
            except Exception as cleanup_exc:
                logger.warning(f"Job {job_id}: WhisperX cleanup failed: {cleanup_exc}")

        # Cleanup all local temp files
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
