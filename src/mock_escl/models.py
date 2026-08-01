"""Domain models for scan jobs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pydantic import BaseModel


class JobState(str, Enum):
    """Lifecycle states of a scan job, mirroring the eSCL specification."""

    PENDING = "Pending"
    PROCESSING = "Processing"
    COMPLETED = "Completed"
    FAILED = "Failed"
    ABORTED = "Aborted"


class JobStateReason(str, Enum):
    """PWG ``JobStateReason`` values advertised in ``ScannerStatus``.

    Values mirror the PWG Common Job Ticket Model + eSCL specifics.
    """

    NONE = "None"
    PROCESSING = "Processing"
    JOB_COMPLETED_SUCCESSFULLY = "JobCompletedSuccessfully"
    JOB_ABORTED_BY_USER = "JobAbortedByUser"
    JOB_CANCELED = "JobCanceled"
    FAILED = "JobFailed"


def state_reason(state: JobState) -> JobStateReason:
    """Translate a JobState into the matching JobStateReason."""
    return {
        JobState.PENDING: JobStateReason.PROCESSING,
        JobState.PROCESSING: JobStateReason.PROCESSING,
        JobState.COMPLETED: JobStateReason.JOB_COMPLETED_SUCCESSFULLY,
        JobState.ABORTED: JobStateReason.JOB_ABORTED_BY_USER,
        JobState.FAILED: JobStateReason.FAILED,
    }.get(state, JobStateReason.NONE)


class InputSource(str, Enum):
    """Where the scan comes from."""

    PLATEN = "Platen"
    FEEDER = "Feeder"


@dataclass(frozen=True)
class ScanRegion:
    """A client-requested sub-area of the platen.

    Coordinates use ``units`` (defaults to ``escl:ThreeHundredthsOfInches``
    per PWG) and reference the platen origin (top-left).
    """

    x_offset: int = 0
    y_offset: int = 0
    width: int = 0
    height: int = 0
    units: str = "escl:ThreeHundredthsOfInches"


def region_pixel_size(
    region: ScanRegion,
    max_width_mm: int,
    max_height_mm: int,
    resolution_dpi: int,
) -> tuple[int, int]:
    """Convert a client-requested ScanRegion into pixel dimensions.

    ``ThreeHundredthsOfInches`` is the PWG default. The spec also allows
    ``escl:Microns`` but most clients use ThreeHundredthsOfInches.
    """
    mm_per_inch = 25.4
    if region.units == "escl:ThreeHundredthsOfInches":
        width_in = region.width / 300.0
        height_in = region.height / 300.0
    elif region.units == "escl:Microns":
        width_in = region.width / 25_400.0
        height_in = region.height / 25_400.0
    else:
        width_in = region.width / 300.0
        height_in = region.height / 300.0
    width_mm = width_in * mm_per_inch
    height_mm = height_in * mm_per_inch
    if width_mm <= 0:
        width_mm = max_width_mm
    if height_mm <= 0:
        height_mm = max_height_mm
    width_px = max(1, int((width_mm / mm_per_inch) * resolution_dpi))
    height_px = max(1, int((height_mm / mm_per_inch) * resolution_dpi))
    return width_px, height_px


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

    intent: str | None = None
    input_source: InputSource = InputSource.PLATEN
    duplex: bool = False
    scan_region: ScanRegion | None = None
    compression_factor: int = 25

    pages: list[bytes] = []
    """Rendered document bytes, one entry per page. Empty until the
    render finishes. For a single-page job, ``pages[0]`` is the only
    document. The ``image`` property below is the convenience accessor
    for the common single-page case."""
    error_message: str | None = None

    model_config = {"arbitrary_types_allowed": True}

    @property
    def image(self) -> bytes:
        """Convenience accessor returning the first rendered page.

        Most of the codebase was written assuming a single ``bytes``
        attribute. Now that we support multi-page, callers that only
        care about the first page can keep using ``job.image``.
        """
        return self.pages[0] if self.pages else b""


__all__ = [
    "InputSource",
    "JobState",
    "JobStateReason",
    "ScanJob",
    "ScanRegion",
    "region_pixel_size",
    "state_reason",
]
