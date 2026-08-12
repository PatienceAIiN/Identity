"""Fused photo bytes in object storage.

Postgres was the wrong home for these. Every photo is megabytes, every backup
carries all of them, and a row store gives nothing in return for the space —
the bytes are never queried, only handed back whole.

Photos are private, which is the one place this differs from the APK storage in
releases.py. There is no public base URL and no presigned link: a fused photo is
somebody's face, and the only reader is this API, after it has checked who is
asking. That costs us the bandwidth and buys the property that a leaked URL is
not a leaked portrait.

When R2 is not configured — local development, the test suite — put() returns
None and the caller keeps storing bytes in the database exactly as before. That
keeps a checkout with no cloud credentials fully working, and it is why the
Photo row still has an image_png column.
"""

from __future__ import annotations

import logging
import os

from fastapi import HTTPException

log = logging.getLogger("photobind.blobs")

PREFIX = "photos/"

# Stored copies are WebP, and lossless. scripts/measure_photo_format.py
# reproduces the decision.
#
# Lossless is 1.7-1.9x smaller than the encoder's PNG (1278 KiB -> 783, 1207 ->
# 676, 1668 -> 994) and pixel-identical to it, so the stored copy decodes
# exactly as well as the image that passed validation. That is a guarantee, not
# a measurement.
#
# Lossy q92 was measured first and rejected, even though it is 8-11x smaller.
# Across three portraits it looked free — same decode rate, binding distance
# 0.0000. A fourth photo broke that: baseline decode rate 0.90 fell to 0.70 at
# q95 and 0.80 at q90, with q92 holding at 0.90 by luck rather than by any
# property. Compression that sometimes costs a code its scannability is not a
# trade this product gets to make quietly for a few tens of KiB, so it does not
# make it at all.
#
# If storage ever becomes the binding constraint, the lever is lossy plus a
# per-photo re-validation of the compressed copy — not lossy on its own.
WEBP_LOSSLESS_QUALITY = 101      # cv2: above 100 means lossless

# Cloudflare's free tier is 10 GB of storage. Past it the overage is cheap
# rather than catastrophic (~$0.015/GB-month), so the default ceiling sits at
# the free limit and refuses new photos with a clear message instead of
# quietly running up a bill.
FREE_TIER_BYTES = 10 * 1024 ** 3
MAX_BYTES = int(os.environ.get("PHOTOBIND_R2_MAX_BYTES") or FREE_TIER_BYTES)
WARN_FRACTION = 0.8


def _decode(data: bytes):
    import cv2
    import numpy as np
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a decodable image")
    return img


def compress(png_bytes: bytes) -> bytes:
    """PNG in, WebP out. Raises rather than storing an unreadable object.

    Uses OpenCV rather than Pillow because OpenCV is already in the deployment
    image and Pillow is not. Found the hard way: the first version of this ran
    green locally and failed on every photo in production with "No module named
    'PIL'", because the test environment carries Pillow and the container does
    not.
    """
    import cv2
    ok, buf = cv2.imencode(".webp", _decode(png_bytes),
                           [cv2.IMWRITE_WEBP_QUALITY, WEBP_LOSSLESS_QUALITY])
    if not ok:
        raise ValueError("WebP encode failed")
    return buf.tobytes()


def to_png(data: bytes) -> bytes:
    """WebP back to PNG, so /v1/photos/{id}.png keeps its contract.

    The alternative was serving WebP under a .png name, which hands people a
    misnamed file when they download their own code.
    """
    import cv2
    ok, buf = cv2.imencode(".png", _decode(data))
    if not ok:
        raise ValueError("PNG encode failed")
    return buf.tobytes()


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


