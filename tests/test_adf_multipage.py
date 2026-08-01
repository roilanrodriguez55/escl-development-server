"""TDD: multi-page (ADF) support.

Real scanners with an Automatic Document Feeder deliver one page per
``NextDocument`` call. The mock used to return 404 after the first
page. This suite makes the multi-page contract explicit and verifies
that the server delivers N pages in order, marks the job as
Completed only when all pages are rendered, and that 404 fires only
after the last page has been delivered.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

ESCL_NS = "http://schemas.hp.com/imaging/escl/2011/05/03"
PWG_NS = "http://www.pwg.org/schemas/2010/12/sm"


def test_pages_total_default_is_one(client) -> None:
    """A job with no pages_total override has pages_total=1 (single page)."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    assert r.status_code == 201
    info = client.get(f"{r.headers['location']}/ScanImageInfo").text
    assert "<scan:Images>1</scan:Images>" in info


def test_adf_config_can_be_set_via_config_file(tmp_path) -> None:
    """A custom config with pages_total=3 yields a 3-page job."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text('{"host":"0.0.0.0","port":8080,"pages_total":3}')
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 201
        loc = r.headers["location"]

        # First page.
        p1 = c.get(f"{loc}/NextDocument")
        assert p1.status_code == 200
        # Second page.
        p2 = c.get(f"{loc}/NextDocument")
        assert p2.status_code == 200
        # Third page.
        p3 = c.get(f"{loc}/NextDocument")
        assert p3.status_code == 200
        # Fourth page: 404.
        p4 = c.get(f"{loc}/NextDocument")
        assert p4.status_code == 404

        # The 3 pages should be different (different page numbers stamped).
        assert p1.content != p2.content
        assert p2.content != p3.content
        assert p1.content != p3.content


def test_adf_page_count_in_scanimage_info(tmp_path) -> None:
    """ScanImageInfo reflects the configured total page count."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text('{"host":"0.0.0.0","port":8080,"pages_total":4}')
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        assert r.status_code == 201
        loc = r.headers["location"]
        info = c.get(f"{loc}/ScanImageInfo").text
        assert "<scan:Images>4</scan:Images>" in info


def test_adf_job_status_reports_images_to_transfer(tmp_path) -> None:
    """The per-job status shows ImagesToTransfer decreasing as pages are
    delivered and ImagesCompleted increasing."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text('{"host":"0.0.0.0","port":8080,"pages_total":3}')
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]

        before = c.get(loc).text
        assert "<pwg:ImagesToTransfer>3</pwg:ImagesToTransfer>" in before
        assert "<pwg:ImagesCompleted>0</pwg:ImagesCompleted>" in before

        c.get(f"{loc}/NextDocument")
        mid = c.get(loc).text
        assert "<pwg:ImagesToTransfer>2</pwg:ImagesToTransfer>" in mid
        assert "<pwg:ImagesCompleted>1</pwg:ImagesCompleted>" in mid

        c.get(f"{loc}/NextDocument")
        c.get(f"{loc}/NextDocument")
        after = c.get(loc).text
        assert "<pwg:ImagesToTransfer>0</pwg:ImagesToTransfer>" in after
        assert "<pwg:ImagesCompleted>3</pwg:ImagesCompleted>" in after


def test_adf_pages_have_page_number_visible(tmp_path) -> None:
    """Each rendered page has its page number visible in the image so
    the client (and humans inspecting the output) can tell pages apart.
    """
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg_path = tmp_path / "scanner.json"
    cfg_path.write_text('{"host":"0.0.0.0","port":8080,"pages_total":3}')
    cfg = ScannerConfig.load(cfg_path)
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        page_bytes = []
        for _ in range(3):
            page_bytes.append(c.get(f"{loc}/NextDocument").content)
        # All three should be valid JPEGs.
        for b in page_bytes:
            assert b[:3] == b"\xff\xd8\xff"
        # And they should be different (different page numbers).
        assert len(set(page_bytes)) == 3


def test_default_config_is_single_page(client) -> None:
    """The default config (no pages_total override) produces 1-page jobs."""
    r = client.post(
        "/eSCL/ScanJobs",
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
        headers={"Content-Type": "application/xml"},
    )
    loc = r.headers["location"]
    first = client.get(f"{loc}/NextDocument")
    assert first.status_code == 200
    second = client.get(f"{loc}/NextDocument")
    assert second.status_code == 404


def test_pages_total_zero_or_negative_falls_back_to_one() -> None:
    """Defensive: a misconfigured pages_total=0 should still deliver 1 page."""
    from fastapi.testclient import TestClient
    from mock_escl.config import ScannerConfig
    from mock_escl.server import create_app

    cfg = ScannerConfig.model_construct(
        host="0.0.0.0", port=8080, pages_total=0,
    )
    app = create_app(cfg, seed=42)
    with TestClient(app) as c:
        r = c.post(
            "/eSCL/ScanJobs",
            content=f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NS}" xmlns:pwg="{PWG_NS}">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
</scan:ScanSettings>""",
            headers={"Content-Type": "application/xml"},
        )
        loc = r.headers["location"]
        first = c.get(f"{loc}/NextDocument")
        assert first.status_code == 200
        second = c.get(f"{loc}/NextDocument")
        assert second.status_code == 404
