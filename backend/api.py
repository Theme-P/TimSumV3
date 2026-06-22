"""
FastAPI endpoint for Transcription-Summarization Pipeline.
Provides REST API for frontend integration.
"""
import os
import tempfile
import shutil
import uuid
import subprocess
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List
from io import BytesIO
from dotenv import load_dotenv
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from bson import ObjectId

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
from app.services.cache import CacheService
from app.routers.auth import router as auth_router
from app.routers.quota import router as quota_router
from app.routers.admin import router as admin_router
from app.routers.package import router as package_router
from app.routers.user import router as user_router
from app.routers.voice_samples import router as voice_samples_router
from app.routers.activity import router as activity_router
from app.routers.consent import router as consent_router
from app.routers.queue import router as queue_router
from app.routers.system_admin import router as system_admin_router
from app.routers.meeting_template import router as meeting_template_router
from app.routers.llm_config import router as llm_config_router
from app.core.auth import get_current_user, get_current_consented_user
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


def _user_owns_clip_prefix(mongo_service: MongoService, user_id: ObjectId, clip_prefix: str) -> bool:
    """Return True when the requested speaker-clip prefix belongs to the user."""
    if not clip_prefix or "/" in clip_prefix or "\\" in clip_prefix or ".." in clip_prefix:
        return False

    try:
        if ObjectId.is_valid(clip_prefix):
            clip_obj_id = ObjectId(clip_prefix)
            if mongo_service.db.job.find_one({"_id": clip_obj_id, "user_id": user_id}, {"_id": 1}):
                return True

        return bool(mongo_service.db.session.find_one(
            {"clip_prefix": clip_prefix, "user_id": user_id},
            {"_id": 1},
        ))
    except Exception:
        return False


