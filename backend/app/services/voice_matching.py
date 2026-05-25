"""
Voice matching service for speaker identification via voice enrollment.

Uses PyAnnote embedding model to extract speaker embeddings from audio clips,
and cosine similarity to match diarized speakers against enrolled voice samples.
"""
import os
import logging
import numpy as np
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    a_arr = np.array(a, dtype=np.float32)
    b_arr = np.array(b, dtype=np.float32)
    norm_a = np.linalg.norm(a_arr)
    norm_b = np.linalg.norm(b_arr)
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return float(np.dot(a_arr, b_arr) / (norm_a * norm_b))


class VoiceMatchingService:
    """
    Service for extracting speaker embeddings and matching them
    against enrolled voice samples.
    """

    def __init__(self, device: str = "cpu"):
        self.device = device
        self._model = None

    def _load_model(self):
        """Lazy-load the PyAnnote embedding model."""
        if self._model is not None:
            return

        try:
            from pyannote.audio import Inference
            hf_token = os.environ.get("HF_TOKEN", "")
            self._model = Inference(
                "pyannote/embedding",
                use_auth_token=hf_token,
                window="whole",
            )
            # Move to the target device
            self._model.to(self.device)
            logger.info(f"PyAnnote embedding model loaded on {self.device}")
        except TypeError:
            # Newer pyannote versions use 'token' parameter
            from pyannote.audio import Inference
            hf_token = os.environ.get("HF_TOKEN", "")
            self._model = Inference(
                "pyannote/embedding",
                token=hf_token,
                window="whole",
            )
            self._model.to(self.device)
            logger.info(f"PyAnnote embedding model loaded on {self.device} (new API)")
        except Exception as e:
            logger.error(f"Failed to load PyAnnote embedding model: {e}")
            raise

    def extract_embedding(self, audio_path: str) -> list[float]:
        """
        Extract speaker embedding from an audio file.

        Args:
            audio_path: Path to audio file (WAV, MP3, etc.)

        Returns:
            List of floats representing the speaker embedding vector (~256 dims)
        """
        self._load_model()

        try:
            embedding = self._model(audio_path)
            # pyannote returns numpy array — convert to list
            if hasattr(embedding, 'tolist'):
                return embedding.tolist()
            return list(embedding)
        except Exception as e:
            logger.error(f"Failed to extract embedding from {audio_path}: {e}")
            raise

    def extract_embedding_from_segments(
        self,
        audio_path: str,
        segments: list[dict],
        speaker_label: str,
        max_duration: float = 30.0,
    ) -> Optional[list[float]]:
        """
        Extract speaker embedding from diarized segments for a specific speaker.

        Collects up to max_duration seconds of audio for the target speaker
        and extracts a combined embedding.

        Args:
            audio_path: Path to the full audio file
            segments: Diarized segments with 'speaker', 'start', 'end' keys
            speaker_label: The speaker label to extract embedding for
            max_duration: Maximum audio duration to use (seconds)

        Returns:
            Embedding vector or None if no segments found
        """
        self._load_model()

        # Get segments for this speaker
        speaker_segs = [
            s for s in segments
            if s.get("speaker") == speaker_label
        ]
        if not speaker_segs:
            return None

        try:
            from pyannote.core import Segment
            from pyannote.audio import Inference
            import torch

            # Sort by duration (longest first) and accumulate up to max_duration
            speaker_segs.sort(key=lambda s: s["end"] - s["start"], reverse=True)

            embeddings = []
            total_duration = 0.0

            for seg in speaker_segs:
                if total_duration >= max_duration:
                    break

                start = seg["start"]
                end = seg["end"]
                duration = end - start

                if duration < 0.5:  # Skip very short segments
                    continue

                try:
                    segment = Segment(start, min(end, start + max_duration - total_duration))
                    emb = self._model.crop(audio_path, segment)
                    if hasattr(emb, 'tolist'):
                        embeddings.append(emb)
                    total_duration += duration
                except Exception:
                    continue

            if not embeddings:
                return None

            # Average all embeddings
            avg_embedding = np.mean(embeddings, axis=0)
            return avg_embedding.tolist()

        except Exception as e:
            logger.warning(f"Failed to extract segment embedding for {speaker_label}: {e}")
            return None

    def match_speakers(
        self,
        diarized_embeddings: Dict[str, list[float]],
        voice_samples: List[dict],
        threshold: float = 0.75,
    ) -> Dict[str, dict]:
        """
        Match diarized speaker embeddings against enrolled voice samples.

        Args:
            diarized_embeddings: {speaker_label: embedding_vector}
            voice_samples: List of voice sample dicts with 'speaker_name',
                          'speaker_position', 'embedding' keys
            threshold: Minimum cosine similarity for a match

        Returns:
            Dict of matched speakers:
            {
                "คนพูด 1": {
                    "name": "คุณเจษฎา",
                    "position": "ประธาน",
                    "confidence": 0.89,
                    "source": "voice_enrollment"
                },
                ...
            }
        """
        if not diarized_embeddings or not voice_samples:
            return {}

        matches = {}
        used_samples = set()  # Prevent same sample matching multiple speakers

        # For each diarized speaker, find the best matching voice sample
        for speaker_label, speaker_emb in diarized_embeddings.items():
            best_match = None
            best_score = -1

            for i, sample in enumerate(voice_samples):
                if i in used_samples:
                    continue

                sample_emb = sample.get("embedding", [])
                if not sample_emb:
                    continue

                score = _cosine_similarity(speaker_emb, sample_emb)

                if score > best_score and score >= threshold:
                    best_score = score
                    best_match = {
                        "name": sample.get("speaker_name", ""),
                        "position": sample.get("speaker_position", ""),
                        "confidence": round(score, 3),
                        "source": "voice_enrollment",
                        "sample_index": i,
                    }

            if best_match:
                matches[speaker_label] = best_match
                used_samples.add(best_match["sample_index"])
                logger.info(
                    f"Voice match: {speaker_label} → {best_match['name']} "
                    f"(confidence: {best_match['confidence']})"
                )

        # Clean up sample_index from results
        for match in matches.values():
            match.pop("sample_index", None)

        return matches

    def get_audio_duration(self, audio_path: str) -> float:
        """Get duration of an audio file in seconds."""
        try:
            import subprocess
            result = subprocess.run(
                [
                    "ffprobe", "-v", "quiet",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                return float(result.stdout.strip())
        except Exception:
            pass
        return 0.0
