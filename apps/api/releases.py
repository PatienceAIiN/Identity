"""App release channel: the update manifest, R2 storage, and pruning.

Flow (OTA):
1. You publish an APK: POST /v1/admin/releases with the file + version info.
   The APK goes to Cloudflare R2 under apks/identity-<versionName>.apk and a
   row lands in app_releases.
2. Phones poll GET /v1/app/latest. If versionCode is higher than theirs, the
   app downloads the APK in the background, checks its SHA-256, and installs.
3. After a successful install the app calls POST /v1/app/installed. Once a
   newer release has confirmed installs, superseded APKs are deleted from R2
   and their rows marked pruned.

Honest note on step 3, kept in the code because it is a real trade: pruning
the previous APK from R2 removes the ability to roll back to it. Devices that
have not updated yet are unaffected — they fetch the newest version, not the
old one — but if a release turns out to be broken there is no older artifact
to serve. PRUNE_KEEP controls how many superseded versions stay.

Without R2 credentials configured the endpoints still work: files are stored
in run/apks/ and served locally. That is DEV storage, not a CDN.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db import Base, utcnow

def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _normalize_endpoint(endpoint: str, bucket: str) -> str:
    """Strip a trailing /<bucket> (and any trailing slash) from an R2 endpoint."""
    e = endpoint.rstrip("/")
    if bucket and e.endswith("/" + bucket):
        e = e[: -(len(bucket) + 1)]
    return e


def prune_keep() -> int:
    """Superseded releases kept in storage — the rollback depth."""
    return int(_env("PHOTOBIND_PRUNE_KEEP", "1"))


LOCAL_APK_DIR = Path(__file__).resolve().parents[2] / "run" / "apks"


class AppRelease(Base):
    __tablename__ = "app_releases"
    version_code: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_name: Mapped[str] = mapped_column(String)
    min_sdk: Mapped[int] = mapped_column(Integer, default=26)
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String)
    object_key: Mapped[str] = mapped_column(String)
    notes: Mapped[str] = mapped_column(String, default="")
    mandatory: Mapped[bool] = mapped_column(Boolean, default=False)
    pruned: Mapped[bool] = mapped_column(Boolean, default=False)
    install_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# Human-readable Android version for a minSdk, for the download CTA note.
SDK_NAMES = {
    26: "Android 8.0 Oreo", 27: "Android 8.1", 28: "Android 9 Pie",
    29: "Android 10", 30: "Android 11", 31: "Android 12", 32: "Android 12L",
    33: "Android 13", 34: "Android 14", 35: "Android 15",
}


def android_label(min_sdk: int) -> str:
    return SDK_NAMES.get(min_sdk, f"API level {min_sdk}")


class Storage:
    """R2 when configured, local disk otherwise. Same interface either way."""

    def __init__(self):
        self.bucket = _env("PHOTOBIND_R2_BUCKET")
        # Cloudflare shows the endpoint with the bucket appended
        # (…r2.cloudflarestorage.com/identity), but the S3 API wants the
        # account endpoint alone — boto3 adds the bucket itself. Accept either
        # form rather than failing with an opaque SignatureDoesNotMatch.
        self.endpoint = _normalize_endpoint(_env("PHOTOBIND_R2_ENDPOINT"),
                                           self.bucket)
        self.public_base = _env("PHOTOBIND_R2_PUBLIC_BASE")
        self._access = _env("PHOTOBIND_R2_ACCESS_KEY")
        self._secret = _env("PHOTOBIND_R2_SECRET_KEY")
        self.r2 = bool(self.bucket and self.endpoint and self._access and self._secret)
        self._client = None
        if not self.r2:
            self.dir.mkdir(parents=True, exist_ok=True)

    @property
    def dir(self) -> Path:
        # Read through the module attribute so tests can redirect it.
        import releases
        return releases.LOCAL_APK_DIR

    def _s3(self):
        if self._client is None:
            import boto3
            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint,
                aws_access_key_id=self._access,
                aws_secret_access_key=self._secret, region_name="auto")
        return self._client

    def put(self, key: str, data: bytes) -> None:
        if self.r2:
            self._s3().put_object(Bucket=self.bucket, Key=key, Body=data,
                                  ContentType="application/vnd.android.package-archive")
        else:
            self.dir.mkdir(parents=True, exist_ok=True)
            (self.dir / Path(key).name).write_bytes(data)

    def delete(self, key: str) -> None:
        if self.r2:
            self._s3().delete_object(Bucket=self.bucket, Key=key)
        else:
            p = self.dir / Path(key).name
            if p.exists():
                p.unlink()

    def url(self, key: str, public_host: str) -> str:
        if self.r2 and self.public_base:
            return f"{self.public_base.rstrip('/')}/{key}"
        return f"{public_host.rstrip('/')}/dl/{Path(key).name}"

    def read(self, key: str) -> bytes | None:
        if self.r2:
            try:
                return self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except Exception:
                return None
        p = self.dir / Path(key).name
        return p.read_bytes() if p.exists() else None

    @property
    def kind(self) -> str:
        return "cloudflare-r2" if self.r2 else "local-dev-disk"


def make_router(SessionLocal, public_host: str, notify=None) -> APIRouter:
    router = APIRouter()

    def announce(event: str, data: dict) -> None:
        if notify is not None:
            notify(None, event, data)          # None = broadcast to everyone

    def store() -> Storage:
        return Storage()

    def db_session():
        return SessionLocal()

    def latest_release(db) -> AppRelease | None:
        return (db.query(AppRelease).filter_by(pruned=False)
                .order_by(AppRelease.version_code.desc()).first())

    def require_admin(request: Request) -> None:
        admin_token = _env("PHOTOBIND_ADMIN_TOKEN")
        if not admin_token:
            raise HTTPException(503, "Publishing is not configured "
                                     "(PHOTOBIND_ADMIN_TOKEN unset).")
        if request.headers.get("x-admin-token") != admin_token:
            raise HTTPException(401, "bad admin token")

    def prune(db) -> list[int]:
        """Delete superseded APKs from storage once a newer release has
        confirmed installs, keeping PRUNE_KEEP for rollback."""
        live = (db.query(AppRelease).filter_by(pruned=False)
                .order_by(AppRelease.version_code.desc()).all())
        if len(live) <= 1:
            return []
        newest = live[0]
        if newest.install_count < 1:
            return []          # nothing has actually installed it yet
        removed = []
        for old in live[1 + prune_keep():]:
            store().delete(old.object_key)
            old.pruned = True
            removed.append(old.version_code)
        if removed:
            db.commit()
        return removed

    @router.post("/v1/app/prune")
    def prune_now(request: Request, keep: int = 1):
        """Delete superseded APKs now, rather than waiting for install reports.

        The automatic prune holds a release back until a newer one has confirmed
        installs, which is the right default — it keeps a rollback target while
        an update is still rolling out. This is the deliberate override for when
        the operator knows the old builds are finished with.
        """
        require_admin(request)
        db = db_session()
        try:
            return _prune_to(db, keep)
        finally:
            db.close()

    def _prune_to(db, keep: int) -> dict:
        live = (db.query(AppRelease).filter_by(pruned=False)
                .order_by(AppRelease.version_code.desc()).all())
        keep = max(1, keep)
        removed = []
        for old in live[keep:]:
            try:
                store().delete(old.object_key)
            except Exception:
                pass          # already gone from storage; still mark it pruned
            old.pruned = True
            removed.append(old.version_code)
        if removed:
            db.commit()
        return {"kept": [r.version_code for r in live[:keep]],
                "deleted": removed, "count": len(removed)}

    @router.get("/v1/app/latest")
    def app_latest():
        """Manifest the Android app polls. Also feeds the website CTA."""
        db = db_session()
        try:
            rel = latest_release(db)
            if rel is None:
                return {"available": False,
                        "reason": "no release published yet",
                        "storage": store().kind}
            return {
                "available": True,
                "version_code": rel.version_code,
                "version_name": rel.version_name,
                "min_sdk": rel.min_sdk,
                "min_android": android_label(rel.min_sdk),
                "size_bytes": rel.size_bytes,
                "size_mb": round(rel.size_bytes / 1_048_576, 1),
                "sha256": rel.sha256,
                "url": store().url(rel.object_key, public_host),
                "notes": rel.notes,
                "mandatory": rel.mandatory,
                "published_at": rel.created_at.isoformat(),
                "storage": store().kind,
            }
        finally:
            db.close()

    @router.post("/v1/app/installed")
    def app_installed(version_code: int = Form(...)):
        """The app calls this after a successful install. Confirmed installs
        are what allow older APKs to be pruned."""
        db = db_session()
        try:
            rel = db.get(AppRelease, version_code)
            if rel is None:
                raise HTTPException(404, "unknown version")
            rel.install_count += 1
            db.commit()
            return {"status": "recorded", "version_code": version_code,
                    "pruned": prune(db)}
        finally:
            db.close()

    @router.post("/v1/admin/releases", status_code=201)
    async def publish_release(request: Request,
                              apk: UploadFile = File(...),
                              version_code: int = Form(...),
                              version_name: str = Form(...),
                              min_sdk: int = Form(26),
                              notes: str = Form(""),
                              mandatory: bool = Form(False),
                              replace: bool = Form(False)):
        require_admin(request)
        data = await apk.read()
        if data[:2] != b"PK":
            raise HTTPException(422, "that file is not an APK (no zip header)")
        db = db_session()
        try:
            existing = db.get(AppRelease, version_code)
            if existing and not replace:
                raise HTTPException(
                    409, f"version_code {version_code} already published. Pass "
                         f"replace=true to overwrite it (only sensible before "
                         f"anyone has installed it).")
            if existing:
                # Replacing in place: drop the old object so storage does not
                # accumulate orphans under a key we are about to reuse.
                if existing.object_key:
                    store().delete(existing.object_key)
                db.delete(existing)
                db.commit()
            digest = hashlib.sha256(data).hexdigest()
            # The digest is in the filename, so a replaced build is a different
            # URL and no cache can answer with the old bytes.
            key = f"apks/identity-{version_name}-{version_code}-{digest[:12]}.apk"
            store().put(key, data)
            rel = AppRelease(version_code=version_code, version_name=version_name,
                             min_sdk=min_sdk, size_bytes=len(data),
                             sha256=digest,
                             object_key=key, notes=notes, mandatory=mandatory)
            db.add(rel)
            db.commit()
            announce("release.published", {"version_name": version_name,
                                           "version_code": version_code})
            return {"published": version_name, "version_code": version_code,
                    "sha256": rel.sha256, "url": store().url(key, public_host),
                    "storage": store().kind}
        finally:
            db.close()

    @router.get("/dl/{filename}")
    def download_apk(filename: str):
        """Serves APKs when R2 is not configured (dev), and is the download
        target for the website CTA."""
        from fastapi.responses import Response
        db = db_session()
        try:
            rel = latest_release(db)
            if rel is None or Path(rel.object_key).name != filename:
                # Allow older non-pruned versions too.
                match = (db.query(AppRelease)
                         .filter(AppRelease.object_key.endswith(filename),
                                 AppRelease.pruned == False).first())  # noqa: E712
                if match is None:
                    raise HTTPException(404, "no such release")
                rel = match
            data = store().read(rel.object_key)
            if data is None:
                raise HTTPException(410, "this version is no longer available")
            return Response(data,
                            media_type="application/vnd.android.package-archive",
                            headers={
                                "Content-Disposition":
                                    f'attachment; filename="{filename}"',
                                # The name carries the digest, so the bytes at a
                                # given URL never change; still revalidate so a
                                # pruned release cannot be served from a cache.
                                "Cache-Control": "public, max-age=300, must-revalidate",
                            })
        finally:
            db.close()

    return router
