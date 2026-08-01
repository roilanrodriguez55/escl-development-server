"""FastAPI application implementing the eSCL/AirScan HTTP surface."""

from __future__ import annotations

import asyncio
import base64
import contextvars
import logging
import os
import socket
import time
import uuid as _uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response

from .config import ScannerConfig
from .jobs import JobManager
from .models import (
    InputSource,
    JobState,
    ScanJob,
    ScanRegion,
    region_pixel_size,
    state_reason,
)

LOGGER = logging.getLogger("mock_escl.server")

ESCL_NAMESPACE = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NAMESPACE = "http://www.pwg.org/schemas/2010/12/sm"

_capture_dir_env = os.environ.get("MOCK_ESCL_CAPTURE_DIR", "/tmp/mock-escl-captures")
CAPTURE_DIR: Path | None = None if _capture_dir_env == "" else Path(_capture_dir_env)

LAST_REQUESTS: list[dict] = []
MAX_CAPTURED = 50

# Per-request correlation id; consumed by every log line emitted while a
# request is being processed. Defaults to ``"-"`` outside a request.
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "mock_escl_request_id", default="-"
)


def request_id() -> str:
    """Return the current request's correlation id, or ``-`` if none."""
    return _request_id_var.get()


def _xml_header() -> bytes:
    return b'<?xml version="1.0" encoding="UTF-8"?>\n'


def _server_header(config: ScannerConfig) -> str:
    """Server header that matches the scanner identity.

    Several real-world clients (notably some HP LaserJet devices seen by
    sane-airscan) enable quirks based on the value of ``Server``.
    """
    return f"{config.manufacturer} {config.model}"


def _format_headers(headers) -> str:
    """Pretty-print an iterable of (key, value) pairs for debug output."""
    return "\n".join(f"    {k}: {v}" for k, v in headers)


