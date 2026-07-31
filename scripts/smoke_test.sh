#!/usr/bin/env bash
# End-to-end smoke test for the mock eSCL scanner.
#
# Usage:
#   BASE_URL=http://127.0.0.1:8080 ./scripts/smoke_test.sh
#
# Requires: curl, grep, awk, file
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
OUTPUT="${OUTPUT:-scanned.png}"

echo "==> 1) GET ${BASE_URL}/eSCL/ScannerCapabilities"
curl -fsS "${BASE_URL}/eSCL/ScannerCapabilities" | head -n 25

echo
echo "==> 2) POST ${BASE_URL}/eSCL/ScanJobs"
TMP_XML="$(mktemp --suffix=.xml)"
trap 'rm -f "${TMP_XML}"' EXIT

cat > "${TMP_XML}" <<'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<eSCL:ScanSettings xmlns:eSCL="http://schemas.hp.com/eSCL/2012/02">
  <eSCL:Version>2.63</eSCL:Version>
  <eSCL:Intent>Document</eSCL:Intent>
  <eSCL:DocumentFormatExt>image/png</eSCL:DocumentFormatExt>
  <eSCL:XResolution>300</eSCL:XResolution>
  <eSCL:YResolution>300</eSCL:YResolution>
  <eSCL:ColorMode>RGB24</eSCL:ColorMode>
  <eSCL:InputSource>Platen</eSCL:InputSource>
</eSCL:ScanSettings>
EOF

HEADERS="$(mktemp)"
LOCATION="$(curl -fsS -X POST \
  -H "Content-Type: application/xml" \
  --data-binary @"${TMP_XML}" \
  -D "${HEADERS}" \
  "${BASE_URL}/eSCL/ScanJobs" >/dev/null \
  && tr -d '\r' < "${HEADERS}" | awk '/^Location: / {print $2}')"

rm -f "${HEADERS}"

if [[ -z "${LOCATION}" ]]; then
  echo "ERROR: no Location header returned from POST /eSCL/ScanJobs" >&2
  exit 1
fi

echo "Job created at: ${LOCATION}"

echo "==> 3) Waiting for scan to complete..."
sleep 3

echo "==> 4) GET ${BASE_URL}${LOCATION}/NextDocument"
curl -fsS "${BASE_URL}${LOCATION}/NextDocument" --output "${OUTPUT}"
echo "Saved to: ${OUTPUT}"

if command -v file >/dev/null 2>&1; then
  file "${OUTPUT}"
fi