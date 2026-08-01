# Mock eSCL Scanner Server

A **fake scanner** for your local network. It speaks the same language as a real
eSCL/AirScan scanner, so any scanner client (macOS Image Capture, iOS Notes,
Windows, Linux `scanimage`, mobile apps, etc.) can discover it on your LAN and
"scan" documents — **no hardware needed**.

The server generates a synthetic test image (PNG / JPEG / TIFF) or a black-and-
white text PDF for every scan job and hands it back through the standard eSCL
HTTP API.

---

## Table of contents

- [What is eSCL?](#what-is-escl)
- [Why use this?](#why-use-this)
- [Protocol conformance](#protocol-conformance)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the server](#running-the-server)
- [Testing it](#testing-it)
- [Configuration](#configuration)
- [HTTP API reference](#http-api-reference)
- [Diagnostic endpoints](#diagnostic-endpoints)
- [How a scan works (end-to-end)](#how-a-scan-works-end-to-end)
- [Project layout](#project-layout)
- [Compatibility matrix](#compatibility-matrix)
- [Troubleshooting](#troubleshooting)
- [Limitations](#limitations)
- [Development](#development)
- [Roadmap](#roadmap)
- [License](#license)

---

## What is eSCL?

**eSCL** (eSCL Scanner Control Language) is a REST/XML protocol defined by HP
and standardised through Mopria. It is the protocol used by AirScan / Mopria
printers to expose their scanner over the network. Modern operating systems
talk to it natively:

| OS | Native client |
|----|---------------|
| macOS | Image Capture |
| iOS / iPadOS | Notes, Files, "Scan Documents" action |
| Windows 10/11 | Windows Scanner Service (Settings → Bluetooth & devices → Printers & scanners) |
| Linux | `sane-airscan` → `scanimage`, SANE frontends (XSane, gscan2pdf), Paperless-ngx |

This server pretends to be one of those devices. It announces itself on the LAN
using **mDNS / DNS-SD** (the same mechanism AirPrint / AirPlay use) so clients
discover it automatically — no manual IP entry required.

## Why use this?

- **Develop scanner integrations without a physical device.** Handy for CI,
  demos, or working from a coffee shop.
- **Reproduce bugs.** Pass `--seed N` for byte-stable scans of the same job.
- **Test how your app behaves with a real scanner client.** Spin this up, point
  macOS Image Capture at it, and observe.
- **Demo offline.** No USB scanners, no drivers, no surprises.

## Protocol conformance

This mock targets the Mopria eSCL specification at the wire level so that
strict clients (sane-airscan, Apple Image Capture, Windows Scanner Service,
Mopria Scan, Paperless-ngx) parse the responses without workarounds.

| Spec area | Implemented | Notes |
|-----------|:-----------:|-------|
| `scan:` namespace prefix in responses | ✅ | Uses `xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"` |
| `pwg:` namespace with PWG Common schema | ✅ | `<pwg:Version>`, `<pwg:State>`, `<pwg:JobState>`, `<pwg:DocumentFormat>`, `<pwg:ContentType>` |
| `<scan:SettingProfile>` block | ✅ | Required by eSCL 2.x clients; nested under `<scan:PlatenInputCaps>` |
| `<scan:eSCLConfigCap>` (state + credentials) | ✅ | `StateSupport` + `ScannerAdminCredentialsSupport` |
| `<scan:CompressionFactorSupport>` | ✅ | Range 1–100, normal 25 (used for JPEG/TIFF) |
| Apple `application/octet-stream` in pdl | ✅ | Required by macOS/iOS for AirScan compatibility |
| `<scan:Adf>` with Simplex + Duplex input caps | ✅ | Advertised only when `adf_enabled: true` |
| `<scan:DocumentFormatExt>` alongside `<pwg:DocumentFormat>` | ✅ | sane-airscan reads both |
| `<pwg:State>` derived from active jobs | ✅ | Reports `Processing` whenever a job is Pending or Processing |
| `<scan:AdfState>` reported when ADF enabled | ✅ | `ScannerAdfLoaded` while jobs exist |
| Per-job `<scan:JobInfo>` with `<pwg:JobStateReasons>` | ✅ | Maps `JobState` to a PWG `JobStateReason` |
| `<scan:ScanRegions>` parsing (offset, size, units) | ✅ | Supports `escl:ThreeHundredthsOfInches` + `escl:Microns` |
| `<scan:Intent>` honored (Preview/TextAndGraphic/Document/Photo) | ✅ | Affects PDF font choice |
| `<scan:CompressionFactor>` honored for JPEG/TIFF | ✅ | Mapped to Pillow `quality` |
| `<scan:Duplex>` accepted | ✅ | Stored on the job (rendering is single-page under "Conformidad base") |
| Color modes: `RGB24`, `Grayscale8`, `BlackAndWhite1` | ✅ | All three respected; BW1 uses Floyd–Steinberg dithering |
| Real PDF output (magic `%PDF-`) | ✅ | Text-document PDF via reportlab, B&W typography |
| `Server` header matches MakeAndModel | ✅ | Some clients (HP, EPSON) enable quirks based on it |
| `Retry-After` on 503 responses | ✅ | Set to `1` second |
| Job TTL = 300 s (PWG recommendation) | ✅ | Background cleanup task |
| Multipart `related` response on NextDocument | ✅ | `ScanImageInfo` + image in one body |
| ADF multi-page | ❌ | One page per job; configured for "Conformidad base" |
| Duplex rendering (front+back) | ❌ | Flag honored but not rendered separately |
| Real duplex document metadata | ❌ | Flag stored, single-page returned |
| `application/octet-stream` raw bytes from scanner | ⚠️ | Declared in capabilities; client receives whatever MIME it requested |
| ScannerFault XML on errors | ❌ | Uses FastAPI's JSON error envelope |

If you need a capability not in the table, see [Roadmap](#roadmap) or open an
issue.

## Features

- Implements the canonical eSCL endpoints (`ScannerCapabilities`,
  `ScannerStatus`, `ScanJobs` create/get/delete, `NextDocument`, `ScanImageInfo`).
- Advertises as `_uscan._tcp.local.` plus the `_universal` subtype so the widest
  range of clients find it.
- Uses the canonical `http://schemas.hp.com/imaging/escl/2011/05/03` namespace.
- Synthetic scanned images generated with Pillow — no real data, no leakage.
- Synthetic text PDFs generated with reportlab — magic bytes `%PDF-`, real
  selectable text, B&W typography.
- Request/response capture to disk for debugging clients.
- Configurable: name, manufacturer, model, platen size, resolutions, color
  modes, scan delay, optional ADF block.
- Pure Python, single process, no database.

## Requirements

| Requirement | Why |
|-------------|-----|
| **Python 3.10+** | Runtime |
| **pip** | Install dependencies |
| **`avahi-daemon`** (Linux only) | The OS daemon that actually publishes mDNS. The server uses `zeroconf` but Linux still needs the daemon installed. |
| **`avahi-utils`** (Linux, optional) | `avahi-browse` to verify discovery from another machine. |
| **`libxml2-utils`** (Linux, optional) | `xmllint` to validate XML responses from the smoke test. |
| **Bonjour Print Services** (Windows only) | Lets Windows see mDNS services. |

Dependencies are listed in `requirements.txt` and installed automatically:
`fastapi`, `uvicorn`, `zeroconf`, `Pillow`, `reportlab`, `pydantic`,
`pydantic-settings`.

## Installation

```bash
# 1. Clone or download
git clone https://github.com/your-org/mock-escl-scanner.git
cd mock-escl-scanner

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .\.venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. Install the package (this also installs dependencies)
pip install -e .
```

The editable install (`-e`) is required so that `python -m mock_escl` and the
`mock-escl` console command find the `src/mock_escl/` package.

## Running the server

```bash
python -m mock_escl --config config/scanner.json
```

You should see:

```text
[INFO] mock_escl.server: Mock eSCL scanner started on 0.0.0.0:8080
[INFO] mock_escl.discovery: Advertising on mDNS at 192.168.1.50:8080
[INFO] mock_escl.discovery: Registered: Python Mock eSCL Scanner._uscan._tcp.local.
[INFO] mock_escl.discovery: Registered: Python Mock eSCL Scanner._universal._sub._uscan._tcp.local.
[INFO] mock_escl: Capabilities: http://0.0.0.0:8080/eSCL/ScannerCapabilities
```

Press `Ctrl+C` to stop. The mDNS registration is cleaned up automatically.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `--config PATH` | `config/scanner.json` | Scanner configuration file. |
| `--host IP` | from config | Bind host. `0.0.0.0` listens on all interfaces. |
| `--port N` | from config | TCP port. |
| `--log-level LEVEL` | `info` | `debug` / `info` / `warning` / `error`. |
| `--no-mdns` | off | Run HTTP only — useful for local testing. |
| `--seed N` | off | Make scans deterministic. Same job_id → same bytes (same image content, same PDF text). |

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_ESCL_CAPTURE_DIR` | `/tmp/mock-escl-captures` | Where every incoming POST body is saved for debugging. Set to an empty string to disable. |

## Testing it

### Quick HTTP smoke test

Open two terminals. In the first, start the server:

```bash
python -m mock_escl --config config/scanner.json --no-mdns
```

In the second, run the bundled smoke test:

```bash
bash scripts/smoke_test.sh
```

It will:

1. GET `/eSCL/ScannerCapabilities` and validate the XML structure.
2. GET `/eSCL/ScannerStatus`.
3. POST a `ScanSettings` body asking for a PNG, poll until Completed.
4. GET `/ScanImageInfo` and verify dimensions match the requested region.
5. GET `/NextDocument` and save the PNG.
6. POST a `ScanSettings` body asking for a PDF; verify the magic bytes are `%PDF`.
7. POST a `ScanSettings` body asking for a JPEG with `CompressionFactor=85`.
8. DELETE all jobs.

Override the URL if the server is on another host or port:

```bash
BASE_URL=http://192.168.1.50:8080 ./scripts/smoke_test.sh
```

### LAN discovery (mDNS)

Verify the server is being advertised:

```bash
# Linux (requires avahi-utils)
avahi-browse -rt _uscan._tcp

# macOS
dns-sd -B _uscan._tcp

# Windows: open Settings → Bluetooth & devices → Printers & scanners
# (requires Bonjour Print Services)
```

You should see an entry whose TXT record contains `txtvers=1`, `rs=eSCL`,
`vers=2.0`, `ty=Mock Inc. ESCL-2000`, `pdl=application/octet-stream,…` and
`cs=color,grayscale` (or `binary` when `BlackAndWhite1` is enabled).

### Using a real client app

If `sane-airscan` is installed on Linux:

```bash
sudo apt install sane-airscan sane-utils
scanimage -L
# device `airscan:e0:Mock Inc.:ESCL-2000' is a Mock Inc. ESCL-2000 flatbed scanner

scanimage --format=png --resolution 300 --output-file test.png
```

On macOS, open **Image Capture** — the scanner should appear in the device list
within a few seconds. Click *Scan*.

On iOS, open **Notes**, create a new note, long-press → **Scan Documents** —
the mock will show up under "Scanners" in the top-right menu.

On Windows, **Settings → Bluetooth & devices → Printers & scanners → Add
device** → "Python Mock eSCL Scanner".

## Configuration

Edit `config/scanner.json` to change what the server advertises:

```json
{
  "host": "0.0.0.0",
  "port": 8080,
  "name": "Python Mock eSCL Scanner",
  "manufacturer": "Mock Inc.",
  "model": "ESCL-2000",
  "serial": "MOCK-001",
  "uuid": "00000000-0000-0000-0000-000000000001",
  "color_modes": ["RGB24", "Grayscale8", "BlackAndWhite1"],
  "resolutions": [75, 150, 200, 300, 600],
  "max_width_mm": 210,
  "max_height_mm": 297,
  "default_format": "image/png",
  "delay_seconds": 0.0,
  "service_type": "_uscan._tcp.local.",
  "adf_enabled": false,
  "duplex_supported": false
}
```

| Field | Meaning |
|-------|---------|
| `host` | Bind address. `0.0.0.0` = all interfaces. |
| `port` | TCP port to bind (default `8080`). |
| `name` | Friendly name advertised via mDNS. |
| `manufacturer` / `model` | Used in `MakeAndModel` and the mDNS TXT record. |
| `serial` / `uuid` | Stable device identifiers. |
| `color_modes` | Color modes advertised in `ScannerCapabilities`. Add `BlackAndWhite1` for 1-bit dithering. |
| `resolutions` | Discrete DPI values. |
| `max_width_mm` / `max_height_mm` | Platen size (A4 = 210×297 by default). |
| `default_format` | Fallback format when the client doesn't request one. |
| `delay_seconds` | Simulated scan time before the image is ready. Defaults to `0.0` so even aggressive pollers see the image on the first NextDocument. Set to `1.0`–`3.0` to simulate a real A4 scan. |
| `service_type` | mDNS service type. Leave as `_uscan._tcp.local.`. |
| `adf_enabled` | When `true`, advertise an automatic document feeder block. |
| `duplex_supported` | Reflected in the `duplex` TXT record (`T` / `F`). |

## HTTP API reference

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/eSCL/ScannerCapabilities` | Device capabilities XML (with `<scan:SettingProfile>`). |
| `GET` | `/eSCL/ScannerStatus` | Current scanner + job state (PWG semantics). |
| `GET` | `/eSCL/ScannerIcon` | Tiny 1×1 PNG icon. |
| `POST` | `/eSCL/ScanJobs` | Submit a `ScanSettings` body. Returns `201` + `Location`. |
| `GET` | `/eSCL/ScanJobs/{id}` | Per-job status XML. |
| `GET` | `/eSCL/ScanJobs/{id}/ScanImageInfo` | Description of the upcoming document (some clients require it). |
| `GET` | `/eSCL/ScanJobs/{id}/NextDocument` | The scanned document (PNG / JPEG / TIFF image, or real PDF). |
| `DELETE` | `/eSCL/ScanJobs/{id}` | Cancel a job in flight (sets `Aborted`) or remove a finished one. |

If the client sends `Accept: multipart/related`, `NextDocument` returns a
multipart response with `ScanImageInfo` XML first, then the document bytes —
matching what strict eSCL clients expect.

### Job lifecycle

```
Pending → Processing → Completed
                    ↘ Failed
                    ↘ Aborted  (triggered by DELETE)
```

Transitions are reported in:

- `/eSCL/ScanJobs/{id}` via `<pwg:JobState>` + `<pwg:JobStateReasons>`.
- `/eSCL/ScannerStatus` via `<pwg:State>` (Idle when no active jobs).
- `NextDocument` returns:
  - `503` + `Retry-After: 1` while Processing,
  - `410` if Aborted,
  - `500` if Failed,
  - `404` once the (single) page has been delivered.

## Diagnostic endpoints

These are not part of the eSCL spec — they are mock-only helpers for debugging
client behaviour, namespaced under `/_mock-admin/` so they never collide with
the device `AdminURI` advertised in capabilities.

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/_mock-admin/last-requests` | Last 20 requests as JSON (method, path, headers, body preview). |
| `GET` | `/_mock-admin/captures` | The capture directory and recent filenames. |

The server also writes every POST body and a `.meta` sidecar file to
`MOCK_ESCL_CAPTURE_DIR`. Inspect those when a client misbehaves.

## How a scan works (end-to-end)

```
1. Client      discovers the scanner via mDNS (_uscan._tcp).
2. Client      GET /eSCL/ScannerCapabilities   →  decides what the device supports.
3. Client      POST /eSCL/ScanJobs  (XML body) →  server creates a job, returns 201
                                                  with Location: /eSCL/ScanJobs/{id}.
4. Server      "scans" (waits delay_seconds, generates an image / PDF).
5. Client      polls GET /eSCL/ScanJobs/{id}   →  sees JobState transition to Completed.
6. Client      GET /eSCL/ScanJobs/{id}/NextDocument  →  receives the document bytes.
7. Client      DELETE /eSCL/ScanJobs/{id}     →  cleans up.
```

The server records each step. If anything goes wrong, check
`/_mock-admin/last-requests` and the capture directory.

## Project layout

```
.
├── README.md                 ← you are here
├── pyproject.toml            ← package metadata + console script
├── requirements.txt
├── .gitignore
│
├── config/
│   └── scanner.json          ← default scanner configuration
│
├── scripts/
│   ├── smoke_test.sh         ← end-to-end HTTP test (PNG + PDF + JPEG)
│   └── discover_test.sh      ← mDNS verification
│
└── src/
    └── mock_escl/
        ├── __init__.py
        ├── __main__.py       ← CLI entry point
        ├── config.py         ← ScannerConfig pydantic model
        ├── discovery.py      ← mDNS / Zeroconf advertiser (full TXT record)
        ├── jobs.py           ← JobManager + renderers (image + PDF)
        ├── models.py         ← Pydantic models + ScanRegion + JobStateReason
        ├── server.py         ← FastAPI app + eSCL endpoints + ET-based parser
        └── images.py         ← Pillow-based synthetic image builder
```

## Compatibility matrix

| Client | Platform | Status |
|--------|----------|--------|
| `sane-airscan` / `scanimage` | Linux | ✅ tested |
| macOS Image Capture | macOS 11+ | ✅ |
| iOS / iPadOS "Scan Documents" | iOS 13+ | ✅ |
| Windows Scanner Service | Win 10/11 | ✅ (requires Bonjour Print Services) |
| Paperless-ngx | cross-platform | ✅ |
| Generic AirScan apps | mobile | ✅ |

The TXT record advertises `protocol=uscan`, `rs=eSCL`, the full document-format
list including `application/octet-stream`, the `cs=` color-mode vocabulary used
by sane-airscan, and a `_universal._sub._uscan._tcp.local.` subtype — matching
real eSCL hardware.

## Troubleshooting

**Server starts but clients can't find it on Linux.**
Make sure `avahi-daemon` is running: `systemctl status avahi-daemon`. On
NetworkManager-managed interfaces you may also need
`systemctl enable avahi-daemon`. Firewalls must allow UDP port 5353 (mDNS) and
the server's TCP port.

**Windows doesn't show the scanner.**
Install **Bonjour Print Services** from Apple's website and reboot. Without it,
Windows can't see mDNS services at all.

**Client connects but the scan fails immediately.**
Run `bash scripts/smoke_test.sh` first — if that works, the issue is in what
the client sends. Hit `http://<server>:8080/_mock-admin/last-requests` to see
the request your client actually made and check the body in
`MOCK_ESCL_CAPTURE_DIR`.

**`avahi-browse` returns nothing from another machine.**
Make sure the server is binding on `0.0.0.0` (not `127.0.0.1`) and that no
firewall blocks UDP 5353 between machines. Some routers block multicast between
Wi-Fi clients — try Ethernet.

**`scanimage -L` lists the device but the scan is reported as "Processing"
forever.**
Increase `delay_seconds` in `config/scanner.json` (the default `0.0` may be
shorter than the time your client takes between ScanJobs and NextDocument).

**PDF output is empty or shows odd fonts.**
Verify that `reportlab` is installed in the active Python environment:
`pip show reportlab`. Without it the server fails to start the PDF renderer.

**Port 8080 already in use.**
Either stop the conflicting service, or change `port` in `config/scanner.json`,
or pass `--port 9090`.

## Limitations

- **One page per job.** ADF is advertised (with `AdfSimplex`/`AdfDuplex` caps)
  but multi-page feeder simulation is not implemented — `GET /NextDocument`
  returns 404 after the first page.
- **Single-page duplex.** `Duplex` is accepted and stored on the job, but the
  back side of a duplex scan is not rendered with different content.
- **No TLS.** Clients requiring `_uscans._tcp.local.` (note the `s`) will
  refuse to connect.
- **No TWAIN Direct.** See the roadmap.

## Development

```bash
# Run with debug logging
python -m mock_escl --config config/scanner.json --log-level debug

# Reproducible scans for snapshot tests
python -m mock_escl --config config/scanner.json --no-mdns --seed 42

# Inspect request traffic while developing a client
watch -n1 'curl -s http://127.0.0.1:8080/_mock-admin/last-requests | tail -40'

# Rebuild after editing
pip install -e .
```

Project conventions:

- Code lives under `src/mock_escl/` (src-layout).
- Dependencies pinned with `>=` (loose). A real lockfile is intentionally
  avoided — the project is small enough that drift isn't worth the friction.
- The `--no-mdns` flag exists so you can run integration checks without
  polluting the LAN.
- `--seed N` produces reproducible scans for snapshot-based integration tests.

## Roadmap

- **Multi-page / feeder simulation** — generate N synthetic pages per job.
- **Real duplex rendering** — different content on front and back of a
  duplex scan.
- **TLS via `_uscans._tcp.local.`** — for clients that require encrypted
  transport.
- **ScannerFault XML errors** — return eSCL-style fault XML rather than
  FastAPI's JSON envelope.
- **pytest suite** — deferred by project decision; smoke + discover scripts
  cover the critical paths today.

## License

MIT.
