"""Shared cancellation primitives for long-running worker pipelines."""


class JobCancelled(Exception):
    """Raised inside worker code when a job was cancelled by the user/admin."""

