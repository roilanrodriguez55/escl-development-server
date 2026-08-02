# Clients

What real scanner clients do, what to expect, and how to reproduce.

## Linux: `sane-airscan` + `scanimage`

**Status**: fully working. Tested end-to-end with sane-airscan 0.99.36 on Ubuntu.

### Discovery

```bash
sudo apt install sane-airscan sane-utils
# Optional: avahi-utils for the bonjour browser
sudo apt install avahi-utils

scanimage -L
# device `airscan:e0:Python Mock eSCL Scanner' is a eSCL Python Mock eSCL Scanner ip=192.168.1.4
```

The `e0:` prefix is sane-airscan's vendor id for "extended eSCL". Real HP scanners would show as `airscan:w1:` (WSD) or `airscan:e0:` (eSCL) depending on the protocol they speak.

### Single-page scan

```bash
scanimage --format=png --resolution 150 \
  -d 'airscan:e0:Python Mock eSCL Scanner' \
  --output-file /tmp/scan.png
file /tmp/scan.png
# /tmp/scan.png: PNG image data, 1240 x 1753, 8-bit/color RGB
```

### Multi-page ADF batch

```bash
scanimage -b --format=jpeg --resolution 150 \
  -d 'airscan:e0:ADF Mock Scanner' \
  --batch-count=3
# Scanning 3 pages, incrementing by 1, numbering from 1
# Scanning page 1
# Scanned page 1. (scanner status = 5)
# Scanning page 2
# Scanned page 2. (scanner status = 5)
# Scanning page 3
# Scanned page 3. (scanner status = 5)
# Batch terminated, 3 pages scanned

ls -la out*.jpg
# out1.jpg  out2.jpg  out3.jpg
```

The `scanner status = 5` line comes from sane-airscan and means "no error" (PWG `JobStateReason = None`). It's expected.

### Duplex scan

`sane-airscan` does not yet support duplex in the `--source` flag of `scanimage`. To test duplex, send the ScanSettings body directly:

```bash
curl -X POST http://192.168.1.4:8080/eSCL/ScanJobs \
  -H "Content-Type: application/xml" \
  --data-binary @- <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:Duplex>true</scan:Duplex>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>
EOF

# -> 201, Location: /eSCL/ScanJobs/{uuid}
# Pull the 4 pages (1 sheet × 2 sides) one at a time, then the 404.
```

### Color modes and resolution

```bash
# 300 DPI RGB
scanimage --format=png --resolution 300 \
  -d 'airscan:e0:Python Mock eSCL Scanner' \
  --output-file /tmp/scan-300.png

# Grayscale
scanimage --format=png --mode Gray --resolution 200 \
  -d 'airscan:e0:Python Mock eSCL Scanner' \
  --output-file /tmp/scan-gray.png
```

If you ask for `BlackAndWhite1` mode but the server's `color_modes` doesn't include it, the server snaps to the first advertised mode (default `RGB24`). Add `"BlackAndWhite1"` to `config/scanner.json` to enable it.

## macOS: Image Capture

**Status**: working on macOS 11+. Tested via mDNS discovery and AirScan.

### Discovery

1. Open **Image Capture** (located at `/Applications/Utilities/Image Capture.app`).
2. The mock scanner appears in the device list as `Python Mock eSCL Scanner` or whatever you set in `config/scanner.json → name`.
3. Click the device.
4. Click **Scan** in the preview pane.

The first scan takes a few seconds because macOS negotiates the format and resolution. Subsequent scans are instant.

### Format selection

macOS Image Capture always sends `application/octet-stream` first and then negotiates. The server's `Content-Type: application/octet-stream` is honored end-to-end. To force macOS to a specific format, configure the default in `config/scanner.json`:

```json
{
  "default_format": "image/jpeg"
}
```

macOS will then ask for `image/jpeg` and the server will return JPEG bytes with the JPEG Content-Type.

### iOS / iPadOS: Notes, Files, "Scan Documents"

**Status**: working on iOS 13+.

1. Open **Notes**, create a new note.
2. Long-press → **Scan Documents**.
3. The mock appears in the top-right menu under "Scanners".
4. Select it, take a snapshot or capture a page.

iOS also uses `application/octet-stream` and follows the same flow as macOS.

## Windows: Windows Scanner Service

**Status**: working on Windows 10/11.

### Discovery

Windows does not include a built-in mDNS client. Install **Bonjour Print Services** from Apple's website, then reboot. After that:

1. **Settings → Bluetooth & devices → Printers & scanners → Add device**.
2. The mock appears as `Python Mock eSCL Scanner`.
3. Click **Add device**.

The Windows scanner service uses the same eSCL wire protocol and is tested end-to-end with this server.

## Linux without `sane-airscan`: `python-escl`

[python-escl](https://github.com/etremblay/python-escl) is a pure-Python eSCL client used by Paperless-ngx. To test the server against it:

```bash
pip install python-escl
python -c "
from escl.client import EsclClient
c = EsclClient('http://192.168.1.4:8080/eSCL')
print('Scanner:', c.scanner_model)
scan = c.scan('image/jpeg', 'RGB24', 150)
with open('/tmp/out.jpg', 'wb') as f:
    f.write(scan)
"
```

Paperless-ngx uses the same library to consume documents from the mock — no configuration changes needed, point it at the server's URL.

## Generic AirScan apps

The mDNS service type `_uscan._tcp.local.` plus the `_universal._sub._uscan._tcp.local.` subtype covers the broadest set of clients. Most generic AirScan apps (Mopria Scan, Epson Smart Panel, HP Smart) will discover and connect to the mock without modification.

## Per-client quirks summary

| Client | Quirk |
|---|---|
| `sane-airscan` | 503 body must be `<scan:ScannerStatus>` XML with `<pwg:State>Processing</pwg:State>`, or sane-airscan treats it as IO error and gives up. (Already handled.) |
| `sane-airscan` | Errors must be `<scan:ScanFault>` XML, not FastAPI JSON. (Already handled.) |
| macOS Image Capture | Always sends `application/octet-stream` first. The server honors it. |
| iOS Notes | Same as macOS. |
| Windows Scanner Service | Same as macOS but on Windows. Requires Bonjour Print Services. |
| `scanimage` | `--resolution` is not a flag (use device-specific options; sane-airscan uses `scanimage --resolution N` anyway through the SANE option system). |
| `scanimage -b` | For ADF, `--batch-count=N` controls how many pages to pull. The server delivers up to `pages_total`; sane-airscan stops at the 404. |
| Paperless-ngx | Uses python-escl. Standard URL: `http://host:port/eSCL`. |

## Common issues

**"The scanner doesn't appear"**: mDNS is not working. Check:
- `make discover` should show the service.
- `avahi-daemon` is running on Linux.
- Bonjour Print Services is installed on Windows.
- The firewall is not blocking UDP 5353.

**"The scanner appears but the scan fails"**: check `make last-requests` or `/tmp/mock-escl.log`. Look for 4xx/5xx responses and their `<scan:FaultString>`. If the body is empty, the request was malformed — fix the ScanSettings body.

**"macOS shows the scanner but Image Capture hangs"**: macOS is asking for a format the server doesn't support. The most common cause is asking for `image/x-pdf` or `image/tiff` with multi-page semantics. Set `default_format` in `config/scanner.json` to `application/pdf` or `image/jpeg` to constrain the negotiation.

**"`scanimage` says 'Error during device I/O'"**: the server returned a render failure. Check the log for `render failed` and the underlying exception (often a PIL bounds issue on small regions). This is the exact symptom the user-reported 503 was caused by — see [errors.md](errors.md) for the fix.
