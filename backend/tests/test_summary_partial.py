import unittest

from app.services import summary_pipeline as pipeline


class SummaryPartialTests(unittest.TestCase):
    def test_chunk_segments_cover_each_segment_once_without_overlap(self):
        segments = [
            {"text": f"ประเด็นที่ {index} " * 20, "speaker": "คนพูด 1", "start": index, "end": index + 1}
            for index in range(8)
        ]

        chunks = pipeline.chunk_segments(segments, max_tokens=80, overlap_tokens=0)
        seen = [segment_id for chunk in chunks for segment_id in chunk["segment_ids"]]

        self.assertGreater(len(chunks), 1)
        self.assertEqual(seen, list(range(8)))
        self.assertEqual(len(seen), len(set(seen)))

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

    def test_recovered_partial_chunk_is_not_left_unresolved(self):
        chunks = [
            {"chunk_number": 1, "segment_ids": [0, 1, 2]},
            {"chunk_number": 2, "segment_ids": [3, 4]},
        ]

        self.assertEqual(
            pipeline.unresolved_chunk_numbers(chunks, [1], {0, 1}),
            [1],
        )
        self.assertEqual(
            pipeline.unresolved_chunk_numbers(chunks, [1], {0, 1, 2}),
            [],
        )

    def test_incremental_summary_marks_partial_metadata(self):
        segments = [
            {"text": "วันนี้หารือเรื่องงบประมาณประจำปี", "speaker": "นายก", "start": 0, "end": 10},
            {"text": "ขอเพิ่มงบการตลาดและให้ฝ่ายบัญชีตรวจตัวเลข", "speaker": "เลขา", "start": 10, "end": 20},
        ]

        def llm_call(system_prompt, user_prompt, **kwargs):
            if "Transcript ปัจจุบัน" in user_prompt:
                return '{"summary": "มีการหารืองบประมาณและงานให้ฝ่ายบัญชีตรวจ'
            return "สรุปสุดท้ายจาก partial"

        summary, metadata = pipeline.summarize_transcript_incrementally(
            transcript="\n".join(segment["text"] for segment in segments),
            segments=segments,
            meeting_type_id=0,
            template_prompt="",
            custom_prompt="",
            llm_call=llm_call,
        )

        self.assertIn("สรุปสุดท้ายจาก partial", summary)
        self.assertFalse(metadata["coverage_complete"])
        self.assertEqual(metadata["partial_chunks"], [1])
        self.assertEqual(metadata["failed_chunks"], [1])
        self.assertIn("user_warning", metadata)


if __name__ == "__main__":
    unittest.main()
