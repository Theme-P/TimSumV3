"""
FastAPI endpoint for Transcription-Summarization Pipeline.
Provides REST API for frontend integration.
"""
import os
import asyncio
import logging
import tempfile
import shutil
import uuid
import subprocess
from datetime import datetime, timezone
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Depends, Request
from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
from starlette.background import BackgroundTask
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator
from typing import Any, AsyncGenerator, Dict, List, Optional
from io import BytesIO
from dotenv import load_dotenv
from bson import ObjectId

# Load environment variables
load_dotenv()

# Import pipeline components
from app.models.meeting import MEETING_TYPES
from app.utils.export import export_transcript_to_docx, export_summary_to_docx
from app.services.email_service import EmailService
from app.services.storage import (
    StorageService,
    get_storage_service,
    BUCKET_AUDIO,
    BUCKET_CLIPS,
    BUCKET_ARTIFACTS,
)
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
from app.core.runtime_validation import validate_runtime_configuration
from app.models.user import UserData
from app.services.rate_limit import EMAIL_USER, UPLOAD_USER, enforce_rate_limit
from app.services.security import get_client_ip

# Initialize FastAPI app
app = FastAPI(
    title="TimSumV3 API",
    description="Merged Transcription-Summarization API with configurable NTC Gateway LLM",
    version="3.0.0"
)

# Max upload size
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "500"))
logger = logging.getLogger(__name__)


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


def _validated_display_filename(value: str, *, max_length: int = 180) -> str:
    """Validate user-provided names for display only, never for server paths."""
    name = (value or "").strip()
    if not name or len(name) > max_length:
        raise HTTPException(status_code=400, detail="Invalid file name")
    if any(character in name for character in ("/", "\\")):
        raise HTTPException(status_code=400, detail="Invalid file name")
    if any(ord(character) < 32 or ord(character) == 127 for character in name):
        raise HTTPException(status_code=400, detail="Invalid file name")
    return name


def _authorization_package_limits(mongo_service: MongoService, user_id: ObjectId) -> dict:
    """Read entitlement state directly from Mongo; display cache is not authoritative."""
    assignment = mongo_service.db.user_package.find_one({
        "user_id": user_id,
        "status": "active",
        "$or": [
            {"expires_at": {"$exists": False}},
            {"expires_at": None},
            {"expires_at": {"$gt": datetime.now(timezone.utc)}},
        ],
    })
    if not assignment:
        raise HTTPException(status_code=403, detail="ไม่พบแพ็กเกจที่ใช้งานได้")
    package = mongo_service.db.package.find_one({
        "_id": assignment.get("package_id"),
        "is_active": {"$ne": False},
    })
    if not package:
        raise HTTPException(status_code=403, detail="แพ็กเกจไม่พร้อมใช้งาน")
    return package.get("limits", {})


# Enable CORS for frontend (whitelist from env)
_default_allowed_origins = ",".join([
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
])
_default_allowed_origin_regex = (
    r"^https?://("
    r"(localhost|127\.0\.0\.1)(:\d+)?|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?|"
    r"192\.168\.\d{1,3}\.\d{1,3}(:\d+)?|"
    r"172\.(1[6-9]|2\d|3[0-1])\.\d{1,3}\.\d{1,3}(:\d+)?|"
    r"[A-Za-z0-9-]+\.local(:\d+)?"
    r")$"
)
_allowed_origins = os.getenv("ALLOWED_ORIGINS", _default_allowed_origins)
allowed_origins = [o.strip() for o in _allowed_origins.split(",") if o.strip()]
allowed_origin_regex = os.getenv("ALLOWED_ORIGIN_REGEX", _default_allowed_origin_regex) or None
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_origin_regex=allowed_origin_regex,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

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

