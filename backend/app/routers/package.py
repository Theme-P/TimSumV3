from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel, Field

from app.core.auth import get_current_admin, get_current_user
from app.models.package import PackageLimits, VALID_PACKAGE_REQUEST_STATUSES
from app.models.user import User, UserData
from app.services.email_service import EmailService
from app.services.mongo import MongoService
from app.core.authorization import authorize_user_management
from app.services.security import get_client_ip

router = APIRouter(prefix="/api", tags=["package"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


class AssignPackageRequest(BaseModel):
    package_id: str
    reset_usage: bool = True


class PackagePayload(BaseModel):
    name: str
    description: str = ""
    price: float = Field(0, ge=0)
    billing_cycle: str = "monthly"
    limits: PackageLimits = Field(default_factory=PackageLimits)
    tier: int = Field(0, ge=0, le=99)
    is_active: bool = True


class PackageRequestCreate(BaseModel):
    requested_package_id: str
    note: str = Field("", max_length=1000)


class PackageRequestReview(BaseModel):
    admin_note: str = Field("", max_length=1000)
    reset_usage: bool = True


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
    """List all available public packages."""
    packages = mongo_service.get_all_packages(active_only=True)
    public = [p for p in packages if p.get("tier", 0) < 10]
    return {"success": True, "packages": public}


@router.get("/user/package-requests")
async def list_my_package_requests(
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List current user's package change requests."""
    requests = mongo_service.get_package_requests(user_id=str(user.id), limit=20)
    return {"success": True, "requests": requests}


@router.post("/user/package-requests", status_code=201)
async def create_my_package_request(
    req: PackageRequestCreate,
    request: Request,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Request package upgrade/downgrade/change."""
    try:
        if user.role != "user":
            raise HTTPException(status_code=403, detail="บัญชีผู้ดูแลใช้ได้เฉพาะ internal package ที่กำหนดโดย Super Admin")
        requested_package = mongo_service.get_package_by_id(req.requested_package_id)
        if not requested_package or requested_package.get("is_active") is False:
            raise HTTPException(status_code=404, detail="แพ็กเกจที่ขอไม่พร้อมใช้งาน")
        if int(requested_package.get("tier", 0) or 0) >= 10:
            raise HTTPException(status_code=403, detail="ไม่สามารถขอ internal package ได้")
        request_id = mongo_service.create_package_request(
            user_id=str(user.id),
            requested_package_id=req.requested_package_id,
            note=req.note,
        )
        mongo_service.log_activity(
            str(user.id),
            "package_request_create",
            resource_type="package_request",
            resource_id=request_id,
            ip_address=get_client_ip(request),
        )

        # Send confirmation email to user
        pkg = mongo_service.get_package_by_id(req.requested_package_id)
        pkg_name = pkg["name"] if pkg else req.requested_package_id
        _send_package_request_confirmation_email(
            request, str(user.id), user.username, user.email, pkg_name, req.note, mongo_service,
        )

        return {
            "success": True,
            "request_id": request_id,
            "message": "ส่งคำขอเปลี่ยนแพ็กเกจเรียบร้อยแล้ว",
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/user/package-requests/{request_id}")
async def cancel_my_package_request(
    request_id: str,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Cancel own pending package request."""
    cancelled = mongo_service.cancel_package_request(request_id, str(user.id))
    if not cancelled:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอที่ยกเลิกได้")
    mongo_service.log_activity(
        str(user.id),
        "package_request_cancel",
        resource_type="package_request",
        resource_id=request_id,
    )
    return {"success": True, "message": "ยกเลิกคำขอเรียบร้อย"}


# ── Admin package endpoints ──

@router.get("/admin/packages")
async def admin_list_packages(
    active_only: bool = Query(True),
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List all packages, including internal packages."""
    packages = mongo_service.get_all_packages(active_only=active_only)
    if admin.role != "superadmin":
        packages = [package for package in packages if int(package.get("tier", 0) or 0) < 10]
    return {"success": True, "packages": packages}


@router.post("/admin/packages", status_code=201)
async def admin_create_package(
    req: PackagePayload,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Create a package."""
    if req.tier >= 10:
        raise HTTPException(status_code=400, detail="ไม่สามารถสร้าง internal package ผ่านหน้า admin ได้")
    if mongo_service.get_package_by_name(req.name):
        raise HTTPException(status_code=409, detail="มีชื่อแพ็กเกจนี้อยู่แล้ว")

    package_id = mongo_service.create_package(req.model_dump())
    mongo_service.log_activity(
        str(admin.id),
        "admin_create_package",
        resource_type="package",
        resource_id=package_id,
        metadata={"name": req.name},
    )
    return {"success": True, "package_id": package_id, "message": "สร้างแพ็กเกจเรียบร้อย"}


@router.put("/admin/packages/{package_id}")
async def admin_update_package(
    package_id: str,
    req: PackagePayload,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Update a package."""
    existing = mongo_service.get_package_by_id(package_id)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจ")
    if existing.get("tier", 0) >= 10:
        raise HTTPException(status_code=403, detail="ไม่สามารถแก้ไข internal package ได้")

    same_name = mongo_service.get_package_by_name(req.name)
    if same_name and same_name.get("_id") != package_id:
        raise HTTPException(status_code=409, detail="มีชื่อแพ็กเกจนี้อยู่แล้ว")

    updated = mongo_service.update_package_by_id(package_id, req.model_dump())
    if not updated:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจ")

    mongo_service.log_activity(
        str(admin.id),
        "admin_update_package",
        resource_type="package",
        resource_id=package_id,
        metadata={"name": req.name},
    )
    return {"success": True, "message": "อัปเดตแพ็กเกจเรียบร้อย"}


@router.delete("/admin/packages/{package_id}")
async def admin_deactivate_package(
    package_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Soft-delete a package."""
    existing = mongo_service.get_package_by_id(package_id)
    if not existing:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจ")
    if existing.get("tier", 0) >= 10:
        raise HTTPException(status_code=403, detail="ไม่สามารถปิด internal package ได้")

    deactivated = mongo_service.deactivate_package(package_id)
    if not deactivated:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจ")

    mongo_service.log_activity(
        str(admin.id),
        "admin_deactivate_package",
        resource_type="package",
        resource_id=package_id,
        metadata={"name": existing.get("name")},
    )
    return {"success": True, "message": "ปิดใช้งานแพ็กเกจเรียบร้อย"}


@router.put("/admin/users/{user_id}/package")
async def assign_package(
    user_id: str,
    req: AssignPackageRequest,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Assign or change a user's package."""
    pkg = mongo_service.get_package_by_id(req.package_id)
    if not pkg:
        raise HTTPException(status_code=404, detail="ไม่พบแพ็กเกจที่ระบุ")

    try:
        target = mongo_service.get_user_by_id(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    authorize_user_management(admin, target, "package")

    tier = int(pkg.get("tier", 0) or 0)
    if tier >= 10:
        if admin.role != "superadmin":
            raise HTTPException(status_code=403, detail="ต้องใช้สิทธิ์ Super Admin สำหรับ internal package")
        expected_role = "superadmin" if tier >= 99 else "admin"
        if target.role != expected_role:
            raise HTTPException(status_code=409, detail="Internal package ไม่ตรงกับ role ของผู้ใช้")
    elif target.role != "user":
        raise HTTPException(status_code=409, detail="บัญชีผู้ดูแลต้องใช้ internal package ที่ตรงกับ role")

    try:
        mongo_service.assign_user_package(
            user_id=user_id,
            package_id=req.package_id,
            assigned_by=str(admin.id),
            reset_usage=req.reset_usage,
            source="admin",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    mongo_service.log_activity(
        str(admin.id),
        "admin_assign_package",
        resource_type="user",
        resource_id=user_id,
        metadata={"package_id": req.package_id, "reset_usage": req.reset_usage},
    )
    logger.info(f"Package '{pkg['name']}' assigned to user {user_id} by admin {admin.id}")
    return {"success": True, "message": f"กำหนดแพ็กเกจ {pkg['name']} เรียบร้อย"}


@router.get("/admin/users/{user_id}/package")
async def get_user_package_admin(
    user_id: str,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get a specific user's package info."""
    try:
        target = mongo_service.get_user_by_id(user_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้")
    authorize_user_management(admin, target, "view_package")
    up = mongo_service.get_user_package(user_id)
    return {"success": True, "package": up}


# ── Admin package request endpoints ──

@router.get("/admin/package-requests")
async def admin_list_package_requests(
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    _admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """List package change requests for admin review."""
    if status and status not in VALID_PACKAGE_REQUEST_STATUSES:
        raise HTTPException(status_code=400, detail="สถานะคำขอไม่ถูกต้อง")
    requests = mongo_service.get_package_requests(status=status, limit=limit)
    return {"success": True, "requests": requests, "count": len(requests)}


@router.put("/admin/package-requests/{request_id}/approve")
async def admin_approve_package_request(
    request_id: str,
    req: PackageRequestReview,
    request: Request,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Approve package change request and assign package."""
    package_request = mongo_service.get_package_request_by_id(request_id)
    if not package_request:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
    if package_request.get("status") not in {"pending", "applying", "approved"}:
        raise HTTPException(status_code=400, detail="คำขอนี้ถูกพิจารณาแล้ว")

    pkg = mongo_service.get_package_by_id(package_request["requested_package_id"])
    if not pkg or pkg.get("is_active") is False:
        raise HTTPException(status_code=404, detail="แพ็กเกจที่ขอไม่พร้อมใช้งาน")
    if int(pkg.get("tier", 0) or 0) >= 10:
        raise HTTPException(status_code=403, detail="ไม่สามารถอนุมัติ internal package ผ่านคำขอผู้ใช้")

    try:
        target = mongo_service.get_user_by_id(package_request["user_id"])
    except ValueError:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้เจ้าของคำขอ")
    authorize_user_management(admin, target, "package_request")
    if target.role != "user":
        raise HTTPException(status_code=409, detail="บัญชีผู้ดูแลไม่สามารถรับ public package")
    if target.deletion_pending or target.status != "approved":
        raise HTTPException(status_code=409, detail="บัญชีผู้ใช้ไม่พร้อมสำหรับการเปลี่ยนแพ็กเกจ")

    try:
        outcome = mongo_service.apply_package_request(
            request_id,
            reviewed_by=str(admin.id),
            admin_note=req.admin_note,
            reset_usage=req.reset_usage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=409,
            detail="คำขอกำลังถูกประมวลผลหรือโควต้าเปลี่ยนระหว่างอนุมัติ กรุณาลองใหม่",
        ) from exc

    # Retried requests complete the durable checkpoint but must not duplicate
    # audit/email side effects after the original approval succeeded.
    if not outcome.get("idempotent"):
        mongo_service.log_activity(
            str(admin.id),
            "admin_approve_package_request",
            resource_type="package_request",
            resource_id=request_id,
            metadata={"user_id": package_request["user_id"], "package_id": package_request["requested_package_id"]},
        )
        _send_package_approved_email(
            request, package_request["user_id"], pkg["name"], mongo_service,
        )

    return {
        "success": True,
        "idempotent": bool(outcome.get("idempotent")),
        "message": "อนุมัติและเปลี่ยนแพ็กเกจเรียบร้อย",
    }


@router.put("/admin/package-requests/{request_id}/reject")
async def admin_reject_package_request(
    request_id: str,
    req: PackageRequestReview,
    request: Request,
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Reject package change request."""
    package_request = mongo_service.get_package_request_by_id(request_id)
    if not package_request:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
    if package_request.get("status") != "pending":
        raise HTTPException(status_code=400, detail="คำขอนี้ถูกพิจารณาแล้ว")

    try:
        target = mongo_service.get_user_by_id(package_request["user_id"])
    except ValueError:
        raise HTTPException(status_code=404, detail="ไม่พบผู้ใช้เจ้าของคำขอ")
    authorize_user_management(admin, target, "package_request")

    # Fetch package name before rejecting (for the email body)
    pkg = mongo_service.get_package_by_id(package_request["requested_package_id"])
    pkg_name = pkg["name"] if pkg else "ไม่ทราบชื่อ"

    updated = mongo_service.update_package_request_status(
        request_id,
        "rejected",
        reviewed_by=str(admin.id),
        admin_note=req.admin_note,
        expected_status="pending",
    )
    if not updated:
        raise HTTPException(status_code=400, detail="คำขอนี้ถูกพิจารณาแล้ว")
    mongo_service.log_activity(
        str(admin.id),
        "admin_reject_package_request",
        resource_type="package_request",
        resource_id=request_id,
        metadata={"user_id": package_request["user_id"]},
    )

    # Send rejection email to user
    _send_package_rejected_email(
        request, package_request["user_id"], pkg_name, req.admin_note, mongo_service,
    )

    return {"success": True, "message": "ปฏิเสธคำขอเรียบร้อย"}


# ── Email Helpers ──

def _send_package_request_confirmation_email(
    request: Request,
    user_id: str,
    username: str,
    email: str,
    package_name: str,
    note: str,
    mongo_service: MongoService,
) -> None:
    """Send confirmation email after user submits a package change request. Non-blocking."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Package request email skipped for {email} (SMTP not configured)")
            return

        display_name = username or email
        note_line = f"\n- หมายเหตุ: {note}" if note else ""
        body_text = (
            f"สวัสดี คุณ{display_name},\n\n"
            f"คำขอเปลี่ยนแพ็กเกจของคุณได้ถูกบันทึกเรียบร้อยแล้ว\n\n"
            f"รายละเอียดคำขอ:\n"
            f"- แพ็กเกจที่ขอ: {package_name}{note_line}\n\n"
            f"สถานะ: รอการพิจารณาจากผู้ดูแลระบบ\n"
            f"คุณจะได้รับอีเมลแจ้งผลเมื่อผู้ดูแลระบบพิจารณาเสร็จสิ้น\n\n"
            f"ขอบคุณครับ,\nทีมงาน TimSum"
        )
        email_service.send_simple_email(
            recipient_email=email,
            subject="คำขอเปลี่ยนแพ็กเกจของคุณถูกบันทึกแล้ว",
            body_text=body_text,
        )
    except Exception as e:
        logger.error(f"Failed to send package request email to {email}: {e}")


def _send_package_approved_email(
    request: Request,
    user_id: str,
    package_name: str,
    mongo_service: MongoService,
) -> None:
    """Send email notifying user their package request was approved. Non-blocking."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Package approval email skipped for user {user_id} (SMTP not configured)")
            return

        user: User = mongo_service.get_user_by_id(user_id)
        display_name = user.username or user.email

        body_text = (
            f"สวัสดี คุณ{display_name},\n\n"
            f"คำขอเปลี่ยนแพ็กเกจของคุณได้รับการอนุมัติเรียบร้อยแล้ว\n\n"
            f"รายละเอียด:\n"
            f"- แพ็กเกจใหม่: {package_name}\n"
            f"- สถานะ: เปลี่ยนแพ็กเกจสำเร็จ\n\n"
            f"คุณสามารถเริ่มใช้งานแพ็กเกจใหม่ได้ทันที\n\n"
            f"หากมีข้อสงสัย กรุณาติดต่อผู้ดูแลระบบ\n\n"
            f"ขอบคุณที่ใช้บริการ TimSum V3"
        )
        email_service.send_simple_email(
            recipient_email=user.email,
            subject="คำขอเปลี่ยนแพ็กเกจได้รับการอนุมัติแล้ว",
            body_text=body_text,
        )
    except Exception as e:
        logger.error(f"Failed to send package approval email for user {user_id}: {e}")


def _send_package_rejected_email(
    request: Request,
    user_id: str,
    package_name: str,
    admin_note: str,
    mongo_service: MongoService,
) -> None:
    """Send email notifying user their package request was rejected. Non-blocking."""
    try:
        email_service: EmailService = request.app.state.email_service
        if not email_service.is_configured:
            logger.info(f"Package rejection email skipped for user {user_id} (SMTP not configured)")
            return

        user: User = mongo_service.get_user_by_id(user_id)
        display_name = user.username or user.email

        # Only show admin note line if admin provided one
        note_line = f"\n- หมายเหตุจากผู้ดูแล: {admin_note}" if admin_note else ""

        body_text = (
            f"สวัสดี คุณ{display_name},\n\n"
            f"คำขอเปลี่ยนแพ็กเกจของคุณได้รับการพิจารณาแล้ว\n\n"
            f"รายละเอียด:\n"
            f"- แพ็กเกจที่ขอ: {package_name}\n"
            f"- สถานะ: ไม่อนุมัติ{note_line}\n\n"
            f"หากต้องการข้อมูลเพิ่มเติม กรุณาติดต่อผู้ดูแลระบบ\n\n"
            f"ขอบคุณที่ใช้บริการ TimSum V3"
        )
        email_service.send_simple_email(
            recipient_email=user.email,
            subject="ผลการพิจารณาคำขอเปลี่ยนแพ็กเกจ",
            body_text=body_text,
        )
    except Exception as e:
        logger.error(f"Failed to send package rejection email for user {user_id}: {e}")
