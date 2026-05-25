"""
Voice Samples API router — upload, list, play, delete voice enrollment clips.

All endpoints are user-scoped (each user sees only their own samples).
Upload requires package permission: voice_enrollment_enabled.
"""
import os
import uuid
from io import BytesIO

from bson import ObjectId
from fastapi import APIRouter, File, Form, UploadFile, HTTPException, Depends, Request
from fastapi.responses import StreamingResponse

from app.core.auth import get_current_user
from app.models.user import UserData
from app.models.voice_sample import (
    MAX_VOICE_SAMPLES_PER_USER,
    MAX_VOICE_SAMPLE_MB,
    ALLOWED_VOICE_EXTENSIONS,
)
from app.services.mongo import MongoService
from app.services.storage import StorageService, BUCKET_VOICE_SAMPLES

router = APIRouter(prefix="/api/voice-samples", tags=["voice-samples"])


def _get_mongo(request: Request) -> MongoService:
    return request.app.state.mongo_service


def _get_storage(request: Request) -> StorageService:
    return request.app.state.storage_service


@router.post("")
async def upload_voice_sample(
    request: Request,
    audio: UploadFile = File(..., description="Voice audio clip (5-30s recommended)"),
    speaker_name: str = Form(..., description="Speaker name (e.g. คุณเจษฎา)"),
    speaker_position: str = Form("", description="Speaker position (optional)"),
    user: UserData = Depends(get_current_user),
    mongo: MongoService = Depends(_get_mongo),
    storage: StorageService = Depends(_get_storage),
):
    """
    Upload a voice sample for speaker enrollment.

    Extracts speaker embedding and stores the clip in MinIO.
    Requires package permission: voice_enrollment_enabled.
    """
    # Check package permission
    user_pkg = mongo.get_user_package(str(user.id))
    if not user_pkg or not user_pkg.get("package", {}).get("limits", {}).get("voice_enrollment_enabled"):
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
    speaker_name = speaker_name.strip()
    if not speaker_name or len(speaker_name) > 100:
        raise HTTPException(status_code=400, detail="กรุณาระบุชื่อผู้พูด (ไม่เกิน 100 ตัวอักษร)")

    # Validate file type
    file_ext = os.path.splitext(audio.filename or "")[1].lower()
    if file_ext not in ALLOWED_VOICE_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"ไม่รองรับไฟล์ประเภทนี้ รองรับ: {', '.join(ALLOWED_VOICE_EXTENSIONS)}",
        )

    # Read and validate file size
    content = await audio.read()
    max_bytes = MAX_VOICE_SAMPLE_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"ไฟล์ใหญ่เกินไป สูงสุด {MAX_VOICE_SAMPLE_MB} MB",
        )

    # Upload to MinIO
    file_id = str(uuid.uuid4())
    object_name = f"{user.id}/{file_id}{file_ext}"

    try:
        storage.upload_stream(
            BUCKET_VOICE_SAMPLES,
            object_name,
            BytesIO(content),
            len(content),
            content_type=audio.content_type or "audio/mpeg",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ไม่สามารถบันทึกไฟล์ได้: {str(e)}")

    # Extract speaker embedding
    import tempfile
    tmp_path = None
    embedding = []
    duration_seconds = 0.0

    try:
        # Write to temp file for embedding extraction
        fd, tmp_path = tempfile.mkstemp(suffix=file_ext)
        os.close(fd)
        with open(tmp_path, "wb") as f:
            f.write(content)

        from app.services.voice_matching import VoiceMatchingService
        matcher = VoiceMatchingService(device="cpu")  # CPU for upload endpoint
        embedding = matcher.extract_embedding(tmp_path)
        duration_seconds = matcher.get_audio_duration(tmp_path)
    except Exception as e:
        # Clean up MinIO if embedding fails
        try:
            storage.delete_object(BUCKET_VOICE_SAMPLES, object_name)
        except Exception:
            pass
        raise HTTPException(
            status_code=500,
            detail=f"ไม่สามารถวิเคราะห์เสียงได้: {str(e)}",
        )
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    # Save to MongoDB
    from datetime import datetime, timezone

    sample_doc = {
        "_id": ObjectId(),
        "user_id": ObjectId(str(user.id)),
        "speaker_name": speaker_name,
        "speaker_position": (speaker_position or "").strip(),
        "audio_path": object_name,
        "embedding": embedding,
        "duration_seconds": duration_seconds,
        "original_filename": audio.filename or "",
        "created_at": datetime.now(timezone.utc),
    }

    sample_id = mongo.create_voice_sample(sample_doc)

    return {
        "success": True,
        "sample": {
            "_id": sample_id,
            "speaker_name": speaker_name,
            "speaker_position": (speaker_position or "").strip(),
            "duration_seconds": duration_seconds,
            "original_filename": audio.filename or "",
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
    filename = sample.get("original_filename", "voice_sample.mp3")

    return StreamingResponse(
        BytesIO(clip_bytes),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
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
            pass  # Continue even if MinIO delete fails

    # Delete from MongoDB
    mongo.delete_voice_sample(sample_id, str(user.id))

    return {"success": True, "message": "ลบตัวอย่างเสียงเรียบร้อยแล้ว"}
