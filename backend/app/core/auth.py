import os
import jwt
from typing import Optional
from bson import ObjectId
from fastapi import Request, HTTPException, Security
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.models.user import UserData

security = HTTPBearer(auto_error=False)

def get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET_KEY")
    if not secret:
        raise HTTPException(status_code=500, detail="JWT_SECRET_KEY is not configured")
    return secret

def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Security(security)) -> UserData:
    """Validate JWT token and return UserData."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token is missing!")
    
    token = credentials.credentials
    try:
        secret = get_jwt_secret()
        user_data_dict = jwt.decode(token, secret, algorithms=["HS256"])
        
        # Convert string ID back to ObjectId for UserData model
        if "id" in user_data_dict:
            user_data_dict["_id"] = ObjectId(user_data_dict.pop("id"))
            
        user_data = UserData(**user_data_dict)
        return user_data

    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token has expired!")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Token is invalid!")

def get_current_admin(user: UserData = Security(get_current_user)) -> UserData:
    """Check if the current user has admin role."""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="You do not have permission to access this resource!")
    return user
