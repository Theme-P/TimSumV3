import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import Lock
from types import SimpleNamespace

from bson import ObjectId

from app.services.mongo import MongoService, usage_period


class UserPackageCollection:
    def __init__(self, document):
        self.document = document
        self.reservation_update = None
        self.lock = Lock()

    def find_one(self, query, projection=None):
        if query.get("status") == "active" and self.document.get("status") != "active":
            return None
        if query.get("status") == "active" and self.document.get("expires_at"):
            if self.document["expires_at"] <= datetime.now(timezone.utc):
                return None
        return self.document

    def update_one(self, query, update):
        if "usage_reset_month" in query and isinstance(query["usage_reset_month"], dict):
            return SimpleNamespace(modified_count=0)
        with self.lock:
            self.reservation_update = (query, update)
            path, reservation = next(
                (key, value)
                for key, value in update.get("$set", {}).items()
                if key.startswith("quota_reservations.")
            )
            key = path.split(".", 1)[1]
            reservations = self.document.setdefault("quota_reservations", {})
            if key in reservations:
                return SimpleNamespace(modified_count=0)
            usage = self.document["usage"]
            if usage["files_this_month"] >= query["usage.files_this_month"]["$lt"]:
                return SimpleNamespace(modified_count=0)
            if usage["ai_summaries_this_month"] >= query["usage.ai_summaries_this_month"]["$lt"]:
                return SimpleNamespace(modified_count=0)
            if usage["transcription_minutes_this_month"] > query["usage.transcription_minutes_this_month"]["$lte"]:
                return SimpleNamespace(modified_count=0)
            reservations[key] = reservation
            for field, amount in update["$inc"].items():
                usage[field.rsplit(".", 1)[1]] += amount
            return SimpleNamespace(modified_count=1)


class PackageCollection:
    def __init__(self, document):
        self.document = document

    def find_one(self, query):
        return self.document


class QuotaLedgerTests(unittest.TestCase):
    @staticmethod
    def service_with_limit(limit=6):
        user_id = ObjectId()
        package_id = ObjectId()
        user_package = UserPackageCollection({
            "_id": ObjectId(),
            "user_id": user_id,
            "package_id": package_id,
            "status": "active",
            "usage_reset_month": usage_period(),
            "usage": {
                "files_this_month": 0,
                "ai_summaries_this_month": 0,
                "transcription_minutes_this_month": 0,
            },
            "quota_reservations": {},
        })
        service = MongoService.__new__(MongoService)
        service.db = SimpleNamespace(
            user_package=user_package,
            package=PackageCollection({
                "_id": package_id,
                "is_active": True,
                "limits": {
                    "max_files_per_month": limit,
                    "ai_summary_per_month": limit,
                    "transcription_minutes_per_month": limit * 10,
                },
            }),
        )
        service.cache = None
        return service, user_id, user_package

    def test_period_uses_bangkok_boundary(self):
        instant = datetime(2026, 7, 31, 17, 30, tzinfo=timezone.utc)
        self.assertEqual(usage_period(instant), "2026-08")

    def test_reservation_is_stable_and_idempotent_by_job_id(self):
        service, user_id, user_package = self.service_with_limit()

        first = service.reserve_job_quota(str(user_id), "job-1", 10)
        second = service.reserve_job_quota(str(user_id), "job-1", 10)

        self.assertTrue(first["allowed"])
        self.assertEqual(first["reservation_id"], "job-1")
        self.assertTrue(second["idempotent"])
        self.assertEqual(
            user_package.reservation_update[1]["$inc"]["usage.transcription_minutes_this_month"],
            10,
        )

    def test_twenty_concurrent_requests_cannot_exceed_limit_six(self):
        service, user_id, user_package = self.service_with_limit(6)

        with ThreadPoolExecutor(max_workers=20) as executor:
            results = list(executor.map(
                lambda number: service.reserve_job_quota(
                    str(user_id), f"job-{number}", 10,
                ),
                range(20),
            ))

        self.assertEqual(sum(bool(result["allowed"]) for result in results), 6)
        self.assertEqual(user_package.document["usage"]["files_this_month"], 6)
        self.assertEqual(user_package.document["usage"]["ai_summaries_this_month"], 6)
        self.assertEqual(user_package.document["usage"]["transcription_minutes_this_month"], 60)

    def test_expired_or_inactive_assignment_cannot_reserve(self):
        service, user_id, user_package = self.service_with_limit(6)
        user_package.document["status"] = "expired"

        result = service.reserve_job_quota(str(user_id), "job-expired", 1)

        self.assertFalse(result["allowed"])

        user_package.document["status"] = "active"
        user_package.document["expires_at"] = datetime.now(timezone.utc) - timedelta(seconds=1)
        expired = service.reserve_job_quota(str(user_id), "job-expired-time", 1)
        self.assertFalse(expired["allowed"])


if __name__ == "__main__":
    unittest.main()
