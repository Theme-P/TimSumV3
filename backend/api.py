"""
FastAPI endpoint for Transcription-Summarization Pipeline.
Provides REST API for frontend integration.
"""
import os
import tempfile
import shutil
import uuid
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from io import BytesIO
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

# Load environment variables
load_dotenv()

# Import pipeline components
from app.models.meeting import MEETING_TYPES
from app.utils.export import export_transcript_to_docx, export_summary_to_docx
from app.services.email_service import EmailService
from app.services.storage import StorageService, get_storage_service, BUCKET_AUDIO, BUCKET_CLIPS
from app.tasks.transcription import process_audio

# New Services & Routers
from app.services.mongo import MongoService
from app.routers.auth import router as auth_router
from app.routers.quota import router as quota_router
from app.routers.admin import router as admin_router
from app.routers.package import router as package_router
from app.core.auth import get_current_user
from app.models.user import UserData

# Initialize FastAPI app
app = FastAPI(
    title="TimSumV3 API",
    description="Merged Transcription-Summarization API with GPT-4.1",
    version="3.0.0"
)

# Rate Limiting
limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Max upload size
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))


def get_mongo_service(request: Request) -> MongoService:
    """Get MongoDB service from app state."""
    return request.app.state.mongo_service


def get_storage(request: Request) -> StorageService:
    """Get MinIO storage service from app state."""
    return request.app.state.storage_service


# Enable CORS for frontend (whitelist from env)
_allowed_origins = os.getenv("ALLOWED_ORIGINS", "http://localhost:3000")
allowed_origins = [o.strip() for o in _allowed_origins.split(",")]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

# Initialize Services
mongo_uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://localhost:27017")
mongo_db = os.getenv("MONGO_DB_NAME", "timsumv3")
app.state.mongo_service = MongoService(uri=mongo_uri, db_name=mongo_db)
app.state.storage_service = get_storage_service()

# Include Routers
app.include_router(auth_router)
app.include_router(quota_router)
app.include_router(admin_router)
app.include_router(package_router)


# ── Auto-create superadmin & admin users on startup ──
def _ensure_default_users():
    """Create superadmin and admin users if they don't exist yet."""
    from app.models.user import User, Quota
    from bson import ObjectId

    mongo = app.state.mongo_service

    defaults = [
        {
            "username": os.getenv("SUPERADMIN_USERNAME", "superadmin"),
            "email": os.getenv("SUPERADMIN_EMAIL", "superadmin@timsumv3.local"),
            "password": os.getenv("SUPERADMIN_PASS", "TimSum@SuperAdmin2026"),
            "role": "superadmin",
        },
        {
            "username": os.getenv("ADMIN_USERNAME", "admin"),
            "email": os.getenv("ADMIN_EMAIL", "admin@timsumv3.local"),
            "password": os.getenv("ADMIN_PASS", "TimSum@Admin2026"),
            "role": "admin",
        },
    ]

    for cfg in defaults:
        try:
            if mongo.get_user_by_email(cfg["email"]):
                continue
            user = User(
                _id=ObjectId(),
                username=cfg["username"],
                email=cfg["email"],
                password=cfg["password"],
                role=cfg["role"],
                status="approved",
            )
            quota = Quota(
                _id=ObjectId(),
                user_id=user.id,
                value1=100, value2=100, value3=100, value4=100,
            )
            mongo.create_user(user)
            mongo.create_quota(quota)
            print(f"✅ {cfg['role']} user auto-created: {cfg['email']}")
        except Exception as e:
            print(f"⚠️ Could not auto-create {cfg['role']}: {e}")

_ensure_default_users()


# ── Migrate legacy users: add status field if missing ──
def _migrate_users_status():
    mongo = app.state.mongo_service
    result = mongo.db.user.update_many(
        {"status": {"$exists": False}},
        {"$set": {"status": "approved"}},
    )
    if result.modified_count > 0:
        print(f"✅ Migrated {result.modified_count} legacy user(s): added status='approved'")

_migrate_users_status()


