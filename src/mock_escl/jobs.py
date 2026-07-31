"""Job manager: stores scan jobs and renders synthetic documents."""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from datetime import datetime, timezone

from PIL import Image, ImageDraw, ImageFont

from .config import ScannerConfig
from .models import JobState, ScanJob


class JobManager:
    """In-memory store for scan jobs.

    A real scanner would talk to firmware; we just synthesize an image
    based on the parameters the client sent in `ScanSettings`.
    """

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self._jobs: dict[str, ScanJob] = {}

    def create(
        self,
        document_format: str,
        color_mode: str,
        resolution: int,
    ) -> ScanJob:
        job = ScanJob(
            job_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            document_format=document_format,
            color_mode=color_mode,
            resolution=resolution,
        )
        self._jobs[job.job_id] = job
        return job

    def get(self, job_id: str) -> ScanJob | None:
        return self._jobs.get(job_id)

    def list_ids(self) -> list[str]:
        return list(self._jobs.keys())

    def delete(self, job_id: str) -> bool:
        return self._jobs.pop(job_id, None) is not None

    async def process(self, job: ScanJob) -> None:
        """Simulate the scanner working on a job."""
        job.state = JobState.PROCESSING
        await asyncio.sleep(self.config.delay_seconds)

        try:
            job.image = self._render_scan(job)
            job.pages_total = 1
            job.completed_at = datetime.now(timezone.utc)
            job.state = JobState.COMPLETED
        except Exception as exc:
            job.state = JobState.FAILED
            job.error_message = str(exc)

    def _render_scan(self, job: ScanJob) -> bytes:
        mm_per_inch = 25.4
        width_px = max(
            1,
            int((self.config.max_width_mm / mm_per_inch) * job.resolution),
        )
        height_px = max(
            1,
            int((self.config.max_height_mm / mm_per_inch) * job.resolution),
        )

        if job.color_mode == "Grayscale8":
            image = Image.new("L", (width_px, height_px), 255)
        else:
            image = Image.new("RGB", (width_px, height_px), "white")

        draw = ImageDraw.Draw(image)

        font_size = max(12, int(job.resolution / 4))
        font = self._load_font(font_size)
        fill = 0 if image.mode == "L" else "black"

        margin = max(20, int(job.resolution / 2))

        draw.rectangle(
            (margin, margin, width_px - margin, height_px - margin),
            outline=fill,
            width=4,
        )

        cursor_y = margin * 2
        draw.text(
            (margin * 2, cursor_y),
            f"Mock eSCL Scanner — {self.config.name}",
            fill=fill,
            font=font,
        )
        cursor_y += font_size + int(font_size / 2)

        draw.text(
            (margin * 2, cursor_y),
            f"Generated: {datetime.now().isoformat(timespec='seconds')}",
            fill=fill,
            font=font,
        )
        cursor_y += font_size + int(font_size / 2)

        draw.text(
            (margin * 2, cursor_y),
            f"Format:   {job.document_format}",
            fill=fill,
            font=font,
        )
        cursor_y += font_size + int(font_size / 2)

        draw.text(
            (margin * 2, cursor_y),
            f"Color:    {job.color_mode}",
            fill=fill,
            font=font,
        )
        cursor_y += font_size + int(font_size / 2)

        draw.text(
            (margin * 2, cursor_y),
            f"Resolution: {job.resolution} DPI",
            fill=fill,
            font=font,
        )
        cursor_y += font_size + int(font_size / 2)

        draw.line(
            (
                margin * 2,
                cursor_y,
                width_px - margin * 2,
                cursor_y,
            ),
            fill=fill,
            width=2,
        )
        cursor_y += int(font_size / 2)

        for index in range(1, 12):
            line_y = cursor_y + index * (font_size + int(font_size / 3))
            if line_y > height_px - margin * 2:
                break
            draw.text(
                (margin * 2, line_y),
                f"Simulated scanned line {index}: "
                f"this content was generated locally by the mock eSCL server.",
                fill=fill,
                font=font,
            )

        output = io.BytesIO()
        ext = self._extension_for(job.document_format)
        image.save(output, format=ext)
        return output.getvalue()

    @staticmethod
    def _load_font(size: int) -> ImageFont.ImageFont:
        """Return the first TTF font we can find on the host."""
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/TTF/DejaVuSans.ttf",
            "/Library/Fonts/Arial.ttf",
            "/Library/Fonts/Arial Bold.ttf",
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        for path in candidates:
            if os.path.exists(path):
                return ImageFont.truetype(path, size=size)
        return ImageFont.load_default()

    @staticmethod
    def _extension_for(document_format: str) -> str:
        """Map eSCL MIME types to PIL format names."""
        if "jpeg" in document_format:
            return "JPEG"
        if "tiff" in document_format:
            return "TIFF"
        if "pdf" in document_format:
            return "PNG"
        return "PNG"


__all__ = ["JobManager"]