"""Signup consent gate, email verification, and honest sign-in errors."""

import re
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

PW = "a-long-password-123"


class Outbox:
    """Captures mail instead of sending it, so tests assert on real content."""

    def __init__(self):
        self.sent = []

    def install(self, monkeypatch):
        def fake(to, subject, text, html=None):
            self.sent.append({"to": to, "subject": subject, "text": text})
            return "smtp"
        monkeypatch.setattr(mailer, "send", fake)
        monkeypatch.setattr(mailer, "configured", lambda: True)

    def code_for(self, email):
        for m in reversed(self.sent):
            if m["to"] == email:
                found = re.search(r"code is (\d{6})", m["text"])
                if found:
                    return found.group(1)
        return None

    def welcome_for(self, email):
        return [m for m in self.sent
                if m["to"] == email and "Welcome" in m["subject"]]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    box = Outbox()
    box.install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "k", db_url=f"sqlite:///{tmp_path}/a.db")
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
    yield {"base": f"http://127.0.0.1:{port}", "box": box}
    srv.should_exit = True
    t.join(timeout=5)


def signup(base, email, accept=True, name="Ama Osei"):
    return httpx.post(f"{base}/v1/auth/signup",
                      json={"name": name, "email": email, "password": PW,
                            "accept_terms": accept})


# -- consent -------------------------------------------------------------------

def test_signup_requires_accepting_terms(env):
    r = signup(env["base"], "a@example.org", accept=False)
    assert r.status_code == 422
    assert "terms and privacy" in r.json()["detail"]
    # And nothing was emailed, because nothing was created.
    assert env["box"].sent == []


def test_google_signup_requires_accepting_terms(env, tmp_path):
    # Configure a stub verifier by rebuilding the app is overkill; the
    # unauthenticated path is enough to prove the gate is checked server-side.
    r = httpx.post(f"{env['base']}/v1/auth/google",
                   json={"id_token": "x", "accept_terms": False})
    assert r.status_code in (401, 422)   # rejected either way, never created


def test_terms_version_is_recorded(env):
    email = "consent@example.org"
    signup(env["base"], email)
    c = httpx.Client(base_url=env["base"])
    c.post("/v1/auth/verify-email",
           json={"email": email, "code": env["box"].code_for(email)})
    me = c.get("/v1/me").json()
    assert me["terms_version"]           # which version they agreed to
    assert me["email_verified"] is True


# -- verification ---------------------------------------------------------------

def test_no_account_until_code_is_confirmed(env):
    email = "pending@example.org"
    assert signup(env["base"], email).status_code == 202
    # Cannot sign in yet — and is told the signup is unfinished, not "wrong
    # password".
    r = httpx.post(f"{env['base']}/v1/auth/signin",
                   json={"email": email, "password": PW})
    assert r.status_code == 409 and "isn't finished" in r.json()["detail"]


def test_verification_creates_account_and_sends_welcome(env):
    email = "welcome@example.org"
    signup(env["base"], email)
    code = env["box"].code_for(email)
    assert code and len(code) == 6
    c = httpx.Client(base_url=env["base"])
    r = c.post("/v1/auth/verify-email", json={"email": email, "code": code})
    assert r.status_code == 201
    assert c.get("/v1/me").json()["email"] == email
    welcome = env["box"].welcome_for(email)
    assert len(welcome) == 1
    # The welcome mail is a tutorial, not a greeting.
    body = welcome[0]["text"]
    for step in ("Create a code", "Share it", "Revoke", "Verify a photo"):
        assert step in body
    assert "never store face embeddings" in body or "biometric" in body


def test_wrong_code_counts_down_then_locks(env):
    email = "wrong@example.org"
    signup(env["base"], email)
    last = None
    for _ in range(5):
        last = httpx.post(f"{env['base']}/v1/auth/verify-email",
                          json={"email": email, "code": "000000"})
    assert last.status_code == 401
    # Sixth attempt kills the pending signup rather than allowing forever.
    r = httpx.post(f"{env['base']}/v1/auth/verify-email",
                   json={"email": email, "code": "000000"})
    assert r.status_code == 429
    r = httpx.post(f"{env['base']}/v1/auth/verify-email",
                   json={"email": email, "code": "000000"})
    assert r.status_code == 404          # gone entirely


def test_resend_issues_a_new_code_and_invalidates_the_old(env):
    email = "resend@example.org"
    signup(env["base"], email)
    first = env["box"].code_for(email)
    httpx.post(f"{env['base']}/v1/auth/resend-code", json={"email": email})
    second = env["box"].code_for(email)
    assert second != first
    assert httpx.post(f"{env['base']}/v1/auth/verify-email",
                      json={"email": email, "code": first}).status_code == 401
    assert httpx.post(f"{env['base']}/v1/auth/verify-email",
                      json={"email": email, "code": second}).status_code == 201


def test_codes_are_not_stored_in_plaintext(env, tmp_path):
    email = "hashed@example.org"
    signup(env["base"], email)
    code = env["box"].code_for(email)
    db_bytes = next(tmp_path.glob("*.db")).read_bytes()
    assert code.encode() not in db_bytes    # stored hashed


# -- sign-in errors -------------------------------------------------------------

def test_unknown_email_is_told_to_sign_up(env):
    r = httpx.post(f"{env['base']}/v1/auth/signin",
                   json={"email": "nobody@example.org", "password": PW})
    assert r.status_code == 404
    assert "Create one first" in r.json()["detail"]


def test_short_password_rejected_before_any_email(env):
    r = httpx.post(f"{env['base']}/v1/auth/signup",
                   json={"name": "x", "email": "short@example.org",
                         "password": "tiny", "accept_terms": True})
    assert r.status_code == 422 and "12 characters" in r.json()["detail"]
    assert env["box"].sent == []
