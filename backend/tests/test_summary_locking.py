import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.tasks import summary


class FakeRedis:
    def __init__(self, value="token"):
        self.value = value
        self.deleted = False
        self.expired = False

    def eval(self, script, key_count, key, token, *args):
        self.asserted = (key_count, key)
        if self.value != token:
            return 0
        if "del" in script:
            self.deleted = True
            self.value = None
            return 1
        self.expired = True
        return 1


class SummaryLockTests(unittest.TestCase):
    def test_release_is_token_checked_lua(self):
        client = FakeRedis("owner")
        summary._release_lock("job", client, "other")
        self.assertFalse(client.deleted)

        summary._release_lock("job", client, "owner")
        self.assertTrue(client.deleted)

    def test_renew_is_token_checked_lua(self):
        client = FakeRedis("owner")
        self.assertFalse(summary._renew_lock("job", client, "other"))
        self.assertTrue(summary._renew_lock("job", client, "owner"))
        self.assertTrue(client.expired)

    def test_redis_outage_fails_closed(self):
        with patch.object(summary, "_redis_client", side_effect=OSError("offline")):
            with self.assertRaises(summary.SummaryLockUnavailable):
                summary._acquire_lock("job")

    def test_stale_run_cannot_mutate_summary_state(self):
        class StaleCollection:
            def update_one(self, query, update):
                self.query = query
                return SimpleNamespace(matched_count=0)

        collection = StaleCollection()
        with self.assertRaises(summary.SummaryLockLost):
            summary._fenced_state_update(
                collection,
                "job",
                "stale-run",
                {"$set": {"status": "completed"}},
                reason="test",
            )
        self.assertEqual(collection.query["active_run_id"], "stale-run")


if __name__ == "__main__":
    unittest.main()
