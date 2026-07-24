import importlib
import os
import unittest
from unittest.mock import patch


CONFIG_ENV_KEYS = [
    "WHISPERX_MODEL",
    "WHISPERX_DEVICE",
    "WHISPERX_COMPUTE_TYPE",
    "WHISPERX_BATCH_SIZE",
    "WHISPERX_MIN_BATCH_SIZE",
    "WHISPERX_OOM_FALLBACK_COMPUTE_TYPE",
    "WHISPERX_LANGUAGE",
    "WHISPERX_BEAM_SIZE",
    "WHISPERX_BEST_OF",
    "WHISPERX_PATIENCE",
    "WHISPERX_VAD_ONSET",
    "WHISPERX_VAD_OFFSET",
    "WHISPERX_MIN_DURATION_ON",
    "WHISPERX_MIN_DURATION_OFF",
    "DIARIZATION_MIN_SPEAKERS",
    "DIARIZATION_MAX_SPEAKERS",
]


class PipelineConfigTests(unittest.TestCase):
    def _load_config_values(self, env):
        config_module = importlib.import_module("app.core.config")

        with patch.dict(os.environ, {}, clear=False):
            for key in CONFIG_ENV_KEYS:
                os.environ.pop(key, None)
            os.environ.update(env)

            config_module = importlib.reload(config_module)
            config = config_module.PipelineConfig
            values = {
                "model": config.MODEL_NAME,
                "device": config.DEVICE,
                "compute_type": config.COMPUTE_TYPE,
                "batch_size": config.BATCH_SIZE,
                "min_batch_size": config.MIN_BATCH_SIZE,
                "language": config.LANGUAGE,
                "beam_size": config.BEAM_SIZE,
                "best_of": config.BEST_OF,
                "patience": config.PATIENCE,
                "vad_onset": config.VAD_ONSET,
                "vad_offset": config.VAD_OFFSET,
                "min_duration_on": config.MIN_DURATION_ON,
                "min_duration_off": config.MIN_DURATION_OFF,
                "min_speakers": config.MIN_SPEAKERS,
                "max_speakers": config.MAX_SPEAKERS,
            }

        importlib.reload(config_module)
        return values

    def test_default_balanced_medium_profile(self):
        values = self._load_config_values({})

        self.assertEqual(values["model"], "medium")
        self.assertEqual(values["device"], "cuda")
        self.assertEqual(values["compute_type"], "float16")
        self.assertEqual(values["batch_size"], 8)
        self.assertEqual(values["min_batch_size"], 2)
        self.assertIsNone(values["language"])
        self.assertEqual(values["beam_size"], 5)
        self.assertEqual(values["best_of"], 5)
        self.assertEqual(values["patience"], 1.5)
        self.assertEqual(values["vad_onset"], 0.500)
        self.assertEqual(values["vad_offset"], 0.363)
        self.assertEqual(values["min_duration_on"], 0.10)
        self.assertEqual(values["min_duration_off"], 0.10)
        self.assertIsNone(values["min_speakers"])
        self.assertIsNone(values["max_speakers"])

    def test_env_overrides_vad_and_diarization_bounds(self):
        values = self._load_config_values(
            {
                "WHISPERX_BATCH_SIZE": "12",
                "WHISPERX_MIN_BATCH_SIZE": "3",
                "WHISPERX_LANGUAGE": "th",
                "WHISPERX_BEAM_SIZE": "4",
                "WHISPERX_BEST_OF": "3",
                "WHISPERX_PATIENCE": "1.2",
                "WHISPERX_VAD_ONSET": "0.420",
                "WHISPERX_VAD_OFFSET": "0.280",
                "WHISPERX_MIN_DURATION_ON": "0.15",
                "WHISPERX_MIN_DURATION_OFF": "0.25",
                "DIARIZATION_MIN_SPEAKERS": "2",
                "DIARIZATION_MAX_SPEAKERS": "8",
            }
        )

        self.assertEqual(values["batch_size"], 12)
        self.assertEqual(values["min_batch_size"], 3)
        self.assertEqual(values["language"], "th")
        self.assertEqual(values["beam_size"], 4)
        self.assertEqual(values["best_of"], 3)
        self.assertEqual(values["patience"], 1.2)
        self.assertEqual(values["vad_onset"], 0.420)
        self.assertEqual(values["vad_offset"], 0.280)
        self.assertEqual(values["min_duration_on"], 0.15)
        self.assertEqual(values["min_duration_off"], 0.25)
        self.assertEqual(values["min_speakers"], 2)
        self.assertEqual(values["max_speakers"], 8)

    def test_invalid_env_values_fall_back_or_clamp(self):
        values = self._load_config_values(
            {
                "WHISPERX_BATCH_SIZE": "not-an-int",
                "WHISPERX_MIN_BATCH_SIZE": "0",
                "WHISPERX_BEAM_SIZE": "nope",
                "WHISPERX_BEST_OF": "0",
                "WHISPERX_PATIENCE": "-2",
                "WHISPERX_VAD_ONSET": "2.5",
                "WHISPERX_VAD_OFFSET": "-1",
                "WHISPERX_MIN_DURATION_ON": "-0.5",
                "DIARIZATION_MIN_SPEAKERS": "0",
                "DIARIZATION_MAX_SPEAKERS": "unknown",
            }
        )

        self.assertEqual(values["batch_size"], 8)
        self.assertEqual(values["min_batch_size"], 1)
        self.assertEqual(values["beam_size"], 5)
        self.assertEqual(values["best_of"], 1)
        self.assertEqual(values["patience"], 0.0)
        self.assertEqual(values["vad_onset"], 1.0)
        self.assertEqual(values["vad_offset"], 0.0)
        self.assertEqual(values["min_duration_on"], 0.0)
        self.assertEqual(values["min_speakers"], 1)
        self.assertIsNone(values["max_speakers"])


if __name__ == "__main__":
    unittest.main()
