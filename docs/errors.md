# Errors

Every error response from the server is a `<scan:ScanFault>` XML envelope. The server never returns FastAPI's default JSON envelope to the client. This document describes when each error fires, what the body looks like, and how to debug from the client side.

## Why XML instead of JSON

Strict eSCL clients (sane-airscan, Apple Image Capture, Windows Scanner Service) parse error bodies the same way they parse successful responses. They look for the `<scan:FaultCode>` and `<scan:FaultString>` elements. If they can't find them — for example, because the server returned `{"detail": "..."}` JSON — they treat the response as an IO error and surface "Error during device I/O" to the user.

## The envelope

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanFault xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <scan:FaultCode>404</scan:FaultCode>
  <scan:FaultString>Unknown scan job: 0b9f2a7c</scan:FaultString>
  <scan:Command>/eSCL/ScanJobs/0b9f2a7c</scan:Command>
  <scan:Detail>All pages of this job have already been delivered.</scan:Detail>
</scan:ScanFault>
```

| Element | Required | Meaning |
|---|---|---|
| `scan:FaultCode` | yes | Numeric code. Usually equals the HTTP status code. |
| `scan:FaultString` | yes | Human-readable description. |
| `scan:Command` | no | The URI that caused the fault. |
| `scan:Detail` | no | Extra context (e.g. the actual error message from a render failure). |

## When each error fires

| HTTP | Fault code | Trigger | Body example |
|---|---|---|---|
| `400` | `400` | Body is fundamentally unparseable AND substring fallback can't extract a format hint. | `<scan:FaultString>Invalid ScanSettings body</scan:FaultString>` |
| `404` | `404` | `/eSCL/ScanJobs/{id}` where `{id}` doesn't match any known job. | `<scan:FaultString>Unknown scan job: ...</scan:FaultString>` |
| `404` | `404` | `/NextDocument` when all pages have been delivered. | `<scan:FaultString>No more documents</scan:FaultString>` |
| `410` | `410` | `/NextDocument` on an `Aborted` job. | `<scan:FaultString>Job aborted</scan:FaultString>` |
| `500` | `500` | `/NextDocument` on a `Failed` job (renderer raised). | `<scan:FaultString>x1 must be greater than or equal to x0</scan:FaultString>` (or whatever the renderer threw) |

## 503 — special case

The 503 response is **not** a `<scan:ScanFault>`. It carries the same `<scan:ScannerStatus>` body the GET endpoint returns, with `<pwg:State>Processing</pwg:State>`. This is by design: `sane-airscan` only retries on 503 when the body parses as a `ScannerStatus` XML with a busy `<pwg:State>`. If we returned a `<scan:ScanFault>`, sane-airscan would treat it as an IO error and stop.

```xml
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScannerStatus xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03" xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <pwg:State>Processing</pwg:State>
  <scan:Jobs>
    <!-- one <scan:JobInfo> per active job -->
  </scan:Jobs>
</scan:ScannerStatus>
```

Plus the header: `Retry-After: 1`.

## What to do when a client misbehaves

1. Check the server log: `--log-level debug` shows every request with the parsed body.
2. Check the capture dir: `MOCK_ESCL_CAPTURE_DIR` (default `/tmp/mock-escl-captures`) has the raw request body for every POST.
3. Check the in-memory ring buffer: `GET /_mock-admin/last-requests` returns the last 20 requests as JSON, including a 4 KiB body preview.
4. Check the `req_id` field in the log and grep the capture dir for matching `.meta` files.

## Server-side: catching new errors

If you add an endpoint that should return a `<scan:ScanFault>`, raise the `ScannerFault` exception. The exception handler in `server.py` does the XML serialisation for you:

```python
from .server import ScannerFault

@app.get("/eSCL/SomeNewEndpoint")
async def some_new_endpoint():
    if something_is_wrong:
        raise ScannerFault(
            code=400,
            message="Bad input",
            status_code=400,
            command="/eSCL/SomeNewEndpoint",
            detail="Explain what was wrong in detail.",
        )
    return Response(...)
```

The `command` and `detail` fields are optional but recommended — they make debugging much faster.

## Server-side: catching unhandled exceptions

Anything that escapes a route is converted by FastAPI's default 500 handler into the FastAPI JSON envelope. If you want unhandled exceptions to be `<scan:ScanFault>` XML, add an additional handler:

```python
from fastapi.responses import JSONResponse
from starlette.requests import Request

@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    LOGGER.exception("[%s] unhandled exception: %s", request_id(), exc)
    body = scanner_fault_xml(
        code=500,
        message="Internal server error",
        command=request.url.path,
        detail=str(exc),
    )
    return Response(
        status_code=500,
        content=body,
        media_type="application/xml",
        headers={"Server": server_header},
    )
```

(The current server does not do this because the inline-render path means most errors are caught at render time and reflected in the job's state. If a real exception escapes, it's almost always a bug, and the FastAPI default response is fine for visibility.)
