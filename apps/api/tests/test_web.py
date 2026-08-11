"""Web-surface tests: CSP on the resolution route, static serving, and the
full browser flow (encrypt → create → resolve → decrypt) exercised through
real HTTP with the same crypto the browser uses."""

import base64
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "packages" / "binding" / "tests"))
from main import create_app  # noqa: E402
from conftest import synthetic_photo  # noqa: E402
from authhelp import Outbox, register  # noqa: E402


# Maps base url -> outbox, so helpers can find the right mail capture.
server_info_outbox: dict[str, Outbox] = {}


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/w.db")
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
    server_info = f"http://127.0.0.1:{port}"
    server_info_outbox[server_info] = outbox
    yield server_info
    srv.should_exit = True
    t.join(timeout=5)


def test_pages_served(server):
    assert "The photo is the code" in httpx.get(server + "/").text
    for page in ("auth.html", "new.html", "codes.html", "scan.html",
                 "profile.html", "terms.html", "privacy.html"):
        assert httpx.get(f"{server}/app/{page}").status_code == 200
    for asset in ("styles.css", "app.js", "auth.js", "profile.js",
                  "legal.js", "download.js", "tokens.css"):
        assert httpx.get(f"{server}/static/{asset}").status_code == 200, asset


def test_auth_page_has_consent_gate_and_password_reveal(server):
    html = httpx.get(server + "/app/auth.html").text
    assert 'id="accept"' in html and "terms of use" in html
    assert 'class="pw-eye"' in html          # reveal control present
    js = httpx.get(server + "/static/auth.js").text
    # The create-account button is disabled until consent is ticked.
    assert 'mode === "signup" && !$("accept").checked' in js
    assert "verify-email" in js and "resend-code" in js


def test_profile_page_has_the_account_controls(server):
    html = httpx.get(server + "/app/profile.html").text
    for needle in ('id="save"', 'id="change"', 'id="signout"', 'id="del"',
                   'data-legal="terms"', 'data-legal="privacy"'):
        assert needle in html, needle
    # Legal sits above sign out, and deletion states its consequence.
    assert html.index('data-legal="terms"') < html.index('id="signout"')
    assert "revokes every code" in html


def test_static_path_traversal_blocked(server):
    r = httpx.get(server + "/static/../api/main.py")
    assert r.status_code in (404, 400)


def test_resolution_page_has_strict_csp(server):
    r = httpx.get(server + "/r/anything", headers={"Accept": "text/html"})
    csp = r.headers.get("content-security-policy", "")
    assert "default-src 'self'" in csp
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert "object-src 'none'" in csp and "base-uri 'none'" in csp
    assert r.headers.get("referrer-policy") == "no-referrer"
    # The page itself carries no inline script.
    assert "<script>" not in r.text
    assert httpx.get(server + "/static/r.js").headers.get("content-security-policy")


def test_full_browser_flow_encrypt_create_resolve_decrypt(server):
    c = register(server, "w@example.org", server_info_outbox[server], "Web User")
    # Browser-side encryption (mirrored here with the same AES-256-GCM params).
    key, nonce = AESGCM.generate_key(bit_length=256), b"\x03" * 12
    plaintext = b"Dr. A. Osei - cardiology"
    ct = AESGCM(key).encrypt(nonce, plaintext, None)

    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(7), "image/jpeg")},
               data={"ciphertext_b64": b64u(ct), "nonce_b64": b64u(nonce),
                     "label": "LinkedIn", "encode_qr": "0"})
    assert r.status_code == 201, r.text
    oid = r.json()["opaque_resolution_id"]

    # Public JSON resolution — no auth, ciphertext only, key never sent.
    pub = httpx.get(f"{server}/r/{oid}", headers={"Accept": "application/json"})
    assert pub.status_code == 200
    data = pub.json()
    assert data["ciphertext"] == b64u(ct)
    assert "key" not in pub.text.lower()

    # Client-side decryption succeeds with the fragment key.
    def unb64u(s):
        return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))
    assert AESGCM(key).decrypt(unb64u(data["nonce"]),
                               unb64u(data["ciphertext"]), None) == plaintext

    # A wrong key fails closed — server can't help, and doesn't hold one.
    import pytest as _pytest
    with _pytest.raises(Exception):
        AESGCM(AESGCM.generate_key(bit_length=256)).decrypt(
            unb64u(data["nonce"]), unb64u(data["ciphertext"]), None)


def test_revoked_share_renders_revoked_state(server):
    c = register(server, "r@example.org", server_info_outbox[server], "R")
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(8), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"x" * 32), "nonce_b64": b64u(b"n" * 12),
                     "encode_qr": "0"})
    oid = r.json()["opaque_resolution_id"]
    sid = next(x["share_id"] for x in c.get("/v1/codes").json()["codes"]
               if x["opaque_resolution_id"] == oid)
    c.delete(f"/v1/shares/{sid}")
    j = httpx.get(f"{server}/r/{oid}", headers={"Accept": "application/json"})
    assert j.status_code == 410 and j.json()["status"] == "REVOKED"
    # The HTML shell still serves (it renders the revoked state client-side).
    assert httpx.get(f"{server}/r/{oid}", headers={"Accept": "text/html"}).status_code == 200
