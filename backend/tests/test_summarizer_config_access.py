"""
Regression tests for summarizer DB/config access.

Run: python -m pytest backend/tests/test_summarizer_config_access.py -v
  or: python backend/tests/test_summarizer_config_access.py
"""
import os
import sys
import types
import unittest
import importlib.util


sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def ensure_runtime_stubs():
    """Let these focused tests run in minimal local envs without backend deps installed."""
    if importlib.util.find_spec("requests") is None:
        requests_stub = types.ModuleType("requests")

        class RequestException(Exception):
            pass

        class Timeout(RequestException):
            pass

        requests_stub.exceptions = types.SimpleNamespace(
            RequestException=RequestException,
            Timeout=Timeout,
        )
        requests_stub.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            RequestException("requests is not installed")
        )
        sys.modules["requests"] = requests_stub

    try:
        pymongo_database_spec = importlib.util.find_spec("pymongo.database")
    except ModuleNotFoundError:
        pymongo_database_spec = None

    if pymongo_database_spec is None:
        pymongo_stub = types.ModuleType("pymongo")
        database_stub = types.ModuleType("pymongo.database")

        class Database:
            pass

        database_stub.Database = Database
        pymongo_stub.database = database_stub
        sys.modules["pymongo"] = pymongo_stub
        sys.modules["pymongo.database"] = database_stub


ensure_runtime_stubs()


class RaisingBoolMongoService:
    def __bool__(self):
        raise NotImplementedError("truth value testing is not supported")

    def get_llm_config(self, name):
        return {
            "name": name,
            "primary_model": "custom-primary",
            "fallback_models": ["custom-fallback"],
            "temperature": 0.2,
            "max_tokens": 1234,
        }

    def get_meeting_template(self, meeting_type_id):
        return {
            "meeting_type_id": meeting_type_id,
            "system_prompt": "custom prompt {num_speakers}",
            "temperature": 0.1,
            "max_tokens": 999,
        }


class LegacyModelMongoService:
    def get_llm_config(self, name):
        return {
            "name": name,
            "primary_model": "gpt-4.1",
            "fallback_models": [
                "qwen2.5:72b-instruct-q4_K_M",
                "scb10x/typhoon2.1-gemma3-12b",
            ],
            "temperature": 0.3,
            "max_tokens": 4000,
        }


class FakeCollection:
    def __init__(self, doc):
        self.doc = doc
        self.last_query = None

    def find_one(self, query):
        self.last_query = query
        return dict(self.doc)


class FakeDatabase:
    def __init__(self):
        self.llm_config = FakeCollection({
            "name": "default_fallback",
            "primary_model": "db-primary",
            "fallback_models": ["db-fallback"],
            "temperature": 0.25,
            "max_tokens": 2222,
        })
        self.meeting_template = FakeCollection({
            "meeting_type_id": 4,
            "system_prompt": "db prompt {num_speakers}",
            "temperature": 0.35,
            "max_tokens": 3333,
        })

    def __bool__(self):
        raise NotImplementedError("truth value testing is not supported")


class TestSummarizerConfigAccess(unittest.TestCase):
    def test_mongo_service_does_not_use_truth_value(self):
        from app.services import summarizer

        service = RaisingBoolMongoService()

        config = summarizer.get_llm_config(service)
        self.assertEqual(config["primary_model"], "custom-primary")
        self.assertEqual(config["fallback_models"], ["custom-fallback"])

        template = summarizer._get_template_for_meeting(4, service)
        self.assertEqual(template["system_prompt"], "custom prompt {num_speakers}")

    def test_legacy_models_are_normalized_to_ntc_gateway_models(self):
        from app.services import summarizer

        config = summarizer.get_llm_config(LegacyModelMongoService())

        self.assertEqual(config["primary_model"], summarizer.NTC_MODEL)
        self.assertEqual(config["fallback_models"], summarizer.DEFAULT_FALLBACK_MODELS)

    def test_pymongo_database_shape_is_supported(self):
        from app.services import summarizer

        original_database_type = summarizer.Database
        summarizer.Database = FakeDatabase
        try:
            db = FakeDatabase()

            config = summarizer.get_llm_config(db)
            self.assertEqual(config["primary_model"], "db-primary")
            self.assertEqual(db.llm_config.last_query, {"name": "default_fallback"})

            template = summarizer._get_template_for_meeting(4, db)
            self.assertEqual(template["system_prompt"], "db prompt {num_speakers}")
            self.assertEqual(db.meeting_template.last_query, {"meeting_type_id": 4})
        finally:
            summarizer.Database = original_database_type

    def test_gateway_error_sanitizer_redacts_token_details(self):
        from app.services import summarizer

        fake_key = "sk-" + "secret-last4"
        fake_hash = (
            "8405baf2126950ce06c809373a091ab1b"
            "b0bb9f7521b6471a1afb9909def9be8"
        )
        raw = (
            f"Authentication Error. Received API Key = {fake_key}, "
            f"Key Hash (Token) ={fake_hash}"
        )
        sanitized = summarizer._sanitize_gateway_error(raw)

        self.assertNotIn(fake_key, sanitized)
        self.assertNotIn(fake_hash, sanitized)
        self.assertIn("[redacted]", sanitized)


if __name__ == "__main__":
    unittest.main(verbosity=2)
