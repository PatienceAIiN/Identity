"""Sign-out must actually tear the session down.

The reported bug: after signing out, the browser's Back button still showed a
signed-in page. Two independent defences are asserted here — the server
revokes the session row (so a replayed cookie is dead) and every
authenticated surface is sent with no-store (so the browser cannot redisplay
a cached one). The bfcache case is covered by the browser-driven check in
scripts/, which cannot run inside pytest.
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
from main import COOKIE_NAME, create_app  # noqa: E402
from authhelp import Outbox, PASSWORD, register  # noqa: E402


@pytest.fixture()
def env(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "k", db_url=f"sqlite:///{tmp_path}/s.db")
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


def test_signout_revokes_the_session_server_side(env):
    c = register(env["base"], "out@example.org", env["outbox"])
    token = c.cookies.get(COOKIE_NAME)
    assert c.get("/v1/me").status_code == 200

    c.post("/v1/auth/signout")
    assert c.get("/v1/me").status_code == 401

    # A browser that kept the cookie cannot reuse it: the row is revoked.
    replay = httpx.Client(base_url=env["base"])
    replay.cookies.set(COOKIE_NAME, token)
    assert replay.get("/v1/me").status_code == 401
    assert replay.get("/v1/codes").status_code == 401


def test_authenticated_surfaces_are_never_cacheable(env):
    for path in ("/", "/app/codes.html", "/app/profile.html", "/app/new.html",
                 "/v1/app/latest"):
        cc = httpx.get(env["base"] + path).headers.get("cache-control", "")
        assert "no-store" in cc, f"{path} is cacheable: {cc!r}"


def test_private_pages_hide_themselves_until_auth_confirmed(env):
    """A signed-in page must not paint before /v1/me confirms the session —
    that is what let a bfcache restore show stale signed-in content.

    The generator is deliberately NOT in this list: it is reachable without an
    account so the free trial works, and it decides per-request what to show.
    """
    for page in ("codes.html", "scan.html", "profile.html"):
        html = httpx.get(f"{env['base']}/app/{page}").text
        assert 'data-requires-auth="1"' in html, page
        assert "visibility:hidden" in html, page

    # The generator is public, and must not be guarded — a visitor has to be
    # able to reach it.
    gen = httpx.get(f"{env['base']}/app/new.html").text
    assert 'data-requires-auth="1"' not in gen
    assert "trial.js" in gen
    app_js = httpx.get(env["base"] + "/static/app.js").text
    # Redirect must not leave a signed-in entry in history.
    assert "location.replace" in app_js
    # And the reveal has to be explicit, or the page stays blank.
    assert 'style.visibility = "visible"' in app_js
    assert "pageshow" in app_js


def test_signout_helper_lands_on_the_public_page(env):
    app_js = httpx.get(env["base"] + "/static/app.js").text
    assert "signOutAndLeave" in app_js
    assert 'location.replace("/")' in app_js


def test_destructive_actions_confirm_first(env):
    app_js = httpx.get(env["base"] + "/static/app.js").text
    assert "confirmAction" in app_js and "requireText" in app_js
    profile = httpx.get(env["base"] + "/static/profile.js").text
    assert "confirmAction" in profile              # sign out and delete
    assert 'requireText: "DELETE"' in profile
    assert "withSpinner" in profile                # busy state on the buttons
    codes = httpx.get(env["base"] + "/app/codes.html").text
    assert "confirmAction" in codes and "withSpinner" in codes
