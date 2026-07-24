import hashlib
import json

from fastapi import APIRouter, Depends, Request, HTTPException, Query
from pydantic import BaseModel, ConfigDict, StrictBool

from app.models.user import UserData
from app.models.consent import CONSENT_TYPES, REQUIRED_CONSENT_TYPES
from app.services.mongo import MongoService
from app.core.auth import get_current_user, get_current_admin
from app.services.security import get_client_ip

router = APIRouter(prefix="/api", tags=["consent"])


def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service


def consent_policy_hash(consent_type: str) -> str:
    """Hash the canonical policy metadata recorded with a consent event."""
    policy = {
        "consent_type": consent_type,
        **CONSENT_TYPES[consent_type],
    }
    encoded = json.dumps(
        policy,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ConsentChoices(BaseModel):
    """Exact, strict consent choices accepted by the current policy."""

    privacy_policy: StrictBool
    data_processing: StrictBool

    model_config = ConfigDict(extra="forbid")


class ConsentSubmitRequest(BaseModel):
    consents: ConsentChoices

    model_config = ConfigDict(extra="forbid")


@router.get("/consent")
async def get_my_consents(
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get current user's consent records and required versions."""
    records = mongo_service.get_user_consents(str(user.id))
    # Build a dict of current consent status per type
    consent_status = {}
    for ct, info in CONSENT_TYPES.items():
        existing = next((r for r in records if r["consent_type"] == ct), None)
        consent_status[ct] = {
            "label": info["label"],
            "version": info["version"],
            "required": info["required"],
            "consented": existing["consented"] if existing else False,
            "current_version": existing.get("version") if existing else None,
            "version_outdated": (
                existing is not None
                and existing.get("version") != info["version"]
                and existing.get("consented")
            ),
            "consented_at": existing.get("consented_at") if existing else None,
        }
    # Check if all required consents are satisfied with current versions
    required_versions = {k: v["version"] for k, v in CONSENT_TYPES.items() if v["required"]}
    all_consented = mongo_service.has_required_consents(
        str(user.id), REQUIRED_CONSENT_TYPES, required_versions
    )
    return {
        "success": True,
        "all_required_consented": all_consented,
        "consents": consent_status,
    }


@router.post("/consent")
async def submit_consents(
    req: ConsentSubmitRequest,
    request: Request,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Submit consent choices. Required types must be True to proceed."""
    ip = get_client_ip(request)
    submitted_consents = req.consents.model_dump()

    # Validate all required consents are accepted
    for ct in REQUIRED_CONSENT_TYPES:
        if not submitted_consents.get(ct):
            raise HTTPException(
                status_code=400,
                detail=f"จำเป็นต้องยินยอม '{CONSENT_TYPES[ct]['label']}' เพื่อใช้งานระบบ",
            )

    # Save each consent
    for ct, accepted in submitted_consents.items():
        version = CONSENT_TYPES[ct]["version"]
        mongo_service.save_consent(
            str(user.id),
            ct,
            version,
            accepted,
            ip_address=ip,
            policy_hash=consent_policy_hash(ct),
        )

    # Log activity
    mongo_service.log_activity(
        str(user.id), "consent_given",
        metadata={"types": list(submitted_consents.keys())},
        ip_address=ip,
    )

    return {"success": True, "message": "บันทึกการยินยอมเรียบร้อย"}


@router.delete("/consent/{consent_type}")
async def withdraw_consent(
    consent_type: str,
    request: Request,
    user: UserData = Depends(get_current_user),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Withdraw a specific consent. Required consents cannot be withdrawn."""
    if consent_type not in CONSENT_TYPES:
        raise HTTPException(status_code=404, detail="ไม่พบประเภทการยินยอมนี้")
    if CONSENT_TYPES[consent_type]["required"]:
        raise HTTPException(
            status_code=400,
            detail="ไม่สามารถถอนการยินยอมที่จำเป็นได้ หากต้องการ กรุณาติดต่อผู้ดูแลระบบเพื่อลบบัญชี",
        )

    ip = get_client_ip(request)
    version = CONSENT_TYPES[consent_type]["version"]
    mongo_service.save_consent(
        str(user.id),
        consent_type,
        version,
        False,
        ip_address=ip,
        policy_hash=consent_policy_hash(consent_type),
    )
    mongo_service.log_activity(
        str(user.id), "consent_withdrawn",
        metadata={"consent_type": consent_type},
        ip_address=ip,
    )
    return {"success": True, "message": "ถอนการยินยอมเรียบร้อย"}


@router.get("/admin/consent-records")
async def get_all_consent_records(
    limit: int = Query(200, ge=1, le=500),
    offset: int = Query(0, ge=0),
    admin: UserData = Depends(get_current_admin),
    mongo_service: MongoService = Depends(get_mongo_service),
):
    """Get all consent records. Admin only."""
    records = mongo_service.get_all_consent_records(limit=limit, offset=offset)
    return {"success": True, "records": records, "count": len(records)}
