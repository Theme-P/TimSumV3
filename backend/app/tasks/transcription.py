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
from app.services.storage import StorageService, BUCKET_AUDIO, BUCKET_CLIPS
from app.services.db import get_worker_db
from app.services.email_service import EmailService
from app.utils.export import export_transcript_to_docx, export_summary_to_docx
from app.models.meeting import MEETING_TYPES


def _get_storage() -> StorageService:
    """Get MinIO connection for the worker process."""
    return StorageService()


def _update_job(db, job_id: str, update: dict):
    """Update job document in MongoDB."""
    db.job.update_one({"_id": ObjectId(job_id)}, {"$set": update})


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
            )
            docx_files.append((summary_path, f"{file_name_no_ext}_Summary"))

            body = (
                f"เรียน คุณผู้ใช้งาน\n\n"
                f"เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว และระบบได้ส่งผลลัพธ์มาให้โดยอัตโนมัติ\n\n"
                f"รายละเอียด:\n"
                f"- ชื่อไฟล์: {original_filename}\n"
                f"- การประมวลผล: บันทึกเสียงและสรุปเอกสาร\n"
                f"- จำนวนไฟล์แนบ: {len(docx_files)} ไฟล์ (Summary + Transcript)\n\n"
                f"หมายเหตุ: หากต้องการผลลัพธ์พร้อมชื่อ Speaker ที่แก้ไขแล้ว สามารถเข้าไปแก้ที่หน้าเว็บและกด 'ส่งซ้ำ' ได้\n\n"
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
    max_retries=2,
    default_retry_delay=60,
    soft_time_limit=1800,   # 30 min soft limit
    time_limit=2100,        # 35 min hard limit
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

    try:
        # Mark job as processing
        _update_job(db, job_id, {
            "status": "processing",
            "current_step": "model_load",
            "progress": 5,
            "celery_task_id": self.request.id,
        })

        # Download audio from MinIO to temp file
        file_ext = os.path.splitext(audio_object)[1]
        local_audio = os.path.join(temp_dir, f"audio{file_ext}")
        storage.download_file(BUCKET_AUDIO, audio_object, local_audio)

        # Progress callback — pipeline calls this at each step
        def on_progress(step: str, progress: int):
            _update_job(db, job_id, {"current_step": step, "progress": progress})

        # Run the pipeline with live progress reporting
        pipeline = TranscribeSummaryPipeline()
        result = pipeline.process(
            local_audio,
            meeting_type_id=meeting_type_id,
            on_progress=on_progress,
            custom_prompt=custom_prompt,
            voice_samples=voice_samples,
        )

        # Pipeline completed — upload clips to MinIO
        _update_job(db, job_id, {"current_step": "saving", "progress": 95})

        clip_dir = result.get("clip_dir", "")
        clip_prefix = job_id  # clips stored under speaker-clips/{job_id}/
        speaker_clips_response = {}

        for speaker, clip_info in result.get("speaker_clips", {}).items():
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

        # Save session to history
        meeting_type_info = MEETING_TYPES.get(meeting_type_id, {})
        session_doc = {
            "user_id": ObjectId(user_id),
            "audio_file": original_filename,
            "audio_length_seconds": result["audio_length_seconds"],
            "meeting_type_id": meeting_type_id,
            "meeting_type_name": meeting_type_info.get("thai", "ตรวจจับอัตโนมัติ"),
            "summary": result["summary"],
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
            "created_at": datetime.now(timezone.utc),
        }
        session_result = db.session.insert_one(session_doc)
        session_id = str(session_result.inserted_id)

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
            "speaker_clips": speaker_clips_response,
            "clip_prefix": clip_prefix,
            "suggested_names": result.get("suggested_names", {}),
        }

        _update_job(db, job_id, {
            "status": "completed",
            "current_step": "done",
            "progress": 100,
            "result": job_result,
            "session_id": session_id,
            "completed_at": datetime.now(timezone.utc),
        })

        logger.info(f"Job {job_id} completed successfully")

        # Auto-send results via email if requested. This runs AFTER the job is
        # marked completed so email failures cannot prevent the user from seeing
        # results in the UI.
        if email_recipient:
            _auto_send_result_email(
                db=db,
                job_id=job_id,
                recipient=email_recipient,
                result_payload=job_result,
                meeting_type_id=meeting_type_id,
                original_filename=original_filename,
            )

        return {"job_id": job_id, "session_id": session_id}

    except Exception as exc:
        logger.error(f"Job {job_id} failed: {exc}")
        _update_job(db, job_id, {
            "status": "failed",
            "current_step": "error",
            "progress": 0,
            "error": str(exc),
            "completed_at": datetime.now(timezone.utc),
        })
        # Retry if attempts remaining
        if self.request.retries < self.max_retries:
            logger.info(f"Job {job_id} retrying ({self.request.retries + 1}/{self.max_retries})")
            _update_job(db, job_id, {"status": "queued", "current_step": "retry", "error": None})
            raise self.retry(exc=exc)
        raise

    finally:
        # Cleanup all local temp files
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)