def _seed_llm_config():
    """Seed or normalize default LLM runtime config."""
    from app.models.llm_config import (
        DEFAULT_NTC_MODEL,
        LEGACY_PRIMARY_MODELS,
        get_default_llm_config,
        normalize_fallback_models,
        normalize_primary_model,
    )

    mongo = app.state.mongo_service
    default_config = get_default_llm_config()
    existing = mongo.get_llm_config(default_config["name"])
    if not existing:
        mongo.upsert_llm_config(default_config["name"], default_config)
        print("✅ Seeded default LLM config")
        return

    current_primary = existing.get("primary_model")
    normalized_primary = normalize_primary_model(current_primary)
    current_fallbacks = existing.get("fallback_models")
    normalized_fallbacks = normalize_fallback_models(current_fallbacks)

    should_follow_env_model = (
        not current_primary
        or current_primary in LEGACY_PRIMARY_MODELS
        or current_primary == DEFAULT_NTC_MODEL
        or existing.get("updated_by") in (None, "system")
    )
    should_follow_env_fallbacks = existing.get("updated_by") in (None, "system")

    updates = {}
    if should_follow_env_model and current_primary != default_config["primary_model"]:
        updates["primary_model"] = default_config["primary_model"]
    elif current_primary != normalized_primary:
        updates["primary_model"] = normalized_primary

    if should_follow_env_fallbacks and current_fallbacks != default_config["fallback_models"]:
        updates["fallback_models"] = default_config["fallback_models"]
    elif current_fallbacks != normalized_fallbacks:
        updates["fallback_models"] = normalized_fallbacks

    if updates:
        updates["updated_by"] = "system"
        updates["updated_at"] = datetime.now(timezone.utc)
        merged = {key: value for key, value in existing.items() if key != "_id"}
        mongo.upsert_llm_config(default_config["name"], {**merged, **updates})
        print("✅ Normalized default LLM config for NTC Gateway")

# ── Migrate legacy users: add status field if missing ──
def _migrate_users_status():
    mongo = app.state.mongo_service
    result = mongo.db.user.update_many(
        {"status": {"$exists": False}},
        {"$set": {"status": "approved"}},
    )
    if result.modified_count > 0:
        print(f"✅ Migrated {result.modified_count} legacy user(s): added status='approved'")
# ── Seed default packages & assign to default users ──
def _seed_packages():
    from app.models.package import DEFAULT_PACKAGES, ADMIN_PACKAGE, SUPERADMIN_PACKAGE

    mongo = app.state.mongo_service

    # Seed public packages
    for pkg in DEFAULT_PACKAGES:
        pkg_copy = {**pkg, "is_active": True}
        mongo.seed_package_if_missing(pkg_copy)

    # Seed internal admin packages
    for pkg in [ADMIN_PACKAGE, SUPERADMIN_PACKAGE]:
        pkg_copy = {**pkg, "is_active": True}
        mongo.seed_package_if_missing(pkg_copy)


@app.on_event("startup")
def _startup_initialize_services():
    """Initialize external services and run idempotent startup seeds/migrations."""
    if getattr(app.state, "services_initialized", False):
        return

    validate_runtime_configuration()

    cache_service = CacheService()
    mongo_uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://localhost:27017")
    mongo_db = os.getenv("MONGO_DB_NAME", "timsumv3")
    app.state.mongo_service = MongoService(uri=mongo_uri, db_name=mongo_db, cache=cache_service)
    app.state.storage_service = get_storage_service()
    app.state.email_service = EmailService()

    _seed_meeting_templates()
    _seed_llm_config()
    _migrate_users_status()
    _seed_packages()

    app.state.services_initialized = True

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
    speaker: Optional[str] = None

class ExportTranscriptRequest(BaseModel):
    segments: List[TranscriptSegment]
    audio_file: str = ""
    audio_length_seconds: float = 0

class ExportSummaryRequest(BaseModel):
    summary: str
    speaker_summary: dict = None  # Optional: speaking_time and word_count per speaker
    meeting_type_id: int = 0  # Meeting type for position formatting
    agendas: List[dict] = Field(default_factory=list)
    summary_warning: str = ""


# ===================== ENDPOINTS =====================

