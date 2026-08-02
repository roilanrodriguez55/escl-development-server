# Makefile for the mock eSCL scanner server.
#
# All targets assume you're in the repo root. Every command uses the
# project virtualenv at .venv/ — no global Python pollution.
#
# Quick reference:
#   make help             list every target
#   make install          create .venv and install the package
#   make run              run the server (with mDNS)
#   make run-no-mdns      run the server without mDNS (local testing)
#   make test             run the pytest suite
#   make smoke            run the bash smoke test against a running server
#   make discover         show mDNS-discovered eSCL services on the LAN
#   make stop             kill any running mock_escl process
#   make clean            remove build artifacts and the capture directory

VENV         := .venv
PY           := $(VENV)/bin/python
PIP          := $(VENV)/bin/pip
PYTEST       := $(VENV)/bin/pytest
RUNNER       := $(PY) -m mock_escl
CONFIG       := config/scanner.json
LOG          := /tmp/mock-escl.log
PIDFILE      := /tmp/mock-escl.pid
CAPTURE_DIR  := /tmp/mock-escl-captures
PORT         := 8080
BASE_URL     := http://127.0.0.1:$(PORT)

.PHONY: help
help:                       ## show this help
	@awk 'BEGIN {FS = ":.*?## "} /^[a-zA-Z_-]+:.*?## / {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

.PHONY: install
install:                    ## create .venv and install the package + dev deps
	@test -d $(VENV) || python3 -m venv $(VENV)
	$(PIP) install -e .
	$(PIP) install pytest httpx
	@echo "Installed. Activate with: source $(VENV)/bin/activate"

