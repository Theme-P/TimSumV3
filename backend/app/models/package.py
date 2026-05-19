from datetime import datetime
from typing import Optional
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


# ── Default package definitions (seeded on startup) ──

DEFAULT_PACKAGES = [
    {
        "name": "TimSumBasic",
        "description": "แพ็กเกจพื้นฐาน สำหรับทดลองใช้งาน",
        "price": 200,
        "billing_cycle": "monthly",
        "limits": {
            "transcription_minutes_per_month": 180,
            "max_audio_minutes_per_file": 30,
            "max_files_per_month": 6,
            "ai_summary_per_month": 6,
        },
        "tier": 0,
    },
    {
        "name": "TimSumPro",
        "description": "แพ็กเกจสำหรับมืออาชีพ",
        "price": 400,
        "billing_cycle": "monthly",
        "limits": {
            "transcription_minutes_per_month": 900,
            "max_audio_minutes_per_file": 90,
            "max_files_per_month": 20,
            "ai_summary_per_month": 20,
            "custom_prompt_enabled": True,
        },
        "tier": 1,
    },
    {
        "name": "TimSumEnterprise",
        "description": "แพ็กเกจองค์กร รายเดือน",
        "price": 1000,
        "billing_cycle": "monthly",
        "limits": {
            "transcription_minutes_per_month": 3200,
            "max_audio_minutes_per_file": 300,
            "max_files_per_month": 250,
            "ai_summary_per_month": 250,
            "custom_prompt_enabled": True,
        },
        "tier": 2,
    },
    {
        "name": "TimSumEnterprise (รายปี)",
        "description": "แพ็กเกจองค์กร รายปี ประหยัดกว่า",
        "price": 6000,
        "billing_cycle": "yearly",
        "limits": {
            "transcription_minutes_per_month": 3200,
            "max_audio_minutes_per_file": 300,
            "max_files_per_month": 250,
            "ai_summary_per_month": 250,
            "custom_prompt_enabled": True,
        },
        "tier": 2,
    },
]

# Special unlimited package for admin / superadmin
ADMIN_PACKAGE = {
    "name": "TimSumAdmin",
    "description": "แพ็กเกจสำหรับผู้ดูแลระบบ",
    "price": 0,
    "billing_cycle": "none",
    "limits": {
        "transcription_minutes_per_month": 99999,
        "max_audio_minutes_per_file": 99999,
        "max_files_per_month": 99999,
        "ai_summary_per_month": 99999,
        "custom_prompt_enabled": True,
    },
    "tier": 10,
}

SUPERADMIN_PACKAGE = {
    "name": "TimSumSuperAdmin",
    "description": "แพ็กเกจสำหรับ Super Admin — ไม่จำกัด",
    "price": 0,
    "billing_cycle": "none",
    "limits": {
        "transcription_minutes_per_month": 99999,
        "max_audio_minutes_per_file": 99999,
        "max_files_per_month": 99999,
        "ai_summary_per_month": 99999,
        "custom_prompt_enabled": True,
    },
    "tier": 99,
}


class PackageLimits(BaseModel):
    transcription_minutes_per_month: int = 180
    max_audio_minutes_per_file: int = 30
    max_files_per_month: int = 6
    ai_summary_per_month: int = 6
    custom_prompt_enabled: bool = False


class Package(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    name: str
    description: str = ""
    price: float = 0
    billing_cycle: str = "monthly"  # monthly, yearly, none
    limits: PackageLimits = PackageLimits()
    tier: int = 0  # higher = better; used for badge display
    is_active: bool = True
    created_at: Optional[datetime] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )


class UserPackageUsage(BaseModel):
    files_this_month: int = 0
    ai_summaries_this_month: int = 0
    transcription_minutes_this_month: float = 0


class UserPackage(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    user_id: ObjectId
    package_id: ObjectId
    status: str = "active"  # active, expired
    started_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None
    usage: UserPackageUsage = UserPackageUsage()
    usage_reset_month: Optional[str] = None  # "2026-05" format, tracks last reset
    assigned_by: Optional[str] = None

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )
