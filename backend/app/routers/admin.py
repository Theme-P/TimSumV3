from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Depends, Request, Query
from loguru import logger

from app.models.user import User, UserData, VALID_STATUSES
from app.services.mongo import MongoService
from app.services.email_service import EmailService
from app.core.auth import get_current_admin, get_current_superadmin
from app.core.authorization import authorize_user_management, superadmin_invariant_guard
from app.services.security import build_frontend_url

router = APIRouter(prefix="/api/admin", tags=["admin"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


def _target_user(mongo_service: MongoService, user_id: str) -> User:
    try:
        return mongo_service.get_user_by_id(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")


def _authorize_status_change(
    mongo_service: MongoService,
    actor: UserData,
    user_id: str,
    new_status: str,
) -> User:
    target = _target_user(mongo_service, user_id)
    authorize_user_management(actor, target, "status", new_status=new_status)
    return target


def _commit_status_change(
    mongo_service: MongoService,
    target: User,
    user_id: str,
    new_status: str,
    *,
    admin_id: str | None = None,
) -> bool:
    guard_required = (
        target.role == "superadmin"
        and target.status == "approved"
        and new_status != "approved"
    )
    if guard_required:
        with superadmin_invariant_guard(mongo_service, target):
            return mongo_service.update_user_status(user_id, new_status, admin_id=admin_id)
    return mongo_service.update_user_status(user_id, new_status, admin_id=admin_id)


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
    if admin.role != "superadmin":
        users = [user for user in users if user.get("role", "user") == "user"]
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
    request: Request,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Approve a pending user. Admin only."""
    target = _authorize_status_change(mongo_service, admin, user_id, "approved")
    updated = _commit_status_change(
        mongo_service, target, user_id, "approved", admin_id=str(admin.id)
    )
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} approved by admin {admin.id}")
    mongo_service.log_activity(str(admin.id), "admin_approve_user",
                               resource_type="user", resource_id=user_id)

    # Send approval notification email to user
    _send_approval_email(request, user_id, mongo_service)

    return {"success": True, "message": "อนุมัติผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/reject")
async def reject_user(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Reject a pending user. Admin only."""
    target = _authorize_status_change(mongo_service, admin, user_id, "rejected")
    updated = _commit_status_change(mongo_service, target, user_id, "rejected")
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} rejected by admin {admin.id}")
    mongo_service.log_activity(str(admin.id), "admin_reject_user",
                               resource_type="user", resource_id=user_id)
    return {"success": True, "message": "ปฏิเสธผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/suspend")
async def suspend_user(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Suspend an active user. Admin only."""
    target = _authorize_status_change(mongo_service, admin, user_id, "suspended")
    updated = _commit_status_change(mongo_service, target, user_id, "suspended")
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    logger.info(f"User {user_id} suspended by admin {admin.id}")
    mongo_service.log_activity(str(admin.id), "admin_suspend_user",
                               resource_type="user", resource_id=user_id)
    return {"success": True, "message": "ระงับผู้ใช้เรียบร้อย"}


@router.put("/users/{user_id}/status")
async def update_user_status(
    user_id: str,
    req: UpdateStatusRequest,
    request: Request,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Update user status to any valid value. Admin only."""
    try:
        target = _authorize_status_change(mongo_service, admin, user_id, req.status)
        admin_id = str(admin.id) if req.status == "approved" else None
        updated = _commit_status_change(
            mongo_service, target, user_id, req.status, admin_id=admin_id
        )
        if not updated:
            raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
        logger.info(f"User {user_id} status changed to {req.status} by admin {admin.id}")

        # Send approval email if status changed to approved
        if req.status == "approved":
            _send_approval_email(request, user_id, mongo_service)

        return {"success": True, "message": f"อัปเดตสถานะเป็น {req.status} เรียบร้อย"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ── Email Helpers ──

def _send_approval_email(
    request: Request,
    user_id: str,
    mongo_service: MongoService,
) -> None:
    """Send approval notification email to user. Failures are logged, not raised."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Approval email skipped for user {user_id} (SMTP not configured)")
            return

        user: User = mongo_service.get_user_by_id(user_id)
        display_name = user.username or user.email

        login_link = build_frontend_url("/login")

        body_text = (
            f"สวัสดี คุณ{display_name},\n\n"
            f"บัญชี TimSum ของคุณได้รับการอนุมัติเรียบร้อยแล้ว\n\n"
            f"รายละเอียด:\n"
            f"- อีเมล: {user.email}\n"
            f"- สถานะ: อนุมัติแล้ว\n\n"
            f"คุณสามารถเข้าสู่ระบบได้ทันทีที่:\n"
            f"{login_link}\n\n"
            f"หากมีข้อสงสัย กรุณาติดต่อผู้ดูแลระบบ\n\n"
            f"ขอบคุณที่ใช้บริการ TimSum V3"
        )
        email_service.send_simple_email(
            recipient_email=user.email,
            subject="บัญชีของคุณได้รับการอนุมัติแล้ว",
            body_text=body_text,
        )
    except Exception as e:
        # Email failure should not block the approval action
        logger.error(f"Failed to send approval email for user {user_id}: {e}")

@router.delete("/users/{user_id}", status_code=202)
async def delete_user(
    user_id: str,
    superadmin: UserData = Depends(get_current_superadmin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Start an idempotent, asynchronous account-deletion workflow."""
    try:
        target = _target_user(mongo_service, user_id)
        authorize_user_management(superadmin, target, "delete")
        with superadmin_invariant_guard(mongo_service, target):
            manifest = mongo_service.create_deletion_manifest(user_id, str(superadmin.id))
        deletion_id = manifest["deletion_id"]
        enqueue_status = "queued"
        try:
            from app.tasks.maintenance import delete_account

            delete_account.apply_async(args=[deletion_id], queue="maintenance")
        except Exception as exc:
            # The durable pending manifest is picked up by the reconciler. Do
            # not roll back deletion_pending/JWT revocation after this boundary.
            enqueue_status = "pending_reconciliation"
            logger.error(f"Could not enqueue account deletion {deletion_id}: {exc}")

        logger.info(f"Account deletion {deletion_id} requested by superadmin {superadmin.id}")
        mongo_service.log_activity(
            str(superadmin.id),
            "superadmin_request_user_deletion",
            resource_type="data_deletion",
            resource_id=deletion_id,
        )
        return {
            "success": True,
            "deletion_id": deletion_id,
            "status": manifest.get("status", "pending"),
            "phase": manifest.get("phase", "cancel_jobs"),
            "enqueue_status": enqueue_status,
        }
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/deletions/{deletion_id}")
async def get_deletion_status(
    deletion_id: str,
    _superadmin: UserData = Depends(get_current_superadmin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Return the non-PII progress manifest for an account deletion."""
    manifest = mongo_service.get_deletion_manifest(deletion_id)
    if not manifest:
        raise HTTPException(status_code=404, detail="ไม่พบรายการลบบัญชี")
    return {"success": True, "deletion": manifest}
