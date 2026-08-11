#!/usr/bin/env bash
# Pair (once) and install the Identity APK over wireless debugging.
#
#   scripts/install_wireless.sh <PAIR_PORT> <6-DIGIT-CODE>   # first time
#   scripts/install_wireless.sh                              # already paired
#
# The pairing port and code come from the phone:
#   Settings → Developer options → Wireless debugging
#     → "Pair device with pairing code"
# That dialog shows "IP:PORT" and a 6-digit code. The pairing port is NOT the
# same as the connect port.
set -uo pipefail

ADB="${ANDROID_HOME:-/home/harsh/Android/Sdk}/platform-tools/adb"
APK="$(cd "$(dirname "$0")/.." && pwd)/dist/identity-1.0.apk"
PKG="in.photobind.app"

phone_ip() {
  timeout 10 "$ADB" mdns services 2>/dev/null \
    | awk '/_adb-tls-connect/ {print $3; exit}'
}

CONNECT_ADDR="$(phone_ip)"
if [ -z "$CONNECT_ADDR" ]; then
  echo "No phone advertising wireless debugging on this network."
  echo "Turn on Settings → Developer options → Wireless debugging, same Wi-Fi."
  exit 1
fi
IP="${CONNECT_ADDR%%:*}"
echo "phone: $CONNECT_ADDR"

if [ $# -ge 2 ]; then
  echo "pairing with $IP:$1 …"
  "$ADB" pair "$IP:$1" "$2" || { echo "Pairing failed — check the code (it expires fast)."; exit 1; }
fi

"$ADB" connect "$CONNECT_ADDR" || true
sleep 2
if ! "$ADB" devices | grep -q "$IP.*device$"; then
  # The connect port rotates; re-resolve once after pairing.
  CONNECT_ADDR="$(phone_ip)"
  "$ADB" connect "$CONNECT_ADDR" || true
  sleep 2
fi
"$ADB" devices -l

echo "installing $APK …"
"$ADB" install -r "$APK" || { echo "Install failed."; exit 1; }
"$ADB" shell monkey -p "$PKG" -c android.intent.category.LAUNCHER 1 >/dev/null 2>&1
echo "Installed and launched Identity on the phone."
echo
echo "Point the app at this machine's API (same Wi-Fi):"
echo "  rebuild with:  ./gradlew assembleRelease -PapiBase=http://192.168.1.5:8000"
