# Phase 0 report — decode-rate vs face-SSIM frontier

**Date:** 2026-08-10 · **Test set:** 13 public-domain portraits (varied skin
tones/lighting) · **Sweep:** 936 fusions (2 QR versions × 3 contrast levels ×
3 protection alphas × 4 center ratios × 13 photos), 25 degradation conditions
each, 3 decoders → 23,400 result rows.

## Gate: ≥85% decode at JPEG 75 / ±15° / 50% scale, at face SSIM ≥ 0.90

**Verdict: FAIL under the strict definition** (decode rate = mean over all
three decoders). Best combo: 85.9% decode at face SSIM 0.895 — short on SSIM
by 0.005, and only reachable with near-solid modules.

**The failure is entirely OpenCV's QRCodeDetector.** The frontier is bimodal:

| Rendering | face SSIM | zxing | pyzbar | OpenCV | 3-mean | any |
|---|---|---|---|---|---|---|
| Photo-dominant (v2, cr 0.5, medium) | **0.902** | 100% | 92% | 0% | 64% | 100% |
| Photo-dominant (v3, cr 0.5, strong) | 0.895 | 100% | 100% | 0% | 67% | 100% |
| QR-dominant (v2, cr 0.9, strong) | 0.895 | 100% | 100% | 58% | **86%** | 100% |

- **zxing-cpp: 100% at the gate condition in every one of the 72 param
  combos.** pyzbar: 88–100% on all cr ≥ 0.5 combos.
- **OpenCV decodes 0% of any halftone-fused code — including pristine,
  undegraded renders** — while decoding a plain QR of the same version/scale
  perfectly (verified for both `QRCodeDetector` and `QRCodeDetectorAruco`,
  OpenCV 5.0). Its decoder samples whole modules, so any rendering that keeps
  photo detail inside module interiors breaks it structurally. This is
  inherent to the photo-QR technique, not a tuning miss: it will fail the
  same way on Visualead-style output.
- Under an "any decoder" or "zxing+pyzbar" definition, the gate **passes
  everywhere** with huge margin at face SSIM 0.90.

## What the harness taught us

1. **Grid placement is the dominant lever for face SSIM** — worth +0.15–0.37.
   Full-bleed grids put the full-contrast timing row across the eyes. The
   fusion engine renders 16 candidate placements/scales and keeps the best
   face SSIM that still decodes; large centered faces get a 0.62-scale code
   anchored away from the face.
2. **Minimal-intervention rendering:** per-module center dots with the minimum
   alpha to cross a binarization threshold. alpha_protected (0.25→0.65)
   barely moves either axis once placement is right.
3. **Axis robustness (photo-dominant, v3/strong/cr0.5, zxing+pyzbar):** 100%
   at every JPEG quality down to 30, every rotation to ±30°, brightness
   0.6–1.4×, blur σ≤2. The only soft spot is 25% downscale (~280px), where
   pyzbar drops; zxing survives.
4. **Payload capacity conflict (Phase 2 decision needed):** v2-H fits 14
   bytes, v3-H fits 24. `https://domain/r/{id}#{256-bit-key}` (~70 chars)
   needs ~v6-H, which §3 forbids. The sweep used short-domain id-only
   payloads (`pb.id/r/…`). Options: bigger version (worse scanning), 128-bit
   key (~v5), key-wrapping via the resolution page (weakens Pillar 1), or a
   very short domain + id-only QR with the key delivered another way.

## Decision required (per §2: the bar does not move silently)

- **Option A — redefine the gate's decoder set** to zxing + pyzbar (or
  any-of-three), on the grounds that OpenCV's detector is a library, not a
  consumer scanner, and structurally cannot read any photo-QR. Real-scanner
  validation (iOS camera, Google Lens, ML Kit) should replace it as the
  acceptance test. → Phase 0 passes today; proceed to Phase 1.
- **Option B — keep OpenCV in the gate** and ship QR-dominant renders
  (cr 0.9): the photo stays recognisable (SSIM 0.895) but the code region
  looks like a normal QR, and it still only reaches 86%/0.895 — a product
  change, not a threshold change.
- **Option C — stop.**

Artifacts: `results.csv`, `results_cr_high.csv`, `frontier.png`, `samples/`.
