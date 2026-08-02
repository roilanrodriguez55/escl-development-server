# Configuration

The server reads its configuration from a JSON file (default `config/scanner.json`), overridable via the `--config` CLI flag. Some fields can also be overridden on the command line; the rest is fixed at startup.

## `config/scanner.json` schema

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

  "pages_total": 1,

  "service_type": "_uscan._tcp.local.",

  "adf_enabled": false,
  "duplex_supported": false
}
```

| Field | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | `0.0.0.0` | Bind address. `0.0.0.0` = all interfaces. |
| `port` | int | `8080` | TCP port to bind. |
| `name` | string | `Python Mock eSCL Scanner` | Friendly name advertised via mDNS and shown in the `Server` header. |
| `manufacturer` | string | `Mock Inc.` | Manufacturer part of `MakeAndModel`. |
| `model` | string | `ESCL-2000` | Model part of `MakeAndModel`. |
| `serial` | string | `MOCK-001` | Stable device serial. Surfaced in `<scan:SerialNumber>`. |
| `uuid` | string | `00000000-0000-0000-0000-000000000001` | Stable device UUID. Surface in `<scan:UUID>` and the mDNS `uuid=` TXT record. |
| `color_modes` | list | `["RGB24", "Grayscale8"]` | Color modes advertised in `<scan:ColorMode>`. Add `"BlackAndWhite1"` for 1-bit dithering. |
| `resolutions` | list of int | `[75, 150, 200, 300, 600]` | Discrete DPI values advertised in `<scan:DiscreteResolution>`. |
| `max_width_mm` | int | `210` | Platen width (A4 = 210). |
| `max_height_mm` | int | `297` | Platen height (A4 = 297). |
| `default_format` | string | `image/png` | Fallback MIME when the client request omits `<pwg:DocumentFormat>`. |
| `delay_seconds` | float | `0.0` | Simulated scan time. `0.0` = render inline. `1.0`–`3.0` mimics a real A4 scan. |
| `pages_total` | int | `1` | Number of pages per ADF job. `1` = single page. Set higher to simulate a feeder. Doubled when `scan:Duplex=true`. |
| `service_type` | string | `_uscan._tcp.local.` | mDNS service type. The `_universal._sub._uscan._tcp.local.` subtype is also registered. |
| `adf_enabled` | bool | `false` | When `true`, advertise an automatic document feeder block. |
| `duplex_supported` | bool | `false` | When `true`, the `duplex` TXT record is `T`; otherwise `F`. The actual duplex rendering is driven by the per-request `<scan:Duplex>`. |

## CLI flags

```
python -m mock_escl --config config/scanner.json
```

| Flag | Default | Description |
|---|---|---|
| `--config PATH` | `config/scanner.json` | Path to the scanner configuration JSON. |
| `--host IP` | from config | Override the bind host. |
| `--port N` | from config | Override the bind port. |
| `--log-level LEVEL` | `info` | One of `debug`, `info`, `warning`, `error`. |
| `--log-file PATH` | none | Optional path to a log file. The file always captures everything (DEBUG and up); stdout respects `--log-level`. |
| `--no-mdns` | off | Disable mDNS advertisement (server is still reachable via HTTP). |
| `--seed N` | off | Make scans deterministic. Same `job_id` → same bytes. Same input → same rendered text and timestamps. |

The hostname reported in the `Location` header of POST /ScanJobs is taken from the request's `Host` header. The fallback for direct IP access is the LAN IP that the server discovered on startup.

## Environment variables

| Variable | Default | Description |
|---|---|---|
| `MOCK_ESCL_CAPTURE_DIR` | `/tmp/mock-escl-captures` | Where every incoming POST body and a `.meta` sidecar are written. Set to an empty string to disable capture. |

## Recommended profiles

### Bare-bones single-page (default)

```json
{
  "color_modes": ["RGB24", "Grayscale8"],
  "resolutions": [75, 150, 200, 300, 600],
  "default_format": "image/png",
  "delay_seconds": 0.0,
  "pages_total": 1,
  "adf_enabled": false,
  "duplex_supported": false
}
```

### Apple AirScan (macOS / iOS)

```json
{
  "name": "Mock AirScan",
  "manufacturer": "Apple",
  "model": "VirtualScanner",
  "default_format": "application/octet-stream",
  "color_modes": ["RGB24", "Grayscale8"],
  "resolutions": [75, 150, 200, 300, 600]
}
```

`application/octet-stream` is the MIME macOS Image Capture and iOS Notes request most often. The server honours it on the NextDocument response and renders the bytes as PNG by default.

### Multi-page ADF with optional duplex

```json
{
  "name": "Mock ADF Scanner",
  "manufacturer": "Mock Inc.",
  "model": "ESCL-ADF",
  "pages_total": 3,
  "adf_enabled": true,
  "duplex_supported": true
}
```

A client that sends `<scan:Duplex>true</scan:Duplex>` gets 6 pages back (3 sheets × 2 sides). A client that omits Duplex or sends `<scan:Duplex>false</scan:Duplex>` gets 3 pages.

### Realistic scan latency

```json
{
  "delay_seconds": 2.0
}
```

This makes the server wait 2 seconds before the job transitions to `Completed`. Useful when you want to test how your client handles the `<pwg:State>Processing</pwg:State>` polling path and the 503 + `Retry-After: 1` retry behaviour.

### Snapshot-test friendly

```bash
python -m mock_escl --config config/scanner.json --no-mdns --seed 42
```

Identical requests produce byte-identical output. Useful for snapshot tests, deterministic CI, and recording expected baselines.
