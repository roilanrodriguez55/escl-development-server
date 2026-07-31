"""FastAPI application implementing the eSCL/AirScan HTTP surface."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import socket
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

from .config import ScannerConfig
from .jobs import JobManager
from .models import JobState, ScanJob

LOGGER = logging.getLogger("mock_escl.server")

ESCL_NAMESPACE = "http://schemas.hp.com/imaging/escl/2011/05/03"

CAPTURE_DIR = Path(os.environ.get("MOCK_ESCL_CAPTURE_DIR", "/tmp/mock-escl-captures"))
LAST_REQUESTS: list[dict] = []
MAX_CAPTURED = 50


def _xml_header() -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'


def _capture_request(request: Request, body: bytes | None) -> None:
    """Record the most recent requests so we can diagnose client behaviour."""
    if not CAPTURE_DIR.exists() and not _safe_mkdir(CAPTURE_DIR):
        return

    entry = {
        "ts": time.time(),
        "method": request.method,
        "path": request.url.path,
        "query": dict(request.query_params),
        "content_type": request.headers.get("Content-Type", ""),
        "content_length": request.headers.get("Content-Length", ""),
        "body_preview": (body or b"")[:4096].decode("utf-8", errors="replace"),
    }
    LAST_REQUESTS.append(entry)
    if len(LAST_REQUESTS) > MAX_CAPTURED:
        del LAST_REQUESTS[: len(LAST_REQUESTS) - MAX_CAPTURED]

    try:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
        slug = request.url.path.replace("/", "_").strip("_") or "root"
        path = CAPTURE_DIR / f"{stamp}-{request.method}-{slug}.body"
        if body is not None:
            path.write_bytes(body)
        meta = CAPTURE_DIR / f"{stamp}-{request.method}-{slug}.meta"
        meta.write_text(
            "\n".join(
                f"{k}: {v}"
                for k, v in (
                    ("Method", request.method),
                    ("Path", request.url.path),
                    ("Query", str(dict(request.query_params))),
                    ("Content-Type", request.headers.get("Content-Type", "")),
                    ("Content-Length", request.headers.get("Content-Length", "")),
                )
            )
            + "\n"
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Failed to capture request: %s", exc)


def _safe_mkdir(path: Path) -> bool:
    try:
        path.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:  # noqa: BLE001
        return False


def _xml_escape(value: str) -> str:
    """Escape characters that would otherwise break the XML."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def _lan_ip(preferred: str) -> str:
    """Return a routable IPv4 address for use in URLs handed to clients.

    Bind addresses like ``0.0.0.0`` and ``::`` are not valid for clients; they
    need the actual address of an interface on the LAN. We try the configured
    ``host`` first, then a UDP socket trick to learn the outbound interface,
    then fall back to ``127.0.0.1``.
    """
    if preferred and preferred not in {"0.0.0.0", "::", "[::]"}:
        return preferred

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def _server_url(config: ScannerConfig, request_host: str | None = None) -> str:
    """Build the externally-visible URL clients should use.

    Preference order:
    1. The ``Host`` header sent by the client (the most accurate signal).
    2. The configured ``host`` if it is a routable IP.
    3. The detected LAN IP.
    """
    if request_host:
        return f"http://{request_host}"

    host = _lan_ip(config.host)
    return f"http://{host}:{config.port}"


