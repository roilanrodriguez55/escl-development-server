# Mock eSCL Scanner Server

A local mock **eSCL/AirScan** scanner server written in Python. It advertises
itself on the LAN via **mDNS/DNS-SD** so that real scanner client applications
(macOS Image Capture, iOS Notes, Windows Scanner Service, Linux `scanimage`
through `sane-airscan`, Paperless-ngx, mobile scanner apps, etc.) can discover
it and submit scan jobs — without any physical hardware.

The server is fully spec-driven:

- Implements the canonical eSCL endpoints (`ScannerCapabilities`,
  `ScannerStatus`, `ScanJobs` create/get/delete, `NextDocument`).
- Uses the `http://schemas.hp.com/eSCL/2012/02` XML namespace that real-world
  clients (notably `sane-airscan`) parse.
- Advertises as `_uscan._tcp.local.` with the `_universal` subtype for maximum
  client compatibility.

## Why

Use it to:

- Develop or test scanner integrations without a physical device.
- Demo eSCL client applications in environments without hardware.
- Reproduce or debug issues that depend on a known, deterministic scanner
  response.

## System requirements

Install these on the machine that will run the server:

- **Python 3.10 or newer.**
- **pip** (bundled with most Python distributions).
- **`avahi-daemon`** running on Linux so the OS lets you announce mDNS
  services. Required even if you don't browse.
- **`avahi-utils`** (optional, but recommended) for `avahi-browse`.

On **Windows**, install **Bonjour Print Services** (Apple) so that mDNS
reception works. The server itself still runs fine under WSL or native Python.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

For an editable install (also gives you the `mock-escl` console script):

```bash
pip install -e .
```

## Run

```bash
python -m mock_escl --config config/scanner.json
```

You should see output similar to:

```text
[INFO] mock_escl.discovery: Advertising on mDNS at 192.168.1.50:8080
[INFO] mock_escl.discovery: Registered: Python Mock eSCL Scanner._uscan._tcp.local.
[INFO] mock_escl.discovery: Registered: Python Mock eSCL Scanner._universal._sub._uscan._tcp.local.
[INFO] mock_escl: Capabilities: http://0.0.0.0:8080/eSCL/ScannerCapabilities
```

CLI flags:

| Flag | Description |
|---|---|
| `--config PATH` | Path to scanner JSON config (default `config/scanner.json`). |
| `--host IP` | Override bind host (default from config). |
| `--port N` | Override bind port (default from config). |
| `--log-level LEVEL` | `debug`, `info`, `warning`, `error`. |
| `--no-mdns` | Run without advertising on mDNS. The HTTP server still works. |

## Manual testing

### Smoke test (HTTP only)

In one terminal:

```bash
python -m mock_escl --config config/scanner.json
```

In another:

```bash
bash scripts/smoke_test.sh
```

This will:

1. Fetch `ScannerCapabilities`.
2. `POST` a `ScanSettings` document and capture the `Location` header.
3. Wait for the simulated scan delay.
4. Download `NextDocument` and save it as `scanned.png`.

### Discovery (Linux)

From another machine (or the same one) with `avahi-utils`:

```bash
bash scripts/discover_test.sh
# or directly:
avahi-browse -rt _uscan._tcp
```

### Discovery (macOS)

```bash
dns-sd -B _uscan._tcp
```

### Discovery (Windows)

Open **Settings → Bluetooth & devices → Printers & scanners**. The mock
scanner should appear in the list within a minute. (Requires Bonjour Print
Services installed.)

### Discovery via SANE (Linux)

If `sane-airscan` is installed, any SANE frontend — including `scanimage` —
will automatically pick up the mock:

```bash
sudo apt install sane-airscan sane-utils
scanimage -L
# expected:
# device `airscan:e0:Mock Inc.:ESCL-2000' is a ...

scanimage --format=png --output-file test.png
```

## Configuration

`config/scanner.json`:

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
|---|---|
| `host` | Bind address. `0.0.0.0` listens on all interfaces. |
| `port` | TCP port to bind. Default `8080`. |
| `name` | Human-friendly scanner name advertised via mDNS. |
| `manufacturer` / `model` | Used in `MakeAndModel` and the mDNS TXT record. |
| `serial` / `uuid` | Stable identifiers for the device. |
| `color_modes` | Color modes advertised in `ScannerCapabilities`. |
| `resolutions` | Discrete DPI values advertised. |
| `max_width_mm` / `max_height_mm` | Platen size in millimetres. |
| `default_format` | Format used when the client doesn't specify one. |
| `delay_seconds` | Simulated scan time before the image is available. |
| `service_type` | mDNS service type (leave as `_uscan._tcp.local.`). |
| `adf_enabled` | Advertise an automatic document feeder block. |
| `duplex_supported` | Reserved for future duplex support. |

## Endpoints exposed

| Method | Path | Purpose |
|---|---|---|
| GET | `/eSCL/ScannerCapabilities` | Device capabilities XML. |
| GET | `/eSCL/ScannerStatus` | Current scanner and job status. |
| GET | `/eSCL/ScannerIcon` | Tiny placeholder icon. |
| POST | `/eSCL/ScanJobs` | Submit a `ScanSettings` body; returns `201` + `Location`. |
| GET | `/eSCL/ScanJobs/{id}` | Per-job status XML. |
| GET | `/eSCL/ScanJobs/{id}/NextDocument` | The scanned document bytes. |
| DELETE | `/eSCL/ScanJobs/{id}` | Cancel a queued job. |

## Compatibility notes

- **Linux**: requires `avahi-daemon` running. The server speaks zeroconf
  directly, but daemon installation is still required on most distros.
- **Windows**: install Bonjour Print Services for mDNS reception.
- **macOS**: works out of the box.
- **PDF**: requests for `application/pdf` are accepted, but the synthetic
  image is encoded as PNG (Pillow cannot write PDF without an extra
  dependency). Adjust `_extension_for` in `src/mock_escl/jobs.py` and add a
  PDF library if you need real PDF output.
- **Real clients**: any AirScan/Mopria-compatible client will work. If you
  find one that doesn't, capture its `ScanSettings` payload to add support
  for fields we currently parse by substring match.

## Firewall

If the server's port is blocked, clients on the LAN won't be able to
connect. Open it:

```bash
# ufw
sudo ufw allow 8080/tcp

# firewalld
sudo firewall-cmd --add-port=8080/tcp --permanent
sudo firewall-cmd --reload
```

## Future work

- **TWAIN Direct** support. Conceptually similar to eSCL — would expose a
  second REST API alongside the existing one. Would broaden real-client
  coverage (notably Windows-native TWAIN Direct apps).
- **Multi-page documents** and feeder simulation.
- **TLS** via `_uscans._tcp.local.` for clients that require it.
- **pytest** suite (deferred per project decision).

## License

MIT.