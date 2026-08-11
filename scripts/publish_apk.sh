#!/usr/bin/env bash
# Publish an APK to the release channel. Phones pick it up on next launch and
# the website's download button updates itself from the same manifest.
#
#   scripts/publish_apk.sh [BASE_URL] [VERSION_CODE] [VERSION_NAME]
#
# Defaults: the deployed Cloud Run URL, versionCode/Name read from the APK.
# The admin token comes from run/.prod-admin-token (written at deploy time) or
# $PHOTOBIND_ADMIN_TOKEN.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AAPT="${ANDROID_HOME:-/home/harsh/Android/Sdk}/build-tools/34.0.0/aapt"
# Default to the build output, not a hand-copied file: a stale dist/ copy once
# got published over a fresh build, and the checksum check passed because it
# only compares the file that was sent against the manifest.
GRADLE_APK="$ROOT/apps/android/app/build/outputs/apk/release/app-release.apk"
APK="${APK:-$([ -f "$GRADLE_APK" ] && echo "$GRADLE_APK" || echo "$ROOT/dist/identity-1.0.apk")}"
BASE="${1:-https://identity-70168600033.us-central1.run.app}"

TOKEN="${PHOTOBIND_ADMIN_TOKEN:-}"
[ -z "$TOKEN" ] && [ -f "$ROOT/run/.prod-admin-token" ] && \
  TOKEN="$(cat "$ROOT/run/.prod-admin-token")"
if [ -z "$TOKEN" ]; then
  echo "No admin token. Set PHOTOBIND_ADMIN_TOKEN or create run/.prod-admin-token."
  exit 1
fi
[ -f "$APK" ] || { echo "APK not found: $APK"; exit 1; }

# Read the real version out of the APK so the manifest can never disagree with
# the binary it is describing.
if [ -x "$AAPT" ]; then
  BADGING="$($AAPT dump badging "$APK" 2>/dev/null | head -1)"
  CODE="${2:-$(sed -n "s/.*versionCode='\([0-9]*\)'.*/\1/p" <<<"$BADGING")}"
  NAME="${3:-$(sed -n "s/.*versionName='\([^']*\)'.*/\1/p" <<<"$BADGING")}"
  MINSDK="$($AAPT dump badging "$APK" 2>/dev/null | sed -n "s/^sdkVersion:'\([0-9]*\)'/\1/p")"
else
  CODE="${2:?versionCode needed (aapt unavailable)}"
  NAME="${3:?versionName needed (aapt unavailable)}"
  MINSDK=26
fi
NOTES="${NOTES:-}"

echo "publishing $NAME (versionCode $CODE, minSdk $MINSDK) to $BASE"
RESP=$(curl -s -X POST "$BASE/v1/admin/releases" \
  -H "x-admin-token: $TOKEN" \
  -F "apk=@$APK" \
  -F "version_code=$CODE" \
  -F "version_name=$NAME" \
  -F "min_sdk=${MINSDK:-26}" \
  -F "notes=$NOTES" \
  -F "replace=${REPLACE:-false}")
echo "$RESP"

# Confirm the served bytes match what we uploaded — a manifest that lies about
# its own checksum would break every OTA install.
LOCAL_SHA=$(sha256sum "$APK" | cut -d' ' -f1)
MANIFEST_SHA=$(curl -s "$BASE/v1/app/latest" | sed -n 's/.*"sha256":"\([^"]*\)".*/\1/p')
if [ "$LOCAL_SHA" = "$MANIFEST_SHA" ]; then
  echo "checksum matches the published manifest"
else
  echo "CHECKSUM MISMATCH — local $LOCAL_SHA vs manifest $MANIFEST_SHA"
  exit 1
fi
curl -s "$BASE/v1/app/latest" | tr ',' '\n' | grep -E '"(version_name|min_android|size_mb|storage|url)"'
