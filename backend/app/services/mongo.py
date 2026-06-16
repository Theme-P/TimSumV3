import hashlib
import secrets
import logging
from datetime import datetime, timezone
from typing import Optional

from bson import ObjectId
from pymongo import MongoClient
from app.models.user import User, UserData, Quota, USER_STATUS_APPROVED, VALID_STATUSES

logger = logging.getLogger(__name__)

class MongoService:
    def __init__(self, uri: str, db_name: str, cache=None) -> None:
        self.client = MongoClient(uri, tz_aware=True)
        self.db = self.client[db_name]
        self.cache = cache  # Optional CacheService instance

        # Explicitly create collections if they don't exist
        required_collections = [
            "user", "quota", "session", "job",
            "package", "user_package", "password_reset", "voice_sample",
            "activity_log", "consent_record", "llm_config", "meeting_template",
            "package_request", "package_assignment_history",
        ]
        existing_collections = self.db.list_collection_names()

        for collection in required_collections:
            if collection not in existing_collections:
                self.db.create_collection(collection)

        # TTL indexes — auto-delete stale documents
        self.db.activity_log.create_index("timestamp", expireAfterSeconds=90 * 24 * 3600, background=True)   # 90 days
        self.db.session.create_index("created_at", expireAfterSeconds=90 * 24 * 3600, background=True)        # 90 days
        self.db.password_reset.create_index("created_at", expireAfterSeconds=7 * 24 * 3600, background=True)  # 7 days

        # Indexes for fast lookups
        self.db.activity_log.create_index([("user_id", 1), ("timestamp", -1)], background=True)
        self.db.consent_record.create_index([("user_id", 1), ("consent_type", 1)], background=True)

        # Performance indexes (Phase 16.2)
        self.db.user.create_index("email", unique=True, background=True)
        self.db.quota.create_index("user_id", unique=True, background=True)
        self.db.user_package.create_index("user_id", unique=True, background=True)
        self.db.package.create_index("name", unique=True, background=True)
        self.db.session.create_index("user_id", background=True)
        self.db.job.create_index([("user_id", 1), ("status", 1)], background=True)
        self.db.job.create_index("status", background=True)
        self.db.voice_sample.create_index("user_id", background=True)
        self.db.llm_config.create_index("name", unique=True, background=True)
        self.db.package_request.create_index([("status", 1), ("requested_at", -1)], background=True)
        self.db.package_request.create_index([("user_id", 1), ("status", 1)], background=True)
        self.db.package_assignment_history.create_index([("user_id", 1), ("changed_at", -1)], background=True)

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
        """Authenticate user with email and password. Only approved users can login."""
        user_data = self.db.user.find_one({"email": email})
        if not user_data:
            return None

        if not self._verify_password(password, user_data["password"], user_data["salt"]):
            return None

        user = User(**user_data)
        # Check user status — only approved users can login
        status = user_data.get("status", USER_STATUS_APPROVED)
        if status != USER_STATUS_APPROVED:
            return None

        return user

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
        """Delete a user by their ID and clean up related data."""
        obj_id = ObjectId(user_id)
        result = self.db.user.delete_one({"_id": obj_id})
        if result.deleted_count == 0:
            msg = "User not found"
            raise ValueError(msg)
        
        # Cascade deletes for related collections
        self.db.quota.delete_one({"user_id": obj_id})
        self.db.user_package.delete_many({"user_id": obj_id})
        self.db.session.delete_many({"user_id": obj_id})
        self.db.voice_sample.delete_many({"user_id": obj_id})
        self.db.consent_record.delete_many({"user_id": str(user_id)})
        self.db.package_request.delete_many({"user_id": obj_id})
        self.db.package_assignment_history.delete_many({"user_id": obj_id})

    def delete_quota(self, user_id: ObjectId) -> None:
        """Delete a quota by user ID."""
        result = self.db.quota.delete_one({"user_id": user_id})
        if result.deleted_count == 0:
            msg = "Quota not found for user"
            raise ValueError(msg)

    # ── Google SSO ──

    def find_or_create_google_user(self, google_id: str, email: str, name: str) -> User:
        """
        Find existing user by email or create a new Google SSO user.
        - If user exists with same email → link google_id and return (must be approved).
        - If user doesn't exist → create with status=approved, random password.
        Returns User or raises ValueError if user exists but is not approved.
        """
        existing = self.db.user.find_one({"email": email})

        if existing:
            user = User(**existing)
            status = existing.get("status", USER_STATUS_APPROVED)
            if status != USER_STATUS_APPROVED:
                raise ValueError(status)
            # Link google_id if not already set
            if not existing.get("google_id"):
                self.db.user.update_one(
                    {"_id": existing["_id"]},
                    {"$set": {"google_id": google_id}},
                )
            return user

        # Create new user with random password (Google users login via SSO only)
        random_password = secrets.token_urlsafe(32)
        hashed_password, salt = self._hash_password(random_password)

        # Split name into first/last
        name_parts = name.strip().split(" ", 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""

        user_doc = {
            "_id": ObjectId(),
            "username": name or email.split("@")[0],
            "email": email,
            "password": hashed_password,
            "salt": salt,
            "role": "user",
            "status": USER_STATUS_APPROVED,
            "first_name": first_name,
            "last_name": last_name,
            "google_id": google_id,
            "registered_at": datetime.now(timezone.utc),
            "approved_at": datetime.now(timezone.utc),
            "approved_by": "google_sso",
        }
        self.db.user.insert_one(user_doc)

        # Create quota for the new user
        quota_doc = {
            "_id": ObjectId(),
            "user_id": user_doc["_id"],
            "value1": 0, "value2": 0, "value3": 0, "value4": 0,
        }
        self.db.quota.insert_one(quota_doc)

        # Assign default TimSumBasic package
        basic_pkg = self.get_package_by_name("TimSumBasic")
        if basic_pkg:
            self.assign_user_package(
                str(user_doc["_id"]), basic_pkg["_id"], assigned_by="google_sso"
            )

        return User(**user_doc)

    # ── User Status & Admin Management ──

    def get_user_status(self, email: str) -> Optional[str]:
        """Get user status by email. Returns None if user not found."""
        user_data = self.db.user.find_one({"email": email}, {"status": 1})
        if not user_data:
            return None
        return user_data.get("status", USER_STATUS_APPROVED)

    def register_public_user(self, user: User) -> str:
        """Register a new public user with pending status. Returns user_id."""
        if self.db.user.find_one({"email": user.email}):
            msg = "User with this email already exists"
            raise ValueError(msg)

        password_str = user.password.get_secret_value()
        hashed_password, salt = self._hash_password(password_str)

        user_data = user.model_dump(by_alias=True)
        user_data["password"] = hashed_password
        user_data["salt"] = salt
        user_data["status"] = "pending"
        user_data["registered_at"] = datetime.now(timezone.utc)

        result = self.db.user.insert_one(user_data)
        return str(result.inserted_id)

    def get_users_by_status(self, status: Optional[str] = None, limit: int = 100) -> list:
        """Get users filtered by status. If status is None, return all."""
        query = {}
        if status and status in VALID_STATUSES:
            query["status"] = status

        cursor = (
            self.db.user.find(query, {"password": 0, "salt": 0})
            .sort("registered_at", -1)
            .limit(limit)
        )
        users = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            for ts_field in ("registered_at", "approved_at"):
                if doc.get(ts_field):
                    doc[ts_field] = doc[ts_field].isoformat()
            users.append(doc)
        return users

    def update_user_status(self, user_id: str, status: str, admin_id: str = None) -> bool:
        """Update user status (approve/reject/suspend). Returns True if updated."""
        if status not in VALID_STATUSES:
            msg = f"Invalid status: {status}"
            raise ValueError(msg)

        update_fields = {"status": status}
        if status == USER_STATUS_APPROVED and admin_id:
            update_fields["approved_at"] = datetime.now(timezone.utc)
            update_fields["approved_by"] = admin_id

        result = self.db.user.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_fields},
        )
        return result.matched_count > 0

    def get_user_count_by_status(self) -> dict:
        """Get count of users grouped by status."""
        pipeline = [
            {"$group": {"_id": "$status", "count": {"$sum": 1}}}
        ]
        result = {s: 0 for s in VALID_STATUSES}
        for doc in self.db.user.aggregate(pipeline):
            status = doc["_id"] or USER_STATUS_APPROVED
            result[status] = doc["count"]
        return result

    # ── Package ──

    def upsert_package(self, pkg_data: dict) -> str:
        """Insert or update a package by name. Returns package_id."""
        existing = self.db.package.find_one({"name": pkg_data["name"]})
        if existing:
            self.db.package.update_one({"_id": existing["_id"]}, {"$set": pkg_data})
            if self.cache:
                self.cache.invalidate_packages()
            return str(existing["_id"])
        pkg_data.setdefault("created_at", datetime.now(timezone.utc))
        result = self.db.package.insert_one(pkg_data)
        if self.cache:
            self.cache.invalidate_packages()
        return str(result.inserted_id)

    def create_package(self, pkg_data: dict) -> str:
        """Create a new package. Returns package_id."""
        pkg_data.setdefault("is_active", True)
        pkg_data.setdefault("created_at", datetime.now(timezone.utc))
        result = self.db.package.insert_one(pkg_data)
        if self.cache:
            self.cache.invalidate_packages()
        return str(result.inserted_id)

    def update_package_by_id(self, package_id: str, pkg_data: dict) -> bool:
        """Update an existing package by ID."""
        result = self.db.package.update_one(
            {"_id": ObjectId(package_id)},
            {"$set": pkg_data},
        )
        if result.matched_count and self.cache:
            self.cache.invalidate_packages()
        return result.matched_count > 0

    def deactivate_package(self, package_id: str) -> bool:
        """Soft-delete a package by marking it inactive."""
        result = self.db.package.update_one(
            {"_id": ObjectId(package_id)},
            {"$set": {"is_active": False}},
        )
        if result.matched_count and self.cache:
            self.cache.invalidate_packages()
        return result.matched_count > 0

    def get_all_packages(self, active_only: bool = True) -> list:
        """Get all packages sorted by tier."""
        cache_key = f"pkg:all:{'active' if active_only else 'all'}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        query = {"is_active": True} if active_only else {}
        cursor = self.db.package.find(query).sort("tier", 1)
        packages = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("created_at"):
                doc["created_at"] = doc["created_at"].isoformat()
            doc["user_count"] = self.db.user_package.count_documents({
                "package_id": ObjectId(doc["_id"]),
                "status": "active",
            })
            packages.append(doc)

        if self.cache:
            self.cache.set(cache_key, packages)
        return packages

    def get_package_by_id(self, package_id: str) -> Optional[dict]:
        """Get a single package by ID."""
        doc = self.db.package.find_one({"_id": ObjectId(package_id)})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return doc

    def get_package_by_name(self, name: str) -> Optional[dict]:
        """Get a single package by name."""
        doc = self.db.package.find_one({"name": name})
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        return doc

    # ── User Package ──

    def assign_user_package(
        self,
        user_id: str,
        package_id: str,
        assigned_by: str = None,
        reset_usage: bool = True,
        source: str = "admin",
        request_id: str = None,
    ) -> str:
        """Assign a package to a user. Returns user_package id."""
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")
        user_obj_id = ObjectId(user_id)
        package_obj_id = ObjectId(package_id)
        current = self.db.user_package.find_one({"user_id": user_obj_id})

        if reset_usage or not current:
            usage = {
                "files_this_month": 0,
                "ai_summaries_this_month": 0,
                "transcription_minutes_this_month": 0,
            }
            usage_reset_month = current_month
        else:
            usage = current.get("usage", {
                "files_this_month": 0,
                "ai_summaries_this_month": 0,
                "transcription_minutes_this_month": 0,
            })
            usage_reset_month = current.get("usage_reset_month", current_month)

        doc = {
            "user_id": user_obj_id,
            "package_id": package_obj_id,
            "status": "active",
            "expires_at": None,
            "usage": usage,
            "usage_reset_month": usage_reset_month,
            "assigned_by": assigned_by,
            "updated_at": now,
        }

        if current:
            result = self.db.user_package.update_one(
                {"_id": current["_id"]},
                {"$set": doc},
                upsert=False,
            )
            user_package_id = str(current["_id"])
        else:
            doc["started_at"] = now
            result = self.db.user_package.insert_one(doc)
            user_package_id = str(result.inserted_id)

        self.db.package_assignment_history.insert_one({
            "user_id": user_obj_id,
            "from_package_id": current.get("package_id") if current else None,
            "to_package_id": package_obj_id,
            "changed_by": assigned_by,
            "changed_at": now,
            "source": source,
            "request_id": ObjectId(request_id) if request_id else None,
            "reset_usage": reset_usage,
        })

        if self.cache:
            self.cache.invalidate_user_package(user_id)
            self.cache.invalidate_packages()
        return user_package_id

    def create_package_request(self, user_id: str, requested_package_id: str, note: str = "") -> str:
        """Create a user package change request."""
        user_obj_id = ObjectId(user_id)
        requested_obj_id = ObjectId(requested_package_id)

        pending = self.db.package_request.find_one({
            "user_id": user_obj_id,
            "status": "pending",
        })
        if pending:
            raise ValueError("คุณมีคำขอเปลี่ยนแพ็กเกจที่รอการพิจารณาอยู่แล้ว")

        current = self.db.user_package.find_one({"user_id": user_obj_id})
        current_package_id = current.get("package_id") if current else None
        if current_package_id and current_package_id == requested_obj_id:
            raise ValueError("คุณใช้งานแพ็กเกจนี้อยู่แล้ว")

        current_pkg = self.db.package.find_one({"_id": current_package_id}) if current_package_id else None
        requested_pkg = self.db.package.find_one({"_id": requested_obj_id})
        if not requested_pkg or requested_pkg.get("is_active") is False:
            raise ValueError("ไม่พบแพ็กเกจที่ต้องการ")

        current_tier = current_pkg.get("tier", 0) if current_pkg else -1
        requested_tier = requested_pkg.get("tier", 0)
        if requested_tier > current_tier:
            request_type = "upgrade"
        elif requested_tier < current_tier:
            request_type = "downgrade"
        else:
            request_type = "change"

        doc = {
            "_id": ObjectId(),
            "user_id": user_obj_id,
            "current_package_id": current_package_id,
            "requested_package_id": requested_obj_id,
            "request_type": request_type,
            "status": "pending",
            "note": (note or "").strip()[:1000],
            "admin_note": "",
            "requested_at": datetime.now(timezone.utc),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        self.db.package_request.insert_one(doc)
        return str(doc["_id"])

    def _serialize_package_request(self, doc: dict) -> dict:
        """Serialize package request with joined user/package information."""
        user = self.db.user.find_one(
            {"_id": doc["user_id"]},
            {"password": 0, "salt": 0},
        )
        current_pkg = self.db.package.find_one({"_id": doc.get("current_package_id")}) if doc.get("current_package_id") else None
        requested_pkg = self.db.package.find_one({"_id": doc.get("requested_package_id")}) if doc.get("requested_package_id") else None

        result = {
            "_id": str(doc["_id"]),
            "user_id": str(doc["user_id"]),
            "current_package_id": str(doc["current_package_id"]) if doc.get("current_package_id") else None,
            "requested_package_id": str(doc["requested_package_id"]),
            "request_type": doc.get("request_type", "change"),
            "status": doc.get("status", "pending"),
            "note": doc.get("note", ""),
            "admin_note": doc.get("admin_note", ""),
            "requested_at": doc["requested_at"].isoformat() if doc.get("requested_at") else None,
            "reviewed_at": doc["reviewed_at"].isoformat() if doc.get("reviewed_at") else None,
            "reviewed_by": doc.get("reviewed_by"),
        }
        if user:
            result["user"] = {
                "_id": str(user["_id"]),
                "email": user.get("email"),
                "username": user.get("username"),
                "first_name": user.get("first_name"),
                "last_name": user.get("last_name"),
                "organization": user.get("organization"),
            }
        if current_pkg:
            result["current_package"] = {
                "_id": str(current_pkg["_id"]),
                "name": current_pkg.get("name"),
                "tier": current_pkg.get("tier", 0),
                "price": current_pkg.get("price", 0),
                "billing_cycle": current_pkg.get("billing_cycle"),
            }
        if requested_pkg:
            result["requested_package"] = {
                "_id": str(requested_pkg["_id"]),
                "name": requested_pkg.get("name"),
                "tier": requested_pkg.get("tier", 0),
                "price": requested_pkg.get("price", 0),
                "billing_cycle": requested_pkg.get("billing_cycle"),
            }
        return result

    def get_package_requests(self, status: str = None, user_id: str = None, limit: int = 100) -> list:
        """List package change requests."""
        query = {}
        if status:
            query["status"] = status
        if user_id:
            query["user_id"] = ObjectId(user_id)

        cursor = (
            self.db.package_request.find(query)
            .sort("requested_at", -1)
            .limit(limit)
        )
        return [self._serialize_package_request(doc) for doc in cursor]

    def get_package_request_by_id(self, request_id: str) -> Optional[dict]:
        doc = self.db.package_request.find_one({"_id": ObjectId(request_id)})
        if not doc:
            return None
        return self._serialize_package_request(doc)

    def update_package_request_status(
        self,
        request_id: str,
        status: str,
        reviewed_by: str = None,
        admin_note: str = "",
    ) -> bool:
        """Update package request review status."""
        result = self.db.package_request.update_one(
            {"_id": ObjectId(request_id)},
            {"$set": {
                "status": status,
                "admin_note": (admin_note or "").strip()[:1000],
                "reviewed_by": reviewed_by,
                "reviewed_at": datetime.now(timezone.utc),
            }},
        )
        return result.matched_count > 0

    def cancel_package_request(self, request_id: str, user_id: str) -> bool:
        """Cancel a pending request by owner."""
        result = self.db.package_request.update_one(
            {"_id": ObjectId(request_id), "user_id": ObjectId(user_id), "status": "pending"},
            {"$set": {
                "status": "cancelled",
                "reviewed_at": datetime.now(timezone.utc),
                "reviewed_by": str(user_id),
            }},
        )
        return result.modified_count > 0

    def get_user_package(self, user_id: str) -> Optional[dict]:
        """Get user's current package assignment with package details."""
        if self.cache:
            cached = self.cache.get_user_package(user_id)
            if cached is not None:
                return cached

        up = self.db.user_package.find_one({"user_id": ObjectId(user_id)})
        if not up:
            return None

        # Auto-reset usage if month changed
        now = datetime.now(timezone.utc)
        current_month = now.strftime("%Y-%m")
        if up.get("usage_reset_month") != current_month:
            self.db.user_package.update_one(
                {"_id": up["_id"]},
                {"$set": {
                    "usage.files_this_month": 0,
                    "usage.ai_summaries_this_month": 0,
                    "usage.transcription_minutes_this_month": 0,
                    "usage_reset_month": current_month,
                }},
            )
            up["usage"] = {"files_this_month": 0, "ai_summaries_this_month": 0, "transcription_minutes_this_month": 0}
            up["usage_reset_month"] = current_month

        # Join with package details
        pkg = self.db.package.find_one({"_id": up["package_id"]})
        result = {
            "_id": str(up["_id"]),
            "user_id": str(up["user_id"]),
            "package_id": str(up["package_id"]),
            "status": up.get("status", "active"),
            "usage": up.get("usage", {}),
            "usage_reset_month": up.get("usage_reset_month"),
            "started_at": up["started_at"].isoformat() if up.get("started_at") else None,
            "assigned_by": up.get("assigned_by"),
        }
        if pkg:
            result["package"] = {
                "_id": str(pkg["_id"]),
                "name": pkg.get("name"),
                "description": pkg.get("description"),
                "price": pkg.get("price"),
                "billing_cycle": pkg.get("billing_cycle"),
                "limits": pkg.get("limits", {}),
                "tier": pkg.get("tier", 0),
            }

        if self.cache:
            self.cache.set_user_package(user_id, result)
        return result

    def increment_usage(self, user_id: str, files: int = 0, ai_summaries: int = 0, transcription_minutes: float = 0):
        """Atomically increment usage counters for a user."""
        inc = {}
        if files:
            inc["usage.files_this_month"] = files
        if ai_summaries:
            inc["usage.ai_summaries_this_month"] = ai_summaries
        if transcription_minutes:
            inc["usage.transcription_minutes_this_month"] = transcription_minutes
        if inc:
            self.db.user_package.update_one(
                {"user_id": ObjectId(user_id)},
                {"$inc": inc},
            )
            if self.cache:
                self.cache.invalidate_user_package(user_id)

    def check_package_limits(self, user_id: str) -> dict:
        """Check if user is within package limits. Returns {allowed, reason, usage, limits}."""
        up = self.get_user_package(user_id)
        if not up or not up.get("package"):
            return {"allowed": False, "reason": "ไม่พบแพ็กเกจ กรุณาติดต่อผู้ดูแลระบบ"}

        usage = up["usage"]
        limits = up["package"]["limits"]

        if usage.get("files_this_month", 0) >= limits.get("max_files_per_month", 0):
            return {"allowed": False, "reason": "จำนวนไฟล์ที่อัปโหลดเดือนนี้ครบแล้ว"}

        if usage.get("ai_summaries_this_month", 0) >= limits.get("ai_summary_per_month", 0):
            return {"allowed": False, "reason": "จำนวน AI สรุปเดือนนี้ครบแล้ว"}

        if usage.get("transcription_minutes_this_month", 0) >= limits.get("transcription_minutes_per_month", 0):
            return {"allowed": False, "reason": "นาทีการถอดเสียงเดือนนี้ครบแล้ว"}

        return {
            "allowed": True,
            "usage": usage,
            "limits": limits,
            "max_audio_minutes_per_file": limits.get("max_audio_minutes_per_file", 30),
        }

    # ── Voice Samples ──

    def create_voice_sample(self, doc: dict) -> str:
        """Create a new voice sample. Returns sample_id."""
        result = self.db.voice_sample.insert_one(doc)
        return str(result.inserted_id)

    def get_voice_samples_by_user(self, user_id: str, limit: int = 100) -> list:
        """Get voice samples for a user (without embedding vectors)."""
        cursor = (
            self.db.voice_sample.find(
                {"user_id": ObjectId(user_id)},
                {"embedding": 0},  # Exclude large embedding vectors
            )
            .sort("created_at", -1)
            .limit(limit)
        )
        samples = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["user_id"] = str(doc["user_id"])
            if doc.get("created_at"):
                doc["created_at"] = doc["created_at"].isoformat()
            samples.append(doc)
        return samples

    def get_voice_samples_with_embeddings(self, user_id: str, limit: int = 50) -> list:
        """Get voice samples for a user INCLUDING embeddings (for matching)."""
        cursor = self.db.voice_sample.find({"user_id": ObjectId(user_id)}).limit(limit)
        samples = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["user_id"] = str(doc["user_id"])
            samples.append(doc)
        return samples

    def get_voice_sample_by_id(self, sample_id: str, user_id: str) -> dict | None:
        """Get a single voice sample by ID (ownership check)."""
        try:
            doc = self.db.voice_sample.find_one({
                "_id": ObjectId(sample_id),
                "user_id": ObjectId(user_id),
            })
        except Exception:
            return None
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        return doc

    def delete_voice_sample(self, sample_id: str, user_id: str) -> bool:
        """Delete a voice sample (ownership check). Returns True if deleted."""
        result = self.db.voice_sample.delete_one({
            "_id": ObjectId(sample_id),
            "user_id": ObjectId(user_id),
        })
        return result.deleted_count > 0

    def count_voice_samples(self, user_id: str) -> int:
        """Count voice samples for a user."""
        return self.db.voice_sample.count_documents({"user_id": ObjectId(user_id)})

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

    # ── Password Reset & Profile Update ──

    def create_password_reset_token(self, email: str, token: str, expires_at: datetime) -> None:
        """Create a new password reset token."""
        # Delete any existing tokens for this email
        self.db.password_reset.delete_many({"email": email})
        
        doc = {
            "email": email,
            "token": token,
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
        }
        self.db.password_reset.insert_one(doc)

    def get_password_reset_token(self, token: str) -> Optional[dict]:
        """Retrieve a password reset token if it hasn't expired."""
        now = datetime.now(timezone.utc)
        doc = self.db.password_reset.find_one({"token": token, "expires_at": {"$gt": now}})
        return doc

    def delete_password_reset_token(self, token: str) -> None:
        """Delete a password reset token after use."""
        self.db.password_reset.delete_one({"token": token})

    def update_user_password(self, user_id: str, new_password: str) -> None:
        """Update a user's password."""
        hashed_password, salt = self._hash_password(new_password)
        result = self.db.user.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": {"password": hashed_password, "salt": salt}},
        )
        if result.matched_count == 0:
            raise ValueError("User not found")

    # ── Activity Log ──

    def log_activity(self, user_id: str, action: str, resource_type: str = None,
                     resource_id: str = None, ip_address: str = None, metadata: dict = None) -> None:
        """Write an activity log entry. Silently swallows errors to never break callers."""
        try:
            from datetime import datetime, timezone
            doc = {
                "user_id": user_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "ip_address": ip_address,
                "metadata": metadata or {},
                "timestamp": datetime.now(timezone.utc),
            }
            self.db.activity_log.insert_one(doc)
        except Exception:
            pass  # activity log must never break the main flow

    def get_activity_logs(self, user_id: str = None, action: str = None,
                          limit: int = 100, offset: int = 0) -> list:
        """Get activity logs with optional filters. Returns newest-first."""
        query = {}
        if user_id:
            query["user_id"] = user_id
        if action:
            query["action"] = action

        cursor = (
            self.db.activity_log.find(query)
            .sort("timestamp", -1)
            .skip(offset)
            .limit(limit)
        )
        logs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("timestamp"):
                doc["timestamp"] = doc["timestamp"].isoformat()
            logs.append(doc)
        return logs

    def count_activity_logs(self, user_id: str = None, action: str = None) -> int:
        """Count activity logs matching filters."""
        query = {}
        if user_id:
            query["user_id"] = user_id
        if action:
            query["action"] = action
        return self.db.activity_log.count_documents(query)

    # ── Consent Records ──

    def save_consent(self, user_id: str, consent_type: str, version: str,
                     consented: bool, ip_address: str = None) -> None:
        """Upsert a consent record for a user+type combination."""
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "consent_type": consent_type,
            "version": version,
            "consented": consented,
            "consented_at": now,
            "ip_address": ip_address,
            "withdrawn_at": None if consented else now,
        }
        self.db.consent_record.update_one(
            {"user_id": user_id, "consent_type": consent_type},
            {"$set": doc},
            upsert=True,
        )

    def get_user_consents(self, user_id: str) -> list:
        """Get all consent records for a user."""
        cursor = self.db.consent_record.find({"user_id": user_id})
        records = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            for ts_field in ("consented_at", "withdrawn_at"):
                if doc.get(ts_field):
                    doc[ts_field] = doc[ts_field].isoformat()
            records.append(doc)
        return records

    def has_required_consents(self, user_id: str, required_types: list, required_versions: dict) -> bool:
        """Check if user has all required consents at the current version."""
        for consent_type in required_types:
            doc = self.db.consent_record.find_one({
                "user_id": user_id,
                "consent_type": consent_type,
                "consented": True,
                "withdrawn_at": None,
            })
            if not doc:
                return False
            if required_versions.get(consent_type) and doc.get("version") != required_versions[consent_type]:
                return False
        return True

    def get_all_consent_records(self, limit: int = 200, offset: int = 0) -> list:
        """Get all consent records (superadmin use)."""
        cursor = (
            self.db.consent_record.find()
            .sort("consented_at", -1)
            .skip(offset)
            .limit(limit)
        )
        records = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            for ts_field in ("consented_at", "withdrawn_at"):
                if doc.get(ts_field):
                    doc[ts_field] = doc[ts_field].isoformat()
            records.append(doc)
        return records

    # ── Queue Monitoring ──

    def get_job_stats(self) -> dict:
        """Aggregate job counts by status + today's completed count."""
        pipeline = [{"$group": {"_id": "$status", "count": {"$sum": 1}}}]
        counts = {"queued": 0, "processing": 0, "completed": 0, "failed": 0, "cancelled": 0}
        for doc in self.db.job.aggregate(pipeline):
            if doc["_id"] in counts:
                counts[doc["_id"]] = doc["count"]
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        counts["completed_today"] = self.db.job.count_documents({
            "status": "completed",
            "completed_at": {"$gte": today_start},
        })
        return counts

    def get_all_jobs(self, status: str = None, limit: int = 50, offset: int = 0) -> list:
        """List all jobs for admin view, newest first, excluding heavy result payloads."""
        query = {}
        if status:
            query["status"] = status
        cursor = (
            self.db.job.find(query, {"result": 0})
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        jobs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            doc["user_id"] = str(doc["user_id"])
            for ts_field in ("created_at", "completed_at", "email_sent_at"):
                if doc.get(ts_field):
                    doc[ts_field] = doc[ts_field].isoformat()
            jobs.append(doc)
        return jobs

    # ── LLM Config ──
    def get_all_llm_configs(self) -> list:
        """Get all LLM configurations."""
        cursor = self.db.llm_config.find().sort("name", 1)
        configs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            if doc.get("updated_at"):
                doc["updated_at"] = doc["updated_at"].isoformat()
            configs.append(doc)
        return configs

    def get_llm_config(self, name: str = "default_fallback") -> Optional[dict]:
        """Get LLM configuration."""
        doc = self.db.llm_config.find_one({"name": name})
        if doc:
            doc["_id"] = str(doc["_id"])
            if doc.get("updated_at"):
                doc["updated_at"] = doc["updated_at"].isoformat()
        return doc

    def upsert_llm_config(self, name: str, config_data: dict) -> str:
        """Insert or update LLM configuration."""
        existing = self.db.llm_config.find_one({"name": name})
        if existing:
            self.db.llm_config.update_one({"_id": existing["_id"]}, {"$set": config_data})
            return str(existing["_id"])
        config_data["name"] = name
        result = self.db.llm_config.insert_one(config_data)
        return str(result.inserted_id)

    def cancel_job(self, job_id: str) -> bool:
        """Mark a queued/processing job as cancelled. Returns True if updated."""
        result = self.db.job.update_one(
            {"_id": ObjectId(job_id), "status": {"$in": ["queued", "processing"]}},
            {"$set": {
                "status": "cancelled",
                "completed_at": datetime.now(timezone.utc),
                "error": "Cancelled by admin",
            }},
        )
        return result.modified_count > 0

    def update_user_profile(self, user_id: str, profile_data: dict) -> None:
        """Update a user's profile information."""
        allowed_fields = ["first_name", "last_name", "phone", "organization"]
        update_data = {k: v for k, v in profile_data.items() if k in allowed_fields}
        
        if not update_data:
            return
            
        result = self.db.user.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_data},
        )
        if result.matched_count == 0:
            raise ValueError("User not found")

    # ── Meeting Templates ──

    def get_meeting_template(self, meeting_type_id: str) -> Optional[dict]:
        """Get a single meeting template by ID."""
        cache_key = f"mtg_tmpl:{meeting_type_id}"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        doc = self.db.meeting_template.find_one({"meeting_type_id": meeting_type_id})
        if doc:
            doc["_id"] = str(doc["_id"])
            if self.cache:
                self.cache.set(cache_key, doc)
        return doc

    def get_all_meeting_templates(self) -> list:
        """Get all meeting templates."""
        cache_key = "mtg_tmpl:all"
        if self.cache:
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

        cursor = self.db.meeting_template.find()
        templates = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            templates.append(doc)
        
        if self.cache:
            self.cache.set(cache_key, templates)
        return templates

    def update_meeting_template(self, meeting_type_id: str, data: dict) -> bool:
        """Update or insert a meeting template."""
        data.setdefault("updated_at", datetime.now(timezone.utc))
        result = self.db.meeting_template.update_one(
            {"meeting_type_id": meeting_type_id},
            {"$set": data},
            upsert=True
        )
        
        if self.cache:
            self.cache.delete(f"mtg_tmpl:{meeting_type_id}")
            self.cache.delete("mtg_tmpl:all")
            
        return result.modified_count > 0 or result.upserted_id is not None
