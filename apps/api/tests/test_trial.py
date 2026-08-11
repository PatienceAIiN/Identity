"""Free trial: five codes, nothing stored, quota that cannot be talked out of.

The important assertions here are the negative ones — that the limit is not
bypassable from the client side, and that a trial genuinely persists nothing.
"""

import socket
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[3]
                       / "packages" / "binding" / "tests"))
from db import Credential, Photo, Share, make_engine  # noqa: E402
from main import create_app  # noqa: E402
from conftest import synthetic_photo  # noqa: E402
from authhelp import Outbox, register  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    monkeypatch.setenv("PHOTOBIND_TRIAL_LIMIT", "5")
    monkeypatch.setenv("PHOTOBIND_PROXY_HOPS", "0")     # nothing in front in tests
    monkeypatch.delenv("PHOTOBIND_TRUST_PROXY_HEADERS", raising=False)
    db_path = tmp_path / "t.db"
    app = create_app(keys_dir=tmp_path / "k", db_url=f"sqlite:///{db_path}")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port,
                                        log_level="error", proxy_headers=False))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not srv.started:
        if time.time() > deadline:
            raise RuntimeError("server failed to start")
        time.sleep(0.02)
    yield {"base": f"http://127.0.0.1:{port}", "db": db_path, "outbox": outbox}
    srv.should_exit = True
    t.join(timeout=5)


def make_trial(base_or_client, seed=5, payload="https://example.org/me"):
    c = base_or_client if isinstance(base_or_client, httpx.Client) else \
        httpx.Client(base_url=base_or_client, timeout=120)
    return c.post("/v1/trial/codes",
                  files={"photo": ("p.jpg", synthetic_photo(seed), "image/jpeg")},
                  data={"payload": payload, "coverage": "full"})


def test_five_free_codes_then_a_prompt_to_sign_up(env):
    c = httpx.Client(base_url=env["base"], timeout=180)
    for i in range(5):
        r = make_trial(c, seed=i)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["saved"] is False
        assert body["remaining"] == 4 - i
        assert body["decode_rate"] >= 85
        # The trade-off is restated on every single response.
        assert "cannot be switched off" in body["note"]

    r = make_trial(c, seed=9)
    assert r.status_code == 402
    assert "all 5 free codes" in r.json()["detail"]
    assert "account" in r.json()["detail"]


def test_a_trial_stores_absolutely_nothing(env):
    c = httpx.Client(base_url=env["base"], timeout=180)
    assert make_trial(c).status_code == 201
    Session = sessionmaker(make_engine(f"sqlite:///{env['db']}"))
    with Session() as db:
        assert db.query(Photo).count() == 0
        assert db.query(Credential).count() == 0
        assert db.query(Share).count() == 0


def test_the_limit_cannot_be_forged_from_the_client(env):
    """Every value a caller controls must fail to reset the quota."""
    c = httpx.Client(base_url=env["base"], timeout=180)
    for i in range(5):
        assert make_trial(c, seed=i).status_code == 201

    forgeries = [
        {"X-Forwarded-For": "1.2.3.4"},
        {"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.9.9.9"},
        {"CF-Connecting-IP": "9.9.9.9"},
        {"X-Real-IP": "7.7.7.7"},
        {"Forwarded": "for=1.2.3.4"},
    ]
    for headers in forgeries:
        r = c.post("/v1/trial/codes", headers=headers,
                   files={"photo": ("p.jpg", synthetic_photo(1), "image/jpeg")},
                   data={"payload": "x"})
        assert r.status_code == 402, f"{headers} bypassed the quota"

    # A completely fresh client (no trial cookie) is still held by the address.
    fresh = httpx.Client(base_url=env["base"], timeout=120)
    assert make_trial(fresh).status_code == 402


def test_signed_in_users_are_sent_to_the_real_flow(env):
    c = register(env["base"], "trial@example.org", env["outbox"])
    r = make_trial(c)
    assert r.status_code == 409
    assert "New code" in r.json()["detail"]
    # And their status reports no trial at all.
    st = c.get("/v1/trial/status").json()
    assert st["trial"] is False and st["signed_in"] is True


def test_inputs_are_bounded(env):
    c = httpx.Client(base_url=env["base"], timeout=180)
    r = c.post("/v1/trial/codes",
               files={"photo": ("p.jpg", synthetic_photo(2), "image/jpeg")},
               data={"payload": "y" * 200})
    assert r.status_code == 422 and "characters" in r.json()["detail"]

    r = c.post("/v1/trial/codes",
               files={"photo": ("p.jpg", synthetic_photo(3), "image/jpeg")},
               data={"payload": "   "})
    assert r.status_code == 422

    r = c.post("/v1/trial/codes",
               files={"photo": ("p.jpg", b"x" * (13 * 1024 * 1024), "image/jpeg")},
               data={"payload": "ok"})
    assert r.status_code == 413


def test_status_reports_the_quota_honestly(env):
    c = httpx.Client(base_url=env["base"], timeout=180)
    st = c.get("/v1/trial/status").json()
    assert st == {"trial": True, "signed_in": False, "limit": 5,
                  "used": 0, "remaining": 5}
    make_trial(c)
    st = c.get("/v1/trial/status").json()
    assert st["used"] == 1 and st["remaining"] == 4


def test_security_headers_are_present_everywhere(env):
    for path in ("/", "/app/new.html", "/v1/trial/status"):
        h = httpx.get(env["base"] + path).headers
        assert h.get("x-content-type-options") == "nosniff", path
        assert h.get("x-frame-options") == "DENY", path
        assert "referrer-policy" in h, path
        assert "permissions-policy" in h, path
