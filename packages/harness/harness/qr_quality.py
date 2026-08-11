"""Phase 0 §10 extension: image-quality metrics beyond face SSIM for the
encoder parameter sweep. Face SSIM is a visual-quality subset metric only —
never an authentication metric.

Reads the fused sample images saved by sweep.py, recomputes the reference
(cover-crop of the source photo at canvas size) and reports per param combo:
full-image SSIM, PSNR, face-region SSIM (from the sweep CSV), and file-size
change (JPEG-90 fused vs JPEG-90 reference — same codec both sides).
"""

import argparse
import csv
import re
from collections import defaultdict
from pathlib import Path

import cv2
import numpy as np
from skimage.metrics import structural_similarity, peak_signal_noise_ratio

from .fuse import _cover_crop

SAMPLE_RE = re.compile(r"^(?P<photo>.+)_v(?P<version>\d)_(?P<contrast>[a-z]+)"
                       r"_ap(?P<ap>[\d.]+)_cr(?P<cr>[\d.]+)$")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", default="photos")
    ap.add_argument("--samples", default="results/samples")
    ap.add_argument("--out", default="results/qr_quality.csv")
    args = ap.parse_args()

    photos = {p.stem: p for p in Path(args.photos).iterdir()
              if p.suffix.lower() in (".jpg", ".jpeg", ".png")}
    combos = defaultdict(list)

    for sample in sorted(Path(args.samples).glob("*.png")):
        m = SAMPLE_RE.match(sample.stem)
        if not m or m["photo"] not in photos:
            continue
        fused = cv2.imread(str(sample))
        src = cv2.imread(str(photos[m["photo"]]))
        ref = _cover_crop(src, fused.shape[0])
        g_ref = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY)
        g_fus = cv2.cvtColor(fused, cv2.COLOR_BGR2GRAY)
        ssim = structural_similarity(g_ref, g_fus)
        psnr = peak_signal_noise_ratio(g_ref, g_fus)
        _, ref_j = cv2.imencode(".jpg", ref, [cv2.IMWRITE_JPEG_QUALITY, 90])
        _, fus_j = cv2.imencode(".jpg", fused, [cv2.IMWRITE_JPEG_QUALITY, 90])
        combos[(m["version"], m["contrast"], m["ap"], m["cr"])].append(
            (ssim, psnr, len(fus_j) / len(ref_j)))

    rows = []
    for (v, ct, ap_, cr), vals in sorted(combos.items()):
        a = np.array(vals)
        rows.append({"version": v, "contrast": ct, "alpha_protected": ap_,
                     "center_ratio": cr, "n_photos": len(vals),
                     "ssim_mean": round(float(a[:, 0].mean()), 4),
                     "psnr_mean_db": round(float(a[:, 1].mean()), 2),
                     "file_size_ratio_jpeg90": round(float(a[:, 2].mean()), 3)})
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} combos -> {args.out}")
    for r in rows[:6]:
        print(r)


if __name__ == "__main__":
    main()
