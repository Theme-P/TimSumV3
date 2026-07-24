import unittest
from copy import deepcopy
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from app.services.summarizer import LLMCallResult
from app.services.summary_budget import SummaryBudget
from app.services.summary_pipeline import chunk_segments, normalize_record
from app.tasks import summary as summary_task


class AsyncSummaryFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)
        self.segments = [
            {"text": "วาระงบประมาณ", "speaker": "คนพูด 1", "start": 0, "end": 5, "_source_index": 0},
            {"text": "อนุมัติงบประมาณ", "speaker": "คนพูด 2", "start": 5, "end": 10, "_source_index": 1},
        ]
        self.chunks = chunk_segments(self.segments, max_tokens=8000, overlap_tokens=0)
        self.artifact = {
            "meeting_type_id": 0,
            "effective_meeting_type_id": 0,
            "custom_prompt": "",
            "full_transcript": {
                "segments": self.segments,
                "combined_text": "วาระงบประมาณ อนุมัติงบประมาณ",
            },
        }
        self.budget = SummaryBudget(started_at=self.now, now_fn=lambda: self.now)

    def record(self, coverage):
        record = normalize_record({"summary": "มีการอนุมัติงบประมาณ"})
        record.update({
            "coverage": coverage,
            "source_chunks": [1],
            "failed_chunks": [],
            "partial_chunks": [],
        })
        return record

    def finalize(self, records, llm_result, state=None):
        with patch.object(summary_task, "_template_prompt", return_value=""):
            return summary_task._final_metadata(
                Mock(),
                self.artifact,
                state or {},
                records,
                self.chunks,
                self.segments,
                lambda *args, **kwargs: llm_result,
                self.budget,
            )

    def test_full_coverage_and_final_render_is_completed(self):
        summary, metadata, status = self.finalize(
            [self.record([0, 1])],
            LLMCallResult(content="รายงานฉบับสมบูรณ์", model="gemma", attempts=1),
        )

        self.assertEqual(status, "completed")
        self.assertEqual(summary, "รายงานฉบับสมบูรณ์")
        self.assertEqual(metadata["stop_reason"], "completed")
        self.assertEqual(metadata["final_render_status"], "llm_completed")
        self.assertEqual(metadata["coverage_percentage"], 100.0)

    def test_incomplete_coverage_is_partially_completed(self):
        _, metadata, status = self.finalize(
            [self.record([0])],
            LLMCallResult(content="รายงานบางส่วน", model="gemma", attempts=1),
        )

        self.assertEqual(status, "partially_completed")
        self.assertFalse(metadata["coverage_complete"])
        self.assertEqual(metadata["coverage_percentage"], 50.0)
        self.assertTrue(metadata["failed_ranges"])

    def test_final_render_timeout_uses_deterministic_fallback(self):
        summary, metadata, status = self.finalize(
            [self.record([0, 1])],
            LLMCallResult(timed_out=True, error_kind="model_timeout", model="gemma", attempts=1),
        )

        self.assertEqual(status, "partially_completed")
        self.assertIn("สรุปการประชุม", summary)
        self.assertEqual(metadata["stop_reason"], "final_render_timeout")
        self.assertEqual(metadata["final_render_status"], "deterministic_fallback")

    def test_no_structured_record_is_failed_with_empty_summary(self):
        summary, metadata, status = self.finalize(
            [],
            LLMCallResult(content="should not be called"),
        )

        self.assertEqual(status, "failed")
        self.assertEqual(summary, "")
        self.assertEqual(metadata["final_render_status"], "unavailable")
        self.assertIn("user_warning", metadata)

    def test_worker_resume_filters_already_covered_segments(self):
        parts = summary_task._remaining_root_parts(self.chunks[0], {0})

        self.assertEqual(len(parts), 1)
        self.assertEqual(parts[0]["segment_ids"], [1])

    def test_checkpoint_replaces_same_record_id_instead_of_duplicating(self):
        class FakeCollection:
            def __init__(self):
                self.state = {
                    "job_id": "job-1",
                    "records": [],
                    "attempts": {},
                }

            def find_one(self, query):
                return deepcopy(self.state)

            def update_one(self, query, update, **kwargs):
                self.state.update(deepcopy(update.get("$set", {})))

        collection = FakeCollection()
        record = self.record([0, 1])
        record["_record_id"] = "1:structured"

        with (
            patch.object(summary_task, "_summary_state_collection", return_value=collection),
            patch.object(summary_task, "_update_job"),
        ):
            summary_task._checkpoint_summary_record(
                Mock(), "job-1", record, self.chunks, {0, 1}, self.budget, 0,
            )
            summary_task._checkpoint_summary_record(
                Mock(), "job-1", record, self.chunks, {0, 1}, self.budget, 0,
            )

        self.assertEqual(len(collection.state["records"]), 1)
        self.assertEqual(collection.state["attempts"]["1:structured"], 2)
        self.assertEqual(collection.state["coverage_percentage"], 100.0)


if __name__ == "__main__":
    unittest.main()
