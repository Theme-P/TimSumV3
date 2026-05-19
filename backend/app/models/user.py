from typing import Optional
from datetime import datetime
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field, SecretStr


# Valid user statuses
USER_STATUS_PENDING = "pending"
USER_STATUS_APPROVED = "approved"
USER_STATUS_REJECTED = "rejected"
USER_STATUS_SUSPENDED = "suspended"
VALID_STATUSES = [USER_STATUS_PENDING, USER_STATUS_APPROVED, USER_STATUS_REJECTED, USER_STATUS_SUSPENDED]

# Valid roles
ROLE_USER = "user"
ROLE_ADMIN = "admin"
ROLE_SUPERADMIN = "superadmin"
VALID_ROLES = [ROLE_USER, ROLE_ADMIN, ROLE_SUPERADMIN]


class User(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    username: str
    email: str
    password: SecretStr
    role: str = ROLE_USER
    salt: Optional[str] = None
    # Registration fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    organization: Optional[str] = None
    status: str = USER_STATUS_APPROVED  # default approved for admin-created users
    registered_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    approved_by: Optional[str] = None  # admin user_id who approved

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )


class UserData(BaseModel):
    id: ObjectId = Field(alias="_id")
    username: str
    email: str
    role: str

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )

class Quota(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    user_id: ObjectId
    value1: float = Field(ge=0, le=100, description="Value 1 must be between 0 and 100")
    value2: float = Field(ge=0, le=100, description="Value 2 must be between 0 and 100")
    value3: float = Field(ge=0, le=100, description="Value 3 must be between 0 and 100")
    value4: float = Field(ge=0, le=100, description="Value 4 must be between 0 and 100")

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )
