# Mock eSCL Scanner Server

A **fake scanner** for your local network. It speaks the same language as a real
eSCL/AirScan scanner, so any scanner client (macOS Image Capture, iOS Notes,
Windows, Linux `scanimage`, mobile apps, etc.) can discover it on your LAN and
"scan" documents — **no hardware needed**.

The server generates a synthetic test image for every scan job and hands it
back through the standard eSCL HTTP API.

---

## Table of contents

- [What is eSCL?](#what-is-escl)
- [Why use this?](#why-use-this)
- [Features](#features)
- [Requirements](#requirements)
- [Installation](#installation)
- [Running the server](#running-the-server)
- [Testing it](#testing-it)
  - [Quick HTTP smoke test](#quick-http-smoke-test)
  - [LAN discovery (mDNS)](#lan-discovery-mdns)
  - [Using a real client app](#using-a-real-client-app)
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
- **Reproduce bugs.** The "scan" is deterministic: same settings → same image.
- **Test how your app behaves with a real scanner client.** Spin this up, point
  macOS Image Capture at it, and observe.
- **Demo offline.** No USB scanners, no drivers, no surprises.

## Features

- Implements the canonical eSCL endpoints (`ScannerCapabilities`,
  `ScannerStatus`, `ScanJobs` create/get/delete, `NextDocument`, `ScanImageInfo`).
- Advertises as `_uscan._tcp.local.` plus the `_universal` subtype so the widest
  range of clients find it.
- Uses the official `http://schemas.hp.com/eSCL/2012/02` XML namespace.
- Synthetic scanned images generated with Pillow — no real data, no leakage.
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
| **Bonjour Print Services** (Windows only) | Lets Windows see mDNS services. |

Dependencies are listed in `requirements.txt` and installed automatically:
`fastapi`, `uvicorn`, `zeroconf`, `Pillow`, `pydantic`, `pydantic-settings`.

## Installation

```bash
# 1. Clone or download
git clone https://github.com/your-org/mock-escl-scanner.git
cd mock-escl-scanner

# 2. Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .\.venv\Scripts\Activate.ps1   # Windows PowerShell

# 3. Install dependencies
pip install -r requirements.txt

# 4. (Optional) Install as a package — gives you the `mock-escl` command
pip install -e .
```

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

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MOCK_ESCL_CAPTURE_DIR` | `/tmp/mock-escl-captures` | Where every incoming POST body is saved for debugging. Set to an empty string to disable. |

## Testing it

### Quick HTTP smoke test

Open two terminals. In the first, start the server:

```bash
python -m mock_escl --config config/scanner.json
```

In the second, run the bundled smoke test:

```bash
bash scripts/smoke_test.sh
```

It will:

1. GET `/eSCL/ScannerCapabilities` and print the first 25 lines of XML.
2. POST a `ScanSettings` body and capture the `Location` header it gets back.
3. Wait a couple of seconds (the configured scan delay).
4. GET `…/NextDocument` and save it as `scanned.png`.

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

You should see an entry with the name from your config file
(`Python Mock eSCL Scanner` by default).

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
  "color_modes": ["RGB24", "Grayscale8"],
  "resolutions": [75, 150, 200, 300, 600],
  "max_width_mm": 210,
  "max_height_mm": 297,
  "default_format": "image/png",
  "delay_seconds": 2.0,
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
| `color_modes` | Color modes advertised in `ScannerCapabilities`. |
| `resolutions` | Discrete DPI values. |
| `max_width_mm` / `max_height_mm` | Platen size (A4 = 210×297 by default). |
| `default_format` | Fallback format when the client doesn't request one. |
| `delay_seconds` | Simulated scan time before the image is ready. |
| `service_type` | mDNS service type. Leave as `_uscan._tcp.local.`. |
| `adf_enabled` | When `true`, advertise an automatic document feeder block. |
| `duplex_supported` | Reserved. |

## HTTP API reference

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/eSCL/ScannerCapabilities` | Device capabilities XML. |
| `GET` | `/eSCL/ScannerStatus` | Current scanner + job state. |
| `GET` | `/eSCL/ScannerIcon` | Tiny 1×1 PNG icon. |
| `POST` | `/eSCL/ScanJobs` | Submit a `ScanSettings` body. Returns `201` + `Location`. |
| `GET` | `/eSCL/ScanJobs/{id}` | Per-job status XML. |
| `GET` | `/eSCL/ScanJobs/{id}/ScanImageInfo` | Description of the upcoming document (some clients require it). |
| `GET` | `/eSCL/ScanJobs/{id}/NextDocument` | The scanned image bytes (PNG / JPEG / TIFF; PDF returned as PNG). |
| `DELETE` | `/eSCL/ScanJobs/{id}` | Cancel a job. |

If the client sends `Accept: multipart/related`, `NextDocument` returns a
multipart response with `ScanImageInfo` XML first, then the image bytes —
matching what strict eSCL clients expect.

## Diagnostic endpoints

These are not part of the eSCL spec — they are mock-only helpers for debugging
client behaviour:

| Method | Path | Returns |
|--------|------|---------|
| `GET` | `/admin/last-requests` | Last 20 requests as JSON (method, path, headers, body preview). |
| `GET` | `/admin/captures` | The list of files captured to disk. |

The server also writes every POST body and a `.meta` sidecar file to
`MOCK_ESCL_CAPTURE_DIR`. Inspect those when a client misbehaves.

## How a scan works (end-to-end)

```
1. Client      discovers the scanner via mDNS (_uscan._tcp).
2. Client      GET /eSCL/ScannerCapabilities   →  decides what the device supports.
3. Client      POST /eSCL/ScanJobs  (XML body) →  server creates a job, returns 201
                                                 with Location: /eSCL/ScanJobs/{id}.
4. Server      "scans" (waits delay_seconds, generates an image with Pillow).
5. Client      polls GET /eSCL/ScanJobs/{id}   →  sees JobState transition to Completed.
6. Client      GET /eSCL/ScanJobs/{id}/NextDocument  →  receives the image bytes.
7. Client      DELETE /eSCL/ScanJobs/{id}     →  cleans up.
```

The server records each step. If anything goes wrong, check
`/admin/last-requests` and the capture directory.

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
│   ├── smoke_test.sh         ← end-to-end HTTP test
│   └── discover_test.sh      ← mDNS verification
│
└── src/
    └── mock_escl/
        ├── __init__.py
        ├── __main__.py       ← CLI entry point
        ├── config.py         ← ScannerConfig pydantic model
        ├── discovery.py      ← mDNS / Zeroconf advertiser
        ├── jobs.py           ← JobManager, ScanJob, synthetic image generation
        ├── models.py         ← Pydantic models
        ├── server.py         ← FastAPI app + eSCL endpoints
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

The TXT record advertises `protocol=uscan`, `rs=eSCL`, standard color/format
fields, and a `_universal._sub._uscan._tcp.local.` subtype — the same shape
real eSCL hardware emits.

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
the client sends. Hit `http://<server>:8080/admin/last-requests` to see the
request your client actually made and check the body in
`MOCK_ESCL_CAPTURE_DIR`.

**`avahi-browse` returns nothing from another machine.**
Make sure the server is binding on `0.0.0.0` (not `127.0.0.1`) and that no
firewall blocks UDP 5353 between machines. Some routers block multicast between
Wi-Fi clients — try Ethernet.

**`scanimage -L` lists the device but `scanimage` errors out.**
The mock returns PNG bytes even when the client asks for PDF (Pillow can't
write PDF without extra deps). See the *Limitations* section.

**Port 8080 already in use.**
Either stop the conflicting service, or change `port` in `config/scanner.json`,
or pass `--port 9090`.

## Limitations

- **PDF output is fake.** Requests for `application/pdf` succeed, but the bytes
  are encoded as PNG (Pillow can't write PDF without `reportlab` / `fpdf2`).
  Add a real PDF library and update `_extension_for` in `src/mock_escl/jobs.py`
  if you need genuine PDF.
- **Single-page scans only.** No feeder simulation; every job has one page.
- **No duplex support.** `duplex_supported` is reserved.
- **No TLS.** Clients requiring `_uscans._tcp.local.` (note the `s`) will
  refuse to connect.
- **No TWAIN Direct.** See the roadmap.

## Development

```bash
# Run with debug logging
python -m mock_escl --config config/scanner.json --log-level debug

# Inspect request traffic while developing a client
watch -n1 'curl -s http://127.0.0.1:8080/admin/last-requests | tail -40'

# Rebuild after editing
pip install -e .
```

Project conventions:

- Code lives under `src/mock_escl/` (src-layout).
- Dependencies pinned with `>=` (loose). A real lockfile is intentionally
  avoided — the project is small enough that drift isn't worth the friction.
- The `--no-mdns` flag exists so you can run unit-style checks without
  polluting the LAN.
- No test suite yet (see roadmap).

## Roadmap

- **TWAIN Direct** support — a second REST API alongside eSCL, widening
  Windows-native client coverage.
- **Multi-page / feeder simulation** — generate N synthetic pages per job.
- **TLS via `_uscans._tcp.local.`** — for clients that require encrypted
  transport.
- **pytest suite** — deferred by project decision; smoke + discover scripts
  cover the critical paths today.

## License

MIT.