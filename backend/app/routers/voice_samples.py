"""
Voice Samples API router — upload, list, play, delete voice enrollment clips.

All endpoints are user-scoped (each user sees only their own samples).
Upload requires package permission: voice_enrollment_enabled.
"""
import os
import uuid
import asyncio
import tempfile
import threading
from datetime import datetime, timezone
from io import BytesIO

from bson import ObjectId
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth import get_current_user, get_current_consented_user
from app.models.user import UserData
from app.models.voice_sample import (
    MAX_VOICE_SAMPLES_PER_USER,
    MAX_VOICE_SAMPLE_MB,
    ALLOWED_VOICE_EXTENSIONS,
)
from app.services.mongo import MongoService
from app.services.storage import StorageService, BUCKET_VOICE_SAMPLES
from app.services.rate_limit import VOICE_USER, enforce_rate_limit

router = APIRouter(prefix="/api/voice-samples", tags=["voice-samples"])
_voice_matcher = None
_voice_inference_lock = threading.Lock()
_VOICE_CONTENT_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".flac": "audio/flac",
    ".ogg": "audio/ogg",
    ".webm": "audio/webm",
}


def _get_mongo(request: Request) -> MongoService:
    return request.app.state.mongo_service


def _get_storage(request: Request) -> StorageService:
    return request.app.state.storage_service


def _analyze_voice_sample(file_path: str) -> tuple[list[float], float]:
    """Run the singleton CPU model outside the event loop, one inference at a time."""
    global _voice_matcher
    from app.services.voice_matching import VoiceMatchingService

    with _voice_inference_lock:
        if _voice_matcher is None:
            _voice_matcher = VoiceMatchingService(device="cpu")
        duration = float(_voice_matcher.get_audio_duration(file_path))
        if duration < 5 or duration > 30:
            raise ValueError("duration_out_of_range")
        embedding = _voice_matcher.extract_embedding(file_path)
        return embedding, duration


def _validate_display_text(value: str, *, field: str, max_length: int) -> str:
    normalized = (value or "").strip()
    if not normalized or len(normalized) > max_length:
        raise HTTPException(status_code=400, detail=f"{field} ไม่ถูกต้อง")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        raise HTTPException(status_code=400, detail=f"{field} ไม่ถูกต้อง")
    return normalized


def _has_voice_entitlement(mongo: MongoService, user_id: ObjectId) -> bool:
    assignment = mongo.db.user_package.find_one({
        "user_id": user_id,
        "status": "active",
        "$or": [
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": {"$gt": datetime.now(timezone.utc)}},
        ],
    })
    if not assignment:
        return False
    package = mongo.db.package.find_one({
        "_id": assignment.get("package_id"),
        "is_active": {"$ne": False},
    })
    return bool((package or {}).get("limits", {}).get("voice_enrollment_enabled"))


def _ensure_user_can_store_voice(mongo: MongoService, user_id: ObjectId) -> None:
    """Close account-deletion races around the external storage side effect."""
    current = mongo.db.user.find_one(
        {"_id": user_id},
        {"status": 1, "deletion_pending": 1},
    )
    if not current or current.get("deletion_pending") or current.get("status") != "approved":
        raise HTTPException(status_code=409, detail="บัญชีไม่พร้อมสำหรับการเพิ่มตัวอย่างเสียง")


