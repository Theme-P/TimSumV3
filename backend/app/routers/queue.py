from fastapi import APIRouter, Depends, HTTPException, Request
from bson import ObjectId

from app.core.auth import get_current_admin
from app.models.user import UserData
from app.services.mongo import MongoService

router = APIRouter(prefix="/api/admin", tags=["admin-queue"])


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
    status: str = None,
    limit: int = 50,
    offset: int = 0,
    _admin: UserData = Depends(get_current_admin),
    mongo: MongoService = Depends(get_mongo),
):
    jobs = mongo.get_all_jobs(status=status, limit=min(limit, 200), offset=offset)
    return {"success": True, "jobs": jobs, "count": len(jobs)}


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
    if job_doc.get("status") not in ("queued", "processing"):
        raise HTTPException(status_code=400, detail="งานนี้ไม่สามารถยกเลิกได้ (สถานะไม่ใช่ queued/processing)")

    celery_task_id = job_doc.get("celery_task_id")
    if celery_task_id:
        try:
            from app.celery_app import celery_app
            celery_app.control.revoke(celery_task_id, terminate=True, signal="SIGTERM")
        except Exception:
            pass  # best-effort — worker may be unreachable

    cancelled = mongo.cancel_job(job_id)
    if not cancelled:
        raise HTTPException(status_code=400, detail="ไม่สามารถยกเลิกงานได้")

    mongo.log_activity(str(admin.id), "admin_cancel_job", resource_type="job", resource_id=job_id)
    return {"success": True, "message": "ยกเลิกงานเรียบร้อยแล้ว"}