def _capture_request(
    request: Request,
    req_id: str,
    body: bytes | None,
    response_status: int | None = None,
    response_headers: list[tuple[str, str]] | None = None,
    response_body: bytes | None = None,
) -> None:
    """Record the most recent requests so we can diagnose client behaviour.

    On disk we save up to three files per request:
      ``<stamp>-<METHOD>-<slug>.req.body``  raw inbound body
      ``<stamp>-<METHOD>-<slug>.res.body``  truncated outbound body
      ``<stamp>-<METHOD>-<slug>.meta``      key headers + correlation id
    """
    entry = {
        "ts": time.time(),
        "req_id": req_id,
        "method": request.method,
        "path": request.url.path,
        "query": dict(request.query_params),
        "client": request.client.host if request.client else "?",
        "content_type": request.headers.get("Content-Type", ""),
        "content_length": request.headers.get("Content-Length", ""),
        "accept": request.headers.get("Accept", ""),
        "user_agent": request.headers.get("User-Agent", ""),
        "body_preview": (body or b"")[:4096].decode("utf-8", errors="replace"),
        "response_status": response_status,
        "response_headers": dict(response_headers or []),
    }
    LAST_REQUESTS.append(entry)
    if len(LAST_REQUESTS) > MAX_CAPTURED:
        del LAST_REQUESTS[: len(LAST_REQUESTS) - MAX_CAPTURED]

    if CAPTURE_DIR is None:
        return
    if not CAPTURE_DIR.exists() and not _safe_mkdir(CAPTURE_DIR):
        return

    try:
        stamp = datetime.now().strftime("%Y%m%dT%H%M%S-%f")
        slug = request.url.path.replace("/", "_").strip("_") or "root"
        if body is not None:
            (CAPTURE_DIR / f"{stamp}-{request.method}-{slug}.req.body").write_bytes(body)

        if response_body is not None:
            (CAPTURE_DIR / f"{stamp}-{request.method}-{slug}.res.body").write_bytes(
                response_body[:4096]
            )

        meta_lines = [
            f"RequestId: {req_id}",
            f"Method: {request.method}",
            f"Path: {request.url.path}",
            f"Query: {dict(request.query_params)}",
            f"Client: {request.client.host if request.client else '?'}",
            f"Content-Type: {request.headers.get('Content-Type', '')}",
            f"Content-Length: {request.headers.get('Content-Length', '')}",
            f"Accept: {request.headers.get('Accept', '')}",
            f"User-Agent: {request.headers.get('User-Agent', '')}",
        ]
        if response_status is not None:
            meta_lines.append(f"Response-Status: {response_status}")
            for k, v in response_headers or []:
                meta_lines.append(f"Response-{k}: {v}")
        (CAPTURE_DIR / f"{stamp}-{request.method}-{slug}.meta").write_text(
            "\n".join(meta_lines) + "\n"
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


def _aggregate_state(jobs: JobManager) -> tuple[str, str]:
    """Derive the global scanner state from the active jobs.

    Returns ``(pwg_state, adf_state)``. ``adf_state`` is ``ScannerAdfLoaded``
    when ADF is enabled, otherwise ``""``.
    """
    adf_state = "ScannerAdfLoaded" if jobs.config.adf_enabled else ""
    pwg_state = "Idle"
    for job_id in jobs.list_ids():
        job = jobs.get(job_id)
        if job is None:
            continue
        if job.state in (JobState.PENDING, JobState.PROCESSING):
            pwg_state = "Processing"
            break
    return pwg_state, adf_state


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


# --------------------------------------------------------------------------- #
#  eSCL capabilities, status and per-job XML builders
# --------------------------------------------------------------------------- #

_DEFAULT_DOCUMENT_FORMATS: tuple[str, ...] = (
    "application/octet-stream",
    "image/jpeg",
    "image/png",
    "image/tiff",
    "application/pdf",
)

_DEFAULT_INTENTS: tuple[str, ...] = (
    "Preview",
    "TextAndGraphic",
    "Document",
    "Photo",
)


def _resolutions_xml(resolutions: list[int]) -> str:
    return "".join(
        f"""        <scan:DiscreteResolution>
          <scan:XResolution>{r}</scan:XResolution>
          <scan:YResolution>{r}</scan:YResolution>
        </scan:DiscreteResolution>"""
        for r in resolutions
    )


def _color_modes_xml(color_modes: list[str]) -> str:
    return "".join(
        f"        <scan:ColorMode>{_xml_escape(m)}</scan:ColorMode>"
        for m in color_modes
    )


def _document_formats_xml(formats: tuple[str, ...]) -> str:
    """Emit every document format under both ``pwg:`` and ``scan:`` prefixes.

    sane-airscan reads both; macOS Image Capture expects ``application/octet-stream``
    alongside the others to show the device as AirScan-compatible.
    """
    lines: list[str] = []
    for fmt in formats:
        lines.append(f"          <pwg:DocumentFormat>{fmt}</pwg:DocumentFormat>")
        lines.append(f"          <scan:DocumentFormatExt>{fmt}</scan:DocumentFormatExt>")
    return "\n".join(lines)


def _setting_profile_xml(config: ScannerConfig) -> str:
    return f"""        <scan:SettingProfiles>
          <scan:SettingProfile>
            <scan:ColorModes>
{_color_modes_xml(config.color_modes)}
            </scan:ColorModes>
            <scan:ContentTypes>
              <pwg:ContentType>TextAndPhoto</pwg:ContentType>
            </scan:ContentTypes>
            <scan:DocumentFormats>
{_document_formats_xml(_DEFAULT_DOCUMENT_FORMATS)}
            </scan:DocumentFormats>
            <scan:SupportedResolutions>
              <scan:DiscreteResolutions>
{_resolutions_xml(config.resolutions)}
              </scan:DiscreteResolutions>
            </scan:SupportedResolutions>
            <scan:ColorSpaces>
              <scan:ColorSpace>sRGB</scan:ColorSpace>
            </scan:ColorSpaces>
            <scan:SupportedIntents>
              {''.join(f'<scan:Intent>{i}</scan:Intent>' for i in _DEFAULT_INTENTS)}
            </scan:SupportedIntents>
          </scan:SettingProfile>
        </scan:SettingProfiles>"""


def _platen_caps_xml(config: ScannerConfig) -> str:
    max_res = max(config.resolutions)
    return f"""    <scan:Platen>
      <scan:PlatenInputCaps>
        <scan:MinWidth>1</scan:MinWidth>
        <scan:MaxWidth>{config.max_width_mm}</scan:MaxWidth>
        <scan:MinHeight>1</scan:MinHeight>
        <scan:MaxHeight>{config.max_height_mm}</scan:MaxHeight>
        <scan:MaxScanRegions>1</scan:MaxScanRegions>
{_setting_profile_xml(config)}
        <scan:MaxOpticalXResolution>{max_res}</scan:MaxOpticalXResolution>
        <scan:MaxOpticalYResolution>{max_res}</scan:MaxOpticalYResolution>
      </scan:PlatenInputCaps>
    </scan:Platen>"""


def _adf_caps_xml(config: ScannerConfig) -> str:
    if not config.adf_enabled:
        return ""
    max_res = max(config.resolutions)
    caps_inner = f"""        <scan:MinWidth>1</scan:MinWidth>
        <scan:MaxWidth>{config.max_width_mm}</scan:MaxWidth>
        <scan:MinHeight>1</scan:MinHeight>
        <scan:MaxHeight>{config.max_height_mm}</scan:MaxHeight>
        <scan:MaxScanRegions>1</scan:MaxScanRegions>
{_setting_profile_xml(config)}
        <scan:MaxOpticalXResolution>{max_res}</scan:MaxOpticalXResolution>
        <scan:MaxOpticalYResolution>{max_res}</scan:MaxOpticalYResolution>"""
    return f"""    <scan:Adf>
      <scan:AdfSimplexInputCaps>
{caps_inner}
      </scan:AdfSimplexInputCaps>
      <scan:AdfDuplexInputCaps>
{caps_inner}
      </scan:AdfDuplexInputCaps>
    </scan:Adf>"""


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

    platen_block = _platen_caps_xml(config)
    adf_block = _adf_caps_xml(config)

    compression_block = ""
    if "image/jpeg" in _DEFAULT_DOCUMENT_FORMATS or "image/tiff" in _DEFAULT_DOCUMENT_FORMATS:
        compression_block = """
    <scan:CompressionFactorSupport>
      <scan:Min>1</scan:Min>
      <scan:Max>100</scan:Max>
      <scan:Normal>25</scan:Normal>
      <scan:Step>1</scan:Step>
    </scan:CompressionFactorSupport>"""

    return (
        _xml_header()
        + f"""<scan:ScannerCapabilities xmlns:scan="{ESCL_NAMESPACE}" xmlns:pwg="{PWG_NAMESPACE}">
  <scan:Version>2.63</scan:Version>
  <pwg:Version>2.0</pwg:Version>
  <scan:MakeAndModel>{make_and_model}</scan:MakeAndModel>
  <pwg:MakeAndModel>{make_and_model}</pwg:MakeAndModel>
  <scan:SerialNumber>{serial}</scan:SerialNumber>
  <scan:UUID>{uuid_val}</scan:UUID>
  <scan:AdminURI>{admin_uri}</scan:AdminURI>
  <scan:IconURI>{icon_uri}</scan:IconURI>
{platen_block}{adf_block}{compression_block}
  <scan:eSCLConfigCap>
    <scan:StateSupport>
      <scan:State>disabled</scan:State>
      <scan:State>enabled</scan:State>
    </scan:StateSupport>
    <scan:ScannerAdminCredentialsSupport>false</scan:ScannerAdminCredentialsSupport>
  </scan:eSCLConfigCap>
</scan:ScannerCapabilities>
""".encode("utf-8")
    )


def _job_info_xml(job: ScanJob) -> str:
    """Render one ``<scan:JobInfo>`` block for use inside ``ScannerStatus``."""
    age = max(0, int((datetime.now(job.created_at.tzinfo) - job.created_at).total_seconds()))
    images_to_transfer = max(0, job.pages_total - job.pages_delivered)
    images_completed = min(job.pages_total, job.pages_delivered)
    return f"""    <scan:JobInfo>
      <pwg:JobUri>/eSCL/ScanJobs/{job.job_id}</pwg:JobUri>
      <pwg:JobUuid>{job.job_id}</pwg:JobUuid>
      <scan:Age>{age}</scan:Age>
      <pwg:JobState>{job.state.value}</pwg:JobState>
      <pwg:ImagesToTransfer>{images_to_transfer}</pwg:ImagesToTransfer>
      <pwg:ImagesCompleted>{images_completed}</pwg:ImagesCompleted>
      <pwg:JobStateReasons>
        <pwg:JobStateReason>{state_reason(job.state).value}</pwg:JobStateReason>
      </pwg:JobStateReasons>
    </scan:JobInfo>"""


def scanner_status_xml(jobs: JobManager) -> bytes:
    """Build the ScannerStatus XML with PWG semantics and aggregate state."""
    pwg_state, adf_state = _aggregate_state(jobs)
    job_entries = "".join(_job_info_xml(jobs.get(jid)) for jid in jobs.list_ids() if jobs.get(jid))
    adf_block = (
        f"  <scan:AdfState>{adf_state}</scan:AdfState>\n" if adf_state else ""
    )
    return (
        _xml_header()
        + f"""<scan:ScannerStatus xmlns:scan="{ESCL_NAMESPACE}" xmlns:pwg="{PWG_NAMESPACE}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:State>{pwg_state}</pwg:State>
{adf_block}  <scan:Jobs>
{job_entries}
  </scan:Jobs>
</scan:ScannerStatus>
""".encode("utf-8")
    )


def scan_status_xml(job: ScanJob) -> bytes:
    """Build the per-job status XML (one job)."""
    age = max(0, int((datetime.now(job.created_at.tzinfo) - job.created_at).total_seconds()))
    images_to_transfer = max(0, job.pages_total - job.pages_delivered)
    images_completed = min(job.pages_total, job.pages_delivered)
    return (
        _xml_header()
        + f"""<scan:ScanJobStatus xmlns:scan="{ESCL_NAMESPACE}" xmlns:pwg="{PWG_NAMESPACE}">
  <pwg:Version>2.0</pwg:Version>
  <scan:JobUuid>{job.job_id}</scan:JobUuid>
  <scan:Age>{age}</scan:Age>
  <pwg:JobState>{job.state.value}</pwg:JobState>
  <pwg:ImagesToTransfer>{images_to_transfer}</pwg:ImagesToTransfer>
  <pwg:ImagesCompleted>{images_completed}</pwg:ImagesCompleted>
  <pwg:JobStateReasons>
    <pwg:JobStateReason>{state_reason(job.state).value}</pwg:JobStateReason>
  </pwg:JobStateReasons>
</scan:ScanJobStatus>
""".encode("utf-8")
    )


def scan_image_info_xml(job: ScanJob, config: ScannerConfig) -> bytes:
    """Build a ScanImageInfo XML describing the document the server will hand out.

    Some clients (notably sane-airscan) ask for this endpoint before pulling
    NextDocument. Width/Height are derived from the client's ``ScanRegion`` if
    provided; otherwise from the platen size.
    """
    if job.scan_region is not None:
        width_px, height_px = region_pixel_size(
            job.scan_region,
            config.max_width_mm,
            config.max_height_mm,
            job.resolution,
        )
    else:
        width_px = max(1, int((config.max_width_mm / 25.4) * job.resolution))
        height_px = max(1, int((config.max_height_mm / 25.4) * job.resolution))

    compression_block = ""
    if job.document_format in ("image/jpeg", "image/tiff"):
        compression_block = (
            f"    <scan:CompressionFactor>{job.compression_factor}"
            f"</scan:CompressionFactor>\n"
        )

    return (
        _xml_header()
        + f"""<scan:ScanImageInfo xmlns:scan="{ESCL_NAMESPACE}" xmlns:pwg="{PWG_NAMESPACE}">
  <pwg:Version>2.0</pwg:Version>
  <scan:JobUuid>{job.job_id}</scan:JobUuid>
  <scan:Images>{job.pages_total}</scan:Images>
  <scan:Image>
    <pwg:DocumentFormat>{job.document_format}</pwg:DocumentFormat>
    <scan:DocumentFormatExt>{job.document_format}</scan:DocumentFormatExt>
    <scan:InputSource>{job.input_source.value}</scan:InputSource>
    <scan:ColorMode>{job.color_mode}</scan:ColorMode>
    <scan:XResolution>{job.resolution}</scan:XResolution>
    <scan:YResolution>{job.resolution}</scan:YResolution>
{compression_block}    <scan:Width>{width_px}</scan:Width>
    <scan:Height>{height_px}</scan:Height>
  </scan:Image>
</scan:ScanImageInfo>
""".encode("utf-8")
    )


# --------------------------------------------------------------------------- #
#  ScanSettings parser
# --------------------------------------------------------------------------- #

import xml.etree.ElementTree as ET

_SCAN_NS = {"scan": ESCL_NAMESPACE, "pwg": PWG_NAMESPACE}


def _find_first(body: ET.Element, paths: tuple[str, ...]) -> ET.Element | None:
    """Find the first matching element across namespace-prefixed paths."""
    for path in paths:
        el = body.find(path, _SCAN_NS)
        if el is not None:
            return el
    return None


def _text(el: ET.Element | None, default: str = "") -> str:
    if el is None or el.text is None:
        return default
    return el.text.strip()


def _int(el: ET.Element | None, default: int) -> int:
    if el is None or el.text is None:
        return default
    try:
        return int(el.text.strip())
    except ValueError:
        return default


def _normalize_format(raw: str) -> str:
    """Translate whatever the client sent into a canonical eSCL MIME."""
    raw = raw.strip()
    aliases = {
        "jpeg": "image/jpeg",
        "jpg": "image/jpeg",
        "png": "image/png",
        "tiff": "image/tiff",
        "tif": "image/tiff",
        "pdf": "application/pdf",
    }
    lowered = raw.lower()
    if lowered in aliases:
        return aliases[lowered]
    if "/" in raw:
        return raw
    return raw or "image/png"


def _parse_scan_settings(body: bytes, config: ScannerConfig) -> dict:
    """Extract every meaningful field from a ``ScanSettings`` XML body.

    Returns a dict that ``JobManager.create`` can consume. Falls back to
    substring matching when the body is not parseable XML, so the mock stays
    tolerant to malformed clients.
    """
    LOGGER.debug(
        "[%s] parsing ScanSettings body: %d bytes",
        request_id(),
        len(body),
    )
    fallback = _fallback_substring_parse(body, config)
    if not body.strip():
        LOGGER.debug("[%s] empty body, using config defaults", request_id())
        return fallback

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        LOGGER.warning(
            "[%s] ScanSettings is not valid XML (%s); using substring fallback",
            request_id(),
            exc,
        )
        return fallback

    intent_el = _find_first(root, ("scan:Intent", "pwg:Intent"))
    fmt_el = _find_first(
        root,
        (
            "pwg:DocumentFormat",
            "scan:DocumentFormat",
            "scan:DocumentFormatExt",
            "scan:ImageFormat",
        ),
    )
    color_el = _find_first(root, ("scan:ColorMode", "pwg:ColorMode"))
    xres_el = _find_first(root, ("scan:XResolution", "pwg:XResolution"))
    yres_el = _find_first(root, ("scan:YResolution", "pwg:YResolution"))
    source_el = _find_first(root, ("pwg:InputSource", "scan:InputSource"))
    duplex_el = _find_first(root, ("scan:Duplex", "pwg:Duplex"))
    compression_el = _find_first(
        root, ("scan:CompressionFactor", "pwg:CompressionFactor")
    )
    units_el = _find_first(
        root, ("pwg:ScanRegions/pwg:ScanRegion/pwg:ContentRegionUnits",)
    )

    document_format = (
        _normalize_format(_text(fmt_el)) if fmt_el is not None else fallback["document_format"]
    )
    color_mode = (
        _text(color_el, fallback["color_mode"]) or fallback["color_mode"]
    )
    if color_mode not in config.color_modes:
        # If client asked for a color mode we don't advertise, snap to first
        # advertised mode rather than crashing.
        LOGGER.debug(
            "[%s] color mode %r not advertised; snapping to %r",
            request_id(),
            color_mode,
            config.color_modes[0] if config.color_modes else "RGB24",
        )
        color_mode = config.color_modes[0] if config.color_modes else "RGB24"

    x_res = _int(xres_el, fallback["resolution"])
    y_res = _int(yres_el, x_res)
    resolution = x_res if x_res == y_res else x_res

    # Snap to the nearest advertised resolution.
    if config.resolutions:
        snapped = min(config.resolutions, key=lambda r: abs(r - resolution))
        if snapped != resolution:
            LOGGER.debug(
                "[%s] resolution %d snapped to %d (advertised)",
                request_id(),
                resolution,
                snapped,
            )
        resolution = snapped

    intent = _text(intent_el, "") or None

    source_raw = _text(source_el, "")
    if source_raw.lower() == "feeder":
        input_source = InputSource.FEEDER
    else:
        input_source = InputSource.PLATEN

    duplex_raw = _text(duplex_el, "false").lower()
    duplex = duplex_raw in ("true", "1", "yes")

    compression = _int(compression_el, fallback["compression_factor"])
    compression = max(1, min(100, compression))

    region: ScanRegion | None = None
    region_el = _find_first(root, ("pwg:ScanRegions/pwg:ScanRegion",))
    if region_el is not None:
        units = _text(units_el, "escl:ThreeHundredthsOfInches")
        region = ScanRegion(
            x_offset=_int(_find_first(region_el, ("pwg:XOffset",)), 0),
            y_offset=_int(_find_first(region_el, ("pwg:YOffset",)), 0),
            width=_int(_find_first(region_el, ("pwg:Width",)), 0),
            height=_int(_find_first(region_el, ("pwg:Height",)), 0),
            units=units,
        )

    LOGGER.debug(
        "[%s] raw elements: Intent=%r Format=%r ColorMode=%r XRes=%r YRes=%r "
        "InputSource=%r Duplex=%r CompressionFactor=%r RegionUnits=%r",
        request_id(),
        _text(intent_el),
        _text(fmt_el) if fmt_el is not None else None,
        _text(color_el),
        _text(xres_el),
        _text(yres_el),
        _text(source_el),
        _text(duplex_el),
        _text(compression_el),
        _text(units_el),
    )

    return {
        "document_format": document_format,
        "color_mode": color_mode,
        "resolution": resolution,
        "intent": intent,
        "input_source": input_source,
        "duplex": duplex,
        "scan_region": region,
        "compression_factor": compression,
    }


def _fallback_substring_parse(body: bytes, config: ScannerConfig) -> dict:
    """Defensive fallback for clients that send non-XML bodies."""
    document_format = config.default_format
    color_mode = config.color_modes[0] if config.color_modes else "RGB24"
    resolution = config.resolutions[len(config.resolutions) // 2]
    compression = 25

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

    return {
        "document_format": document_format,
        "color_mode": color_mode,
        "resolution": resolution,
        "intent": None,
        "input_source": InputSource.PLATEN,
        "duplex": False,
        "scan_region": None,
        "compression_factor": compression,
    }


def _absolute_base_url(request: Request, config: ScannerConfig) -> str:
    """Build a URL that points at the scanner, suitable for Location headers."""
    host = request.headers.get("Host")
    if host:
        return f"http://{host}"
    return _server_url(config)


# --------------------------------------------------------------------------- #
#  FastAPI application factory
# --------------------------------------------------------------------------- #

_ICON_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f"
    "15c4890000000d49444154789c636000010000000500017a9d3a3e00000000"
    "49454e44ae426082"
)


def create_app(config: ScannerConfig, seed: int | None = None) -> FastAPI:
    """Build a FastAPI app bound to a JobManager configured by ``config``.

    ``seed`` enables deterministic scans: identical requests produce
    byte-identical images and PDFs (same timestamps, same text).
    """
    jobs = JobManager(config, seed=seed)
    server_header = _server_header(config)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        LOGGER.info("Mock eSCL scanner started on %s:%s", config.host, config.port)
        if CAPTURE_DIR is not None:
            LOGGER.info("Capturing payloads to: %s", CAPTURE_DIR)
        try:
            yield
        finally:
            await jobs.aclose()
            LOGGER.info("Mock eSCL scanner stopped")

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        """Per-request diagnostics: id, headers, body, response, duration.

        INFO  — one line per request with method, path, status, duration, id
        DEBUG — full headers, body preview, response headers
        """
        req_id = _uuid.uuid4().hex[:8]
        token = _request_id_var.set(req_id)

        body: bytes | None = None
        if request.method in {"POST", "PUT", "PATCH"}:
            body = await request.body()

        client = request.client.host if request.client else "?"
        LOGGER.info(
            "[%s] %s %s %s from %s%s",
            req_id,
            request.method,
            request.url.path,
            ("?" + str(request.url.query) if request.url.query else ""),
            client,
            f":{request.client.port}" if request.client and request.client.port else "",
        )
        LOGGER.debug(
            "[%s] request headers:\n%s",
            req_id,
            _format_headers(request.headers.items()),
        )
        if body is not None:
            preview = body[:4096].decode("utf-8", errors="replace")
            LOGGER.debug("[%s] request body (%d bytes):\n%s", req_id, len(body), preview)

        start = datetime.now()
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("[%s] handler raised: %s", req_id, exc)
            raise
        duration_ms = (datetime.now() - start).total_seconds() * 1000

        response_headers_list = [
            (k, v) for k, v in response.headers.items()
        ]
        response_body = getattr(response, "body", None)
        response_size = len(response_body) if response_body else 0

        LOGGER.info(
            "[%s] -> %d (%d B, %.1f ms)",
            req_id,
            response.status_code,
            response_size,
            duration_ms,
        )
        LOGGER.debug(
            "[%s] response headers:\n%s",
            req_id,
            _format_headers(response_headers_list),
        )
        if response_body and len(response_body) <= 4096:
            try:
                preview = response_body.decode("utf-8", errors="replace")
                LOGGER.debug("[%s] response body:\n%s", req_id, preview)
            except Exception:  # noqa: BLE001
                LOGGER.debug("[%s] response body (%d bytes, binary)", req_id, len(response_body))

        _capture_request(
            request,
            req_id,
            body,
            response_status=response.status_code,
            response_headers=response_headers_list,
            response_body=response_body,
        )

        _request_id_var.reset(token)
        return response

    @app.get("/eSCL/ScannerCapabilities")
    async def scanner_capabilities(request: Request) -> Response:
        host = request.headers.get("Host")
        LOGGER.debug(
            "[%s] building ScannerCapabilities (Host=%s, Server=%s)",
            request_id(),
            host,
            server_header,
        )
        body = capabilities_xml(config, host)
        return Response(
            content=body,
            media_type="application/xml",
            headers={"Server": server_header},
        )

    @app.get("/eSCL/ScannerStatus")
    async def scanner_status() -> Response:
        pwg_state, adf_state = _aggregate_state(jobs)
        active = jobs.list_ids()
        LOGGER.debug(
            "[%s] ScannerStatus pwg_state=%s adf_state=%s active_jobs=%d (%s)",
            request_id(),
            pwg_state,
            adf_state or "(none)",
            len(active),
            ",".join(active) if active else "-",
        )
        return Response(
            content=scanner_status_xml(jobs),
            media_type="application/xml",
            headers={"Server": server_header},
        )

    @app.get("/eSCL/ScannerIcon")
    async def scanner_icon() -> Response:
        return Response(
            content=_ICON_PNG,
            media_type="image/png",
            headers={"Server": server_header},
        )

    @app.post("/eSCL/ScanJobs")
    async def create_scan_job(request: Request) -> Response:
        body = await request.body()
        LOGGER.info(
            "[%s] received ScanSettings (%d bytes)",
            request_id(),
            len(body),
        )
        parsed = _parse_scan_settings(body, config)
        LOGGER.info(
            "[%s] parsed ScanSettings: format=%s color=%s res=%d intent=%s "
            "input_source=%s duplex=%s compression_factor=%s region=%s",
            request_id(),
            parsed["document_format"],
            parsed["color_mode"],
            parsed["resolution"],
            parsed["intent"] or "(none)",
            parsed["input_source"].value,
            parsed["duplex"],
            parsed["compression_factor"],
            (
                f"({parsed['scan_region'].x_offset},{parsed['scan_region'].y_offset})"
                f" {parsed['scan_region'].width}x{parsed['scan_region'].height} "
                f"{parsed['scan_region'].units}"
                if parsed["scan_region"] is not None
                else "(full platen)"
            ),
        )

        job = jobs.create(**parsed)
        asyncio.create_task(jobs.process(job))

        base = _absolute_base_url(request, config)
        location = f"{base}/eSCL/ScanJobs/{job.job_id}"
        LOGGER.info(
            "[%s] job created %s -> %s",
            request_id(),
            job.job_id,
            location,
        )
        return Response(
            status_code=201,
            headers={
                "Location": location,
                "Server": server_header,
                "Content-Length": "0",
            },
        )

    @app.get("/eSCL/ScanJobs/{job_id}")
    async def get_scan_job(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            LOGGER.info("[%s] unknown job %s", request_id(), job_id)
            raise HTTPException(status_code=404, detail="Unknown scan job")
        LOGGER.debug(
            "[%s] job %s state=%s reason=%s age=%ds images=%d/%d",
            request_id(),
            job_id,
            job.state.value,
            state_reason(job.state).value,
            max(0, int((datetime.now(job.created_at.tzinfo) - job.created_at).total_seconds())),
            job.pages_delivered,
            job.pages_total,
        )
        return Response(
            content=scan_status_xml(job),
            media_type="application/xml",
            headers={"Server": server_header},
        )

    @app.get("/eSCL/ScanJobs/{job_id}/ScanImageInfo")
    async def scan_image_info(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            LOGGER.info("[%s] ScanImageInfo for unknown job %s", request_id(), job_id)
            raise HTTPException(status_code=404, detail="Unknown scan job")
        LOGGER.debug(
            "[%s] ScanImageInfo job=%s state=%s format=%s %dx%d @ %d DPI",
            request_id(),
            job_id,
            job.state.value,
            job.document_format,
            # Only meaningful once Completed, but always log the request.
            (
                region_pixel_size(job.scan_region, config.max_width_mm, config.max_height_mm, job.resolution)
                if job.scan_region is not None
                else (
                    int((config.max_width_mm / 25.4) * job.resolution),
                    int((config.max_height_mm / 25.4) * job.resolution),
                )
            )[0],
            (
                region_pixel_size(job.scan_region, config.max_width_mm, config.max_height_mm, job.resolution)
                if job.scan_region is not None
                else (
                    int((config.max_width_mm / 25.4) * job.resolution),
                    int((config.max_height_mm / 25.4) * job.resolution),
                )
            )[1],
            job.resolution,
        )
        return Response(
            content=scan_image_info_xml(job, config),
            media_type="application/xml",
            headers={"Server": server_header},
        )

    @app.get("/eSCL/ScanJobs/{job_id}/NextDocument")
    async def next_document(job_id: str, request: Request) -> Response:
        job = jobs.get(job_id)
        if job is None:
            LOGGER.info("[%s] NextDocument for unknown job %s", request_id(), job_id)
            raise HTTPException(status_code=404, detail="Unknown scan job")

        accept = request.headers.get("Accept", "")
        LOGGER.debug(
            "[%s] NextDocument job=%s state=%s Accept=%r",
            request_id(),
            job_id,
            job.state.value,
            accept[:80],
        )

        if job.state == JobState.FAILED:
            LOGGER.warning(
                "[%s] job %s failed: %s",
                request_id(),
                job_id,
                job.error_message,
            )
            raise HTTPException(
                status_code=500,
                detail=job.error_message or "Scan failed",
            )

        if job.state == JobState.ABORTED:
            raise HTTPException(status_code=410, detail="Job aborted")

        if job.state != JobState.COMPLETED:
            # Per eSCL spec, 503 + Retry-After tells the client the job is
            # still running. sane-airscan classifies a 503 as "temporary" but
            # only retries when the body parses as a ScannerStatus XML whose
            # <pwg:State> resolves to a busy status — otherwise it treats it
            # as IO_ERROR and aborts. So we return the same ScannerStatus body
            # the GET endpoint emits, with the current aggregate state.
            age = max(0, int((datetime.now(job.created_at.tzinfo) - job.created_at).total_seconds()))
            LOGGER.debug(
                "[%s] job %s not ready (state=%s age=%ds); sending 503 + Retry-After: 1",
                request_id(),
                job_id,
                job.state.value,
                age,
            )
            body = scanner_status_xml(jobs)
            return Response(
                status_code=503,
                content=body,
                media_type="application/xml",
                headers={"Retry-After": "1", "Server": server_header},
            )

        if job.pages_delivered >= job.pages_total:
            LOGGER.info(
                "[%s] job %s has no more documents (%d/%d)",
                request_id(),
                job_id,
                job.pages_delivered,
                job.pages_total,
            )
            raise HTTPException(status_code=404, detail="No more documents")

        job.pages_delivered += 1

        ext = {
            "image/jpeg": "jpg",
            "image/png": "png",
            "image/tiff": "tiff",
            "application/pdf": "pdf",
        }.get(job.document_format, "bin")

        filename = f"scan-{job.job_id}.{ext}"

        if "multipart/related" in accept:
            boundary = "MOCK_ESCL_BOUNDARY"
            related = (
                f"--{boundary}\r\n"
                f"Content-Type: application/xml\r\n\r\n"
                f"{scan_image_info_xml(job, config).decode('utf-8')}\r\n"
                f"--{boundary}\r\n"
                f"Content-Type: {job.document_format}\r\n"
                f"Content-Disposition: attachment; filename=\"{filename}\"\r\n\r\n"
            ).encode("utf-8") + job.image + f"\r\n--{boundary}--\r\n".encode("utf-8")
            LOGGER.info(
                "[%s] job %s: delivering %d bytes as multipart/related (%s)",
                request_id(),
                job_id,
                len(related),
                job.document_format,
            )
            return Response(
                content=related,
                media_type=f'multipart/related; boundary="{boundary}"',
                headers={"Server": server_header},
            )

        LOGGER.info(
            "[%s] job %s: delivering %d bytes (%s)",
            request_id(),
            job_id,
            len(job.image),
            job.document_format,
        )
        return Response(
            content=job.image,
            media_type=job.document_format,
            headers={
                "Server": server_header,
                "Content-Disposition": f'attachment; filename="{filename}"',
            },
        )

    @app.delete("/eSCL/ScanJobs/{job_id}")
    async def delete_scan_job(job_id: str) -> Response:
        job = jobs.get(job_id)
        if job is None:
            LOGGER.info("[%s] DELETE unknown job %s", request_id(), job_id)
            raise HTTPException(status_code=404, detail="Unknown scan job")
        LOGGER.info(
            "[%s] DELETE job %s (was %s)",
            request_id(),
            job_id,
            job.state.value,
        )
        jobs.abort(job_id)
        return Response(status_code=200, headers={"Server": server_header})

    # --------------------------------------------------------------------- #
    #  Diagnostic endpoints (mock-only; namespaced under ``/_mock-admin/``)
    # --------------------------------------------------------------------- #

    @app.get("/_mock-admin/last-requests")
    async def admin_last_requests() -> Response:
        from json import dumps

        return Response(
            content=dumps(LAST_REQUESTS[-20:], indent=2).encode("utf-8"),
            media_type="application/json",
            headers={"Server": server_header},
        )

    @app.get("/_mock-admin/captures")
    async def admin_captures_dir() -> Response:
        from json import dumps

        if CAPTURE_DIR is None:
            payload = {
                "capture_dir": None,
                "exists": False,
                "files": [],
                "disabled": True,
            }
        else:
            exists = CAPTURE_DIR.exists()
            files: list[str] = []
            if exists:
                files = sorted(p.name for p in CAPTURE_DIR.iterdir())
            payload = {
                "capture_dir": str(CAPTURE_DIR),
                "exists": exists,
                "files": files[-30:],
                "disabled": False,
            }

        return Response(
            content=dumps(payload, indent=2).encode("utf-8"),
            media_type="application/json",
            headers={"Server": server_header},
        )

    return app


__all__ = [
    "create_app",
    "capabilities_xml",
    "scanner_status_xml",
    "scan_status_xml",
    "scan_image_info_xml",
    "ESCL_NAMESPACE",
    "PWG_NAMESPACE",
    "CAPTURE_DIR",
]
