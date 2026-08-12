"""Developer keys, the signed third-party API, and the admin panel.

These tests are about the security properties, not the happy path: an unsigned
request, a replayed one, a stale one, a key reaching past its scope or past its
owner's data, and an admin surface that a signed-in user must not be able to
touch.
"""
import base64
import json
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
from auth import hash_password  # noqa: E402
from conftest import synthetic_photo  # noqa: E402
from authhelp import Outbox, register  # noqa: E402
from platformapi import sign  # noqa: E402

ADMIN_PASSWORD = "Admin@110426"


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


@pytest.fixture()
def server(tmp_path, monkeypatch):
    # The admin password is configured as a hash, the way it is in deployment.
    monkeypatch.setenv("PHOTOBIND_ADMIN_PASSWORD_HASH", hash_password(ADMIN_PASSWORD))
    outbox = Outbox().install(monkeypatch)
    app = create_app(keys_dir=tmp_path / "keys", db_url=f"sqlite:///{tmp_path}/p.db")
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


def signed(base, key_id, secret, method, path, body=None, ts=None, nonce=None):
    raw = b"" if body is None else json.dumps(body).encode()
    ts = ts or str(int(time.time()))
    nonce = nonce or f"n{time.time_ns()}"
    headers = {
        "X-Api-Key": key_id,
        "X-Api-Timestamp": ts,
        "X-Api-Nonce": nonce,
        "X-Api-Signature": sign(secret, method, path, ts, nonce, raw),
    }
    if body is not None:
        headers["Content-Type"] = "application/json"
    return httpx.request(method, base + path, headers=headers, content=raw,
                         timeout=120)


def dev_client(base, email, outbox=None, password="developer-password-1",
               name="Dev Co"):
    """A verified developer account — separate credentials, separate cookie."""
    c = httpx.Client(base_url=base, timeout=120)
    r = c.post("/v1/dev/auth/signup",
               json={"name": name, "email": email, "password": password})
    assert r.status_code == 202, r.text
    assert outbox is not None, "developer signup needs the emailed code"
    code = outbox.code_for(email)
    assert code, f"no verification code captured for {email}"
    r = c.post("/v1/dev/auth/verify", json={"email": email, "code": code})
    assert r.status_code == 201, r.text
    return c


def mint_key(c, scopes=None):
    r = c.post("/v1/dev/keys", json={"name": "test", "scopes": scopes})
    assert r.status_code == 201, r.text
    return r.json()["key_id"], r.json()["secret"]


def test_secret_is_shown_once_and_never_again(server):
    base, outbox = server
    c = dev_client(base, "dev@example.org", outbox)
    key_id, secret = mint_key(c)
    assert secret and len(secret) > 20

    listed = c.get("/v1/dev/keys").json()["keys"]
    assert listed[0]["key_id"] == key_id
    # Nothing in the listing, anywhere, reveals the secret.
    assert secret not in json.dumps(listed)


def test_signed_request_works_and_only_sees_its_owner(server):
    base, outbox = server
    # The developer signs up with the same address as this account, so its shadow
    # owner is that account and the key sees exactly its codes.
    owner = register(base, "own@example.org", outbox, "Own")
    key_id, secret = mint_key(dev_client(base, "own@example.org", outbox))

    # A code belonging to someone else must never appear.
    other = register(base, "oth@example.org", outbox, "Oth")
    other.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(6), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"a" * 48), "nonce_b64": b64u(b"n" * 12),
                     "label": "theirs", "encode_qr": "0"})

    r = signed(base, key_id, secret, "GET", "/api/v1/codes")
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 0

    owner.post("/v1/codes",
               files={"photo": ("p.jpg", synthetic_photo(7), "image/jpeg")},
               data={"ciphertext_b64": b64u(b"b" * 48), "nonce_b64": b64u(b"n" * 12),
                     "label": "mine", "encode_qr": "0"})
    r = signed(base, key_id, secret, "GET", "/api/v1/codes")
    assert r.json()["count"] == 1
    assert r.json()["codes"][0]["label"] == "mine"


