"""Real-HTTP security + functional tests for the production-shaped dev API.

All additive-phase properties re-tested here: ciphertext-only ingress,
fragment scrubbing, revoked=410≠404, rate-limited enumeration, fail-closed
verification — plus session auth, ownership checks, and account deletion
revoking every code.
"""

import base64
import logging
import socket
import threading
import time
from pathlib import Path

import cv2
import httpx
import numpy as np
import pytest
import uvicorn

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "packages" / "binding" / "tests"))
from main import create_app, RATE_LIMIT_MAX, scrub_fragment  # noqa: E402
from conftest import synthetic_photo  # noqa: E402
from authhelp import Outbox, PASSWORD, register  # noqa: E402

CIPHER = {"ciphertext_b64": base64.urlsafe_b64encode(b"\x01" * 48).decode(),
          "nonce_b64": base64.urlsafe_b64encode(b"\x02" * 12).decode()}


class StubGoogle:
    def verify(self, id_token):
        if id_token != "good-token":
            raise ValueError("bad token")
        return {"sub": "g-123", "email": "g@example.org", "name": "G User"}


class LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def server(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys",
                     db_url=f"sqlite:///{tmp_path}/t.db",
                     google_verifier=StubGoogle())
    capture = LogCapture()
    logging.getLogger("photobind.access").addHandler(capture)
    logging.getLogger("photobind.access").setLevel(logging.INFO)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server failed to start")
        time.sleep(0.02)
    yield {"base": f"http://127.0.0.1:{port}", "port": port, "logs": capture,
           "outbox": outbox}
    srv.should_exit = True
    thread.join(timeout=5)
    logging.getLogger("photobind.access").removeHandler(capture)


def signed_in_client(server, email="t@example.org") -> httpx.Client:
    return register(server["base"], email, server["outbox"])


def make_code(c: httpx.Client, seed=41, label="LinkedIn") -> dict:
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(seed), "image/jpeg")},
               data={**CIPHER, "label": label, "encode_qr": "0"})
    assert r.status_code == 201, r.text
    return r.json()


# -- auth ---------------------------------------------------------------------

def test_auth_required_everywhere_private(server):
    for method, path in [("get", "/v1/codes"), ("get", "/v1/me"),
                         ("delete", "/v1/shares/x")]:
        assert getattr(httpx, method)(f"{server['base']}{path}").status_code == 401
    r = httpx.post(f"{server['base']}/v1/codes", data=CIPHER,
                   files={"photo": ("p.jpg", b"x", "image/jpeg")})
    assert r.status_code == 401


def test_signin_and_wrong_password(server):
    c = signed_in_client(server)
    c.post("/v1/auth/signout")
    assert c.get("/v1/me").status_code == 401
    r = c.post("/v1/auth/signin", json={"email": "t@example.org",
                                        "password": "wrong-password-xx"})
    assert r.status_code == 401
    r = c.post("/v1/auth/signin", json={"email": "t@example.org",
                                        "password": "a-long-password-123"})
    assert r.status_code == 200
    assert c.get("/v1/me").json()["email"] == "t@example.org"


def test_google_signin_stub(server):
    c = httpx.Client(base_url=server["base"])
    assert c.post("/v1/auth/google", json={"id_token": "bad",
                                           "accept_terms": True}).status_code == 401
    r = c.post("/v1/auth/google", json={"id_token": "good-token",
                                        "accept_terms": True})
    assert r.status_code == 200
    assert c.get("/v1/me").json()["email"] == "g@example.org"


def test_short_password_rejected(server):
    r = httpx.post(f"{server['base']}/v1/auth/signup",
                   json={"name": "x", "email": "s@example.org",
                         "password": "short", "accept_terms": True})
    assert r.status_code == 422
    assert "12 characters" in r.text


# -- ciphertext-only ingress -------------------------------------------------

def test_plaintext_field_rejected(server):
    c = signed_in_client(server)
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(42), "image/jpeg")},
               data={**CIPHER, "encode_qr": "0", "payload": '{"n":"PLAINTEXT"}'})
    assert r.status_code == 422
    assert "never plaintext" in r.text


def test_invalid_base64_rejected(server):
    c = signed_in_client(server)
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(43), "image/jpeg")},
               data={"ciphertext_b64": "!!!", "nonce_b64": CIPHER["nonce_b64"],
                     "encode_qr": "0"})
    assert r.status_code == 422


# -- fragments -----------------------------------------------------------------

def test_fragment_never_reaches_logs(server):
    for target in ("/r/abc123#SECRETFRAGMENT", "/r/abc123%23SECRETFRAGMENT"):
        with socket.create_connection(("127.0.0.1", server["port"])) as s:
            s.sendall(f"GET {target} HTTP/1.1\r\nHost: x\r\n"
                      f"Connection: close\r\n\r\n".encode())
            s.recv(4096)
    time.sleep(0.2)
    joined = "\n".join(server["logs"].lines)
    assert "SECRETFRAGMENT" not in joined
    assert joined.count("GET /r/abc") == 2


