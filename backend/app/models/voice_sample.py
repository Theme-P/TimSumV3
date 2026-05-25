"""
Voice Sample model for speaker voice enrollment.

Stores voice clip metadata, MinIO path, and speaker embedding vector
for voice-matched diarization.
"""
from datetime import datetime
from typing import Optional
from bson import ObjectId
from pydantic import BaseModel, ConfigDict, Field


# Limits
MAX_VOICE_SAMPLES_PER_USER = 20
MAX_VOICE_SAMPLE_MB = 10  # 10 MB max per clip
ALLOWED_VOICE_EXTENSIONS = [".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm"]

# Voice matching
DEFAULT_SIMILARITY_THRESHOLD = 0.75


class VoiceSample(BaseModel):
    id: ObjectId = Field(alias="_id", default_factory=ObjectId)
    user_id: ObjectId
    speaker_name: str  # e.g. "คุณเจษฎา"
    speaker_position: str = ""  # e.g. "ประธาน" (optional)
    audio_path: str  # MinIO object name in voice-samples bucket
    embedding: list[float] = []  # speaker embedding vector (~256 floats)
    duration_seconds: float = 0.0
    original_filename: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        populate_by_name=True,
        json_encoders={ObjectId: str},
    )
