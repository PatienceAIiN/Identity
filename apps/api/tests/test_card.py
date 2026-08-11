"""The public card a code can point at.

Covers the two things that matter about a page handed to strangers: it shows
only what the owner put there, and it disappears with the account.
"""
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
from authhelp import Outbox, register  # noqa: E402


@pytest.fixture()
def server(tmp_path, monkeypatch):
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/c.db")
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


def test_card_starts_with_only_a_name_and_publishes_what_you_add(server):
    base, outbox = server
    c = register(base, "card@example.org", outbox, "Ama Osei")

    card = c.get("/v1/me/card").json()
    assert card["display_name"] == "Ama Osei"
    # Nothing else is published until it is entered — in particular the account
    # address is not put on a public page just because we know it.
    assert card["email"] == "" and card["phone"] == "" and card["website"] == ""

    page = httpx.get(f"{base}/c/{card['card_id']}")
    assert page.status_code == 200
    assert "Ama Osei" in page.text
    assert "card@example.org" not in page.text
    # Handed to strangers, so it must not be indexable and must carry the strict
    # policy the resolution page uses.
    assert "noindex" in page.headers.get("x-robots-tag", "")
    assert "script-src 'self'" in page.headers.get("content-security-policy", "")

    c.put("/v1/me/card", json={"headline": "Cardiology", "email": "ama@clinic.org",
                               "website": "clinic.org"})
    page = httpx.get(f"{base}/c/{card['card_id']}")
    assert "Cardiology" in page.text and "ama@clinic.org" in page.text
    assert 'href="https://clinic.org"' in page.text     # scheme added for a bare host


def test_card_escapes_what_it_is_given(server):
    base, outbox = server
    c = register(base, "x@example.org", outbox, "X")
    card = c.get("/v1/me/card").json()
    c.put("/v1/me/card", json={"display_name": "<script>alert(1)</script>"})
    page = httpx.get(f"{base}/c/{card['card_id']}")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


def test_deleting_the_account_takes_the_card_with_it(server):
    base, outbox = server
    c = register(base, "gone@example.org", outbox, "Gone")
    card_id = c.get("/v1/me/card").json()["card_id"]
    assert httpx.get(f"{base}/c/{card_id}").status_code == 200

    r = c.request("DELETE", "/v1/me", json={"confirm": "DELETE"})
    assert r.status_code == 200
    assert httpx.get(f"{base}/c/{card_id}").status_code == 404


def test_unknown_card_is_404(server):
    base, _ = server
    assert httpx.get(f"{base}/c/nope123").status_code == 404
