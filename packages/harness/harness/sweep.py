"""Sweep orchestrator: photos x fusion params x degradation conditions.

Emits one CSV row per (photo, params, condition) with the three decoder
verdicts, plus pristine SSIM metrics for the frontier plot. Saves a few fused
samples for eyeballing. Run analyze.py on the CSV for the gate verdict.
"""

import argparse
import csv
import itertools
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import cv2

from .decode import decode_all
from .degrade import all_conditions, apply
from .fuse import FuseParams, fuse
from .metrics import ssim_face, ssim_full

VERSIONS = (2, 3)
CONTRAST = ("strong", "medium", "soft")
ALPHA_PROTECTED = (0.25, 0.45, 0.65)
CENTER_RATIO = (0.38, 0.5)

FIELDS = [
    "photo", "version", "contrast", "alpha_protected", "center_ratio",
    "placement", "ssim_full", "ssim_face",
    "tag", "jpeg_q", "rotation", "brightness", "scale", "blur",
    "zxing", "pyzbar", "opencv",
]


def param_grid(center_ratios=CENTER_RATIO, versions=VERSIONS,
               contrasts=CONTRAST, alphas=ALPHA_PROTECTED) -> list[FuseParams]:
    return [FuseParams(version=v, contrast=ct, alpha_protected=ap, center_ratio=cr)
            for v, ct, ap, cr in itertools.product(versions, contrasts,
                                                   alphas, center_ratios)]


def run_one(photo_path: str, params: FuseParams, samples_dir: str | None) -> list[dict]:
    # One OpenCV thread per worker process — otherwise 10 workers x 12-thread
    # pools thrash the whole machine.
    cv2.setNumThreads(1)
    img = cv2.imread(photo_path)
    if img is None:
        raise ValueError(f"unreadable photo: {photo_path}")
    fused = fuse(img, params)

    s_full = ssim_full(fused.reference, fused.image)
    s_face = ssim_face(fused.reference, fused.image, fused.face_bbox)

    if samples_dir:
        out = Path(samples_dir) / f"{Path(photo_path).stem}_{params.label()}.png"
        cv2.imwrite(str(out), fused.image)

    rows = []
    base = {
        "photo": Path(photo_path).name,
        "version": params.version,
        "contrast": params.contrast,
        "alpha_protected": params.alpha_protected,
        "center_ratio": params.center_ratio,
        "placement": fused.placement,
        "ssim_full": round(s_full, 4),
        "ssim_face": round(s_face, 4),
    }
    for cond in all_conditions():
        degraded = apply(fused.image, cond)
        verdicts = decode_all(degraded, fused.payload)
        rows.append({
            **base,
            "tag": cond.tag, "jpeg_q": cond.jpeg_q, "rotation": cond.rotation,
            "brightness": cond.brightness, "scale": cond.scale, "blur": cond.blur,
            **{k: int(v) for k, v in verdicts.items()},
        })
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", default="photos", help="directory of test photos")
    ap.add_argument("--out", default="results/results.csv")
    ap.add_argument("--samples", default="results/samples",
                    help="directory for sample fused images ('' to skip)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--center-ratios", type=float, nargs="+", default=list(CENTER_RATIO))
    ap.add_argument("--versions", type=int, nargs="+", default=list(VERSIONS))
    ap.add_argument("--contrasts", nargs="+", default=list(CONTRAST))
    ap.add_argument("--alphas", type=float, nargs="+", default=list(ALPHA_PROTECTED))
    args = ap.parse_args()

    photos = sorted(p for p in Path(args.photos).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))
    if not photos:
        sys.exit(f"no photos in {args.photos}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    if args.samples:
        Path(args.samples).mkdir(parents=True, exist_ok=True)

    grid = param_grid(tuple(args.center_ratios), tuple(args.versions),
                      tuple(args.contrasts), tuple(args.alphas))
    jobs = [(str(p), params) for p in photos for params in grid]
    print(f"{len(photos)} photos x {len(grid)} param combos = {len(jobs)} fusions, "
          f"{len(all_conditions())} conditions each")

    t0 = time.time()
    rows, failures = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(run_one, p, params, args.samples or None): (p, params)
                for p, params in jobs}
        for i, fut in enumerate(as_completed(futs), 1):
            p, params = futs[fut]
            try:
                rows.extend(fut.result())
            except Exception as e:  # record, don't die mid-sweep
                failures.append((Path(p).name, params.label(), str(e)))
            if i % 10 == 0 or i == len(jobs):
                print(f"  {i}/{len(jobs)} fusions done ({time.time()-t0:.0f}s)")

    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {args.out}")
    if failures:
        print(f"{len(failures)} FAILURES:")
        for name, label, err in failures:
            print(f"  {name} {label}: {err}")


if __name__ == "__main__":
    main()
