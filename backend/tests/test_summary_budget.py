import unittest
from datetime import datetime, timedelta, timezone

from app.services.summary_budget import SummaryBudget, SummaryBudgetExhausted


class SummaryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.started_at = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    def budget_at(self, elapsed_seconds: int) -> SummaryBudget:
        now = self.started_at + timedelta(seconds=elapsed_seconds)
        return SummaryBudget(
            started_at=self.started_at,
            total_seconds=1200,
            request_seconds=300,
            min_remaining_seconds=30,
            final_reserved_seconds=120,
            now_fn=lambda: now,
        )

    def test_full_request_timeout_at_start(self):
        budget = self.budget_at(0)

        self.assertEqual(budget.request_timeout("extraction"), 300)
        self.assertEqual(budget.remaining_seconds("extraction"), 1080)
        self.assertEqual(budget.remaining_seconds("finalization"), 1200)

    def test_extraction_request_is_capped_before_finalize_reserve(self):
        budget = self.budget_at(1000)

        self.assertEqual(budget.request_timeout("extraction"), 80)
        self.assertEqual(budget.request_timeout("finalization"), 200)

    def test_request_stops_when_phase_has_less_than_minimum_time(self):
        budget = self.budget_at(1055)

        with self.assertRaises(SummaryBudgetExhausted) as raised:
            budget.request_timeout("extraction")

        self.assertEqual(raised.exception.reason, "insufficient_time_for_next_request")
        self.assertEqual(raised.exception.remaining_seconds, 25)

    def test_total_deadline_has_distinct_stop_reason(self):
        budget = self.budget_at(1200)

        with self.assertRaises(SummaryBudgetExhausted) as raised:
            budget.request_timeout("finalization")

        self.assertEqual(raised.exception.reason, "total_time_limit_reached")

    def test_state_round_trip_keeps_original_start_time(self):
        budget = self.budget_at(200)
        state = budget.state_fields()
        resumed = SummaryBudget.from_state(state, now_fn=lambda: self.started_at + timedelta(seconds=500))

        self.assertEqual(resumed.started_at, self.started_at)
        self.assertEqual(resumed.elapsed_seconds(), 500)


if __name__ == "__main__":
    unittest.main()