def _probe_audio_duration_seconds(file_path: str) -> float:
    """Read audio/video duration with ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if result.returncode != 0:
            raise ValueError(result.stderr.strip() or "ffprobe failed")
        duration = float(result.stdout.strip())
        if duration <= 0:
            raise ValueError("Invalid duration")
        return duration
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Cannot read audio duration: {exc}")


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
cache_service = CacheService()
mongo_uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://localhost:27017")
mongo_db = os.getenv("MONGO_DB_NAME", "timsumv3")
app.state.mongo_service = MongoService(uri=mongo_uri, db_name=mongo_db, cache=cache_service)
app.state.storage_service = get_storage_service()

# Include Routers
app.include_router(auth_router)
app.include_router(quota_router)
app.include_router(admin_router)
app.include_router(package_router)
app.include_router(user_router)
app.include_router(voice_samples_router)
app.include_router(activity_router)
app.include_router(consent_router)
app.include_router(queue_router)
app.include_router(system_admin_router, prefix="/api/admin/system", tags=["System Admin"])
app.include_router(meeting_template_router, prefix="/api/admin/meeting-templates", tags=["Admin Meeting Templates"])
app.include_router(llm_config_router, prefix="/api/admin/llm-configs", tags=["Admin LLM Config"])

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

def _seed_meeting_templates():
    """Seed default meeting templates into the database if missing."""
    from app.models.meeting_template import get_default_meeting_templates
    mongo = app.state.mongo_service
    defaults = get_default_meeting_templates()
    for template in defaults:
        # Check if exists, if not upsert
        existing = mongo.get_meeting_template(template["meeting_type_id"])
        if not existing:
            mongo.update_meeting_template(template["meeting_type_id"], template)

_seed_meeting_templates()


def _seed_llm_config():
    """Seed default LLM runtime config if missing."""
    from app.models.llm_config import get_default_llm_config

    mongo = app.state.mongo_service
    default_config = get_default_llm_config()
    if not mongo.get_llm_config(default_config["name"]):
        mongo.upsert_llm_config(default_config["name"], default_config)
        print("✅ Seeded default LLM config")


_seed_llm_config()


def _migrate_meeting_templates_multilingual():
    """One-time migration: update all meeting templates with multilingual-aware prompts.

    Replaces the old Thai-summary rule with the stronger multilingual rule that
    forces Thai output regardless of transcript language. Safe to run repeatedly
    — only updates templates that still contain the old rule wording.
    """
    from app.models.meeting_template import get_default_meeting_templates
    mongo = app.state.mongo_service
    defaults = get_default_meeting_templates()
    updated_count = 0
    for template in defaults:
        existing = mongo.get_meeting_template(template["meeting_type_id"])
        if not existing:
            continue
        old_prompt = existing.get("system_prompt", "")
        # Detect templates that still have the old wording (pre-multilingual)
        if "สรุปเป็นภาษาไทยเป็นหลัก" in old_prompt or "สรุปเป็นภาษาไทยเสมอ" not in old_prompt:
            mongo.update_meeting_template(
                template["meeting_type_id"],
                {"system_prompt": template["system_prompt"]},
            )
            updated_count += 1
    if updated_count > 0:
        print(f"✅ Migrated {updated_count} meeting template(s): added multilingual Thai-summary rule")

_migrate_meeting_templates_multilingual()


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
    agendas: List[dict] = Field(default_factory=list)


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
    use_voice_matching: bool = Form(False, description="Use voice enrollment for speaker identification"),
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
    temp_upload_path = None
    object_uploaded = False
    quota_reserved = False
    job_id = None
    duration_seconds = 0.0
    duration_minutes = 0.0

    try:
        max_bytes = MAX_UPLOAD_MB * 1024 * 1024
        total_bytes = 0
        with tempfile.NamedTemporaryFile(delete=False, suffix=file_ext) as tmp:
            temp_upload_path = tmp.name
            while True:
                chunk = await audio.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=f"File too large. Maximum size: {MAX_UPLOAD_MB} MB"
                    )
                tmp.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty")

        duration_seconds = _probe_audio_duration_seconds(temp_upload_path)
        duration_minutes = duration_seconds / 60
        max_minutes_per_file = limit_check.get("max_audio_minutes_per_file", 30)
        if max_minutes_per_file > 0 and duration_minutes > max_minutes_per_file:
            raise HTTPException(
                status_code=403,
                detail=f"ไฟล์เสียงยาวเกินแพ็กเกจที่กำหนด ({max_minutes_per_file} นาที/ไฟล์)",
            )

        reservation = mongo_service.reserve_upload_quota(str(user.id), duration_minutes)
        if not reservation.get("allowed"):
            raise HTTPException(status_code=403, detail=reservation.get("reason", "เกินโควต้าการใช้งาน"))
        quota_reserved = True

        storage.upload_file(
            BUCKET_AUDIO,
            object_name,
            temp_upload_path,
            content_type=audio.content_type or "audio/mpeg",
        )
        object_uploaded = True

        # Create job in MongoDB (store MinIO object name instead of file path)
        job_id = mongo_service.create_job(
            user_id=user.id,
            audio_file=audio.filename,
            meeting_type_id=meeting_type_id,
            audio_path=object_name,
            email_recipient=email_recipient,
        )

        # Fetch voice samples if voice matching is enabled
        voice_samples_data = None
        if use_voice_matching:
            voice_samples_data = mongo_service.get_voice_samples_with_embeddings(str(user.id))
            if not voice_samples_data:
                voice_samples_data = None  # No samples to match against

        # Send task to Celery worker
        process_audio.delay(
            job_id=job_id,
            audio_object=object_name,
            original_filename=audio.filename,
            meeting_type_id=meeting_type_id,
            user_id=str(user.id),
            email_recipient=email_recipient,
            custom_prompt=custom_prompt,
            voice_samples=voice_samples_data,
        )
    except HTTPException:
        if quota_reserved:
            mongo_service.refund_upload_quota(str(user.id), duration_minutes)
        if object_uploaded:
            try:
                storage.delete_object(BUCKET_AUDIO, object_name)
            except Exception:
                pass
        raise
    except Exception as e:
        if quota_reserved:
            mongo_service.refund_upload_quota(str(user.id), duration_minutes)
        if object_uploaded:
            try:
                storage.delete_object(BUCKET_AUDIO, object_name)
            except Exception:
                pass
        if job_id and ObjectId.is_valid(job_id):
            mongo_service.db.job.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {"status": "failed", "error": f"Failed to enqueue task: {str(e)}"}},
            )
        raise HTTPException(status_code=500, detail=f"Failed to queue upload: {str(e)}")
    finally:
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except Exception:
                pass

    # Activity log
    client_ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "")
    mongo_service.log_activity(
        str(user.id), "upload_audio",
        resource_type="job", resource_id=job_id,
        ip_address=client_ip,
        metadata={"filename": audio.filename, "meeting_type_id": meeting_type_id},
    )

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
            meeting_type_id=request.meeting_type_id,
            agendas=request.agendas,
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
    user: UserData = Depends(get_current_consented_user),
    mongo_service: MongoService = Depends(get_mongo_service),
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
    if not _user_owns_clip_prefix(mongo_service, user.id, session_id):
        raise HTTPException(status_code=404, detail="Clip not found")

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
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """
    Cleanup speaker clips for a session from MinIO.
    """
    if not _user_owns_clip_prefix(mongo_service, user.id, session_id):
        raise HTTPException(status_code=404, detail="Session not found")
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
    agendas: List[dict] = Field(default_factory=list)


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
            meeting_type_id=request.meeting_type_id,
            agendas=request.agendas,
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
    mongo_service.log_activity(str(user.id), "view_history", resource_type="session")
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
    mongo_service.log_activity(
        str(user.id), "view_session",
        resource_type="session", resource_id=session_id,
    )
    return {"success": True, "session": session}


# ===================== STARTUP =====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