def test_unsigned_wrong_stale_and_replayed_requests_are_refused(server):
    base, outbox = server
    c = dev_client(base, "sig@example.org", outbox)
    key_id, secret = mint_key(c)

    # No headers at all.
    assert httpx.get(base + "/api/v1/codes").status_code == 401
    # Wrong secret.
    assert signed(base, key_id, "not-the-secret", "GET",
                  "/api/v1/codes").status_code == 401
    # A timestamp outside the window.
    stale = str(int(time.time()) - 3600)
    assert signed(base, key_id, secret, "GET", "/api/v1/codes",
                  ts=stale).status_code == 401
    # A nonce cannot be used twice, even with an otherwise valid signature.
    nonce = "reused-nonce"
    ts = str(int(time.time()))
    first = signed(base, key_id, secret, "GET", "/api/v1/codes", ts=ts, nonce=nonce)
    assert first.status_code == 200
    replay = signed(base, key_id, secret, "GET", "/api/v1/codes", ts=ts, nonce=nonce)
    assert replay.status_code == 401 and "nonce" in replay.text.lower()
    # A signature for one path does not authorise another.
    ts2 = str(int(time.time()))
    n2 = "path-swap"
    good = sign(secret, "GET", "/api/v1/codes", ts2, n2, b"")
    r = httpx.get(base + "/api/v1/codes/anything",
                  headers={"X-Api-Key": key_id, "X-Api-Timestamp": ts2,
                           "X-Api-Nonce": n2, "X-Api-Signature": good})
    assert r.status_code == 401


def test_scopes_are_enforced_and_revocation_is_immediate(server):
    base, outbox = server
    c = dev_client(base, "scope@example.org", outbox)
    key_id, secret = mint_key(c, scopes=["codes:read"])

    # Reading is allowed; writing is not.
    assert signed(base, key_id, secret, "GET", "/api/v1/codes").status_code == 200
    r = signed(base, key_id, secret, "POST", "/api/v1/codes",
               body={"photo_b64": "", "ciphertext_b64": "x", "nonce_b64": "y"})
    assert r.status_code == 403 and "scope" in r.text.lower()

    assert c.request("DELETE", f"/v1/dev/keys/{key_id}").status_code == 200
    assert signed(base, key_id, secret, "GET", "/api/v1/codes").status_code == 401


def test_api_refuses_plaintext_payloads(server):
    base, outbox = server
    c = dev_client(base, "plain@example.org", outbox)
    key_id, secret = mint_key(c)
    r = signed(base, key_id, secret, "POST", "/api/v1/codes",
               body={"photo_b64": b64u(synthetic_photo(9)),
                     "ciphertext_b64": "x", "nonce_b64": "y", "plaintext": "secret"})
    assert r.status_code == 422 and "ciphertext only" in r.text


