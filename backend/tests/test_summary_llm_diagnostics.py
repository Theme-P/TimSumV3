import unittest
from unittest.mock import Mock, patch

import requests

from app.services import summarizer


class SummaryLLMDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        summarizer._MODEL_COOLDOWN_UNTIL.clear()

    def test_gateway_uses_dynamic_timeout_and_returns_diagnostics(self):
        response = Mock()
        response.status_code = 200
        response.json.return_value = {
            "choices": [{"message": {"content": "สรุปสำเร็จ"}}],
        }
        timeouts = []

        def timeout_provider(model, attempt, configured_timeout):
            timeouts.append((model, attempt, configured_timeout))
            return 42

        with (
            patch.object(summarizer, "NTC_API_KEY", "test-key"),
            patch.object(summarizer, "NTC_LLM_STREAM_RESPONSES", False),
            patch.object(summarizer.requests, "post", return_value=response) as post,
        ):
            result = summarizer._call_ntc_gateway(
                "system",
                "user",
                timeout=300,
                model_name="test-model",
                attempt_timeout_provider=timeout_provider,
            )

        self.assertEqual(result.content, "สรุปสำเร็จ")
        self.assertFalse(result.timed_out)
        self.assertEqual(result.model, "test-model")
        self.assertEqual(result.attempts, 1)
        self.assertEqual(timeouts, [("test-model", 1, 300)])
        self.assertEqual(post.call_args.kwargs["timeout"], (15, 42))

    def test_gateway_marks_read_timeout(self):
        with (
            patch.object(summarizer, "NTC_API_KEY", "test-key"),
            patch.object(summarizer, "NTC_LLM_STREAM_RESPONSES", False),
            patch.object(
                summarizer.requests,
                "post",
                side_effect=requests.exceptions.ReadTimeout("read timed out"),
            ),
        ):
            result = summarizer._call_ntc_gateway(
                "system",
                "user",
                timeout=30,
                model_name="test-model",
                cooldown_on_read_timeout=False,
            )

        self.assertTrue(result.timed_out)
        self.assertEqual(result.error_kind, "model_timeout")
        self.assertEqual(result.attempts, 1)

    def test_fallback_keeps_plain_text_interface_by_default(self):
        gateway_result = summarizer.LLMCallResult(content="legacy text", model="test-model", attempts=1)
        config = {
            "primary_model": "test-model",
            "fallback_models": [],
            "temperature": 0.3,
            "max_tokens": 100,
        }
        with patch.object(summarizer, "_call_ntc_gateway", return_value=gateway_result):
            result = summarizer._call_llm_with_fallback(
                "system",
                "user",
                override_config=config,
            )

        self.assertEqual(result, "legacy text")


if __name__ == "__main__":
    unittest.main()
