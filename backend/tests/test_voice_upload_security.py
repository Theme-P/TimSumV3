from types import SimpleNamespace
from unittest.mock import patch

import pytest
from bson import ObjectId
from fastapi import HTTPException

from app.routers import voice_samples


class FakeAudio:
    def __init__(self, chunks, filename="sample.wav", content_type="audio/wav"):
        self._chunks = list(chunks)
        self.filename = filename
        self.content_type = content_type
        self.read_calls = 0

    async def read(self, _size):
        self.read_calls += 1
        return self._chunks.pop(0) if self._chunks else b""


class FakeMongo:
    def __init__(self):
        self.sample = None

    def count_voice_samples(self, _user_id):
        return 0

    def create_voice_sample(self, document):
        self.sample = document
        return str(document["_id"])


class FakeStorage:
    def __init__(self):
        self.upload = None

    def upload_file(self, bucket, object_name, path, content_type):
        self.upload = (bucket, object_name, path, content_type)

    def delete_object(self, _bucket, _object_name):
        return None


@pytest.mark.asyncio
async def test_voice_upload_stops_reading_after_size_limit():
    audio = FakeAudio([b"a" * 700_000, b"b" * 700_000, b"should-not-be-read"])
    user = SimpleNamespace(id=ObjectId())
    with (
        patch.object(voice_samples, "MAX_VOICE_SAMPLE_MB", 1),
        patch.object(voice_samples, "enforce_rate_limit"),
        patch.object(voice_samples, "_has_voice_entitlement", return_value=True),
    ):
        with pytest.raises(HTTPException) as exc_info:
            await voice_samples.upload_voice_sample(
                request=SimpleNamespace(),
                audio=audio,
                speaker_name="ผู้พูดทดสอบ",
                speaker_position="",
                user=user,
                mongo=FakeMongo(),
                storage=FakeStorage(),
            )

    assert exc_info.value.status_code == 413
    assert audio.read_calls == 2


@pytest.mark.asyncio
async def test_voice_upload_persists_the_same_server_generated_object_that_was_uploaded():
    audio = FakeAudio([b"valid-audio-bytes"])
    user = SimpleNamespace(id=ObjectId())
    mongo = FakeMongo()
    storage = FakeStorage()
    with (
        patch.object(voice_samples, "enforce_rate_limit"),
        patch.object(voice_samples, "_has_voice_entitlement", return_value=True),
        patch.object(voice_samples, "_ensure_user_can_store_voice"),
        patch.object(voice_samples, "_analyze_voice_sample", return_value=([0.1, 0.2], 8.5)),
    ):
        response = await voice_samples.upload_voice_sample(
            request=SimpleNamespace(),
            audio=audio,
            speaker_name="ผู้พูดทดสอบ",
            speaker_position="ประธาน",
            user=user,
            mongo=mongo,
            storage=storage,
        )

    assert response["success"] is True
    assert storage.upload is not None
    assert storage.upload[1] == mongo.sample["audio_path"]
    assert mongo.sample["content_type"] == "audio/wav"
    assert mongo.sample["duration_seconds"] == 8.5


@pytest.mark.asyncio
async def test_voice_upload_does_not_trust_client_supplied_html_mime():
    audio = FakeAudio([b"valid-audio-bytes"], content_type="text/html")
    user = SimpleNamespace(id=ObjectId())
    mongo = FakeMongo()
    storage = FakeStorage()
    with (
        patch.object(voice_samples, "enforce_rate_limit"),
        patch.object(voice_samples, "_has_voice_entitlement", return_value=True),
        patch.object(voice_samples, "_ensure_user_can_store_voice"),
        patch.object(voice_samples, "_analyze_voice_sample", return_value=([0.1], 8.0)),
    ):
        await voice_samples.upload_voice_sample(
            request=SimpleNamespace(),
            audio=audio,
            speaker_name="ผู้พูดทดสอบ",
            speaker_position="",
            user=user,
            mongo=mongo,
            storage=storage,
        )

    assert storage.upload[3] == "audio/wav"
    assert mongo.sample["content_type"] == "audio/wav"


def test_voice_analysis_rejects_duration_before_embedding():
    matcher = SimpleNamespace(
        get_audio_duration=lambda _path: 4.9,
        extract_embedding=lambda _path: pytest.fail("embedding should not run"),
    )
    with patch.object(voice_samples, "_voice_matcher", matcher):
        with pytest.raises(ValueError, match="duration_out_of_range"):
            voice_samples._analyze_voice_sample("sample.wav")
