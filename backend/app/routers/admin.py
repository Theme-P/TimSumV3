from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from loguru import logger

from app.models.user import UserData, VALID_STATUSES
from app.services.mongo import MongoService
from app.core.auth import get_current_admin

router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


class UpdateStatusRequest(BaseModel):
    status: str  # approved, rejected, suspended


@router.get("/users")
async def list_users(
    status: Optional[str] = Query(None, description="Filter by status: pending, approved, rejected, suspended"),
    limit: int = Query(100, ge=1, le=500),
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List users, optionally filtered by status. Admin only."""
    if status and status not in VALID_STATUSES:
        raise HTTPException(status_code=400, detail=f"Invalid status. Must be one of: {', '.join(VALID_STATUSES)}")

    users = mongo_service.get_users_by_status(status=status, limit=limit)
    return {"success": True, "users": users, "count": len(users)}


@router.get("/users/stats")
async def user_stats(
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get user count grouped by status. Admin only."""
    counts = mongo_service.get_user_count_by_status()
    return {"success": True, "counts": counts}


@router.put("/users/{user_id}/approve")
async def approve_user(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Approve a pending user. Admin only."""
    updated = mongo_service.update_user_status(user_id, "approved", admin_id=str(admin.id))
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} approved by admin {admin.id}")
    return {"success": True, "message": "อนุมัติผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/reject")
async def reject_user(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Reject a pending user. Admin only."""
    updated = mongo_service.update_user_status(user_id, "rejected")
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} rejected by admin {admin.id}")
    return {"success": True, "message": "ปฏิเสธผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Suspend an active user. Admin only."""
    updated = mongo_service.update_user_status(user_id, "suspended")
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} suspended by admin {admin.id}")
    return {"success": True, "message": "ระงับผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    req: UpdateStatusRequest,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Update user status to any valid value. Admin only."""
    try:
        admin_id = str(admin.id) if req.status == "approved" else None
        updated = mongo_service.update_user_status(user_id, req.status, admin_id=admin_id)
        if not updated:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
        logger.info(f"User {user_id} status changed to {req.status} by admin {admin.id}")
        return {"success": True, "message": f"อัปเดตสถานะเป็น {req.status} เรียบร้อย"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
