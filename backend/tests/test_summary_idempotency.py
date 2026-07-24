import unittest
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from bson import ObjectId

from app.tasks import summary


class JobCollection:
    def __init__(self, document):
        self.document = document
        self.rotate_on_commit_owner = False

    def find_one(self, query, projection=None):
        if query.get("_id") != self.document["_id"]:
            return None
        if self.rotate_on_commit_owner and query.get("summary_commit_token"):
            self.document.update({
                "status": "completed",
                "result_available": True,
                "summary_active_run_id": "new-run",
                "summary_commit_run_id": "new-run",
                "summary_commit_token": "new-token",
                "summary_commit_generation": 2,
            })
            return None
        expected_run = query.get("summary_active_run_id")
        if expected_run and expected_run != self.document.get("summary_active_run_id"):
            return None
        return deepcopy(self.document)

    def update_one(self, query, update):
        if query.get("_id") != self.document["_id"]:
            return SimpleNamespace(matched_count=0, modified_count=0)
        if self.document.get("status") == "cancelled":
            return SimpleNamespace(matched_count=0, modified_count=0)
        if query.get("summary_active_run_id") not in (None, self.document.get("summary_active_run_id")):
            return SimpleNamespace(matched_count=0, modified_count=0)
        if query.get("result_available") and self.document.get("result_available"):
            return SimpleNamespace(matched_count=0, modified_count=0)
        self.document.update(deepcopy(update.get("$set", {})))
        for field, amount in update.get("$inc", {}).items():
            self.document[field] = int(self.document.get(field) or 0) + amount
        return SimpleNamespace(matched_count=1, modified_count=1)

    def find_one_and_update(self, query, update, projection=None, return_document=None):
        result = self.update_one(query, update)
        return deepcopy(self.document) if result.matched_count else None


class SessionCollection:
    def __init__(self):
        self.documents = {}

    def update_one(self, query, update, upsert=False):
        if query["job_id"] not in self.documents:
            document = deepcopy(update.get("$setOnInsert", {}))
            document["_id"] = ObjectId()
            self.documents[query["job_id"]] = document
            upserted_id = document["_id"]
        else:
            upserted_id = None
        self.documents[query["job_id"]].update(deepcopy(update.get("$set", {})))
        return SimpleNamespace(matched_count=0 if upserted_id else 1, modified_count=1, upserted_id=upserted_id)

    def find_one(self, query, projection=None):
        return deepcopy(self.documents.get(query["job_id"]))

    def delete_one(self, query):
        document = self.documents.get(query.get("job_id"))
        if document and query.get("summary_run_id") not in (None, document.get("summary_run_id")):
            return
        self.documents.pop(query.get("job_id"), None)


class SummaryStateCollection:
    def __init__(self, job_id, run_id):
        self.document = {
            "_id": ObjectId(),
            "job_id": job_id,
            "active_run_id": run_id,
            "status": "finalizing",
            "lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=5),
        }

    def find_one(self, query, projection=None):
        if query.get("job_id") != self.document["job_id"]:
            return None
        if query.get("active_run_id") not in (None, self.document["active_run_id"]):
            return None
        lease_query = query.get("lease_expires_at") or {}
        if "$gt" in lease_query and self.document["lease_expires_at"] <= lease_query["$gt"]:
            return None
        return deepcopy(self.document)

    def update_one(self, query, update):
        if query.get("job_id") != self.document["job_id"]:
            return SimpleNamespace(matched_count=0, modified_count=0)
        self.document.update(deepcopy(update.get("$set", {})))
        return SimpleNamespace(matched_count=1, modified_count=1)


class UserCollection:
    def __init__(self, user_id):
        self.user_id = user_id

    def find_one(self, query, projection=None):
        if query.get("_id") == self.user_id:
            return {"_id": self.user_id, "deletion_pending": False}
        return None


