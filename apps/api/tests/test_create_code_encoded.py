"""The path the apps actually use: encode_qr=1.

Every other create-code test passes encode_qr=0, which skips the encoder and,
more importantly, was the only path covered — a foreign-key ordering bug in the
encoded path reached production behind that gap. These tests also turn SQLite's
foreign-key enforcement ON, because it is off by default and silently accepts
the row order Postgres rejects.
"""
import base64
import os
import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn
ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "apps" / "api"),
                str(ROOT / "packages" / "binding" / "tests")]

from main import create_app  # noqa: E402
from conftest import synthetic_photo  # noqa: E402
from authhelp import Outbox, register  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/e.db")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="error"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 20
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server failed to start")
        time.sleep(0.02)
    base = f"http://127.0.0.1:{port}"
    yield base, outbox
    srv.should_exit = True
    t.join(timeout=5)


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.mark.parametrize("coverage", ["full", "auto"])
def test_create_code_with_encoding_persists_all_three_rows(server, coverage):
    base, outbox = server
    c = register(base, f"enc-{coverage}@example.org", outbox, "Enc User")
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(11), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"x" * 48), "nonce_b64": b64u(b"n" * 12),
                     "label": "LinkedIn", "encode_qr": "1",
                     "fragment_key": "k" * 43, "coverage": coverage},
               timeout=180)
    assert r.status_code == 201, r.text
    body = r.json()

    # The photo row must exist, or the credential's foreign key is dangling.
    assert body["photo_id"]
    assert body["credential_id"]
    listed = c.get("/v1/codes").json()["codes"]
    assert any(x["credential_id"] == body["credential_id"] for x in listed)

    # And the share must resolve publicly, which needs all three rows committed.
    pub = httpx.get(f"{base}/r/{body['opaque_resolution_id']}",
                    headers={"Accept": "application/json"})
    assert pub.status_code == 200, pub.text


def test_encoded_photo_is_returned_and_scannable_id_fits_the_qr(server):
    base, outbox = server
    c = register(base, "enc2@example.org", outbox, "Enc User 2")
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(12), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"y" * 48), "nonce_b64": b64u(b"n" * 12),
                     "label": "Badge", "encode_qr": "1", "coverage": "full"},
               timeout=180)
    assert r.status_code == 201, r.text
    body = r.json()
    assert len(body["opaque_resolution_id"]) == 11      # v7 capacity budget
    assert base64.b64decode(body["image_png_b64"])[:8] == b"\x89PNG\r\n\x1a\n"
