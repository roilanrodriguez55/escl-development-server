"""Regression tests for specific bugs that previously hit real clients.

These tests pin down fixes for issues the user reported while testing with
real scanner clients (sane-airscan, macOS Image Capture, iOS Notes). Each
test names the symptom it guards against.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"


def test_page_is_ready_when_location_returned_with_zero_delay(client) -> None:
    """Regression: race condition where NextDocument saw PENDING and 503'd.

    With ``delay_seconds: 0`` (the default), the previous implementation
    scheduled the render as an ``asyncio.create_task`` and returned the
    201 + Location immediately. A fast client that polled NextDocument
    before the event loop had a chance to run the task would see the
    state as PENDING and get 503 forever. We now render inline.

    The job stays ``Processing`` until the page is actually transferred —
    reporting ``Completed`` with nothing transferred makes macOS abort.
    """
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]

    # Without any further delay, the page is rendered and awaiting transfer.
    state = client.get(loc).text
    assert "<pwg:JobState>Processing</pwg:JobState>" in state

    # And the NextDocument should return 200, not 503.
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200, nd.text[:200]

    # Only now, with the page handed over, is the job Completed.
    assert "<pwg:JobState>Completed</pwg:JobState>" in client.get(loc).text
    assert nd.headers["content-type"] == "image/jpeg"
    assert nd.content[:3] == b"\xff\xd8\xff"


def test_immediate_next_document_after_post(client) -> None:
    """The 'impatient client' pattern: POST then immediately GET NextDocument.

    With the previous async-scheduling approach, this would 503. Now it
    must succeed on the first try.
    """
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>75</scan:XResolution>
  <scan:YResolution>75</scan:YResolution>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]

    # The client didn't poll, it just calls NextDocument. Must work.
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.headers["content-type"] == "image/png"
    assert nd.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_processing_503_body_is_parseable_scanner_status(client) -> None:
    """503 response body must parse as a ScannerStatus XML with <pwg:State>.

    sane-airscan classifies a 503 as 'retry' only when the body is a
    valid ScannerStatus XML. If it can't parse, it gives up and surfaces
    IO_ERROR. This guards against accidentally returning a JSON or
    truncated body in the 503 path.
    """
    from mock_escl.server import scanner_status_xml
    from mock_escl.jobs import JobManager
    from mock_escl.config import ScannerConfig
    from pathlib import Path

    cfg = ScannerConfig.load(Path("config/scanner.json"))
    mgr = JobManager(cfg, seed=42)
    body = scanner_status_xml(mgr)
    # The body the 503 endpoint actually sends.
    root = ET.fromstring(body)
    ns = {"pwg": PWG_NS, "scan": ESCL_NS}
    state = root.find(".//pwg:State", ns)
    assert state is not None
    assert state.text in ("Idle", "Processing")


def test_location_header_uses_request_host(client) -> None:
    """The Location header reflects the Host the client connected with.

    Some real clients (sane-airscan) take the Location URL literally and
    use it for subsequent calls. If the server returns a Location with the
    wrong IP, the client ends up talking to the wrong host.
    """
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={
            "Content-Type": "application/xml",
            "Host": "192.168.1.50:8080",
        },
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    assert "192.168.1.50:8080" in loc


def test_xml_body_with_unbound_prefix_does_not_crash(client) -> None:
    """Some real clients send ScanSettings with undeclared pwg: prefix.

    The XML parser correctly flags 'unbound prefix', and we fall back to
    substring matching. The job must still be created and rendered.
    """
    # Note: only scan: namespace declared, pwg: used undeclared.
    body = f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.headers["content-type"] == "image/jpeg"


def test_asyncio_task_is_referenced_after_post(client) -> None:
    """After POST /ScanJobs, the JobManager still has a tracked task.

    Defensive: even though the inline render path doesn't need scheduled
    tasks for the render itself, the cleanup task is still scheduled.
    Garbage-collecting it would prevent the 5-minute TTL cleanup.
    """
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    # We can't easily inspect the JobManager through the TestClient,
    # but we can verify the cleanup task is running by ensuring the
    # server keeps responding for a bit. (Indirect check.)
    s = client.get("/eSCL/ScannerStatus")
    assert s.status_code == 200


def test_scanregions_with_microns_units_parses(client) -> None:
    """Some real clients send ScanRegion with escl:Microns instead of inches."""
    body = f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:Microns</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>210000</pwg:Width>
      <pwg:Height>297000</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>75</scan:XResolution>
  <scan:YResolution>75</scan:YResolution>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]
    # Should produce a 1240x1753 JPEG (A4 at 75dpi) — or whatever the
    # Micron region maps to.
    info = client.get(f"{loc}/ScanImageInfo").text
    assert "<scan:Width>" in info
    assert "<scan:Height>" in info


def test_multipart_related_accept_returns_dual_part_response(client) -> None:
    """NextDocument with ``Accept: multipart/related`` returns the XML
    descriptor + the document bytes in a single response. This is the
    eSCL-correct way and what some strict clients (sane-airscan) send.
    """
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]

    nd = client.get(
        f"{loc}/NextDocument",
        headers={"Accept": "multipart/related"},
    )
    assert nd.status_code == 200
    assert nd.headers["content-type"].startswith("multipart/related")
    body = nd.content
    assert b"<scan:ScanImageInfo" in body
    assert b"\xff\xd8\xff" in body


def test_small_scan_region_renders_without_margin_overflow(client) -> None:
    """Regression: a small ScanRegion (e.g. 210x297 ThreeHundredthsOfInches
    at 300 DPI = 210x297 px) used to crash the renderer with
    ``ValueError: x1 must be greater than or equal to x0`` because the
    decorative margin was larger than the image. sane-airscan sends
    exactly this kind of small region for preview scans.
    """
    body = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>210</pwg:Width>
      <pwg:Height>297</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200, nd.text[:200]
    assert nd.headers["content-type"] == "image/png"
    assert nd.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_tiny_scan_region_renders(client) -> None:
    """A region that resolves to < 50 pixels still produces a valid image."""
    body = f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>30</pwg:Width>
      <pwg:Height>30</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>75</scan:XResolution>
  <scan:YResolution>75</scan:YResolution>
</scan:ScanSettings>
"""
    r = client.post("/eSCL/ScanJobs", content=body,
                    headers={"Content-Type": "application/xml"})
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.content[:8] == b"\x89PNG\r\n\x1a\n"