@router.post("")
async def upload_voice_sample(
    request: Request,
    audio: UploadFile = File(..., description="Voice audio clip (5-30s recommended)"),
    speaker_name: str = Form(..., description="Speaker name (e.g. คุณเจษฎา)"),
    speaker_position: str = Form("", description="Speaker position (optional)"),
    user: UserData = Depends(get_current_consented_user),
    mongo: MongoService = Depends(_get_mongo),
    storage: StorageService = Depends(_get_storage),
):
    """
    Upload a voice sample for speaker enrollment.

    Extracts speaker embedding and stores the clip in MinIO.
    Requires package permission: voice_enrollment_enabled.
    """
    enforce_rate_limit(request, VOICE_USER, str(user.id))

    # Check package permission
    if not _has_voice_entitlement(mongo, user.id):
        raise HTTPException(
            status_code=403,
            detail="แพ็กเกจของคุณไม่รองรับคลังเสียง กรุณาอัปเกรดเป็น Pro ขึ้นไป",
        )

    # Check sample count limit
    count = mongo.count_voice_samples(str(user.id))
    if count >= MAX_VOICE_SAMPLES_PER_USER:
        raise HTTPException(
            status_code=400,
            detail=f"ตัวอย่างเสียงเต็มแล้ว (สูงสุด {MAX_VOICE_SAMPLES_PER_USER} ตัวอย่าง)",
        )

    # Validate speaker name
    speaker_name = _validate_display_text(speaker_name, field="ชื่อผู้พูด", max_length=100)
    speaker_position = (speaker_position or "").strip()
    if len(speaker_position) > 100 or any(ord(c) < 32 or ord(c) == 127 for c in speaker_position):
        raise HTTPException(status_code=400, detail="ตำแหน่งผู้พูดไม่ถูกต้อง")

    original_filename = _validate_display_text(
        audio.filename or "voice-sample",
        field="ชื่อไฟล์",
        max_length=180,
    )
    if "/" in original_filename or "\\" in original_filename:
        raise HTTPException(status_code=400, detail="ชื่อไฟล์ไม่ถูกต้อง")

    # Validate file type
    file_ext = os.path.splitext(original_filename)[1].lower()
    if file_ext not in ALLOWED_VOICE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"ไม่รองรับไฟล์ประเภทนี้ รองรับ: {', '.join(ALLOWED_VOICE_EXTENSIONS)}",
        )

    # Stream to a server-generated path and stop reading as soon as the limit is
    # exceeded. The request body is never accumulated in memory.
    max_bytes = MAX_VOICE_SAMPLE_MB * 1024 * 1024
    object_name = f"{user.id}/{uuid.uuid4()}{file_ext}"
    tmp_path = None
    total_bytes = 0
    fd, tmp_path = tempfile.mkstemp(suffix=file_ext)
    try:
        with os.fdopen(fd, "wb") as temporary:
            while True:
                chunk = await audio.read(256 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"ไฟล์ใหญ่เกินไป สูงสุด {MAX_VOICE_SAMPLE_MB} MB",
                    )
                temporary.write(chunk)
        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="ไฟล์เสียงว่างเปล่า")

        try:
            embedding, duration_seconds = await asyncio.to_thread(_analyze_voice_sample, tmp_path)
        except ValueError as exc:
            if str(exc) == "duration_out_of_range":
                raise HTTPException(status_code=400, detail="ตัวอย่างเสียงต้องยาว 5–30 วินาที")
            raise HTTPException(status_code=422, detail="ไม่สามารถอ่านระยะเวลาไฟล์เสียงได้")
        except Exception:
            raise HTTPException(status_code=422, detail="ไม่สามารถวิเคราะห์ไฟล์เสียงได้")

        # UploadFile.content_type is client-controlled. Persist a safe audio
        # MIME derived from the validated container extension so playback can
        # never turn the authenticated API origin into an inline HTML host.
        content_type = _VOICE_CONTENT_TYPES[file_ext]
        _ensure_user_can_store_voice(mongo, user.id)
        try:
            storage.upload_file(
                BUCKET_VOICE_SAMPLES,
                object_name,
                tmp_path,
                content_type=content_type,
            )
        except Exception:
            raise HTTPException(status_code=503, detail="ไม่สามารถบันทึกไฟล์เสียงได้")
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Save to MongoDB

    sample_doc = {
        "_id": ObjectId(),
        "user_id": ObjectId(str(user.id)),
        "speaker_name": speaker_name,
        "speaker_position": speaker_position,
        "audio_path": object_name,
        "embedding": embedding,
        "duration_seconds": duration_seconds,
        "original_filename": original_filename,
        "content_type": content_type,
        "created_at": datetime.now(timezone.utc),
    }

    try:
        _ensure_user_can_store_voice(mongo, user.id)
        sample_id = mongo.create_voice_sample(sample_doc)
    except HTTPException:
        try:
            storage.delete_object(BUCKET_VOICE_SAMPLES, object_name)
        except Exception:
            pass
        raise
    except ValueError as exc:
        try:
            storage.delete_object(BUCKET_VOICE_SAMPLES, object_name)
        except Exception:
            pass
        raise HTTPException(
            status_code=409,
            detail=f"ตัวอย่างเสียงเต็มแล้ว (สูงสุด {MAX_VOICE_SAMPLES_PER_USER} ตัวอย่าง)",
        ) from exc
    except Exception:
        try:
            storage.delete_object(BUCKET_VOICE_SAMPLES, object_name)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="ไม่สามารถบันทึกข้อมูลตัวอย่างเสียงได้")

    return {
        "success": True,
        "sample": {
            "_id": sample_id,
            "speaker_name": speaker_name,
            "speaker_position": speaker_position,
            "duration_seconds": duration_seconds,
            "original_filename": original_filename,
            "created_at": sample_doc["created_at"].isoformat(),
        },
    }