class SummaryIdempotencyTests(unittest.TestCase):
    def test_complete_job_upserts_one_session_across_redelivery(self):
        job_id = str(ObjectId())
        user_id = ObjectId()
        run_id = "winning-run"
        db = SimpleNamespace(
            job=JobCollection({
                "_id": ObjectId(job_id),
                "user_id": user_id,
                "status": "processing",
                "result_available": False,
                "summary_active_run_id": run_id,
            }),
            user=UserCollection(user_id),
            session=SessionCollection(),
            summary_state=SummaryStateCollection(job_id, run_id),
        )
        artifact = {
            "user_id": str(user_id),
            "original_filename": "meeting.mp3",
            "audio_length_seconds": 60,
            "meeting_type_id": 0,
            "processing_time": {},
            "full_transcript": {"segments": [], "speaker_summary": {}},
        }
        metadata = {
            "summary_started_at": datetime.now(timezone.utc).isoformat(),
            "summary_elapsed_seconds": 1,
        }
        finished = datetime.now(timezone.utc)

        with patch.object(summary, "settle_job_quota_db", return_value=True):
            first = summary._complete_job(
                db, job_id, run_id, artifact, "summary", metadata, "completed", finished,
            )
            second = summary._complete_job(
                db, job_id, run_id, artifact, "summary", metadata, "completed", finished,
            )

        self.assertEqual(first[0], second[0])
        self.assertEqual(len(db.session.documents), 1)
        self.assertEqual(db.session.documents[job_id]["summary_run_id"], run_id)

    def test_stale_finalizer_cannot_create_session_or_commit_job(self):
        job_id = str(ObjectId())
        user_id = ObjectId()
        db = SimpleNamespace(
            job=JobCollection({
                "_id": ObjectId(job_id),
                "user_id": user_id,
                "status": "processing",
                "result_available": False,
                "summary_active_run_id": "new-run",
            }),
            user=UserCollection(user_id),
            session=SessionCollection(),
            summary_state=SummaryStateCollection(job_id, "new-run"),
        )
        artifact = {
            "user_id": str(user_id),
            "original_filename": "meeting.mp3",
            "audio_length_seconds": 60,
            "meeting_type_id": 0,
            "processing_time": {},
            "full_transcript": {"segments": [], "speaker_summary": {}},
        }
        metadata = {"summary_started_at": datetime.now(timezone.utc).isoformat()}

        with self.assertRaises(summary.SummaryLockLost):
            summary._complete_job(
                db,
                job_id,
                "stale-run",
                artifact,
                "stale summary",
                metadata,
                "completed",
                datetime.now(timezone.utc),
            )

        self.assertEqual(db.session.documents, {})
        self.assertFalse(db.job.document["result_available"])

    def test_terminal_job_repairs_nonterminal_state_and_postcommit(self):
        job_id = str(ObjectId())
        run_id = "run"
        user_id = ObjectId()
        state_collection = SummaryStateCollection(job_id, run_id)
        db = SimpleNamespace(
            job=JobCollection({
                "_id": ObjectId(job_id),
                "user_id": user_id,
                "status": "completed",
                "result_available": True,
                "result": {"summary": "done"},
                "session_id": ObjectId(),
                "completed_at": datetime.now(timezone.utc),
                "summary_active_run_id": run_id,
            }),
            summary_state=state_collection,
        )
        state_collection.document["artifact_object"] = "artifact"

        with (
            patch.object(summary, "_load_artifact", return_value={"email_recipient": ""}),
            patch.object(summary, "_enqueue_post_commit") as enqueue,
        ):
            recovered = summary._recover_terminal_job_checkpoint(
                db, object(), job_id, deepcopy(state_collection.document)
            )

        self.assertEqual(recovered["status"], "completed")
        self.assertEqual(state_collection.document["status"], "completed")
        enqueue.assert_called_once()

    def test_paused_old_finalizer_cannot_overwrite_newer_session(self):
        job_id = str(ObjectId())
        user_id = ObjectId()
        run_id = "old-run"
        jobs = JobCollection({
            "_id": ObjectId(job_id),
            "user_id": user_id,
            "status": "processing",
            "result_available": False,
            "summary_active_run_id": run_id,
        })
        jobs.rotate_on_commit_owner = True
        sessions = SessionCollection()
        sessions.documents[job_id] = {
            "_id": ObjectId(),
            "job_id": job_id,
            "summary": "winning summary",
            "summary_run_id": "new-run",
            "summary_commit_token": "new-token",
            "summary_commit_generation": 2,
        }
        db = SimpleNamespace(
            job=jobs,
            user=UserCollection(user_id),
            session=sessions,
            summary_state=SummaryStateCollection(job_id, run_id),
        )
        artifact = {
            "user_id": str(user_id),
            "original_filename": "meeting.mp3",
            "audio_length_seconds": 60,
            "meeting_type_id": 0,
            "processing_time": {},
            "full_transcript": {"segments": [], "speaker_summary": {}},
        }

        with self.assertRaises(summary.SummaryLockLost):
            summary._complete_job(
                db,
                job_id,
                run_id,
                artifact,
                "stale summary",
                {"summary_started_at": datetime.now(timezone.utc).isoformat()},
                "completed",
                datetime.now(timezone.utc),
            )

        self.assertEqual(sessions.documents[job_id]["summary"], "winning summary")
        self.assertEqual(sessions.documents[job_id]["summary_commit_generation"], 2)


if __name__ == "__main__":
    unittest.main()