def capabilities_xml(config: ScannerConfig, request_host: str | None = None) -> bytes:
    """Build the ScannerCapabilities XML response.

    Structure mirrors the Mopria eSCL specification so that strict clients
    (sane-airscan, Apple Image Capture, Windows Scanner Service) parse it.
    """
    base_url = _server_url(config, request_host)
    admin_uri = _xml_escape(base_url)
    icon_uri = _xml_escape(f"{base_url}/eSCL/ScannerIcon")
    make_and_model = _xml_escape(f"{config.manufacturer} {config.model}")
    serial = _xml_escape(config.serial)
    uuid_val = _xml_escape(config.uuid)

    resolutions_xml = "".join(
        f"""        <eSCL:DiscreteResolution>
          <eSCL:XResolution>{r}</eSCL:XResolution>
          <eSCL:YResolution>{r}</eSCL:YResolution>
        </eSCL:DiscreteResolution>"""
        for r in config.resolutions
    )

    color_modes_xml = "".join(
        f"        <eSCL:ColorMode>{_xml_escape(m)}</eSCL:ColorMode>"
        for m in config.color_modes
    )

    document_formats_xml = """          <eSCL:DocumentFormat>image/jpeg</eSCL:DocumentFormat>
          <eSCL:DocumentFormat>image/png</eSCL:DocumentFormat>
          <eSCL:DocumentFormat>image/tiff</eSCL:DocumentFormat>
          <eSCL:DocumentFormat>application/pdf</eSCL:DocumentFormat>"""

    max_res = max(config.resolutions)

    platen_block = f"""    <eSCL:Platen>
      <eSCL:PlatenInputCaps>
        <eSCL:MinWidth>1</eSCL:MinWidth>
        <eSCL:MaxWidth>{config.max_width_mm}</eSCL:MaxWidth>
        <eSCL:MinHeight>1</eSCL:MinHeight>
        <eSCL:MaxHeight>{config.max_height_mm}</eSCL:MaxHeight>
        <eSCL:MaxXResolution>{max_res}</eSCL:MaxXResolution>
        <eSCL:MaxYResolution>{max_res}</eSCL:MaxYResolution>
        <eSCL:MaxOpticalXResolution>{max_res}</eSCL:MaxOpticalXResolution>
        <eSCL:MaxOpticalYResolution>{max_res}</eSCL:MaxOpticalYResolution>
        <eSCL:SupportedResolutions>
          <eSCL:DiscreteResolutions>
{resolutions_xml}
          </eSCL:DiscreteResolutions>
        </eSCL:SupportedResolutions>
        <eSCL:ColorModes>
{color_modes_xml}
        </eSCL:ColorModes>
        <eSCL:DocumentFormats>
{document_formats_xml}
        </eSCL:DocumentFormats>
      </eSCL:PlatenInputCaps>
    </eSCL:Platen>"""

    feeder_block = ""
    if config.adf_enabled:
        feeder_block = f"""
    <eSCL:Feeder>
      <eSCL:FeederInputCaps>
        <eSCL:MinWidth>1</eSCL:MinWidth>
        <eSCL:MaxWidth>{config.max_width_mm}</eSCL:MaxWidth>
        <eSCL:MinHeight>1</eSCL:MinHeight>
        <eSCL:MaxHeight>{config.max_height_mm}</eSCL:MaxHeight>
        <eSCL:MaxXResolution>{max_res}</eSCL:MaxXResolution>
        <eSCL:MaxYResolution>{max_res}</eSCL:MaxYResolution>
        <eSCL:MaxOpticalXResolution>{max_res}</eSCL:MaxOpticalXResolution>
        <eSCL:MaxOpticalYResolution>{max_res}</eSCL:MaxOpticalYResolution>
        <eSCL:SupportedResolutions>
          <eSCL:DiscreteResolutions>
{resolutions_xml}
          </eSCL:DiscreteResolutions>
        </eSCL:SupportedResolutions>
        <eSCL:ColorModes>
{color_modes_xml}
        </eSCL:ColorModes>
        <eSCL:DocumentFormats>
{document_formats_xml}
        </eSCL:DocumentFormats>
      </eSCL:FeederInputCaps>
    </eSCL:Feeder>"""

    return (
        _xml_header()
        + f"""<eSCL:ScannerCapabilities xmlns:eSCL="{ESCL_NAMESPACE}" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <eSCL:Version>2.63</eSCL:Version>
  <eSCL:interfaceVersion>2.63</eSCL:interfaceVersion>
  <eSCL:MakeAndModel>{make_and_model}</eSCL:MakeAndModel>
  <eSCL:makeAndModel>{make_and_model}</eSCL:makeAndModel>
  <eSCL:SerialNumber>{serial}</eSCL:SerialNumber>
  <eSCL:serialNumber>{serial}</eSCL:serialNumber>
  <eSCL:UUID>{uuid_val}</eSCL:UUID>
  <eSCL:AdminURI>{admin_uri}</eSCL:AdminURI>
  <eSCL:IconURI>{icon_uri}</eSCL:IconURI>
  <eSCL:ScannerState>Idle</eSCL:ScannerState>
{platen_block}{feeder_block}
</eSCL:ScannerCapabilities>
""".encode("utf-8")
    )


