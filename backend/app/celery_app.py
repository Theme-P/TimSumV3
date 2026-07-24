"""
Celery application configuration for TimSumV3.
Uses Redis as broker and result backend.
"""
import os
from celery import Celery
from dotenv import load_dotenv
from app.core.runtime_validation import validate_runtime_configuration

load_dotenv()
# Workers and beat are independent processes; they must fail closed under the
# same production invariants as the FastAPI startup path.
validate_runtime_configuration()

REDIS_URL = os.getenv("REDIS_URL", "redis://redis:6379/0")
WORKER_CONCURRENCY = int(os.getenv("CELERY_WORKER_CONCURRENCY", "1"))
WORKER_PREFETCH_MULTIPLIER = int(os.getenv("CELERY_WORKER_PREFETCH_MULTIPLIER", "1"))
WORKER_MAX_TASKS_PER_CHILD = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "0"))

celery_app = Celery(
    "timsumv3",
    broker=REDIS_URL,
    backend=REDIS_URL,
    include=["app.tasks.transcription", "app.tasks.summary", "app.tasks.maintenance"],
)

celery_app.conf.update(
    # Serialization
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",

    # Worker settings — one task at a time per GPU by default.
    # Prefetch=1 prevents one worker from reserving many long GPU jobs.
    worker_concurrency=WORKER_CONCURRENCY,
    worker_prefetch_multiplier=WORKER_PREFETCH_MULTIPLIER,

    # Task settings
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_default_queue="transcription",
    task_routes={
        "transcription.process_audio": {"queue": "transcription"},
        "summary.process_next_chunk": {"queue": "summary"},
        "summary.finalize": {"queue": "summary"},
        "maintenance.*": {"queue": "maintenance"},
    },

    # Result expiry
    result_expires=3600,

    # Timezone
    timezone="Asia/Bangkok",
    beat_schedule={
        "reconcile-durable-workflows": {
            "task": "maintenance.reconcile",
            "schedule": 60.0,
            "options": {"queue": "maintenance"},
        },
    },
)

if WORKER_MAX_TASKS_PER_CHILD > 0:
    celery_app.conf.worker_max_tasks_per_child = WORKER_MAX_TASKS_PER_CHILD
