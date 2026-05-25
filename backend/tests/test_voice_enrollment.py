"""
Test script for Voice Enrollment System (Phase 4).

Tests:
1. VoiceSample model validation
2. Cosine similarity calculation
3. Voice matching logic
4. MongoDB voice sample CRUD (mock)
5. Package voice_enrollment_enabled flag

Run: python -m pytest backend/tests/test_voice_enrollment.py -v
  or: python backend/tests/test_voice_enrollment.py
"""
import sys
import os

# Add project root to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import unittest
from datetime import datetime
from bson import ObjectId


class TestCosineSimlarity(unittest.TestCase):
    """Test the cosine similarity function used for voice matching."""

    def test_identical_vectors(self):
        from app.services.voice_matching import _cosine_similarity
        vec = [1.0, 0.0, 0.5, 0.3]
        result = _cosine_similarity(vec, vec)
        self.assertAlmostEqual(result, 1.0, places=5)

    def test_orthogonal_vectors(self):
        from app.services.voice_matching import _cosine_similarity
        vec_a = [1.0, 0.0]
        vec_b = [0.0, 1.0]
        result = _cosine_similarity(vec_a, vec_b)
        self.assertAlmostEqual(result, 0.0, places=5)

    def test_opposite_vectors(self):
        from app.services.voice_matching import _cosine_similarity
        vec_a = [1.0, 0.0]
        vec_b = [-1.0, 0.0]
        result = _cosine_similarity(vec_a, vec_b)
        self.assertAlmostEqual(result, -1.0, places=5)

    def test_similar_vectors(self):
        from app.services.voice_matching import _cosine_similarity
        vec_a = [1.0, 0.5, 0.3]
        vec_b = [0.9, 0.6, 0.2]
        result = _cosine_similarity(vec_a, vec_b)
        self.assertGreater(result, 0.9)

    def test_zero_vector(self):
        from app.services.voice_matching import _cosine_similarity
        vec_a = [0.0, 0.0]
        vec_b = [1.0, 0.0]
        result = _cosine_similarity(vec_a, vec_b)
        self.assertEqual(result, 0.0)


class TestVoiceMatching(unittest.TestCase):
    """Test the speaker matching logic."""

    def setUp(self):
        from app.services.voice_matching import VoiceMatchingService
        self.service = VoiceMatchingService(device="cpu")

    def test_match_speakers_perfect_match(self):
        diarized = {
            "คนพูด 1": [1.0, 0.0, 0.5],
            "คนพูด 2": [0.0, 1.0, 0.5],
        }
        samples = [
            {
                "speaker_name": "คุณเจษฎา",
                "speaker_position": "ประธาน",
                "embedding": [1.0, 0.0, 0.5],  # Matches คนพูด 1
            },
            {
                "speaker_name": "คุณสมศรี",
                "speaker_position": "เลขาฯ",
                "embedding": [0.0, 1.0, 0.5],  # Matches คนพูด 2
            },
        ]
        matches = self.service.match_speakers(diarized, samples, threshold=0.9)
        self.assertEqual(len(matches), 2)
        self.assertEqual(matches["คนพูด 1"]["name"], "คุณเจษฎา")
        self.assertEqual(matches["คนพูด 2"]["name"], "คุณสมศรี")

    def test_match_speakers_below_threshold(self):
        diarized = {
            "คนพูด 1": [1.0, 0.0, 0.5],
        }
        samples = [
            {
                "speaker_name": "คุณใครก็ไม่รู้",
                "embedding": [0.0, 1.0, 0.0],  # Very different
            },
        ]
        matches = self.service.match_speakers(diarized, samples, threshold=0.75)
        self.assertEqual(len(matches), 0)

    def test_match_speakers_empty_inputs(self):
        self.assertEqual(self.service.match_speakers({}, []), {})
        self.assertEqual(self.service.match_speakers({"x": [1.0]}, []), {})
        self.assertEqual(self.service.match_speakers({}, [{"embedding": [1.0]}]), {})

    def test_match_speakers_no_duplicate_assignments(self):
        """Same voice sample should not match multiple speakers."""
        diarized = {
            "คนพูด 1": [1.0, 0.0, 0.0],
            "คนพูด 2": [0.95, 0.05, 0.0],  # Very similar to คนพูด 1
        }
        samples = [
            {
                "speaker_name": "คุณเจษฎา",
                "embedding": [1.0, 0.0, 0.0],
            },
        ]
        matches = self.service.match_speakers(diarized, samples, threshold=0.9)
        # Should match at most 1 speaker per sample
        names = [m["name"] for m in matches.values()]
        self.assertEqual(names.count("คุณเจษฎา"), min(1, len(matches)))

    def test_match_includes_confidence_and_source(self):
        diarized = {"คนพูด 1": [1.0, 0.0, 0.5]}
        samples = [
            {
                "speaker_name": "คุณเจษฎา",
                "speaker_position": "ประธาน",
                "embedding": [1.0, 0.0, 0.5],
            },
        ]
        matches = self.service.match_speakers(diarized, samples, threshold=0.5)
        self.assertIn("คนพูด 1", matches)
        match = matches["คนพูด 1"]
        self.assertIn("confidence", match)
        self.assertEqual(match["source"], "voice_enrollment")
        self.assertGreater(match["confidence"], 0.99)


