# pyrefly: ignore [missing-import]
import jwt
import os
from datetime import datetime, timezone, timedelta
from typing import Optional
from pydantic import BaseModel, field_validator
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Depends, Request
# pyrefly: ignore [missing-import]
from loguru import logger

from app.models.user import User, Quota, UserData, USER_STATUS_APPROVED
from app.services.mongo import MongoService
from app.core.auth import get_jwt_secret, get_current_admin
# pyrefly: ignore [missing-import]
from bson import ObjectId

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
