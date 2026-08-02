# HTTP API reference

The server exposes the eSCL/AirScan HTTP surface on the configured port (default `8080`). All XML bodies use the canonical `scan:` and `pwg:` namespaces:

```xml
xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"
```

Every response includes a `Server: <manufacturer> <model>` header (e.g. `Server: Mock Inc. ESCL-2000`) — some clients (HP, EPSON) enable quirks based on this value.

The 503 path also returns XML (`<scan:ScannerStatus>`) and the error path returns `<scan:ScanFault>` XML — see [errors.md](errors.md) for the full envelope. FastAPI's default JSON envelope is never returned to the client.

---

## Endpoints summary

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/eSCL/ScannerCapabilities` | Device capabilities XML. |
| `GET`  | `/eSCL/ScannerStatus` | Current scanner + per-job state. |
| `GET`  | `/eSCL/ScannerIcon` | 1×1 PNG icon. |
| `POST` | `/eSCL/ScanJobs` | Create a job. Returns `201` + `Location` header. |
| `GET`  | `/eSCL/ScanJobs/{id}` | Per-job status XML. |
| `GET`  | `/eSCL/ScanJobs/{id}/ScanImageInfo` | Descriptor of the upcoming page. |
| `GET`  | `/eSCL/ScanJobs/{id}/NextDocument` | The scanned document (PNG/JPEG/TIFF/PDF). |
| `DELETE` | `/eSCL/ScanJobs/{id}` | Abort a running job or remove a finished one. |
| `GET`  | `/_mock-admin/last-requests` | Last 20 requests as JSON (diagnostic). |
| `GET`  | `/_mock-admin/captures` | Capture directory listing (diagnostic). |

---

## `GET /eSCL/ScannerCapabilities`

Returns the full device capabilities document. Every advertised feature is honored by the parser/renderer, so clients that read this document get back exactly what they expect.

**Response headers**

| Header | Value |
|---|---|
| `Content-Type` | `application/xml` |
| `Server` | `<manufacturer> <model>` |

**Response body**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerCapabilities xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:Version>2.63</scan:Version>
  <pwg:Version>2.0</pwg:Version>
  <scan:MakeAndModel>Mock Inc. ESCL-2000</scan:MakeAndModel>
  <pwg:MakeAndModel>Mock Inc. ESCL-2000</pwg:MakeAndModel>
  <scan:SerialNumber>MOCK-001</scan:SerialNumber>
  <scan:UUID>00000000-0000-0000-0000-000000000001</scan:UUID>
  <scan:AdminURI>http://192.168.1.4:8080</scan:AdminURI>
  <scan:IconURI>http://192.168.1.4:8080/eSCL/ScannerIcon</scan:IconURI>
  <scan:Platen>
    <scan:PlatenInputCaps>
      <scan:MinWidth>1</scan:MinWidth>
      <scan:MaxWidth>210</scan:MaxWidth>
      <scan:MinHeight>1</scan:MinHeight>
      <scan:MaxHeight>297</scan:MaxHeight>
      <scan:MaxScanRegions>1</scan:MaxScanRegions>
      <scan:SettingProfiles>
        <scan:SettingProfile>
          <scan:ColorModes>
            <scan:ColorMode>RGB24</scan:ColorMode>
            <scan:ColorMode>Grayscale8</scan:ColorMode>
          </scan:ColorModes>
          <scan:ContentTypes>
            <pwg:ContentType>TextAndPhoto</pwg:ContentType>
          </scan:ContentTypes>
          <scan:DocumentFormats>
            <pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
            <scan:DocumentFormatExt>application/octet-stream</scan:DocumentFormatExt>
            <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
            <scan:DocumentFormatExt>application/pdf</scan:DocumentFormatExt>
            <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
            <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
            <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
            <scan:DocumentFormatExt>image/png</scan:DocumentFormatExt>
            <pwg:DocumentFormat>image/tiff</pwg:DocumentFormat>
            <scan:DocumentFormatExt>image/tiff</scan:DocumentFormatExt>
          </scan:DocumentFormats>
          <scan:SupportedResolutions>
            <scan:DiscreteResolutions>
              <scan:DiscreteResolution>
                <scan:XResolution>75</scan:XResolution>
                <scan:YResolution>75</scan:YResolution>
              </scan:DiscreteResolution>
              <!-- 150, 200, 300, 600 follow -->
            </scan:DiscreteResolutions>
          </scan:SupportedResolutions>
          <scan:ColorSpaces>
            <scan:ColorSpace>sRGB</scan:ColorSpace>
          </scan:ColorSpaces>
          <scan:SupportedIntents>
            <scan:Intent>Preview</scan:Intent>
            <scan:Intent>TextAndGraphic</scan:Intent>
            <scan:Intent>Document</scan:Intent>
            <scan:Intent>Photo</scan:Intent>
          </scan:SupportedIntents>
        </scan:SettingProfile>
      </scan:SettingProfiles>
      <scan:MaxOpticalXResolution>600</scan:MaxOpticalXResolution>
      <scan:MaxOpticalYResolution>600</scan:MaxOpticalYResolution>
    </scan:PlatenInputCaps>
  </scan:Platen>
  <!-- <scan:Adf> block present when adf_enabled: true -->
  <scan:CompressionFactorSupport>
    <scan:Min>1</scan:Min>
    <scan:Max>100</scan:Max>
    <scan:Normal>25</scan:Normal>
    <scan:Step>1</scan:Step>
  </scan:CompressionFactorSupport>
  <scan:eSCLConfigCap>
    <scan:StateSupport>
      <scan:State>disabled</scan:State>
      <scan:State>enabled</scan:State>
    </scan:StateSupport>
    <scan:ScannerAdminCredentialsSupport>false</scan:ScannerAdminCredentialsSupport>
  </scan:eSCLConfigCap>
</scan:ScannerCapabilities>
```

