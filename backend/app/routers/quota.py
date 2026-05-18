from fastapi import APIRouter, HTTPException, Depends, Request
from loguru import logger

from app.models.user import UserData
from app.services.mongo import MongoService
from app.core.auth import get_current_user

router = APIRouter(prefix="/api/quota", tags=["quota"])

# Helper function to get MongoDB service from app state
def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service

@router.get("")
async def get_quota(
    current_user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service)
):
    """Retrieve user quota. Requires valid JWT token."""
    try:
        quota = mongo_service.get_quota_by_user_id(current_user.id)

        return {
            "status": "success",
            "message": "Quota retrieved successfully",
            "data": {
                "id": str(quota.id),
                "user_id": str(quota.user_id),
                "value1": quota.value1,
                "value2": quota.value2,
                "value3": quota.value3,
                "value4": quota.value4,
            },
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Quota retrieval error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
