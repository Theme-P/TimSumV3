"""
Shared MongoDB client singleton for Celery worker processes.
Prevents creating a new MongoClient on every task execution.
"""
import os
from pymongo import MongoClient
from pymongo.database import Database

_client: MongoClient | None = None


def get_worker_db() -> Database:
    """Get MongoDB database for Celery worker (singleton connection)."""
    global _client
    if _client is None:
        uri = os.getenv("MONGO_CONNECTION_STRING", "mongodb://mongo:27017")
        _client = MongoClient(uri, maxPoolSize=10)
    db_name = os.getenv("MONGO_DB_NAME", "timsumv3")
    return _client[db_name]
