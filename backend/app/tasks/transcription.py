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
import tempfile
import shutil
from datetime import datetime, timezone

from bson import ObjectId
from loguru import logger

from app.celery_app import celery_app
from app.services.pipeline import TranscribeSummaryPipeline
from app.services.storage import StorageService, BUCKET_ARTIFACTS, BUCKET_AUDIO, BUCKET_CLIPS
from app.services.db import get_worker_db
from app.services.email_service import EmailService
from app.services.cancellation import JobCancelled
from app.utils.export import export_transcript_to_docx, export_summary_to_docx
from app.models.meeting import MEETING_TYPES


TASK_MAX_RETRIES = int(os.getenv("TRANSCRIBE_MAX_RETRIES", "2"))
TASK_RETRY_DELAY_SECONDS = int(os.getenv("TRANSCRIBE_RETRY_DELAY_SECONDS", "60"))
TASK_SOFT_TIME_LIMIT_SECONDS = int(os.getenv("TRANSCRIBE_SOFT_TIME_LIMIT_SECONDS", "1800"))
TASK_TIME_LIMIT_SECONDS = int(os.getenv("TRANSCRIBE_TIME_LIMIT_SECONDS", "2100"))


def _summary_pipeline_mode() -> str:
    return os.getenv("SUMMARY_PIPELINE_MODE", "inline").strip().lower()


def _async_summary_enabled() -> bool:
    return _summary_pipeline_mode() == "async"


def _get_storage() -> StorageService:
    """Get MinIO connection for the worker process."""
    return StorageService()


def _is_job_cancelled(db, job_id: str) -> bool:
    """Return True when the job has been cancelled in MongoDB."""
    try:
        doc = db.job.find_one({"_id": ObjectId(job_id)}, {"status": 1})
    except Exception:
        return False
    return bool(doc and doc.get("status") == "cancelled")


def _ensure_not_cancelled(db, job_id: str):
    if _is_job_cancelled(db, job_id):
        raise JobCancelled(f"Job {job_id} was cancelled")


def _update_job(db, job_id: str, update: dict, *, allow_cancelled: bool = False):
    """Update job document in MongoDB."""
    query = {"_id": ObjectId(job_id)}
    if not allow_cancelled:
        query["status"] = {"$ne": "cancelled"}
    return db.job.update_one(query, {"$set": update})


def _refund_job_quota_once(db, job_id: str) -> bool:
    """Refund a job's reserved package quota once."""
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


