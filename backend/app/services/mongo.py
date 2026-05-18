import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo import MongoClient
from app.models.user import User, UserData, Quota

logger = logging.getLogger(__name__)

class MongoService:
    def __init__(self, uri: str, db_name: str) -> None:
        self.client = MongoClient(uri)
        self.db = self.client[db_name]

        # Explicitly create collections if they don't exist
        required_collections = ["user", "quota", "session", "job"]
        existing_collections = self.db.list_collection_names()

        for collection in required_collections:
            if collection not in existing_collections:
                self.db.create_collection(collection)

    def _hash_password(self, password: str, salt: Optional[str] = None) -> tuple[str, str]:
        """Hash password with salt using PBKDF2."""
        if salt is None:
            salt = secrets.token_hex(32)

        # Use PBKDF2 with SHA256 for strong password hashing
        hashed = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return hashed.hex(), salt

    def _verify_password(self, password: str, hashed_password: str, salt: str) -> bool:
        """Verify password against stored hash."""
        test_hash, _ = self._hash_password(password, salt)
        return secrets.compare_digest(test_hash, hashed_password)

    def authenticate_user(self, email: str, password: str) -> Optional[User]:
        """Authenticate user with email and password."""
        user_data = self.db.user.find_one({"email": email})
        if not user_data:
            return None

        if self._verify_password(password, user_data["password"], user_data["salt"]):
            return User(**user_data)
        return None

    def get_user_by_id(self, user_id: str) -> User:
        """Retrieve a user by their ID."""
        user = self.db.user.find_one({"_id": ObjectId(user_id)})
        if not user:
            msg = "User not found"
            raise ValueError(msg)
        return User(**user)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Retrieve a user by their email."""
        user = self.db.user.find_one({"email": email})
        if not user:
            return None
        return User(**user)

    def get_quota_by_user_id(self, user_id: ObjectId) -> Quota:
        """Retrieve quota by user ID."""
        quota = self.db.quota.find_one({"user_id": user_id})
        if not quota:
            logger.debug(f"Quota not found for user_id: {user_id}")
            msg = "Quota not found for user"
            raise ValueError(msg)
        return Quota(**quota)

    def create_user(self, user: User) -> None:
        """Create a new user in the database with encrypted password."""
        if self.db.user.find_one({"email": user.email}):
            msg = "User with this email already exists"
            raise ValueError(msg)

        # Hash the password before storing
        password_str = user.password.get_secret_value()
        hashed_password, salt = self._hash_password(password_str)

        user_data = user.model_dump(by_alias=True)
        user_data["password"] = hashed_password
        user_data["salt"] = salt

        self.db.user.insert_one(user_data)

    def create_quota(self, quota: Quota) -> None:
        """Create a new quota for a user."""
        if self.db.quota.find_one({"user_id": quota.user_id}):
            msg = "Quota for this user already exists"
            raise ValueError(msg)
        self.db.quota.insert_one(quota.model_dump(by_alias=True))

    def update_user(self, user_id: str, user: User) -> None:
        """Update an existing user."""
        user_data = user.model_dump(by_alias=True)

        # If password is being updated, hash it
        if "password" in user_data:
            password_str = user.password.get_secret_value()
            hashed_password, salt = self._hash_password(password_str)
            user_data["password"] = hashed_password
            user_data["salt"] = salt

        result = self.db.user.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": user_data},
        )
        if result.matched_count == 0:
            msg = "User not found"
            raise ValueError(msg)

    def update_quota(self, user_id: ObjectId, quota: Quota) -> None:
        """Update an existing quota for a user."""
        result = self.db.quota.update_one(
            {"user_id": user_id},
            {"$set": quota.model_dump(by_alias=True)},
        )
        if result.matched_count == 0:
            msg = "Quota not found for user"
            raise ValueError(msg)

    def delete_user(self, user_id: str) -> None:
        """Delete a user by their ID."""
        result = self.db.user.delete_one({"_id": ObjectId(user_id)})
        if result.deleted_count == 0:
            msg = "User not found"
            raise ValueError(msg)

    def delete_quota(self, user_id: ObjectId) -> None:
        """Delete a quota by user ID."""
        result = self.db.quota.delete_one({"user_id": user_id})
        if result.deleted_count == 0:
            msg = "Quota not found for user"
            raise ValueError(msg)

    # ── Session / History ──

    def save_session(self, session_doc: dict) -> str:
        """Save a processing session to history."""
        result = self.db.session.insert_one(session_doc)
        return str(result.inserted_id)

    def get_sessions_by_user(self, user_id: ObjectId, limit: int = 50) -> list:
        """Get lightweight session list for a user (no full transcript)."""
        cursor = (
            self.db.session.find(
                {"user_id": user_id},
                {"transcript.segments": 0, "transcript.combined_text": 0},
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        sessions = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["user_id"] = str(doc["user_id"])
            if doc.get("created_at"):
                doc["created_at"] = doc["created_at"].isoformat()
            sessions.append(doc)
        return sessions

    def get_session_by_id(self, session_id: str, user_id: ObjectId) -> Optional[dict]:
        """Get full session detail by ID (only if owned by user)."""
        doc = self.db.session.find_one({
            "_id": ObjectId(session_id),
            "user_id": user_id,
        })
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        if doc.get("created_at"):
            doc["created_at"] = doc["created_at"].isoformat()
        return doc

    # ── Job Queue ──

    def create_job(
        self,
        user_id: ObjectId,
        audio_file: str,
        meeting_type_id: int,
        audio_path: str,
        email_recipient: str = "",
    ) -> str:
        """Create a new processing job. Returns job ID."""
        doc = {
            "user_id": user_id,
            "status": "queued",
            "current_step": "queued",
            "progress": 0,
            "audio_file": audio_file,
            "audio_path": audio_path,
            "meeting_type_id": meeting_type_id,
            "result": None,
            "session_id": None,
            "error": None,
            "celery_task_id": None,
            "created_at": datetime.now(timezone.utc),
            "completed_at": None,
            # Email auto-send fields. email_status: null | queued | sending | sent | failed
            "email_recipient": email_recipient or None,
            "email_status": "queued" if email_recipient else None,
            "email_error": None,
            "email_sent_at": None,
        }
        result = self.db.job.insert_one(doc)
        return str(result.inserted_id)

    def get_job(self, job_id: str, user_id: ObjectId) -> Optional[dict]:
        """Get job status (only if owned by user). Returns lightweight status."""
        doc = self.db.job.find_one(
            {"_id": ObjectId(job_id), "user_id": user_id},
            # Exclude heavy result data for status polling
            {"result": 0},
        )
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        for ts_field in ("created_at", "completed_at", "email_sent_at"):
            if doc.get(ts_field):
                doc[ts_field] = doc[ts_field].isoformat()
        return doc

    def get_job_result(self, job_id: str, user_id: ObjectId) -> Optional[dict]:
        """Get full job result (only when completed)."""
        doc = self.db.job.find_one({
            "_id": ObjectId(job_id),
            "user_id": user_id,
            "status": "completed",
        })
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        if doc.get("created_at"):
            doc["created_at"] = doc["created_at"].isoformat()
        if doc.get("completed_at"):
            doc["completed_at"] = doc["completed_at"].isoformat()
        return doc
