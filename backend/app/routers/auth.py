# pyrefly: ignore [missing-import]
import jwt
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, Field, field_validator
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Depends, Request
# pyrefly: ignore [missing-import]
from loguru import logger

from app.models.user import User, Quota, UserData
from app.services.mongo import MongoService
from app.services.email_service import EmailService
from app.services.rate_limit import (
    FORGOT_EMAIL,
    FORGOT_IP,
    LOGIN_EMAIL,
    LOGIN_IP,
    REGISTER_IP,
    RESET_IP,
    RESET_TOKEN,
    enforce_rate_limits,
)
from app.services.security import (
    MAX_PASSWORD_LENGTH,
    SecurityConfigurationError,
    build_frontend_url,
    validate_password,
)
from app.core.auth import get_jwt_secret, get_current_admin

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return validate_password(value)


class PublicRegisterRequest(BaseModel):
    email: str
    password: str
    first_name: str
    last_name: str
    phone: Optional[str] = None
    organization: Optional[str] = None

    @field_validator("password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return validate_password(value)

    @field_validator("email")
    @classmethod
    def email_format(cls, v):
        v = v.strip().lower()
        if "@" not in v or "." not in v.split("@")[-1]:
            raise ValueError("รูปแบบอีเมลไม่ถูกต้อง")
        return v


class ForgotPasswordRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=32, max_length=256)
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, value: str) -> str:
        return validate_password(value)


# Helper function to get MongoDB service from app state
def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


@router.post("/login")
async def login(
    req: LoginRequest,
    request: Request,
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Authenticate a user and return a JWT token."""
    try:
        email = req.email.strip().lower()
        enforce_rate_limits(
            request,
            ((LOGIN_IP, None), (LOGIN_EMAIL, email)),
        )
        user = mongo_service.authenticate_user(email, req.password)
        if not user:
            # Keep the response identical for unknown accounts, wrong
            # passwords, and non-approved accounts to prevent enumeration.
            raise HTTPException(status_code=401, detail="อีเมลหรือรหัสผ่านไม่ถูกต้อง")
        if user.deletion_pending:
            raise HTTPException(status_code=403, detail="บัญชีกำลังอยู่ระหว่างการลบ")

        # Generate JWT token
        secret = get_jwt_secret()
        expire_hours = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
        token_payload = {
            "id": str(user.id),
            "role": user.role,
            "ver": user.auth_version,
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
        logger.exception("Login error for {}: {}", req.email.strip().lower(), e)
        raise HTTPException(status_code=500, detail="An unexpected error occurred")


@router.post("/register", status_code=201)
async def register(
    req: RegisterRequest,
    request: Request,
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

        # Send welcome email to admin-created user
        _send_admin_created_user_email(request, req.username, req.email)

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
    request: Request,
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Public registration. Creates user with status=pending (needs admin approval)."""
    try:
        enforce_rate_limits(request, ((REGISTER_IP, None),))
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

        # Send registration confirmation email
        _send_registration_confirmation_email(request, req)

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


# ── Email Helpers ──

def _send_registration_confirmation_email(
    request: Request,
    req: PublicRegisterRequest,
) -> None:
    """Send confirmation email after public registration. Non-blocking on failure."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Registration email skipped for {req.email} (SMTP not configured)")
            return

        display_name = f"{req.first_name} {req.last_name}".strip()
        org_line = f"\n- องค์กร: {req.organization}" if req.organization else ""
        body_text = (
            f"สวัสดี คุณ{display_name},\n\n"
            f"ขอบคุณที่ลงทะเบียนใช้งานระบบ TimSum\n\n"
            f"รายละเอียดบัญชี:\n"
            f"- อีเมล: {req.email}{org_line}\n\n"
            f"สถานะบัญชีของคุณตอนนี้: รอการอนุมัติ\n"
            f"ผู้ดูแลระบบจะตรวจสอบและอนุมัติบัญชีของคุณโดยเร็วที่สุด\n"
            f"คุณจะได้รับอีเมลแจ้งเตือนอีกครั้งเมื่อบัญชีพร้อมใช้งาน\n\n"
            f"หากคุณไม่ได้ทำรายการนี้ กรุณาเพิกเฉยต่ออีเมลฉบับนี้\n\n"
            f"ขอบคุณครับ,\nทีมงาน TimSum"
        )
        email_service.send_simple_email(
            recipient_email=req.email,
            subject="ลงทะเบียนสำเร็จ - รอการอนุมัติ",
            body_text=body_text,
        )
    except Exception as e:
        logger.error(f"Failed to send registration email to {req.email}: {e}")


def _send_admin_created_user_email(
    request: Request,
    username: str,
    email: str,
) -> None:
    """Send welcome email when admin creates a user account. Non-blocking on failure."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Welcome email skipped for {email} (SMTP not configured)")
            return

        login_link = build_frontend_url("/login")

        body_text = (
            f"สวัสดี คุณ{username},\n\n"
            f"ผู้ดูแลระบบได้สร้างบัญชี TimSum ให้คุณเรียบร้อยแล้ว\n\n"
            f"รายละเอียด:\n"
            f"- อีเมล: {email}\n"
            f"- สถานะ: พร้อมใช้งาน\n\n"
            f"คุณสามารถเข้าสู่ระบบได้ทันทีที่:\n"
            f"{login_link}\n\n"
            f"กรุณาเปลี่ยนรหัสผ่านหลังจากเข้าสู่ระบบครั้งแรก\n\n"
            f"หากมีข้อสงสัย กรุณาติดต่อผู้ดูแลระบบ\n\n"
            f"ขอบคุณที่ใช้บริการ TimSum V3"
        )
        email_service.send_simple_email(
            recipient_email=email,
            subject="บัญชี TimSum ของคุณพร้อมใช้งานแล้ว",
            body_text=body_text,
        )
    except Exception as e:
        logger.error(f"Failed to send welcome email to {email}: {e}")


