# Mock eSCL Scanner Server

A **fake scanner** for your local network. It speaks the same language as a real eSCL/AirScan scanner, so any scanner client (macOS Image Capture, iOS Notes, Windows, Linux `scanimage`, mobile apps, etc.) can discover it on your LAN and "scan" documents — **no hardware needed**.

The server generates a synthetic test image (PNG / JPEG / TIFF) or a black-and-white text PDF for every scan job and hands it back through the standard eSCL HTTP API.

## Quick start

```bash
# Install
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Run (advertises on mDNS so real clients can find it)
python -m mock_escl --config config/scanner.json

# Discover from another machine
avahi-browse -rt _uscan._tcp        # Linux
dns-sd -B _uscan._tcp               # macOS
# Or open Image Capture / Notes / Printers & Scanners on the client OS
```

## Documentation index

| Document | Contents |
|---|---|
| [docs/api.md](docs/api.md) | Full HTTP API reference — every endpoint, every status code, every request/response body, full XML schemas |
| [docs/architecture.md](docs/architecture.md) | Internal design — module layout, job lifecycle, render pipeline, mDNS, failure modes and mitigations |
| [docs/configuration.md](docs/configuration.md) | `config/scanner.json` schema, all CLI flags, all environment variables, recommended profiles |
| [docs/errors.md](docs/errors.md) | `<scan:ScanFault>` XML envelope, when each error fires, the 503 special case, debugging recipes |
| [docs/clients.md](docs/clients.md) | sane-airscan, macOS Image Capture, iOS Notes, Windows Scanner Service, python-escl / Paperless-ngx — per-client quirks and reproduction recipes |
| [docs/development.md](docs/development.md) | Running the test suite, adding endpoints, debugging live traffic, project conventions |

## Make targets

Every common command is wrapped in a `make` target. `make help` lists them all.

| Command | What it does |
|---|---|
| `make install` | Create `.venv` and install the package + test deps. |
| `make run` | Run the server with mDNS in the background. |
| `make run-no-mdns` | Run the server without mDNS (local testing). |
| `make run-debug` | Run with `--log-level debug` for body-level logging. |
| `make run-seed` | Run with deterministic seed (same `job_id` → same bytes). |
| `make run-apple` | Run with Apple AirScan config (`application/octet-stream` default). |
| `make run-adf` | Run with ADF config (3 pages, duplex-enabled). |
| `make stop` | Kill any background server. |
| `make status` | Show whether the server is running, what port. |
| `make test` | Run the full pytest suite (63 tests). |
| `make smoke` | Run the bash smoke test against a running server. |
| `make discover` | Browse the LAN for eSCL services via zeroconf. |
| `make scan` | Run `scanimage` against the mock (saves to `/tmp/scan-out.png`). |
| `make scan-batch` | Run `scanimage -b` for ADF batch (3 pages). |
| `make clean` | Remove `__pycache__`, build artifacts, `/tmp` scan outputs. |
| `make fresh` | `clean-all && install && run` from scratch. |

## What is eSCL?

**eSCL** (Scanner Control Language) is a REST/XML protocol defined by HP and standardised through Mopria. It is the protocol used by AirScan / Mopria printers to expose their scanner over the network. Modern operating systems talk to it natively:

| OS | Native client |
|---|---|
| macOS | Image Capture |
| iOS / iPadOS | Notes, Files, "Scan Documents" action |
| Windows 10/11 | Windows Scanner Service (Settings → Bluetooth & devices → Printers & scanners) |
| Linux | `sane-airscan` → `scanimage`, SANE frontends (XSane, gscan2pdf), Paperless-ngx |

This server pretends to be one of those devices. It announces itself on the LAN using **mDNS / DNS-SD** (the same mechanism AirPrint / AirPlay use) so clients discover it automatically — no manual IP entry required.

## Features