# ── Seed default packages & assign to default users ──
def _seed_packages():
    from app.models.package import DEFAULT_PACKAGES, ADMIN_PACKAGE, SUPERADMIN_PACKAGE

    mongo = app.state.mongo_service

    # Seed public packages
    for pkg in DEFAULT_PACKAGES:
        pkg_copy = {**pkg, "is_active": True}
        mongo.upsert_package(pkg_copy)

    # Seed internal admin packages
    for pkg in [ADMIN_PACKAGE, SUPERADMIN_PACKAGE]:
        pkg_copy = {**pkg, "is_active": True}
        mongo.upsert_package(pkg_copy)

    # Auto-assign packages to default users if they don't have one
    sa_email = os.getenv("SUPERADMIN_EMAIL", "superadmin@timsumv3.local")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@timsumv3.local")

    for email, pkg_name in [(sa_email, "TimSumSuperAdmin"), (admin_email, "TimSumAdmin")]:
        user = mongo.get_user_by_email(email)
        if not user:
            continue
        existing = mongo.get_user_package(str(user.id))
        if existing:
            continue
        pkg = mongo.get_package_by_name(pkg_name)
        if pkg:
            mongo.assign_user_package(str(user.id), pkg["_id"], assigned_by="system")
            print(f"✅ Assigned {pkg_name} to {email}")

_seed_packages()

# ===================== RESPONSE MODELS =====================

class HealthResponse(BaseModel):
    status: str
    message: str

class MeetingTypeInfo(BaseModel):
    id: int
    name: str
    thai: str
    structure: str
    key_focus: str

class MeetingTypesResponse(BaseModel):
    success: bool
    meeting_types: list[MeetingTypeInfo]

# Request models for export
class TranscriptSegment(BaseModel):
    start: float
    end: float
    text: str
    speaker: str = None

class ExportTranscriptRequest(BaseModel):
    segments: List[TranscriptSegment]
    audio_file: str = ""
    audio_length_seconds: float = 0

class ExportSummaryRequest(BaseModel):
    summary: str
    speaker_summary: dict = None  # Optional: speaking_time and word_count per speaker
    meeting_type_id: int = 0  # Meeting type for position formatting


