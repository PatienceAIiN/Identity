"""Feedback, bug reports, and crash reports.

The load-bearing assertions are about restraint: declining diagnostics must
mean they are not stored, and nothing resembling a key may ever be accepted
into a report.
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
import mailer  # noqa: E402
from main import create_app  # noqa: E402
from authhelp import Outbox, register  # noqa: E402

ADMIN = "reports-admin-token"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    monkeypatch.setenv("PHOTOBIND_ADMIN_TOKEN", ADMIN)
    app = create_app(keys_dir=tmp_path / "k", db_url=f"sqlite:///{tmp_path}/r.db")
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
    yield {"base": f"http://127.0.0.1:{port}", "outbox": outbox}
    srv.should_exit = True
    t.join(timeout=5)


def post_report(base, **kw):
    body = {"kind": "feedback", "summary": "something", **kw}
    return httpx.post(f"{base}/v1/reports", json=body)


def test_feedback_can_be_sent_without_signing_in(env):
    """Someone who cannot sign in is exactly who most needs to report it."""
    r = post_report(env["base"], kind="bug", summary="Sign in loops forever")
    assert r.status_code == 201
    assert r.json()["kind"] == "bug"
    # It reached the admin as email.
    admin_mail = [m for m in env["outbox"].sent if "bug" in m["subject"].lower()]
    assert admin_mail, "no admin email sent"
    assert "Sign in loops forever" in admin_mail[-1]["text"]


def test_declining_diagnostics_stores_nothing(env):
    r = post_report(env["base"], include_diagnostics=False,
                    diagnostics={"user_agent": "should-not-be-kept"})
    assert r.json()["diagnostics_included"] is False
    listing = httpx.get(f"{env['base']}/v1/admin/reports",
                        headers={"x-admin-token": ADMIN}).json()
    assert listing["reports"][0]["has_diagnostics"] is False
    # And it never reached the admin email either.
    assert not any("should-not-be-kept" in m["text"] for m in env["outbox"].sent)


def test_key_like_fields_are_never_accepted(env):
    """A diagnostics blob must not be able to smuggle a decryption key."""
    post_report(env["base"], include_diagnostics=True,
                diagnostics={"page": "/app/codes.html",
                             "fragment_key": "SECRETKEYMATERIAL",
                             "aesKey": "ALSOSECRET",
                             "user_agent": "kept"})
    body = env["outbox"].sent[-1]["text"]
    assert "SECRETKEYMATERIAL" not in body
    assert "ALSOSECRET" not in body
    assert "kept" in body            # ordinary fields survive


def test_empty_summary_rejected(env):
    assert post_report(env["base"], summary="   ").status_code == 422


def test_summary_is_bounded(env):
    r = post_report(env["base"], summary="x" * 5000)
    assert r.status_code == 201       # accepted, but truncated server-side
    listing = httpx.get(f"{env['base']}/v1/admin/reports",
                        headers={"x-admin-token": ADMIN}).json()
    assert len(listing["reports"][0]["summary"]) <= 300


def test_admin_listing_requires_the_token(env):
    post_report(env["base"])
    assert httpx.get(f"{env['base']}/v1/admin/reports").status_code == 401
    assert httpx.get(f"{env['base']}/v1/admin/reports",
                     headers={"x-admin-token": "wrong"}).status_code == 401
    ok = httpx.get(f"{env['base']}/v1/admin/reports",
                   headers={"x-admin-token": ADMIN})
    assert ok.status_code == 200 and ok.json()["count"] >= 1


def test_report_survives_a_mail_outage(env, monkeypatch):
    """A failed email must not lose the report."""
    monkeypatch.setattr(mailer, "send", lambda *a, **k: "failed")
    r = post_report(env["base"], summary="mail is down")
    assert r.status_code == 201 and r.json()["delivery"] == "failed"
    listing = httpx.get(f"{env['base']}/v1/admin/reports",
                        headers={"x-admin-token": ADMIN}).json()
    assert any(x["summary"] == "mail is down" for x in listing["reports"])


def test_signed_in_reports_carry_the_account(env):
    c = register(env["base"], "reporter@example.org", env["outbox"])
    r = c.post("/v1/reports", json={"kind": "feedback", "summary": "nice work"})
    assert r.status_code == 201
    listing = httpx.get(f"{env['base']}/v1/admin/reports",
                        headers={"x-admin-token": ADMIN}).json()
    mine = [x for x in listing["reports"] if x["summary"] == "nice work"]
    assert mine and mine[0]["reporter"] == "reporter@example.org"


def test_account_deletion_sends_a_confirmation(env):
    c = register(env["base"], "gone@example.org", env["outbox"])
    r = c.request("DELETE", "/v1/me", json={"confirm": "DELETE"})
    assert r.status_code == 200
    assert "codes_revoked" in r.json()
    sent = [m for m in env["outbox"].sent
            if m["to"] == "gone@example.org" and "deleted" in m["subject"].lower()]
    assert sent, "no deletion confirmation email"
    assert "cannot be undone" in sent[-1]["text"]
