// MongoDB initialization script — runs on first container startup only.
// Creates indexes for performance and TTL for auto-cleanup.

db = db.getSiblingDB('timsumv3');

// User collection — unique email
db.user.createIndex({ "email": 1 }, { unique: true });

// Job collection — query by user + status, auto-delete after 30 days
db.job.createIndex({ "user_id": 1, "created_at": -1 });
db.job.createIndex({ "status": 1 });
db.job.createIndex({ "created_at": 1 }, { expireAfterSeconds: 2592000 });  // 30 days

// Session collection — query by user, auto-delete after 90 days
db.session.createIndex({ "user_id": 1, "created_at": -1 });
db.session.createIndex({ "created_at": 1 }, { expireAfterSeconds: 7776000 });  // 90 days

// Password reset tokens — auto-delete after 7 days
db.password_reset.createIndex({ "created_at": 1 }, { expireAfterSeconds: 604800 });  // 7 days

// Activity log — auto-delete after 90 days
db.activity_log.createIndex({ "timestamp": 1 }, { expireAfterSeconds: 7776000 });  // 90 days

// Quota collection — lookup by user
db.quota.createIndex({ "user_id": 1 }, { unique: true });

print("✅ TimSumV3 MongoDB indexes created.");