class TestVoiceSampleModel(unittest.TestCase):
    """Test VoiceSample model validation."""

    def test_model_creation(self):
        from app.models.voice_sample import VoiceSample
        sample = VoiceSample(
            _id=ObjectId(),
            user_id=ObjectId(),
            speaker_name="คุณเจษฎา",
            speaker_position="ประธาน",
            audio_path="user_123/abc.mp3",
            embedding=[0.1, 0.2, 0.3],
            duration_seconds=10.5,
        )
        self.assertEqual(sample.speaker_name, "คุณเจษฎา")
        self.assertEqual(len(sample.embedding), 3)
        self.assertEqual(sample.duration_seconds, 10.5)

    def test_model_defaults(self):
        from app.models.voice_sample import VoiceSample
        sample = VoiceSample(
            _id=ObjectId(),
            user_id=ObjectId(),
            speaker_name="Test",
            audio_path="test.mp3",
        )
        self.assertEqual(sample.speaker_position, "")
        self.assertEqual(sample.embedding, [])
        self.assertEqual(sample.duration_seconds, 0.0)


class TestPackageVoiceEnrollment(unittest.TestCase):
    """Test that voice_enrollment_enabled is properly set in packages."""

    def test_basic_package_disabled(self):
        from app.models.package import DEFAULT_PACKAGES
        basic = DEFAULT_PACKAGES[0]
        self.assertEqual(basic["name"], "TimSumBasic")
        self.assertFalse(basic["limits"].get("voice_enrollment_enabled", False))

    def test_pro_package_enabled(self):
        from app.models.package import DEFAULT_PACKAGES
        pro = DEFAULT_PACKAGES[1]
        self.assertEqual(pro["name"], "TimSumPro")
        self.assertTrue(pro["limits"].get("voice_enrollment_enabled", False))

    def test_enterprise_package_enabled(self):
        from app.models.package import DEFAULT_PACKAGES
        enterprise = DEFAULT_PACKAGES[2]
        self.assertEqual(enterprise["name"], "TimSumEnterprise")
        self.assertTrue(enterprise["limits"].get("voice_enrollment_enabled", False))

    def test_admin_package_enabled(self):
        from app.models.package import ADMIN_PACKAGE
        self.assertTrue(ADMIN_PACKAGE["limits"].get("voice_enrollment_enabled", False))

    def test_superadmin_package_enabled(self):
        from app.models.package import SUPERADMIN_PACKAGE
        self.assertTrue(SUPERADMIN_PACKAGE["limits"].get("voice_enrollment_enabled", False))

    def test_package_limits_model_has_field(self):
        from app.models.package import PackageLimits
        limits = PackageLimits()
        self.assertFalse(limits.voice_enrollment_enabled)
        limits_enabled = PackageLimits(voice_enrollment_enabled=True)
        self.assertTrue(limits_enabled.voice_enrollment_enabled)


class TestVoiceSampleConstants(unittest.TestCase):
    """Test voice sample constants."""

    def test_limits(self):
        from app.models.voice_sample import (
            MAX_VOICE_SAMPLES_PER_USER,
            MAX_VOICE_SAMPLE_MB,
            ALLOWED_VOICE_EXTENSIONS,
            DEFAULT_SIMILARITY_THRESHOLD,
        )
        self.assertEqual(MAX_VOICE_SAMPLES_PER_USER, 20)
        self.assertEqual(MAX_VOICE_SAMPLE_MB, 10)
        self.assertIn(".mp3", ALLOWED_VOICE_EXTENSIONS)
        self.assertIn(".wav", ALLOWED_VOICE_EXTENSIONS)
        self.assertEqual(DEFAULT_SIMILARITY_THRESHOLD, 0.75)


if __name__ == "__main__":
    print("\n🧪 Running Voice Enrollment Tests...")
    print("=" * 60)
    unittest.main(verbosity=2)
