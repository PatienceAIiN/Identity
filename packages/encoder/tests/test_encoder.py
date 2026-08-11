"""Encoder contract tests: validate-before-return, version selection,
escalation, and the biometric non-persistence property."""

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from encoder import (EncodeOptions, EncodeValidationError, NoFaceFoundError,
                     PayloadTooLargeError, choose_version, encode_photo)
from encoder.decoders import decode_all

PHOTOS = Path(__file__).resolve().parents[2] / "harness" / "photos"
PORTRAIT = next(PHOTOS.glob("Official_portrait*.jpg"))

ZK_PAYLOAD_V7 = "pb.id/r/hK4mZq9TvW#Ab3xK9mQpZw4TfGhJk2LnPqRsTuVwXyZ01dEfGhIjKm"


def test_choose_version():
    assert choose_version("pb.id/r/x7Qm2K") == 2
    assert choose_version("pb.id/r/hK4mZq9TvW3nXbRd") == 3
    assert choose_version(ZK_PAYLOAD_V7) == 7
    assert choose_version("x" * 74) == 8
    with pytest.raises(PayloadTooLargeError):
        choose_version("x" * 200)


def test_encode_returns_validated_scannable_image():
    result = encode_photo(PORTRAIT.read_bytes(), ZK_PAYLOAD_V7)
    assert result.decode_rate >= 0.85
    assert result.version == 7
    assert result.escalations == 0
    # The returned PNG itself decodes to the exact payload.
    img = cv2.imdecode(np.frombuffer(result.image_png, np.uint8),
                       cv2.IMREAD_COLOR)
    assert any(decode_all(img, ZK_PAYLOAD_V7).values())
    # Confidence report covers every validation condition.
    assert set(result.decode_confidence) == {
        "as_delivered", "jpeg75", "gate_rot+15", "gate_rot-15", "quarter_scale"}
    # Nothing biometric in the result payload.
    s = str(result.summary())
    for banned in ("landmark", "embedding", "bbox", "protection"):
        assert banned not in s


def test_photo_without_a_face_still_encodes():
    """Landscapes, objects, logos: nothing to protect, so protect nothing —
    but still validate and still return a scannable image."""
    flat = np.full((800, 800, 3), 128, np.uint8)
    ok, buf = cv2.imencode(".jpg", flat)
    r = encode_photo(buf.tobytes(), ZK_PAYLOAD_V7)
    assert r.decode_rate >= 0.85
    assert r.has_face is False
    assert r.face_ssim is None        # meaningless without a face
    # A flat grey image has no texture for the modules to hide in, so
    # full-bleed coverage necessarily changes most of it. The metric is
    # reported rather than asserted high — pretending otherwise would be
    # asserting something the rendering model cannot deliver.
    assert 0.0 < r.image_ssim <= 1.0


def test_textured_photo_keeps_more_of_the_image():
    """With texture to hide in, the same coverage preserves much more."""
    r = encode_photo(PORTRAIT.read_bytes(), ZK_PAYLOAD_V7)
    assert r.image_ssim > 0.4
    off_face = encode_photo(PORTRAIT.read_bytes(), ZK_PAYLOAD_V7,
                            EncodeOptions(coverage="auto"))
    # "auto" exists precisely to trade code area for image fidelity.
    assert off_face.image_ssim > r.image_ssim


def test_portrait_reports_face_metrics():
    r = encode_photo(PORTRAIT.read_bytes(), ZK_PAYLOAD_V7)
    assert r.has_face is True and r.face_ssim is not None


def test_unreachable_rate_raises_not_returns():
    with pytest.raises(EncodeValidationError):
        encode_photo(PORTRAIT.read_bytes(), ZK_PAYLOAD_V7,
                     EncodeOptions(min_decode_rate=1.01, allow_escalation=False))


def test_undecodable_input_rejected():
    with pytest.raises(Exception):
        encode_photo(b"not an image", "pb.id/r/x7Qm2K")
