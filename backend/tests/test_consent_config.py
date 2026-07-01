import unittest

from app.models.consent import CONSENT_TYPES, REQUIRED_CONSENT_TYPES


class TestConsentConfiguration(unittest.TestCase):
    def test_marketing_consent_is_not_available(self):
        self.assertNotIn("marketing", CONSENT_TYPES)

    def test_required_pdpa_consents_remain_available(self):
        self.assertEqual(
            set(REQUIRED_CONSENT_TYPES),
            {"privacy_policy", "data_processing"},
        )


if __name__ == "__main__":
    unittest.main()
