"""Adversarial boundary search (spec §22): after calibration, find the
smallest transformations that cross each verification boundary. These are
boundary conditions, not marketing numbers — the evasion boundaries are as
important to report as the detection ones.

All searches run on held-out portraits with the frozen calibrated thresholds.
"""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from binding import canonical, fingerprint
from binding.record import build_binding
from binding.verify import Thresholds

from .decode import decode_all
from .fuse import FuseParams, fuse
from .binding_eval import NOW

PARAMS = FuseParams(version=3, contrast="strong", alpha_protected=0.45,
                    center_ratio=0.5)


def _jpeg(img, q=92):
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    return buf.tobytes()


def _verdict(ref_binding, candidate_bytes, th) -> str:
    """Distance-layer verdict only (credential layer is orthogonal here)."""
    try:
        bgr = canonical.decode_bgr(candidate_bytes)
        gray = canonical.decode_gray(candidate_bytes)
    except ValueError:
        return "CANNOT_VERIFY_PHOTO"
    d = fingerprint.distance(fingerprint.phash_global(gray),
                             ref_binding.global_fingerprint)
    tiles = fingerprint.compare_tiles(
        {"phash": ref_binding.region_fingerprints,
         "chroma": ref_binding.region_chroma,
         "energy": ref_binding.region_energy},
        fingerprint.tile_features(bgr))
    flagged = (tiles["max_hash"] >= th.tile_hash_min
               or tiles["max_chroma"] >= th.tile_chroma_min
               or tiles["max_energy"] >= th.tile_energy_min)
    if d >= th.modified_min or flagged:
        return "CONTENT_MODIFIED"
    if d <= th.derived_max:
        return "AUTHENTIC_DERIVED"
    return "INSUFFICIENT_EVIDENCE"


def _search(values, predicate):
    """First value in `values` where predicate flips true; None if never."""
    for v in values:
        if predicate(v):
            return v
    return None


def boundaries_for(photo_path: str, th: Thresholds) -> dict:
    img = cv2.imread(photo_path)
    base = _jpeg(img)
    ref = build_binding(base, photo_id="p", credential_id="c",
                        signing_key_id="k", created_at=NOW)
    decoded = canonical.decode_bgr(base)
    h, w = decoded.shape[:2]
    out = {}

    # 1. Smallest crop that loses AUTHENTIC_DERIVED (keep fraction per side).
    def crop_flags(keep):
        dy, dx = int(h * (1 - keep) / 2), int(w * (1 - keep) / 2)
        return _verdict(ref, _jpeg(decoded[dy:h - dy, dx:w - dx]), th) != "AUTHENTIC_DERIVED"
    out["smallest_crop_causing_mismatch_pct_per_side"] = _search(
        [round(1 - k, 3) for k in np.arange(0.995, 0.85, -0.005)],
        lambda c: crop_flags(1 - c))

    # 2. Smallest centered square edit (solid patch) that is DETECTED, and
    #    largest that EVADES — same axis, both ends reported.
    def patch_detected(frac):
        side = max(4, int(min(h, w) * frac))
        x, y = w // 2 - side // 2, h // 3
        m = decoded.copy()
        m[y:y + side, x:x + side] = (0, 255, 0)
        return _verdict(ref, _jpeg(m), th) == "CONTENT_MODIFIED"
    fracs = [round(f, 3) for f in np.arange(0.005, 0.2, 0.005)]
    smallest_detected = _search(fracs, patch_detected)
    out["smallest_inserted_patch_detected_frac_of_min_dim"] = smallest_detected
    evading = [f for f in fracs if not patch_detected(f)]
    out["largest_inserted_patch_evading_frac_of_min_dim"] = max(evading) if evading else None

    # 3. Smallest local blur sigma detected (fixed textured region, 16% side).
    def blur_detected(sigma):
        side = int(min(h, w) * 0.16)
        x, y = w // 2 - side // 2, h // 3
        m = decoded.copy()
        m[y:y + side, x:x + side] = cv2.GaussianBlur(
            m[y:y + side, x:x + side], (0, 0), sigma)
        return _verdict(ref, _jpeg(m), th) == "CONTENT_MODIFIED"
    out["smallest_local_blur_sigma_detected"] = _search(
        [1, 2, 3, 4, 5, 6, 8, 10, 12, 16, 20, 28, 40], blur_detected)

    # 4. Smallest brightness deviation that breaks AUTHENTIC_DERIVED.
    def bright_breaks(delta):
        m = np.clip(decoded.astype(np.float32) * (1 + delta), 0, 255).astype(np.uint8)
        return _verdict(ref, _jpeg(m), th) != "AUTHENTIC_DERIVED"
    out["smallest_brightness_increase_breaking_binding"] = _search(
        [round(v, 2) for v in np.arange(0.05, 1.0, 0.05)], bright_breaks)
    out["smallest_brightness_decrease_breaking_binding"] = _search(
        [round(v, 2) for v in np.arange(0.05, 0.95, 0.05)],
        lambda d: bright_breaks(-d))

    # QR boundaries on the fused artifact.
    fused = fuse(img, PARAMS)

    def qr_dead(data):
        arr = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        v = decode_all(arr, fused.payload)
        return not (v["zxing"] or v["pyzbar"])

    out["lowest_jpeg_quality_qr_survives"] = None
    for q in range(95, 4, -5):
        if qr_dead(_jpeg(fused.image, q)):
            break
        out["lowest_jpeg_quality_qr_survives"] = q

    out["smallest_resize_scale_qr_survives"] = None
    for s in [round(v, 2) for v in np.arange(1.0, 0.05, -0.05)]:
        small = cv2.resize(fused.image, None, fx=s, fy=s,
                           interpolation=cv2.INTER_AREA)
        if qr_dead(_jpeg(small, 85)):
            break
        out["smallest_resize_scale_qr_survives"] = s

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", default="photos")
    ap.add_argument("--out", default="results/boundaries.json")
    ap.add_argument("--n", type=int, default=3)
    args = ap.parse_args()

    th = Thresholds.load()
    assert th.calibrated, "run binding_eval first to produce thresholds.json"
    photos = sorted(str(p) for p in Path(args.photos).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))[:args.n]
    results = {Path(p).stem: boundaries_for(p, th) for p in photos}
    Path(args.out).write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
