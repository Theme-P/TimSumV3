"""Preflight and apply additive durable-workflow schema/index migration.

Run inside the backend container while uploads and workers are drained:

  python scripts/migrate_workflow_v2.py --check
  python scripts/migrate_workflow_v2.py --apply
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from bson import ObjectId
from dotenv import load_dotenv
from pymongo import MongoClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.mongo import (
    EMAIL_OUTBOX_RETENTION_DAYS,
    JOB_RETENTION_SECONDS,
    SESSION_RETENTION_SECONDS,
    _drop_ttl_indexes,
    _ensure_ttl_index,
    _reservation_key,
    usage_period,
)
from app.models.voice_sample import MAX_VOICE_SAMPLES_PER_USER


TERMINAL = {"completed", "partially_completed", "failed", "cancelled"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate TimSumV3 workflow schema to v2")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true", help="validate only; write nothing")
    mode.add_argument("--apply", action="store_true", help="backfill fields and reconcile indexes")
    return parser.parse_args()


def preflight(db) -> dict:
    duplicate_sessions = list(db.session.aggregate([
        {"$match": {"job_id": {"$type": "string"}}},
        {"$group": {"_id": "$job_id", "count": {"$sum": 1}, "ids": {"$push": "$_id"}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$limit": 20},
    ]))
    duplicate_summary_states = list(db.summary_state.aggregate([
        {"$group": {"_id": "$job_id", "count": {"$sum": 1}, "ids": {"$push": "$_id"}}},
        {"$match": {"_id": {"$ne": None}, "count": {"$gt": 1}}},
        {"$limit": 20},
    ]))
    session_owners: dict[ObjectId, ObjectId] = {}
    for session in db.session.find({}, {"user_id": 1}):
        session_owners[session["_id"]] = session.get("user_id")

    mapped: dict[ObjectId, ObjectId] = {}
    ownership_mismatches = []
    duplicate_mappings = []
    backfillable = 0
    for job in db.job.find({"session_id": {"$ne": None}}, {"user_id": 1, "session_id": 1}):
        try:
            session_id = ObjectId(str(job["session_id"]))
        except Exception:
            ownership_mismatches.append((str(job["_id"]), str(job.get("session_id")), "invalid_session_id"))
            continue
        owner = session_owners.get(session_id)
        if owner is None or owner != job.get("user_id"):
            ownership_mismatches.append((str(job["_id"]), str(session_id), "owner_mismatch_or_missing"))
            continue
        previous_job = mapped.get(session_id)
        if previous_job and previous_job != job["_id"]:
            duplicate_mappings.append((str(session_id), str(previous_job), str(job["_id"])))
            continue
        mapped[session_id] = job["_id"]
        backfillable += 1

    unmapped_sessions = [
        str(session["_id"])
        for session in db.session.find(
            {"$or": [{"job_id": {"$exists": False}}, {"job_id": None}]}, {"_id": 1}
        )
        if session["_id"] not in mapped
    ][:20]

    active_package_request_duplicates = list(db.package_request.aggregate([
        {"$match": {"status": {"$in": ["pending", "applying"]}}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "ids": {"$push": "$_id"}}},
        {"$match": {"count": {"$gt": 1}}},
        {"$limit": 20},
    ]))
    voice_counts = {
        row["_id"]: int(row["count"])
        for row in db.voice_sample.aggregate([
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        ])
    }
    voice_owner_ids = set(voice_counts)
    existing_voice_owners = {
        user["_id"] for user in db.user.find({"_id": {"$in": list(voice_owner_ids)}}, {"_id": 1})
    } if voice_owner_ids else set()
    voice_ownership_mismatches = [
        str(user_id) for user_id in voice_owner_ids - existing_voice_owners
    ][:20]
    voice_over_limit = [
        {"user_id": str(user_id), "count": count}
        for user_id, count in voice_counts.items()
        if count > MAX_VOICE_SAMPLES_PER_USER
    ][:20]
    voice_counter_mismatches = 0
    for user in db.user.find({}, {"voice_sample_count": 1}):
        if int(user.get("voice_sample_count") or 0) != voice_counts.get(user["_id"], 0):
            voice_counter_mismatches += 1

    report = {
        "duplicate_session_job_ids": duplicate_sessions,
        "duplicate_summary_state_job_ids": duplicate_summary_states,
        "ownership_mismatches": ownership_mismatches[:20],
        "duplicate_job_session_mappings": duplicate_mappings[:20],
        "unmapped_legacy_sessions": unmapped_sessions,
        "backfillable_sessions": backfillable,
        "jobs_needing_v2": db.job.count_documents({"workflow_version": {"$ne": 2}}),
        "summary_states_needing_expiry": db.summary_state.count_documents({"expires_at": {"$exists": False}}),
        "packages_needing_epoch": db.user_package.count_documents({"usage_epoch": {"$exists": False}}),
        "duplicate_active_package_requests": active_package_request_duplicates,
        "voice_ownership_mismatches": voice_ownership_mismatches,
        "voice_over_limit": voice_over_limit,
        "voice_counter_mismatches": voice_counter_mismatches,
    }
    if (
        duplicate_sessions
        or duplicate_summary_states
        or ownership_mismatches
        or duplicate_mappings
        or active_package_request_duplicates
        or voice_ownership_mismatches
        or voice_over_limit
    ):
        raise RuntimeError(f"Preflight found data conflicts: {report}")
    return report


def apply_backfill(db) -> dict:
    now = datetime.now(timezone.utc)
    counters = {
        "sessions": 0,
        "jobs": 0,
        "states": 0,
        "packages": 0,
        "reservations": 0,
        "auth_versions": 0,
        "outbox": 0,
        "voice_counters": 0,
    }

    marker_id = "workflow_v2_forced_logout"
    if not db.schema_migration.find_one({"_id": marker_id}):
        result = db.user.update_many({}, {"$inc": {"auth_version": 1}})
        counters["auth_versions"] = result.modified_count
        db.schema_migration.insert_one({
            "_id": marker_id,
            "applied_at": now,
            "updated_users": result.modified_count,
        })

    for job in db.job.find({"session_id": {"$ne": None}}, {"session_id": 1}):
        session_id = ObjectId(str(job["session_id"]))
        result = db.session.update_one(
            {"_id": session_id, "$or": [{"job_id": {"$exists": False}}, {"job_id": str(job["_id"])}]},
            {"$set": {"job_id": str(job["_id"])}},
        )
        counters["sessions"] += result.modified_count

    # Sessions created before durable jobs existed still receive a stable,
    # collision-free key.  They remain valid history; no legacy data is
    # deleted merely because there is no job document to map back to.
    for session in db.session.find(
        {"$or": [{"job_id": {"$exists": False}}, {"job_id": None}]},
        {"_id": 1},
    ):
        result = db.session.update_one(
            {"_id": session["_id"], "$or": [{"job_id": {"$exists": False}}, {"job_id": None}]},
            {"$set": {"job_id": f"legacy-session-{session['_id']}"}},
        )
        counters["sessions"] += result.modified_count

    result = db.job.update_many(
        {"workflow_version": {"$ne": 2}},
        {"$set": {
            "workflow_version": 2,
            "audio_cleanup_state": "pending",
            "artifact_cleanup_state": "not_created",
            "cancellation_state": "active",
        }},
    )
    counters["jobs"] = result.modified_count

    result = db.summary_state.update_many(
        {"expires_at": {"$exists": False}},
        {"$set": {"expires_at": now + timedelta(days=30)}},
    )
    counters["states"] = result.modified_count

    for event in db.email_outbox.find({"expires_at": {"$exists": False}}):
        created_at = event.get("created_at") or now
        update: dict = {"$set": {
            "expires_at": created_at + timedelta(days=EMAIL_OUTBOX_RETENTION_DAYS),
        }}
        if event.get("status") in {"sent", "cancelled", "dead", "needs_review", "failed"}:
            if event.get("status") == "failed":
                update["$set"]["status"] = "dead"
            update["$unset"] = {
                "recipient": "",
                "result_payload": "",
                "original_filename": "",
            }
        result = db.email_outbox.update_one({"_id": event["_id"]}, update)
        counters["outbox"] += result.modified_count

    for package in db.user_package.find({}):
        period = package.get("usage_reset_month") or usage_period()
        update = {"usage_epoch": period}
        if "quota_reservations" not in package:
            update["quota_reservations"] = {}
        result = db.user_package.update_one({"_id": package["_id"]}, {"$set": update})
        counters["packages"] += result.modified_count

    voice_counts = {
        row["_id"]: int(row["count"])
        for row in db.voice_sample.aggregate([
            {"$group": {"_id": "$user_id", "count": {"$sum": 1}}},
        ])
    }
    for user in db.user.find({}, {"voice_sample_count": 1}):
        actual = voice_counts.get(user["_id"], 0)
        result = db.user.update_one(
            {"_id": user["_id"], "voice_sample_count": {"$ne": actual}},
            {"$set": {"voice_sample_count": actual}},
        )
        counters["voice_counters"] += result.modified_count

    db.package_request.update_many(
        {"status": {"$in": ["pending", "applying"]}},
        {"$set": {"active": True}},
    )
    db.package_request.update_many(
        {"status": {"$nin": ["pending", "applying"]}},
        {"$set": {"active": False}},
    )

    for job in db.job.find({"quota_reserved": True, "quota_reservation_id": {"$in": [None, ""]}}):
        reservation_id = str(job["_id"])
        period = usage_period(job.get("created_at") or now)
        state = "reserved"
        if job.get("quota_refunded"):
            state = "refunded"
        elif job.get("status") in {"completed", "partially_completed"}:
            state = "consumed"
        elif job.get("status") in {"failed", "cancelled"}:
            state = "reserved"
        key = _reservation_key(reservation_id)
        db.user_package.update_one(
            {"user_id": job["user_id"], f"quota_reservations.{key}": {"$exists": False}},
            {"$set": {f"quota_reservations.{key}": {
                "reservation_id": reservation_id,
                "period": period,
                "state": state,
                "files": 1,
                "ai_summaries": 1,
                "minutes": max(float(job.get("quota_minutes") or 0), 0),
                "created_at": job.get("created_at") or now,
                "migrated_at": now,
            }}},
        )
        db.job.update_one(
            {"_id": job["_id"]},
            {"$set": {"quota_reservation_id": reservation_id, "quota_settlement": state}},
        )
        counters["reservations"] += 1

    db.session.create_index(
        "job_id",
        unique=True,
        partialFilterExpression={"job_id": {"$type": "string"}},
        name="session_job_id_unique",
    )
    _ensure_ttl_index(db, db.session, "created_at", SESSION_RETENTION_SECONDS, "session_created_at_ttl")
    _drop_ttl_indexes(db.password_reset, "created_at")
    _ensure_ttl_index(db, db.password_reset, "expires_at", 0, "password_reset_expires_at")
    _drop_ttl_indexes(db.job, "created_at")
    _ensure_ttl_index(db, db.job, "completed_at", JOB_RETENTION_SECONDS, "job_completed_at_ttl")
    _ensure_ttl_index(db, db.summary_state, "expires_at", 0, "summary_state_expires_at")
    _ensure_ttl_index(db, db.email_outbox, "expires_at", 0, "email_outbox_expires_at")
    db.summary_state.create_index("job_id", unique=True)
    db.email_outbox.create_index("event_key", unique=True)
    db.data_deletion.create_index("deletion_id", unique=True)
    request_indexes = db.package_request.index_information()
    for index_name in (
        "uniq_pending_package_request_per_user",
        "uniq_active_package_request_per_user",
    ):
        if index_name in request_indexes:
            db.package_request.drop_index(index_name)
    db.package_request.create_index(
        [("user_id", 1)],
        unique=True,
        partialFilterExpression={"status": {"$in": ["pending", "applying"]}},
        name="uniq_active_package_request_per_user",
    )
    return counters


def main() -> int:
    load_dotenv()
    args = parse_args()
    client = MongoClient(
        os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017"),
        tz_aware=True,
    )
    db = client[os.getenv("MONGO_DB_NAME", "timsumv3")]
    try:
        client.admin.command("ping")
        report = preflight(db)
        print(f"Preflight OK: {report}")
        if args.check:
            print("Check-only mode; no writes were performed.")
            return 0
        counters = apply_backfill(db)
        postflight = preflight(db)
        print(f"Migration applied: {counters}")
        print(f"Postflight OK: {postflight}")
        return 0
    except Exception as exc:
        print(f"Workflow migration failed: {exc}", file=sys.stderr)
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
