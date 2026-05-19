from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from loguru import logger

from app.models.user import UserData
from app.services.mongo import MongoService
from app.core.auth import get_current_user, get_current_admin

router = APIRouter(prefix="/api", tags=["package"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


# ── User endpoints ──

@router.get("/user/package")
async def get_my_package(
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get current user's package info and usage."""
    up = mongo_service.get_user_package(str(user.id))
    if not up:
        return {"success": True, "package": None, "message": "ยังไม่มีแพ็กเกจ"}
    return {"success": True, "package": up}


@router.get("/packages")
async def list_packages(
    _user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List all available packages (for user to see options)."""
    packages = mongo_service.get_all_packages(active_only=True)
    # Filter out internal admin packages
    public = [p for p in packages if p.get("tier", 0) < 10]
    return {"success": True, "packages": public}


# ── Admin endpoints ──

class AssignPackageRequest(BaseModel):
    package_id: str


@router.get("/admin/packages")
async def admin_list_packages(
    active_only: bool = Query(True),
    _admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List all packages (admin view, includes internal)."""
    packages = mongo_service.get_all_packages(active_only=active_only)
    return {"success": True, "packages": packages}


@router.put("/admin/users/{user_id}/package")
async def assign_package(
    user_id: str,
    req: AssignPackageRequest,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Assign or change a user's package. Admin only."""
    # Verify package exists
    pkg = mongo_service.get_package_by_id(req.package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจที่ระบุ")

    mongo_service.assign_user_package(
        user_id=user_id,
        package_id=req.package_id,
        assigned_by=str(admin.id),
    )
    logger.info(f"Package '{pkg['name']}' assigned to user {user_id} by admin {admin.id}")
    return {"success": True, "message": f"กำหนดแพ็กเกจ {pkg['name']} เรียบร้อย"}


@router.get("/admin/users/{user_id}/package")
async def get_user_package_admin(
    user_id: str,
    _admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get a specific user's package info. Admin only."""
    up = mongo_service.get_user_package(user_id)
    return {"success": True, "package": up}
