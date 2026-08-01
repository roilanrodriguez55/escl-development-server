"""TDD: real duplex rendering.

eSCL duplex scans produce 2 pages per sheet (front + back). The mock
used to advertise duplex support but rendered only a single page per
job. This suite pins down the duplex behaviour: a duplex job with
N pages delivers 2N pages, alternating front/back content, and
the back side is visibly different from the front.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"


def test_duplex_job_doubles_total_page_count(tmp_path) -> None:
    """A duplex job with pages_total=2 should produce 4 rendered pages."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text(
        '{"host":"0.0.0.0","port":8080,"pages_total":2,"adf_enabled":true}'
    )
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 201
        loc = r.headers["location"]
        info = c.get(f"{loc}/ScanImageInfo").text
        # pages_total=2 sheets × 2 sides = 4 rendered pages.
        assert "<scan:Images>4</scan:Images>" in info


def test_duplex_job_delivers_two_pages_per_sheet(tmp_path) -> None:
    """A duplex job with pages_total=2 yields 4 NextDocument calls
    (front, back, front, back) before the 404."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text(
        '{"host":"0.0.0.0","port":8080,"pages_total":2,"adf_enabled":true}'
    )
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        page_bytes = []
        for _ in range(5):
            resp = c.get(f"{loc}/NextDocument")
            if resp.status_code == 200:
                page_bytes.append(resp.content)
            else:
                break
        # 4 pages delivered before 404.
        assert len(page_bytes) == 4


def test_duplex_front_and_back_pages_differ(tmp_path) -> None:
    """The back side of a sheet should be visually distinct from the
    front side. We use the deterministic seed to compare bytes."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text(
        '{"host":"0.0.0.0","port":8080,"pages_total":2,"adf_enabled":true}'
    )
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        front1 = c.get(f"{loc}/NextDocument").content
        back1 = c.get(f"{loc}/NextDocument").content
        front2 = c.get(f"{loc}/NextDocument").content
        back2 = c.get(f"{loc}/NextDocument").content

        # Front and back of the same sheet are different.
        assert front1 != back1
        assert front2 != back2
        # The two fronts should not be identical either (different content
        # drawn from the random pool).
        assert front1 != front2


def test_duplex_filename_marks_side(tmp_path) -> None:
    """The Content-Disposition filename for a duplex job distinguishes
    front from back (e.g. ``scan-{id}-sheet-01-front.jpg``)."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text(
        '{"host":"0.0.0.0","port":8080,"pages_total":1,"adf_enabled":true}'
    )
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        job_id = loc.rsplit("/", 1)[-1]
        first = c.get(f"{loc}/NextDocument")
        assert "front" in first.headers.get("content-disposition", "").lower()
        second = c.get(f"{loc}/NextDocument")
        assert "back" in second.headers.get("content-disposition", "").lower()
        assert f"scan-{job_id}" in first.headers.get("content-disposition", "")


def test_simplex_job_does_not_double_page_count(client) -> None:
    """A non-duplex job (the default) still produces pages_total pages."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    loc = r.headers["location"]
    info = client.get(f"{loc}/ScanImageInfo").text
    assert "<scan:Images>1</scan:Images>" in info


def test_duplex_false_keeps_default_behaviour(client) -> None:
    """Explicit Duplex=false does the same as no Duplex element at all."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>false</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    loc = r.headers["location"]
    info = client.get(f"{loc}/ScanImageInfo").text
    assert "<scan:Images>1</scan:Images>" in info


def test_duplex_known_content_renders_correctly(tmp_path) -> None:
    """A 1-page duplex job produces 2 rendered pages, each a valid JPEG."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text(
        '{"host":"0.0.0.0","port":8080,"pages_total":1,"adf_enabled":true}'
    )
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        p1 = c.get(f"{loc}/NextDocument")
        p2 = c.get(f"{loc}/NextDocument")
        assert p1.status_code == 200
        assert p2.status_code == 200
        assert p1.content[:3] == b"\xff\xd8\xff"
        assert p2.content[:3] == b"\xff\xd8\xff"
        # 404 on third call.
        assert c.get(f"{loc}/NextDocument").status_code == 404
