"""A job must not report ``Completed`` before its pages are transferred.

macOS (AirScanScanner) polls ``ScannerStatus`` right after POSTing the
ScanSettings and, before calling ``NextDocument``, reads the JobInfo for the
job it just created. A job already in ``Completed`` with ``ImagesCompleted``
of 0 reads as "the scan finished and produced nothing", so macOS gives up and
DELETEs the job instead of fetching the page — Image Capture then shows
"Failed to open a connection to the device (-21345)".

sane-airscan never noticed because it goes straight to ``NextDocument``
without consulting the job state.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from mock_escl.server import ESCL_NAMESPACE, PWG_NAMESPACE

NS = {"scan": ESCL_NAMESPACE, "pwg": PWG_NAMESPACE}

SETTINGS = f"""<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="{ESCL_NAMESPACE}" xmlns:pwg="{PWG_NAMESPACE}">
  <scan:Intent>TextAndGraphic</scan:Intent>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>
"""


def _job_info(client, job_id: str) -> ET.Element:
    """Return the ``<scan:JobInfo>`` block for ``job_id`` from ScannerStatus."""
    status = client.get("/eSCL/ScannerStatus")
    assert status.status_code == 200
    root = ET.fromstring(status.content)
    for info in root.findall("scan:Jobs/scan:JobInfo", NS):
        if info.findtext("pwg:JobUuid", "", NS) == job_id:
            return info
    raise AssertionError(f"job {job_id} missing from ScannerStatus")


def _create(client) -> str:
    created = client.post(
        "/eSCL/ScanJobs",
        content=SETTINGS,
        headers={"Content-Type": "text/xml"},
    )
    assert created.status_code == 201
    return created.headers["Location"].rsplit("/", 1)[-1]


def test_job_is_processing_until_page_is_fetched(client):
    """Before NextDocument, the job is still Processing, not Completed."""
    job_id = _create(client)

    info = _job_info(client, job_id)
    assert info.findtext("pwg:JobState", "", NS) == "Processing"
    assert info.findtext("pwg:ImagesToTransfer", "", NS) == "1"
    assert info.findtext("pwg:ImagesCompleted", "", NS) == "0"


def test_job_completes_once_the_page_is_transferred(client):
    """After NextDocument delivers the last page, the job is Completed."""
    job_id = _create(client)

    page = client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")
    assert page.status_code == 200
    assert page.content[:3] == b"\xff\xd8\xff"  # JPEG magic

    info = _job_info(client, job_id)
    assert info.findtext("pwg:JobState", "", NS) == "Completed"
    assert info.findtext("pwg:ImagesToTransfer", "", NS) == "0"
    assert info.findtext("pwg:ImagesCompleted", "", NS) == "1"


def test_scanner_reports_processing_while_a_page_is_pending(client):
    """The aggregate scanner state stays busy until the page is picked up."""
    job_id = _create(client)

    root = ET.fromstring(client.get("/eSCL/ScannerStatus").content)
    assert root.findtext("pwg:State", "", NS) == "Processing"

    client.get(f"/eSCL/ScanJobs/{job_id}/NextDocument")

    root = ET.fromstring(client.get("/eSCL/ScannerStatus").content)
    assert root.findtext("pwg:State", "", NS) == "Idle"