.PHONY: reinstall
reinstall:                  ## reinstall everything from scratch
	rm -rf $(VENV) ./*.egg-info
	$(MAKE) install

.PHONY: deps
deps:                       ## install just the dev/test dependencies
	$(PIP) install pytest httpx

# ---------------------------------------------------------------------------
# Running the server
# ---------------------------------------------------------------------------

.PHONY: run
run:                        ## run the server (with mDNS) in the background, log to $(LOG)
	$(MAKE) _run-bg ARGS="--config $(CONFIG)"

.PHONY: run-no-mdns
run-no-mdns:                ## run the server in the background, mDNS disabled
	$(MAKE) _run-bg ARGS="--config $(CONFIG) --no-mdns"

.PHONY: run-debug
run-debug:                  ## run the server in debug mode (verbose logging)
	$(MAKE) _run-bg ARGS="--config $(CONFIG) --no-mdns --log-level debug"

.PHONY: run-seed
run-seed:                   ## run with deterministic seed (same job_id -> same bytes)
	$(MAKE) _run-bg ARGS="--config $(CONFIG) --no-mdns --seed 42 --log-level debug"

.PHONY: run-delay
run-delay:                  ## run with a 2-second simulated scan delay
	$(MAKE) _run-bg ARGS="--config $(CONFIG) --no-mdns --log-level debug"
	@echo "Hint: edit $(CONFIG) -> delay_seconds=2.0 and re-make run"

.PHONY: run-apple
run-apple:                  ## run with Apple AirScan config (octet-stream default)
	@mkdir -p /tmp
	@printf '%s\n' \
	  '{' \
	  '  "host": "0.0.0.0",' \
	  '  "port": 8080,' \
	  '  "name": "Mock AirScan",' \
	  '  "manufacturer": "Apple",' \
	  '  "model": "VirtualScanner",' \
	  '  "serial": "AIR-001",' \
	  '  "uuid": "00000000-0000-0000-0000-0000000000aa",' \
	  '  "color_modes": ["RGB24", "Grayscale8"],' \
	  '  "resolutions": [75, 150, 200, 300, 600],' \
	  '  "max_width_mm": 210,' \
	  '  "max_height_mm": 297,' \
	  '  "default_format": "application/octet-stream",' \
	  '  "delay_seconds": 0.0,' \
	  '  "pages_total": 1,' \
	  '  "service_type": "_uscan._tcp.local.",' \
	  '  "adf_enabled": false,' \
	  '  "duplex_supported": false' \
	  '}' > /tmp/apple-config.json
	$(MAKE) _run-bg CONFIG=/tmp/apple-config.json ARGS=""

.PHONY: run-adf
run-adf:                    ## run with ADF config (3 pages, optional duplex)
	@mkdir -p /tmp
	@printf '%s\n' \
	  '{' \
	  '  "host": "0.0.0.0",' \
	  '  "port": 8080,' \
	  '  "name": "Mock ADF Scanner",' \
	  '  "manufacturer": "Mock Inc.",' \
	  '  "model": "ESCL-ADF",' \
	  '  "serial": "ADF-001",' \
	  '  "uuid": "00000000-0000-0000-0000-0000000000ad",' \
	  '  "color_modes": ["RGB24", "Grayscale8"],' \
	  '  "resolutions": [75, 150, 200, 300, 600],' \
	  '  "max_width_mm": 210,' \
	  '  "max_height_mm": 297,' \
	  '  "default_format": "image/jpeg",' \
	  '  "delay_seconds": 0.0,' \
	  '  "pages_total": 3,' \
	  '  "service_type": "_uscan._tcp.local.",' \
	  '  "adf_enabled": true,' \
	  '  "duplex_supported": true' \
	  '}' > /tmp/adf-config.json
	$(MAKE) _run-bg CONFIG=/tmp/adf-config.json ARGS=""

.PHONY: _run-bg
_run-bg:                    ## internal: spawn the server detached (use run, run-no-mdns, etc.)
	@mkdir -p $(dir $(LOG))
	@EXISTING=$$(ps -eo pid,comm,args | awk '$$2=="python" && /python -m mock_escl/ {print $$1}') ; \
	  if [ -n "$$EXISTING" ]; then \
	    echo "Server already running (pid $$EXISTING). Use 'make stop' first."; \
	    exit 1; \
	  fi
	@CONFIG="$(or $(CONFIG),$(CONFIG_PATH))" ; \
	  if [ -n "$$CONFIG" ] && [ ! -f "$$CONFIG" ]; then \
	    echo "Config not found: $$CONFIG" ; exit 1 ; \
	  fi ; \
	  rm -f $(LOG) ; \
	  CONFIG=$$CONFIG nohup setsid $(PY) -m mock_escl $$ARGS >$(LOG) 2>&1 < /dev/null & \
	  SERVER_PID=$$! ; \
	  echo $$SERVER_PID > $(PIDFILE) ; \
	  sleep 1 ; \
	  if kill -0 $$SERVER_PID 2>/dev/null; then \
	    echo "Server started: pid=$$SERVER_PID log=$(LOG)" ; \
	    echo "Capabilities: $(BASE_URL)/eSCL/ScannerCapabilities" ; \
	    echo "Run 'make stop' to kill, 'make tail' to follow the log." ; \
	  else \
	    echo "Server failed to start. Tail of $(LOG):" ; \
	    tail -20 $(LOG) ; \
	    exit 1 ; \
	  fi

.PHONY: foreground
foreground:                 ## run the server in the foreground (Ctrl-C to stop)
	$(PY) -m mock_escl --config $(CONFIG) --log-level info

# ---------------------------------------------------------------------------
# Stop / inspect
# ---------------------------------------------------------------------------

.PHONY: stop
stop:                       ## kill any running mock_escl process
	@PIDS=$$(ps -eo pid,comm,args | awk '$$2=="python" && /python -m mock_escl/ {print $$1}') ; \
	  if [ -z "$$PIDS" ]; then \
	    echo "No server running." ; \
	  else \
	    echo "Killing: $$PIDS" ; \
	    kill $$PIDS 2>/dev/null || true ; \
	    sleep 1 ; \
	    PIDS2=$$(ps -eo pid,comm,args | awk '$$2=="python" && /python -m mock_escl/ {print $$1}') ; \
	    if [ -n "$$PIDS2" ]; then \
	      kill -9 $$PIDS2 2>/dev/null || true ; \
	    fi ; \
	  fi ; \
	  rm -f $(PIDFILE) ; \
	  echo "Done."

.PHONY: status
status:                     ## show whether the server is running and on which port
	@PID=$$(ps -eo pid,comm,args | awk '$$2=="python" && /python -m mock_escl/ {print $$1}') ; \
	  if [ -z "$$PID" ]; then \
	    echo "Not running. Start with 'make run' or 'make run-no-mdns'." ; \
	  else \
	    echo "Running (pid $$PID)" ; \
	    if [ -f $(LOG) ]; then \
	      echo "Log: $(LOG)" ; \
	      grep -E "Registered|advertis|started" $(LOG) | tail -5 ; \
	    fi ; \
	    echo "" ; \
	    echo "Trying $(BASE_URL)/eSCL/ScannerCapabilities ..." ; \
	    curl -sS -m 3 -o /dev/null -w "  HTTP %{http_code} (%{size_download} bytes, %{time_total}s)\n" \
	      $(BASE_URL)/eSCL/ScannerCapabilities || echo "  not reachable" ; \
	  fi

.PHONY: tail
tail:                       ## tail the server log
	tail -f $(LOG)

.PHONY: tail-debug
tail-debug:                 ## tail the server log, debug filtering
	tail -f $(LOG) | grep -E "DEBUG|ERROR|WARNING"

# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

.PHONY: capabilities
capabilities:               ## GET /eSCL/ScannerCapabilities and pretty-print
	curl -sS $(BASE_URL)/eSCL/ScannerCapabilities | head -50

.PHONY: status-xml
status-xml:                 ## GET /eSCL/ScannerStatus
	curl -sS $(BASE_URL)/eSCL/ScannerStatus

.PHONY: last-requests
last-requests:              ## show the last 20 requests the server received
	curl -sS $(BASE_URL)/_mock-admin/last-requests | head -80

.PHONY: captures
captures:                   ## list files in the capture directory
	@ls -la $(CAPTURE_DIR) 2>/dev/null | head -30 || echo "No capture dir (set MOCK_ESCL_CAPTURE_DIR to enable)"

.PHONY: scanimage-list
scanimage-list:             ## list scanners sane-airscan can see on the LAN
	@if command -v scanimage >/dev/null 2>&1; then \
	  scanimage -L 2>&1 | grep -E "airscan|^(device|\\*)" ; \
	else \
	  echo "scanimage not installed. Install: sudo apt install sane-airscan sane-utils" ; \
	fi

.PHONY: scan
scan:                       ## run scanimage against the mock (output: /tmp/scan-out.png)
	@if ! pgrep -f "python -m mock_escl" >/dev/null; then \
	  echo "Server not running. Start with 'make run' or 'make run-no-mdns'." ; \
	  exit 1 ; \
	fi
	@if ! command -v scanimage >/dev/null 2>&1; then \
	  echo "scanimage not installed. Install: sudo apt install sane-airscan sane-utils" ; \
	  exit 1 ; \
	fi
	@DEV=$$(scanimage -L 2>&1 | grep -oE "airscan:e0:[A-Za-z0-9 ._-]+" | head -1) ; \
	  if [ -z "$$DEV" ]; then \
	    echo "No airscan device found. Make sure mDNS is up (use 'make run' not 'make run-no-mdns')." ; \
	    exit 1 ; \
	  fi ; \
	  echo "Scanning from $$DEV ..." ; \
	  scanimage --format=png --resolution 150 -d "$$DEV" --output-file /tmp/scan-out.png 2>&1 | tail -5 ; \
	  echo "Saved to /tmp/scan-out.png" ; \
	  file /tmp/scan-out.png

.PHONY: scan-batch
scan-batch:                 ## run scanimage -b for ADF batch (3 pages)
	@if ! command -v scanimage >/dev/null 2>&1; then \
	  echo "scanimage not installed. Install: sudo apt install sane-airscan sane-utils" ; \
	  exit 1 ; \
	fi
	@DEV=$$(scanimage -L 2>&1 | grep -oE "airscan:e0:[A-Za-z0-9 ._-]+" | head -1) ; \
	  if [ -z "$$DEV" ]; then echo "No airscan device found." ; exit 1 ; fi ; \
	  rm -f /tmp/out*.jpg ; \
	  scanimage -b --format=jpeg --resolution 150 -d "$$DEV" --batch-count=3 2>&1 | tail -10 ; \
	  ls -la /tmp/out*.jpg 2>/dev/null

.PHONY: discover
discover:                   ## browse the LAN for eSCL services via zeroconf
	@$(PY) scripts/discover.py

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

.PHONY: test
test:                       ## run the pytest suite
	$(PYTEST) -q

.PHONY: test-verbose
test-verbose:               ## run the pytest suite with full output
	$(PYTEST) -v

.PHONY: test-coverage
test-coverage:              ## run pytest with coverage
	$(PIP) install pytest-cov
	$(PYTEST) --cov=mock_escl --cov-report=term-missing

.PHONY: smoke
smoke:                      ## run the bash smoke test (requires the server running)
	@if ! pgrep -f "python -m mock_escl" >/dev/null; then \
	  echo "Server not running. Start with 'make run-no-mdns' first." ; \
	  exit 1 ; \
	fi
	BASE_URL=$(BASE_URL) DELAY=2 bash scripts/smoke_test.sh

.PHONY: smoke-nopng
smoke-nopng:                ## run smoke test without saving the PNG (CI-friendly)
	@if ! pgrep -f "python -m mock_escl" >/dev/null; then \
	  echo "Server not running. Start with 'make run-no-mdns' first." ; \
	  exit 1 ; \
	fi
	BASE_URL=$(BASE_URL) DELAY=2 OUTPUT_PNG=/dev/null bash scripts/smoke_test.sh

# ---------------------------------------------------------------------------
# Hygiene
# ---------------------------------------------------------------------------

.PHONY: clean
clean:                      ## remove __pycache__, build artifacts, capture files
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf build/ dist/ .coverage htmlcov/
	rm -f /tmp/scan-out.png /tmp/out*.jpg /tmp/out*.png /tmp/scanimage-out.png
	@echo "Cleaned. (Capture dir $(CAPTURE_DIR) preserved — run 'make clean-captures' to wipe it.)"

.PHONY: clean-captures
clean-captures:             ## remove the capture directory
	rm -rf $(CAPTURE_DIR)
	@echo "Wiped $(CAPTURE_DIR)"

.PHONY: clean-all
clean-all: clean clean-captures stop   ## nuke everything (deps, builds, captures, server)
	@echo "All clean. (Reinstall with 'make install'.)"

# ---------------------------------------------------------------------------
# Install + run helper combos
# ---------------------------------------------------------------------------

.PHONY: fresh
fresh: clean-all install run   ## wipe, install, and run from scratch
	@echo ""
	@echo "Server is starting in the background. Tail with: make tail"
	@echo "Stop with: make stop"