def _auto_send_result_email(
    db,
    job_id: str,
    recipient: str,
    result_payload: dict,
    meeting_type_id: int,
    original_filename: str,
) -> None:
    """
    Generate DOCX files and email them to the recipient.
    Failure here must NOT fail the job — we only update email_status.
    """
    if _is_job_cancelled(db, job_id):
        logger.info(f"Job {job_id}: skipping result email because job is cancelled")
        return

    _update_job(db, job_id, {"email_status": "sending"})

    email_svc = EmailService()
    if not email_svc.is_configured:
        logger.warning(f"Job {job_id}: SMTP not configured, skipping auto-send")
        _update_job(db, job_id, {
            "email_status": "failed",
            "email_error": "SMTP not configured on server",
        })
        return

    temp_dir = tempfile.mkdtemp(prefix="timsumv3_email_")
    try:
        file_name_no_ext = os.path.splitext(original_filename)[0] or "meeting"

        # Check if summary is valid
        is_summary_valid = bool(result_payload["summary"]) and "เกิดข้อผิดพลาดในการสรุปผล" not in result_payload["summary"]

        docx_files = []
        
        # Generate transcript DOCX
        transcript_path = os.path.join(temp_dir, f"{file_name_no_ext}_transcript.docx")
        export_transcript_to_docx(
            segments=result_payload["transcript"]["segments"],
            output_path=transcript_path,
            audio_file=result_payload["audio_file"],
            audio_length=result_payload["audio_length_seconds"],
        )
        docx_files.append((transcript_path, f"{file_name_no_ext}_Transcription"))

        if is_summary_valid:
            # Generate summary DOCX
            summary_path = os.path.join(temp_dir, f"{file_name_no_ext}_summary.docx")
            export_summary_to_docx(
                summary_text=result_payload["summary"],
                output_path=summary_path,
                speaker_summary=result_payload["transcript"]["speaker_summary"],
                meeting_type_id=meeting_type_id,
                agendas=result_payload.get("agendas"),
            )
            docx_files.append((summary_path, f"{file_name_no_ext}_Summary"))

            body = (
                f"เรียน คุณผู้ใช้งาน\n\n"
                f"เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว และระบบได้ส่งผลลัพธ์มาให้โดยอัตโนมัติ\n\n"
                f"รายละเอียด:\n"
                f"- ชื่อไฟล์: {original_filename}\n"
                f"- การประมวลผล: บันทึกเสียงและสรุปเอกสาร\n"
                f"- จำนวนไฟล์แนบ: {len(docx_files)} ไฟล์ (Summary + Transcript)\n\n"
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
                f"หมายเหตุ: ระบบสรุปเอกสารไม่สามารถใช้งานได้ในขณะนี้\n"
                f"คุณสามารถตรวจสอบไฟล์ Transcription เพื่อดูรายละเอียดครบถ้วน\n\n"
                f"ขอบคุณที่ใช้บริการ TimSum V3"
            )

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
    except Exception as e:
        logger.exception(f"Job {job_id}: email auto-send failed")
        _update_job(db, job_id, {
            "email_status": "failed",
            "email_error": str(e),
        })
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

    try:
        started_at = datetime.now(timezone.utc)
        queue_wait_seconds = None
        job_doc = db.job.find_one({"_id": ObjectId(job_id)}, {"created_at": 1})
        created_at = job_doc.get("created_at") if job_doc else None
        if created_at:
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            queue_wait_seconds = max((started_at - created_at).total_seconds(), 0)

        _ensure_not_cancelled(db, job_id)

        # Mark job as processing
        _update_job(db, job_id, {
            "status": "processing",
            "current_step": "model_load",
            "progress": 5,
            "celery_task_id": self.request.id,
            "started_at": started_at,
            "queue_wait_seconds": queue_wait_seconds,
        })
        _ensure_not_cancelled(db, job_id)

        # Download audio from MinIO to temp file
        file_ext = os.path.splitext(audio_object)[1]
        local_audio = os.path.join(temp_dir, f"audio{file_ext}")
        storage.download_file(BUCKET_AUDIO, audio_object, local_audio)
        _ensure_not_cancelled(db, job_id)

        # Progress callback — pipeline calls this at each step
        def on_progress(step: str, progress: int):
            _ensure_not_cancelled(db, job_id)
            _update_job(db, job_id, {"current_step": step, "progress": progress})
            _ensure_not_cancelled(db, job_id)

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
            cancellation_checker=lambda: _ensure_not_cancelled(db, job_id),
            run_summary=not async_summary,
        )
        _ensure_not_cancelled(db, job_id)

        # Pipeline completed — upload clips to MinIO
        _update_job(db, job_id, {"current_step": "saving", "progress": 95})
        _ensure_not_cancelled(db, job_id)

        clip_dir = result.get("clip_dir", "")
        clip_prefix = job_id  # clips stored under speaker-clips/{job_id}/
        speaker_clips_response = {}

        for speaker, clip_info in result.get("speaker_clips", {}).items():
            _ensure_not_cancelled(db, job_id)
            clip_filename = clip_info["clip_filename"]
            local_clip_path = os.path.join(clip_dir, clip_filename)

            if os.path.exists(local_clip_path):
                object_name = f"{clip_prefix}/{clip_filename}"
                storage.upload_file(BUCKET_CLIPS, object_name, local_clip_path, content_type="audio/mpeg")

            speaker_clips_response[speaker] = {
                "clip_filename": clip_filename,
                "start": clip_info["start"],
                "end": clip_info["end"],
                "duration": clip_info["duration"],
            }

        # Cleanup source audio from MinIO
        try:
            storage.delete_object(BUCKET_AUDIO, audio_object)
        except Exception:
            pass

        if async_summary:
            from app.tasks.summary import initialize_summary_state, process_next_chunk

            artifact_object = f"artifacts/{job_id}/transcript.json"
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
            }
            storage.upload_json(BUCKET_ARTIFACTS, artifact_object, artifact_payload)
            state = initialize_summary_state(
                db,
                job_id,
                artifact_object,
                result["full_transcript"]["segments"],
            )
            _update_job(db, job_id, {
                "status": "processing",
                "current_step": "summary_queued",
                "progress": 75,
                "transcript_artifact_object": artifact_object,
                "summary_status": "queued",
                "summary_progress": 0,
                "summary_completed_chunks": 0,
                "summary_total_chunks": state.get("total_chunks", 0),
                "covered_segments": 0,
                "total_segments": state.get("total_segments", 0),
                "partial_chunks": [],
                "coverage_complete": False,
            })
            process_next_chunk.apply_async(args=[job_id], queue="summary")
            logger.info(
                "Job %s transcription completed; async summary queued with %s chunks",
                job_id,
                state.get("total_chunks", 0),
            )
            return {"job_id": job_id, "summary_queued": True}

        # Save session to history
        _ensure_not_cancelled(db, job_id)
        meeting_type_info = MEETING_TYPES.get(meeting_type_id, {})
        session_doc = {
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
            "created_at": datetime.now(timezone.utc),
        }
        session_result = db.session.insert_one(session_doc)
        session_id = str(session_result.inserted_id)
        _ensure_not_cancelled(db, job_id)

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
        }

        _update_job(db, job_id, {
            "status": "completed",
            "current_step": "done",
            "progress": 100,
            "result": job_result,
            "session_id": session_id,
            "completed_at": datetime.now(timezone.utc),
        })
        _ensure_not_cancelled(db, job_id)

        logger.info(f"Job {job_id} completed successfully")

        # Auto-send results via email if requested. This runs AFTER the job is
        # marked completed so email failures cannot prevent the user from seeing
        # results in the UI.
        if email_recipient:
            _ensure_not_cancelled(db, job_id)
            _auto_send_result_email(
                db=db,
                job_id=job_id,
                recipient=email_recipient,
                result_payload=job_result,
                meeting_type_id=meeting_type_id,
                original_filename=original_filename,
            )

        return {"job_id": job_id, "session_id": session_id}

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
            "error": "Cancelled by admin",
            "cancelled_at": datetime.now(timezone.utc),
            "completed_at": datetime.now(timezone.utc),
        }, allow_cancelled=True)
        try:
            _refund_job_quota_once(db, job_id)
        except Exception as refund_exc:
            logger.warning(f"Job {job_id}: quota refund failed after cancellation: {refund_exc}")
        try:
            storage.delete_object(BUCKET_AUDIO, audio_object)
        except Exception:
            pass
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
                "error": "Cancelled by admin",
                "cancelled_at": datetime.now(timezone.utc),
                "completed_at": datetime.now(timezone.utc),
            }, allow_cancelled=True)
            try:
                _refund_job_quota_once(db, job_id)
            except Exception as refund_exc:
                logger.warning(f"Job {job_id}: quota refund failed after cancellation: {refund_exc}")
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
            })
            raise self.retry(exc=exc)

        # All retries exhausted — mark as permanently failed and refund quota.
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "progress": 0,
            "error": str(exc),
            "completed_at": datetime.now(timezone.utc),
        })
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
