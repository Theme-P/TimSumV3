"""
MinIO Object Storage service for TimSumV3.

Buckets:
  - audio-uploads   : raw audio files from users
  - speaker-clips   : extracted speaker audio clips (~10s each)
"""
import os
import tempfile
from io import BytesIO
from typing import Optional

from loguru import logger
from minio import Minio
from minio.error import S3Error

BUCKET_AUDIO = "audio-uploads"
BUCKET_CLIPS = "speaker-clips"
BUCKET_VOICE_SAMPLES = "voice-samples"

_ALL_BUCKETS = [BUCKET_AUDIO, BUCKET_CLIPS, BUCKET_VOICE_SAMPLES]


class StorageService:
    """Thin wrapper around MinIO client with auto-bucket creation."""

    def __init__(
        self,
        endpoint: str = None,
        access_key: str = None,
        secret_key: str = None,
        secure: bool = False,
    ):
        self.endpoint = endpoint or os.getenv("MINIO_ENDPOINT", "minio:9000")
        self.access_key = access_key or os.getenv("MINIO_ACCESS_KEY", "minioadmin")
        self.secret_key = secret_key or os.getenv("MINIO_SECRET_KEY", "minioadmin")
        self.secure = secure

        self.client = Minio(
            self.endpoint,
            access_key=self.access_key,
            secret_key=self.secret_key,
            secure=self.secure,
        )

        # Create buckets on init
        for bucket in _ALL_BUCKETS:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)
                logger.info(f"Created MinIO bucket: {bucket}")

    # ── Upload ──

    def upload_file(self, bucket: str, object_name: str, file_path: str, content_type: str = "application/octet-stream") -> str:
        """Upload a local file to MinIO. Returns the object name."""
        self.client.fput_object(bucket, object_name, file_path, content_type=content_type)
        return object_name

    def upload_stream(self, bucket: str, object_name: str, data: BytesIO, length: int, content_type: str = "application/octet-stream") -> str:
        """Upload from a stream/bytes. Returns the object name."""
        self.client.put_object(bucket, object_name, data, length, content_type=content_type)
        return object_name

    # ── Download ──

    def download_file(self, bucket: str, object_name: str, dest_path: str) -> str:
        """Download an object to a local file. Returns the dest path."""
        self.client.fget_object(bucket, object_name, dest_path)
        return dest_path

    def download_to_tempfile(self, bucket: str, object_name: str, suffix: str = "") -> str:
        """Download to a temp file. Caller must clean up. Returns temp path."""
        fd, tmp_path = tempfile.mkstemp(suffix=suffix)
        os.close(fd)
        self.client.fget_object(bucket, object_name, tmp_path)
        return tmp_path

    def get_object_bytes(self, bucket: str, object_name: str) -> bytes:
        """Get entire object content as bytes (small files only)."""
        response = self.client.get_object(bucket, object_name)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    # ── Query ──

    def object_exists(self, bucket: str, object_name: str) -> bool:
        """Check if an object exists."""
        try:
            self.client.stat_object(bucket, object_name)
            return True
        except S3Error:
            return False

    def list_objects(self, bucket: str, prefix: str = "", recursive: bool = True) -> list[str]:
        """List object names under a prefix."""
        return [
            obj.object_name
            for obj in self.client.list_objects(bucket, prefix=prefix, recursive=recursive)
        ]

    # ── Delete ──

    def delete_object(self, bucket: str, object_name: str):
        """Delete a single object."""
        self.client.remove_object(bucket, object_name)

    def delete_prefix(self, bucket: str, prefix: str):
        """Delete all objects under a prefix (like rm -rf dir/)."""
        objects = self.list_objects(bucket, prefix=prefix)
        for obj_name in objects:
            self.client.remove_object(bucket, obj_name)


def get_storage_service() -> StorageService:
    """Factory — called once at app startup."""
    return StorageService()