**Status codes**

| Code | When |
|---|---|
| `200` | Success (always). |

---

## `GET /eSCL/ScannerStatus`

Returns the aggregate scanner state plus a per-job `<scan:JobInfo>` block for every active job.

**Response body**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <pwg:State>Idle</pwg:State>           <!-- or "Processing" if any job is Pending/Processing -->
  <scan:AdfState>ScannerAdfLoaded</scan:AdfState>   <!-- only when adf_enabled -->
  <scan:Jobs>
    <scan:JobInfo>
      <pwg:JobUri>/eSCL/ScanJobs/{uuid}</pwg:JobUri>
      <pwg:JobUuid>{uuid}</pwg:JobUuid>
      <scan:Age>0</scan:Age>
      <pwg:JobState>Completed</pwg:JobState>
      <pwg:ImagesToTransfer>1</pwg:ImagesToTransfer>
      <pwg:ImagesCompleted>0</pwg:ImagesCompleted>
      <pwg:JobStateReasons>
        <pwg:JobStateReason>JobCompletedSuccessfully</pwg:JobStateReason>
      </pwg:JobStateReasons>
    </scan:JobInfo>
  </scan:Jobs>
</scan:ScannerStatus>
```

**`<pwg:State>` values**

| Value | When |
|---|---|
| `Idle` | No Pending or Processing jobs. |
| `Processing` | At least one job is Pending or Processing. |

---

## `GET /eSCL/ScannerIcon`

Returns a 1×1 transparent PNG. The `Server` header is set; no other metadata is returned.

**Response body**: 69 bytes of PNG data (magic bytes `\x89PNG\r\n\x1a\n`).

---

## `POST /eSCL/ScanJobs`

Submit a new scan job. The body is the eSCL `ScanSettings` XML. The server parses the settings, creates the job, renders the document (inline when `delay_seconds <= 0`, scheduled otherwise), and returns the job URL in the `Location` header.

**Request body**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
  <pwg:InputSource>Platen</pwg:InputSource>
  <scan:Duplex>false</scan:Duplex>
  <scan:CompressionFactor>25</scan:CompressionFactor>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>850</pwg:Width>
      <pwg:Height>1100</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <scan:Intent>Document</scan:Intent>
</scan:ScanSettings>
```

**Recognised elements** (every field is optional; missing fields fall back to config defaults)

