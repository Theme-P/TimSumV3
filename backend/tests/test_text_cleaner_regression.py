import unittest

from app.services.text_cleaner import (
    clean_transcription,
    filter_excessive_words,
    join_canonical_segments,
)


class TextCleanerRegressionTests(unittest.TestCase):
    def test_common_terms_are_never_globally_capped(self):
        text = " ".join(f"โครงการ วาระ{i}" for i in range(100))

        cleaned = clean_transcription(text)

        self.assertEqual(cleaned.split().count("โครงการ"), 100)

    def test_only_consecutive_phrase_hallucination_is_capped(self):
        text = "อนุมัติ งบประมาณ " * 8

        cleaned = clean_transcription(text)

        self.assertEqual(cleaned, "อนุมัติ งบประมาณ อนุมัติ งบประมาณ")

    def test_ordinary_music_words_remain_but_explicit_marker_is_removed(self):
        text = "กล่าวถึงเสียงดนตรีในงาน [เสียงดนตรี] แล้วประชุมต่อ"

        cleaned = clean_transcription(text)

        self.assertEqual(cleaned, "กล่าวถึงเสียงดนตรีในงาน แล้วประชุมต่อ")

    def test_speaker_line_boundaries_are_preserved(self):
        text = "[คนพูด 1]: เริ่มประชุม\n[คนพูด 2]: รับทราบ"

        self.assertEqual(clean_transcription(text).splitlines(), text.splitlines())

    def test_legacy_frequency_helper_is_non_destructive(self):
        text = "ชื่อ " * 10
        self.assertEqual(filter_excessive_words(text, max_occurrences=3), text)

    def test_canonical_segments_keep_newline_boundaries(self):
        segments = [{"text": "วาระหนึ่ง"}, {"text": "วาระสอง"}]
        self.assertEqual(join_canonical_segments(segments), "วาระหนึ่ง\nวาระสอง")


if __name__ == "__main__":
    unittest.main()
