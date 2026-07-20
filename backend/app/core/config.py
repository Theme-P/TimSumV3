import os


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


class PipelineConfig:
    """Configuration for the transcription-summary pipeline"""
    
    # Device settings
    DEVICE = os.environ.get("WHISPERX_DEVICE", "cuda")
    COMPUTE_TYPE = os.environ.get("WHISPERX_COMPUTE_TYPE", "float16")
    
    # WhisperX settings
    MODEL_NAME = os.environ.get("WHISPERX_MODEL", "large-v3")
    BATCH_SIZE = _env_int("WHISPERX_BATCH_SIZE", 8)
    MIN_BATCH_SIZE = _env_int("WHISPERX_MIN_BATCH_SIZE", 2)
    OOM_FALLBACK_COMPUTE_TYPE = (
        os.environ.get("WHISPERX_OOM_FALLBACK_COMPUTE_TYPE", "int8_float16").strip()
        or None
    )
    LANGUAGE = os.environ.get("WHISPERX_LANGUAGE") or None  # None = auto-detect language
    
    # Beam search settings
    BEAM_SIZE = int(os.environ.get("WHISPERX_BEAM_SIZE", "5"))
    BEST_OF = int(os.environ.get("WHISPERX_BEST_OF", "5"))
    PATIENCE = float(os.environ.get("WHISPERX_PATIENCE", "1.5"))

    # Optional stages. Keep defaults enabled to preserve current output quality.
    ENABLE_ALIGNMENT = _env_bool("ENABLE_ALIGNMENT", True)
    ENABLE_SPEAKER_CLIPS = _env_bool("ENABLE_SPEAKER_CLIPS", True)
    ENABLE_VOICE_MATCHING = _env_bool("ENABLE_VOICE_MATCHING", True)
    ENABLE_SPEAKER_NAME_DETECTION = _env_bool("ENABLE_SPEAKER_NAME_DETECTION", True)
    ENABLE_AGENDA_DETECTION = _env_bool("ENABLE_AGENDA_DETECTION", True)
    
    # VAD options (tuned for meeting audio with multiple speakers)
    VAD_ONSET = 0.500       # Speech start threshold (higher = less false positives)
    VAD_OFFSET = 0.363      # Speech end threshold
    MIN_DURATION_ON = 0.10  # Min speech duration (filter out clicks/noise)
    MIN_DURATION_OFF = 0.10 # Min silence to split segments (avoid over-splitting)
    
    # Speaker diarization settings
    MIN_SPEAKERS = None     # None = auto-detect (let pyannote decide)
    MAX_SPEAKERS = None     # None = auto-detect
    
    # HuggingFace token for diarization
    HF_TOKEN = os.environ.get("HF_TOKEN", "")


# EmailConfig was removed — EmailService reads env vars directly on construction.
# See app/services/email_service.py for the authoritative SMTP configuration.
