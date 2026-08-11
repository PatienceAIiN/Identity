#!/usr/bin/env bash
# Point the release channel at Cloudflare R2, verify the credentials actually
# work, then (optionally) publish the current APK.
#
#   export PHOTOBIND_R2_ACCESS_KEY=...      # R2 API token: Access Key ID
#   export PHOTOBIND_R2_SECRET_KEY=...      # R2 API token: Secret Access Key
#   scripts/configure_r2.sh                 # local check only
#   scripts/configure_r2.sh --deploy        # also set them on Cloud Run
#
# Optional:
#   PHOTOBIND_R2_PUBLIC_BASE=https://cdn.patienceai.in   # or the r2.dev URL
# Without a public base, APKs are served through the API's /dl/ route instead
# of straight from R2 — correct, just not a CDN.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
: "${PHOTOBIND_R2_BUCKET:=identity}"
: "${PHOTOBIND_R2_ENDPOINT:=https://6a3e72c0dbd0c739e1b3e041d80ccaf2.r2.cloudflarestorage.com}"
: "${PHOTOBIND_R2_PUBLIC_BASE:=}"
SERVICE="identity"
REGION="us-central1"
PROJECT="gen-lang-client-0839484503"

if [ -z "${PHOTOBIND_R2_ACCESS_KEY:-}" ] || [ -z "${PHOTOBIND_R2_SECRET_KEY:-}" ]; then
  cat <<'MSG'
Missing R2 credentials.

In the Cloudflare dashboard: R2 → API → "Manage API tokens" →
Create API token, permission "Object Read & Write", scoped to the
`identity` bucket. It shows an Access Key ID and a Secret Access Key once.

Then:
  export PHOTOBIND_R2_ACCESS_KEY='...'
  export PHOTOBIND_R2_SECRET_KEY='...'
  scripts/configure_r2.sh --deploy
MSG
  exit 1
fi

export PHOTOBIND_R2_BUCKET PHOTOBIND_R2_ENDPOINT PHOTOBIND_R2_PUBLIC_BASE

echo "bucket:   $PHOTOBIND_R2_BUCKET"
echo "endpoint: $PHOTOBIND_R2_ENDPOINT"
echo "public:   ${PHOTOBIND_R2_PUBLIC_BASE:-<none — served via /dl/>}"
echo

# Round-trip a probe object so a bad key or bucket fails here, loudly, rather
# than during a release.
"$ROOT/.venv/bin/python" - <<'PY' || exit 1
import os, sys
sys.path.insert(0, os.path.join(os.environ.get("ROOT", "."), "apps", "api"))
sys.path.insert(0, "apps/api")
from releases import Storage

s = Storage()
if not s.r2:
    sys.exit("Storage did not pick up R2 config — check the env vars")
key = "apks/.photobind-probe"
probe = b"photobind r2 probe"
try:
    s.put(key, probe)
    got = s.read(key)
    assert got == probe, "read-back mismatch"
    s.delete(key)
    assert s.read(key) is None, "delete did not remove the object"
except Exception as e:
    sys.exit(f"R2 check FAILED: {type(e).__name__}: {e}")
print("R2 check passed: write, read-back, and delete all work")
PY

if [ "${1:-}" != "--deploy" ]; then
  echo
  echo "Local check only. Re-run with --deploy to set these on Cloud Run."
  exit 0
fi

echo
echo "setting env vars on Cloud Run service $SERVICE …"
gcloud run services update "$SERVICE" --region "$REGION" --project "$PROJECT" \
  --update-env-vars \
"PHOTOBIND_R2_BUCKET=$PHOTOBIND_R2_BUCKET,PHOTOBIND_R2_ENDPOINT=$PHOTOBIND_R2_ENDPOINT,PHOTOBIND_R2_ACCESS_KEY=$PHOTOBIND_R2_ACCESS_KEY,PHOTOBIND_R2_SECRET_KEY=$PHOTOBIND_R2_SECRET_KEY,PHOTOBIND_R2_PUBLIC_BASE=$PHOTOBIND_R2_PUBLIC_BASE" \
  --quiet >/dev/null || exit 1

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" \
        --project "$PROJECT" --format="value(status.url)")
echo "storage now reports: $(curl -s "$URL/v1/app/latest" | grep -o '"storage":"[^"]*"')"
echo
echo "Publish the APK with:"
echo "  scripts/publish_apk.sh $URL"