@app.get("/api/health", response_model=HealthResponse)
async def health_check():
    """Process liveness only; it deliberately does not touch dependencies."""
    return HealthResponse(
        status="healthy",
        message="Transcribe-Summary API is running"
    )


@app.get("/api/health/ready")
async def readiness_check(request: Request):
    """Bounded readiness probe for MongoDB, Redis and object storage."""
    import redis

    timeout_seconds = float(os.getenv("READINESS_TIMEOUT_SECONDS", "2.5"))
    mongo_service: MongoService = request.app.state.mongo_service
    storage: StorageService = request.app.state.storage_service

    def check_mongo():
        mongo_service.client.admin.command("ping", maxTimeMS=max(100, int(timeout_seconds * 1000)))

    def check_redis():
        redis_url = os.getenv("REDIS_URL", "redis://redis:6379/0")
        client = redis.from_url(
            redis_url,
            socket_connect_timeout=timeout_seconds,
            socket_timeout=timeout_seconds,
        )
        try:
            client.ping()
        finally:
            client.close()

    def check_minio():
        if not storage.client.bucket_exists(BUCKET_AUDIO):
            raise RuntimeError("required object bucket is unavailable")

    async def bounded_probe(name: str, probe):
        try:
            await asyncio.wait_for(asyncio.to_thread(probe), timeout=timeout_seconds)
            return name, "ready"
        except Exception:
            return name, "unavailable"

    results = dict(await asyncio.gather(
        bounded_probe("mongo", check_mongo),
        bounded_probe("redis", check_redis),
        bounded_probe("minio", check_minio),
    ))
    ready = all(value == "ready" for value in results.values())
    payload = {"status": "ready" if ready else "not_ready", "dependencies": results}
    return JSONResponse(payload, status_code=200 if ready else 503)


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
async def transcribe_summarize(
    request: Request,
    audio: UploadFile = File(..., description="Audio file to transcribe"),
    meeting_type_id: int = Form(0, description="Meeting type ID (0=auto-detect, 1-11=specific type)"),
    email_recipient: str = Form("", description="Optional: email to auto-send results to"),
    custom_prompt: str = Form("", description="Optional: custom instruction for summary (max 500 chars)"),
    use_voice_matching: bool = Form(False, description="Use voice enrollment for speaker identification"),
    user: UserData = Depends(get_current_consented_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """
    Submit audio file for async transcription and summarization.
    Returns a job_id immediately — poll /api/jobs/{job_id} for progress.
    If email_recipient is provided, results will be auto-sent when processing completes.
    """
    if os.getenv("UPLOADS_ENABLED", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        raise HTTPException(
            status_code=503,
            detail="Uploads are temporarily paused for maintenance",
            headers={"Retry-After": "300"},
        )
    enforce_rate_limit(request, UPLOAD_USER, str(user.id))

    # All mutation authorization reads current Mongo state; cached package data
    # is display-only and the atomic reservation below is the quota authority.
    current_limits = _authorization_package_limits(mongo_service, user.id)

    # Validate meeting type
    if meeting_type_id < 0 or meeting_type_id > 11:
        raise HTTPException(status_code=400, detail="meeting_type_id must be between 0 and 11")

    display_filename = _validated_display_filename(audio.filename or "")

    # Lightweight email validation (full RFC validation is not worth it; SMTP server is the source of truth)
    email_recipient = (email_recipient or "").strip()
    if email_recipient and ("@" not in email_recipient or "." not in email_recipient.split("@")[-1]):
        raise HTTPException(status_code=400, detail="Invalid email_recipient format")
    if email_recipient:
        enforce_rate_limit(request, EMAIL_USER, str(user.id))

    # Validate custom prompt
    custom_prompt = (custom_prompt or "").strip()
    if len(custom_prompt) > 500:
        raise HTTPException(status_code=400, detail="custom_prompt ต้องไม่เกิน 500 ตัวอักษร")

    if custom_prompt and not current_limits.get("custom_prompt_enabled", False):
        raise HTTPException(status_code=403, detail="แพ็กเกจไม่รองรับ custom prompt")
    if use_voice_matching and not current_limits.get("voice_enrollment_enabled", False):
        raise HTTPException(status_code=403, detail="แพ็กเกจไม่รองรับ voice matching")

    # Validate file type
    allowed_extensions = ['.mp3', '.wav', '.m4a', '.flac', '.ogg', '.webm', '.mp4']
    file_ext = os.path.splitext(display_filename)[1].lower()
    if file_ext not in allowed_extensions:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type. Allowed: {', '.join(allowed_extensions)}"
        )

    # Upload to MinIO
    job_id = mongo_service.new_job_id()
    object_name = f"{job_id}{file_ext}"
    temp_upload_path = None
    object_uploaded = False
    quota_reserved = False
    job_created = False
    publish_intent_durable = False
    publish_recovery_pending = False
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

        duration_seconds = await asyncio.to_thread(
            _probe_audio_duration_seconds,
            temp_upload_path,
        )
        duration_minutes = duration_seconds / 60
        max_minutes_per_file = current_limits.get("max_audio_minutes_per_file", 30)
        if max_minutes_per_file > 0 and duration_minutes > max_minutes_per_file:
            raise HTTPException(
                status_code=403,
                detail=f"ไฟล์เสียงยาวเกินแพ็กเกจที่กำหนด ({max_minutes_per_file} นาที/ไฟล์)",
            )

        # Persist an initializing job before mutating quota or object storage.
        # The stable job ID is the idempotency key for every later checkpoint.
        job_id = mongo_service.create_job(
            user_id=user.id,
            audio_file=display_filename,
            meeting_type_id=meeting_type_id,
            audio_path=object_name,
            email_recipient=email_recipient,
            quota_minutes=duration_minutes,
            job_id=job_id,
            initial_status="initializing",
            quota_reserved=False,
        )
        job_created = True
        mongo_service.db.job.update_one(
            {"_id": ObjectId(job_id), "status": "initializing"},
            {"$set": {
                "custom_prompt": custom_prompt,
                "use_voice_matching": bool(use_voice_matching),
                "content_type": audio.content_type or "audio/mpeg",
            }},
        )

        reservation = mongo_service.reserve_job_quota(str(user.id), job_id, duration_minutes)
        if not reservation.get("allowed"):
            raise HTTPException(status_code=403, detail=reservation.get("reason", "เกินโควต้าการใช้งาน"))
        quota_reserved = True
        if not mongo_service.checkpoint_job_quota_reserved(
            job_id,
            reservation["reservation_id"],
            duration_minutes,
        ):
            raise RuntimeError("Could not checkpoint quota reservation")

        storage.upload_file(
            BUCKET_AUDIO,
            object_name,
            temp_upload_path,
            content_type=audio.content_type or "audio/mpeg",
        )
        object_uploaded = True
        if not mongo_service.checkpoint_job_upload_complete(job_id, object_name):
            raise RuntimeError("Could not checkpoint uploaded audio")

        # Fetch voice samples if voice matching is enabled.
        # Use `or None` so the pipeline receives None (not []) when no samples exist,
        # keeping the intent explicit for callers that check `if voice_samples`.
        voice_samples_data = None
        if use_voice_matching:
            voice_samples_data = mongo_service.get_voice_samples_with_embeddings(str(user.id)) or None

        # Persist a complete, replayable publish intent before touching the
        # broker.  Once this boundary is durable a publish error is ambiguous:
        # the broker may have accepted the task even if the client did not
        # receive its confirmation.  Maintenance must therefore retry the same
        # task ID; compensating quota/audio after this point would race a worker
        # that is already running.
        celery_task_id = str(uuid.uuid4())
        task_kwargs = {
            "job_id": job_id,
            "audio_object": object_name,
            "original_filename": display_filename,
            "meeting_type_id": meeting_type_id,
            "user_id": str(user.id),
            "email_recipient": email_recipient,
            "custom_prompt": custom_prompt,
            "voice_samples": voice_samples_data,
        }
        replay_kwargs = {
            key: value for key, value in task_kwargs.items() if key != "voice_samples"
        }
        replay_kwargs["use_voice_matching"] = bool(use_voice_matching)
        if not mongo_service.checkpoint_job_publish_intent(
            job_id,
            celery_task_id,
            replay_kwargs,
        ):
            raise RuntimeError("Could not checkpoint transcription publish intent")
        publish_intent_durable = True
        try:
            process_audio.apply_async(
                kwargs=task_kwargs,
                task_id=celery_task_id,
                queue="transcription",
            )
        except Exception as publish_error:
            publish_recovery_pending = True
            mongo_service.db.job.update_one(
                {
                    "_id": ObjectId(job_id),
                    "celery_task_id": celery_task_id,
                    "status": {"$nin": ["completed", "partially_completed", "failed", "cancelled"]},
                },
                {"$set": {
                    "publish_state": "publishing",
                    "publish_last_error": str(publish_error)[:1000],
                }},
            )
            # A durable reconciler also scans this state.  This eager enqueue is
            # best-effort only because the same broker may currently be down.
            try:
                from app.tasks.maintenance import recover_transcription_publish

                recover_transcription_publish.apply_async(
                    args=[job_id],
                    queue="maintenance",
                    countdown=5,
                )
            except Exception:
                logger.warning(
                    "Publish for job %s is ambiguous; maintenance reconciliation will retry",
                    job_id,
                    exc_info=True,
                )
        else:
            try:
                published_checkpointed = mongo_service.checkpoint_job_published(
                    job_id,
                    celery_task_id,
                )
            except Exception as checkpoint_error:
                # Delivery is confirmed but the acknowledgement checkpoint is
                # not. Keep the durable intent for the same recovery path.
                publish_recovery_pending = True
                logger.warning(
                    "Could not acknowledge published job %s: %s",
                    job_id,
                    checkpoint_error,
                )
            else:
                if not published_checkpointed:
                    # A fast worker may already have advanced the job beyond
                    # `queued`. The durable intent and worker lease remain the
                    # authorities for recovery/idempotency.
                    logger.warning("Job %s advanced before publish checkpoint", job_id)
    except HTTPException:
        if quota_reserved:
            if job_created:
                mongo_service.refund_job_quota_once(job_id)
            else:
                mongo_service.settle_quota_reservation(str(user.id), job_id, "refunded")
        if object_uploaded:
            try:
                storage.delete_object(BUCKET_AUDIO, object_name)
                object_uploaded = False
            except Exception:
                pass
        if job_created:
            now = datetime.now(timezone.utc)
            mongo_service.db.job.update_one(
                {"_id": ObjectId(job_id), "status": {"$in": ["initializing", "queued"]}},
                {"$set": {
                    "status": "failed",
                    "current_step": "error",
                    "error": "Upload request was rejected",
                    "completed_at": now,
                    "audio_cleanup_state": "pending" if object_uploaded else "completed",
                    "audio_cleanup_after": now,
                }},
            )
        raise
    except Exception as e:
        if quota_reserved and not publish_intent_durable:
            if job_created:
                mongo_service.refund_job_quota_once(job_id)
            else:
                mongo_service.settle_quota_reservation(str(user.id), job_id, "refunded")
        if object_uploaded and not publish_intent_durable:
            try:
                storage.delete_object(BUCKET_AUDIO, object_name)
                object_uploaded = False
            except Exception:
                pass
        if job_id and ObjectId.is_valid(job_id) and not publish_intent_durable:
            mongo_service.db.job.update_one(
                {"_id": ObjectId(job_id)},
                {"$set": {
                    "status": "failed",
                    "current_step": "error",
                    "error": f"Failed to enqueue task: {str(e)}",
                    "completed_at": datetime.now(timezone.utc),
                    "audio_cleanup_state": "pending" if object_uploaded else "completed",
                    "audio_cleanup_after": datetime.now(timezone.utc),
                }},
            )
        logger.exception("Failed to queue upload for job %s", job_id)
        raise HTTPException(status_code=500, detail="Failed to queue upload")
    finally:
        if temp_upload_path and os.path.exists(temp_upload_path):
            try:
                os.remove(temp_upload_path)
            except Exception:
                pass

    # Activity log
    mongo_service.log_activity(
        str(user.id), "upload_audio",
        resource_type="job", resource_id=job_id,
        ip_address=get_client_ip(request),
        metadata={"filename": display_filename, "meeting_type_id": meeting_type_id},
    )

    response = {"success": True, "job_id": job_id, "status": "queued"}
    if publish_recovery_pending:
        response["publish_state"] = "pending_reconciliation"
    return response


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
    """Get a preserved result for completed, partial, or transcript-only jobs."""
    job = mongo_service.get_job_result(job_id, user.id)
    if not job:
        raise HTTPException(status_code=404, detail="Job result is not available")

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
            summary_text=(
                f"หมายเหตุ: {request.summary_warning}\n\n{request.summary}"
                if request.summary_warning else request.summary
            ),
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
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """
    Serve a speaker audio clip from MinIO.

    - **session_id**: clip_prefix from job result (typically job_id)
    - **filename**: Clip filename (e.g., speaker_0.mp3)
    """
    # Validate filename (prevent path traversal)
    if '/' in filename or '\\' in filename or '..' in filename or any(ord(c) < 32 for c in filename):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if not _user_owns_clip_prefix(mongo_service, user.id, session_id):
        raise HTTPException(status_code=404, detail="Clip not found")

    object_name = f"{session_id}/{filename}"
    if not storage.object_exists(BUCKET_CLIPS, object_name):
        raise HTTPException(status_code=410, detail="Clip expired or was deleted")

    clip_bytes = storage.get_object_bytes(BUCKET_CLIPS, object_name)
    return StreamingResponse(
        BytesIO(clip_bytes),
        media_type="audio/mpeg",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )


@app.delete("/api/session/{session_id}", deprecated=True)
async def cleanup_session(
    session_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """
    Deprecated compatibility endpoint. Use DELETE /api/history/{session_id}.
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
    segments: List[TranscriptSegment] = Field(default_factory=list)
    audio_file: str = ""
    audio_length_seconds: float = 0
    speaker_summary: dict = None
    meeting_type_id: int = 0
    agendas: List[dict] = Field(default_factory=list)
    summary_status: str = "completed"
    is_partial_summary: bool = False
    coverage_percentage: float = 100.0
    summary_warning: str = ""

    @field_validator("recipient_email")
    @classmethod
    def validate_recipient_email(cls, v: str) -> str:
        v = v.strip()
        if not v or "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("รูปแบบอีเมลผู้รับไม่ถูกต้อง")
        return v


@app.post("/api/email-results")
async def email_results(
    request: EmailResultsRequest,
    req: Request,
    user: UserData = Depends(get_current_consented_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """
    Generate DOCX files and send them via email.
    Requires SMTP configuration in .env
    """
    email_svc: EmailService = req.app.state.email_service
    enforce_rate_limit(req, EMAIL_USER, str(user.id))
    if not email_svc.is_configured:
        raise HTTPException(status_code=503, detail="Email service not configured. Set SMTP_SERVER and SENDER_EMAIL in .env")

    display_filename = _validated_display_filename(request.file_name)
    temp_dir = tempfile.mkdtemp()
    try:
        docx_files = []

        has_summary = bool(request.summary.strip()) and request.summary_status != "failed"
        if has_summary:
            summary_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.docx")
            summary_text = request.summary
            if request.summary_warning:
                summary_text = f"หมายเหตุ: {request.summary_warning}\n\n{summary_text}"
            export_summary_to_docx(
                summary_text=summary_text,
                output_path=summary_path,
                speaker_summary=request.speaker_summary,
                meeting_type_id=request.meeting_type_id,
                agendas=request.agendas,
            )
            docx_files.append((summary_path, f"{display_filename}_Summary"))

        # Generate transcript DOCX if segments provided
        if request.segments:
            transcript_path = os.path.join(temp_dir, f"{uuid.uuid4().hex}.docx")
            segments = [seg.model_dump() for seg in request.segments]
            export_transcript_to_docx(
                segments=segments,
                output_path=transcript_path,
                audio_file=request.audio_file,
                audio_length=request.audio_length_seconds
            )
            docx_files.append((transcript_path, f"{display_filename}_Transcription"))

        # Send email
        if request.is_partial_summary:
            result_note = (
                f"Summary ครอบคลุม Transcript {request.coverage_percentage:.1f}% "
                "กรุณาตรวจ Transcript สำหรับช่วงที่เหลือ"
            )
        elif request.summary_status == "failed":
            result_note = "ระบบสรุปไม่สำเร็จ จึงส่งเฉพาะ Transcript ที่ประมวลผลสำเร็จ"
        else:
            result_note = "Summary และ Transcript ประมวลผลสำเร็จ"

        body = f"""เรียน คุณผู้ใช้งาน

เอกสารของคุณได้รับการประมวลผลเรียบร้อยแล้ว

รายละเอียด:
- ชื่อไฟล์: {display_filename}
- จำนวนไฟล์แนบ: {len(docx_files)} ไฟล์
- สถานะ: {result_note}

กรุณาดาวน์โหลดไฟล์ที่แนบมาและตรวจสอบผลการประมวลผล

ขอบคุณที่ใช้บริการ TimSum V3"""

        # Account deletion or suspension may have started while the documents
        # were being generated. Re-check immediately before SMTP delivery.
        current_user = mongo_service.db.user.find_one(
            {"_id": user.id},
            {"status": 1, "deletion_pending": 1},
        )
        if (
            not current_user
            or current_user.get("deletion_pending")
            or current_user.get("status") != "approved"
        ):
            raise HTTPException(status_code=409, detail="บัญชีไม่พร้อมสำหรับการส่งอีเมล")

        success = email_svc.send_email_with_attachments(
            recipient_email=request.recipient_email,
            subject=f"Document Processing Complete - {display_filename}",
            body_text=body,
            docx_files=docx_files,
        )

        if not success:
            raise HTTPException(status_code=500, detail="Failed to send email")

        return {"success": True, "message": f"Email sent to {request.recipient_email}"}

    except HTTPException:
        raise
    except Exception:
        logger.exception("Email-results delivery failed for user %s", user.id)
        raise HTTPException(status_code=500, detail="Email delivery failed")
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


@app.delete("/api/history/{session_id}")
async def delete_history(
    session_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
    storage: StorageService = Depends(get_storage),
):
    """Delete one owned History record and its retained speaker clips."""
    session_obj_id = ObjectId(session_id) if ObjectId.is_valid(session_id) else None
    if session_obj_id is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session = mongo_service.db.session.find_one({
        "_id": session_obj_id,
        "user_id": user.id,
    })
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")

    clip_prefix = str(session.get("clip_prefix") or session.get("job_id") or session_id)
    if not clip_prefix or any(value in clip_prefix for value in ("/", "\\", "..")):
        raise HTTPException(status_code=409, detail="Session clip metadata is invalid")
    try:
        storage.delete_prefix(BUCKET_CLIPS, prefix=f"{clip_prefix}/")
    except Exception:
        logger.exception("Could not delete clips for session %s", session_id)
        raise HTTPException(status_code=503, detail="Clip cleanup failed; retry deletion")

    result = mongo_service.db.session.delete_one({
        "_id": session_obj_id,
        "user_id": user.id,
    })
    if result.deleted_count != 1:
        raise HTTPException(status_code=409, detail="Session changed during deletion; retry")
    mongo_service.log_activity(
        str(user.id),
        "delete_session",
        resource_type="session",
        resource_id=session_id,
    )
    return {"success": True, "session_id": session_id, "clips_deleted": True}


# ===================== STARTUP =====================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