| Element | Values | Default |
|---|---|---|
| `pwg:DocumentFormat` | `image/jpeg`, `image/png`, `image/tiff`, `application/pdf`, `application/octet-stream` | `config.default_format` |
| `scan:ColorMode` | `RGB24`, `Grayscale8`, `BlackAndWhite1` | First advertised mode |
| `scan:XResolution` / `scan:YResolution` | 75, 150, 200, 300, 600 | Mid-range advertised |
| `pwg:InputSource` | `Platen`, `Feeder` | `Platen` |
| `scan:Duplex` | `true`, `false` | `false` |
| `scan:CompressionFactor` | 1–100 | 25 |
| `pwg:ScanRegions` | One or more regions with `ContentRegionUnits` in `escl:ThreeHundredthsOfInches` or `escl:Microns` | Full platen |
| `scan:Intent` | `Preview`, `TextAndGraphic`, `Document`, `Photo` | `None` (defaults to sans-serif) |

**Response headers**

| Header | Value |
|---|---|
| `Location` | Absolute URL of the new job, e.g. `http://192.168.1.4:8080/eSCL/ScanJobs/{uuid}` |
| `Content-Length` | `0` |
| `Server` | `<manufacturer> <model>` |

**Status codes**

| Code | When |
|---|---|
| `201 Created` | Always (the server is permissive — invalid settings are snapped to advertised values, not rejected). |
| `400 Bad Request` | Body is fundamentally unparseable AND substring fallback cannot extract any format hint. Returned as `<scan:ScanFault>`. |

---

## `GET /eSCL/ScanJobs/{id}`

Returns the per-job status. Used by clients to poll state transitions.

**Response body**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanJobStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <scan:JobUuid>{uuid}</scan:JobUuid>
  <scan:Age>0</scan:Age>
  <pwg:JobState>Completed</pwg:JobState>
  <pwg:ImagesToTransfer>1</pwg:ImagesToTransfer>
  <pwg:ImagesCompleted>0</pwg:ImagesCompleted>
  <pwg:JobStateReasons>
    <pwg:JobStateReason>JobCompletedSuccessfully</pwg:JobStateReason>
  </pwg:JobStateReasons>
</scan:ScanJobStatus>
```

**`<pwg:JobState>` values**

| Value | PWG `<pwg:JobStateReason>` |
|---|---|
| `Pending` | `Processing` |
| `Processing` | `Processing` |
| `Completed` | `JobCompletedSuccessfully` |
| `Aborted` | `JobAbortedByUser` |
| `Failed` | `JobFailed` |

**Status codes**

| Code | When |
|---|---|
| `200` | Job exists. |
| `404 Not Found` | `{id}` does not match any known job. Returns `<scan:ScanFault>`. |

---

## `GET /eSCL/ScanJobs/{id}/ScanImageInfo`

Returns a descriptor of the upcoming document. Required by some clients (notably `sane-airscan`) before they pull `NextDocument`. Width/Height reflect the requested `ScanRegion` at the requested DPI, or the full platen if no region was given.

**Response body**

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanImageInfo xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <scan:JobUuid>{uuid}</scan:JobUuid>
  <scan:Images>1</scan:Images>
  <scan:Image>
    <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
    <scan:DocumentFormatExt>image/jpeg</scan:DocumentFormatExt>
    <scan:InputSource>Platen</scan:InputSource>
    <scan:ColorMode>RGB24</scan:ColorMode>
    <scan:XResolution>150</scan:XResolution>
    <scan:YResolution>150</scan:YResolution>
    <scan:CompressionFactor>25</scan:CompressionFactor>
    <scan:Width>1240</scan:Width>
    <scan:Height>1753</scan:Height>
  </scan:Image>
</scan:ScanImageInfo>
```

**Status codes**

| Code | When |
|---|---|
| `200` | Job exists. |
| `404 Not Found` | `{id}` does not match. Returns `<scan:ScanFault>`. |

---

## `GET /eSCL/ScanJobs/{id}/NextDocument`

Returns the next page of the scanned document. For multi-page (ADF) and duplex jobs, repeated calls deliver each page in order. Returns `404` once all pages have been delivered.

**Response headers**

| Header | Value |
|---|---|
| `Content-Type` | One of `image/jpeg`, `image/png`, `image/tiff`, `application/pdf`, `application/octet-stream` (matches the requested `DocumentFormat`). |
| `Content-Disposition` | `attachment; filename="scan-{id}.{ext}"` for single-page, `scan-{id}-page-NN-{front,back}.{ext}` for multi-page. |
| `Server` | `<manufacturer> <model>` |

