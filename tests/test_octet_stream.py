"""TDD: application/octet-stream MIME handling.

Apple AirScan clients (macOS Image Capture, iOS Notes) often request
``application/octet-stream`` because that MIME is the most generic one
the device advertises. The server should honour that MIME on the
NextDocument response, returning the rendered bytes with the
application/octet-stream Content-Type and a sensible filename.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"


def test_octet_stream_request_returns_octet_stream_response(client) -> None:
    """A ScanSettings with application/octet-stream MIME returns the document
    with that Content-Type and a sensible filename."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.headers["content-type"] == "application/octet-stream"
    assert len(nd.content) > 0


def test_octet_stream_response_has_attachment_filename(client) -> None:
    """The Content-Disposition header should still carry a filename so the
    client can save the file with a meaningful name (e.g. .bin)."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    cd = nd.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert "filename=" in cd


def test_octet_stream_renders_as_png_by_default(client) -> None:
    """Internally the document is rendered as PNG (the default fallback).
    The bytes should still start with the PNG magic so the file is
    openable by any image viewer even though the MIME is octet-stream.
    """
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_octet_stream_with_pdf_request_returns_pdf_bytes(client) -> None:
    """If the client asks for application/octet-stream but the request
    body actually requested a PDF, the PDF path takes precedence because
    it's explicit. (A PDF is always PDF, not octet-stream.)"""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}">
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert nd.content[:5] == b"%PDF-"


def test_octet_stream_filename_uses_bin_extension(client) -> None:
    """The Content-Disposition filename for octet-stream uses .bin so
    the file is recognized as a generic binary by the OS."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    job_id = loc.rsplit("/", 1)[-1]
    nd = client.get(f"{loc}/NextDocument")
    assert nd.status_code == 200
    assert f"scan-{job_id}.bin" in nd.headers.get("content-disposition", "")


def test_octet_stream_scanimage_info_lists_octet_format(client) -> None:
    """ScanImageInfo for an octet-stream job should reflect the requested
    format on both pwg:DocumentFormat and scan:DocumentFormatExt so
    clients that look at the descriptor before pulling bytes see the
    same MIME."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:Version>2.0</pwg:Version>
  <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    loc = r.headers["location"]
    info = client.get(f"{loc}/ScanImageInfo").text
    assert "<pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>" in info
    assert (
        "<scan:DocumentFormatExt>application/octet-stream</scan:DocumentFormatExt>"
        in info
    )
