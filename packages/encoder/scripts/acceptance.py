"""Phase 1 acceptance run (CLAUDE.md build order #2): encode every portrait
in the acceptance set with the real v7 zero-knowledge payload shape and
report per-image decode confidence + face SSIM. Nothing is skipped: photos
where encoding fails or no face is found are rows, not omissions.

Gate reading: every encoded image must pass validate-before-return (>=85%
decode over conditions x available decoders); face-SSIM >= 0.90 is reported
as the visual-quality bar with the actual distribution.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from encoder import EncodeOptions, encode_photo, EncodeError  # noqa: E402

HARNESS = Path(__file__).resolve().parents[2] / "harness"
ZK_PAYLOAD = "pb.id/r/hK4mZq9TvW#Ab3xK9mQpZw4TfGhJk2LnPqRsTuVwXyZ01dEfGhIjKm"


def main():
    photo_dirs = [HARNESS / "photos", HARNESS / "photos_fresh"]
    photos = sorted({p for d in photo_dirs if d.exists()
                     for p in d.glob("*.jpg")}, key=lambda p: p.name)
    rows = []
    for p in photos:
        try:
            r = encode_photo(p.read_bytes(), ZK_PAYLOAD, EncodeOptions())
            rows.append({"photo": p.stem, "ok": True,
                         "decode_rate": r.decode_rate, "face_ssim": r.face_ssim,
                         "placement": r.placement, "escalations": r.escalations})
        except EncodeError as e:
            rows.append({"photo": p.stem, "ok": False, "error": str(e)})
        print(rows[-1])

    ok = [r for r in rows if r["ok"]]
    faces_ok = [r for r in ok if r["face_ssim"] >= 0.90]
    out = {
        "payload_shape": "v7 zero-knowledge URL (256-bit key, 64-bit id)",
        "n_photos": len(rows),
        "n_encoded_and_validated": len(ok),
        "n_failed": len(rows) - len(ok),
        "decode_rate_mean": round(sum(r["decode_rate"] for r in ok) / len(ok), 4),
        "decode_rate_min": min(r["decode_rate"] for r in ok),
        "face_ssim_mean": round(sum(r["face_ssim"] for r in ok) / len(ok), 4),
        "face_ssim_min": min(r["face_ssim"] for r in ok),
        "n_face_ssim_ge_0.90": len(faces_ok),
        "n_escalated": sum(1 for r in ok if r["escalations"] > 0),
        "rows": rows,
    }
    dest = Path(__file__).resolve().parent.parent / "acceptance_report.json"
    dest.write_text(json.dumps(out, indent=2))
    print(f"\n{len(ok)}/{len(rows)} validated; "
          f"decode mean {out['decode_rate_mean']:.1%} min {out['decode_rate_min']:.1%}; "
          f"face-SSIM mean {out['face_ssim_mean']:.3f}, "
          f">=0.90 on {len(faces_ok)}/{len(ok)}")
    print(f"report -> {dest}")


if __name__ == "__main__":
    main()