# ===================== ENDPOINTS =====================

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint"""
    return HealthResponse(
        status="healthy",
        message="Transcribe-Summary API is running"
    )


@app.get("/api/meeting-types", response_model=MeetingTypesResponse)
async def get_meeting_types():
    """Get list of available meeting types"""
    types_list = []
    for type_id, info in MEETING_TYPES.items():
        types_list.append(MeetingTypeInfo(
            id=type_id,
            name=info['name'],
            thai=info['thai'],
            structure=info['structure'],
            key_focus=info.get('key_focus', '')
        ))
    
    return MeetingTypesResponse(
        success=True,
        meeting_types=types_list
    )


@app.post("/api/transcribe-summarize")
@limiter.limit("10/minute")
async def transcribe_summarize(
    request: Request,
    audio: UploadFile = File(..., description="Audio file to transcribe"),
    meeting_type_id: int = Form(0, description="Meeting type ID (0=auto-detect, 1-11=specific type)"),
    email_recipient: str = Form("", description="Optional: email to auto-send results to"),
    custom_prompt: str = Form("", description="Optional: custom instruction for summary (max 500 chars)"),
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """
    Submit audio file for async transcription and summarization.
    Returns a job_id immediately — poll /api/jobs/{job_id} for progress.
    If email_recipient is provided, results will be auto-sent when processing completes.
    """
    # Check package limits
    limit_check = mongo_service.check_package_limits(str(user.id))
    if not limit_check.get("allowed"):
        raise HTTPException(status_code=403, detail=limit_check.get("reason", "เกินโควต้าการใช้งาน"))

    # Validate meeting type
    if meeting_type_id < 0 or meeting_type_id > 11:
        raise HTTPException(status_code=400, detail="meeting_type_id must be between 0 and 11")

    # Lightweight email validation (full RFC validation is not worth it; SMTP server is the source of truth)
    email_recipient = (email_recipient or "").strip()
    if email_recipient and ("@" not in email_recipient or "." not in email_recipient.split("@")[-1]):
        raise HTTPException(status_code=400, detail="Invalid email_recipient format")

    # Validate custom prompt
    custom_prompt = (custom_prompt or "").strip()
    if len(custom_prompt) > 500:
        raise HTTPException(status_code=400, detail="custom_prompt ต้องไม่เกิน 500 ตัวอักษร")

    # Validate file type
    allowed_extensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.webm', '.mp4']
    file_ext = os.path.splitext(audio.filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}"
        )

    # Upload to MinIO
    file_id = str(uuid.uuid4())
    object_name = f"{file_id}{file_ext}"

    try:
        content = await audio.read()

        # Server-side file size validation
        max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        if len(content) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File too large. Maximum size: {MAX_UPLOAD_MB} MB"
            )

        storage.upload_stream(
            BUCKET_AUDIO, object_name,
            BytesIO(content), len(content),
            content_type=audio.content_type or "audio/mpeg",
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save upload: {str(e)}")

    # Create job in MongoDB (store MinIO object name instead of file path)
    job_id = mongo_service.create_job(
        user_id=user.id,
        audio_file=audio.filename,
        meeting_type_id=meeting_type_id,
        audio_path=object_name,
        email_recipient=email_recipient,
    )

    # Send task to Celery worker
    process_audio.delay(
        job_id=job_id,
        audio_object=object_name,
        original_filename=audio.filename,
        meeting_type_id=meeting_type_id,
        user_id=str(user.id),
        email_recipient=email_recipient,
        custom_prompt=custom_prompt,
    )

    # Increment usage counters (files + ai summaries)
    mongo_service.increment_usage(str(user.id), files=1, ai_summaries=1)

    return {"success": True, "job_id": job_id, "status": "queued"}


@app.get("/api/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Poll job progress. Returns status, current_step, progress (0-100)."""
    job = mongo_service.get_job(job_id, user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.get("/api/jobs/{job_id}/result")
async def get_job_result(
    job_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get full result once job is completed."""
    job = mongo_service.get_job_result(job_id, user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not completed")

    result = job.get("result", {})
    # clip_prefix is the MinIO prefix for speaker clips (e.g. "job_id/")
    result["session_id"] = result.get("clip_prefix", job_id)
    return result


# ===================== EXPORT ENDPOINTS =====================

@app.post("/api/export/transcript")
async def export_transcript(request: ExportTranscriptRequest, _user: UserData = Depends(get_current_user)):
    """
    Export transcript segments to DOCX file.
    """
    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, "transcript.docx")
    
    try:
        # Convert segments to dict format
        segments = [seg.model_dump() for seg in request.segments]
        
        # Generate DOCX
        export_transcript_to_docx(
            segments=segments,
            output_path=output_path,
            audio_file=request.audio_file,
            audio_length=request.audio_length_seconds
        )
        
        return FileResponse(
            path=output_path,
            filename="transcript.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True)
        )
    except Exception as e:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


@app.post("/api/export/summary")
async def export_summary(request: ExportSummaryRequest, _user: UserData = Depends(get_current_user)):
    """
    Export summary text to DOCX file.0
    """
    temp_dir = tempfile.mkdtemp()
    output_path = os.path.join(temp_dir, "summary.docx")
    
    try:
        # Generate DOCX with optional speaker header section and meeting type
        export_summary_to_docx(
            summary_text=request.summary,
            output_path=output_path,
            speaker_summary=request.speaker_summary,
            meeting_type_id=request.meeting_type_id
        )
        
        return FileResponse(
            path=output_path,
            filename="summary.docx",
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            background=BackgroundTask(shutil.rmtree, temp_dir, ignore_errors=True)
        )
    except Exception as e:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        raise HTTPException(status_code=500, detail=f"Export error: {str(e)}")


# ===================== SPEAKER CLIP ENDPOINTS =====================

@app.get("/api/speaker-clip/{session_id}/{filename}")
async def get_speaker_clip(
    session_id: str,
    filename: str,
    _user: UserData = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
):
    """
    Serve a speaker audio clip from MinIO.

    - **session_id**: clip_prefix from job result (typically job_id)
    - **filename**: Clip filename (e.g., speaker_0.mp3)
    """
    # Validate filename (prevent path traversal)
    if '/' in filename or '..' in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    object_name = f"{session_id}/{filename}"
    if not storage.object_exists(BUCKET_CLIPS, object_name):
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_bytes = storage.get_object_bytes(BUCKET_CLIPS, object_name)
    return StreamingResponse(
        BytesIO(clip_bytes),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.delete("/api/session/{session_id}")
async def cleanup_session(
    session_id: str,
    _user: UserData = Depends(get_current_user),
    storage: StorageService = Depends(get_storage),
):
    """
    Cleanup speaker clips for a session from MinIO.
    """
    storage.delete_prefix(BUCKET_CLIPS, prefix=f"{session_id}/")
    return {"success": True, "message": "Session cleaned up"}


# ===================== EMAIL ENDPOINT (Optional) =====================

class EmailResultsRequest(BaseModel):
    recipient_email: str
    file_name: str
    summary: str
    segments: List[TranscriptSegment] = []
    audio_file: str = ""
    audio_length_seconds: float = 0
    speaker_summary: dict = None
    meeting_type_id: int = 0


@app.post("/api/email-results")
async def email_results(request: EmailResultsRequest, _user: UserData = Depends(get_current_user)):
    """
    Generate DOCX files and send them via email.
    Requires SMTP configuration in .env
    """
    email_svc = EmailService()
    if not email_svc.is_configured:
        raise HTTPException(status_code=503, detail="Email service not configured. Set SMTP_SERVER and SENDER_EMAIL in .env")

    temp_dir = tempfile.mkdtemp()
    try:
        docx_files = []

        # Generate summary DOCX
        summary_path = os.path.join(temp_dir, f"{request.file_name}_summary.docx")
        export_summary_to_docx(
            summary_text=request.summary,
            output_path=summary_path,
            speaker_summary=request.speaker_summary,
            meeting_type_id=request.meeting_type_id
        )
        docx_files.append((summary_path, f"{request.file_name}_Summary"))

        # Generate transcript DOCX if segments provided
        if request.segments:
            transcript_path = os.path.join(temp_dir, f"{request.file_name}_transcript.docx")
            segments = [seg.model_dump() for seg in request.segments]
            export_transcript_to_docx(
                segments=segments,
                output_path=transcript_path,
                audio_file=request.audio_file,
                audio_length=request.audio_length_seconds
            )
            docx_files.append((transcript_path, f"{request.file_name}_Transcription"))

        # Send email
        body = f"""เรียน คุณผู้ใช้งาน

เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว

รายละเอียด:
- ชื่อไฟล์: {request.file_name}
- จำนวนไฟล์แนบ: {len(docx_files)} ไฟล์

กรุณาดาวน์โหลดไฟล์ที่แนบมาและตรวจสอบผลการประมวลผล

ขอบคุณที่ใช้บริการ TimSum V3"""

        success = email_svc.send_email_with_attachments(
            recipient_email=request.recipient_email,
            subject=f"Document Processing Complete - {request.file_name}",
            body_text=body,
            docx_files=docx_files,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to send email")

        return {"success": True, "message": f"Email sent to {request.recipient_email}"}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Email error: {str(e)}")
    finally:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir, ignore_errors=True)


# ===================== HISTORY ENDPOINTS =====================

@app.get("/api/history")
async def get_history(
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get user's session history (lightweight list without full transcript)."""
    sessions = mongo_service.get_sessions_by_user(user.id)
    return {"success": True, "sessions": sessions}


@app.get("/api/history/{session_id}")
async def get_history_detail(
    session_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get full session detail including transcript and summary."""
    session = mongo_service.get_session_by_id(session_id, user.id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"success": True, "session": session}


# ===================== STARTUP =====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
