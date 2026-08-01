#!/usr/bin/env bash
# End-to-end smoke test for the mock eSCL scanner.
#
# Usage:
#   BASE_URL=http://127.0.0.1:8080 ./scripts/smoke_test.sh
#
# Requires: curl, grep, awk, file
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8080}"
OUTPUT_PNG="${OUTPUT_PNG:-scanned.png}"
OUTPUT_PDF="${OUTPUT_PDF:-scanned.pdf}"
OUTPUT_JPEG="${OUTPUT_JPEG:-scanned.jpg}"
DELAY="${DELAY:-3}"

note() { printf '\n==> %s\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

note "0) Validating XML well-formedness tooling"
if ! command -v xmllint >/dev/null 2>&1; then
  echo "  (xmllint not installed — skipping XML well-formed checks)"
  HAVE_XMLLINT=0
else
  HAVE_XMLLINT=1
fi

note "1) GET ${BASE_URL}/eSCL/ScannerCapabilities"
CAPS="$(curl -fsS "${BASE_URL}/eSCL/ScannerCapabilities")"
echo "${CAPS}" | head -n 5
echo "${CAPS}" | grep -q "<scan:ScannerCapabilities" || fail "Capabilities is not eSCL XML"
echo "${CAPS}" | grep -q "<scan:SettingProfile" || fail "Capabilities is missing SettingProfile"
echo "${CAPS}" | grep -q "application/octet-stream" || fail "Capabilities is missing application/octet-stream (Apple compat)"
[ "${HAVE_XMLLINT}" = "1" ] && echo "${CAPS}" | xmllint --noout - && echo "  (xml well-formed)"

note "2) GET ${BASE_URL}/eSCL/ScannerStatus"
STATUS=$(curl -fsS "${BASE_URL}/eSCL/ScannerStatus")
echo "${STATUS}" | head -n 3
echo "${STATUS}" | grep -q "<pwg:State>" || fail "ScannerStatus is missing <pwg:State>"
[ "${HAVE_XMLLINT}" = "1" ] && echo "${STATUS}" | xmllint --noout -

note "3) POST ${BASE_URL}/eSCL/ScanJobs  (PNG)"
TMP_XML="$(mktemp --suffix=.xml)"
trap 'rm -f "${TMP_XML}" "${HEADERS_PNG:-}"' EXIT

cat > "${TMP_XML}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:ScanRegions>
    <pwg:ScanRegion>
      <pwg:ContentRegionUnits>escl:ThreeHundredthsOfInches</pwg:ContentRegionUnits>
      <pwg:XOffset>0</pwg:XOffset>
      <pwg:YOffset>0</pwg:YOffset>
      <pwg:Width>850</pwg:Width>
      <pwg:Height>1100</pwg:Height>
    </pwg:ScanRegion>
  </pwg:ScanRegions>
  <pwg:InputSource>Platen</pwg:InputSource>
  <pwg:DocumentFormat>image/png</pwg:DocumentFormat>
  <scan:ColorMode>RGB24</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
</scan:ScanSettings>
EOF

HEADERS_PNG="$(mktemp)"
LOCATION_PNG="$(curl -fsS -X POST \
  -H "Content-Type: application/xml" \
  --data-binary @"${TMP_XML}" \
  -D "${HEADERS_PNG}" \
  "${BASE_URL}/eSCL/ScanJobs" >/dev/null \
  && tr -d '\r' < "${HEADERS_PNG}" | awk '/^[Ll]ocation: / {print $2}')"

if [[ -z "${LOCATION_PNG}" ]]; then
  fail "no Location header returned from POST /eSCL/ScanJobs"
fi
echo "Job created at: ${LOCATION_PNG}"

note "4) Polling ${LOCATION_PNG} until Completed"
for attempt in $(seq 1 20); do
  STATE=$(curl -fsS "${LOCATION_PNG}" | grep -oP '<pwg:JobState>\K[^<]+' | head -1)
  echo "  poll ${attempt}: ${STATE:-?}"
  if [ "${STATE}" = "Completed" ]; then break; fi
  if [ "${STATE}" = "Failed" ] || [ "${STATE}" = "Aborted" ]; then
    fail "job entered terminal state ${STATE}"
  fi
  sleep 1
done
[ "${STATE:-}" = "Completed" ] || fail "job never reached Completed state"

note "5) GET ${LOCATION_PNG}/ScanImageInfo"
INFO=$(curl -fsS "${LOCATION_PNG}/ScanImageInfo")
echo "${INFO}" | grep -q "<pwg:DocumentFormat>image/png</pwg:DocumentFormat>" || \
  fail "ScanImageInfo missing pwg:DocumentFormat"
echo "${INFO}" | grep -q "<scan:DocumentFormatExt>image/png</scan:DocumentFormatExt>" || \
  fail "ScanImageInfo missing scan:DocumentFormatExt"
echo "${INFO}" | grep -q "<scan:Width>425</scan:Width>" || \
  fail "ScanImageInfo width does not match ScanRegion (expected 425)"
[ "${HAVE_XMLLINT}" = "1" ] && echo "${INFO}" | xmllint --noout -

note "6) GET ${LOCATION_PNG}/NextDocument"
curl -fsS "${LOCATION_PNG}/NextDocument" --output "${OUTPUT_PNG}"
echo "Saved PNG to: ${OUTPUT_PNG}"
if command -v file >/dev/null 2>&1; then file "${OUTPUT_PNG}"; fi

# ------------------------------------------------------------------
#  PDF path
# ------------------------------------------------------------------

note "7) POST ${BASE_URL}/eSCL/ScanJobs  (PDF)"
cat > "${TMP_XML}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <scan:Intent>Document</scan:Intent>
  <pwg:DocumentFormat>application/pdf</pwg:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>300</scan:XResolution>
  <scan:YResolution>300</scan:YResolution>
</scan:ScanSettings>
EOF

LOCATION_PDF="$(curl -fsS -X POST \
  -H "Content-Type: application/xml" \
  --data-binary @"${TMP_XML}" \
  -D "${HEADERS_PNG}" \
  "${BASE_URL}/eSCL/ScanJobs" >/dev/null \
  && tr -d '\r' < "${HEADERS_PNG}" | awk '/^[Ll]ocation: / {print $2}')"

echo "Job created at: ${LOCATION_PDF}"
for attempt in $(seq 1 20); do
  STATE=$(curl -fsS "${LOCATION_PDF}" | grep -oP '<pwg:JobState>\K[^<]+' | head -1)
  [ "${STATE}" = "Completed" ] && break
  sleep 1
done
[ "${STATE:-}" = "Completed" ] || fail "PDF job never reached Completed state"

curl -fsS "${LOCATION_PDF}/NextDocument" --output "${OUTPUT_PDF}"
echo "Saved PDF to: ${OUTPUT_PDF}"

# Verify the file is a real PDF (starts with %PDF-).
MAGIC=$(head -c 4 "${OUTPUT_PDF}")
if [ "${MAGIC}" != "%PDF" ]; then
  fail "PDF output is not a real PDF (magic bytes: ${MAGIC})"
fi
echo "  magic bytes OK: %PDF"
if command -v file >/dev/null 2>&1; then file "${OUTPUT_PDF}"; fi

# ------------------------------------------------------------------
#  JPEG path with CompressionFactor
# ------------------------------------------------------------------

note "8) POST ${BASE_URL}/eSCL/ScanJobs  (JPEG + CompressionFactor)"
cat > "${TMP_XML}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<scan:ScanSettings xmlns:scan="http://schemas.hp.com/imaging/escl/2011/05/03"
                   xmlns:pwg="http://www.pwg.org/schemas/2010/12/sm">
  <pwg:Version>2.0</pwg:Version>
  <pwg:DocumentFormat>image/jpeg</pwg:DocumentFormat>
  <scan:ColorMode>Grayscale8</scan:ColorMode>
  <scan:XResolution>150</scan:XResolution>
  <scan:YResolution>150</scan:YResolution>
  <scan:CompressionFactor>85</scan:CompressionFactor>
</scan:ScanSettings>
EOF

LOCATION_JPG="$(curl -fsS -X POST \
  -H "Content-Type: application/xml" \
  --data-binary @"${TMP_XML}" \
  -D "${HEADERS_PNG}" \
  "${BASE_URL}/eSCL/ScanJobs" >/dev/null \
  && tr -d '\r' < "${HEADERS_PNG}" | awk '/^[Ll]ocation: / {print $2}')"

for attempt in $(seq 1 20); do
  STATE=$(curl -fsS "${LOCATION_JPG}" | grep -oP '<pwg:JobState>\K[^<]+' | head -1)
  [ "${STATE}" = "Completed" ] && break
  sleep 1
done
[ "${STATE:-}" = "Completed" ] || fail "JPEG job never reached Completed state"

curl -fsS "${LOCATION_JPG}/NextDocument" --output "${OUTPUT_JPEG}"
echo "Saved JPEG to: ${OUTPUT_JPEG}"
if command -v file >/dev/null 2>&1; then file "${OUTPUT_JPEG}"; fi

note "9) DELETE jobs"
curl -fsS -X DELETE "${LOCATION_PNG}" >/dev/null && echo "  ${LOCATION_PNG} deleted"
curl -fsS -X DELETE "${LOCATION_PDF}" >/dev/null && echo "  ${LOCATION_PDF} deleted"
curl -fsS -X DELETE "${LOCATION_JPG}" >/dev/null && echo "  ${LOCATION_JPG} deleted"

note "All checks passed."
