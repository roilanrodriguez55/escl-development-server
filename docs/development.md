# Development

How to work on the mock eSCL scanner: running tests, adding a feature, debugging live traffic, contributing.

## Setup

```bash
make install
```

This creates `.venv/`, installs the package in editable mode, and pulls in `pytest` and `httpx` for the test suite. The `pip` wrapper in `.venv/bin/pip` may have a hard-coded path; if it breaks, use `python -m pip` instead.

## Running the tests

```bash
make test
```

The suite is 63 tests across 7 files:

| File | Coverage |
|---|---|
| `tests/test_existing_behavior.py` | Protocol conformance: namespaces, capabilities structure, status, ScanSettings parsing, single-page PNG/PDF/JPEG flow, ScanImageInfo, DELETE, ScannerIcon, diagnostic endpoints. |
| `tests/test_bugfixes.py` | Regressions for the inline-render race, location header host, unbound-prefix XML, multipart/related accept. |
| `tests/test_scanner_fault.py` | The `<scan:ScanFault>` envelope for 404/410/500 paths. |
| `tests/test_octet_stream.py` | `application/octet-stream` end-to-end. |
| `tests/test_adf_multipage.py` | Multi-page (ADF) delivery, page counting, filename. |
| `tests/test_duplex.py` | Duplex doubling, front/back side, distinct content. |
| `tests/conftest.py` | Shared `client`, `adf_client`, `color_client` fixtures using FastAPI's `TestClient`. |

Run a single test with `-k`:

```bash
.venv/bin/pytest -k "test_small_scan_region"
.venv/bin/pytest tests/test_duplex.py::test_duplex_front_and_back_pages_differ
```

Verbose output:

```bash
make test-verbose
```

Coverage:

```bash
make test-coverage
```

## Adding a new endpoint

1. Decide the URL and method. Look at the existing routes in `server.py` to match the pattern (route definitions are at the bottom of `create_app`).
2. If the endpoint returns XML, write a builder function next to the existing `*_xml` helpers (`capabilities_xml`, `scanner_status_xml`, `scan_status_xml`, `scan_image_info_xml`). Use the existing `_xml_header()` and `_xml_escape()`.
3. If the endpoint can fail with a parseable error, raise `ScannerFault` instead of `HTTPException`. The exception handler converts it to a `<scan:ScanFault>` body.
4. Write a failing test in the relevant `tests/test_*.py` file. Use the existing fixtures; if you need a new fixture (e.g. a different config), add it to `tests/conftest.py`.
5. Run the test, see RED. Implement the endpoint. Run again, see GREEN.
6. Run the full suite to make sure nothing else broke.

## Adding a new feature (e.g. a new color mode)

1. Update `ScannerConfig` in `src/mock_escl/config.py` if there's a new top-level field.
2. Update the renderer in `src/mock_escl/jobs.py` if it changes output bytes.
3. Update the capabilities XML in `src/mock_escl/server.py` if it changes the advertised surface.
4. Update the mDNS TXT record in `src/mock_escl/discovery.py` if it's a client-visible identity change.
5. Add tests.

## Debugging live traffic

When a client misbehaves and you can't figure out from the client's UI what request it sent:

1. **Server log**: `make tail` (or `tail -f /tmp/mock-escl.log`). The default log level is `info`; use `make run-debug` to start with `debug` for body-level detail.
2. **Capture dir**: every POST body and a `.meta` sidecar land in `/tmp/mock-escl-captures/` (or `MOCK_ESCL_CAPTURE_DIR`). Inspect the raw bytes — these are the actual requests, not what the parser saw.
3. **In-memory ring buffer**: `make last-requests` returns the last 20 requests as JSON. Useful when you don't want to grep the capture dir.
4. **Per-request correlation id**: every request gets an 8-character hex id that appears in the log, the `.meta` file, and the in-memory buffer. Grep the log for the id, or grep the meta files, to get a complete trace.

When you find the symptom, look up the matching `ScannerFault` code in [errors.md](errors.md) and you usually have a one-line fix.

## Working with the live server

The server has two running modes:

| Mode | Command | Use case |
|---|---|---|
| Foreground | `make foreground` | Interactive use, manual testing, when you want Ctrl-C to kill. |
| Background (detached) | `make run` or `make run-no-mdns` | Long-running test session, integration with a real client. |

`make stop` kills any background instance. `make status` shows whether one is running and on which port.

If you make a code change while the server is running in the foreground, you need to restart it (`Ctrl-C` + `make foreground`). The background mode does not auto-reload — edit code, then `make stop && make run`.

## Project conventions

- **Code lives under `src/mock_escl/`** (src-layout). Tests are siblings of the source tree, in `tests/`.
- **Dependencies are pinned with `>=`** in `pyproject.toml` and `requirements.txt`. No lockfile — the project is small enough that drift isn't worth the friction.
- **Python 3.10+** required. Type hints everywhere. `from __future__ import annotations` at the top of every file.
- **Logging format**: `"%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s"` with the date format `"%Y-%m-%d %H:%M:%S"`. One format everywhere, set in `__main__.configure_logging`.
- **Logging levels**: INFO for one-line per request, DEBUG for body-level detail, WARNING for recoverable issues, ERROR for renderer failures.
- **XML namespaces**: every response uses `xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"` and `xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm"`. Both must be on the root element.
- **HTTP errors**: raise `ScannerFault`, never `HTTPException`. The exception handler turns it into `<scan:ScanFault>`.
- **Tests are TDD**: RED first (write the test, see it fail), then GREEN (write the smallest change that flips it), then REFACTOR if needed.

## Commit conventions

- One atomic commit per verified increment.
- Subject: 50 chars or less, imperative mood, "fix:" or "feat:" or "docs:" or "test:" or "chore:" prefix.
- Body: explain the why, not the what. Reference the bug or spec section.
- Sign off with: `git -c user.name="..." -c user.email="..." commit -m "..."` if you don't have a global git identity.

## Releasing

Not done yet. The project isn't on PyPI. When it is, the workflow will be:

1. Bump `version` in `pyproject.toml`.
2. `make clean && make test` (full suite must be green).
3. `git tag vX.Y.Z && git push --tags`.
4. `make build` (calls `python -m build`).
5. `make publish` (calls `twine upload dist/*`).