@router.get("")
async def list_voice_samples(
    user: UserData = Depends(get_current_user),
    mongo: MongoService = Depends(_get_mongo),
):
    """List all voice samples for the current user."""
    samples = mongo.get_voice_samples_by_user(str(user.id))
    return {"success": True, "samples": samples, "count": len(samples)}


@router.get("/{sample_id}/play")
async def play_voice_sample(
    sample_id: str,
    user: UserData = Depends(get_current_user),
    mongo: MongoService = Depends(_get_mongo),
    storage: StorageService = Depends(_get_storage),
):
    """Stream a voice sample audio clip."""
    sample = mongo.get_voice_sample_by_id(sample_id, str(user.id))
    if not sample:
        raise HTTPException(status_code=404, detail="ไม่พบตัวอย่างเสียงนี้")

    audio_path = sample.get("audio_path", "")
    if not audio_path or not storage.object_exists(BUCKET_VOICE_SAMPLES, audio_path):
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์เสียง")

    clip_bytes = storage.get_object_bytes(BUCKET_VOICE_SAMPLES, audio_path)
    extension = os.path.splitext(audio_path)[1].lower()
    content_type = _VOICE_CONTENT_TYPES.get(extension, "application/octet-stream")

    return StreamingResponse(
        BytesIO(clip_bytes),
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="voice-sample{os.path.splitext(sample.get("audio_path", ""))[1]}"'},
    )


@router.delete("/{sample_id}")
async def delete_voice_sample(
    sample_id: str,
    user: UserData = Depends(get_current_user),
    mongo: MongoService = Depends(_get_mongo),
    storage: StorageService = Depends(_get_storage),
):
    """Delete a voice sample (MinIO + MongoDB)."""
    sample = mongo.get_voice_sample_by_id(sample_id, str(user.id))
    if not sample:
        raise HTTPException(status_code=404, detail="ไม่พบตัวอย่างเสียงนี้")

    # Delete from MinIO
    audio_path = sample.get("audio_path", "")
    if audio_path:
        try:
            storage.delete_object(BUCKET_VOICE_SAMPLES, audio_path)
        except Exception:
            raise HTTPException(status_code=503, detail="ลบไฟล์เสียงไม่สำเร็จ กรุณาลองอีกครั้ง")

    # Delete from MongoDB
    mongo.delete_voice_sample(sample_id, str(user.id))

    return {"success": True, "message": "ลบตัวอย่างเสียงเรียบร้อยแล้ว"}
