"""Central authorization rules for privileged user-management actions."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import secrets

from fastapi import HTTPException
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


_STATUS_TRANSITIONS = {
    "pending": {"approved", "rejected"},
    "approved": {"suspended"},
    "rejected": {"approved"},
    "suspended": {"approved"},
}


def authorize_user_management(actor, target, action: str, *, new_status: str | None = None) -> None:
    """Enforce the admin hierarchy before mutating another user."""
    actor_id = str(actor.id)
    target_id = str(target.id)
    if actor_id == target_id:
        raise HTTPException(status_code=403, detail="ไม่สามารถจัดการบัญชีของตนเองผ่านคำสั่งนี้ได้")

    actor_role = getattr(actor, "role", "user")
    target_role = getattr(target, "role", "user")
    if actor_role == "admin" and target_role != "user":
        raise HTTPException(status_code=403, detail="Admin จัดการได้เฉพาะบัญชีผู้ใช้ทั่วไป")
    if actor_role != "superadmin" and target_role in {"admin", "superadmin"}:
        raise HTTPException(status_code=403, detail="ต้องใช้สิทธิ์ Super Admin")

    if action == "status":
        current_status = getattr(target, "status", "approved")
        if new_status == current_status:
            return
        allowed = _STATUS_TRANSITIONS.get(current_status, set())
        if new_status not in allowed:
            raise HTTPException(
                status_code=409,
                detail=f"ไม่อนุญาตให้เปลี่ยนสถานะจาก {current_status} เป็น {new_status}",
            )


@contextmanager
def superadmin_invariant_guard(mongo_service, target):
    """Serialize mutations that could remove an approved Super Admin.

    A count-then-update without this lease lets two concurrent requests each
    observe the other account and remove both. The lease lives in one Mongo
    document so it works with the standalone deployment.
    """
    if getattr(target, "role", "user") != "superadmin":
        yield
        return

    now = datetime.now(timezone.utc)
    token = secrets.token_hex(16)
    try:
        lease = mongo_service.db.system_guard.find_one_and_update(
            {
                "_id": "superadmin_invariant",
                "$or": [
                    {"lease_expires_at": {"$lte": now}},
                    {"lease_expires_at": {"$exists": False}},
                ],
            },
            {"$set": {
                "owner": token,
                "lease_expires_at": now + timedelta(seconds=30),
                "updated_at": now,
            }},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        lease = None
    if not lease or lease.get("owner") != token:
        raise HTTPException(
            status_code=409,
            detail="มีการเปลี่ยนแปลง Super Admin พร้อมกัน กรุณาลองใหม่",
        )

    try:
        remaining = mongo_service.db.user.count_documents({
            "role": "superadmin",
            "status": "approved",
            "deletion_pending": {"$ne": True},
            "_id": {"$ne": target.id},
        })
        if remaining < 1:
            raise HTTPException(
                status_code=409,
                detail="ระบบต้องมี Super Admin ที่ใช้งานได้อย่างน้อยหนึ่งบัญชี",
            )
        yield
    finally:
        mongo_service.db.system_guard.update_one(
            {"_id": "superadmin_invariant", "owner": token},
            {"$unset": {"owner": "", "lease_expires_at": ""}, "$set": {"updated_at": datetime.now(timezone.utc)}},
        )


def ensure_superadmin_remains(mongo_service, target) -> None:
    """Compatibility preflight; mutations should use the serialized guard."""
    with superadmin_invariant_guard(mongo_service, target):
        return
