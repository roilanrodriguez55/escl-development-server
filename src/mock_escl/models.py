"""Domain models for scan jobs."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class JobState(str, Enum):
    """Lifecycle states of a scan job, mirroring the eSCL specification."""

    PENDING = "Pending"
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    FAILED = "Failed"
    ABORTED = "Aborted"


class ScanJob(BaseModel):
    """In-memory representation of a single scan request."""

    job_id: str
    state: JobState = JobState.PENDING
    created_at: datetime
    completed_at: datetime | None = None

    pages_total: int = 1
    pages_delivered: int = 0

    document_format: str = "image/png"
    color_mode: str = "RGB24"
    resolution: int = 300

    image: bytes = b""
    error_message: str | None = None

    model_config = {"arbitrary_types_allowed": True}


__all__ = ["JobState", "ScanJob", "Field"]