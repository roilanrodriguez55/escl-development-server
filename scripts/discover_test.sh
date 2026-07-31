#!/usr/bin/env bash
# Verify that the mock eSCL scanner is being advertised via mDNS.
# Requires avahi-utils (Linux).
set -euo pipefail

SERVICE_TYPE="${SERVICE_TYPE:-_uscan._tcp}"

if ! command -v avahi-browse >/dev/null 2>&1; then
  echo "avahi-browse is not installed. Install avahi-utils:" >&2
  echo "  sudo apt install avahi-utils" >&2
  exit 1
fi

echo "Browsing for ${SERVICE_TYPE}..."
exec avahi-browse -rt "${SERVICE_TYPE}"