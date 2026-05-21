# pyrefly: ignore [missing-import]
import jwt
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, field_validator
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Depends, Request
# pyrefly: ignore [missing-import]
from loguru import logger

from app.models.user import User, Quota, UserData, USER_STATUS_APPROVED
from app.services.mongo import MongoService
from app.services.email_service import EmailService
from app.core.auth import get_jwt_secret, get_current_admin
# pyrefly: ignore [missing-import]
from bson import ObjectId

# Google SSO
from google.oauth2 import id_token as google_id_token
from google.auth.transport import requests as google_requests

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str


class PublicRegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    organization: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
        return v

    @field_validator("email")
    @classmethod
    def email_format(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("รูปแบบอีเมลไม่ถูกต้อง")
        return v

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
        return v


# Helper function to get MongoDB service from app state
def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


# ── Status message mapping for login errors ──
_STATUS_MESSAGES = {
    "pending": "บัญชีของคุณอยู่ระหว่างรอการอนุมัติ กรุณารอผู้ดูแลระบบตรวจสอบ",
    "rejected": "บัญชีของคุณถูกปฏิเสธ กรุณาติดต่อผู้ดูแลระบบ",
    "suspended": "บัญชีของคุณถูกระงับ กรุณาติดต่อผู้ดูแลระบบ",
}


@router.post("/login")
async def login(req: LoginRequest, mongo_service: MongoService = Depends(get_mongo_service)):
    """Authenticate a user and return a JWT token."""
    try:
        user = mongo_service.authenticate_user(req.email, req.password)
        if not user:
            # Check if user exists but has non-approved status
            status = mongo_service.get_user_status(req.email)
            if status and status != USER_STATUS_APPROVED:
                msg = _STATUS_MESSAGES.get(status, "ไม่สามารถเข้าสู่ระบบได้")
                raise HTTPException(status_code=403, detail=msg)
            raise HTTPException(status_code=401, detail="อีเมลหรือรหัสผ่านไม่ถูกต้อง")

        # Generate JWT token
        secret = get_jwt_secret()
        expire_hours = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
        token_payload = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours),
        }
        token = jwt.encode(token_payload, secret, algorithm="HS256")

        return {
            "status": "success",
            "message": "Login successful",
            "token": token,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/register", status_code=201)
async def register(
    req: RegisterRequest,
    mongo_service: MongoService = Depends(get_mongo_service),
    _: UserData = Depends(get_current_admin),
):
    """Register a new user (admin-only). User is auto-approved."""
    try:
        existing_user = mongo_service.get_user_by_email(req.email)
        if existing_user:
            raise HTTPException(status_code=409, detail="User with this email already exists")

        new_user = User(
            username=req.username,
            email=req.email,
            password=req.password,
            role="user",
            status="approved",
        )
        new_quota = Quota(
            user_id=new_user.id,
            value1=0, value2=0, value3=0, value4=0,
        )

        mongo_service.create_user(new_user)
        mongo_service.create_quota(new_quota)

        return {"status": "success", "message": "User registered successfully"}

    except ValueError as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected registration error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/register-public", status_code=201)
async def register_public(
    req: PublicRegisterRequest,
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Public registration. Creates user with status=pending (needs admin approval)."""
    try:
        existing_user = mongo_service.get_user_by_email(req.email)
        if existing_user:
            raise HTTPException(status_code=409, detail="อีเมลนี้ถูกใช้งานแล้ว")

        username = f"{req.first_name} {req.last_name}".strip()
        new_user = User(
            username=username,
            email=req.email,
            password=req.password,
            role="user",
            first_name=req.first_name,
            last_name=req.last_name,
            phone=req.phone,
            organization=req.organization,
            status="pending",
        )

        mongo_service.register_public_user(new_user)

        return {
            "status": "success",
            "message": "ลงทะเบียนสำเร็จ กรุณารอการอนุมัติจากผู้ดูแลระบบ",
        }

    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Public registration error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง")


# ── Google SSO ──

class GoogleAuthRequest(BaseModel):
    credential: str  # Google ID token from GIS


GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")


@router.get("/google/client-id")
async def google_client_id():
    """Return Google Client ID for frontend GIS initialization."""
    if not GOOGLE_CLIENT_ID:
        return {"enabled": False, "client_id": ""}
    return {"enabled": True, "client_id": GOOGLE_CLIENT_ID}


@router.post("/google")
async def google_login(
    req: GoogleAuthRequest,
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Authenticate via Google SSO. Creates user if new (auto-approved)."""
    if not GOOGLE_CLIENT_ID:
        raise HTTPException(status_code=501, detail="Google SSO ยังไม่ได้ตั้งค่า")

    # Verify the Google ID token
    try:
        idinfo = google_id_token.verify_oauth2_token(
            req.credential,
            google_requests.Request(),
            GOOGLE_CLIENT_ID,
        )
    except ValueError as e:
        logger.warning(f"Google token verification failed: {e}")
        raise HTTPException(status_code=401, detail="Google token ไม่ถูกต้องหรือหมดอายุ")

    google_sub = idinfo.get("sub")
    email = idinfo.get("email", "").lower().strip()
    name = idinfo.get("name", "")

    if not email:
        raise HTTPException(status_code=400, detail="ไม่สามารถดึงอีเมลจาก Google ได้")

    try:
        user = mongo_service.find_or_create_google_user(
            google_id=google_sub,
            email=email,
            name=name,
        )
    except ValueError as status:
        # User exists but is not approved
        msg = _STATUS_MESSAGES.get(str(status), "ไม่สามารถเข้าสู่ระบบได้")
        raise HTTPException(status_code=403, detail=msg)
    except Exception as e:
        logger.error(f"Google SSO error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการเข้าสู่ระบบด้วย Google")

    # Generate JWT token (same as regular login)
    secret = get_jwt_secret()
    expire_hours = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
    token_payload = {
        "id": str(user.id),
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours),
    }
    token = jwt.encode(token_payload, secret, algorithm="HS256")

    return {
        "status": "success",
        "message": "Google login successful",
        "token": token,
    }

# ── Password Reset ──

@router.post("/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest,
    request: Request,
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        user = mongo_service.get_user_by_email(req.email)
        if not user:
            # For security, return success even if user not found
            return {"status": "success", "message": "หากอีเมลนี้อยู่ในระบบ เราได้ส่งลิงก์รีเซ็ตรหัสผ่านไปให้แล้ว"}
            
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mongo_service.create_password_reset_token(req.email, token, expires_at)
        
        # Determine base URL for reset link
        origin = request.headers.get("origin", "")
        if not origin:
            # Fallback if origin is missing
            host = request.headers.get("host", "localhost")
            scheme = "https" if request.url.scheme == "https" else "http"
            origin = f"{scheme}://{host}"
            
        reset_link = f"{origin}/reset-password?token={token}"
        
        email_service = EmailService()
        if email_service.is_configured:
            body_text = f"สวัสดี,\n\nคุณได้ร้องขอการเปลี่ยนรหัสผ่านสำหรับบัญชี TimSum\nกรุณาคลิกที่ลิงก์ด้านล่างเพื่อตั้งรหัสผ่านใหม่ (ลิงก์มีอายุ 1 ชั่วโมง):\n\n{reset_link}\n\nหากคุณไม่ได้ทำรายการนี้ กรุณาเพิกเฉยต่ออีเมลฉบับนี้\n\nขอบคุณครับ,\nทีมงาน TimSum"
            email_service.send_simple_email(
                recipient_email=req.email,
                subject="รีเซ็ตรหัสผ่าน TimSum",
                body_text=body_text
            )
        else:
            logger.info(f"Password reset link generated for {req.email}: {reset_link}")
            
        return {"status": "success", "message": "หากอีเมลนี้อยู่ในระบบ เราได้ส่งลิงก์รีเซ็ตรหัสผ่านไปให้แล้ว"}
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง")

@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        token_doc = mongo_service.get_password_reset_token(req.token)
        if not token_doc:
            raise HTTPException(status_code=400, detail="ลิงก์รีเซ็ตรหัสผ่านไม่ถูกต้องหรือหมดอายุแล้ว")
            
        user = mongo_service.get_user_by_email(token_doc["email"])
        if not user:
            raise HTTPException(status_code=400, detail="ไม่พบผู้ใช้ในระบบ")
            
        mongo_service.update_user_password(str(user.id), req.new_password)
        mongo_service.delete_password_reset_token(req.token)
        
        return {"status": "success", "message": "เปลี่ยนรหัสผ่านสำเร็จ คุณสามารถเข้าสู่ระบบด้วยรหัสผ่านใหม่ได้ทันที"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการเปลี่ยนรหัสผ่าน")