**If the client sends `Accept: multipart/related`**: the response is a multipart envelope with `ScanImageInfo` as the first part and the document as the second part. The boundary is `MOCK_ESCL_BOUNDARY`.

```
--MOCK_ESCL_BOUNDARY
Content-Type: application/xml

<?xml version="1.0" ...><scan:ScanImageInfo>...</scan:ScanImageInfo>
--MOCK_ESCL_BOUNDARY
Content-Type: image/jpeg
Content-Disposition: attachment; filename="scan-{id}.jpg"

<binary JPEG bytes>
--MOCK_ESCL_BOUNDARY--
```

**Status codes**

| Code | When | Body |
|---|---|---|
| `200` | Document available. | The image/PDF bytes. |
| `404 Not Found` | All pages already delivered. | `<scan:ScanFault>` XML. |
| `410 Gone` | Job is `Aborted`. | `<scan:ScanFault>` XML. |
| `500 Internal Server Error` | Job is `Failed` (renderer raised an exception). | `<scan:ScanFault>` XML with the error message. |
| `503 Service Unavailable` | Job is `Pending` or `Processing`. | `<scan:ScannerStatus>` XML with `<pwg:State>Processing</pwg:State>` + `Retry-After: 1` header. |

---

## `DELETE /eSCL/ScanJobs/{id}`

Aborts a running job (sets state to `Aborted` and signals the render task) or no-ops on a finished one (clears the abort event but keeps the state).

**Status codes**

| Code | When |
|---|---|
| `200 OK` | Always (when the job exists). |
| `404 Not Found` | `{id}` does not match any job. Returns `<scan:ScanFault>`. |

---

## Diagnostic endpoints

Mock-only helpers for debugging client behaviour, namespaced under `/_mock-admin/` so they never collide with the device `AdminURI` advertised in capabilities.

### `GET /_mock-admin/last-requests`

Last 20 HTTP requests as JSON. Each entry has `ts`, `req_id`, `method`, `path`, `query`, `client`, `content_type`, `content_length`, `accept`, `user_agent`, `body_preview` (first 4 KiB), `response_status`, `response_headers`.

### `GET /_mock-admin/captures`

JSON with `capture_dir`, `exists`, `files` (last 30 filenames in the capture dir), and `disabled` (true when `MOCK_ESCL_CAPTURE_DIR=""`).

The server also writes every POST body and a `.meta` sidecar file to `MOCK_ESCL_CAPTURE_DIR`. Inspect those when a client misbehaves.

---

## Examples

### Plain JPEG, 150 DPI

```bash
curl -X POST http://192.168.1.4:8080/eSCL/ScanJobs \
  -H "Content-Type: application/xml" \
  --data-binary @- <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>
EOF
# -> 201, Location: http://192.168.1.4:8080/eSCL/ScanJobs/{uuid}

curl -o scan.jpg http://192.168.1.4:8080/eSCL/ScanJobs/{uuid}/NextDocument
```

### Black-and-white 1-bit dithered PNG, 300 DPI

```bash
curl -X POST http://192.168.1.4:8080/eSCL/ScanJobs \
  -H "Content-Type: application/xml" \
  --data-binary @- <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
  <scan:ColorMode>BlackAndWhite1</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>
EOF
```

### Custom scan region (US Letter)

```xml
<pwg:ScanRegions>
  <pwg:ScanRegion>
    <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
    <pwg:XOffset>0</pwg:XOffset>
    <pwg:YOffset>0</pwg:YOffset>
    <pwg:Width>850</pwg:Width>     <!-- 8.5 in -->
    <pwg:Height>1100</pwg:Height>  <!-- 11 in -->
  </pwg:ScanRegion>
</pwg:ScanRegions>
```

### Duplex

```xml
<scan:Duplex>true</scan:Duplex>
```

Combined with `pages_total: 2` in the config, the job delivers 4 pages (front 1, back 1, front 2, back 2). The filename on each NextDocument response includes `-front` or `-back` accordingly.

### Apple AirScan (application/octet-stream)

```xml
<pwg:DocumentFormat>application/octet-stream</pwg:DocumentFormat>
```

The bytes are PNG by default (configurable via the JSON). The Content-Type of the NextDocument response honours the requested octet-stream MIME.
