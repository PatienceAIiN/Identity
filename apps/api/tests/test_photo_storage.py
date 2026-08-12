"""Photos in object storage.

The interesting risk is not whether the bytes arrive. It is that compressing a
stored code quietly costs it something: a scan that no longer decodes, or a
photo that no longer verifies as the one the code was made for. Both are
asserted here against the real encoder and the real binding, so a future change
to the format has to keep them true.
"""

import base64
import io
import pathlib
import sys

import cv2
import numpy as np
import pytest

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
for pkg in ("packages/encoder", "packages/harness", "packages/binding"):
    p = str(ROOT / pkg)
    if p not in sys.path:
        sys.path.insert(0, p)

import blobs  # noqa: E402
from binding.fingerprint import (  # noqa: E402
    compare_tiles, distance, phash_global, tile_features)
from binding.verify import Thresholds  # noqa: E402
from encoder.api import EncodeOptions, _confidence, encode_photo  # noqa: E402

PAYLOAD = "https://identity.patienceai.in/r/AbCdEf-GhIjK"
PHOTO = ROOT / "apps/web/static/assets/hero.jpg"


@pytest.fixture(scope="module")
def fused() -> bytes:
    if not PHOTO.exists():
        pytest.skip("sample photo missing")
    return encode_photo(PHOTO.read_bytes(), PAYLOAD,
                        EncodeOptions(coverage="full")).image_png


def test_compression_is_worth_doing(fused):
    small = blobs.compress(fused)
    # Lossless WebP measured 1.7-1.9x smaller than the PNG on three portraits.
    # 1.3x is the floor worth converting for.
    assert len(small) * 1.3 < len(fused), (
        f"{len(fused)} -> {len(small)} is not worth the conversion")


def test_the_stored_copy_is_pixel_identical_to_what_was_validated(fused):
    """The reason the stored format is lossless.

    Every claim about a code — its decode rate, its binding — was measured
    against the pixels the encoder produced. If the stored copy differs from
    those pixels, none of those measurements describe what the user actually
    has. Asserting equality is stronger than re-measuring decodability, because
    it cannot pass by luck on one photo and fail on another.
    """
    original = cv2.imdecode(np.frombuffer(fused, np.uint8), cv2.IMREAD_COLOR)
    stored = cv2.imdecode(np.frombuffer(blobs.compress(fused), np.uint8),
                          cv2.IMREAD_COLOR)
    assert stored.shape == original.shape
    assert np.array_equal(stored, original), "the stored copy is not the image "\
        "that passed validation"


def test_a_stored_photo_still_scans(fused):
    stored = cv2.imdecode(np.frombuffer(blobs.compress(fused), np.uint8),
                          cv2.IMREAD_COLOR)
    _, rate = _confidence(np.ascontiguousarray(stored), PAYLOAD)
    original = _confidence(
        np.ascontiguousarray(cv2.imdecode(np.frombuffer(fused, np.uint8),
                                          cv2.IMREAD_COLOR)), PAYLOAD)[1]
    assert rate >= original, (
        f"compression cost decodability: {original:.2f} -> {rate:.2f}")


def test_a_stored_photo_still_verifies_as_the_same_image(fused):
    """The product's central promise, applied to our own storage.

    If compression pushed the perceptual distance past the threshold, every
    user who downloaded their own code and submitted it back would be told it
    had been modified.
    """
    th = Thresholds.load()
    ref = cv2.imdecode(np.frombuffer(fused, np.uint8), cv2.IMREAD_COLOR)
    back = cv2.imdecode(np.frombuffer(blobs.compress(fused), np.uint8),
                        cv2.IMREAD_COLOR)
    d = distance(phash_global(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)),
                 phash_global(cv2.cvtColor(back, cv2.COLOR_BGR2GRAY)))
    assert d <= th.derived_max, f"stored copy reads as modified (distance {d})"
    tiles = compare_tiles(tile_features(ref), tile_features(back))
    changed = [r for r in (tiles.get("regions") or []) if r.get("changed")]
    assert not changed, f"compression flagged {len(changed)} tiles as changed"


def test_served_bytes_are_png_whatever_is_stored(fused):
    """The endpoint is /v1/photos/{id}.png and must not hand back a WebP."""
    assert blobs.to_png(blobs.compress(fused))[:8] == b"\x89PNG\r\n\x1a\n"


class FakeR2:
    """Enough of the S3 surface for the store, so the paths run without cloud."""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.deleted: list[str] = []

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key])}

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)
        self.objects.pop(Key, None)

    def list_objects_v2(self, Bucket, Prefix, MaxKeys, ContinuationToken=None):
        return {"Contents": [{"Key": k, "Size": len(v)}
                             for k, v in self.objects.items()
                             if k.startswith(Prefix)]}


@pytest.fixture
def store(monkeypatch):
    s = blobs.PhotoStore()
    fake = FakeR2()
    monkeypatch.setattr(s, "enabled", True)
    monkeypatch.setattr(s, "bucket", "test-bucket")
    monkeypatch.setattr(s, "_s3", lambda: fake)
    return s, fake


def test_round_trip_through_the_store(store, fused):
    s, fake = store
    key = s.put("ph_abc", fused)
    assert key == "photos/ph_abc.webp"
    assert list(fake.objects) == [key]
    assert s.read(key)[:8] == b"\x89PNG\r\n\x1a\n"     # PNG on the way out
    assert len(fake.objects[key]) * 1.3 < len(fused)   # WebP on the way in


def test_deleting_removes_the_object(store, fused):
    s, fake = store
    key = s.put("ph_gone", fused)
    assert s.delete(key) is True
    assert fake.deleted == [key]
    assert s.read(key) is None


def test_storage_full_refuses_instead_of_overrunning_the_budget(store, fused,
                                                               monkeypatch):
    s, _ = store
    s.put("ph_one", fused)
    # Ceiling below what is already stored: the next write must be refused,
    # rather than silently running past the free tier.
    monkeypatch.setattr(blobs, "MAX_BYTES", 10)
    with pytest.raises(Exception) as excinfo:
        s.put("ph_two", fused)
    assert getattr(excinfo.value, "status_code", None) == 507


def test_usage_report_counts_what_was_stored(store, fused):
    s, fake = store
    s.put("ph_a", fused)
    s.put("ph_b", fused)
    s._usage_cache = None                  # force a real recount
    report = s.usage_report()
    assert report["objects"] == 2
    assert report["bytes"] == sum(len(v) for v in fake.objects.values())
    assert report["figure_is_fresh"] is True
    assert report["full"] is False


def test_unconfigured_store_keeps_bytes_in_the_database(fused):
    """A checkout with no cloud credentials must still work end to end."""
    s = blobs.PhotoStore()
    s.enabled = False
    assert s.put("ph_x", fused) is None
    assert s.kind == "database"
