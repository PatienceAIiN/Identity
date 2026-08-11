"""QR survival vs photo-binding survival (spec §11) + share cloning /
QR-splice replay (spec §12).

QR readability and photo authenticity are separate properties; this module
measures both per transformation and never infers one from the other.

Encoder configuration under test: the Phase 0 photo-dominant recommendation
(v3 / strong contrast / alpha_protected 0.45 / center_ratio 0.5). The
registered canonical image is the JPEG-92 encoding of the fused photo —
the artifact the product would actually deliver.
"""

import argparse
import csv
import json
from pathlib import Path

import cv2
import numpy as np

from binding.keys import DevKeyStore
from binding.record import build_binding, sign_binding
from binding.registry import CredentialRegistry, new_credential_id, new_photo_id
from binding.verify import Thresholds, verify_photo
from skimage.metrics import structural_similarity

from .decode import decode_all
from .fuse import FuseParams, fuse
from .transforms import BENIGN
from .binding_eval import NOW

PARAMS = FuseParams(version=3, contrast="strong", alpha_protected=0.45,
                    center_ratio=0.5)


def _crop(frac):
    def fn(data, rng):
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        h, w = img.shape[:2]
        dy, dx = int(h * (1 - frac) / 2), int(w * (1 - frac) / 2)
        out = img[dy:h - dy, dx:w - dx]
        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
        return buf.tobytes()
    return fn


CROPS = [("crop_keep_95pct_sides", _crop(0.95)),
         ("crop_keep_85pct_sides", _crop(0.85)),
         ("crop_keep_70pct_sides", _crop(0.70))]


def _issue(fused_bytes, keystore, registry):
    photo_id, credential_id = new_photo_id(), new_credential_id()
    rec = build_binding(fused_bytes, photo_id=photo_id, credential_id=credential_id,
                        signing_key_id=keystore.active_key_id(), created_at=NOW)
    registry.register_credential(photo_id, sign_binding(rec, keystore), NOW)
    return registry.mint_share(credential_id, "survival", NOW)


