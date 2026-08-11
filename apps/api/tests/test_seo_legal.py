"""SEO/indexing surface and legal pages.

The load-bearing assertion here is the negative one: resolution links carry a
secret in their fragment, so they must never be indexable or cacheable.
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


@pytest.fixture()
def server(tmp_path):
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/s.db")
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


def test_robots_and_sitemap(server):
    r = httpx.get(server + "/robots.txt")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    assert "Disallow: /r/" in r.text          # never crawl per-share links
    assert "Sitemap: https://identity.patienceai.in/sitemap.xml" in r.text

    s = httpx.get(server + "/sitemap.xml")
    assert s.status_code == 200 and "xml" in s.headers["content-type"]
    assert "https://identity.patienceai.in/" in s.text
    assert "/r/" not in s.text                # no share links in the sitemap


def test_resolution_links_are_never_indexable(server):
    r = httpx.get(server + "/r/somecode", headers={"Accept": "text/html"})
    assert "noindex" in r.headers.get("x-robots-tag", "")
    assert "no-store" in r.headers.get("cache-control", "")
    assert r.headers.get("referrer-policy") == "no-referrer"


def test_landing_seo_tags(server):
    html = httpx.get(server + "/").text
    assert '<meta name="description"' in html
    assert '<link rel="canonical" href="https://identity.patienceai.in/"' in html
    assert 'property="og:title"' in html and 'name="twitter:card"' in html
    assert 'application/ld+json' in html
    assert '"@type":"Organization"' in html and "Patience AI" in html


def test_private_app_pages_are_noindex(server):
    for page in ("auth.html", "new.html", "codes.html", "scan.html"):
        html = httpx.get(f"{server}/app/{page}").text
        assert '<meta name="robots" content="noindex' in html, page


def test_legal_pages_indexable_and_complete(server):
    for page, must in [("terms.html", "Scanning is not proof of identity"),
                       ("privacy.html", "We never store face data")]:
        r = httpx.get(f"{server}/app/{page}")
        assert r.status_code == 200
        assert '<meta name="robots" content="index,follow">' in r.text
        assert "Patience AI" in r.text
    legal = httpx.get(server + "/static/legal.js").text
    # Claims must stay honest: no compliance assertion, limitations present.
    assert "not by itself a determination of compliance" in legal
    assert "It does not catch everything" in legal

    # The footer lives in chrome.js: attribution as plain text, a changelog
    # link, a theme toggle, and no outbound patienceai.in link.
    chrome = httpx.get(server + "/static/chrome.js").text
    assert "A product of Patience AI" in chrome
    assert 'href="https://patienceai.in"' not in chrome
    assert "changelog.html" in chrome
    assert "themeToggleButton" in chrome
    # Feedback is not in the footer — it is for signed-in people only.
    assert "pb-fb-link" not in chrome
    feedback = httpx.get(server + "/static/feedback.js").text
    assert "pb-fb-link" not in feedback


def test_changelog_is_indexable_and_free_of_internals(server):
    """The changelog is public: it must describe what people notice and never
    how the system works inside."""
    html = httpx.get(server + "/app/changelog.html")
    assert html.status_code == 200
    assert '<meta name="robots" content="index,follow">' in html.text
    js = httpx.get(server + "/static/changelog.js").text
    # No internals, no vendors, no architecture in public copy.
    for leak in ("Postgres", "postgres", "Cloud Run", "R2", "Brevo", "SQLite",
                 "AES-256-GCM", "pHash", "Ed25519", "SSE", "uvicorn",
                 "FastAPI", "opaque_id", "zxing", "OpenCV"):
        assert leak not in js, f"changelog leaks an internal: {leak}"


def test_private_nav_is_hidden_before_a_session_exists(server):
    chrome = httpx.get(server + "/static/chrome.js").text
    # Hidden first, revealed only after /v1/me confirms.
    assert "hidePrivateNav()" in chrome
    assert chrome.index("hidePrivateNav()") < chrome.index("async function refreshSession")


def test_favicon(server):
    r = httpx.get(server + "/favicon.svg")
    assert r.status_code == 200 and "svg" in r.headers["content-type"]


def test_gsc_verification_requires_configured_token(server, monkeypatch):
    # Unconfigured: no arbitrary html file is served.
    assert httpx.get(server + "/google123abc.html").status_code == 404


def test_consent_choice_is_enforced_not_just_recorded(server):
    """Refusing measurement must actually stop the third-party script.

    Cloudflare injects its beacon at the edge, so our HTML never contains it and
    client-side code cannot reliably block it. The lever we do have is a policy
    header, so the test is that the header changes with the cookie.
    """
    for path in ("/", "/app/new.html", "/dev", "/admin"):
        no_consent = httpx.get(server + path)
        csp = no_consent.headers.get("content-security-policy", "")
        assert csp, f"{path} sent no policy without consent"
        assert "cloudflareinsights" not in csp, f"{path} allowed the beacon host"
        # Sign-in must keep working under it, or the control just gets disabled.
        assert "https://accounts.google.com" in csp
        assert "'unsafe-inline'" in csp        # our own inline page scripts
        # Google's button fetches its own stylesheet; blocking it renders the
        # sign-in control unstyled, which is how a consent control ends up
        # switched off.
        assert "style-src" in csp and "accounts.google.com" in csp.split("style-src")[1].split(";")[0]

        accepted = httpx.get(server + path, cookies={"pb_consent": "all"})
        assert not accepted.headers.get("content-security-policy"), (
            f"{path} still restricted after consent was given")


def test_the_resolution_page_keeps_its_strict_policy_regardless(server):
    """The key-handling page is not part of the consent bargain: it is strict
    either way, and nothing third-party is ever allowed on it."""
    for cookies in ({}, {"pb_consent": "all"}):
        r = httpx.get(server + "/r/anything", cookies=cookies,
                      headers={"Accept": "text/html"})
        csp = r.headers.get("content-security-policy", "")
        assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
