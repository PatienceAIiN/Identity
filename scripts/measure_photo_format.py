#!/usr/bin/env python3
"""Reproduce the stored-photo format decision.

Answers the only two questions that matter about compressing a stored code:
does it still scan, and does it still verify as the same image? Size is the
easy part and is reported last on purpose.

    python scripts/measure_photo_format.py

Everything printed here is SYNTHETIC: the conditions are the encoder's own
validation transformations (re-compression, resize, brightness, and so on),
not photographs taken with a camera. A format that scores well here has not
been tested against a real sensor.
"""

import pathlib
import sys

import cv2
import numpy as np

ROOT = pathlib.Path(__file__).resolve().parents[1]
for pkg in ("packages/encoder", "packages/harness", "packages/binding", "apps/api"):
    sys.path.insert(0, str(ROOT / pkg))

from binding.fingerprint import (  # noqa: E402
    compare_tiles, distance, phash_global, tile_features)
from binding.verify import Thresholds  # noqa: E402
import blobs  # noqa: E402  — measure the function production actually calls
from encoder.api import EncodeOptions, _confidence, encode_photo  # noqa: E402

PAYLOAD = "https://identity.patienceai.in/r/AbCdEf-GhIjK"
# Qualities passed to cv2.imencode(".webp", ...). Above 100 means lossless,
# which is what production stores; the lossy rows are kept because they are the
# reason it does not — watch the decode rate column, not the size column.
CANDIDATES = [101, 95, 92, 90, 85]


def as_webp(png_bytes: bytes, quality: int) -> bytes:
    """quality > 100 asks OpenCV for lossless."""
    img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
    ok, buf = cv2.imencode(".webp", img, [cv2.IMWRITE_WEBP_QUALITY, quality])
    if not ok:
        raise SystemExit("WebP encode failed — is this OpenCV built with WebP?")
    return buf.tobytes()


def samples() -> list[pathlib.Path]:
    found = [ROOT / "apps/web/static/assets/hero.jpg"]
    found += sorted((ROOT / "packages/harness/photos").glob("*.jpg"))
    return [p for p in found if p.exists()][:4]


def main() -> int:
    th = Thresholds.load()
    print(f"binding thresholds: same/derived <= {th.derived_max}, "
          f"modified >= {th.modified_min} (calibrated={th.calibrated})")
    for src in samples():
        enc = encode_photo(src.read_bytes(), PAYLOAD,
                           EncodeOptions(coverage="full"))
        ref = cv2.imdecode(np.frombuffer(enc.image_png, np.uint8), cv2.IMREAD_COLOR)
        ref_hash = phash_global(cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY))
        ref_tiles = tile_features(ref)
        print(f"\n{src.name}: PNG {len(enc.image_png) / 1024:.0f} KiB, "
              f"decode rate {enc.decode_rate:.2f}")
        print(f"  {'format':16}{'size':>10}  {'decode rate':>12}  "
              f"{'binding distance':>17}  verdict")
        for quality in CANDIDATES:
            label = "WebP lossless" if quality > 100 else f"WebP q{quality}"
            data = (blobs.compress(enc.image_png)
                    if quality == blobs.WEBP_LOSSLESS_QUALITY
                    else as_webp(enc.image_png, quality))
            back = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            _, rate = _confidence(np.ascontiguousarray(back), PAYLOAD)
            d = distance(ref_hash,
                         phash_global(cv2.cvtColor(back, cv2.COLOR_BGR2GRAY)))
            tiles = compare_tiles(ref_tiles, tile_features(back))
            changed = sum(1 for r in (tiles.get("regions") or [])
                          if r.get("changed")) if isinstance(tiles, dict) else 0
            verdict = ("same/derived" if d <= th.derived_max
                       else "MODIFIED" if d >= th.modified_min else "uncertain")
            print(f"  {label:16}{len(data) / 1024:8.0f} KiB{rate:13.2f}"
                  f"{d:19.4f}  {verdict} ({changed} tiles flagged)")
    print("\nProduction stores lossless WebP (apps/api/blobs.py): smaller than "
          "the PNG and pixel-identical to it, so the stored copy decodes exactly "
          "as well as the image that passed validation. The lossy rows are much "
          "smaller and mostly score the same — but not always, and a stored code "
          "that scans worse than the one we validated is not a trade worth tens "
          "of KiB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
