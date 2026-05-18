"""
Script to create an admin user in MongoDB.
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

# Admin credentials — override via env vars if needed
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@timsumv3.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASS", "TimSum@Admin2026")

def main():
    mongo_uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017")
    mongo_db = os.getenv("MONGO_DB_NAME", "timsumv3")
    
    print(f"Connecting to MongoDB at {mongo_uri}, db={mongo_db}")
    mongo = MongoService(uri=mongo_uri, db_name=mongo_db)
    
    # Check if admin already exists
    existing = mongo.get_user_by_email(ADMIN_EMAIL)
    if existing:
        print(f"Admin user already exists: {ADMIN_EMAIL}")
        return
    
    # Create admin user
    admin_user = User(
        _id=ObjectId(),
        username=ADMIN_USERNAME,
        email=ADMIN_EMAIL,
        password=ADMIN_PASSWORD,
        role="admin",
    )
    
    admin_quota = Quota(
        _id=ObjectId(),
        user_id=admin_user.id,
        value1=100,
        value2=100,
        value3=100,
        value4=100,
    )
    
    mongo.create_user(admin_user)
    mongo.create_quota(admin_quota)
    
    print(f"✅ Admin user created successfully!")
    print(f"   Email:    {ADMIN_EMAIL}")
    print(f"   Password: {ADMIN_PASSWORD}")
    print(f"   Role:     admin")

if __name__ == "__main__":
    main()
