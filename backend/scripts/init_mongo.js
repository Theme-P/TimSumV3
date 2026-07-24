// MongoDB initialization script — runs on first container startup only.
// Creates indexes for performance and TTL for auto-cleanup.

db = db.getSiblingDB('timsumv3');

// User collection — plaintext index supports pre-migration records only.
// Encrypted records use a keyed blind index for equality lookups.
db.user.createIndex(
  { "email": 1 },
  {
    unique: true,
    name: "email_legacy_unique",
    partialFilterExpression: { "email": { $type: "string" } }
  }
);
db.user.createIndex(
  { "email_bidx": 1 },
  {
    unique: true,
    name: "email_bidx_unique",
    partialFilterExpression: { "email_bidx": { $type: "string" } }
  }
);

// Job collection — retain terminal diagnostics for 30 days. Running jobs do
// not expire merely because they have spent a long time in a queue.
db.job.createIndex({ "user_id": 1, "created_at": -1 });
db.job.createIndex({ "status": 1 });
db.job.createIndex({ "completed_at": 1 }, { expireAfterSeconds: 2592000 });

// History is retained for the published 365-day policy.
db.session.createIndex({ "user_id": 1, "created_at": -1 });
db.session.createIndex({ "created_at": 1 }, { expireAfterSeconds: 31536000 });
db.session.createIndex(
  { "job_id": 1 },
  {
    unique: true,
    name: "session_job_id_unique",
    partialFilterExpression: { "job_id": { $type: "string" } }
  }
);

// Credential and workflow records use explicit absolute expiry timestamps.
db.password_reset.createIndex({ "expires_at": 1 }, { expireAfterSeconds: 0 });
db.summary_state.createIndex({ "job_id": 1 }, { unique: true });
db.summary_state.createIndex({ "expires_at": 1 }, { expireAfterSeconds: 0 });
db.email_outbox.createIndex({ "event_key": 1 }, { unique: true });
db.email_outbox.createIndex({ "expires_at": 1 }, { expireAfterSeconds: 0 });
db.consent_event.createIndex({ "expires_at": 1 }, { expireAfterSeconds: 0 });

// Activity log — auto-delete after 90 days
db.activity_log.createIndex({ "timestamp": 1 }, { expireAfterSeconds: 7776000 });  // 90 days

// Quota collection — lookup by user
db.quota.createIndex({ "user_id": 1 }, { unique: true });

print("✅ TimSumV3 MongoDB indexes created.");
