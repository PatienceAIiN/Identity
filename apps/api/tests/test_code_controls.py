"""Editing and deleting a code you already made.

The interesting cases are the ones where a control could quietly break a promise
the product makes: a label change must not resurrect a revoked copy, and deleting
a code must leave copies already in the world saying "switched off" rather than
"never existed".
"""
import base64
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
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


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/k.db")
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
    yield f"http://127.0.0.1:{port}", outbox
    srv.should_exit = True
    t.join(timeout=5)


def make_code(c, label="LinkedIn"):
    r = c.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(4), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"z" * 48), "nonce_b64": b64u(b"n" * 12),
                     "label": label, "encode_qr": "0"})
    assert r.status_code == 201, r.text
    return r.json()


def test_label_expiry_and_scan_cap_can_be_changed(server):
    base, outbox = server
    c = register(base, "ctl@example.org", outbox, "Ctl")
    code = make_code(c)
    share_id = c.get("/v1/codes").json()["codes"][0]["share_id"]

    r = c.patch(f"/v1/shares/{share_id}", json={"label": "Conference badge"})
    assert r.status_code == 200 and r.json()["label"] == "Conference badge"

    # A cap of one: the first scan works, the second is refused.
    assert c.patch(f"/v1/shares/{share_id}", json={"max_scans": 1}).status_code == 200
    oid = code["opaque_resolution_id"]
    assert httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"}).status_code == 200
    second = httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"})
    assert second.status_code == 410

    # 0 clears the cap, and the code resolves again.
    assert c.patch(f"/v1/shares/{share_id}", json={"max_scans": 0}).status_code == 200
    assert httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"}).status_code == 200

    # An expiry in the past takes effect; clearing it brings the code back.
    past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert c.patch(f"/v1/shares/{share_id}", json={"expires_at": past}).status_code == 200
    assert httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"}).status_code == 410
    assert c.patch(f"/v1/shares/{share_id}", json={"expires_at": ""}).status_code == 200
    assert httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"}).status_code == 200


def test_editing_cannot_bring_back_a_switched_off_copy(server):
    base, outbox = server
    c = register(base, "off@example.org", outbox, "Off")
    code = make_code(c)
    share_id = c.get("/v1/codes").json()["codes"][0]["share_id"]
    assert c.request("DELETE", f"/v1/shares/{share_id}").status_code == 200

    # Every editable field, and none of them may resurrect it.
    c.patch(f"/v1/shares/{share_id}", json={"label": "try again",
                                            "expires_at": "", "max_scans": 0})
    r = httpx.get(f"{base}/r/{code['opaque_resolution_id']}",
                  headers={"Accept": "application/json"})
    assert r.status_code == 410 and r.json()["status"] == "REVOKED"
    # And "revoked" is not an accepted field at all.
    assert c.patch(f"/v1/shares/{share_id}",
                   json={"revoked_at": None}).status_code == 422


def test_deleting_a_code_leaves_its_copies_saying_switched_off(server):
    base, outbox = server
    c = register(base, "del@example.org", outbox, "Del")
    code = make_code(c)
    oid = code["opaque_resolution_id"]
    photo_id = code["photo_id"]
    assert c.get(f"/v1/photos/{photo_id}.png").status_code == 200

    r = c.request("DELETE", f"/v1/codes/{code['credential_id']}")
    assert r.status_code == 200 and r.json()["copies_switched_off"] == 1

    assert c.get("/v1/codes").json()["count"] == 0
    assert c.get(f"/v1/photos/{photo_id}.png").status_code == 404
    # The printed copy in someone's wallet is told it is off, not that it never was.
    r = httpx.get(f"{base}/r/{oid}", headers={"Accept": "application/json"})
    assert r.status_code == 410 and r.json()["status"] == "REVOKED"


def test_controls_are_scoped_to_the_owner(server):
    base, outbox = server
    owner = register(base, "owner@example.org", outbox, "Owner")
    code = make_code(owner)
    share_id = owner.get("/v1/codes").json()["codes"][0]["share_id"]

    other = register(base, "other@example.org", outbox, "Other")
    assert other.patch(f"/v1/shares/{share_id}", json={"label": "mine now"}).status_code == 404
    assert other.request("DELETE", f"/v1/codes/{code['credential_id']}").status_code == 404
    # Untouched, and still the owner's.
    assert owner.get("/v1/codes").json()["count"] == 1


def test_monthly_code_limit_is_a_counter_not_a_row_count(server, monkeypatch):
    """Deleting a code must not hand back allowance — otherwise the limit only
    binds people who keep their codes."""
    import main
    monkeypatch.setattr(main, "USER_MONTHLY_CODES", 2, raising=False)
    base, outbox = server
    c = register(base, "quota@example.org", outbox, "Quota")

    q = c.get("/v1/me/quota").json()
    assert q["limit"] == 1000 or q["limit"] == 2      # module constant vs patched
    assert q["used"] == 0 and q["resets_at"].endswith("+05:30")
    assert q["resets_at"][8:10] == "01"               # the 1st, IST

    first = make_code(c, "one")
    make_code(c, "two")
    assert c.get("/v1/me/quota").json()["used"] == 2

    # Deleting one does not free a slot.
    assert c.request("DELETE", f"/v1/codes/{first['credential_id']}").status_code == 200
    assert c.get("/v1/me/quota").json()["used"] == 2, "deleting a code reset the quota"
