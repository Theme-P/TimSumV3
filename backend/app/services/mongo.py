import hashlib
import hmac
import os
import secrets
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from app.models.user import User, UserData, Quota, USER_STATUS_APPROVED, VALID_STATUSES
from app.services.encryption import PIIEncryptor
from app.services.passwords import PasswordManager

logger = logging.getLogger(__name__)

USAGE_TIMEZONE = ZoneInfo("Asia/Bangkok")
JOB_RETENTION_DAYS = int(os.getenv("JOB_RETENTION_DAYS", "30"))
SESSION_RETENTION_DAYS = int(os.getenv("SESSION_RETENTION_DAYS", "365"))
ACTIVITY_LOG_RETENTION_DAYS = int(os.getenv("ACTIVITY_LOG_RETENTION_DAYS", "90"))
CONSENT_AUDIT_RETENTION_DAYS = int(os.getenv("CONSENT_AUDIT_RETENTION_DAYS", "365"))
EMAIL_OUTBOX_RETENTION_DAYS = int(os.getenv("EMAIL_OUTBOX_RETENTION_DAYS", "30"))
JOB_INITIALIZATION_TIMEOUT_MINUTES = int(os.getenv("JOB_INITIALIZATION_TIMEOUT_MINUTES", "15"))
JOB_RETENTION_SECONDS = JOB_RETENTION_DAYS * 24 * 3600
SESSION_RETENTION_SECONDS = SESSION_RETENTION_DAYS * 24 * 3600


def usage_period(now: Optional[datetime] = None) -> str:
    """Return the quota period in the product billing timezone."""
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(USAGE_TIMEZONE).strftime("%Y-%m")


def _reservation_key(value: str) -> str:
    """Return a Mongo-safe stable field key for a quota reservation."""
    raw = str(value or "").strip()
    if raw and all(character.isalnum() or character in "_-" for character in raw):
        return raw
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def settle_job_quota_db(db, job_id: str, outcome: str) -> bool:
    """Atomically settle one job reservation using only a raw worker database."""
    if outcome not in {"consumed", "refunded"}:
        raise ValueError("outcome must be consumed or refunded")
    try:
        job_obj_id = ObjectId(str(job_id))
    except Exception:
        return False
    job = db.job.find_one({"_id": job_obj_id})
    if not job:
        return False
    reservation_id = str(job.get("quota_reservation_id") or "")
    if not reservation_id:
        return False
    key = _reservation_key(reservation_id)
    path = f"quota_reservations.{key}"
    package = db.user_package.find_one(
        {"user_id": job["user_id"]},
        {path: 1, "usage_reset_month": 1},
    )
    reservation = ((package or {}).get("quota_reservations") or {}).get(key)
    if not reservation:
        return False

    current_state = reservation.get("state")
    target_state = outcome
    if outcome == "refunded" and reservation.get("period") != usage_period():
        target_state = "period_closed"
    if current_state == target_state or (
        outcome == "refunded" and current_state == "period_closed"
    ):
        db.job.update_one(
            {"_id": job_obj_id},
            {"$set": {
                "quota_settlement": target_state,
                "quota_refunded": outcome == "refunded",
            }},
        )
        return True
    if current_state != "reserved":
        return False

    now = datetime.now(timezone.utc)
    set_fields = {
        f"{path}.state": target_state,
        f"{path}.settled_at": now,
    }
    update: dict = {"$set": set_fields}
    query: dict = {
        "user_id": job["user_id"],
        f"{path}.state": "reserved",
        f"{path}.period": reservation.get("period"),
    }
    if target_state == "refunded":
        files = int(reservation.get("files") or 0)
        summaries = int(reservation.get("ai_summaries") or 0)
        minutes = max(float(reservation.get("minutes") or 0), 0)
        query.update({
            "usage.files_this_month": {"$gte": files},
            "usage.ai_summaries_this_month": {"$gte": summaries},
            "usage.transcription_minutes_this_month": {"$gte": minutes},
        })
        update["$inc"] = {
            "usage.files_this_month": -files,
            "usage.ai_summaries_this_month": -summaries,
            "usage.transcription_minutes_this_month": -minutes,
        }
    result = db.user_package.update_one(query, update)
    if result.modified_count == 0:
        return False
    db.job.update_one(
        {"_id": job_obj_id},
        {"$set": {
            "quota_settlement": target_state,
            "quota_refunded": outcome == "refunded",
            "quota_settled_at": now,
        }},
    )
    return True


def _drop_ttl_indexes(collection, field: str) -> None:
    """Drop only TTL indexes for a field; documents are never modified."""
    for name, info in collection.index_information().items():
        if info.get("key") == [(field, 1)] and "expireAfterSeconds" in info:
            collection.drop_index(name)


def _ensure_ttl_index(db, collection, field: str, seconds: int, name: str) -> None:
    """Reconcile a TTL duration using collMod with a safe index-only fallback."""
    indexes = collection.index_information()
    matching = [
        (index_name, info)
        for index_name, info in indexes.items()
        if info.get("key") == [(field, 1)]
    ]
    for index_name, info in matching:
        current = info.get("expireAfterSeconds")
        if current == seconds:
            return
        if current is not None:
            try:
                db.command({
                    "collMod": collection.name,
                    "index": {"name": index_name, "expireAfterSeconds": seconds},
                })
                return
            except Exception as exc:
                logger.warning(
                    "TTL collMod failed for %s.%s; recreating index only: %s",
                    collection.name,
                    field,
                    exc,
                )
                collection.drop_index(index_name)
                break
    collection.create_index(
        field,
        expireAfterSeconds=seconds,
        name=name,
        background=True,
    )

