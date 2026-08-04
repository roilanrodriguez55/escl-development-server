"""Job manager: stores scan jobs and renders synthetic documents."""

from __future__ import annotations

import asyncio
import io
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone

from PIL import Image, ImageDraw, ImageFont

from .config import ScannerConfig
from .models import InputSource, JobState, ScanJob, region_pixel_size

LOGGER = logging.getLogger("mock_escl.jobs")

JOB_TTL_SECONDS = 300
CLEANUP_INTERVAL_SECONDS = 30


class JobManager:
    """In-memory store for scan jobs.

    A real scanner would talk to firmware; we just synthesize an image
    based on the parameters the client sent in `ScanSettings`.
    """

    def __init__(self, config: ScannerConfig, seed: int | None = None) -> None:
        self.config = config
        self._jobs: dict[str, ScanJob] = {}
        self._abort_events: dict[str, asyncio.Event] = {}
        self._tasks: set[asyncio.Task] = set()
        self._cleanup_task: asyncio.Task | None = None
        self._seed = seed

    # ------------------------------------------------------------------ #
    #  Lifecycle
    # ------------------------------------------------------------------ #

    async def aclose(self) -> None:
        """Cancel background tasks. Called from the FastAPI lifespan."""
        if self._cleanup_task is not None and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        for task in list(self._tasks):
            if not task.done():
                task.cancel()
        self._tasks.clear()

    def schedule_process(self, job: ScanJob) -> asyncio.Task:
        """Schedule ``process(job)`` and keep a strong reference.

        Without the strong reference, the task could be garbage-collected
        mid-execution because the event loop only holds a weak ref to it.
        """
        task = asyncio.create_task(self.process(job))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------ #
    #  CRUD
    # ------------------------------------------------------------------ #

    def create(
        self,
        document_format: str,
        color_mode: str,
        resolution: int,
        intent: str | None = None,
        input_source: InputSource = InputSource.PLATEN,
        duplex: bool = False,
        scan_region=None,
        compression_factor: int = 25,
    ) -> ScanJob:
        job = ScanJob(
            job_id=str(uuid.uuid4()),
            created_at=datetime.now(timezone.utc),
            document_format=document_format,
            color_mode=color_mode,
            resolution=resolution,
            intent=intent,
            input_source=input_source,
            duplex=duplex,
            scan_region=scan_region,
            compression_factor=compression_factor,
        )
        self._jobs[job.job_id] = job
        self._abort_events[job.job_id] = asyncio.Event()
        return job

    def get(self, job_id: str) -> ScanJob | None:
        return self._jobs.get(job_id)

    def list_ids(self) -> list[str]:
        return list(self._jobs.keys())

    def delete(self, job_id: str) -> bool:
        event = self._abort_events.pop(job_id, None)
        if event is not None and not event.is_set():
            event.set()
        return self._jobs.pop(job_id, None) is not None

    def abort(self, job_id: str) -> bool:
        """Signal a job to abort. Returns False if no such job."""
        if job_id not in self._jobs:
            return False
        event = self._abort_events.get(job_id)
        if event is not None and not event.is_set():
            event.set()
        job = self._jobs[job_id]
        if job.state in (JobState.PENDING, JobState.PROCESSING):
            job.state = JobState.ABORTED
        return True

    # ------------------------------------------------------------------ #
    #  Processing
    # ------------------------------------------------------------------ #

    async def process(self, job: ScanJob) -> None:
        """Simulate the scanner working on a job."""
        await self._ensure_cleanup_task()
        await self._do_process(job)

    async def render_inline(self, job: ScanJob) -> None:
        """Render the job immediately inside the calling request.

        Used when ``delay_seconds <= 0`` to avoid the race where a fast
        client calls NextDocument before the asyncio task gets scheduled.
        """
        await self._ensure_cleanup_task()
        await self._do_process(job)

    async def _do_process(self, job: ScanJob) -> None:
        """Shared async body for the scheduled and inline paths."""
        LOGGER.info(
            "[job %s] entering Processing (waiting %.1fs, format=%s color=%s res=%d)",
            job.job_id,
            self.config.delay_seconds,
            job.document_format,
            job.color_mode,
            job.resolution,
        )
        job.state = JobState.PROCESSING
        event = self._abort_events.get(job.job_id)
        try:
            await self._interruptible_sleep(self.config.delay_seconds, event)
        except asyncio.CancelledError:
            LOGGER.info("[job %s] cancelled while waiting", job.job_id)
            job.state = JobState.ABORTED
            return

        if event is not None and event.is_set():
            LOGGER.info("[job %s] aborted before render", job.job_id)
            job.state = JobState.ABORTED
            return

        LOGGER.info(
            "[job %s] rendering %d page(s)…",
            job.job_id,
            max(1, self.config.pages_total),
        )
        render_start = datetime.now(timezone.utc)
        try:
            job.pages = self._render_pages(job, max(1, self.config.pages_total))
            job.pages_total = len(job.pages)
            job.completed_at = datetime.now(timezone.utc)
            render_ms = (job.completed_at - render_start).total_seconds() * 1000
            if event is not None and event.is_set():
                LOGGER.info(
                    "[job %s] aborted during render (%d pages, %.1f ms)",
                    job.job_id,
                    len(job.pages),
                    render_ms,
                )
                job.state = JobState.ABORTED
            else:
                # Stay in Processing: the pages exist but nothing has been
                # transferred yet. Flipping to Completed here makes macOS
                # (AirScanScanner) read the job as "finished, produced
                # nothing" and abort before it ever calls NextDocument.
                # NextDocument marks the job Completed once the last page
                # has actually been handed over.
                job.state = JobState.PROCESSING
                LOGGER.info(
                    "[job %s] rendered (%d page(s) %s in %.1f ms), awaiting transfer",
                    job.job_id,
                    len(job.pages),
                    job.document_format,
                    render_ms,
                )
        except Exception as exc:  # noqa: BLE001
            job.state = JobState.FAILED
            job.error_message = str(exc)
            LOGGER.exception("[job %s] render failed: %s", job.job_id, exc)

    async def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            try:
                self._cleanup_task = asyncio.create_task(self._cleanup_loop())
                self._tasks.add(self._cleanup_task)
                self._cleanup_task.add_done_callback(self._tasks.discard)
            except RuntimeError:
                # No event loop yet (tests that bypass lifespan).
                self._cleanup_task = None

    async def _interruptible_sleep(self, seconds: float, event: asyncio.Event | None) -> None:
        """Sleep with abort polling, split into 100ms slices."""
        if seconds <= 0:
            return
        elapsed = 0.0
        slice_ = 0.1
        while elapsed < seconds:
            if event is not None and event.is_set():
                return
            await asyncio.sleep(min(slice_, seconds - elapsed))
            elapsed += slice_

    async def _cleanup_loop(self) -> None:
        """Periodically evict jobs older than ``JOB_TTL_SECONDS``."""
        try:
            while True:
                await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)
                self._evict_expired()
        except asyncio.CancelledError:
            return

    def _evict_expired(self) -> None:
        now = datetime.now(timezone.utc)
        expired = [
            jid
            for jid, job in self._jobs.items()
            if job.completed_at is not None
            and (now - job.completed_at).total_seconds() > JOB_TTL_SECONDS
        ]
        for jid in expired:
            LOGGER.info("[job %s] evicting (TTL %ds)", jid, JOB_TTL_SECONDS)
            self.delete(jid)

    # ------------------------------------------------------------------ #
    #  Rendering
    # ------------------------------------------------------------------ #

    def _render_scan(self, job: ScanJob) -> bytes:
        """Render a single-page document (convenience over ``_render_pages``)."""
        return self._render_pages(job, 1)[0]

    def _render_pages(self, job: ScanJob, count: int) -> list[bytes]:
        """Render ``count`` pages, one per ADF page.

        For PDF, the result is a single multi-page PDF; for raster
        formats, each entry is a separate image with the page number
        stamped on it. When ``job.duplex`` is true, each sheet
        contributes two pages (front + back) and the back side gets
        different content drawn from a side-specific pool.
        """
        sides_per_sheet = 2 if job.duplex else 1
        total_pages = count * sides_per_sheet

        LOGGER.debug(
            "[job %s] _render_pages format=%s color=%s res=%d intent=%s "
            "sheets=%d sides_per_sheet=%d total_pages=%d",
            job.job_id,
            job.document_format,
            job.color_mode,
            job.resolution,
            job.intent or "(none)",
            count,
            sides_per_sheet,
            total_pages,
        )
        if job.document_format == "application/pdf":
            return [self._render_text_pdf(job, total_pages, duplex=job.duplex)]
        return [
            self._render_color_image(
                job,
                page_index=i,
                page_total=total_pages,
                side="back" if job.duplex and i % 2 == 0 else "front",
            )
            for i in range(1, total_pages + 1)
        ]

    # ----- color image path ------------------------------------------ #

    def _render_color_image(
        self,
        job: ScanJob,
        page_index: int = 1,
        page_total: int = 1,
        side: str = "front",
    ) -> bytes:
        width_px, height_px = self._compute_pixel_size(job)

        mode, bg, fg = self._pil_mode_for(job.color_mode)
        image = Image.new(mode, (width_px, height_px), bg)
        draw = ImageDraw.Draw(image)

        font_size = max(8, int(job.resolution / 4))
        font = self._load_font(font_size)
        fill = fg

        margin = max(4, int(job.resolution / 2))
        margin = min(margin, max(2, min(width_px, height_px) // 6))
        if margin * 2 < min(width_px, height_px):
            draw.rectangle(
                (margin, margin, width_px - margin, height_px - margin),
                outline=fill,
                width=4,
            )

        cursor_y = margin
        lines = self._image_text_lines(
            job, page_index=page_index, page_total=page_total, side=side
        )
        for i, text in enumerate(lines):
            line_y = cursor_y + i * (font_size + int(font_size / 2))
            if line_y > height_px - margin:
                break
            draw.text((margin * 2, line_y), text, fill=fill, font=font)

        if mode == "L" and job.color_mode == "BlackAndWhite1":
            image = image.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

        output = io.BytesIO()
        fmt = self._extension_for(job.document_format)
        save_kwargs: dict = {}
        if fmt == "JPEG":
            save_kwargs["quality"] = job.compression_factor
        image.save(output, format=fmt, **save_kwargs)
        return output.getvalue()

    @staticmethod
    def _pil_mode_for(color_mode: str) -> tuple[str, int | str, int | str]:
        """Map eSCL color modes onto Pillow mode + background + foreground."""
        if color_mode == "Grayscale8":
            return "L", 255, 0
        if color_mode == "BlackAndWhite1":
            return "L", 255, 0
        return "RGB", "white", "black"

    def _image_text_lines(
        self,
        job: ScanJob,
        page_index: int = 1,
        page_total: int = 1,
        side: str = "front",
    ) -> list[str]:
        """Build the header + body text that gets stamped on the image."""
        timestamp = self._deterministic_timestamp(job)
        header = [
            f"Mock eSCL Scanner — {self.config.name}",
            f"Generated: {timestamp.isoformat(timespec='seconds')}",
            f"Format:    {job.document_format}",
            f"Color:     {job.color_mode}",
            f"Resolution: {job.resolution} DPI",
        ]
        if page_total > 1 or side == "back":
            label = f"Page:      {page_index} of {page_total} ({side})"
            header.append(label)
        header.append("")
        body_prefix = "Simulated scanned line"
        body_count = 12
        if self._seed is not None:
            rng = random.Random(
                (self._seed ^ hash(job.job_id)) ^ (page_index * 9973)
            )
            front_pool = [
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit.",
                "The quick brown fox jumps over the lazy dog.",
                "Driverless scanning via eSCL/AirScan and WSD.",
                "Mock data is generated locally — no real documents involved.",
                "Pellentesque habitant morbi tristique senectus et netus.",
                "Vestibulum ante ipsum primis in faucibus orci luctus.",
            ]
            back_pool = [
                "Continued from the front side of this page.",
                "Reverse-side content drawn from a separate pool.",
                "Duplex scanning alternates front and back sheets.",
                "The back side usually has different content.",
                "Two pages per physical sheet on duplex-enabled devices.",
                "AirScan and Mopria clients support duplex via <scan:Duplex>.",
            ]
            pool = back_pool if side == "back" else front_pool
            body = [f"{body_prefix} {i}: {rng.choice(pool)}" for i in range(1, body_count + 1)]
        else:
            suffix = "back side" if side == "back" else "front side"
            body = [
                f"{body_prefix} {i} ({suffix}): "
                "this content was generated locally by the mock eSCL server."
                for i in range(1, body_count + 1)
            ]
        return header + body

    def _deterministic_timestamp(self, job: ScanJob) -> datetime:
        """Return a reproducible timestamp when --seed is enabled."""
        if self._seed is None:
            return datetime.now()
        epoch = datetime(2024, 1, 1)
        return epoch + timedelta(seconds=(self._seed + hash(job.job_id)) % 86_400)

    # ----- PDF text document path ------------------------------------ #

    def _render_text_pdf(self, job: ScanJob, page_total: int = 1, duplex: bool = False) -> bytes:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas as rl_canvas

        buf = io.BytesIO()
        page_w, page_h = A4
        c = rl_canvas.Canvas(buf, pagesize=A4)

        if job.intent == "Document":
            font_regular = "Times-Roman"
            font_bold = "Times-Bold"
        else:
            font_regular = "Helvetica"
            font_bold = "Helvetica-Bold"

        c.setTitle("Mock eSCL scan")
        c.setAuthor(self.config.name)
        c.setSubject(
            f"{job.document_format} • {job.resolution} DPI • "
            f"compression {job.compression_factor}"
        )
        c.setCreator(_server_header(self.config))

        margin = 20
        mm_to_pt = 72.0 / 25.4
        margin_pt = margin * mm_to_pt
        page_w_pt = page_w
        page_h_pt = page_h

        for page_index in range(1, max(1, page_total) + 1):
            side = "back" if duplex and page_index % 2 == 0 else "front"

            c.setFont(font_bold, 11)
            c.drawString(
                margin_pt,
                page_h_pt - margin_pt,
                f"Mock eSCL Scanner — {self.config.name}",
            )
            c.setFont(font_regular, 8)
            c.drawString(
                margin_pt,
                page_h_pt - margin_pt - 12,
                f"Scan {job.job_id[:8]} • {job.resolution} DPI • "
                f"{job.color_mode} • {job.document_format}",
            )
            if page_total > 1 or duplex:
                c.drawString(
                    margin_pt,
                    page_h_pt - margin_pt - 22,
                    f"Page {page_index} of {page_total} ({side})",
                )
            c.line(
                margin_pt,
                page_h_pt - margin_pt - 16,
                page_w_pt - margin_pt,
                page_h_pt - margin_pt - 16,
            )

            c.setFont(font_regular, 10)
            y = page_h_pt - margin_pt - 28
            leading = 13
            body_lines = self._pdf_text_lines(job, page_index=page_index)
            max_lines_per_page = max(
                1,
                int((page_h_pt - 2 * margin_pt - 50) // leading),
            )
            line_no = 0
            for chunk in body_lines:
                if y < margin_pt + 30:
                    c.showPage()
                    c.setFont(font_regular, 10)
                    y = page_h_pt - margin_pt
                    line_no = 0
                c.drawString(margin_pt, y, chunk)
                y -= leading
                line_no += 1
                if line_no >= max_lines_per_page * 4:
                    break

            c.setFont(font_regular, 7)
            timestamp = self._deterministic_timestamp(job)
            c.drawString(
                margin_pt,
                margin_pt,
                f"Generated: {timestamp.isoformat(timespec='seconds')}",
            )
            c.drawRightString(
                page_w_pt - margin_pt,
                margin_pt,
                f"Page {page_index} of {page_total}",
            )
            c.showPage()

        c.save()
        return buf.getvalue()

    def _pdf_text_lines(self, job: ScanJob, page_index: int = 1) -> list[str]:
        """Generate text content for the PDF page (B&W document simulation)."""
        if self._seed is not None:
            rng = random.Random(
                ((self._seed * 31) ^ hash(job.job_id)) ^ (page_index * 7919)
            )
        else:
            rng = random.Random()
        paragraphs = [
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
            "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.",
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris "
            "nisi ut aliquip ex ea commodo consequat.",
            "Duis aute irure dolor in reprehenderit in voluptate velit esse "
            "cillum dolore eu fugiat nulla pariatur.",
            "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui "
            "officia deserunt mollit anim id est laborum.",
            "Curabitur pretium tincidunt lacus. Nulla gravida orci a odio. "
            "Nullam varius, turpis et commodo pharetra, est eros bibendum elit.",
            "Pellentesque habitant morbi tristique senectus et netus et malesuada "
            "fames ac turpis egestas.",
            "Vestibulum ante ipsum primis in faucibus orci luctus et ultrices "
            "posuere cubilia Curae.",
            "Praesent dapibus. Duis viverra diam eu erat. Praesent placerat "
            "mauris vitae nibh.",
        ]
        out: list[str] = []
        for para in paragraphs:
            # Break paragraphs into wrapped lines of ~72 chars.
            words = para.split()
            line = ""
            for w in words:
                if len(line) + len(w) + 1 > 72:
                    out.append(line.rstrip())
                    line = w
                else:
                    line = f"{line} {w}".strip()
            if line:
                out.append(line.rstrip())
            out.append("")
        # Total ~60 lines; client-requested compression_factor maps to a
        # slightly different paragraph count for variety under --seed.
        if self._seed is not None:
            desired = max(20, min(80, job.compression_factor))
            if len(out) > desired:
                out = out[:desired]
        return out

    # ------------------------------------------------------------------ #
    #  Geometry / font helpers
    # ------------------------------------------------------------------ #

    def _compute_pixel_size(self, job: ScanJob) -> tuple[int, int]:
        if job.scan_region is not None:
            return region_pixel_size(
                job.scan_region,
                self.config.max_width_mm,
                self.config.max_height_mm,
                job.resolution,
            )
        width_px = max(1, int((self.config.max_width_mm / 25.4) * job.resolution))
        height_px = max(1, int((self.config.max_height_mm / 25.4) * job.resolution))
        return width_px, height_px

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
            return "PDF"
        return "PNG"


def _server_header(config: ScannerConfig) -> str:
    return f"{config.manufacturer} {config.model}"


__all__ = [
    "JOB_TTL_SECONDS",
    "CLEANUP_INTERVAL_SECONDS",
    "JobManager",
]
