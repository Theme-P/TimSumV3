// MongoDB initialization script — runs on first container startup only.
// Creates indexes for performance and TTL for auto-cleanup.

db = db.getSiblingDB('timsumv3');

// User collection — unique email
db.user.createIndex({ "email": 1 }, { unique: true });

// Job collection — query by user + status, auto-delete after 30 days
db.job.createIndex({ "user_id": 1, "created_at": -1 });
db.job.createIndex({ "status": 1 });
db.job.createIndex({ "created_at": 1 }, { expireAfterSeconds: 2592000 });

// Session collection — query by user
db.session.createIndex({ "user_id": 1, "created_at": -1 });

// Quota collection — lookup by user
db.quota.createIndex({ "user_id": 1 }, { unique: true });

print("✅ TimSumV3 MongoDB indexes created.");
