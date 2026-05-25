from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field

# Current policy versions — bump these to require re-consent
PRIVACY_POLICY_VERSION = "1.0"
TERMS_VERSION = "1.0"
DATA_PROCESSING_VERSION = "1.0"

CONSENT_TYPES = {
    "privacy_policy": {
        "label": "นโยบายความเป็นส่วนตัว",
        "version": PRIVACY_POLICY_VERSION,
        "required": True,
    },
    "data_processing": {
        "label": "การประมวลผลข้อมูลเสียงและ Transcript",
        "version": DATA_PROCESSING_VERSION,
        "required": True,
    },
    "marketing": {
        "label": "รับข้อมูลข่าวสารและโปรโมชั่น",
        "version": "1.0",
        "required": False,
    },
}

REQUIRED_CONSENT_TYPES = [k for k, v in CONSENT_TYPES.items() if v["required"]]


class ConsentRecord(BaseModel):
    user_id: str
    consent_type: str       # "privacy_policy" | "data_processing" | "marketing"
    version: str
    consented: bool
    consented_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ip_address: Optional[str] = None
    withdrawn_at: Optional[datetime] = None
