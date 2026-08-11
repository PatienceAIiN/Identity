import sys
from pathlib import Path

import cv2
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from binding.keys import DevKeyStore
from binding.record import build_binding, sign_binding
from binding.registry import CredentialRegistry, new_credential_id, new_photo_id

NOW = "2026-08-10T00:00:00+00:00"


def synthetic_photo(seed: int, size: int = 640) -> bytes:
    """Deterministic structured test image (gradients + shapes + noise),
    JPEG-encoded. No faces, no network."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) / size
    img = np.stack([120 + 100 * xx, 80 + 120 * yy, 90 + 80 * (xx * yy)], axis=-1)
    for _ in range(12):
        c = tuple(int(v) for v in rng.integers(60, size - 60, 2))
        r = int(rng.integers(20, 90))
        color = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.circle(img, c, r, color, -1)
    img += rng.normal(0, 6, img.shape)
    img = np.clip(img, 0, 255).astype(np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    return buf.tobytes()


@pytest.fixture()
def keystore(tmp_path):
    ks = DevKeyStore(tmp_path / "keys")
    ks.generate(activate=True)
    return ks


@pytest.fixture()
def registry():
    return CredentialRegistry()


def issue(image_bytes: bytes, keystore, registry, label="test"):
    """Register a photo binding + credential + one share; returns
    (share, credential, photo_id)."""
    photo_id = new_photo_id()
    credential_id = new_credential_id()
    rec = build_binding(image_bytes, photo_id=photo_id,
                        credential_id=credential_id,
                        signing_key_id=keystore.active_key_id(), created_at=NOW)
    signed = sign_binding(rec, keystore)
    cred = registry.register_credential(photo_id, signed, NOW)
    share = registry.mint_share(credential_id, label, NOW)
    return share, cred, photo_id
