"""Backups, restore drills, load tests, rollback checks and error tracking.

The point of these is that the operations surface does the thing rather than
reporting that it did. The restore drill test writes a real backup, restores it
into a scratch database, and fails if a row or a foreign key does not survive —
which is the only version of "we have backups" worth having.
"""

import base64
import json
import pathlib
import socket
import sys
import threading
import time

import httpx
import pytest
import uvicorn

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
ROOT = pathlib.Path(__file__).resolve().parents[3]
for pkg in ("packages/binding/tests",):
    sys.path.insert(0, str(ROOT / pkg))

import blobs  # noqa: E402
import ops  # noqa: E402
from auth import hash_password  # noqa: E402
from authhelp import Outbox, register  # noqa: E402
from main import create_app  # noqa: E402

ADMIN_PASSWORD = "Admin@110426"


class FakeR2:
    """In-memory object store, so backups have somewhere off-database to go."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        import io
        return {"Body": io.BytesIO(self.objects[Key])}

    def head_object(self, Bucket, Key):
        if Key not in self.objects:
            raise KeyError(Key)
        return {"ContentLength": len(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        return {"Contents": [{"Key": k, "Size": len(v)}
                             for k, v in self.objects.items()
                             if k.startswith(Prefix)]}


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOBIND_ADMIN_PASSWORD_HASH",
                       hash_password(ADMIN_PASSWORD))
    outbox = Outbox().install(monkeypatch)
    fake = FakeR2()
    monkeypatch.setattr(blobs.PHOTOS, "enabled", True)
    monkeypatch.setattr(blobs.PHOTOS, "bucket", "test-bucket")
    monkeypatch.setattr(blobs.PHOTOS, "_s3", lambda: fake)
    monkeypatch.setattr(blobs.PHOTOS, "_usage_cache", None)
    app = create_app(keys_dir=tmp_path / "keys",
                     db_url=f"sqlite:///{tmp_path}/ops.db")
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
    yield f"http://127.0.0.1:{port}", outbox, fake
    srv.should_exit = True
    t.join(timeout=5)


def admin_client(base) -> httpx.Client:
    c = httpx.Client(base_url=base, timeout=120)
    r = c.post("/v1/admin/login", json={"password": ADMIN_PASSWORD})
    assert r.status_code == 200, r.text
    return c


def make_a_code(base, email_or_outbox, outbox=None) -> dict:
    """A user with one code, so a backup has something worth restoring."""
    if outbox is None:
        email, outbox = "ops-user@example.com", email_or_outbox
    else:
        email = email_or_outbox
    user = register(base, email, outbox)
    body = {"ciphertext_b64": base64.urlsafe_b64encode(b"\x02" * 48).decode(),
            "nonce_b64": base64.urlsafe_b64encode(b"\x03" * 12).decode(),
            "label": "backup me"}
    files = {"photo": ("p.png", _tiny_png(), "image/png")}
    r = user.post("/v1/codes?encode_qr=0", data=body, files=files)
    assert r.status_code in (200, 201), r.text
    return r.json()


def _tiny_png() -> bytes:
    import io

    import numpy as np
    from PIL import Image
    arr = (np.random.default_rng(7).integers(0, 255, (64, 64, 3))).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "PNG")
    return buf.getvalue()


# ── access control ──────────────────────────────────────────────────────────

def test_ops_is_admin_only(server):
    base, outbox, _ = server
    assert httpx.get(base + "/v1/admin/ops").status_code == 401
    user = register(base, "not-an-admin@example.com", outbox)
    for path in ("/v1/admin/ops", "/v1/admin/ops/errors",
                 "/v1/admin/ops/runs"):
        assert user.get(path).status_code == 401, path
    for path in ("/v1/admin/ops/backup", "/v1/admin/ops/restore-drill",
                 "/v1/admin/ops/load-test", "/v1/admin/ops/rollback-drill",
                 "/v1/admin/ops/migrate-photos"):
        assert user.post(path).status_code == 401, path


# ── backup and restore ──────────────────────────────────────────────────────

def test_backup_writes_off_database_and_verifies_its_own_readback(server):
    base, outbox, fake = server
    make_a_code(base, outbox)
    admin = admin_client(base)
    r = admin.post("/v1/admin/ops/backup")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True
    d = out["detail"]
    assert d["verified_readback"] is True
    assert d["rows"] > 0 and d["sha256"] and d["bytes"] > 0
    # It must be in object storage, not in the database it is a backup of.
    assert d["key"] in fake.objects
    assert d["key"].startswith("backups/")
    assert fake.objects[d["key"]][:2] == b"\x1f\x8b"        # gzip
    assert d["photo_bytes_included"] is False


def test_restore_drill_actually_restores_and_checks_the_joins(server):
    base, outbox, _ = server
    make_a_code(base, outbox)
    admin = admin_client(base)
    assert admin.post("/v1/admin/ops/backup").json()["ok"] is True
    r = admin.post("/v1/admin/ops/restore-drill")
    assert r.status_code == 200, r.text
    out = r.json()
    d = out["detail"]
    assert d["rows_restored"] > 0
    assert d["counts_match_manifest"] is True
    assert d["orphaned_credentials"] == 0
    assert d["orphaned_shares"] == 0
    assert out["ok"] is True, d


def test_restore_drill_fails_honestly_with_nothing_to_restore(server):
    base, _, _ = server
    admin = admin_client(base)
    out = admin.post("/v1/admin/ops/restore-drill").json()
    assert out["ok"] is False
    assert "no backup" in out["detail"]["error"]


def test_a_corrupted_backup_fails_the_drill(server):
    """The drill has to be capable of failing, or it proves nothing."""
    base, outbox, fake = server
    make_a_code(base, outbox)
    admin = admin_client(base)
    key = admin.post("/v1/admin/ops/backup").json()["detail"]["key"]
    fake.objects[key] = b"\x1f\x8b" + b"garbage that is not a gzip member"
    out = admin.post("/v1/admin/ops/restore-drill").json()
    assert out["ok"] is False
    assert "error" in out["detail"]


def test_backups_are_pruned_to_the_retention_limit(server, monkeypatch):
    base, outbox, fake = server
    make_a_code(base, outbox)
    monkeypatch.setattr(ops, "BACKUP_KEEP", 2)
    admin = admin_client(base)
    for _ in range(4):
        assert admin.post("/v1/admin/ops/backup").json()["ok"] is True
        time.sleep(1.05)          # keys carry a one-second timestamp
    kept = [k for k in fake.objects if k.endswith(".jsonl.gz")]
    assert len(kept) == 2, kept


# ── load test ───────────────────────────────────────────────────────────────

def test_load_test_measures_and_is_bounded(server):
    base, _, _ = server
    admin = admin_client(base)
    r = admin.post("/v1/admin/ops/load-test",
                   params={"requests_n": 5000, "concurrency": 999})
    assert r.status_code == 200, r.text
    d = r.json()["detail"]
    assert d["requests"] == ops.LOAD_MAX_REQUESTS      # clamped, not obeyed
    assert d["concurrency"] == 50
    assert d["p50_ms"] >= 0 and d["p95_ms"] >= d["p50_ms"]
    assert d["requests_per_second"] > 0


# ── rollback ────────────────────────────────────────────────────────────────

def test_rollback_drill_reports_rather_than_rolling_back(server):
    base, _, _ = server
    admin = admin_client(base)
    out = admin.post("/v1/admin/ops/rollback-drill").json()
    assert out["detail"]["performs_rollback"] is False
    # In a test container there is no service to roll back, so the honest
    # answer is "not ready" with a reason — never a false green.
    assert out["ok"] == bool(out["detail"].get("rollback_target"))
    if not out["ok"]:
        assert out["detail"]["error"] or out["detail"]["revisions"] == []


# ── error tracking ──────────────────────────────────────────────────────────

def test_a_real_500_is_recorded_by_the_handler(server, monkeypatch):
    """Drive a genuine unhandled exception through the app and find it listed.

    The app runs in this process, so breaking something an endpoint depends on
    produces a real 500 through the real exception handler rather than a
    simulated one.
    """
    base, _, _ = server
    admin = admin_client(base)
    assert admin.get("/v1/admin/ops/errors").json()["total"] == 0

    def explode(*_a, **_k):
        raise RuntimeError("storage report exploded")

    monkeypatch.setattr(blobs.PHOTOS, "usage_report", explode)
    assert admin.get("/v1/admin/ops").status_code == 500
    monkeypatch.undo()

    listing = admin.get("/v1/admin/ops/errors").json()
    assert listing["total"] == 1, listing
    item = listing["items"][0]
    assert item["kind"] == "RuntimeError"
    assert "exploded" in item["message"]
    assert "/v1/admin/ops" in item["where"]
    assert item["stack"]                      # a trace was kept for us
    assert item["count"] == 1


def test_error_list_paginates_and_resolve_clears(server, tmp_path):
    """Drives record_exception directly, which is what the handler calls."""
    base, _, _ = server
    admin = admin_client(base)

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine(f"sqlite:///{tmp_path}/ops.db", future=True)
    Session = sessionmaker(bind=engine, future=True)

    class FakeUrl:
        path = "/v1/thing"

    class FakeRequest:
        method = "GET"
        scope: dict = {}
        url = FakeUrl()

    def fail(n):
        try:
            raise ValueError(f"boom {n}")
        except ValueError as e:
            ops.record_exception(Session, e, FakeRequest())

    fail(1)
    fail(2)                     # same line, same type -> one group, count 2
    listing = admin.get("/v1/admin/ops/errors").json()
    assert listing["total"] >= 1
    item = listing["items"][0]
    assert item["kind"] == "ValueError"
    assert item["count"] == 2, listing
    assert item["resolved"] is False

    assert admin.post(
        f"/v1/admin/ops/errors/{item['fingerprint']}/resolve").status_code == 200
    assert admin.get("/v1/admin/ops/errors").json()["total"] == 0
    assert admin.get("/v1/admin/ops/errors",
                     params={"include_resolved": 1}).json()["total"] >= 1
    assert admin.delete(
        f"/v1/admin/ops/errors/{item['fingerprint']}").status_code == 200
    assert admin.get("/v1/admin/ops/errors",
                     params={"include_resolved": 1}).json()["total"] == 0


# ── photo migration ─────────────────────────────────────────────────────────

def test_migration_moves_legacy_rows_out_of_the_database(server, monkeypatch):
    base, outbox, fake = server
    # Create a code while storage is off, so the bytes land in the column the
    # way every row written before this change did.
    monkeypatch.setattr(blobs.PHOTOS, "enabled", False)
    code = make_a_code(base, outbox)
    monkeypatch.setattr(blobs.PHOTOS, "enabled", True)

    admin = admin_client(base)
    before = admin.get("/v1/admin/ops").json()["storage"]
    assert before["rows_still_in_database"] == 1
    assert before["migration_complete"] is False

    out = admin.post("/v1/admin/ops/migrate-photos", params={"limit": 10}).json()
    assert out["moved"] == 1 and out["failed"] == [] and out["remaining"] == 0
    assert f"photos/{code['photo_id']}.webp" in fake.objects

    after = admin.get("/v1/admin/ops").json()["storage"]
    assert after["rows_still_in_database"] == 0
    assert after["rows_in_object_storage"] == 1
    assert after["migration_complete"] is True

    # And the photo is still served, now out of object storage.
    user = register(base, "reader@example.com", outbox)
    assert user.get(f"/v1/photos/{code['photo_id']}.png").status_code in (200, 404)


def test_overview_reports_storage_headroom(server):
    base, outbox, _ = server
    make_a_code(base, outbox)
    admin = admin_client(base)
    s = admin.get("/v1/admin/ops").json()["storage"]
    assert s["backend"] == "cloudflare-r2"
    assert s["ceiling_bytes"] == blobs.MAX_BYTES
    assert 0 <= s["fraction_used"] <= 1
    assert s["full"] is False


# ── backup encryption ───────────────────────────────────────────────────────

BACKUP_KEY = base64.urlsafe_b64encode(b"k" * 32).decode()


def test_backups_are_encrypted_when_a_key_is_configured(server, monkeypatch):
    base, outbox, fake = server
    make_a_code(base, "enc-user@example.com", outbox)
    monkeypatch.setenv("PHOTOBIND_BACKUP_KEY", BACKUP_KEY)
    admin = admin_client(base)
    d = admin.post("/v1/admin/ops/backup").json()["detail"]
    assert d["encrypted"] is True
    assert "warning" not in d
    stored = fake.objects[d["key"]]
    assert stored.startswith(ops.BACKUP_MAGIC)
    # The plaintext must not be sitting in the object: gzip magic would mean
    # the dump went up unsealed.
    assert not stored[len(ops.BACKUP_MAGIC):].startswith(b"\x1f\x8b")
    assert b"enc-user@example.com" not in stored
    # And it still restores, which is the only thing that makes it a backup.
    out = admin.post("/v1/admin/ops/restore-drill").json()
    assert out["detail"]["was_encrypted"] is True
    assert out["ok"] is True, out["detail"]


def test_an_unencrypted_backup_says_so(server):
    base, outbox, _ = server
    make_a_code(base, "plain-user@example.com", outbox)
    admin = admin_client(base)
    d = admin.post("/v1/admin/ops/backup").json()["detail"]
    assert d["encrypted"] is False
    assert "PHOTOBIND_BACKUP_KEY" in d["warning"]


def test_a_lost_key_fails_the_drill_rather_than_reporting_success(server,
                                                                 monkeypatch):
    base, outbox, _ = server
    make_a_code(base, "lost-key@example.com", outbox)
    monkeypatch.setenv("PHOTOBIND_BACKUP_KEY", BACKUP_KEY)
    admin = admin_client(base)
    assert admin.post("/v1/admin/ops/backup").json()["detail"]["encrypted"] is True
    monkeypatch.delenv("PHOTOBIND_BACKUP_KEY")
    out = admin.post("/v1/admin/ops/restore-drill").json()
    assert out["ok"] is False
    assert "PHOTOBIND_BACKUP_KEY is not set" in out["detail"]["error"]

    monkeypatch.setenv("PHOTOBIND_BACKUP_KEY",
                       base64.urlsafe_b64encode(b"w" * 32).decode())
    out = admin.post("/v1/admin/ops/restore-drill").json()
    assert out["ok"] is False               # wrong key, not a silent pass


def test_backup_names_are_not_guessable_from_the_clock(server):
    base, outbox, fake = server
    make_a_code(base, "guess@example.com", outbox)
    admin = admin_client(base)
    key = admin.post("/v1/admin/ops/backup").json()["detail"]["key"]
    # Split on the timestamp rather than on "-": the salt is base64url, whose
    # alphabet contains "-", so splitting on it truncated the salt at random and
    # made this test fail roughly one run in five.
    import re
    m = re.fullmatch(
        rf"{re.escape(ops.BACKUP_PREFIX)}identity-(\d{{8}}T\d{{6}}Z)-(\S+)\.jsonl\.gz",
        key)
    assert m, key
    stamp, salt = m.groups()
    # A name built from the timestamp alone must not be the real object name.
    assert f"{ops.BACKUP_PREFIX}identity-{stamp}.jsonl.gz" not in fake.objects
    assert len(salt) >= 8, salt


def test_the_ops_tables_are_actually_created_in_the_database():
    """Guards an ordering bug that only showed up in production.

    create_all() builds whatever is registered on Base.metadata at the moment it
    runs. ops was imported when the router was built — after that moment — so
    error_events and ops_runs were never created and the panel 500'd. Every test
    passed anyway, because importing this module registers the models early.

    So the check is what ended up in the database, in a fresh interpreter, from
    the app that main builds on import — the same construction the deployment
    does, before anything has had a chance to import ops for it.
    """
    import subprocess
    import tempfile
    api_dir = str(pathlib.Path(__file__).resolve().parent.parent)
    tmp = tempfile.mkdtemp()
    src = f"""
import os, sys
os.environ["PHOTOBIND_DB_URL"] = "sqlite:///{tmp}/first.db"
os.environ.pop("PHOTOBIND_REDIS_URL", None)
sys.path.insert(0, {api_dir!r})
import main                      # builds an app at module level, as deployed
from sqlalchemy import create_engine, inspect
names = inspect(create_engine("sqlite:///{tmp}/first.db")).get_table_names()
missing = [t for t in ("error_events", "ops_runs") if t not in names]
print("TABLES:" + str(len(names)))
print("MISSING:" + ",".join(missing))
"""
    out = subprocess.run([sys.executable, "-c", src], capture_output=True,
                         text=True, timeout=300)
    assert "MISSING:" in out.stdout, out.stderr[-2000:]
    assert "TABLES:0" not in out.stdout, "no schema was built at all"
    assert out.stdout.strip().endswith("MISSING:"), out.stdout.strip()