def _ssim(a_bytes, b_bytes):
    a = cv2.imdecode(np.frombuffer(a_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    b = cv2.imdecode(np.frombuffer(b_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
    if a.shape != b.shape:
        b = cv2.resize(b, (a.shape[1], a.shape[0]))
    return round(float(structural_similarity(a, b)), 4)


def qr_region_bbox(img_bgr, payload):
    """Locate the QR via zxing corner positions; pad for the quiet zone."""
    import zxingcpp
    results = zxingcpp.read_barcodes(img_bgr, formats=zxingcpp.BarcodeFormat.QRCode)
    for res in results:
        if res.text == payload:
            p = res.position
            xs = [p.top_left.x, p.top_right.x, p.bottom_left.x, p.bottom_right.x]
            ys = [p.top_left.y, p.top_right.y, p.bottom_left.y, p.bottom_right.y]
            pad = int(0.18 * (max(xs) - min(xs)))  # cover the quiet zone
            h, w = img_bgr.shape[:2]
            return (max(0, min(xs) - pad), max(0, min(ys) - pad),
                    min(w, max(xs) + pad), min(h, max(ys) + pad))
    return None


def replay_test(photo_a_path, photo_b_path, keystore, registry, th):
    """Spec §12: copy of the whole image must resolve as share A and verify;
    the QR region pasted onto photo B must NOT verify as photo A."""
    fused_a = fuse(cv2.imread(photo_a_path), PARAMS)
    ok, buf = cv2.imencode(".jpg", fused_a.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    fused_bytes = buf.tobytes()
    share = _issue(fused_bytes, keystore, registry)

    out = {}
    # Case 1: whole-image copy (byte-identical).
    r = verify_photo(share.opaque_resolution_id, fused_bytes, registry, keystore, th)
    out["whole_copy"] = {"expected": "AUTHENTIC_EXACT", "got": r.status}

    # Case 2: recompressed whole copy (what a messaging hop produces).
    img = cv2.imdecode(np.frombuffer(fused_bytes, np.uint8), cv2.IMREAD_COLOR)
    ok, buf75 = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 75])
    r = verify_photo(share.opaque_resolution_id, buf75.tobytes(), registry,
                     keystore, th)
    out["recompressed_copy"] = {"expected": "AUTHENTIC_DERIVED", "got": r.status}

    # Case 3: QR region cut from fused A, pasted onto photo B.
    bbox = qr_region_bbox(fused_a.image, fused_a.payload)
    assert bbox is not None, "could not locate QR region in fused image"
    x0, y0, x1, y1 = bbox
    qr_patch = fused_a.image[y0:y1, x0:x1]
    photo_b = cv2.imread(photo_b_path)
    canvas = cv2.resize(photo_b, (fused_a.image.shape[1], fused_a.image.shape[0]))
    canvas[y0:y1, x0:x1] = qr_patch
    ok, buf_b = cv2.imencode(".jpg", canvas, [cv2.IMWRITE_JPEG_QUALITY, 92])
    spliced = buf_b.tobytes()

    verdicts = decode_all(cv2.imdecode(np.frombuffer(spliced, np.uint8),
                                       cv2.IMREAD_COLOR), fused_a.payload)
    r = verify_photo(share.opaque_resolution_id, spliced, registry, keystore, th)
    out["qr_spliced_onto_photo_b"] = {
        "qr_still_decodes_to_share_a": bool(verdicts["zxing"] or verdicts["pyzbar"]),
        "expected_binding": "CONTENT_MODIFIED",
        "got": r.status,
        "changed_regions_flagged": len(r.evidence.get("changed_regions", [])),
    }
    out["pass"] = (out["whole_copy"]["got"] == "AUTHENTIC_EXACT"
                   and out["recompressed_copy"]["got"] == "AUTHENTIC_DERIVED"
                   and out["qr_spliced_onto_photo_b"]["got"] == "CONTENT_MODIFIED"
                   and out["qr_spliced_onto_photo_b"]["qr_still_decodes_to_share_a"])
    return out


def survival_matrix(photo_paths, keystore, registry, th):
    rows = []
    for path in photo_paths:
        fused = fuse(cv2.imread(path), PARAMS)
        ok, buf = cv2.imencode(".jpg", fused.image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        fused_bytes = buf.tobytes()
        share = _issue(fused_bytes, keystore, registry)
        cases = [("as_delivered", fused_bytes)]
        rng = np.random.default_rng(7)
        for t in BENIGN:
            try:
                out = t.apply(fused_bytes, rng)
            except Exception as e:
                rows.append({"photo": Path(path).stem, "transform": t.name,
                             "qr_zxing": None, "qr_pyzbar": None, "qr_opencv": None,
                             "binding_status": f"transform_error: {e}",
                             "ssim_vs_delivered": None})
                continue
            if out is not None:
                cases.append((t.name, out))
        for name, fn in CROPS:
            cases.append((name, fn(fused_bytes, rng)))
        for name, data in cases:
            img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            qr = decode_all(img, fused.payload)
            r = verify_photo(share.opaque_resolution_id, data, registry,
                             keystore, th)
            rows.append({"photo": Path(path).stem, "transform": name,
                         "qr_zxing": int(qr["zxing"]), "qr_pyzbar": int(qr["pyzbar"]),
                         "qr_opencv": int(qr["opencv"]),
                         "binding_status": r.status,
                         "ssim_vs_delivered": _ssim(fused_bytes, data)})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--photos", default="photos")
    ap.add_argument("--out", default="results/qr_survival")
    ap.add_argument("--n", type=int, default=4)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    th = Thresholds.load()
    keystore = DevKeyStore(out / "keys")
    keystore.generate(activate=True)
    registry = CredentialRegistry()

    photos = sorted(str(p) for p in Path(args.photos).iterdir()
                    if p.suffix.lower() in (".jpg", ".jpeg", ".png"))[:args.n]

    rows = survival_matrix(photos, keystore, registry, th)
    with open(out / "survival.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    replay = replay_test(photos[0], photos[1], keystore, registry, th)
    (out / "replay.json").write_text(json.dumps(replay, indent=2))

    print(f"wrote {len(rows)} survival rows -> {out}/survival.csv")
    print("replay:", json.dumps(replay, indent=2))


if __name__ == "__main__":
    main()
