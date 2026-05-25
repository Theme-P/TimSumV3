from fastapi import APIRouter, Depends, Request, Query
from typing import Optional

from app.models.user import UserData
from app.services.mongo import MongoService
from app.core.auth import get_current_user, get_current_admin

router = APIRouter(prefix="/api", tags=["activity"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


@router.get("/user/activity-logs")
async def get_my_activity_logs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get current user's own activity log."""
    logs = mongo_service.get_activity_logs(user_id=str(user.id), limit=limit, offset=offset)
    total = mongo_service.count_activity_logs(user_id=str(user.id))
    return {"success": True, "logs": logs, "total": total}


@router.get("/admin/activity-logs")
async def get_all_activity_logs(
    user_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get activity logs with optional user/action filter. Admin only."""
    logs = mongo_service.get_activity_logs(user_id=user_id, action=action, limit=limit, offset=offset)
    total = mongo_service.count_activity_logs(user_id=user_id, action=action)
    return {"success": True, "logs": logs, "total": total}
