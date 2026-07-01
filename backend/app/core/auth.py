import os
import jwt
from typing import Optional
from bson import ObjectId
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.models.user import UserData, USER_STATUS_APPROVED

security = HTTPBearer(auto_error=False)

def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET_KEY is not configured")
    return secret

def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Security(security),
) -> UserData:
    """Validate JWT token and return fresh UserData from the database."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token is missing!")
    
    token = credentials.credentials
    try:
        secret = get_jwt_secret()
        user_data_dict = jwt.decode(token, secret, algorithms=["HS256"])

        user_id = user_data_dict.get("id") or user_data_dict.get("_id")
        if not user_id or not ObjectId.is_valid(str(user_id)):
            raise HTTPException(status_code=401, detail="Token is invalid!")

        mongo_service = getattr(request.app.state, "mongo_service", None)
        if not mongo_service:
            raise HTTPException(status_code=500, detail="Auth service is unavailable")

        user_doc = mongo_service.get_user_document_by_id(
            str(user_id),
            {"password": 0, "salt": 0},
        )
        if not user_doc:
            raise HTTPException(status_code=401, detail="User no longer exists")

        status = user_doc.get("status", USER_STATUS_APPROVED)
        if status != USER_STATUS_APPROVED:
            raise HTTPException(status_code=403, detail="User account is not approved")

        return UserData(
            _id=user_doc["_id"],
            username=user_doc.get("username", ""),
            email=user_doc.get("email", ""),
            role=user_doc.get("role", "user"),
        )

    except HTTPException:
        raise
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired!")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token is invalid!")

def get_current_admin(user: UserData = Security(get_current_user)) -> UserData:
    """Check if the current user has admin or superadmin role."""
    if user.role not in ("admin", "superadmin"):
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource!")
    return user

def get_current_superadmin(user: UserData = Security(get_current_user)) -> UserData:
    """Check if the current user has superadmin role."""
    if user.role != "superadmin":
        raise HTTPException(status_code=403, detail="Super Admin permission is required!")
    return user


def get_current_consented_user(
    request: Request,
    user: UserData = Security(get_current_user),
) -> UserData:
    """Require the current user to have accepted all required consent versions."""
    from app.models.consent import CONSENT_TYPES, REQUIRED_CONSENT_TYPES

    mongo_service = getattr(request.app.state, "mongo_service", None)
    if not mongo_service:
        raise HTTPException(status_code=500, detail="Consent service is unavailable")

    required_versions = {k: v["version"] for k, v in CONSENT_TYPES.items() if v["required"]}
    if not mongo_service.has_required_consents(str(user.id), REQUIRED_CONSENT_TYPES, required_versions):
        raise HTTPException(status_code=403, detail="Required consent is missing")
    return user