class MongoService:
    def __init__(self, uri: str, db_name: str, cache=None, pii_encryptor=None) -> None:
        self.client = MongoClient(uri, tz_aware=True)
        self.db = self.client[db_name]
        self.cache = cache  # Optional CacheService instance
        self.pii = pii_encryptor or PIIEncryptor.from_env()
        self.passwords = PasswordManager()

        # Explicitly create collections if they don't exist
        required_collections = [
            "user", "quota", "session", "job",
            "package", "user_package", "password_reset", "voice_sample",
            "activity_log", "consent_record", "llm_config", "meeting_template",
            "package_request", "package_assignment_history",
            "summary_state", "email_outbox", "data_deletion", "consent_event",
            "schema_migration", "system_guard",
        ]
        existing_collections = self.db.list_collection_names()

        for collection in required_collections:
            if collection not in existing_collections:
                self.db.create_collection(collection)

        # TTL indexes — auto-delete stale documents
        _ensure_ttl_index(
            self.db, self.db.activity_log, "timestamp",
            ACTIVITY_LOG_RETENTION_DAYS * 24 * 3600,
            "activity_log_timestamp_ttl",
        )
        _ensure_ttl_index(
            self.db, self.db.session, "created_at", SESSION_RETENTION_SECONDS,
            "session_created_at_ttl",
        )
        # Explicit token expiry is authoritative; remove the legacy blanket
        # seven-day TTL that could invalidate a deliberately longer token.
        _drop_ttl_indexes(self.db.password_reset, "created_at")
        _ensure_ttl_index(
            self.db, self.db.password_reset, "expires_at", 0,
            "password_reset_expires_at",
        )
        # A job must not expire merely because it spent time queued/running.
        _drop_ttl_indexes(self.db.job, "created_at")
        _ensure_ttl_index(
            self.db, self.db.job, "completed_at", JOB_RETENTION_SECONDS,
            "job_completed_at_ttl",
        )
        _ensure_ttl_index(
            self.db, self.db.summary_state, "expires_at", 0,
            "summary_state_expires_at",
        )
        _ensure_ttl_index(
            self.db, self.db.email_outbox, "expires_at", 0,
            "email_outbox_expires_at",
        )

        # Indexes for fast lookups
        self.db.activity_log.create_index([("user_id", 1), ("timestamp", -1)], background=True)
        self.db.consent_record.create_index([("user_id", 1), ("consent_type", 1)], background=True)

        # Performance indexes (Phase 16.2)
        # During the rolling PII migration, plaintext users are protected by
        # the legacy email index and encrypted users by a keyed blind index.
        user_indexes = self.db.user.index_information()
        has_email_index = any(
            info.get("key") == [("email", 1)] for info in user_indexes.values()
        )
        if not has_email_index and (
            not self.pii.enabled or self.pii.allow_legacy_plaintext
        ):
            self.db.user.create_index(
                "email",
                unique=True,
                partialFilterExpression={"email": {"$type": "string"}},
                name="email_legacy_unique",
                background=True,
            )
        self.db.user.create_index(
            "email_bidx",
            unique=True,
            partialFilterExpression={"email_bidx": {"$type": "string"}},
            name="email_bidx_unique",
            background=True,
        )
        self.db.quota.create_index("user_id", unique=True, background=True)
        self.db.user_package.create_index("user_id", unique=True, background=True)
        self.db.package.create_index("name", unique=True, background=True)
        self.db.session.create_index("user_id", background=True)
        self.db.session.create_index(
            "job_id",
            unique=True,
            partialFilterExpression={"job_id": {"$type": "string"}},
            name="session_job_id_unique",
            background=True,
        )
        self.db.job.create_index([("user_id", 1), ("status", 1)], background=True)
        self.db.job.create_index("status", background=True)
        self.db.job.create_index("quota_reservation_id", background=True)
        self.db.summary_state.create_index("job_id", unique=True, background=True)
        self.db.email_outbox.create_index("event_key", unique=True, background=True)
        self.db.email_outbox.create_index([("status", 1), ("next_attempt_at", 1)], background=True)
        self.db.data_deletion.create_index("deletion_id", unique=True, background=True)
        self.db.data_deletion.create_index(
            "user_id",
            unique=True,
            partialFilterExpression={"active": True},
            name="active_deletion_per_user",
            background=True,
        )
        self.db.data_deletion.create_index([("status", 1), ("updated_at", 1)], background=True)
        self.db.consent_event.create_index([("subject_id", 1), ("created_at", -1)], background=True)
        self.db.consent_event.create_index(
            "expires_at", expireAfterSeconds=0, name="consent_event_expires_at", background=True
        )
        self.db.voice_sample.create_index("user_id", background=True)
        self.db.llm_config.create_index("name", unique=True, background=True)
        self.db.package_request.create_index([("status", 1), ("requested_at", -1)], background=True)
        self.db.package_request.create_index([("user_id", 1), ("status", 1)], background=True)
        self.db.package_assignment_history.create_index([("user_id", 1), ("changed_at", -1)], background=True)
        self.db.package_assignment_history.create_index(
            "request_id",
            unique=True,
            partialFilterExpression={"request_id": {"$type": "objectId"}},
            name="package_assignment_request_unique",
            background=True,
        )
        active_request_duplicates = list(self.db.package_request.aggregate([
            {"$match": {"status": {"$in": ["pending", "applying"]}}},
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
            {"$match": {"count": {"$gt": 1}}},
            {"$limit": 1},
        ]))
        if active_request_duplicates:
            raise RuntimeError("Duplicate active package requests require migration reconciliation")
        request_indexes = self.db.package_request.index_information()
        if "uniq_pending_package_request_per_user" in request_indexes:
            self.db.package_request.drop_index("uniq_pending_package_request_per_user")
        active_index = request_indexes.get("uniq_active_package_request_per_user")
        expected_active_filter = {"status": {"$in": ["pending", "applying"]}}
        if active_index and active_index.get("partialFilterExpression") != expected_active_filter:
            raise RuntimeError("Active package-request index has old options; run workflow migration")
        if not active_index:
            self.db.package_request.create_index(
                [("user_id", 1)],
                unique=True,
                partialFilterExpression=expected_active_filter,
                name="uniq_active_package_request_per_user",
                background=True,
            )

    @staticmethod
    def _object_id(value: str) -> Optional[ObjectId]:
        """Return an ObjectId for valid strings; otherwise None."""
        try:
            if not ObjectId.is_valid(str(value)):
                return None
            return ObjectId(str(value))
        except Exception:
            return None

    def _hash_password(self, password: str, salt: Optional[str] = None) -> tuple[str, Optional[str]]:
        """Hash new credentials with Argon2id.

        The optional salt argument is retained only for source compatibility;
        Argon2 embeds a randomly generated salt in its encoded hash.
        """
        del salt
        return self.passwords.hash(password)

    def _verify_password(self, password: str, hashed_password: str, salt: Optional[str]) -> bool:
        """Verify Argon2id or a legacy PBKDF2 credential."""
        return self.passwords.verify(password, hashed_password, salt).valid

    def _user_email_query(self, email: str) -> dict:
        """Build a lookup that supports encrypted and legacy users."""
        normalized = self.pii.normalize_email(email)
        if not self.pii.enabled:
            return {"email": normalized}
        encrypted_query = {"email_bidx": self.pii.blind_index(normalized)}
        if self.pii.allow_legacy_plaintext:
            return {"$or": [encrypted_query, {"email": normalized}]}
        return encrypted_query

    def _decrypt_user_document(self, document: Optional[dict]) -> Optional[dict]:
        if document is None:
            return None
        return self.pii.decrypt_user_document(document)

    def get_user_document_by_id(
        self,
        user_id: str,
        projection: Optional[dict] = None,
    ) -> Optional[dict]:
        """Return one decrypted user document for authentication/internal use."""
        obj_id = self._object_id(user_id)
        if not obj_id:
            return None
        document = self.db.user.find_one({"_id": obj_id}, projection)
        return self._decrypt_user_document(document)

    def authenticate_user(self, email: str, password: str) -> Optional[User]:
        """Authenticate user with email and password. Only approved users can login."""
        user_data = self.db.user.find_one(self._user_email_query(email))
        if not user_data:
            return None

        verification = self.passwords.verify(
            password,
            user_data["password"],
            user_data.get("salt"),
        )
        if not verification.valid:
            return None

        if verification.needs_rehash:
            # Compare the old hash so a simultaneous password reset cannot be
            # overwritten by this transparent login-time migration.
            self.db.user.update_one(
                {"_id": user_data["_id"], "password": user_data["password"]},
                {"$set": {
                    "password": verification.upgraded_hash,
                    "salt": verification.upgraded_salt,
                    "password_rehashed_at": datetime.now(timezone.utc),
                }},
            )

        user = User(**self._decrypt_user_document(user_data))
        # Check user status — only approved users can login
        status = user_data.get("status", USER_STATUS_APPROVED)
        if status != USER_STATUS_APPROVED or user_data.get("deletion_pending"):
            return None

        return user

    def get_user_by_id(self, user_id: str) -> User:
        """Retrieve a user by their ID."""
        user = self.get_user_document_by_id(user_id)
        if not user:
            msg = "User not found"
            raise ValueError(msg)
        return User(**user)

    def get_user_by_email(self, email: str) -> Optional[User]:
        """Retrieve a user by their email."""
        user = self.db.user.find_one(self._user_email_query(email))
        if not user:
            return None
        return User(**self._decrypt_user_document(user))

    def get_quota_by_user_id(self, user_id: ObjectId) -> Quota:
        """Retrieve quota by user ID."""
        quota = self.db.quota.find_one({"user_id": user_id})
        if not quota:
            logger.debug(f"Quota not found for user_id: {user_id}")
            msg = "Quota not found for user"
            raise ValueError(msg)
        return Quota(**quota)

    def create_user(self, user: User) -> None:
        """Create a user with a hashed password and encrypted PII."""
        if self.db.user.find_one(self._user_email_query(user.email)):
            msg = "User with this email already exists"
            raise ValueError(msg)

        # Hash the password before storing
        password_str = user.password.get_secret_value()
        hashed_password, salt = self._hash_password(password_str)

        user_data = user.model_dump(by_alias=True)
        user_data["password"] = hashed_password
        user_data["salt"] = salt
        user_data.setdefault("auth_version", 1)
        user_data.setdefault("deletion_pending", False)
        user_data = self.pii.encrypt_user_document(user_data)

        try:
            self.db.user.insert_one(user_data)
        except DuplicateKeyError as exc:
            raise ValueError("User with this email already exists") from exc

    def create_quota(self, quota: Quota) -> None:
        """Create a new quota for a user."""
        if self.db.quota.find_one({"user_id": quota.user_id}):
            msg = "Quota for this user already exists"
            raise ValueError(msg)
        self.db.quota.insert_one(quota.model_dump(by_alias=True))

    def update_user(self, user_id: str, user: User) -> None:
        """Update an existing user."""
        user_data = user.model_dump(by_alias=True)
        user_data.pop("_id", None)

        # If password is being updated, hash it
        if "password" in user_data:
            password_str = user.password.get_secret_value()
            hashed_password, salt = self._hash_password(password_str)
            user_data["password"] = hashed_password
            user_data["salt"] = salt

        user_data = self.pii.encrypt_user_fields(user_id, user_data)

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
        obj_id = self._object_id(user_id)
        if not obj_id:
            raise ValueError("User not found")
        result = self.db.user.delete_one({"_id": obj_id})
        if result.deleted_count == 0:
            msg = "User not found"
            raise ValueError(msg)
        
        # Cascade deletes for related collections
        self.db.quota.delete_one({"user_id": obj_id})
        self.db.user_package.delete_many({"user_id": obj_id})
        self.db.session.delete_many({"user_id": obj_id})
        self.db.job.delete_many({"user_id": obj_id})
        self.db.voice_sample.delete_many({"user_id": obj_id})
        # consent_record stores user_id as string — must match the stored format
        self.db.consent_record.delete_many({"user_id": user_id})
        self.db.activity_log.delete_many({"user_id": user_id})
        self.db.package_request.delete_many({"user_id": obj_id})
        self.db.package_assignment_history.delete_many({"user_id": obj_id})

    def delete_quota(self, user_id: ObjectId) -> None:
        """Delete a quota by user ID."""
        result = self.db.quota.delete_one({"user_id": user_id})
        if result.deleted_count == 0:
            msg = "Quota not found for user"
            raise ValueError(msg)

    # ── User Status & Admin Management ──

    def get_user_status(self, email: str) -> Optional[str]:
        """Get user status by email. Returns None if user not found."""
        user_data = self.db.user.find_one(self._user_email_query(email), {"status": 1})
        if not user_data:
            return None
        return user_data.get("status", USER_STATUS_APPROVED)

    def register_public_user(self, user: User) -> str:
        """Register a new public user with pending status. Returns user_id."""
        if self.db.user.find_one(self._user_email_query(user.email)):
            msg = "User with this email already exists"
            raise ValueError(msg)

        password_str = user.password.get_secret_value()
        hashed_password, salt = self._hash_password(password_str)

        user_data = user.model_dump(by_alias=True)
        user_data["password"] = hashed_password
        user_data["salt"] = salt
        user_data.setdefault("auth_version", 1)
        user_data.setdefault("deletion_pending", False)
        user_data["status"] = "pending"
        user_data["registered_at"] = datetime.now(timezone.utc)
        user_data = self.pii.encrypt_user_document(user_data)

        try:
            result = self.db.user.insert_one(user_data)
        except DuplicateKeyError as exc:
            raise ValueError("User with this email already exists") from exc
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
        user_docs = list(cursor)
        user_ids = [doc["_id"] for doc in user_docs]
        assignments = {
            doc["user_id"]: doc
            for doc in self.db.user_package.find({"user_id": {"$in": user_ids}})
        } if user_ids else {}
        package_ids = {
            assignment["package_id"]
            for assignment in assignments.values()
            if assignment.get("package_id")
        }
        packages = {
            doc["_id"]: doc
            for doc in self.db.package.find({"_id": {"$in": list(package_ids)}})
        } if package_ids else {}

        users = []
        for doc in user_docs:
            doc = self._decrypt_user_document(doc)
            user_obj_id = doc["_id"]
            assignment = assignments.get(user_obj_id)
            package = packages.get(assignment.get("package_id")) if assignment else None

            doc["_id"] = str(user_obj_id)
            doc.pop("email_bidx", None)
            doc.pop("pii_encryption_version", None)
            doc.pop("pii_migrated_at", None)
            for ts_field in ("registered_at", "approved_at"):
                timestamp = doc.get(ts_field)
                if timestamp and hasattr(timestamp, "isoformat"):
                    doc[ts_field] = timestamp.isoformat()

            doc["current_package"] = None
            if assignment and package:
                doc["current_package"] = {
                    "_id": str(package["_id"]),
                    "name": package.get("name"),
                    "tier": package.get("tier", 0),
                    "billing_cycle": package.get("billing_cycle"),
                    "status": assignment.get("status", "active"),
                }
            users.append(doc)
        return users

    def update_user_status(self, user_id: str, status: str, admin_id: str = None) -> bool:
        """Update user status (approve/reject/suspend). Returns True if updated."""
        if status not in VALID_STATUSES:
            msg = f"Invalid status: {status}"
            raise ValueError(msg)
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            return False

        update_fields = {"status": status}
        if status == USER_STATUS_APPROVED and admin_id:
            update_fields["approved_at"] = datetime.now(timezone.utc)
            update_fields["approved_by"] = admin_id

        update = {"$set": update_fields}
        if status != USER_STATUS_APPROVED:
            update["$inc"] = {"auth_version": 1}
        result = self.db.user.update_one(
            {"_id": user_obj_id},
            update,
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

    def seed_package_if_missing(self, pkg_data: dict) -> str:
        """Insert a built-in package once without overwriting administrator edits."""
        seed = dict(pkg_data)
        seed.setdefault("created_at", datetime.now(timezone.utc))
        result = self.db.package.update_one(
            {"name": seed["name"]},
            {"$setOnInsert": seed},
            upsert=True,
        )
        document = self.db.package.find_one({"name": seed["name"]}, {"_id": 1})
        if self.cache and result.upserted_id is not None:
            self.cache.invalidate_packages()
        if not document:
            raise RuntimeError(f"Package seed disappeared: {seed['name']}")
        return str(document["_id"])

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
        obj_id = self._object_id(package_id)
        if not obj_id:
            return False
        result = self.db.package.update_one(
            {"_id": obj_id},
            {"$set": pkg_data},
        )
        if result.matched_count and self.cache:
            self.cache.invalidate_packages()
        return result.matched_count > 0

    def deactivate_package(self, package_id: str) -> bool:
        """Soft-delete a package by marking it inactive."""
        obj_id = self._object_id(package_id)
        if not obj_id:
            return False
        result = self.db.package.update_one(
            {"_id": obj_id},
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
        obj_id = self._object_id(package_id)
        if not obj_id:
            return None
        doc = self.db.package.find_one({"_id": obj_id})
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
        current_month = usage_period(now)
        user_obj_id = self._object_id(user_id)
        package_obj_id = self._object_id(package_id)
        request_obj_id = self._object_id(request_id) if request_id else None
        if not user_obj_id or not package_obj_id or (request_id and not request_obj_id):
            raise ValueError("Invalid user, package, or request ID")
        current = self.db.user_package.find_one({"user_id": user_obj_id})

        if request_obj_id:
            prior_assignment = self.db.package_assignment_history.find_one(
                {"request_id": request_obj_id}, {"to_package_id": 1}
            )
            if prior_assignment:
                if prior_assignment.get("to_package_id") != package_obj_id:
                    raise RuntimeError("Package request id is already bound to a different package")
                if current and current.get("package_id") == package_obj_id:
                    return str(current["_id"])
                raise RuntimeError("Package request was already applied and cannot be replayed")

        active_reservations = [
            reservation
            for reservation in ((current or {}).get("quota_reservations") or {}).values()
            if isinstance(reservation, dict) and reservation.get("state") == "reserved"
        ]
        if reset_usage and active_reservations:
            raise ValueError(
                "ไม่สามารถรีเซ็ตแพ็กเกจขณะมีงานที่จองโควต้าอยู่ กรุณารอให้งานสิ้นสุดหรือยกเลิกงานก่อน"
            )

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
            "assigned_by": assigned_by,
            "updated_at": now,
        }
        if reset_usage or not current:
            doc.update({
                "usage": usage,
                "usage_reset_month": usage_reset_month,
                "usage_epoch": usage_reset_month,
            })

        if current:
            update_query = {"_id": current["_id"]}
            if reset_usage:
                # Compare the exact observed counters and ledger.  A concurrent
                # reservation changes one of them and makes this package reset
                # fail instead of erasing its increment.
                update_query["usage"] = current.get("usage", {})
                if "quota_reservations" in current:
                    update_query["quota_reservations"] = current.get("quota_reservations") or {}
                else:
                    update_query["quota_reservations"] = {"$exists": False}
            result = self.db.user_package.update_one(
                update_query,
                {"$set": doc},
                upsert=False,
            )
            if result.matched_count == 0:
                raise RuntimeError("Package assignment raced with quota usage; retry after active jobs settle")
            user_package_id = str(current["_id"])
        else:
            doc["started_at"] = now
            doc["quota_reservations"] = {}
            result = self.db.user_package.insert_one(doc)
            user_package_id = str(result.inserted_id)

        try:
            self.db.package_assignment_history.insert_one({
                "user_id": user_obj_id,
                "from_package_id": current.get("package_id") if current else None,
                "to_package_id": package_obj_id,
                "changed_by": assigned_by,
                "changed_at": now,
                "source": source,
                "request_id": request_obj_id,
                "reset_usage": reset_usage,
            })
        except DuplicateKeyError:
            if not request_obj_id:
                raise

        if self.cache:
            self.cache.invalidate_user_package(user_id)
            self.cache.invalidate_packages()
        return user_package_id

    def create_package_request(self, user_id: str, requested_package_id: str, note: str = "") -> str:
        """Create a user package change request."""
        user_obj_id = self._object_id(user_id)
        requested_obj_id = self._object_id(requested_package_id)
        if not user_obj_id or not requested_obj_id:
            raise ValueError("ไม่พบแพ็กเกจที่ต้องการ")

        pending = self.db.package_request.find_one({
            "user_id": user_obj_id,
            "status": {"$in": ["pending", "applying"]},
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
            "active": True,
            "note": (note or "").strip()[:1000],
            "admin_note": "",
            "requested_at": datetime.now(timezone.utc),
            "reviewed_at": None,
            "reviewed_by": None,
        }
        try:
            self.db.package_request.insert_one(doc)
        except DuplicateKeyError:
            raise ValueError("คุณมีคำขอเปลี่ยนแพ็กเกจที่รอการพิจารณาอยู่แล้ว")
        return str(doc["_id"])

    def _format_package_request(
        self,
        doc: dict,
        user: Optional[dict] = None,
        current_pkg: Optional[dict] = None,
        requested_pkg: Optional[dict] = None,
    ) -> dict:
        """Serialize package request with already-joined user/package information."""
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

    def _serialize_package_request(self, doc: dict) -> dict:
        """Serialize a single package request with joined user/package information."""
        user = self.db.user.find_one(
            {"_id": doc["user_id"]},
            {"password": 0, "salt": 0},
        )
        user = self._decrypt_user_document(user)
        current_pkg = self.db.package.find_one({"_id": doc.get("current_package_id")}) if doc.get("current_package_id") else None
        requested_pkg = self.db.package.find_one({"_id": doc.get("requested_package_id")}) if doc.get("requested_package_id") else None
        return self._format_package_request(doc, user, current_pkg, requested_pkg)

    def get_package_requests(self, status: str = None, user_id: str = None, limit: int = 100) -> list:
        """List package change requests."""
        query = {}
        if status:
            query["status"] = status
        if user_id:
            user_obj_id = self._object_id(user_id)
            if not user_obj_id:
                return []
            query["user_id"] = user_obj_id

        cursor = (
            self.db.package_request.find(query)
            .sort("requested_at", -1)
            .limit(limit)
        )
        docs = list(cursor)
        if not docs:
            return []

        user_ids = list({doc["user_id"] for doc in docs})
        package_ids = {
            pkg_id
            for doc in docs
            for pkg_id in (doc.get("current_package_id"), doc.get("requested_package_id"))
            if pkg_id
        }
        users = {
            doc["_id"]: self._decrypt_user_document(doc)
            for doc in self.db.user.find({"_id": {"$in": user_ids}}, {"password": 0, "salt": 0})
        }
        packages = {
            doc["_id"]: doc
            for doc in self.db.package.find({"_id": {"$in": list(package_ids)}})
        }
        return [
            self._format_package_request(
                doc,
                users.get(doc["user_id"]),
                packages.get(doc.get("current_package_id")),
                packages.get(doc.get("requested_package_id")),
            )
            for doc in docs
        ]

    def get_package_request_by_id(self, request_id: str) -> Optional[dict]:
        obj_id = self._object_id(request_id)
        if not obj_id:
            return None
        doc = self.db.package_request.find_one({"_id": obj_id})
        if not doc:
            return None
        return self._serialize_package_request(doc)

    def update_package_request_status(
        self,
        request_id: str,
        status: str,
        reviewed_by: str = None,
        admin_note: str = "",
        expected_status: str = None,
    ) -> bool:
        """Update package request review status."""
        obj_id = self._object_id(request_id)
        if not obj_id:
            return False
        query = {"_id": obj_id}
        if expected_status:
            query["status"] = expected_status
        update_fields = {
            "status": status,
            "admin_note": (admin_note or "").strip()[:1000],
            "reviewed_by": reviewed_by,
            "reviewed_at": datetime.now(timezone.utc),
        }
        if status in {"approved", "rejected", "cancelled"}:
            update_fields["active"] = False
        result = self.db.package_request.update_one(
            query,
            {"$set": update_fields},
        )
        return result.matched_count > 0

    def apply_package_request(
        self,
        request_id: str,
        *,
        reviewed_by: str,
        admin_note: str = "",
        reset_usage: bool = True,
    ) -> dict:
        """Claim, apply and approve a package request idempotently."""
        request_obj_id = self._object_id(request_id)
        if not request_obj_id:
            raise ValueError("ไม่พบคำขอ")
        now = datetime.now(timezone.utc)
        token = secrets.token_hex(16)
        request_doc = self.db.package_request.find_one({"_id": request_obj_id})
        if not request_doc:
            raise ValueError("ไม่พบคำขอ")
        if request_doc.get("status") == "approved":
            history = self.db.package_assignment_history.find_one(
                {"request_id": request_obj_id}, {"_id": 1}
            )
            if history:
                return {"applied": True, "idempotent": True}

        claimed = self.db.package_request.find_one_and_update(
            {
                "_id": request_obj_id,
                "$or": [
                    {"status": "pending"},
                    {
                        "status": "applying",
                        "apply_lease_expires_at": {"$lte": now},
                    },
                    {
                        # Recover the legacy broken boundary that marked a
                        # request approved before assigning its package.
                        "status": "approved",
                        "applied_at": {"$exists": False},
                    },
                ],
            },
            {"$set": {
                "status": "applying",
                "apply_token": token,
                "apply_started_at": now,
                "apply_lease_expires_at": now + timedelta(minutes=5),
                "reviewed_by": reviewed_by,
                "admin_note": (admin_note or "").strip()[:1000],
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            latest = self.db.package_request.find_one({"_id": request_obj_id}, {"status": 1}) or {}
            if latest.get("status") == "approved":
                history = self.db.package_assignment_history.find_one(
                    {"request_id": request_obj_id}, {"_id": 1}
                )
                if history:
                    return {"applied": True, "idempotent": True}
            raise RuntimeError("Package request is already being applied or reviewed")

        try:
            current = self.db.user_package.find_one({"user_id": claimed["user_id"]})
            if not current or current.get("package_id") != claimed["requested_package_id"]:
                self.assign_user_package(
                    user_id=str(claimed["user_id"]),
                    package_id=str(claimed["requested_package_id"]),
                    assigned_by=reviewed_by,
                    reset_usage=reset_usage,
                    source="package_request",
                    request_id=request_id,
                )
            elif not self.db.package_assignment_history.find_one({"request_id": request_obj_id}):
                # Recover a crash after the package CAS but before history/request
                # completion without resetting counters a second time.
                try:
                    self.db.package_assignment_history.insert_one({
                        "user_id": claimed["user_id"],
                        "from_package_id": claimed.get("current_package_id"),
                        "to_package_id": claimed["requested_package_id"],
                        "changed_by": reviewed_by,
                        "changed_at": now,
                        "source": "package_request_recovered",
                        "request_id": request_obj_id,
                        "reset_usage": reset_usage,
                    })
                except DuplicateKeyError:
                    pass

            result = self.db.package_request.update_one(
                {"_id": request_obj_id, "status": "applying", "apply_token": token},
                {
                    "$set": {
                        "status": "approved",
                        "active": False,
                        "reviewed_at": datetime.now(timezone.utc),
                        "applied_at": datetime.now(timezone.utc),
                        "last_error": None,
                    },
                    "$unset": {
                        "apply_token": "",
                        "apply_lease_expires_at": "",
                    },
                },
            )
            if result.matched_count == 0:
                raise RuntimeError("Package request approval checkpoint was superseded")
            return {"applied": True, "idempotent": False}
        except Exception as exc:
            self.db.package_request.update_one(
                {"_id": request_obj_id, "status": "applying", "apply_token": token},
                {
                    "$set": {
                        "status": "pending",
                        "active": True,
                        "last_error": f"{type(exc).__name__}: package assignment deferred",
                    },
                    "$unset": {"apply_token": "", "apply_lease_expires_at": ""},
                },
            )
            raise

    def cancel_package_request(self, request_id: str, user_id: str) -> bool:
        """Cancel a pending request by owner."""
        request_obj_id = self._object_id(request_id)
        user_obj_id = self._object_id(user_id)
        if not request_obj_id or not user_obj_id:
            return False
        result = self.db.package_request.update_one(
            {"_id": request_obj_id, "user_id": user_obj_id, "status": "pending"},
            {"$set": {
                "status": "cancelled",
                "active": False,
                "reviewed_at": datetime.now(timezone.utc),
                "reviewed_by": str(user_id),
            }},
        )
        return result.modified_count > 0

    def get_user_package(self, user_id: str) -> Optional[dict]:
        """Get user's current package assignment with package details."""
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            return None
        current_period = usage_period()
        if self.cache:
            cached = self.cache.get_user_package(user_id)
            if cached is not None and cached.get("usage_reset_month") == current_period:
                return cached

        up = self._ensure_usage_period(user_obj_id, current_period)
        if not up:
            return None

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

    def _ensure_usage_period(
        self,
        user_obj_id: ObjectId,
        period: Optional[str] = None,
        *,
        require_active: bool = False,
    ) -> Optional[dict]:
        """Roll counters once with a compare-and-set before any mutation."""
        current_period = period or usage_period()
        now = datetime.now(timezone.utc)
        eligibility: dict = {"user_id": user_obj_id}
        if require_active:
            eligibility.update({
                "status": "active",
                "$or": [
                    {"expires_at": {"$exists": False}},
                    {"expires_at": None},
                    {"expires_at": {"$gt": now}},
                ],
            })
        self.db.user_package.update_one(
            {
                **eligibility,
                "usage_reset_month": {"$ne": current_period},
            },
            {"$set": {
                "usage.files_this_month": 0,
                "usage.ai_summaries_this_month": 0,
                "usage.transcription_minutes_this_month": 0,
                "usage_reset_month": current_period,
                "usage_epoch": current_period,
                "usage_rolled_at": datetime.now(timezone.utc),
            }},
        )
        return self.db.user_package.find_one(eligibility)

    def increment_usage(self, user_id: str, files: int = 0, ai_summaries: int = 0, transcription_minutes: float = 0):
        """Atomically increment usage counters for a user."""
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            return
        self._ensure_usage_period(user_obj_id)
        inc = {}
        if files:
            inc["usage.files_this_month"] = files
        if ai_summaries:
            inc["usage.ai_summaries_this_month"] = ai_summaries
        if transcription_minutes:
            inc["usage.transcription_minutes_this_month"] = transcription_minutes
        if inc:
            self.db.user_package.update_one(
                {"user_id": user_obj_id, "usage_reset_month": usage_period()},
                {"$inc": inc},
            )
            if self.cache:
                self.cache.invalidate_user_package(user_id)

    def reserve_job_quota(
        self,
        user_id: str,
        job_id: str,
        transcription_minutes: float,
    ) -> dict:
        """Reserve quota exactly once for a stable job ID in one Mongo document."""
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            return {"allowed": False, "reason": "ไม่พบผู้ใช้"}
        period = usage_period()
        up = self._ensure_usage_period(user_obj_id, period, require_active=True)
        if not up:
            return {"allowed": False, "reason": "ไม่พบแพ็กเกจ กรุณาติดต่อผู้ดูแลระบบ"}
        pkg = self.db.package.find_one({"_id": up.get("package_id"), "is_active": {"$ne": False}})
        if not pkg:
            return {"allowed": False, "reason": "ไม่พบแพ็กเกจ กรุณาติดต่อผู้ดูแลระบบ"}
        limits = pkg.get("limits", {})
        max_files = limits.get("max_files_per_month", 0)
        max_summaries = limits.get("ai_summary_per_month", 0)
        max_minutes = limits.get("transcription_minutes_per_month", 0)
        minutes = max(float(transcription_minutes or 0), 0)
        reservation_id = str(job_id)
        key = _reservation_key(reservation_id)
        path = f"quota_reservations.{key}"
        existing = ((up.get("quota_reservations") or {}).get(key))
        if existing:
            if existing.get("state") in {"reserved", "consumed"}:
                return {
                    "allowed": True,
                    "reservation_id": reservation_id,
                    "idempotent": True,
                    "period": existing.get("period"),
                }
            return {"allowed": False, "reason": "รายการโควต้านี้ถูกปิดแล้ว"}

        reservation = {
            "reservation_id": reservation_id,
            "period": period,
            "state": "reserved",
            "files": 1,
            "ai_summaries": 1,
            "minutes": minutes,
            "created_at": datetime.now(timezone.utc),
        }
        result = self.db.user_package.update_one(
            {
                "user_id": user_obj_id,
                "status": "active",
                "usage_reset_month": period,
                "$or": [
                    {"expires_at": {"$exists": False}},
                    {"expires_at": None},
                    {"expires_at": {"$gt": datetime.now(timezone.utc)}},
                ],
                path: {"$exists": False},
                "usage.files_this_month": {"$lt": max_files},
                "usage.ai_summaries_this_month": {"$lt": max_summaries},
                "usage.transcription_minutes_this_month": {"$lte": max_minutes - minutes},
            },
            {
                "$set": {path: reservation},
                "$inc": {
                    "usage.files_this_month": 1,
                    "usage.ai_summaries_this_month": 1,
                    "usage.transcription_minutes_this_month": minutes,
                },
            },
        )
        if self.cache:
            self.cache.invalidate_user_package(user_id)
        if result.modified_count == 0:
            concurrent = self.db.user_package.find_one(
                {"user_id": user_obj_id},
                {path: 1},
            )
            concurrent_reservation = (
                ((concurrent or {}).get("quota_reservations") or {}).get(key)
            )
            if concurrent_reservation and concurrent_reservation.get("state") in {"reserved", "consumed"}:
                return {
                    "allowed": True,
                    "reservation_id": reservation_id,
                    "idempotent": True,
                    "period": concurrent_reservation.get("period"),
                }
            return {"allowed": False, "reason": "โควต้าถูกใช้เต็มแล้ว กรุณาตรวจสอบแพ็กเกจอีกครั้ง"}
        return {"allowed": True, "reservation_id": reservation_id, "period": period}

    def reserve_upload_quota(
        self,
        user_id: str,
        transcription_minutes: float,
        reservation_id: Optional[str] = None,
    ) -> dict:
        """Compatibility wrapper; callers should pass a preallocated job ID."""
        stable_id = reservation_id or f"legacy-{secrets.token_hex(12)}"
        return self.reserve_job_quota(user_id, stable_id, transcription_minutes)

    def refund_upload_quota(
        self,
        user_id: str,
        transcription_minutes: float,
        reservation_id: Optional[str] = None,
    ) -> None:
        """Best-effort rollback for a quota reservation when enqueueing fails."""
        if reservation_id:
            if self.settle_quota_reservation(user_id, reservation_id, "refunded"):
                return
        self.increment_usage(
            user_id,
            files=-1,
            ai_summaries=-1,
            transcription_minutes=-max(float(transcription_minutes or 0), 0),
        )

    def settle_quota_reservation(self, user_id: str, reservation_id: str, outcome: str) -> bool:
        """Settle a reservation without requiring a job document (enqueue rollback)."""
        if outcome not in {"consumed", "refunded"}:
            raise ValueError("outcome must be consumed or refunded")
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            return False
        key = _reservation_key(reservation_id)
        path = f"quota_reservations.{key}"
        package = self.db.user_package.find_one({"user_id": user_obj_id})
        reservation = ((package or {}).get("quota_reservations") or {}).get(key)
        if not reservation:
            return False
        target = outcome
        if outcome == "refunded" and reservation.get("period") != usage_period():
            target = "period_closed"
        if reservation.get("state") == target:
            return True
        if reservation.get("state") != "reserved":
            return False
        update: dict = {"$set": {
            f"{path}.state": target,
            f"{path}.settled_at": datetime.now(timezone.utc),
        }}
        query: dict = {"user_id": user_obj_id, f"{path}.state": "reserved"}
        if target == "refunded":
            update["$inc"] = {
                "usage.files_this_month": -int(reservation.get("files") or 0),
                "usage.ai_summaries_this_month": -int(reservation.get("ai_summaries") or 0),
                "usage.transcription_minutes_this_month": -max(float(reservation.get("minutes") or 0), 0),
            }
        result = self.db.user_package.update_one(query, update)
        if self.cache:
            self.cache.invalidate_user_package(user_id)
        return result.modified_count > 0

    def consume_job_quota_once(self, job_id: str) -> bool:
        return settle_job_quota_db(self.db, job_id, "consumed")

    def refund_job_quota_once(self, job_id: str) -> bool:
        """Refund a durable ledger reservation, with a legacy compatibility path."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        existing_job = self.db.job.find_one(
            {"_id": job_obj_id}, {"user_id": 1, "quota_reservation_id": 1}
        )
        if existing_job and existing_job.get("quota_reservation_id"):
            settled = settle_job_quota_db(self.db, job_id, "refunded")
            if settled and self.cache:
                self.cache.invalidate_user_package(str(existing_job["user_id"]))
            return settled
        if existing_job:
            # The reservation ID is the stable job ID.  Recover the link when
            # the API crashed between the single-document reservation and its
            # job checkpoint, then settle through the normal idempotent path.
            key = _reservation_key(job_id)
            package = self.db.user_package.find_one(
                {
                    "user_id": existing_job["user_id"],
                    f"quota_reservations.{key}": {"$exists": True},
                },
                {f"quota_reservations.{key}": 1},
            )
            if package:
                self.db.job.update_one(
                    {"_id": job_obj_id, "quota_reservation_id": {"$in": [None, ""]}},
                    {"$set": {
                        "quota_reserved": True,
                        "quota_reservation_id": str(job_id),
                        "quota_settlement": "reserved",
                    }},
                )
                settled = settle_job_quota_db(self.db, job_id, "refunded")
                if settled and self.cache:
                    self.cache.invalidate_user_package(str(existing_job["user_id"]))
                return settled
        if settle_job_quota_db(self.db, job_id, "refunded"):
            job = self.db.job.find_one({"_id": job_obj_id}, {"user_id": 1})
            if job and self.cache:
                self.cache.invalidate_user_package(str(job["user_id"]))
            return True

        job = self.db.job.find_one_and_update(
            {
                "_id": job_obj_id,
                "quota_reserved": True,
                "quota_refunded": {"$ne": True},
            },
            {"$set": {"quota_refunded": True}},
        )
        if not job:
            return False

        try:
            self.refund_upload_quota(str(job["user_id"]), job.get("quota_minutes", 0))
            return True
        except Exception as exc:
            logger.warning("Could not refund quota for job %s: %s", job_id, exc)
            self.db.job.update_one(
                {"_id": job_obj_id},
                {"$set": {"quota_refunded": False, "quota_refund_error": str(exc)}},
            )
            return False

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
        """Create a voice sample under an atomic per-user capacity counter."""
        from app.models.voice_sample import MAX_VOICE_SAMPLES_PER_USER

        user_id = doc.get("user_id")
        if not isinstance(user_id, ObjectId):
            user_id = self._object_id(user_id)
        if not user_id:
            raise ValueError("User not found")
        actual_count = self.db.voice_sample.count_documents({"user_id": user_id})
        self.db.user.update_one(
            {"_id": user_id, "voice_sample_count": {"$exists": False}},
            {"$set": {"voice_sample_count": actual_count}},
        )
        claimed = self.db.user.find_one_and_update(
            {"_id": user_id, "voice_sample_count": {"$lt": MAX_VOICE_SAMPLES_PER_USER}},
            {"$inc": {"voice_sample_count": 1}},
            projection={"_id": 1},
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            raise ValueError(f"Voice sample limit reached ({MAX_VOICE_SAMPLES_PER_USER})")
        sample = dict(doc)
        sample["user_id"] = user_id
        try:
            result = self.db.voice_sample.insert_one(sample)
            return str(result.inserted_id)
        except Exception:
            self.db.user.update_one(
                {"_id": user_id, "voice_sample_count": {"$gt": 0}},
                {"$inc": {"voice_sample_count": -1}},
            )
            raise

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
        if result.deleted_count:
            self.db.user.update_one(
                {"_id": ObjectId(user_id), "voice_sample_count": {"$gt": 0}},
                {"$inc": {"voice_sample_count": -1}},
            )
        return result.deleted_count > 0

    def count_voice_samples(self, user_id: str) -> int:
        """Count voice samples for a user."""
        return self.db.voice_sample.count_documents({"user_id": ObjectId(user_id)})

    # ── Session / History ──

    def save_session(self, session_doc: dict) -> str:
        """Save a processing session to history."""
        result = self.db.session.insert_one(session_doc)
        return str(result.inserted_id)

    def save_session_for_job(self, job_id: str, session_doc: dict) -> str:
        """Idempotently create one history session for a durable job."""
        document = dict(session_doc)
        document["job_id"] = str(job_id)
        self.db.session.update_one(
            {"job_id": str(job_id)},
            {"$setOnInsert": document},
            upsert=True,
        )
        stored = self.db.session.find_one({"job_id": str(job_id)}, {"_id": 1})
        if not stored:
            raise RuntimeError(f"Session upsert failed for job {job_id}")
        return str(stored["_id"])

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
        session_obj_id = self._object_id(session_id)
        if not session_obj_id:
            return None
        doc = self.db.session.find_one({
            "_id": session_obj_id,
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
        quota_minutes: float = 0.0,
        job_id: Optional[str] = None,
        quota_reservation_id: Optional[str] = None,
        initial_status: str = "queued",
        quota_reserved: Optional[bool] = None,
    ) -> str:
        """Create a new processing job. Returns job ID."""
        if initial_status not in {"initializing", "queued"}:
            raise ValueError("initial_status must be initializing or queued")
        job_obj_id = self._object_id(job_id) if job_id else ObjectId()
        if job_obj_id is None:
            raise ValueError("Invalid job ID")
        now = datetime.now(timezone.utc)
        has_quota = bool(quota_reservation_id) if quota_reserved is None else bool(quota_reserved)
        doc = {
            "_id": job_obj_id,
            "user_id": user_id,
            "workflow_version": 2,
            "status": initial_status,
            "current_step": "initializing" if initial_status == "initializing" else "queued",
            "progress": 0,
            "audio_file": audio_file,
            "audio_path": audio_path,
            "meeting_type_id": meeting_type_id,
            "result": None,
            "result_available": False,
            "session_id": None,
            "error": None,
            "celery_task_id": None,
            "quota_reserved": has_quota,
            "quota_reservation_id": quota_reservation_id,
            "quota_settlement": "reserved" if has_quota else "not_reserved",
            "quota_minutes": max(float(quota_minutes or 0), 0),
            "quota_refunded": False,
            "audio_cleanup_state": "pending",
            "artifact_cleanup_state": "not_created",
            "cancellation_state": "active",
            "upload_state": "pending" if initial_status == "initializing" else "uploaded",
            "publish_state": "pending",
            "initializing_expires_at": now + timedelta(minutes=JOB_INITIALIZATION_TIMEOUT_MINUTES),
            "created_at": now,
            "started_at": None,
            "queue_wait_seconds": None,
            "completed_at": None,
            # Email auto-send fields. email_status: null | queued | sending | sent | failed
            "email_recipient": email_recipient or None,
            "email_status": "queued" if email_recipient else None,
            "email_error": None,
            "email_sent_at": None,
        }
        result = self.db.job.insert_one(doc)
        return str(result.inserted_id)

    @staticmethod
    def new_job_id() -> str:
        """Allocate a stable ID before quota reservation and job insertion."""
        return str(ObjectId())

    def checkpoint_job_quota_reserved(
        self,
        job_id: str,
        reservation_id: str,
        quota_minutes: float,
    ) -> bool:
        """Durably link an exact-once quota reservation to an initializing job."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        result = self.db.job.update_one(
            {
                "_id": job_obj_id,
                "status": "initializing",
                "quota_reserved": {"$ne": True},
            },
            {"$set": {
                "quota_reserved": True,
                "quota_reservation_id": str(reservation_id),
                "quota_minutes": max(float(quota_minutes or 0), 0),
                "quota_settlement": "reserved",
                "quota_reserved_at": datetime.now(timezone.utc),
            }},
        )
        if result.matched_count:
            return True
        existing = self.db.job.find_one(
            {"_id": job_obj_id}, {"quota_reservation_id": 1, "quota_reserved": 1}
        )
        return bool(
            existing
            and existing.get("quota_reserved")
            and str(existing.get("quota_reservation_id")) == str(reservation_id)
        )

    def checkpoint_job_upload_complete(self, job_id: str, audio_object: str) -> bool:
        """Move an initializing job to queued only after its raw object exists."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        result = self.db.job.update_one(
            {
                "_id": job_obj_id,
                "status": {"$in": ["initializing", "queued"]},
                "quota_reserved": True,
            },
            {"$set": {
                "status": "queued",
                "current_step": "queued",
                "audio_path": str(audio_object),
                "audio_object": str(audio_object),
                "upload_state": "uploaded",
                "uploaded_at": datetime.now(timezone.utc),
            }},
        )
        return result.matched_count > 0

    def checkpoint_job_published(self, job_id: str, celery_task_id: str) -> bool:
        """Record broker publication after the task ID is known."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        result = self.db.job.update_one(
            {"_id": job_obj_id, "status": "queued", "upload_state": "uploaded"},
            {"$set": {
                "celery_task_id": str(celery_task_id),
                "publish_state": "published",
                "published_at": datetime.now(timezone.utc),
            }},
        )
        return result.matched_count > 0

    def checkpoint_job_publish_intent(
        self,
        job_id: str,
        celery_task_id: str,
        task_kwargs: dict,
    ) -> bool:
        """Persist a replayable transcription request before broker publish.

        The API must call this before ``apply_async``.  Maintenance can then
        safely publish the same task ID after an API crash without guessing
        request arguments or refunding an already uploaded job.
        """
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        allowed_keys = {
            "job_id",
            "audio_object",
            "original_filename",
            "meeting_type_id",
            "user_id",
            "email_recipient",
            "custom_prompt",
            "use_voice_matching",
        }
        replay = {key: value for key, value in dict(task_kwargs or {}).items() if key in allowed_keys}
        if "voice_samples" in dict(task_kwargs or {}) and "use_voice_matching" not in replay:
            # Store only the intent, not large biometric embeddings.  Recovery
            # reloads the current samples from Mongo immediately before replay.
            replay["use_voice_matching"] = bool(task_kwargs.get("voice_samples"))
        replay["job_id"] = str(job_id)
        result = self.db.job.update_one(
            {
                "_id": job_obj_id,
                "status": "queued",
                "upload_state": "uploaded",
                "publish_state": {"$in": ["pending", "publishing"]},
            },
            {"$set": {
                "celery_task_id": str(celery_task_id),
                "transcription_task_kwargs": replay,
                "publish_state": "publishing",
                "publish_intent_at": datetime.now(timezone.utc),
            }},
        )
        return result.matched_count > 0

    def get_job(self, job_id: str, user_id: ObjectId) -> Optional[dict]:
        """Get job status (only if owned by user). Returns lightweight status."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return None
        doc = self.db.job.find_one(
            {"_id": job_obj_id, "user_id": user_id},
            # Exclude heavy result data for status polling
            {"result": 0},
        )
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        for ts_field in (
            "created_at",
            "started_at",
            "completed_at",
            "email_sent_at",
            "summary_started_at",
            "summary_finished_at",
        ):
            if doc.get(ts_field):
                if hasattr(doc[ts_field], "isoformat"):
                    doc[ts_field] = doc[ts_field].isoformat()
        return doc

    def get_job_result(self, job_id: str, user_id: ObjectId) -> Optional[dict]:
        """Get a full result for any terminal job that preserved an artifact."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return None
        doc = self.db.job.find_one({
            "_id": job_obj_id,
            "user_id": user_id,
            "result": {"$ne": None},
            "$or": [
                {"result_available": True},
                {"status": "completed"},
            ],
        })
        if not doc:
            return None
        doc["_id"] = str(doc["_id"])
        doc["user_id"] = str(doc["user_id"])
        if doc.get("created_at"):
            doc["created_at"] = doc["created_at"].isoformat()
        if doc.get("started_at"):
            doc["started_at"] = doc["started_at"].isoformat()
        if doc.get("completed_at"):
            doc["completed_at"] = doc["completed_at"].isoformat()
        for ts_field in ("summary_started_at", "summary_finished_at"):
            if doc.get(ts_field) and hasattr(doc[ts_field], "isoformat"):
                doc[ts_field] = doc[ts_field].isoformat()
        return doc

    # ── Password Reset & Profile Update ──

    def create_password_reset_token(self, user_id: str, token: str, expires_at: datetime) -> None:
        """Store a one-way hash of a reset token, never the credential itself."""
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.password_reset.delete_many({"user_id": user_id})

        doc = {
            "user_id": user_id,
            "token_hash": token_hash,
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
        }
        self.db.password_reset.insert_one(doc)

    def get_password_reset_token(self, token: str) -> Optional[dict]:
        """Retrieve a password reset token if it hasn't expired."""
        now = datetime.now(timezone.utc)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        # The plaintext branch is temporary compatibility for unexpired tokens
        # created before token hashing was introduced.
        doc = self.db.password_reset.find_one({
            "$or": [{"token_hash": token_hash}, {"token": token}],
            "expires_at": {"$gt": now},
        })
        return doc

    def consume_password_reset_token(self, token: str) -> Optional[dict]:
        """Atomically consume one unexpired reset token and return its document."""
        now = datetime.now(timezone.utc)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return self.db.password_reset.find_one_and_delete({
            "$or": [{"token_hash": token_hash}, {"token": token}],
            "expires_at": {"$gt": now},
        })

    def delete_password_reset_token(self, token: str) -> None:
        """Delete a password reset token after use."""
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        self.db.password_reset.delete_one({
            "$or": [{"token_hash": token_hash}, {"token": token}],
        })

    def delete_password_reset_tokens_for_user(self, user_id: str) -> int:
        """Invalidate every remaining reset credential for one account."""
        result = self.db.password_reset.delete_many({"user_id": str(user_id)})
        return int(result.deleted_count)

    def update_user_password(self, user_id: str, new_password: str) -> None:
        """Update a password and revoke every previously issued JWT."""
        hashed_password, salt = self._hash_password(new_password)
        obj_id = self._object_id(user_id)
        if not obj_id:
            raise ValueError("User not found")
        result = self.db.user.update_one(
            {"_id": obj_id},
            {
                "$set": {
                    "password": hashed_password,
                    "salt": salt,
                    "password_changed_at": datetime.now(timezone.utc),
                },
                "$inc": {"auth_version": 1},
            },
        )
        if result.matched_count == 0:
            raise ValueError("User not found")

    def get_auth_version(self, user_id: str) -> Optional[int]:
        """Return the JWT revocation version, initializing legacy users to 1."""
        obj_id = self._object_id(user_id)
        if not obj_id:
            return None
        document = self.db.user.find_one_and_update(
            {"_id": obj_id, "auth_version": {"$exists": False}},
            {"$set": {"auth_version": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = self.db.user.find_one({"_id": obj_id}, {"auth_version": 1})
        if not document:
            return None
        return int(document.get("auth_version", 1))

    def increment_auth_version(self, user_id: str) -> Optional[int]:
        """Revoke existing JWTs and return the new version."""
        obj_id = self._object_id(user_id)
        if not obj_id:
            return None
        document = self.db.user.find_one_and_update(
            {"_id": obj_id},
            {"$inc": {"auth_version": 1}},
            projection={"auth_version": 1},
            return_document=ReturnDocument.AFTER,
        )
        return int(document.get("auth_version", 1)) if document else None

    # ── Durable account deletion manifests ──

    def create_deletion_manifest(self, user_id: str, requested_by: str) -> dict:
        """Create/reuse a non-PII, idempotent account-deletion manifest."""
        user_obj_id = self._object_id(user_id)
        if not user_obj_id:
            raise ValueError("User not found")
        now = datetime.now(timezone.utc)
        deletion_id = secrets.token_hex(16)
        created = False
        try:
            result = self.db.data_deletion.update_one(
                {"user_id": user_obj_id, "active": True},
                {"$setOnInsert": {
                    "deletion_id": deletion_id,
                    "user_id": user_obj_id,
                    "requested_by": str(requested_by),
                    "status": "pending",
                    "active": True,
                    "phase": "cancel_jobs",
                    "attempts": 0,
                    "last_error": None,
                    "created_at": now,
                    "updated_at": now,
                }},
                upsert=True,
            )
            created = result.upserted_id is not None
        except DuplicateKeyError:
            # A concurrent request won the unique active-per-user insert.
            created = False
        user_update: dict = {
            "$set": {"deletion_pending": True, "deletion_requested_at": now},
        }
        if created:
            user_update["$inc"] = {"auth_version": 1}
        self.db.user.update_one({"_id": user_obj_id}, user_update)
        document = self.db.data_deletion.find_one(
            {"user_id": user_obj_id, "active": True}
        )
        return self._serialize_deletion_manifest(document)

    @staticmethod
    def _serialize_deletion_manifest(document: Optional[dict]) -> Optional[dict]:
        if not document:
            return None
        public_fields = {
            "deletion_id",
            "status",
            "phase",
            "attempts",
            "last_error",
            "created_at",
            "updated_at",
            "completed_at",
            "reconcile_after",
        }
        result = {key: value for key, value in document.items() if key in public_fields}
        for field in ("created_at", "updated_at", "completed_at", "reconcile_after"):
            if result.get(field) and hasattr(result[field], "isoformat"):
                result[field] = result[field].isoformat()
        return result

    def get_deletion_manifest(self, deletion_id: str) -> Optional[dict]:
        return self._serialize_deletion_manifest(
            self.db.data_deletion.find_one({"deletion_id": deletion_id})
        )

    def update_deletion_manifest(
        self,
        deletion_id: str,
        *,
        status: Optional[str] = None,
        phase: Optional[str] = None,
        error: Optional[str] = None,
    ) -> bool:
        fields = {"updated_at": datetime.now(timezone.utc)}
        if status is not None:
            fields["status"] = status
            if status == "completed":
                fields["active"] = False
                fields["completed_at"] = fields["updated_at"]
        if phase is not None:
            fields["phase"] = phase
        if error is not None:
            fields["last_error"] = "DeletionStepError: deletion step failed"
        result = self.db.data_deletion.update_one(
            {"deletion_id": deletion_id},
            {"$set": fields, "$inc": {"attempts": 1}},
        )
        return result.matched_count > 0

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

    @staticmethod
    def _missing_user_identity(user_id) -> dict:
        user_id = str(user_id or "")
        return {
            "id": user_id,
            "display_name": "บัญชีที่ถูกลบ" if user_id else "ระบบ",
            "username": "",
            "email": "",
            "organization": "",
            "missing": bool(user_id),
        }

    def _get_user_identity_map(self, user_ids) -> dict[str, dict]:
        """Resolve user IDs to compact, decrypted identities for admin monitoring."""
        object_ids = []
        for user_id in {str(value) for value in user_ids if value}:
            object_id = self._object_id(user_id)
            if object_id is not None:
                object_ids.append(object_id)

        identities = {}
        if not object_ids:
            return identities

        projection = {
            "username": 1,
            "email": 1,
            "first_name": 1,
            "last_name": 1,
            "organization": 1,
        }
        for encrypted_doc in self.db.user.find({"_id": {"$in": object_ids}}, projection):
            user_id = str(encrypted_doc["_id"])
            try:
                doc = self._decrypt_user_document(encrypted_doc)
            except Exception as exc:
                logger.warning("Could not resolve monitoring identity for user %s: %s", user_id, exc)
                continue

            full_name = " ".join(
                value.strip()
                for value in (doc.get("first_name"), doc.get("last_name"))
                if isinstance(value, str) and value.strip()
            )
            username = doc.get("username") if isinstance(doc.get("username"), str) else ""
            email = doc.get("email") if isinstance(doc.get("email"), str) else ""
            organization = doc.get("organization") if isinstance(doc.get("organization"), str) else ""
            identities[user_id] = {
                "id": user_id,
                "display_name": full_name or username or email or "ไม่ระบุชื่อผู้ใช้",
                "username": username,
                "email": email,
                "organization": organization,
                "missing": False,
            }
        return identities

    def get_activity_logs(self, user_id: str = None, action: str = None,
                          limit: int = 100, offset: int = 0,
                          sort_order: str = "desc") -> list:
        """Get activity logs with optional filters and timestamp ordering."""
        query = {}
        if user_id:
            query["user_id"] = user_id
        if action:
            query["action"] = action

        cursor = (
            self.db.activity_log.find(query)
            .sort("timestamp", 1 if sort_order == "asc" else -1)
            .skip(offset)
            .limit(limit)
        )
        logs = list(cursor)
        identities = self._get_user_identity_map(doc.get("user_id") for doc in logs)
        for doc in logs:
            doc["_id"] = str(doc["_id"])
            user_id_value = str(doc.get("user_id") or "")
            doc["user_id"] = user_id_value
            doc["user"] = identities.get(
                user_id_value,
                self._missing_user_identity(user_id_value),
            )
            if doc.get("timestamp"):
                doc["timestamp"] = doc["timestamp"].isoformat()
        return logs

    def get_activity_filter_users(self) -> list:
        """Return users represented in activity logs for the admin filter."""
        user_ids = list(dict.fromkeys(
            str(user_id)
            for user_id in self.db.activity_log.distinct("user_id")
            if user_id
        ))
        identities = self._get_user_identity_map(user_ids)
        users = [
            identities.get(user_id, self._missing_user_identity(user_id))
            for user_id in user_ids
        ]
        return sorted(
            users,
            key=lambda user: (user["display_name"].casefold(), user["email"].casefold()),
        )

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
                     consented: bool, ip_address: str = None,
                     policy_hash: str = "") -> None:
        """Upsert current consent and append an immutable audit event."""
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
        # The append-only event is the compliance source of truth.  Write it
        # before refreshing the mutable current-state projection so a partial
        # failure can never silently lose evidence of the user's action.
        self.append_consent_event(
            user_id=user_id,
            consent_type=consent_type,
            version=version,
            consented=consented,
            ip_address=ip_address,
            policy_hash=policy_hash,
            created_at=now,
        )
        self.db.consent_record.update_one(
            {"user_id": user_id, "consent_type": consent_type},
            {"$set": doc},
            upsert=True,
        )

    def append_consent_event(
        self,
        *,
        user_id: str,
        consent_type: str,
        version: str,
        consented: bool,
        ip_address: Optional[str],
        policy_hash: str,
        created_at: Optional[datetime] = None,
    ) -> str:
        """Append a 365-day pseudonymous consent evidence record."""
        now = created_at or datetime.now(timezone.utc)
        audit_key = (
            os.getenv("CONSENT_AUDIT_KEY")
            or os.getenv("JWT_SECRET_KEY")
            or "timsumv3-development-consent-key"
        ).encode("utf-8")
        subject_id = hmac.new(audit_key, str(user_id).encode("utf-8"), hashlib.sha256).hexdigest()
        ip_hash = (
            hmac.new(audit_key, f"ip:{ip_address}".encode("utf-8"), hashlib.sha256).hexdigest()
            if ip_address else None
        )
        effective_policy_hash = policy_hash or hashlib.sha256(
            f"{consent_type}:{version}".encode("utf-8")
        ).hexdigest()
        result = self.db.consent_event.insert_one({
            "subject_id": subject_id,
            "consent_type": str(consent_type),
            "version": str(version),
            "consented": bool(consented),
            "policy_hash": effective_policy_hash,
            # Keep audit evidence pseudonymous and append-only.  The mutable
            # consent projection may retain the source IP until account
            # deletion, but immutable compliance events never contain it.
            "ip_hash": ip_hash,
            "created_at": now,
            "expires_at": now + timedelta(days=CONSENT_AUDIT_RETENTION_DAYS),
        })
        return str(result.inserted_id)

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
        counts = {
            "queued": 0,
            "processing": 0,
            "completed": 0,
            "partially_completed": 0,
            "failed": 0,
            "cancelled": 0,
        }
        for doc in self.db.job.aggregate(pipeline):
            if doc["_id"] in counts:
                counts[doc["_id"]] = doc["count"]
        counts["total"] = sum(counts.values())
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        counts["completed_today"] = self.db.job.count_documents({
            "status": "completed",
            "completed_at": {"$gte": today_start},
        })
        return counts

    def _job_monitor_query(self, status: str = None, user_id: str = None) -> dict:
        query = {}
        if status:
            query["status"] = status
        if user_id:
            user_object_id = self._object_id(user_id)
            query["user_id"] = (
                {"$in": [user_object_id, str(user_id)]}
                if user_object_id is not None
                else str(user_id)
            )
        return query

    def get_all_jobs(
        self,
        status: str = None,
        user_id: str = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list:
        """List all jobs for admin view, newest first, excluding heavy result payloads."""
        query = self._job_monitor_query(status=status, user_id=user_id)
        cursor = (
            self.db.job.find(query, {"result": 0})
            .sort("created_at", -1)
            .skip(offset)
            .limit(limit)
        )
        jobs = list(cursor)
        identities = self._get_user_identity_map(doc.get("user_id") for doc in jobs)
        for doc in jobs:
            doc["_id"] = str(doc["_id"])
            user_id_value = str(doc.get("user_id") or "")
            doc["user_id"] = user_id_value
            doc["user"] = identities.get(
                user_id_value,
                self._missing_user_identity(user_id_value),
            )
            for ts_field in (
                "created_at",
                "started_at",
                "completed_at",
                "email_sent_at",
                "summary_started_at",
                "summary_finished_at",
            ):
                if doc.get(ts_field):
                    if hasattr(doc[ts_field], "isoformat"):
                        doc[ts_field] = doc[ts_field].isoformat()
        return jobs

    def count_jobs(self, status: str = None, user_id: str = None) -> int:
        """Count jobs matching the admin monitoring filters."""
        return self.db.job.count_documents(
            self._job_monitor_query(status=status, user_id=user_id)
        )

    def get_job_filter_users(self) -> list:
        """Return users that own at least one job, sorted for the monitoring filter."""
        user_ids = list(dict.fromkeys(
            str(user_id)
            for user_id in self.db.job.distinct("user_id")
            if user_id
        ))
        identities = self._get_user_identity_map(user_ids)
        users = [
            identities.get(user_id, self._missing_user_identity(user_id))
            for user_id in user_ids
        ]
        return sorted(
            users,
            key=lambda user: (user["display_name"].casefold(), user["email"].casefold()),
        )

    # ── LLM Config ──
    def get_all_llm_configs(self) -> list:
        """Get all LLM configurations."""
        cursor = self.db.llm_config.find().sort("name", 1)
        configs = []
        for doc in cursor:
            doc["_id"] = str(doc["_id"])
            updated_at = doc.get("updated_at")
            if updated_at and hasattr(updated_at, "isoformat"):
                doc["updated_at"] = updated_at.isoformat()
            configs.append(doc)
        return configs

    def get_llm_config(self, name: str = "default_fallback") -> Optional[dict]:
        """Get LLM configuration."""
        doc = self.db.llm_config.find_one({"name": name})
        if doc:
            doc["_id"] = str(doc["_id"])
            updated_at = doc.get("updated_at")
            if updated_at and hasattr(updated_at, "isoformat"):
                doc["updated_at"] = updated_at.isoformat()
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
        """Idempotently mark a job cancelled before cooperative task revocation."""
        job_obj_id = self._object_id(job_id)
        if not job_obj_id:
            return False
        now = datetime.now(timezone.utc)
        result = self.db.job.update_one(
            {"_id": job_obj_id, "status": {"$in": ["queued", "processing"]}},
            {"$set": {
                "status": "cancelled",
                "current_step": "cancelled",
                "cancellation_state": "requested",
                "cancellation_cleanup_status": "pending",
                "email_status": "cancelled",
                "result_available": False,
                "cancelled_at": now,
                "completed_at": now,
                "error": "Cancelled by admin",
            }},
        )
        if result.modified_count > 0:
            return True
        return self.db.job.count_documents({"_id": job_obj_id, "status": "cancelled"}) == 1

    def update_user_profile(self, user_id: str, profile_data: dict) -> None:
        """Update a user's profile information."""
        allowed_fields = ["first_name", "last_name", "phone", "organization"]
        update_data = {k: v for k, v in profile_data.items() if k in allowed_fields}
        
        if not update_data:
            return

        update_data = self.pii.encrypt_user_fields(user_id, update_data)
        result = self.db.user.update_one(
            {"_id": ObjectId(user_id)},
            {"$set": update_data},
        )
        if result.matched_count == 0:
            raise ValueError("User not found")

    # ── Meeting Templates ──

    def get_meeting_template(self, meeting_type_id: int) -> Optional[dict]:
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

    def update_meeting_template(self, meeting_type_id: int, data: dict) -> bool:
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
            
        return result.matched_count > 0 or result.upserted_id is not None