def scanner_status_xml(jobs: JobManager) -> bytes:
    """Build the ScannerStatus XML listing jobs with their real state."""
    job_entries = "".join(
        f"""<eSCL:JobInfo>
        <eSCL:JobUri>/eSCL/ScanJobs/{job_id}</eSCL:JobUri>
        <eSCL:JobUuid>{job_id}</eSCL:JobUuid>
        <eSCL:JobState>{jobs.get(job_id).state.value}</eSCL:JobState>
      </eSCL:JobInfo>"""
        for job_id in jobs.list_ids()
    )

    return (
        _xml_header()
        + f"""<eSCL:ScannerStatus xmlns:eSCL="{ESCL_NAMESPACE}">
  <eSCL:Version>2.63</eSCL:Version>
  <eSCL:ScannerState>Idle</eSCL:ScannerState>
  <eSCL:Jobs>{job_entries}</eSCL:Jobs>
</eSCL:ScannerStatus>
""".encode("utf-8")
    )


def scan_status_xml(job: ScanJob) -> bytes:
    """Build the per-job status XML."""
    return (
        _xml_header()
        + f"""<eSCL:ScanJobStatus xmlns:eSCL="{ESCL_NAMESPACE}">
  <eSCL:Version>2.63</eSCL:Version>
  <eSCL:JobUuid>{job.job_id}</eSCL:JobUuid>
  <eSCL:JobState>{job.state.value}</eSCL:JobState>
  <eSCL:JobCreationDate>{job.created_at.isoformat()}</eSCL:JobCreationDate>
  <eSCL:Pages>{job.pages_total}</eSCL:Pages>
  <eSCL:ImagesToTransfer>{job.pages_total - job.pages_delivered}</eSCL:ImagesToTransfer>
  <eSCL:ImagesTransferred>{job.pages_delivered}</eSCL:ImagesTransferred>
</eSCL:ScanJobStatus>
""".encode("utf-8")
    )


def scan_image_info_xml(job: ScanJob) -> bytes:
    """Build a ScanImageInfo XML describing the document the server will hand out.

    Some clients (notably sane-airscan) ask for this endpoint before pulling
    NextDocument. Returning valid XML is enough; the actual data bytes come
    from NextDocument.
    """
    return (
        _xml_header()
        + f"""<eSCL:ScanImageInfo xmlns:eSCL="{ESCL_NAMESPACE}">
  <eSCL:Version>2.63</eSCL:Version>
  <eSCL:JobUuid>{job.job_id}</eSCL:JobUuid>
  <eSCL:Images>{job.pages_total}</eSCL:Images>
  <eSCL:Image>
    <eSCL:DocumentFormat>{job.document_format}</eSCL:DocumentFormat>
    <eSCL:ColorMode>{job.color_mode}</eSCL:ColorMode>
    <eSCL:XResolution>{job.resolution}</eSCL:XResolution>
    <eSCL:YResolution>{job.resolution}</eSCL:YResolution>
    <eSCL:Width>{int((210 / 25.4) * job.resolution)}</eSCL:Width>
    <eSCL:Height>{int((297 / 25.4) * job.resolution)}</eSCL:Height>
  </eSCL:Image>
</eSCL:ScanImageInfo>
""".encode("utf-8")
    )


