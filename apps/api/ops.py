"""Operations the admin panel can run, and the records they leave behind.

Five things live here, and each one exists because "we have it" was not the
same as "we have checked it":

  errors    — unhandled exceptions, grouped, so a 500 is something we know
              about rather than something a user has to report
  backups   — a dump of the database to object storage, hashed
  restore   — a real restore of the newest backup into a scratch database,
              with referential checks. A backup nobody has restored is a
              hope, not a backup
  load      — a bounded burst against the public host, measured
  rollback  — whether a previous revision exists to go back to, and the
              exact command to do it

Every run is recorded with its result and duration, so the admin panel shows
when each was last done rather than only offering a button. A drill that was
never run reads as "never", which is the honest answer and the useful one.

Deliberately not here: automatic scheduling. These are triggered, and the panel
shows the age of the last run, because a cron that silently stopped firing
looks exactly like a cron that is working.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import json
import logging
import os
import statistics
import subprocess
import time
import traceback
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import Integer, String, Text, DateTime, inspect, select
from sqlalchemy.orm import Mapped, mapped_column

from blobs import PHOTOS
from db import Base, utcnow

log = logging.getLogger("photobind.ops")

BACKUP_PREFIX = "backups/"
# Backup objects carry a random suffix as well as a timestamp. The timestamp
# alone would make every backup's name guessable, and a guessable name is one
# misconfiguration away from a downloadable copy of every account.
BACKUP_MAGIC = b"PBBK1"
BACKUP_KEEP = int(os.environ.get("PHOTOBIND_BACKUP_KEEP") or 14)
# A dump is held in memory before it is uploaded, so it is capped. Past this the
# backup is refused rather than half-written: a truncated dump that reports
# success is worse than no dump.
BACKUP_MAX_BYTES = 256 * 1024 * 1024
LOAD_MAX_REQUESTS = 500


class ErrorEvent(Base):
    """One group of identical failures, not one row per occurrence.

    Grouped by fingerprint so a loop that throws ten thousand times is one row
    with a count, and the table cannot be used to fill the disk.
    """

    __tablename__ = "error_events"
    fingerprint: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    where: Mapped[str] = mapped_column(String)          # method + route
    stack: Mapped[str] = mapped_column(Text)
    count: Mapped[int] = mapped_column(Integer, default=1)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class OpsRun(Base):
    """A backup, restore drill, load test or rollback check that happened."""

    __tablename__ = "ops_runs"
    run_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String, index=True)
    ok: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(Text, default="{}")   # JSON


def _run_id(kind: str) -> str:
    return f"{kind}_{base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip('=')}"


def _finish(db, kind: str, started: float, ok: bool, detail: dict) -> dict:
    row = OpsRun(run_id=_run_id(kind), kind=kind, ok=1 if ok else 0,
                 duration_ms=int((time.time() - started) * 1000),
                 detail=json.dumps(detail)[:20000])
    db.add(row)
    db.commit()
    return {"run_id": row.run_id, "kind": kind, "ok": ok,
            "duration_ms": row.duration_ms, "detail": detail,
            "at": row.started_at.isoformat() if row.started_at else None}


# ── error tracking ──────────────────────────────────────────────────────────

def record_exception(SessionLocal, exc: BaseException, request: Request) -> None:
    """Group and store an unhandled exception. Never raises.

    An error tracker that can itself throw turns one failure into two, so every
    path here swallows. The route pattern is stored rather than the raw path:
    /r/{id} groups, /r/AbCdEf does not, and the raw path can carry an opaque id
    we have no reason to keep.
    """
    try:
        route = (request.scope or {}).get("route")
        path = getattr(route, "path", None) or getattr(
            getattr(request, "url", None), "path", None) or "unknown"
        where = f"{getattr(request, 'method', '?')} {path}"
        stack = "".join(traceback.format_exception(type(exc), exc,
                                                  exc.__traceback__))[-6000:]
        # Fingerprint on the type, the location and the last frame — not the
        # message, which often carries an id and would defeat grouping.
        last_frame = ""
        tb = exc.__traceback__
        while tb is not None:
            last_frame = f"{tb.tb_frame.f_code.co_filename}:{tb.tb_lineno}"
            tb = tb.tb_next
        fp = hashlib.sha256(
            f"{type(exc).__name__}|{where}|{last_frame}".encode()).hexdigest()[:32]
        db = SessionLocal()
        try:
            row = db.get(ErrorEvent, fp)
            if row is None:
                db.add(ErrorEvent(fingerprint=fp, kind=type(exc).__name__,
                                  message=str(exc)[:2000], where=where,
                                  stack=stack))
            else:
                row.count += 1
                row.last_seen = utcnow()
                row.message = str(exc)[:2000]
                row.stack = stack
                row.resolved_at = None      # it is back
            db.commit()
        finally:
            db.close()
    except Exception:
        log.exception("error tracking failed, which is not worth a second 500")


# ── backup ──────────────────────────────────────────────────────────────────

def backup_key() -> bytes | None:
    """The key backups are encrypted with, or None if the deployment has none.

    A dump holds every email address, every password hash and every stored
    ciphertext, so it is encrypted before it leaves the process: a leaked bucket
    credential should not be a leaked database. Without a key configured the
    backup still runs and still works — and says, in the run detail and in the
    admin panel, that it is unencrypted. Refusing to back up at all would be the
    worse failure.

    Losing this key makes existing backups unrecoverable. It belongs in the same
    place as the deployment's other secrets, and it must not be rotated without
    keeping the old value for as long as backups encrypted with it are retained.
    """
    raw = (os.environ.get("PHOTOBIND_BACKUP_KEY") or "").strip()
    if not raw:
        return None
    try:
        key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
    except Exception:
        log.error("PHOTOBIND_BACKUP_KEY is not valid base64; backups will be "
                  "written unencrypted")
        return None
    if len(key) != 32:
        log.error("PHOTOBIND_BACKUP_KEY must decode to 32 bytes, got %d; "
                  "backups will be written unencrypted", len(key))
        return None
    return key


def seal(body: bytes) -> tuple[bytes, bool]:
    key = backup_key()
    if key is None:
        return body, False
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return BACKUP_MAGIC + nonce + AESGCM(key).encrypt(nonce, body, None), True


def unseal(stored: bytes) -> bytes:
    """Plaintext back, or an exception explaining which key is missing."""
    if not stored.startswith(BACKUP_MAGIC):
        return stored                       # written before encryption, or unset
    key = backup_key()
    if key is None:
        raise RuntimeError(
            "this backup is encrypted and PHOTOBIND_BACKUP_KEY is not set")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n = len(BACKUP_MAGIC)
    return AESGCM(key).decrypt(stored[n:n + 12], stored[n + 12:], None)


def _encode_value(v):
    if isinstance(v, datetime):
        return {"__dt__": v.isoformat()}
    if isinstance(v, bytes):
        return {"__b64__": base64.b64encode(v).decode()}
    return v


def _decode_value(v):
    if isinstance(v, dict) and "__dt__" in v:
        return datetime.fromisoformat(v["__dt__"])
    if isinstance(v, dict) and "__b64__" in v:
        return base64.b64decode(v["__b64__"])
    return v


def dump_database(engine) -> tuple[bytes, dict]:
    """Every table as gzipped JSON lines, plus a manifest of row counts.

    Photo bytes are excluded. They live in object storage now, so a dump that
    carried them would undo the point of moving them; the manifest records the
    object keys instead, and the restore drill checks a sample of them really
    exist. A legacy row that still holds bytes in the column is counted and
    reported, so "some photos are only in Postgres" cannot go unnoticed.
    """
    counts: dict[str, int] = {}
    legacy_photo_rows = 0
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        with engine.connect() as conn:
            for table in Base.metadata.sorted_tables:
                n = 0
                for row in conn.execute(select(table)).mappings():
                    d = dict(row)
                    if table.name == "photos":
                        if d.get("image_png"):
                            legacy_photo_rows += 1
                        d["image_png"] = None
                    gz.write((json.dumps(
                        {"t": table.name,
                         "r": {k: _encode_value(v) for k, v in d.items()}},
                        separators=(",", ":")) + "\n").encode())
                    n += 1
                    if buf.tell() > BACKUP_MAX_BYTES:
                        raise HTTPException(
                            507, "Database dump exceeded the size cap. Raise "
                                 "PHOTOBIND_BACKUP_MAX or dump out of band.")
                counts[table.name] = n
    manifest = {"created_at": utcnow().isoformat(), "counts": counts,
                "rows": sum(counts.values()),
                "legacy_photo_rows_in_database": legacy_photo_rows,
                "photo_bytes_included": False,
                "dialect": engine.dialect.name}
    return buf.getvalue(), manifest


def run_backup(db, engine) -> dict:
    started = time.time()
    try:
        body, manifest = dump_database(engine)
    except HTTPException as e:
        return _finish(db, "backup", started, False, {"error": e.detail})
    digest = hashlib.sha256(body).hexdigest()
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    salt = base64.urlsafe_b64encode(os.urandom(6)).decode().rstrip("=")
    key = f"{BACKUP_PREFIX}identity-{stamp}-{salt}.jsonl.gz"
    sealed, encrypted = seal(body)
    manifest["sha256"] = digest                 # of the plaintext dump
    manifest["bytes"] = len(body)
    manifest["stored_bytes"] = len(sealed)
    manifest["encrypted"] = encrypted
    manifest["key"] = key
    if not encrypted:
        manifest["warning"] = (
            "stored unencrypted: PHOTOBIND_BACKUP_KEY is not set, so this dump "
            "protects nothing beyond the bucket's own access control")
    stored = PHOTOS.raw_put(key, sealed, "application/octet-stream")
    if stored:
        PHOTOS.raw_put(key + ".manifest.json",
                       json.dumps(manifest, indent=2).encode(),
                       "application/json")
        # Read it back before calling it a backup. A write that returned 200 and
        # stored nothing readable is the failure mode worth catching here.
        # Read it back and unseal it. This checks the upload and the encryption
        # together: a backup that cannot be decrypted is not a backup, and the
        # only moment to find that out is now.
        back = PHOTOS.raw_get(key)
        try:
            verified = (back is not None
                        and hashlib.sha256(unseal(back)).hexdigest() == digest)
        except Exception as e:
            verified = False
            manifest["readback_error"] = f"{type(e).__name__}: {e}"
        manifest["verified_readback"] = verified
        pruned = prune_backups()
        manifest["pruned"] = pruned
        return _finish(db, "backup", started, verified, manifest)
    manifest["verified_readback"] = False
    manifest["error"] = ("object storage is not configured, so there is nowhere "
                        "off-database to put a backup")
    return _finish(db, "backup", started, False, manifest)


def list_backups(limit: int = 30) -> list[dict]:
    items = [o for o in PHOTOS.raw_list(BACKUP_PREFIX, limit=limit * 2)
             if o["key"].endswith(".jsonl.gz")]
    items.sort(key=lambda o: o["key"], reverse=True)
    return items[:limit]


def prune_backups(keep: int | None = None) -> list[str]:
    # Read the module constant at call time. As a default argument it was bound
    # at import, so changing it — in a test, or by reloading config — had no
    # effect and the retention limit silently stayed at whatever it started as.
    keep = BACKUP_KEEP if keep is None else keep
    dropped = []
    for old in list_backups(limit=200)[keep:]:
        if PHOTOS.delete(old["key"]):
            PHOTOS.delete(old["key"] + ".manifest.json")
            dropped.append(old["key"])
    return dropped


# ── restore drill ───────────────────────────────────────────────────────────

def run_restore_drill(db, tmp_dir: str) -> dict:
    """Restore the newest backup into a scratch database and check it.

    This is the only test here that can fail for an interesting reason. It
    rebuilds the schema from scratch, replays every row, and then asks whether
    the result is usable: do the foreign keys resolve, and do the photo objects
    the rows point at actually exist in the bucket.
    """
    started = time.time()
    newest = (list_backups(limit=1) or [None])[0]
    if newest is None:
        return _finish(db, "restore", started, False,
                       {"error": "no backup to restore"})
    body = PHOTOS.raw_get(newest["key"])
    if body is None:
        return _finish(db, "restore", started, False,
                       {"error": f"backup unreadable: {newest['key']}"})
    detail: dict = {"restored_from": newest["key"], "bytes": len(body)}
    detail["was_encrypted"] = body.startswith(BACKUP_MAGIC)
    try:
        body = unseal(body)
    except Exception as e:
        return _finish(db, "restore", started, False,
                       {**detail, "error": f"could not decrypt: {e}"})
    manifest_raw = PHOTOS.raw_get(newest["key"] + ".manifest.json")
    manifest = json.loads(manifest_raw) if manifest_raw else {}

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    scratch = os.path.join(tmp_dir, f"restore-{int(started)}.db")
    engine = create_engine(f"sqlite:///{scratch}", future=True)
    try:
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine, future=True)
        counts: dict[str, int] = {}
        tables = {t.name: t for t in Base.metadata.sorted_tables}
        with engine.begin() as conn:
            for line in gzip.decompress(body).splitlines():
                if not line.strip():
                    continue
                rec = json.loads(line)
                table = tables.get(rec["t"])
                if table is None:
                    continue            # a table the current schema dropped
                values = {k: _decode_value(v) for k, v in rec["r"].items()
                          if k in table.c}
                conn.execute(table.insert().values(**values))
                counts[rec["t"]] = counts.get(rec["t"], 0) + 1
        detail["rows_restored"] = sum(counts.values())
        detail["counts_match_manifest"] = (
            {k: v for k, v in counts.items() if v}
            == {k: v for k, v in (manifest.get("counts") or {}).items() if v})

        # Referential checks: a restore that loads rows but breaks the joins is
        # not a restore. These are the two that matter for resolving a scan.
        s = Session()
        try:
            broken_creds = s.execute(_orphan_sql(
                "credentials", "photo_id", "photos", "photo_id")).scalar() or 0
            broken_shares = s.execute(_orphan_sql(
                "shares", "credential_id", "credentials",
                "credential_id")).scalar() or 0
            detail["orphaned_credentials"] = int(broken_creds)
            detail["orphaned_shares"] = int(broken_shares)
            keys = [k for (k,) in s.execute(
                _photo_keys_sql()).all() if k][:10]
        finally:
            s.close()

        # And the half a restore cannot prove on its own: are the photos there.
        missing = [k for k in keys if not PHOTOS.exists(k)]
        detail["photo_objects_sampled"] = len(keys)
        detail["photo_objects_missing"] = missing
        ok = (detail["counts_match_manifest"] and not broken_creds
              and not broken_shares and not missing)
        detail["verdict"] = ("restored and consistent" if ok
                             else "restored with problems")
        return _finish(db, "restore", started, ok, detail)
    except Exception as e:
        detail["error"] = f"{type(e).__name__}: {e}"
        return _finish(db, "restore", started, False, detail)
    finally:
        engine.dispose()
        try:
            os.unlink(scratch)
        except OSError:
            pass


def _orphan_sql(child: str, fk: str, parent: str, pk: str):
    from sqlalchemy import text
    return text(f"SELECT COUNT(*) FROM {child} c LEFT JOIN {parent} p "
                f"ON c.{fk} = p.{pk} WHERE c.{fk} IS NOT NULL AND p.{pk} IS NULL")


def _photo_keys_sql():
    from sqlalchemy import text
    return text("SELECT object_key FROM photos WHERE object_key IS NOT NULL "
                "LIMIT 10")


# ── load test ───────────────────────────────────────────────────────────────

def run_load_test(db, base_url: str, requests_n: int, concurrency: int) -> dict:
    """A bounded burst at a cheap endpoint, measured.

    Points at /v1/health on purpose, twice over. The interesting endpoints
    either cost real CPU (encoding) or are rate limited by design (/r/), and a
    load test that trips our own enumeration guard measures the guard rather
    than the service. And /healthz is answered by Cloud Run's own frontend
    without reaching the container, so measuring it would measure Google.
    """
    started = time.time()
    requests_n = max(1, min(int(requests_n), LOAD_MAX_REQUESTS))
    concurrency = max(1, min(int(concurrency), 50))
    url = base_url.rstrip("/") + "/v1/health"
    latencies: list[float] = []
    codes: dict[str, int] = {}
    errors: list[str] = []

    import concurrent.futures
    import urllib.error
    import urllib.request

    # urllib rather than httpx: httpx is a test dependency and is not in the
    # deployment image. The first version of this imported it and failed in
    # production with ModuleNotFoundError — which the error tracker caught,
    # which is the only reason this comment exists.
    def one(_i):
        t0 = time.perf_counter()
        # Named agent: Cloudflare blocks the default Python-urllib string
        # outright, which showed up as 200 clean 403s and no measurement at all.
        req = urllib.request.Request(url, headers={
            "User-Agent": "Identity-ops-loadtest/1.0 "
                          "(+https://identity.patienceai.in)"})
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                r.read()
                code = str(r.status)
            return (time.perf_counter() - t0) * 1000, code, None
        except urllib.error.HTTPError as e:
            return (time.perf_counter() - t0) * 1000, str(e.code), None
        except Exception as e:
            return (time.perf_counter() - t0) * 1000, "error", f"{type(e).__name__}"

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as pool:
        for ms, code, err in pool.map(one, range(requests_n)):
            latencies.append(ms)
            codes[code] = codes.get(code, 0) + 1
            if err:
                errors.append(err)
    latencies.sort()

    def pct(p):
        if not latencies:
            return 0
        return round(latencies[min(len(latencies) - 1, int(len(latencies) * p))], 1)

    wall = time.time() - started
    detail = {"url": url, "requests": requests_n, "concurrency": concurrency,
              "p50_ms": pct(0.50), "p95_ms": pct(0.95), "p99_ms": pct(0.99),
              "max_ms": round(max(latencies), 1) if latencies else 0,
              "mean_ms": round(statistics.fmean(latencies), 1) if latencies else 0,
              "requests_per_second": round(requests_n / wall, 1) if wall else 0,
              "status_codes": codes, "errors": len(errors),
              "note": ("Measured from this server against the public host, so "
                       "the figures include Cloudflare and the network in "
                       "between. The generator shares the instance it is "
                       "testing: at concurrency 10 that cost 3 of 200 requests "
                       "a 15s timeout here, while the same burst from an "
                       "independent origin saw 200 of 200 succeed. Treat "
                       "timeouts in this run as contention until an outside "
                       "run agrees. One origin, one region: not a substitute "
                       "for a distributed load test.")}
    ok = codes.get("200", 0) == requests_n
    return _finish(db, "load", started, ok, detail)


# ── rollback drill ──────────────────────────────────────────────────────────

def _metadata(path: str) -> str | None:
    """Ask the instance metadata server. None anywhere that is not on GCP."""
    import urllib.request
    req = urllib.request.Request(
        "http://metadata.google.internal/computeMetadata/v1/" + path,
        headers={"Metadata-Flavor": "Google"})
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.read().decode()
    except Exception:
        return None


def _revisions_via_api(project: str, region: str, service: str) -> tuple[list[str], str | None]:
    """Revision names newest-first, from the Cloud Run Admin API.

    Uses the instance's own token from the metadata server and nothing but the
    standard library. The first version of this shelled out to gcloud, which is
    not in the container, so the drill could only ever report that it could not
    run — true, and useless.
    """
    import json as _json
    import urllib.request
    token_raw = _metadata(
        "instance/service-accounts/default/token")
    if not token_raw:
        return [], "not running on GCP, or the metadata server is unreachable"
    try:
        token = _json.loads(token_raw)["access_token"]
    except Exception:
        return [], "metadata server returned a token this could not parse"
    url = (f"https://run.googleapis.com/v2/projects/{project}/locations/"
           f"{region}/services/{service}/revisions?pageSize=20")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = _json.loads(r.read())
    except Exception as e:
        detail = ""
        if hasattr(e, "read"):
            try:
                detail = ": " + e.read().decode()[:200]
            except Exception:
                detail = ""
        return [], f"{type(e).__name__}{detail}"
    revs = body.get("revisions") or []
    # createTime descending, so the newest usable rollback target comes first.
    revs.sort(key=lambda r: r.get("createTime") or "", reverse=True)
    return [r["name"].rsplit("/", 1)[-1] for r in revs], None


def rollback_status() -> dict:
    """Is there a previous revision to go back to, and how would we do it.

    Read-only on purpose. Giving this service permission to shift its own
    traffic would mean an admin-panel compromise is a deploy compromise, which
    is a worse trade than typing one command. The drill proves a rollback target
    exists and hands over the exact command; it never performs the rollback.
    """
    service = os.environ.get("K_SERVICE") or "identity"
    region = (os.environ.get("PHOTOBIND_RUN_REGION")
              or (_metadata("instance/region") or "").rsplit("/", 1)[-1]
              or "us-central1")
    project = (os.environ.get("PHOTOBIND_PROJECT")
               or _metadata("project/project-id") or "")
    current = os.environ.get("K_REVISION") or ""
    out: dict = {"service": service, "region": region, "project": project,
                 "current_revision": current, "performs_rollback": False}

    revisions, error = _revisions_via_api(project, region, service) if project \
        else ([], "no project id available")
    if error and not revisions:
        # Local runs have gcloud and no metadata server; production is the
        # reverse. Try the other one before giving up.
        try:
            proc = subprocess.run(
                ["gcloud", "run", "revisions", "list", "--service", service,
                 "--region", region, "--format=value(metadata.name)",
                 "--limit=10"], capture_output=True, text=True, timeout=45)
            revisions = [r for r in proc.stdout.split() if r]
            if revisions:
                error = None
            elif proc.stderr.strip():
                error = f"{error}; gcloud: {proc.stderr.strip()[:200]}"
        except FileNotFoundError:
            pass
        except Exception as e:
            error = f"{error}; gcloud: {type(e).__name__}"

    out["revisions"] = revisions
    out["error"] = error
    target = next((r for r in revisions if r != current), None)
    out["rollback_target"] = target
    out["command"] = (
        f"gcloud run services update-traffic {service} --region {region} "
        f"--to-revisions {target}=100" if target else None)
    out["ready"] = bool(target)
    return out


def run_rollback_drill(db) -> dict:
    started = time.time()
    status = rollback_status()
    return _finish(db, "rollback", started, bool(status.get("ready")), status)


# ── photo migration ─────────────────────────────────────────────────────────

def migrate_photos(db, limit: int) -> dict:
    """Move photo bytes still sitting in the database into object storage.

    Runs in batches from the admin panel rather than at startup: a deploy that
    tries to move every photo before serving traffic is a deploy that times out.
    """
    from db import Photo
    if not PHOTOS.enabled:
        raise HTTPException(503, "Object storage is not configured.")
    rows = (db.query(Photo)
              .filter(Photo.object_key.is_(None), Photo.image_png.isnot(None))
              .limit(max(1, min(int(limit), 200))).all())
    moved, failed = 0, []
    for row in rows:
        try:
            key = PHOTOS.put(row.photo_id, row.image_png)
            if not key:
                continue
            row.object_key = key
            row.image_png = None            # the row stops carrying the bytes
            db.commit()
            moved += 1
        except Exception as e:
            db.rollback()
            failed.append({"photo_id": row.photo_id, "error": str(e)[:200]})
    remaining = (db.query(Photo)
                   .filter(Photo.object_key.is_(None),
                           Photo.image_png.isnot(None)).count())
    return {"moved": moved, "failed": failed, "remaining": remaining}


def photo_storage_report(db) -> dict:
    from db import Photo
    total = db.query(Photo).count()
    in_db = (db.query(Photo).filter(Photo.object_key.is_(None),
                                    Photo.image_png.isnot(None)).count())
    in_r2 = db.query(Photo).filter(Photo.object_key.isnot(None)).count()
    report = PHOTOS.usage_report()
    report.update({"photo_rows": total, "rows_in_object_storage": in_r2,
                   "rows_still_in_database": in_db,
                   "migration_complete": in_db == 0})
    return report


# ── router ──────────────────────────────────────────────────────────────────

def make_ops_router(SessionLocal, db_session, require_admin, engine,
                    public_host: str, tmp_dir: str = "/tmp") -> APIRouter:
    router = APIRouter()

    def last_runs(db) -> dict:
        out = {}
        for kind in ("backup", "restore", "load", "rollback"):
            row = (db.query(OpsRun).filter_by(kind=kind)
                     .order_by(OpsRun.started_at.desc()).first())
            out[kind] = None if row is None else {
                "run_id": row.run_id, "ok": bool(row.ok),
                "at": row.started_at.isoformat() if row.started_at else None,
                "age_hours": (round((utcnow() - row.started_at.replace(
                    tzinfo=timezone.utc)).total_seconds() / 3600, 1)
                    if row.started_at else None),
                "duration_ms": row.duration_ms,
                "detail": json.loads(row.detail or "{}")}
        return out

    @router.get("/v1/admin/ops")
    def ops_overview(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        open_errors = (db.query(ErrorEvent)
                         .filter(ErrorEvent.resolved_at.is_(None)).count())
        return {"storage": photo_storage_report(db),
                "backups": list_backups(limit=10),
                "last": last_runs(db),
                "open_errors": open_errors,
                "rollback": rollback_status()}

    @router.get("/v1/admin/ops/errors")
    def ops_errors(request: Request, db=Depends(db_session),
                   include_resolved: int = 0, page: int = 1,
                   per_page: int = 20):
        require_admin(request, db)
        per_page = max(1, min(per_page, 100))
        q = db.query(ErrorEvent)
        if not include_resolved:
            q = q.filter(ErrorEvent.resolved_at.is_(None))
        total = q.count()
        rows = (q.order_by(ErrorEvent.last_seen.desc())
                 .offset((max(1, page) - 1) * per_page).limit(per_page).all())
        return {"total": total, "page": page, "per_page": per_page,
                "items": [{"fingerprint": r.fingerprint, "kind": r.kind,
                           "message": r.message, "where": r.where,
                           "count": r.count, "stack": r.stack,
                           "first_seen": r.first_seen.isoformat() if r.first_seen else None,
                           "last_seen": r.last_seen.isoformat() if r.last_seen else None,
                           "resolved": r.resolved_at is not None}
                          for r in rows]}

    @router.post("/v1/admin/ops/errors/{fingerprint}/resolve")
    def ops_resolve(fingerprint: str, request: Request, db=Depends(db_session)):
        require_admin(request, db)
        row = db.get(ErrorEvent, fingerprint)
        if row is None:
            raise HTTPException(404, "unknown error")
        row.resolved_at = utcnow()
        db.commit()
        return {"fingerprint": fingerprint, "resolved": True}

    @router.delete("/v1/admin/ops/errors/{fingerprint}")
    def ops_delete_error(fingerprint: str, request: Request,
                         db=Depends(db_session)):
        require_admin(request, db)
        db.query(ErrorEvent).filter_by(fingerprint=fingerprint).delete()
        db.commit()
        return {"fingerprint": fingerprint, "deleted": True}

    @router.post("/v1/admin/ops/backup")
    def ops_backup(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        return run_backup(db, engine)

    @router.post("/v1/admin/ops/restore-drill")
    def ops_restore(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        return run_restore_drill(db, tmp_dir)

    @router.post("/v1/admin/ops/load-test")
    def ops_load(request: Request, db=Depends(db_session),
                 requests_n: int = 100, concurrency: int = 10):
        require_admin(request, db)
        return run_load_test(db, public_host, requests_n, concurrency)

    @router.post("/v1/admin/ops/rollback-drill")
    def ops_rollback(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        return run_rollback_drill(db)

    @router.post("/v1/admin/ops/migrate-photos")
    def ops_migrate(request: Request, db=Depends(db_session), limit: int = 50):
        require_admin(request, db)
        return migrate_photos(db, limit)

    @router.get("/v1/admin/ops/runs")
    def ops_runs(request: Request, db=Depends(db_session), kind: str = "",
                 page: int = 1, per_page: int = 20):
        require_admin(request, db)
        per_page = max(1, min(per_page, 100))
        q = db.query(OpsRun)
        if kind:
            q = q.filter_by(kind=kind)
        total = q.count()
        rows = (q.order_by(OpsRun.started_at.desc())
                 .offset((max(1, page) - 1) * per_page).limit(per_page).all())
        return {"total": total, "page": page, "per_page": per_page,
                "items": [{"run_id": r.run_id, "kind": r.kind, "ok": bool(r.ok),
                           "at": r.started_at.isoformat() if r.started_at else None,
                           "duration_ms": r.duration_ms,
                           "detail": json.loads(r.detail or "{}")}
                          for r in rows]}

    return router
