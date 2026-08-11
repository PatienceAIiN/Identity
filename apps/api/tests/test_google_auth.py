"""Google sign-in: consent still required, tokens still verified.

A real Google ID token cannot be minted in a test, so the verifier is stubbed
— which is the right seam anyway: what needs proving here is that the endpoint
enforces consent, records it, and refuses anything the verifier rejects. The
verifier itself checks signature, audience, issuer and expiry via google-auth
and is exercised by configuration, not by a forged token.
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
from main import create_app  # noqa: E402
from authhelp import Outbox  # noqa: E402


class StubGoogle:
    """Stands in for Google's verifier. Only 'good' is a valid token."""

    def verify(self, id_token):
        if id_token != "good":
            raise ValueError("bad token")
        return {"sub": "google-sub-1", "email": "gu@example.org", "name": "G User"}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    monkeypatch.setenv("PHOTOBIND_GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    app = create_app(keys_dir=tmp_path / "k", db_url=f"sqlite:///{tmp_path}/g.db",
                     google_verifier=StubGoogle())
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


def test_config_exposes_only_the_client_id(env):
    cfg = httpx.get(f"{env['base']}/v1/config").json()
    assert cfg["google_enabled"] is True
    assert cfg["google_client_id"].endswith(".apps.googleusercontent.com")
    # The client secret must never be reachable from the browser.
    body = httpx.get(f"{env['base']}/v1/config").text
    assert "secret" not in body.lower()
    assert "GOCSPX" not in body


def test_first_google_signin_requires_consent(env):
    c = httpx.Client(base_url=env["base"])
    r = c.post("/v1/auth/google", json={"id_token": "good", "accept_terms": False})
    assert r.status_code == 422
    assert "terms and privacy" in r.json()["detail"]
    # Nothing was created, so nothing can sign in.
    assert c.get("/v1/me").status_code == 401


def test_google_signin_creates_the_account_and_records_consent(env):
    c = httpx.Client(base_url=env["base"])
    r = c.post("/v1/auth/google", json={"id_token": "good", "accept_terms": True})
    assert r.status_code == 200
    me = c.get("/v1/me").json()
    assert me["email"] == "gu@example.org"
    assert me["auth"] == "google"
    assert me["email_verified"] is True      # Google already verified it
    assert me["terms_version"]               # which version they agreed to
    # A welcome email goes out once, on creation.
    assert len(env["outbox"].welcome_for("gu@example.org")) == 1


def test_returning_google_user_needs_no_further_consent(env):
    c1 = httpx.Client(base_url=env["base"])
    c1.post("/v1/auth/google", json={"id_token": "good", "accept_terms": True})
    c2 = httpx.Client(base_url=env["base"])
    r = c2.post("/v1/auth/google", json={"id_token": "good", "accept_terms": False})
    assert r.status_code == 200               # already has an account
    # And no second welcome email.
    assert len(env["outbox"].welcome_for("gu@example.org")) == 1


def test_invalid_token_is_refused(env):
    r = httpx.post(f"{env['base']}/v1/auth/google",
                   json={"id_token": "forged", "accept_terms": True})
    assert r.status_code == 401


def test_password_signin_blocked_for_a_google_account(env):
    c = httpx.Client(base_url=env["base"])
    c.post("/v1/auth/google", json={"id_token": "good", "accept_terms": True})
    c.post("/v1/auth/signout")
    r = c.post("/v1/auth/signin", json={"email": "gu@example.org",
                                        "password": "a-long-password-123"})
    assert r.status_code == 409
    assert "Google" in r.json()["detail"]


def test_audience_allow_list_is_enforced(monkeypatch):
    """A token minted for another app's client id must be refused, even though
    its signature is genuine."""
    import auth as auth_mod
    monkeypatch.setenv("PHOTOBIND_GOOGLE_CLIENT_ID", "web-client.apps.googleusercontent.com")
    monkeypatch.setenv("PHOTOBIND_GOOGLE_ALLOWED_AUDIENCES",
                       "android-client.apps.googleusercontent.com")
    v = auth_mod.GoogleVerifier()
    assert v.allowed == ["web-client.apps.googleusercontent.com",
                         "android-client.apps.googleusercontent.com"]

    # Stub Google's library call so the audience logic is what is under test.
    # The token string doubles as the audience the "token" claims.
    from google.oauth2 import id_token as gid

    def fake_verify(token, request, audience=None):
        return {"iss": "https://accounts.google.com", "aud": token,
                "sub": "s", "email": "a@b.c", "email_verified": True}
    monkeypatch.setattr(gid, "verify_oauth2_token", fake_verify)

    assert v.verify("web-client.apps.googleusercontent.com")["email"] == "a@b.c"
    assert v.verify("android-client.apps.googleusercontent.com")["sub"] == "s"
    import pytest as _p
    with _p.raises(ValueError, match="not one of this project"):
        v.verify("someone-elses-client.apps.googleusercontent.com")


def test_unverified_google_email_is_refused(monkeypatch):
    import auth as auth_mod
    monkeypatch.setenv("PHOTOBIND_GOOGLE_CLIENT_ID", "web.apps.googleusercontent.com")
    monkeypatch.delenv("PHOTOBIND_GOOGLE_ALLOWED_AUDIENCES", raising=False)
    v = auth_mod.GoogleVerifier()

    from google.oauth2 import id_token as gid

    def fake_verify(token, request, audience=None):
        return {"iss": "https://accounts.google.com",
                "aud": "web.apps.googleusercontent.com", "sub": "s",
                "email": "a@b.c", "email_verified": False}
    monkeypatch.setattr(gid, "verify_oauth2_token", fake_verify)
    import pytest as _p
    with _p.raises(ValueError, match="not verified"):
        v.verify("anything")