- Canonical eSCL endpoints (`ScannerCapabilities`, `ScannerStatus`, `ScanJobs` create/get/delete, `NextDocument`, `ScanImageInfo`).
- Advertises as `_uscan._tcp.local.` plus the `_universal` subtype so the widest range of clients find it.
- Uses the canonical `http://schemas.hp.com/imaging/escl/2011/05/03` namespace.
- Real `<scan:ScanFault>` XML envelope on every error — strict clients (sane-airscan, Apple Image Capture) parse them as eSCL, not JSON.
- Multi-page (ADF) support — `pages_total` config delivers N pages per job via repeated `NextDocument` calls.
- Duplex rendering — `<scan:Duplex>true</scan:Duplex>` produces 2N pages with distinct front/back content.
- `application/octet-stream` honored end-to-end for Apple AirScan clients.
- Synthetic scanned images generated with Pillow — no real data, no leakage.
- Synthetic text PDFs generated with reportlab — magic bytes `%PDF-`, real selectable text, B&W typography.
- Request/response capture to disk for debugging clients (`MOCK_ESCL_CAPTURE_DIR`).
- Configurable: name, manufacturer, model, platen size, resolutions, color modes, scan delay, ADF page count, optional duplex.
- Pure Python, single process, no database.

## Protocol conformance

| Spec area | Implemented | Notes |
|---|---|---|
| `scan:` namespace prefix in responses | yes | `xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"` |
| `pwg:` namespace with PWG Common schema | yes | `<pwg:Version>`, `<pwg:State>`, `<pwg:JobState>`, `<pwg:DocumentFormat>`, `<pwg:ContentType>` |
| `<scan:SettingProfile>` block | yes | Nested under `<scan:PlatenInputCaps>` |
| `<scan:eSCLConfigCap>` (state + credentials) | yes | `StateSupport` + `ScannerAdminCredentialsSupport` |
| `<scan:CompressionFactorSupport>` | yes | Range 1–100, normal 25 (used for JPEG/TIFF) |
| Apple `application/octet-stream` in pdl | yes | Required by macOS/iOS for AirScan compatibility |
| `<scan:Adf>` with Simplex + Duplex input caps | yes | Advertised when `adf_enabled: true` |
| `<scan:DocumentFormatExt>` alongside `<pwg:DocumentFormat>` | yes | sane-airscan reads both |
| `<pwg:State>` derived from active jobs | yes | `Processing` whenever a job is Pending or Processing |
| `<scan:AdfState>` reported when ADF enabled | yes | `ScannerAdfLoaded` while jobs exist |
| Per-job `<scan:JobInfo>` with `<pwg:JobStateReasons>` | yes | Maps `JobState` to a PWG `JobStateReason` |
| `<scan:ScanRegions>` parsing (offset, size, units) | yes | Supports `escl:ThreeHundredthsOfInches` + `escl:Microns` |
| `<scan:Intent>` honored (Preview/TextAndGraphic/Document/Photo) | yes | Affects PDF font choice |
| `<scan:CompressionFactor>` honored for JPEG/TIFF | yes | Mapped to Pillow `quality` |
| `<scan:Duplex>` accepted and rendered | yes | Doubles page count, distinct front/back content |
| Color modes: `RGB24`, `Grayscale8`, `BlackAndWhite1` | yes | All three respected; BW1 uses Floyd–Steinberg dithering |
| Real PDF output (magic `%PDF-`) | yes | Text-document PDF via reportlab, B&W typography |
| `Server` header matches MakeAndModel | yes | Some clients (HP, EPSON) enable quirks based on it |
| `Retry-After` on 503 responses | yes | Set to `1` second |
| Job TTL = 300 s (PWG recommendation) | yes | Background cleanup task |
| Multipart `related` response on NextDocument | yes | `ScanImageInfo` + image in one body |
| `<scan:ScanFault>` on every error | yes | Strict clients parse errors as eSCL |
| ADF multi-page (looping NextDocument) | yes | N pages per job, 404 only after the last page |
| Duplex rendering (front+back, distinct content) | yes | 2N pages for N sheets, separate content pool |
| `application/octet-stream` raw bytes end-to-end | yes | Apple AirScan compatibility |
| TLS via `_uscans._tcp.local.` | not yet | See [Roadmap](#roadmap) |
| ScannerFault XML envelope | yes | See [docs/errors.md](docs/errors.md) |

## License

MIT.