def test_scrubber_unit():
    assert scrub_fragment("/r/x#KEY") == "/r/x#[scrubbed]"
    assert scrub_fragment("/r/x%23KEY") == "/r/x#[scrubbed]"
    assert scrub_fragment("/r/x?a=1") == "/r/x?a=1"


# -- lifecycle ------------------------------------------------------------------

def test_resolution_revocation_and_scan_log(server):
    c = signed_in_client(server)
    code = make_code(c)
    oid = code["opaque_resolution_id"]

    r = httpx.get(f"{server['base']}/r/{oid}")  # public, no auth
    assert r.status_code == 200
    assert r.json()["ciphertext"] == CIPHER["ciphertext_b64"]

    log = c.get(f"/v1/shares/{code_share_id(c, oid)}/scans").json()
    assert log["scan_count"] == 1

    c.delete(f"/v1/shares/{code_share_id(c, oid)}")
    r = httpx.get(f"{server['base']}/r/{oid}")
    assert r.status_code == 410 and r.json()["status"] == "REVOKED"


def code_share_id(c, oid):
    return next(x["share_id"] for x in c.get("/v1/codes").json()["codes"]
                if x["opaque_resolution_id"] == oid)


def test_ownership_enforced(server):
    c1 = signed_in_client(server)
    code = make_code(c1)
    c2 = signed_in_client(server, "o@example.org")
    sid = code_share_id(c1, code["opaque_resolution_id"])
    assert c2.delete(f"/v1/shares/{sid}").status_code == 404  # not yours
    assert c2.get(f"/v1/photos/{code['photo_id']}.png").status_code == 404


def test_verify_photo_end_to_end(server):
    c = signed_in_client(server)
    code = make_code(c, seed=44)
    oid = code["opaque_resolution_id"]
    original = base64.b64decode(code["image_png_b64"])

    def verify(data):
        r = httpx.post(f"{server['base']}/v1/verify-photo",
                       data={"opaque_resolution_id": oid},
                       files={"photo": ("p.png", data, "image/png")})
        return r.json()["status"]

    assert verify(original) == "AUTHENTIC_EXACT"
    img = cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_COLOR)
    _, jp = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    assert verify(jp.tobytes()) in ("AUTHENTIC_DERIVED", "INSUFFICIENT_EVIDENCE")
    assert verify(synthetic_photo(99)) == "CONTENT_MODIFIED"


def test_delete_account_revokes_all_codes(server):
    c = signed_in_client(server)
    code = make_code(c)
    oid = code["opaque_resolution_id"]
    r = c.request("DELETE", "/v1/me", json={"confirm": "DELETE"})
    assert r.status_code == 200
    r = httpx.get(f"{server['base']}/r/{oid}")
    assert r.status_code == 410 and r.json()["status"] == "REVOKED"


def test_unknown_ids_404_then_rate_limited(server):
    codes = []
    for i in range(RATE_LIMIT_MAX + 10):
        codes.append(httpx.get(f"{server['base']}/r/nonexistent{i}").status_code)
    assert set(codes[:RATE_LIMIT_MAX]) == {404}
    assert codes[-1] == 429


def test_enumeration_guard_counts_the_caller_not_the_proxy(server, monkeypatch):
    """Two different callers behind the same proxy must not share one budget.

    This guards a bug that was live in production: the guard was keyed on
    request.client.host, which behind Cloud Run is always the same internal
    address, and behind Cloudflare rotates per edge PoP. One attacker's requests
    scattered across many buckets while unrelated visitors shared one, so the
    per-caller limit was decorative and only the global cap did any work.
    """
    import shared
    import trial

    shared.reset_local()
    monkeypatch.setattr(trial, "proxy_hops", lambda: 2)

    def burn(client_ip, n):
        # The chain a real request arrives with: caller-supplied value first,
        # then Cloudflare's view of the client, then the edge address.
        xff = f"1.2.3.4, {client_ip}, 172.71.0.1"
        return [
            httpx.get(f"{server['base']}/r/absent{client_ip}-{i}",
                      headers={"x-forwarded-for": xff}).status_code
            for i in range(n)
        ]

    assert set(burn("203.0.113.7", RATE_LIMIT_MAX)) == {404}
    assert burn("203.0.113.7", 1) == [429]          # that caller is now spent
    assert burn("198.51.100.9", 1) == [404]         # a different caller is not

    # And the caller cannot buy itself a fresh budget by claiming a new address:
    # the entry it controls is the first one, which is never the one counted.
    spoof = "9.9.9.9, 203.0.113.7, 172.71.0.1"
    assert httpx.get(f"{server['base']}/r/absent-spoof",
                     headers={"x-forwarded-for": spoof}).status_code == 429
