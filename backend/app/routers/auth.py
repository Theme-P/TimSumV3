# pyrefly: ignore [missing-import]
import jwt
import os
from datetime import datetime, timezone, timedelta
from pydantic import BaseModel
# pyrefly: ignore [missing-import]
from fastapi import APIRouter, HTTPException, Depends, Request
# pyrefly: ignore [missing-import]
from loguru import logger

from app.models.user import User, Quota, UserData
from app.services.mongo import MongoService
from app.core.auth import get_jwt_secret, get_current_admin
# pyrefly: ignore [missing-import]
from bson import ObjectId

router = APIRouter(prefix="/api/auth", tags=["auth"])

class LoginRequest(BaseModel):
    email: str
    password: str

class RegisterRequest(BaseModel):
    username: str
    email: str
    password: str

# Helper function to get MongoDB service from app state
def get_mongo_service(request: Request) -> MongoService:
    return request.app.state.mongo_service

@router.post("/login")
async def login(req: LoginRequest, mongo_service: MongoService = Depends(get_mongo_service)):
    """Authenticate a user and return a JWT token."""
    try:
        user = mongo_service.authenticate_user(req.email, req.password)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        # Generate JWT token
        secret = get_jwt_secret()
        expire_hours = int(os.getenv("JWT_EXPIRE_HOURS", "8"))
        token_payload = {
            "id": str(user.id),
            "username": user.username,
            "email": user.email,
            "role": user.role,
            "exp": datetime.now(timezone.utc) + timedelta(hours=expire_hours),
        }
        token = jwt.encode(token_payload, secret, algorithm="HS256")

        return {
            "status": "success",
            "message": "Login successful",
            "token": token,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Login error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")

@router.post("/register", status_code=201)
async def register(
    req: RegisterRequest,
    mongo_service: MongoService = Depends(get_mongo_service),
    _: UserData = Depends(get_current_admin),
):
    """Register a new user. Requires admin JWT token."""
    try:
        # Check if user already exists
        existing_user = mongo_service.get_user_by_email(req.email)
        if existing_user:
            raise HTTPException(status_code=409, detail="User with this email already exists")

        # Create new user
        new_user = User(
            username=req.username,
            email=req.email,
            password=req.password,
            role="user",
        )
        new_quota = Quota(
            user_id=new_user.id,
            value1=0,
            value2=0,
            value3=0,
            value4=0,
        )

        mongo_service.create_user(new_user)
        mongo_service.create_quota(new_quota)

        return {
            "status": "success",
            "message": "User registered successfully",
        }

    except ValueError as e:
        logger.error(f"Registration error: {e}")
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected registration error: {e}")
        raise HTTPException(status_code=500, detail="An unexpected error occurred")
