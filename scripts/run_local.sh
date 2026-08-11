#!/usr/bin/env bash
# Run the whole stack locally: API + web app + release channel.
#
#   scripts/run_local.sh            # binds 0.0.0.0:8000 so your phone can reach it
#
# Open http://<this-machine-ip>:8000 on the laptop or the phone (same Wi-Fi).
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LAN_IP="$(ip -4 addr show | grep -oP 'inet \K[\d.]+' | grep -v '^127\.' \
          | grep -v '^100\.' | head -1)"
PORT="${PORT:-8000}"

export LD_LIBRARY_PATH="$ROOT/.venv/lib"
export PHOTOBIND_DB_URL="sqlite:///$ROOT/run/identity.db"
export PHOTOBIND_PUBLIC_HOST="http://$LAN_IP:$PORT"
export PHOTOBIND_CORS_ORIGIN="http://$LAN_IP:$PORT"
# Local publishing token for the release channel (dev only).
export PHOTOBIND_ADMIN_TOKEN="${PHOTOBIND_ADMIN_TOKEN:-local-dev-admin}"
# Locally there is no proxy in front, so forwarded headers must not be believed.
export PHOTOBIND_TRUST_PROXY_HEADERS=0
export PHOTOBIND_PROXY_HOPS=0
# R2 is unset locally, so APKs are stored in run/apks and served from /dl/.

mkdir -p "$ROOT/run"
echo "Identity — local stack"
echo "  laptop : http://127.0.0.1:$PORT"
echo "  phone  : http://$LAN_IP:$PORT   (same Wi-Fi)"
echo "  db     : $ROOT/run/identity.db"
echo "  admin  : x-admin-token: $PHOTOBIND_ADMIN_TOKEN"
echo
cd "$ROOT/apps/api"
# --no-proxy-headers: uvicorn otherwise rewrites request.client from
# X-Forwarded-For, which would let any caller choose its own identity. The app
# decides what to trust (see trial.py).
exec "$ROOT/.venv/bin/python" -m uvicorn main:app --host 0.0.0.0 --port "$PORT" \
    --no-proxy-headers --log-level info
