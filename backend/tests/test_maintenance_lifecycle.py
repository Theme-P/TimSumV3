import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.tasks import maintenance


class FlakyStorage:
    def __init__(self):
        self.failed = False
        self.deleted = set()

    def delete_object(self, bucket, object_name):
        key = (bucket, object_name)
        if object_name == "artifact" and not self.failed:
            self.failed = True
            raise OSError("injected storage failure")
        self.deleted.add(key)

    def delete_prefix(self, bucket, prefix):
        self.deleted.add((bucket, prefix))


class MaintenanceLifecycleTests(unittest.TestCase):
    def test_storage_deletion_resumes_idempotently_after_failure(self):
        storage = FlakyStorage()
        snapshot = {
            "audio_objects": ["audio"],
            "artifact_objects": ["artifact"],
            "clip_prefixes": ["job"],
            "voice_objects": ["voice"],
        }

        with self.assertRaises(OSError):
            maintenance._delete_snapshot_storage(storage, snapshot)
        maintenance._delete_snapshot_storage(storage, snapshot)

        self.assertIn((maintenance.BUCKET_AUDIO, "audio"), storage.deleted)
        self.assertIn((maintenance.BUCKET_ARTIFACTS, "artifact"), storage.deleted)
        self.assertIn((maintenance.BUCKET_CLIPS, "job/"), storage.deleted)
        self.assertIn((maintenance.BUCKET_VOICE_SAMPLES, "voice"), storage.deleted)

    def test_snapshot_merge_keeps_first_pass_resources_for_reconciliation(self):
        merged = maintenance._merge_snapshots(
            {"job_ids": ["old"], "audio_objects": ["a"], "counts": {"jobs": 1}},
            {"job_ids": ["new"], "audio_objects": ["b"], "counts": {"jobs": 1}},
        )
        self.assertEqual(merged["job_ids"], ["new", "old"])
        self.assertEqual(merged["audio_objects"], ["a", "b"])

    def test_quota_ledger_closes_old_period_and_prunes_old_terminal_entries(self):
        now = datetime.now(timezone.utc)
        document = {
            "_id": "package",
            "quota_reservations": {
                "open": {
                    "state": "reserved",
                    "period": "2020-01",
                    "created_at": now - timedelta(days=90),
                },
                "done": {
                    "state": "consumed",
                    "period": "2020-01",
                    "settled_at": now - timedelta(days=maintenance.JOB_RETENTION_DAYS + 1),
                },
            },
        }

        class Cursor(list):
            def limit(self, count):
                return self[:count]

        class Collection:
            def find(self, query, projection=None):
                return Cursor([document])

            def update_one(self, query, update):
                for path, value in update.get("$set", {}).items():
                    _, key, field = path.split(".", 2)
                    document["quota_reservations"][key][field] = value
                for path in update.get("$unset", {}):
                    _, key = path.split(".", 1)
                    document["quota_reservations"].pop(key, None)
                return SimpleNamespace(modified_count=1)

        class Jobs:
            def update_one(self, query, update):
                return SimpleNamespace(modified_count=1)

        closed, pruned = maintenance._reconcile_quota_ledger(
            SimpleNamespace(user_package=Collection(), job=Jobs()), now
        )

        self.assertEqual((closed, pruned), (1, 1))
        self.assertEqual(document["quota_reservations"]["open"]["state"], "period_closed")
        self.assertNotIn("done", document["quota_reservations"])

    def test_terminal_outbox_update_scrubs_delivery_pii(self):
        update = maintenance._terminal_outbox_update("sent")
        self.assertEqual(
            set(update["$unset"]),
            {"recipient", "result_payload", "original_filename"},
        )


if __name__ == "__main__":
    unittest.main()
