# Architecture

This document describes the internal design of the mock eSCL server. If you only want to use the server, you can skip this and go straight to [clients.md](clients.md). If you want to add endpoints, swap renderers, or wire it into a real scanner backend, read on.

## Module layout

```
src/mock_escl/
├── __init__.py        # version marker
├── __main__.py        # CLI entry point (argparse + uvicorn wiring)
├── config.py          # ScannerConfig pydantic model (loads config/scanner.json)
├── discovery.py       # mDNS / DNS-SD advertiser (zeroconf)
├── server.py          # FastAPI app + eSCL endpoints + XML parsers/builders
├── jobs.py            # JobManager + render pipeline (Pillow + reportlab)
├── models.py          # Pydantic models (ScanJob, ScanRegion, JobState, enums)
└── images.py          # Standalone dev util (CLI: render a sample scan to disk)
```

The dependency graph is strictly one-way: `__main__` → `config`, `discovery`, `server` → `jobs` → `models`, `config`. No cycles. No plugin discovery. The package is small enough to read end-to-end in an afternoon.

## Request flow

A scan from a client goes through these steps:

```
1. mDNS discovery
   Client → _uscan._tcp.local. (multicast UDP 5353)
   Server → registers ServiceInfo with TXT record
   Client → reads location/port/TXT
   (see discovery.py for the full TXT record)

2. Capabilities negotiation
   Client → GET /eSCL/ScannerCapabilities
   Server → capabilities_xml(config, request_host) → bytes
   Client → parses namespaces, color modes, resolutions, formats

3. Job creation
   Client → POST /eSCL/ScanJobs (ScanSettings XML)
   Server → _parse_scan_settings(body) → dict
   Server → jobs.create(**dict) → ScanJob
   Server → (if delay=0) await jobs.render_inline(job)  (renders all pages)
   Server → (else) jobs.schedule_process(job)            (asyncio.Task)
   Server → 201 + Location header

4. Job status polling
   Client → GET /eSCL/ScanJobs/{id}
   Server → scan_status_xml(job) → bytes
   Client → reads <pwg:JobState>; if Completed, proceeds to step 5

5. Document delivery
   Client → GET /eSCL/ScanJobs/{id}/NextDocument
   Server → (if state == COMPLETED and pages_delivered < pages_total)
              serve next page bytes
            (if state == PROCESSING)
              503 + <scan:ScannerStatus> + Retry-After: 1
            (if state == FAILED or ABORTED)
              <scan:ScanFault>
            (if pages_delivered >= pages_total)
              404 + <scan:ScanFault>
   Client → (if 200) save bytes; loop to step 4 if multi-page

6. Cleanup
   Client → DELETE /eSCL/ScanJobs/{id}
   Server → jobs.abort(id) (sets abort event, marks state)
```

The state machine is intentionally tiny. There is no retry logic in the server; the client is expected to follow the spec.

## Job lifecycle

```
       POST
        │
        ▼
    ┌──────┐
    │Pending│  (created in jobs.create)
    └──┬───┘
       │  process() starts (inline or scheduled)
       ▼
   ┌──────────┐
   │Processing│  (state set at top of _do_process)
   └────┬─────┘
        │  render finishes
        ▼
  ┌──────────┐
  │Completed │  (job.pages = list[bytes])
  └────┬─────┘
       │  job is evicted after 300 s (JOB_TTL_SECONDS)

  Alternative exits:
  - DELETE during Pending/Processing → Aborted
  - render() raises Exception       → Failed
```

The state transitions are recorded in the per-job status XML as `<pwg:JobState>` + `<pwg:JobStateReason>`. The aggregate `<pwg:State>` in `/eSCL/ScannerStatus` is derived from the union of all active jobs: `Processing` if any are Pending/Processing, `Idle` otherwise.

## Render pipeline

The `JobManager._render_pages` method is the only thing that turns a `ScanJob` into bytes. The dispatch is:

```python
def _render_pages(self, job, count):
    sides_per_sheet = 2 if job.duplex else 1
    total = count * sides_per_sheet
    if job.document_format == "application/pdf":
        return [self._render_text_pdf(job, total, duplex=job.duplex)]  # one multi-page PDF
    return [self._render_color_image(job, page_index=i, page_total=total, side=...)
            for i in range(1, total + 1)]                                # N separate images
```

`_render_color_image` produces one image per page:
- Computes width/height from the requested region (or the full platen)
- Picks Pillow mode based on color mode (RGB / L / 1)
- Sizes the margin based on resolution and clamps it to 1/6 of the smallest dimension (the fix for the user-reported "small region" crash)
- Draws a header with the scanner name, format, color mode, DPI, page number, and (for duplex) the side
- Draws a deterministic body of Lorem-ipsum text
- For BW1, applies Floyd-Steinberg dithering
- For JPEG, applies the requested `CompressionFactor` as quality
- For PDF, produces a single multi-page document with Times-Bold for `Intent=Document`, Helvetica for everything else

