"""
Script to create admin and superadmin users in MongoDB.
Run inside the backend container:
  docker exec timsumv3-backend python scripts/create_admin.py
"""
import os
import sys
sys.path.insert(0, '/app')

from dotenv import load_dotenv
load_dotenv()

from bson import ObjectId
from app.services.mongo import MongoService
from app.models.user import User, Quota


def _create_user(mongo, username, email, password, role):
    """Create a user if not already exists."""
    existing = mongo.get_user_by_email(email)
    if existing:
        print(f"  [{role}] already exists: {email}")
        return

    user = User(
        _id=ObjectId(),
        username=username,
        email=email,
        password=password,
        role=role,
        status="approved",
    )
    quota = Quota(
        _id=ObjectId(),
        user_id=user.id,
        value1=100, value2=100, value3=100, value4=100,
    )

    mongo.create_user(user)
    mongo.create_quota(quota)
    print(f"  [{role}] created: {email}")


def main():
    mongo_uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017")
    mongo_db = os.getenv("MONGO_DB_NAME", "timsumv3")

    print(f"Connecting to MongoDB at {mongo_uri}, db={mongo_db}")
    mongo = MongoService(uri=mongo_uri, db_name=mongo_db)

    # ── SuperAdmin ──
    sa_username = os.getenv("SUPERADMIN_USERNAME", "superadmin")
    sa_email = os.getenv("SUPERADMIN_EMAIL", "superadmin@timsumv3.local")
    sa_password = os.getenv("SUPERADMIN_PASS", "TimSum@SuperAdmin2026")
    _create_user(mongo, sa_username, sa_email, sa_password, "superadmin")

    # ── Admin ──
    admin_username = os.getenv("ADMIN_USERNAME", "admin")
    admin_email = os.getenv("ADMIN_EMAIL", "admin@timsumv3.local")
    admin_password = os.getenv("ADMIN_PASS", "TimSum@Admin2026")
    _create_user(mongo, admin_username, admin_email, admin_password, "admin")

    print("Done.")


if __name__ == "__main__":
    main()
