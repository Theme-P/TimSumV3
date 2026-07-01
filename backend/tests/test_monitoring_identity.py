import os
import sys
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from bson import ObjectId

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.encryption import PIIEncryptor
from app.services.mongo import MongoService


class FakeCursor:
    def __init__(self, documents):
        self.documents = [dict(document) for document in documents]

    def sort(self, *_args):
        return self

    def skip(self, offset):
        self.documents = self.documents[offset:]
        return self

    def limit(self, limit):
        self.documents = self.documents[:limit]
        return self

    def __iter__(self):
        return iter(self.documents)


class FakeUserCollection:
    def __init__(self, users):
        self.users = users

    def find(self, query, _projection):
        requested_ids = set(query["_id"]["$in"])
        return [user for user in self.users if user["_id"] in requested_ids]


class FakeJobCollection:
    def __init__(self, jobs):
        self.jobs = jobs
        self.last_query = None

    def find(self, query, _projection):
        self.last_query = query
        documents = self.jobs
        if query.get("status"):
            documents = [doc for doc in documents if doc["status"] == query["status"]]
        user_query = query.get("user_id")
        if isinstance(user_query, dict):
            documents = [doc for doc in documents if doc["user_id"] in user_query["$in"]]
        return FakeCursor(documents)

    def count_documents(self, _query):
        return len(self.jobs)

    def distinct(self, field):
        return list({doc[field] for doc in self.jobs})


class FakeActivityCollection:
    def __init__(self, logs):
        self.logs = logs

    def find(self, _query):
        return FakeCursor(self.logs)


class TestMonitoringUserIdentity(unittest.TestCase):
    def setUp(self):
        self.user_id = ObjectId()
        self.encryptor = PIIEncryptor(
            keys={1: b"a" * 32},
            active_version=1,
            blind_index_key=b"b" * 32,
            enabled=True,
        )
        encrypted_user = self.encryptor.encrypt_user_document({
            "_id": self.user_id,
            "username": "somchai",
            "email": "somchai@example.com",
            "first_name": "สมชาย",
            "last_name": "ใจดี",
            "organization": "TimSum",
        })
        now = datetime.now(timezone.utc)
        self.jobs = [{
            "_id": ObjectId(),
            "user_id": self.user_id,
            "audio_file": "meeting.m4a",
            "status": "processing",
            "created_at": now,
            "started_at": now,
        }]
        self.logs = [{
            "_id": ObjectId(),
            "user_id": str(self.user_id),
            "action": "upload_audio",
            "timestamp": now,
        }]
        self.service = MongoService.__new__(MongoService)
        self.service.pii = self.encryptor
        self.service.db = SimpleNamespace(
            user=FakeUserCollection([encrypted_user]),
            job=FakeJobCollection(self.jobs),
            activity_log=FakeActivityCollection(self.logs),
        )

    def test_jobs_include_decrypted_user_identity_and_filter(self):
        jobs = self.service.get_all_jobs(
            status="processing",
            user_id=str(self.user_id),
        )

        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["user"]["display_name"], "สมชาย ใจดี")
        self.assertEqual(jobs[0]["user"]["email"], "somchai@example.com")
        self.assertIn(self.user_id, self.service.db.job.last_query["user_id"]["$in"])
        self.assertIsInstance(jobs[0]["started_at"], str)

    def test_activity_logs_include_decrypted_user_identity(self):
        logs = self.service.get_activity_logs()

        self.assertEqual(logs[0]["user"]["display_name"], "สมชาย ใจดี")
        self.assertEqual(logs[0]["user_id"], str(self.user_id))

    def test_job_user_filter_options_include_identity(self):
        users = self.service.get_job_filter_users()

        self.assertEqual(len(users), 1)
        self.assertEqual(users[0]["id"], str(self.user_id))
        self.assertEqual(users[0]["display_name"], "สมชาย ใจดี")

    def test_missing_user_gets_safe_label(self):
        identity = self.service._missing_user_identity(ObjectId())

        self.assertEqual(identity["display_name"], "บัญชีที่ถูกลบ")
        self.assertTrue(identity["missing"])


if __name__ == "__main__":
    unittest.main()
