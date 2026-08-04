import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from bson import ObjectId

from app.core.auth import get_current_admin
from app.core.authorization import authorize_user_management
from app.models.user import UserData
from app.services.mongo import MongoService

router = APIRouter(prefix="/api/admin", tags=["admin-queue"])
VALID_JOB_STATUSES = {
    "queued",
    "processing",
    "completed",
    "partially_completed",
    "failed",
    "cancelled",
}
logger = logging.getLogger(__name__)


def get_mongo(request: Request) -> MongoService:
    return request.app.state.mongo_service


@router.get("/queue/stats")
async def queue_stats(
    _admin: UserData = Depends(get_current_admin),
    mongo: MongoService = Depends(get_mongo),
):
    return {"success": True, "stats": mongo.get_job_stats()}


@router.get("/queue/tasks")
async def queue_tasks(
    status: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: UserData = Depends(get_current_admin),
    mongo: MongoService = Depends(get_mongo),
):
    if status and status not in VALID_JOB_STATUSES:
        raise HTTPException(status_code=400, detail="สถานะงานไม่ถูกต้อง")
    jobs = mongo.get_all_jobs(
        status=status,
        user_id=user_id,
        limit=limit,
        offset=offset,
    )
    return {
        "success": True,
        "jobs": jobs,
        "count": len(jobs),
        "total": mongo.count_jobs(status=status, user_id=user_id),
        "users": mongo.get_job_filter_users(),
    }


@router.delete("/queue/tasks/{job_id}")
async def cancel_task(
    job_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo: MongoService = Depends(get_mongo),
):
    try:
        job_doc = mongo.db.job.find_one({"_id": ObjectId(job_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Job ID ไม่ถูกต้อง")

    if not job_doc:
        raise HTTPException(status_code=404, detail="ไม่พบงานนี้")

    try:
        target_user = mongo.get_user_by_id(str(job_doc["user_id"]))
    except ValueError:
        raise HTTPException(status_code=409, detail="เจ้าของงานไม่อยู่ในระบบ")
    if str(admin.id) != str(target_user.id):
        actor_role = getattr(admin, "role", "user")
        target_role = getattr(target_user, "role", "user")
        if actor_role == "admin" and target_role != "user":
            raise HTTPException(status_code=403, detail="Admin ยกเลิกงานได้เฉพาะของผู้ใช้ทั่วไปเท่านั้น")

    if job_doc.get("status") == "cancelled":
        return {
            "success": True,
            "already_cancelled": True,
            "cleanup_status": job_doc.get("cancellation_cleanup_status", "pending"),
            "quota_settlement": job_doc.get("quota_settlement", "pending"),
        }
    if job_doc.get("status") not in ("queued", "processing"):
        raise HTTPException(status_code=409, detail="งานนี้อยู่ในสถานะสิ้นสุดแล้วและยกเลิกไม่ได้")

    # Correctness boundary: persist cancellation before contacting Celery.
    cancelled = mongo.cancel_job(job_id)
    if not cancelled:
        current = mongo.db.job.find_one({"_id": ObjectId(job_id)}) or {}
        if current.get("status") == "cancelled":
            return {
                "success": True,
                "already_cancelled": True,
                "cleanup_status": current.get("cancellation_cleanup_status", "pending"),
                "quota_settlement": current.get("quota_settlement", "pending"),
            }
        raise HTTPException(status_code=409, detail="สถานะงานเปลี่ยนระหว่างการยกเลิก กรุณาลองอีกครั้ง")

    task_ids = {
        str(task_id)
        for task_id in (job_doc.get("celery_task_id"), job_doc.get("summary_celery_task_id"))
        if task_id
    }
    for celery_task_id in task_ids:
        try:
            from app.celery_app import celery_app

            celery_app.control.revoke(celery_task_id, terminate=False)
        except Exception as exc:
            logger.warning("Could not revoke celery task %s for job %s: %s", celery_task_id, job_id, exc)

    cleanup_status = "queued"
    try:
        from app.tasks.maintenance import finalize_cancelled_job

        finalize_cancelled_job.apply_async(args=[job_id], queue="maintenance")
    except Exception as exc:
        cleanup_status = "pending_reconciliation"
        logger.warning("Could not enqueue cancelled-job cleanup for %s: %s", job_id, exc)

    mongo.log_activity(str(admin.id), "admin_cancel_job", resource_type="job", resource_id=job_id)
    return {
        "success": True,
        "already_cancelled": False,
        "message": "ยกเลิกงานเรียบร้อยแล้ว",
        "cleanup_status": cleanup_status,
        "quota_settlement": "pending",
    }