# ── Password Reset ──


@router.post("/forgot-password")
async def forgot_password(
    req: ForgotPasswordRequest,
    request: Request,
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        email = req.email.strip().lower()
        enforce_rate_limits(
            request,
            ((FORGOT_IP, None), (FORGOT_EMAIL, email)),
        )
        user = mongo_service.get_user_by_email(email)
        if not user:
            # For security, return success even if user not found
            return {"status": "success", "message": "หากอีเมลนี้อยู่ในระบบ เราได้ส่งลิงก์รีเซ็ตรหัสผ่านไปให้แล้ว"}
            
        # Validate operator-controlled public URL before creating a credential.
        # This prevents issuing a token that cannot be delivered safely.
        build_frontend_url("/reset-password")

        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(timezone.utc) + timedelta(hours=1)
        mongo_service.create_password_reset_token(str(user.id), token, expires_at)

        reset_link = build_frontend_url(
            "/reset-password",
            {"token": token},
        )
        
        email_service: EmailService = request.app.state.email_service
        if email_service.is_configured:
            body_text = f"สวัสดี,\n\nคุณได้ร้องขอการเปลี่ยนรหัสผ่านสำหรับบัญชี TimSum\nกรุณาคลิกที่ลิงก์ด้านล่างเพื่อตั้งรหัสผ่านใหม่ (ลิงก์มีอายุ 1 ชั่วโมง):\n\n{reset_link}\n\nหากคุณไม่ได้ทำรายการนี้ กรุณาเพิกเฉยต่ออีเมลฉบับนี้\n\nขอบคุณครับ,\nทีมงาน TimSum"
            email_service.send_simple_email(
                recipient_email=email,
                subject="รีเซ็ตรหัสผ่าน TimSum",
                body_text=body_text
            )
        else:
            # Token is intentionally NOT logged — it acts as a credential
            logger.info(f"Password reset link generated for {email} (SMTP not configured)")
            
        return {"status": "success", "message": "หากอีเมลนี้อยู่ในระบบ เราได้ส่งลิงก์รีเซ็ตรหัสผ่านไปให้แล้ว"}
    except HTTPException:
        raise
    except SecurityConfigurationError as e:
        logger.error("Password reset configuration error: {}", e)
        raise HTTPException(status_code=503, detail="Password reset service is unavailable")
    except Exception as e:
        logger.error(f"Forgot password error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาด กรุณาลองใหม่อีกครั้ง")


@router.post("/reset-password")
async def reset_password(
    req: ResetPasswordRequest,
    request: Request,
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        enforce_rate_limits(
            request,
            ((RESET_IP, None), (RESET_TOKEN, req.token)),
        )

        # MongoService implements this with find_one_and_delete so a reset
        # credential can be consumed by at most one concurrent request.
        token_doc = mongo_service.consume_password_reset_token(req.token)
        if not token_doc:
            raise HTTPException(status_code=400, detail="ลิงก์รีเซ็ตรหัสผ่านไม่ถูกต้องหรือหมดอายุแล้ว")
            
        if token_doc.get("user_id"):
            try:
                user = mongo_service.get_user_by_id(token_doc["user_id"])
            except ValueError:
                user = None
        else:
            # Compatibility for reset records created before token hashing.
            user = mongo_service.get_user_by_email(token_doc.get("email", ""))
        if not user:
            raise HTTPException(status_code=400, detail="ไม่พบผู้ใช้ในระบบ")

        # Invalidate credentials from concurrent forgot-password requests as
        # well as the credential already consumed above.
        mongo_service.delete_password_reset_tokens_for_user(str(user.id))
        mongo_service.update_user_password(str(user.id), req.new_password)
        # Close the race with a forgot-password request that inserted a token
        # between the first invalidation and the password update.
        mongo_service.delete_password_reset_tokens_for_user(str(user.id))
        
        return {"status": "success", "message": "เปลี่ยนรหัสผ่านสำเร็จ คุณสามารถเข้าสู่ระบบด้วยรหัสผ่านใหม่ได้ทันที"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Reset password error: {e}")
        raise HTTPException(status_code=500, detail="เกิดข้อผิดพลาดในการเปลี่ยนรหัสผ่าน")