def _parse_scan_settings(body: bytes, config: ScannerConfig) -> tuple[str, str, int]:
    """Extract the format, color mode and resolution from a ScanSettings XML body.

    Lightweight substring matching — adequate for a mock. The XML sent by
    clients can use either `eSCL:` or `scan:` prefixes; we match both.
    """
    document_format = config.default_format
    color_mode = config.color_modes[0] if config.color_modes else "RGB24"
    resolution = config.resolutions[len(config.resolutions) // 2]

    if b"image/jpeg" in body or b"<scan:ImageFormat>JPEG" in body:
        document_format = "image/jpeg"
    elif b"image/tiff" in body or b"<scan:ImageFormat>TIFF" in body:
        document_format = "image/tiff"
    elif b"image/png" in body or b"<scan:ImageFormat>PNG" in body:
        document_format = "image/png"
    elif b"application/pdf" in body or b"<scan:ImageFormat>PDF" in body:
        document_format = "application/pdf"

    if b"Grayscale8" in body:
        color_mode = "Grayscale8"
    elif b"BlackAndWhite1" in body:
        color_mode = "BlackAndWhite1"
    elif b"RGB24" in body:
        color_mode = "RGB24"

    for r in config.resolutions:
        token_options = (
            f"<eSCL:XResolution>{r}</eSCL:XResolution>".encode(),
            f"<scan:XResolution>{r}</scan:XResolution>".encode(),
            f"<scan:Resolution>{r}</scan:Resolution>".encode(),
        )
        if any(tok in body for tok in token_options):
            resolution = r
            break

    return document_format, color_mode, resolution


def _absolute_base_url(request: Request, config: ScannerConfig) -> str:
    """Build a URL that points at the scanner, suitable for Location headers."""
    host = request.headers.get("Host")
    if host:
        return f"http://{host}"
    return _server_url(config)


def create_app(config: ScannerConfig) -> FastAPI:
    """Build a FastAPI app bound to a JobManager configured by ``config``."""
    jobs = JobManager(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        LOGGER.info("Mock eSCL scanner started on %s:%s", config.host, config.port)
        if CAPTURE_DIR:
            LOGGER.info("Capturing payloads to: %s", CAPTURE_DIR)
        try:
            yield
        finally:
            LOGGER.info("Mock eSCL scanner stopped")

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        body: bytes | None = None
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()
        _capture_request(request, body)
        start = datetime.now()
        response = await call_next(request)
        duration_ms = (datetime.now() - start).total_seconds() * 1000
        client = request.client.host if request.client else "?"
        LOGGER.info(
            "%s %s %s -> %s (%.1f ms)",
            client,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response

    @app.get("/eSCL/ScannerCapabilities")
    async def scanner_capabilities(request: Request) -> Response:
        host = request.headers.get("Host")
        return Response(
            content=capabilities_xml(config, host),
            media_type="application/xml",
            headers={"Server": "Mock eSCL"},
        )

    @app.get("/eSCL/ScannerStatus")
    async def scanner_status() -> Response:
        return Response(
            content=scanner_status_xml(jobs),
            media_type="application/xml",
            headers={"Server": "Mock eSCL"},
        )

    @app.get("/eSCL/ScannerIcon")
    async def scanner_icon() -> Response:
        empty_icon = bytes.fromhex(
            "89504e470d0a1a0a0000000d49484452000000010000000108060000001f"
            "15c4890000000d49444154789c636000010000000500017a9d3a3e00000000"
            "49454e44ae426082"
        )
        return Response(
            content=empty_icon,
            media_type="image/png",
            headers={"Server": "Mock eSCL"},
        )

    @app.post("/eSCL/ScanJobs")
    async def create_scan_job(request: Request) -> Response:
        body = await request.body()
        LOGGER.info("Received scan settings (%d bytes)", len(body))

        document_format, color_mode, resolution = _parse_scan_settings(body, config)

        job = jobs.create(
            document_format=document_format,
            color_mode=color_mode,
            resolution=resolution,
        )

        asyncio.create_task(jobs.process(job))

        base = _absolute_base_url(request, config)
        location = f"{base}/eSCL/ScanJobs/{job.job_id}"
        LOGGER.info("Created job %s -> %s", job.job_id, location)
        return Response(
            status_code=201,
            headers={
                "Location": location,
                "Server": "Mock eSCL",
                "Content-Length": "0",
            },
        )

    @app.get("/eSCL/ScanJobs/{job_id}")
    async def get_scan_job(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown scan job")
        return Response(
            content=scan_status_xml(job),
            media_type="application/xml",
            headers={"Server": "Mock eSCL"},
        )

    @app.get("/eSCL/ScanJobs/{job_id}/ScanImageInfo")
    async def scan_image_info(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown scan job")
        return Response(
            content=scan_image_info_xml(job),
            media_type="application/xml",
            headers={"Server": "Mock eSCL"},
        )

    @app.get("/eSCL/ScanJobs/{job_id}/NextDocument")
    async def next_document(job_id: str, request: Request) -> Response:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown scan job")

        if job.state == JobState.FAILED:
            raise HTTPException(
                status_code=500,
                detail=job.error_message or "Scan failed",
            )

        if job.state != JobState.COMPLETED:
            raise HTTPException(
                status_code=503,
                detail=f"Job not ready: {job.state.value}",
            )

        if job.pages_delivered >= job.pages_total:
            raise HTTPException(status_code=404, detail="No more documents")

        job.pages_delivered += 1

        ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/tiff": "tiff",
            "application/pdf": "pdf",
        }.get(job.document_format, "bin")

        filename = f"scan-{job.job_id}.{ext}"

        accept = request.headers.get("Accept", "")
        if "multipart/related" in accept:
            boundary = "MOCK_ESCL_BOUNDARY"
            related = (
                f"--{boundary}\r\n"
                f"Content-Type: application/xml\r\n\r\n"
                f"{scan_image_info_xml(job).decode('utf-8')}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: {job.document_format}\r\n"
                f"Content-Disposition: attachment; filename=\"{filename}\"\r\n\r\n"
            ).encode("utf-8") + job.image + f"\r\n--{boundary}--\r\n".encode("utf-8")
            return Response(
                content=related,
                media_type=f'multipart/related; boundary="{boundary}"',
                headers={"Server": "Mock eSCL"},
            )

        return Response(
            content=job.image,
            media_type=job.document_format,
            headers={
                "Server": "Mock eSCL",
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.delete("/eSCL/ScanJobs/{job_id}")
    async def delete_scan_job(job_id: str) -> Response:
        if not jobs.delete(job_id):
            raise HTTPException(status_code=404, detail="Unknown scan job")
        return Response(status_code=200, headers={"Server": "Mock eSCL"})

    @app.get("/admin/last-requests")
    async def admin_last_requests() -> Response:
        """Diagnostic: dump the last few captured requests."""
        from json import dumps

        return Response(
            content=dumps(LAST_REQUESTS[-20:], indent=2).encode("utf-8"),
            media_type="application/json",
            headers={"Server": "Mock eSCL"},
        )

    @app.get("/admin/captures")
    async def admin_captures_dir() -> Response:
        """Diagnostic: show where captured payloads live."""
        from json import dumps

        exists = CAPTURE_DIR.exists()
        files: list[str] = []
        if exists:
            files = sorted(p.name for p in CAPTURE_DIR.iterdir())
        return Response(
            content=dumps(
                {
                    "capture_dir": str(CAPTURE_DIR),
                    "exists": exists,
                    "files": files[-30:],
                },
                indent=2,
            ).encode("utf-8"),
            media_type="application/json",
            headers={"Server": "Mock eSCL"},
        )

    return app


__all__ = [
    "create_app",
    "capabilities_xml",
    "scanner_status_xml",
    "scan_status_xml",
    "scan_image_info_xml",
    "ESCL_NAMESPACE",
    "CAPTURE_DIR",
]