class PhotoStore:
    def __init__(self):
        self.bucket = _env("PHOTOBIND_R2_BUCKET")
        endpoint = _env("PHOTOBIND_R2_ENDPOINT").rstrip("/")
        # Cloudflare displays the endpoint with the bucket appended; the S3 API
        # wants the account endpoint alone. Same normalisation as releases.py.
        if self.bucket and endpoint.endswith("/" + self.bucket):
            endpoint = endpoint[: -(len(self.bucket) + 1)]
        self.endpoint = endpoint
        self._access = _env("PHOTOBIND_R2_ACCESS_KEY")
        self._secret = _env("PHOTOBIND_R2_SECRET_KEY")
        self.enabled = bool(self.bucket and self.endpoint
                            and self._access and self._secret)
        self._client = None
        self._usage_cache: tuple[int, int, float] | None = None

    def _s3(self):
        if self._client is None:
            import boto3
            from botocore.config import Config
            self._client = boto3.client(
                "s3", endpoint_url=self.endpoint,
                aws_access_key_id=self._access,
                aws_secret_access_key=self._secret, region_name="auto",
                # A photo request is in front of a user waiting for an image.
                # Better to fail and fall back than to hold the connection.
                config=Config(connect_timeout=4, read_timeout=12,
                              retries={"max_attempts": 2}))
        return self._client

    def key_for(self, photo_id: str) -> str:
        return f"{PREFIX}{photo_id}.webp"

    def put(self, photo_id: str, data: bytes) -> str | None:
        """Store the bytes and return the object key, or None if unconfigured.

        Raises if storage is configured but the write fails: silently falling
        back to the database would mean the migration quietly stops working and
        nobody finds out until the table is large again.
        """
        if not self.enabled:
            return None
        used, _, _ = self.usage()
        if used >= MAX_BYTES:
            raise HTTPException(
                507, "Photo storage is full. Delete some codes, or raise the "
                     "storage ceiling, and try again.")
        key = self.key_for(photo_id)
        body = compress(data)
        self._s3().put_object(Bucket=self.bucket, Key=key, Body=body,
                              ContentType="image/webp")
        # Keep the cached total honest between recounts, so a burst of uploads
        # cannot walk past the ceiling on a stale figure.
        if self._usage_cache is not None:
            u, n, at = self._usage_cache
            self._usage_cache = (u + len(body), n + 1, at)
        return key

    def read(self, key: str) -> bytes | None:
        """Returns PNG bytes whatever the stored format is."""
        if not self.enabled:
            return None
        try:
            raw = self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception:
            log.warning("photo object unreadable: %s", key)
            return None
        if key.endswith(".webp"):
            try:
                return to_png(raw)
            except Exception:
                log.warning("photo object undecodable: %s", key)
                return None
        return raw

    def delete(self, key: str) -> bool:
        if not self.enabled or not key:
            return False
        try:
            self._s3().delete_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            # The row is already gone. A surviving object is storage waste, not
            # a data leak, and the sweeper below collects it.
            log.warning("photo object not deleted: %s", key)
            return False

    def list_keys(self, limit: int = 100000) -> list[str]:
        if not self.enabled:
            return []
        keys, token = [], None
        while len(keys) < limit:
            kw = {"Bucket": self.bucket, "Prefix": PREFIX, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = self._s3().list_objects_v2(**kw)
            keys.extend(o["Key"] for o in page.get("Contents", []))
            token = page.get("NextContinuationToken")
            if not token:
                break
        return keys

    # -- generic object access -------------------------------------------------
    # Used by backups, which must not live in the database they back up.

    def raw_put(self, key: str, data: bytes, content_type: str) -> bool:
        if not self.enabled:
            return False
        self._s3().put_object(Bucket=self.bucket, Key=key, Body=data,
                              ContentType=content_type)
        return True

    def raw_get(self, key: str) -> bytes | None:
        if not self.enabled:
            return None
        try:
            return self._s3().get_object(Bucket=self.bucket, Key=key)["Body"].read()
        except Exception:
            return None

    def raw_list(self, prefix: str, limit: int = 1000) -> list[dict]:
        if not self.enabled:
            return []
        out, token = [], None
        while len(out) < limit:
            kw = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token:
                kw["ContinuationToken"] = token
            page = self._s3().list_objects_v2(**kw)
            for o in page.get("Contents", []):
                out.append({"key": o["Key"], "bytes": int(o.get("Size") or 0),
                            "modified": (o.get("LastModified").isoformat()
                                         if o.get("LastModified") else None)})
            token = page.get("NextContinuationToken")
            if not token:
                break
        return out

    def exists(self, key: str) -> bool:
        if not self.enabled:
            return False
        try:
            self._s3().head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def usage(self, max_age_s: int = 300) -> tuple[int, int, bool]:
        """(bytes stored, object count, figure is fresh).

        Cached: a LIST is a Class A operation and this is read on every admin
        page view and every upload.
        """
        import time
        if not self.enabled:
            return (0, 0, True)
        if self._usage_cache is not None:
            used, count, at = self._usage_cache
            if time.time() - at < max_age_s:
                return (used, count, True)
        total = count = 0
        token = None
        try:
            while True:
                kw = {"Bucket": self.bucket, "Prefix": PREFIX, "MaxKeys": 1000}
                if token:
                    kw["ContinuationToken"] = token
                page = self._s3().list_objects_v2(**kw)
                for o in page.get("Contents", []):
                    total += int(o.get("Size") or 0)
                    count += 1
                token = page.get("NextContinuationToken")
                if not token:
                    break
        except Exception:
            log.warning("could not measure photo storage")
            if self._usage_cache is not None:
                used, count, _ = self._usage_cache
                return (used, count, False)     # stale, and says so
            return (0, 0, False)
        self._usage_cache = (total, count, time.time())
        return (total, count, True)

    def usage_report(self) -> dict:
        used, count, fresh = self.usage()
        return {"backend": self.kind, "objects": count, "bytes": used,
                "ceiling_bytes": MAX_BYTES, "free_tier_bytes": FREE_TIER_BYTES,
                "fraction_used": round(used / MAX_BYTES, 4) if MAX_BYTES else 0,
                "warn": used >= MAX_BYTES * WARN_FRACTION,
                "full": used >= MAX_BYTES, "figure_is_fresh": fresh,
                "average_bytes": (used // count) if count else 0}

    @property
    def kind(self) -> str:
        return "cloudflare-r2" if self.enabled else "database"


PHOTOS = PhotoStore()
