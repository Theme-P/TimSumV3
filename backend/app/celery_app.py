"""
Celery application configuration for TimSumV3.
Uses Redis as broker and result backend.
"""
import os
from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")

celery_app = Celery(
    "timsumv3",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.transcription"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Worker settings — one task at a time (GPU bound)
    worker_concurrency=1,
    worker_prefetch_multiplier=1,

    # Task settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,

    # Result expiry
    result_expires=3600,

    # Timezone
    timezone="Asia/Bangkok",
)
