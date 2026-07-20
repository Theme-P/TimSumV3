import unittest

from app.services import summary_pipeline as pipeline


class SummaryPartialTests(unittest.TestCase):
    def test_extracts_summary_from_incomplete_json(self):
        content = '{"summary": "สรุปบางส่วนจาก Gemma ก่อน timeout'

        self.assertEqual(
            pipeline._partial_summary_from_content(content),
            "สรุปบางส่วนจาก Gemma ก่อน timeout",
        )

    def test_chunk_record_retains_partial_summary_when_json_is_incomplete(self):
        chunk = {
            "chunk_number": 1,
            "total_chunks": 1,
            "start_segment_idx": 0,
            "end_segment_idx": 1,
            "new_start_segment_idx": 0,
            "segment_ids": [0, 1],
            "text": "[S0][00:00:00][คนพูด 1] สวัสดี\n[S1][00:00:10][คนพูด 2] รับทราบ",
        }

        def llm_call(*args, **kwargs):
            return '{"summary": "ผู้เข้าร่วมทักทายและรับทราบประเด็น'

        record = pipeline.extract_chunk_record(chunk, None, llm_call)

        self.assertEqual(record["summary"], "ผู้เข้าร่วมทักทายและรับทราบประเด็น")
        self.assertEqual(record["failed_chunks"], [1])
        self.assertEqual(record["partial_chunks"], [1])
        self.assertEqual(record["coverage"], [])


if __name__ == "__main__":
    unittest.main()
