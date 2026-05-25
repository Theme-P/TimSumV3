from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId


class ActivityLog(BaseModel):
    user_id: str
    action: str          # e.g. "login", "upload_audio", "view_session"
    resource_type: Optional[str] = None   # e.g. "session", "voice_sample"
    resource_id: Optional[str] = None
    ip_address: Optional[str] = None
    metadata: dict = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# Canonical action name constants
ACTION_LOGIN = "login"
ACTION_LOGIN_FAILED = "login_failed"
ACTION_LOGOUT = "logout"
ACTION_REGISTER = "register"

ACTION_UPLOAD_AUDIO = "upload_audio"
ACTION_VIEW_SESSION = "view_session"
ACTION_VIEW_HISTORY = "view_history"
ACTION_DELETE_SESSION = "delete_session"
ACTION_EXPORT_TRANSCRIPT = "export_transcript"
ACTION_EXPORT_SUMMARY = "export_summary"
ACTION_SEND_EMAIL = "send_email"

ACTION_UPDATE_PROFILE = "update_profile"
ACTION_CHANGE_PASSWORD = "change_password"

ACTION_VOICE_UPLOAD = "voice_sample_upload"
ACTION_VOICE_DELETE = "voice_sample_delete"

ACTION_ADMIN_APPROVE = "admin_approve_user"
ACTION_ADMIN_REJECT = "admin_reject_user"
ACTION_ADMIN_SUSPEND = "admin_suspend_user"
ACTION_ADMIN_ASSIGN_PACKAGE = "admin_assign_package"

ACTION_CONSENT_GIVEN = "consent_given"
ACTION_CONSENT_WITHDRAWN = "consent_withdrawn"
