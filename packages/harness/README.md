# harness — Phase 0 decode-rate test rig

Measures the only thing that matters before any product code gets written:
**do photo-fused QR codes actually scan**, and at what cost to the face.

## Gate (CLAUDE.md §2)

≥ 85% decode at JPEG 75 / ±15° / 50% scale, at face-region SSIM ≥ 0.90.
Decode rate averages photos × both rotation signs × all three decoders.

## Pipeline

1. `fuse.py` — fusion candidate generator. Minimal-intervention halftone
   rendering: full-contrast function patterns, per-module center dots with the
   minimum alpha that crosses a binarization threshold, alpha capped over the
   face-protection mask (EC level H absorbs the loss). Grid placement is
   selected empirically per photo: every candidate placement is rendered,
   checked for decode survival under JPEG75/50%, and the best face SSIM wins.
2. `degrade.py` — JPEG quality / rotation / brightness / downscale / blur,
   one-factor-at-a-time axes plus gate and combined stress conditions.
3. `decode.py` — zxing-cpp, pyzbar (ZBar), OpenCV QRCodeDetector. Success =
   exact payload match.
4. `metrics.py` — full SSIM + face-region SSIM.
5. `sweep.py` / `analyze.py` — orchestration, CSV, gate verdict, frontier plot.

## Run

```bash
cd packages/harness
export LD_LIBRARY_PATH=/home/harsh/photobind/.venv/lib   # locally-built libzbar
../../.venv/bin/python scripts/fetch_photos.py            # once: test portraits
../../.venv/bin/python -m harness.sweep --workers 8
../../.venv/bin/python -m harness.analyze                 # verdict + frontier.png
```

Outputs land in `results/`: `results.csv` (one row per photo × params ×
condition), `samples/` (fused images for eyeballing), `frontier.png`.

## Notes

- Face detection (YuNet) runs in memory per image; nothing biometric is
  persisted (§8 hard rule).
- Test photos are public-domain portraits (US government / NASA works) fetched
  from Wikimedia Commons; they are local test data, gitignored, not shipped.
- Payload capacity warning for Phase 2: v2-H fits 14 bytes, v3-H fits 24.
  `https://domain/r/{id}#{256-bit key}` (~70 chars) needs ~v6-H, which
  contradicts §3's "version 2–3 only". The sweep encodes short-domain
  id-only payloads (14/24 bytes). This conflict needs a product decision.