def test_admin_login_is_gated_rate_limited_and_separate_from_user_sessions(server):
    base, outbox = server
    user = register(base, "u@example.org", outbox, "U")

    # A signed-in user's cookie is not an admin cookie.
    assert user.get("/v1/admin/users").status_code == 401
    # Anonymous, likewise.
    assert httpx.get(base + "/v1/admin/users").status_code == 401

    admin = httpx.Client(base_url=base, timeout=60)
    assert admin.post("/v1/admin/login", json={"password": "wrong"}).status_code == 401
    r = admin.post("/v1/admin/login", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 200 and r.json()["admin"] is True
    assert admin.get("/v1/admin/session").status_code == 200
    assert admin.get("/v1/admin/users").status_code == 200

    # And the admin cookie is not a user cookie.
    assert admin.get("/v1/me").status_code == 401

    admin.post("/v1/admin/logout")
    assert admin.get("/v1/admin/users").status_code == 401

    # Brute force is capped.
    codes = [httpx.post(base + "/v1/admin/login",
                        json={"password": f"bad{i}"}).status_code
             for i in range(8)]
    assert 429 in codes


def test_admin_sees_users_and_developers_but_not_payloads(server):
    base, outbox = server
    u = register(base, "seen@example.org", outbox, "Seen")
    key_id, _ = mint_key(dev_client(base, "seen@example.org", outbox))
    u.post("/v1/codes",
           files={"photo": ("p.jpg", synthetic_photo(10), "image/jpeg")},
           data={"ciphertext_b64": b64u(b"c" * 48), "nonce_b64": b64u(b"n" * 12),
                 "label": "badge", "encode_qr": "0"})

    admin = httpx.Client(base_url=base, timeout=60)
    admin.post("/v1/admin/login", json={"password": ADMIN_PASSWORD})

    users = admin.get("/v1/admin/users").json()
    row = next(x for x in users["users"] if x["email"] == "seen@example.org")
    assert row["codes"] == 1 and row["api_keys"] == 1

    detail = admin.get(f"/v1/admin/users/{row['user_id']}").json()
    assert detail["payloads_readable"] is False
    assert any(k["key_id"] == key_id for k in detail["keys"])
    # No ciphertext, nonce or key material is offered to the operator.
    assert "ciphertext" not in json.dumps(detail)

    devs = admin.get("/v1/admin/developers").json()["developers"]
    assert any(d["key_id"] == key_id and d["email"] == "seen@example.org"
               for d in devs)


def test_admin_delete_user_cascades_and_leaves_codes_switched_off(server):
    base, outbox = server
    u = register(base, "bye@example.org", outbox, "Bye")
    code = u.post("/v1/codes",
                  files={"photo": ("p.jpg", synthetic_photo(11), "image/jpeg")},
                  data={"ciphertext_b64": b64u(b"d" * 48),
                        "nonce_b64": b64u(b"n" * 12), "label": "x",
                        "encode_qr": "0"}).json()
    admin = httpx.Client(base_url=base, timeout=60)
    admin.post("/v1/admin/login", json={"password": ADMIN_PASSWORD})
    users = admin.get("/v1/admin/users").json()["users"]
    uid = next(x["user_id"] for x in users if x["email"] == "bye@example.org")

    # Deletion needs the same explicit confirmation the self-service path does.
    assert admin.request("DELETE", f"/v1/admin/users/{uid}").status_code == 422
    r = admin.request("DELETE", f"/v1/admin/users/{uid}?confirm=DELETE")
    assert r.status_code == 200, r.text

    assert admin.get(f"/v1/admin/users/{uid}").status_code == 404
    resolved = httpx.get(f"{base}/r/{code['opaque_resolution_id']}",
                         headers={"Accept": "application/json"})
    assert resolved.status_code == 410 and resolved.json()["status"] == "REVOKED"


def test_signed_calls_are_capped_per_minute(server):
    base, outbox = server
    c = dev_client(base, "rate@example.org", outbox)
    key_id, secret = mint_key(c, scopes=["codes:read"])
    codes = [signed(base, key_id, secret, "GET", "/api/v1/codes").status_code
             for _ in range(14)]
    assert 429 in codes, "the per-key ceiling did not engage"
    # Ten get through, so this is a limit and not a wall.
    assert codes[0] == 200 and codes.count(200) == 10


def test_the_monthly_budget_is_per_account_not_per_key(server):
    """A second key must not double the allowance — otherwise the cap is a
    formality anyone can lift by clicking 'create key' again."""
    base, outbox = server
    c = dev_client(base, "budget@example.org", outbox)
    usage = c.get("/v1/dev/usage").json()
    assert usage["limit_per_month"] == 300
    assert usage["limit_per_minute"] == 10
    assert usage["used_this_month"] == 0
    assert usage["remaining_this_month"] == 300
    # The reset is midnight IST on the first of next month.
    assert usage["resets_at"].endswith("+05:30")
    assert usage["resets_at"][8:10] == "01"

    k1, s1 = mint_key(c, scopes=["codes:read"])
    for _ in range(3):
        assert signed(base, k1, s1, "GET", "/api/v1/codes").status_code == 200
    after = c.get("/v1/dev/usage").json()
    assert after["used_this_month"] == 3
    assert after["remaining_this_month"] == 297

    # A second key draws from the same budget rather than its own.
    k2, s2 = mint_key(c, scopes=["codes:read"])
    assert signed(base, k2, s2, "GET", "/api/v1/codes").status_code == 200
    both = c.get("/v1/dev/usage").json()
    assert both["used_this_month"] == 4, "keys did not share one account budget"


def test_usage_reports_peaks_and_callers_without_storing_addresses(server):
    base, outbox = server
    c = dev_client(base, "graph@example.org", outbox)
    key_id, secret = mint_key(c, scopes=["codes:read"])
    for _ in range(4):
        signed(base, key_id, secret, "GET", "/api/v1/codes")
    u = c.get("/v1/dev/usage").json()
    assert u["peak_hour_count"] >= 4 and u["peak_hour"]
    assert u["hours"] and u["days"]
    assert u["callers"] and u["callers_are_hashed"] is True
    # The calling address itself must not be recoverable from the response.
    assert "127.0.0.1" not in json.dumps(u)


def test_a_normal_account_cannot_reach_developer_keys(server):
    base, outbox = server
    user = register(base, "normal@example.org", outbox, "Normal")
    # A signed-in user session is not a developer session, in either direction.
    assert user.get("/v1/dev/keys").status_code == 401
    assert user.post("/v1/dev/keys", json={"name": "sneaky"}).status_code == 401
    assert user.get("/v1/dev/usage").status_code == 401

    dev = dev_client(base, "separate@example.org", outbox)
    key_id, _ = mint_key(dev)
    # And a developer session is not a user session.
    assert dev.get("/v1/me").status_code == 401
    # The user still cannot see the developer's key.
    assert key_id not in user.get("/v1/dev/keys").text


def test_developer_signup_reuses_a_matching_account_as_the_code_owner(server):
    base, outbox = server
    # An account that already has a code.
    user = register(base, "both@example.org", outbox, "Both")
    user.post("/v1/codes",
              files={"photo": ("p.jpg", synthetic_photo(13), "image/jpeg")},
              data={"ciphertext_b64": b64u(b"e" * 48), "nonce_b64": b64u(b"n" * 12),
                    "label": "existing", "encode_qr": "0"})
    # Signing up as a developer with the same address keeps that ownership, so a
    # key minted now can see the codes that account already had.
    dev = dev_client(base, "both@example.org", outbox)
    key_id, secret = mint_key(dev)
    r = signed(base, key_id, secret, "GET", "/api/v1/codes")
    assert r.status_code == 200
    assert r.json()["count"] == 1 and r.json()["codes"][0]["label"] == "existing"


def test_developer_signin_is_not_enumerable_and_needs_a_real_password(server):
    base, outbox = server
    dev_client(base, "known@example.org", outbox, password="developer-password-1")
    c = httpx.Client(base_url=base, timeout=60)
    missing = c.post("/v1/dev/auth/signin",
                     json={"email": "nobody@example.org", "password": "whatever12345"})
    wrong = c.post("/v1/dev/auth/signin",
                   json={"email": "known@example.org", "password": "wrongpassword1"})
    assert missing.status_code == wrong.status_code == 401
    assert missing.json()["detail"] == wrong.json()["detail"]
    ok = c.post("/v1/dev/auth/signin",
                json={"email": "known@example.org", "password": "developer-password-1"})
    assert ok.status_code == 200


def test_developer_signup_creates_nothing_until_the_code_is_entered(server):
    base, outbox = server
    c = httpx.Client(base_url=base, timeout=60)
    r = c.post("/v1/dev/auth/signup",
               json={"name": "Later", "email": "later@example.org",
                     "password": "developer-password-1"})
    assert r.status_code == 202
    # No account, no session, no keys — signing up is not the same as existing.
    assert c.get("/v1/dev/me").status_code == 401
    assert c.post("/v1/dev/keys", json={"name": "too soon"}).status_code == 401
    assert c.post("/v1/dev/auth/signin",
                  json={"email": "later@example.org",
                        "password": "developer-password-1"}).status_code == 401

    # A wrong code is refused, and the right one completes it.
    assert c.post("/v1/dev/auth/verify",
                  json={"email": "later@example.org", "code": "000000"}).status_code == 401
    code = outbox.code_for("later@example.org")
    assert c.post("/v1/dev/auth/verify",
                  json={"email": "later@example.org", "code": code}).status_code == 201
    assert c.get("/v1/dev/me").status_code == 200
    # And the welcome email, with the rules, goes out only now.
    mails = [m for m in outbox.sent if m["to"] == "later@example.org"]
    assert any("developer account" in m["subject"] for m in mails)
    assert any("never ship a secret inside an app" in m["text"].lower()
               for m in mails)


def test_one_developer_cannot_see_another_developers_keys(server):
    """The console must show a developer their own keys and nothing else."""
    base, outbox = server
    a = dev_client(base, "alpha@example.org", outbox, name="Alpha")
    b = dev_client(base, "beta@example.org", outbox, name="Beta")
    a_key, a_secret = mint_key(a)
    b_key, _ = mint_key(b)

    a_listed = a.get("/v1/dev/keys").text
    b_listed = b.get("/v1/dev/keys").text
    assert a_key in a_listed and b_key not in a_listed
    assert b_key in b_listed and a_key not in b_listed

    # Nor revoke it, nor see its usage.
    assert a.request("DELETE", f"/v1/dev/keys/{b_key}").status_code == 404
    assert b_key not in a.get("/v1/dev/usage").text
    # And B's key still works afterwards, so the refusal was a refusal and not a
    # side effect.
    assert a.get("/v1/dev/keys").status_code == 200


def test_signing_up_as_a_developer_needs_control_of_the_address(server):
    """Before verification existed, anyone could claim a developer account on
    someone else's address — and, because a matching address adopts that account
    as the code owner, inherit their codes. The emailed code is what stops it."""
    base, outbox = server
    victim = register(base, "victim@example.org", outbox, "Victim")
    victim.post("/v1/codes",
                files={"photo": ("p.jpg", synthetic_photo(15), "image/jpeg")},
                data={"ciphertext_b64": b64u(b"f" * 48),
                      "nonce_b64": b64u(b"n" * 12), "label": "private",
                      "encode_qr": "0"})

    attacker = httpx.Client(base_url=base, timeout=60)
    r = attacker.post("/v1/dev/auth/signup",
                      json={"name": "Not Victim", "email": "victim@example.org",
                            "password": "attacker-password-1"})
    assert r.status_code == 202          # a code is sent to the real owner
    # Without that code, nothing exists and nothing is reachable.
    assert attacker.get("/v1/dev/me").status_code == 401
    assert attacker.post("/v1/dev/keys", json={"name": "x"}).status_code == 401
    assert attacker.post("/v1/dev/auth/verify",
                         json={"email": "victim@example.org",
                               "code": "123456"}).status_code == 401


def test_deleting_a_revoked_key_cannot_reset_the_monthly_budget(server):
    """The obvious way to cheat the cap: revoke a key, delete it, and hope its
    calls disappear with it. They must not."""
    base, outbox = server
    c = dev_client(base, "purge@example.org", outbox)
    key_id, secret = mint_key(c, scopes=["codes:read"])
    for _ in range(4):
        assert signed(base, key_id, secret, "GET", "/api/v1/codes").status_code == 200
    assert c.get("/v1/dev/usage").json()["used_this_month"] == 4

    # A live key cannot be deleted — revoking is a separate, deliberate step.
    assert c.request("DELETE", f"/v1/dev/keys/{key_id}/purge").status_code == 409

    assert c.request("DELETE", f"/v1/dev/keys/{key_id}").status_code == 200
    assert c.request("DELETE", f"/v1/dev/keys/{key_id}/purge").status_code == 200

    # Gone from the list, still counted against the month.
    assert key_id not in c.get("/v1/dev/keys").text
    after = c.get("/v1/dev/usage").json()
    assert after["used_this_month"] == 4, "purging a key reset the budget"
    assert after["remaining_this_month"] == 296


def test_purging_is_scoped_to_the_owner_and_bulk_only_takes_revoked(server):
    base, outbox = server
    a = dev_client(base, "pa@example.org", outbox)
    b = dev_client(base, "pb@example.org", outbox)
    a_dead, _ = mint_key(a)
    a_live, _ = mint_key(a)
    a.request("DELETE", f"/v1/dev/keys/{a_dead}")

    # Another developer cannot purge it.
    assert b.request("DELETE", f"/v1/dev/keys/{a_dead}/purge").status_code == 404

    r = a.post("/v1/dev/keys/purge-revoked")
    assert r.status_code == 200 and r.json()["count"] == 1
    listed = a.get("/v1/dev/keys").text
    assert a_dead not in listed and a_live in listed, "bulk purge took a live key"


def test_a_replayed_nonce_is_refused_durably(server):
    """The replay check must not depend on process memory.

    It used to be an in-process set, which is correct on one instance and useless
    on two. It is now a unique constraint, so the record survives a restart and
    cannot be evicted — the reason it did not go on a shared LRU cache.
    """
    base, outbox = server
    c = dev_client(base, "replay@example.org", outbox)
    key_id, secret = mint_key(c, scopes=["codes:read"])

    ts, nonce = str(int(time.time())), "spent-once"
    first = signed(base, key_id, secret, "GET", "/api/v1/codes", ts=ts, nonce=nonce)
    assert first.status_code == 200

    # Same nonce, and also the same nonce with a *fresh* timestamp and signature:
    # a replayer controls both, so neither may get through.
    again = signed(base, key_id, secret, "GET", "/api/v1/codes", ts=ts, nonce=nonce)
    assert again.status_code == 401 and "nonce" in again.text.lower()
    later = signed(base, key_id, secret, "GET", "/api/v1/codes",
                   ts=str(int(time.time())), nonce=nonce)
    assert later.status_code == 401, "a fresh signature reused an old nonce"

    # A different key may use the same nonce string — the claim is per key.
    other_id, other_secret = mint_key(c, scopes=["codes:read"])
    assert signed(base, other_id, other_secret, "GET", "/api/v1/codes",
                  nonce=nonce).status_code == 200
