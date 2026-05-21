from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request
from pydantic import BaseModel, field_validator
from loguru import logger
from app.services.mongo import MongoService
from app.core.auth import get_current_user
from app.models.user import UserData

router = APIRouter(prefix="/api/user", tags=["user"])

def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service

class ProfileUpdateRequest(BaseModel):
    first_name: str
    last_name: str
    phone: Optional[str] = None
    organization: Optional[str] = None

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def password_strength(cls, v):
        if len(v) < 8:
            raise ValueError("รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร")
        return v

@router.get("/profile")
async def get_profile(
    current_user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        user = mongo_service.get_user_by_id(str(current_user.id))
        return {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "phone": user.phone,
            "organization": user.organization,
            "status": user.status,
            "registered_at": user.registered_at,
        }
    except Exception as e:
        logger.error(f"Get profile error: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถดึงข้อมูลโปรไฟล์ได้")

@router.put("/profile")
async def update_profile(
    req: ProfileUpdateRequest,
    current_user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        mongo_service.update_user_profile(str(current_user.id), req.model_dump())
        return {"status": "success", "message": "อัปเดตข้อมูลโปรไฟล์สำเร็จ"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Update profile error: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถอัปเดตข้อมูลโปรไฟล์ได้")

@router.put("/change-password")
async def change_password(
    req: ChangePasswordRequest,
    current_user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service)
):
    try:
        # First verify current password
        user = mongo_service.authenticate_user(current_user.email, req.current_password)
        if not user:
            raise HTTPException(status_code=400, detail="รหัสผ่านปัจจุบันไม่ถูกต้อง")
            
        mongo_service.update_user_password(str(current_user.id), req.new_password)
        return {"status": "success", "message": "เปลี่ยนรหัสผ่านสำเร็จ"}
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Change password error: {e}")
        raise HTTPException(status_code=500, detail="ไม่สามารถเปลี่ยนรหัสผ่านได้")