The renderer is fully synchronous. The whole `process()` flow is async only because the client-requested `delay_seconds` is implemented as a polled `asyncio.sleep`. With `delay=0`, the render runs inline within the request handler — no task scheduling, no race window, no 503 on a fast client.

## Memory model

The server stores everything in process memory. There is no database. Per-job state lives in `JobManager._jobs: dict[str, ScanJob]`. The 5-minute TTL (`JOB_TTL_SECONDS=300`) is enforced by a background `asyncio.create_task(_cleanup_loop())` that runs every 30 s and evicts completed jobs older than the TTL. The task is owned by the JobManager and cancelled from the FastAPI lifespan.

For multi-page jobs, the rendered pages live in `job.pages: list[bytes]`. A 3-page A4 scan at 150 DPI is roughly 3 × 1.2 MB ≈ 3.6 MB of RAM. A duplex job doubles that. The server does not compress or stream pages — it serves each one as a full `Response` body. For 10+ page scans or 600 DPI, this gets expensive; the recommended workaround is to keep `pages_total ≤ 5` and `resolution ≤ 300` in `config/scanner.json`.

## Capture

Every request that has a body (POST/PUT/PATCH) and a response is recorded in `MOCK_ESCL_CAPTURE_DIR` (default `/tmp/mock-escl-captures`). Three files per request:

- `<stamp>-<METHOD>-<slug>.req.body` — raw inbound body
- `<stamp>-<METHOD>-<slug>.res.body` — truncated outbound body (first 4 KiB)
- `<stamp>-<METHOD>-<slug>.meta` — key headers + correlation id

The capture directory can be set with `MOCK_ESCL_CAPTURE_DIR=/path/to/dir` and disabled with `MOCK_ESCL_CAPTURE_DIR=""`. The directory is created lazily on the first POST. The in-memory buffer of the last 20 requests is always available at `GET /_mock-admin/last-requests` regardless of the capture dir setting.

## Logging

A single log format is used everywhere so timestamps, levels, and logger names are consistent:

```
2026-08-01 18:38:38.731 [INFO   ] mock_escl: Starting mock eSCL scanner: name=...
```

`--log-level debug` enables per-request diagnostics: every request gets a correlation id (`req_id`), and the body of every request/response is logged. The id is also attached to the captured meta file so you can grep `/tmp/mock-escl-captures/*.meta` for the id and see exactly what the server saw.

## mDNS

The advertiser (in `discovery.py`) registers two `ServiceInfo` objects when the service type is `_uscan._tcp.local.`:

1. The base service: `<name>._uscan._tcp.local.` with the full TXT record.
2. A `_universal._sub._uscan._tcp.local.` subtype for broad-casting clients (notably macOS).

The TXT record follows the order real scanners emit:
1. `txtvers=1`
2. `vers=2.0`
3. `rs=eSCL`
4. `protocol=uscan`
5. `ty=<manufacturer> <model>`
6. `mfg`, `mdl`
7. `pdl=application/octet-stream,application/pdf,image/jpeg,image/png,image/tiff`
8. `cs=color,grayscale[,binary]`
9. `is=platen[,adf]`
10. `duplex=T|F`
11. `priority=10`, `Scan=1`, `uuid`, `adminurl`, `representation`, `note`

Order matters for some clients (macOS rejects out-of-order records). The advertiser uses the outbound-interface trick (UDP socket to 8.8.8.8:80) to learn the routable IPv4 and registers that as the service address.

## Failure modes and mitigations

| Failure | Symptom | Mitigation |
|---|---|---|
| PIL margin overflow on small region | `ValueError: x1 must be greater than or equal to x0` in renderer → job `Failed` → `NextDocument` returns 500. | Margin is capped at `min(width, height) // 6` and the rectangle is skipped if no valid box. |
| asyncio race (fast client) | `NextDocument` sees `Pending`/`Processing` → 503 forever. | `process()` runs inline when `delay_seconds <= 0`. |
| asyncio task GC | Task gets garbage-collected before it runs. | `JobManager._tasks` set keeps a strong reference; `add_done_callback` cleans up. |
| mDNS order sensitive | macOS rejects the service. | TXT record is built in the canonical order. |
| Strict client error parsing | `sane-airscan` / Apple Image Capture treat the error as IO_ERROR. | All error responses are `<scan:ScanFault>` XML, never FastAPI's JSON envelope. |
| 503 body not parseable | sane-airscan won't retry. | The 503 body is the same `<scan:ScannerStatus>` XML the GET endpoint emits. |
| Rendered bytes not stable for snapshot tests | Same job_id → different bytes. | `--seed N` deterministically seeds the random body pool. |
| Long-running jobs eating memory | Multi-page renders accumulate in `job.pages`. | 5-minute TTL eviction in the cleanup task. |
