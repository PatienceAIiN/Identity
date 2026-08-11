"""OTA release channel: manifest correctness, checksum, supported-version
note, and the pruning rule that keeps rollback depth honest."""

import hashlib
import io
import os
import socket
import sys
import threading
import time
import zipfile
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ADMIN = "test-admin-token"


def fake_apk(marker: bytes = b"v1") -> bytes:
    """A minimal valid zip — the endpoint checks the PK header, not signing."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("AndroidManifest.xml", marker)
    return buf.getvalue()


@pytest.fixture()
def server(tmp_path, monkeypatch):
    # Config is read at request time, so setting it here is enough — no
    # module reloading (which would redeclare the SQLAlchemy table).
    monkeypatch.setenv("PHOTOBIND_ADMIN_TOKEN", ADMIN)
    monkeypatch.setenv("PHOTOBIND_PRUNE_KEEP", "1")
    import releases
    monkeypatch.setattr(releases, "LOCAL_APK_DIR", tmp_path / "apks")
    from main import create_app

    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/o.db")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server failed to start")
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


def publish(base, code, name, apk=None, min_sdk=26, notes="", mandatory=False):
    apk = apk or fake_apk(name.encode())
    return httpx.post(f"{base}/v1/admin/releases",
                      headers={"x-admin-token": ADMIN},
                      files={"apk": ("app.apk", apk, "application/octet-stream")},
                      data={"version_code": code, "version_name": name,
                            "min_sdk": min_sdk, "notes": notes,
                            "mandatory": str(mandatory).lower()})


def test_no_release_yet_says_so(server):
    m = httpx.get(server + "/v1/app/latest").json()
    assert m["available"] is False and "no release" in m["reason"]


def test_publish_and_manifest_fields(server):
    apk = fake_apk(b"one")
    r = publish(server, 1, "1.0", apk, notes="First build")
    assert r.status_code == 201, r.text
    assert r.json()["sha256"] == hashlib.sha256(apk).hexdigest()

    m = httpx.get(server + "/v1/app/latest").json()
    assert m["available"] and m["version_code"] == 1 and m["version_name"] == "1.0"
    assert m["sha256"] == hashlib.sha256(apk).hexdigest()
    assert m["min_sdk"] == 26 and m["min_android"] == "Android 8.0 Oreo"
    assert m["size_bytes"] == len(apk) and m["notes"] == "First build"
    assert m["storage"] == "local-dev-disk"      # honest about not being a CDN


def test_download_serves_exact_bytes(server):
    apk = fake_apk(b"exact")
    publish(server, 1, "1.0", apk)
    manifest = httpx.get(f"{server}/v1/app/latest").json()
    name = manifest["url"].rsplit("/", 1)[-1]
    # The filename embeds a content digest, so replacing a build changes the URL
    # and no cache can answer with the previous bytes.
    assert name.startswith("identity-1.0-1-") and name.endswith(".apk")
    assert manifest["sha256"][:12] in name

    got = httpx.get(f"{server}/dl/{name}")
    assert got.status_code == 200
    assert got.content == apk
    assert "android.package-archive" in got.headers["content-type"]


def test_admin_token_required(server):
    r = httpx.post(f"{server}/v1/admin/releases",
                   files={"apk": ("a.apk", fake_apk(), "application/octet-stream")},
                   data={"version_code": 9, "version_name": "9.0"})
    assert r.status_code == 401


def test_non_apk_rejected(server):
    r = publish(server, 2, "2.0", apk=b"not a zip at all")
    assert r.status_code == 422 and "not an APK" in r.text


def test_duplicate_version_code_rejected(server):
    publish(server, 1, "1.0")
    assert publish(server, 1, "1.0-again").status_code == 409


def test_newer_release_wins_and_min_sdk_label(server):
    publish(server, 1, "1.0")
    publish(server, 2, "1.1", min_sdk=31)
    m = httpx.get(server + "/v1/app/latest").json()
    assert m["version_code"] == 2 and m["min_android"] == "Android 12"


def test_pruning_needs_a_confirmed_install_and_keeps_rollback(server):
    names = {}
    for code, name in ((1, "1.0"), (2, "1.1"), (3, "1.2")):
        publish(server, code, name, fake_apk(name.encode()))
        names[code] = httpx.get(f"{server}/v1/app/latest").json()["url"].rsplit("/", 1)[-1]

    # Nothing has reported an install, so nothing may be deleted yet.
    assert httpx.get(f"{server}/dl/{names[1]}").status_code == 200
    assert httpx.post(f"{server}/v1/app/installed",
                      data={"version_code": 2}).json()["pruned"] == []

    r = httpx.post(f"{server}/v1/app/installed", data={"version_code": 3})
    assert r.status_code == 200
    # keep=1 -> v1.1 survives as the rollback, v1.0 is removed from storage.
    assert r.json()["pruned"] == [1], r.json()
    assert httpx.get(f"{server}/dl/{names[1]}").status_code in (404, 410)
    assert httpx.get(f"{server}/dl/{names[2]}").status_code == 200
    assert httpx.get(f"{server}/dl/{names[3]}").status_code == 200


def test_installed_unknown_version_404(server):
    assert httpx.post(f"{server}/v1/app/installed",
                      data={"version_code": 99}).status_code == 404


# -- R2 configuration ----------------------------------------------------------

def test_endpoint_accepts_bucket_suffixed_url():
    """Cloudflare displays the endpoint with the bucket appended; the S3 API
    wants it without. Both forms must work, or you get an opaque signature
    error at publish time."""
    from releases import _normalize_endpoint
    account = "https://abc123.r2.cloudflarestorage.com"
    assert _normalize_endpoint(f"{account}/identity", "identity") == account
    assert _normalize_endpoint(f"{account}/identity/", "identity") == account
    assert _normalize_endpoint(account, "identity") == account
    # A bucket name that merely appears elsewhere must not be stripped.
    assert _normalize_endpoint(f"{account}/other", "identity") == f"{account}/other"


def test_storage_selection_is_explicit(monkeypatch):
    """Storage never silently pretends to be a CDN: partial config falls back
    to local disk and reports that in the manifest."""
    from releases import Storage
    for k in ("PHOTOBIND_R2_BUCKET", "PHOTOBIND_R2_ENDPOINT",
              "PHOTOBIND_R2_ACCESS_KEY", "PHOTOBIND_R2_SECRET_KEY",
              "PHOTOBIND_R2_PUBLIC_BASE"):
        monkeypatch.delenv(k, raising=False)
    assert Storage().kind == "local-dev-disk"

    # Bucket + endpoint but no keys is still not R2.
    monkeypatch.setenv("PHOTOBIND_R2_BUCKET", "identity")
    monkeypatch.setenv("PHOTOBIND_R2_ENDPOINT", "https://x.r2.cloudflarestorage.com")
    assert Storage().kind == "local-dev-disk"

    monkeypatch.setenv("PHOTOBIND_R2_ACCESS_KEY", "k")
    monkeypatch.setenv("PHOTOBIND_R2_SECRET_KEY", "s")
    r2 = Storage()
    assert r2.kind == "cloudflare-r2"
    # No public base -> download goes through the API, not a broken R2 URL.
    assert r2.url("apks/a.apk", "https://identity.patienceai.in") == \
        "https://identity.patienceai.in/dl/a.apk"
    monkeypatch.setenv("PHOTOBIND_R2_PUBLIC_BASE", "https://cdn.patienceai.in")
    assert Storage().url("apks/a.apk", "https://x") == \
        "https://cdn.patienceai.in/apks/a.apk"
