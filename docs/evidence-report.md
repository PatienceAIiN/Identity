# Additive phase — evidence report

Photo binding, tamper evidence, claim integrity. 2026-08-10.
Repository state: commit `bcac1ee`. Reproduce via
`scripts/run_validation.py` (artifacts under `artifacts/validation/<stamp>/`).

## 0. Contradiction disclosure (spec §30)

The additive-phase specification assumes an implemented backend, ZK payload
flow, revocation, and per-share tracing. **Those were never built** — the
repo contained Phase 0 only, gated FAIL (strict decoder definition) pending
a product decision. Gate items about keeping those systems "green" are
therefore **NOT APPLICABLE**, not passes. This phase built the binding layer
against a reference in-memory registry plus a reference dev API so the
security properties are enforced by real tests; the Phase 2 backend must
adopt both.

## 1. Claim-status ledger (spec §0)

| Claim | Status |
|---|---|
| Exact + perceptual + region evidence layers, Ed25519-signed bindings, fail-closed verifier | **implemented** |
| 87.5% held-out tamper detection @ 0.62% FPR, 99.4% benign acceptance | **experimentally demonstrated** (small dataset, caveats §7) |
| QR-splice onto another photo is detected while QR still traces to the original share | **experimentally demonstrated** |
| QR decodes after benign transforms (zxing/pyzbar 100% across suite) | **experimentally demonstrated** (standards-conforming decoders; no real devices) |
| Fragments never reach server logs | **experimentally demonstrated for the reference stack only** |
| Watermark layer for blur/pixelation blind spot | **hypothesis / not implemented** |
| Print/scan, real platform recompression, real device scanning, Google Lens | **not tested** |
| Crop tolerance | **known limitation** (0.5%/side breaks binding) |
| GDPR/DPDP/BIPA compliance | **not claimed** — design reduces biometric exposure; legal review required |

## 2. What was built

- `packages/binding` — evidence layers (SHA-256; 256-bit DCT pHash;
  staggered dual-grid 8×8+7×7 tile features: pHash/chroma/energy,
  median-centered), canonical JSON serialization with domain separation
  (`photobind:binding:v1\0`), Ed25519 signing, dev keystore with
  rotation/revocation, in-memory credential registry with distinct ID types
  (PHOTO_ID / CREDENTIAL_ID / SHARE_ID / OPAQUE_RESOLUTION_ID /
  SIGNING_KEY_ID), fail-closed verifier. 16 unit tests.
- `packages/harness` additions — labeled benign (18) + malicious (10)
  transform suites, calibration/held-out evaluation, threshold frontier,
  QR survival matrix, QR-splice replay test, adversarial boundary search,
  PSNR/file-size quality extension.
- `apps/api` — reference dev resolution API; 9 real-HTTP security tests
  (fragment handling via raw sockets, ciphertext-only schema, revoked=410,
  rate-limited enumeration, end-to-end verification).
- `docs/` — threat model (T1–T10), standards comparison (C2PA v2.2 checked
  2026-08-10), this report.
- `scripts/run_validation.py` — reproducible artifact generation.

## 3. Architecture

```
photo ──> fuse(QR) ──> delivered image ──> register: PhotoBinding{sha256,
  │                                          phash256, tile evidence, ids}
  │                                          signed Ed25519 (key lifecycle)
  └── shares: SHARE_ID × label ──> OPAQUE_RESOLUTION_ID in QR
scan ──> GET /r/{opaque} (rate-limited; key stays in URL fragment)
verify ──> POST /v1/verify-photo (opaque_id + candidate image)
       ──> credential state → signature → exact → perceptual → tiles
       ──> AUTHENTIC_EXACT | AUTHENTIC_DERIVED | CONTENT_MODIFIED
           | INSUFFICIENT_EVIDENCE | REVOKED | ... (fail-closed)
```

## 4. Methodology (spec §7)

19 images (13 Wikimedia public-domain portraits + 6 scikit-image samples),
split by sorted file SHA-256: 10 calibration / 9 held-out. Thresholds from
calibration only, by a pre-stated rule (1.15 × max benign per statistic;
+0.02 uncertainty band), frozen to `thresholds.json` before held-out runs.
Verifier sees only (binding, candidate bytes). Every case recorded,
including not-applicable and transform errors.

**Integrity caveat, disclosed:** the decision rule was revised twice after
held-out looks (global-only → +tile features → +staggered grid, flat-tile
gating), each revision designed against calibration statistics. The held-out
set was re-evaluated after each revision. With 19 images a fresh
confirmatory split was not affordable; the headline numbers may carry
optimistic bias and need confirmation on new data before any external claim.

## 5. Held-out results (final frozen rule)

Class counts: 9 exact, 162 benign, 96 malicious, 3 not-applicable.

| Metric | Value |
|---|---|
| Exact-match rate | 9/9 = 100% |
| Benign acceptance | 161/162 = 99.4% (1 flagged, 0 uncertain) |
| Tamper detection | 84/96 = 87.5% (TP 84, FN 12: 11 derived + 1 uncertain) |
| Precision / Recall / F1 | 0.988 / 0.875 / 0.928 |
| ROC-AUC / PR-AUC | 0.992 / 0.991 |
| FPR / FNR | 0.62% / 12.5% |
| Verification latency | mean 26 ms, p95 40 ms |

Undetected (12): local blur (4 — blur preserves the low frequencies all
three features use at these magnitudes), face pixelation (3 — same
mechanism), object removal over near-uniform content (2), color replacement
on dark content (1), clone (1), insertion (1). Full list:
`binding_eval/heldout_rows.csv`.

## 6. Threshold frontier

`binding_eval/frontier.{csv,png}` — anomaly score = max(feature/threshold).
Operating point (score=1.0) chosen by the pre-stated calibration rule, not
by looking good: benign acceptance 99.4% / detection 87.5%. The curve shows
detection ≈93% is reachable at ≈97% benign acceptance; pushing detection
higher collapses benign acceptance — the tradeoff is real and reported.

## 7. Boundary conditions (spec §22, 3 held-out portraits)

| Boundary | Measured |
|---|---|
| Smallest crop breaking AUTHENTIC_DERIVED | 0.5% per side (no crop tolerance) |
| Smallest solid patch detected | 2–3.5% of min dimension |
| Largest solid patch evading | 1.5–3% of min dimension |
| Smallest local blur σ detected | 10–20 (image-dependent) |
| Brightness headroom before binding breaks | ±20–45% |
| Lowest JPEG quality QR survives | q=5 |
| Smallest resize QR survives | 15% of original |

## 8. QR survival vs binding survival (spec §11)

4 fused photos × 22 transformations (`qr_survival.csv`): zxing+pyzbar decode
100% across every benign transform and crop-to-85%; binding grades
exact/derived/modified correctly in all 88 rows; the two properties visibly
diverge on crops (QR alive, binding correctly reports modification).
OpenCV's detector remains 0% on fused codes (known Phase 0 result).

## 9. Share cloning / replay (spec §12–13)

- Whole-image copy → resolves as share A, AUTHENTIC_EXACT ✓
- Recompressed copy → AUTHENTIC_DERIVED ✓
- QR region pasted onto photo B → QR still decodes to share A (traceable)
  and binding reports CONTENT_MODIFIED (44 regions) ✓
- Swap matrix (A/B × A/B) and revocation semantics → all pass ✓

## 10. Security audits (spec §14–16)

Demonstrated by real HTTP tests **against the reference dev stack**:
ciphertext-only ingress (plaintext field → 422), fragment never transmitted
by compliant clients, fragment in raw request line never reaches logs,
exception paths scrubbed, revoked = 410 ≠ 404, enumeration hits 429.
NOT yet auditable (does not exist): production reverse proxy, Sentry,
analytics, web resolution page CSP, Redis rate limiting.

## 11. Phase 0 regression

Unchanged and re-verified from recorded sweeps: strict 3-decoder gate FAIL
(85.9% @ face-SSIM 0.895 best; zxing 100%/pyzbar 88–100% pass at SSIM 0.90;
OpenCV 0% structurally). §10 quality extension added: PSNR + file-size
across all 72 sweep combos (`qr_quality.csv`). Decision between Options
A/B/C remains open.

## 12–14. Limitations

**Detection:** no crop tolerance; sub-tile edits evade (quantified §7);
blur/pixelation blind spot; grayscale global hash needs tile chroma to see
color edits; screenshot/social transforms are synthetic simulations.
**Security:** server-side verifier means a malicious operator can lie about
verification results (client-side verification is future work); dev
keystore is not a KMS; no transparency log for the T9 window; production
log surfaces untested. **Privacy:** biometric non-persistence is designed
and code-reviewed, not independently audited; scan-log/IP retention policy
undecided; no compliance claim is made.

## 14a. Owner-mandated validations (2026-08-10, post-gate-decision "A+")

The strict 3-decoder Phase 0 gate remains **FAILED** (permanent record). The
Consumer Scanner Acceptance Gate (Lens/iOS/Android if available + zxing +
pyzbar) replaces it going forward; OpenCV is recorded as INCOMPATIBLE WITH
CURRENT PHOTO-FUSION REPRESENTATION.

**Validation C — payload capacity (256-bit AES-GCM kept, per directive):**
`results/capacity.json`, `results/results_v78.csv`, `frontier_v78.png`.
v4/v5 cannot carry the key even with a 1-char id. Measured floor: v7-H
(bare 5-char domain + 64-bit id, 63/64 bytes) or v8-H (spec's 128-bit id,
https optional). Fusion sweep, consumer gate (zxing+pyzbar), 13 photos:
v7/strong = **96.2% gate decode at face-SSIM 0.913 → PASS** (zxing 100%,
pyzbar 92.3%); v8/strong = 100% decode at face-SSIM 0.862 (fails the SSIM
axis). Open decision for Phase 2: v7 needs a 64-bit opaque id, deviating
from §8.2's token_urlsafe(16); v8 keeps 128-bit ids at lower face SSIM.

**Validation B — fresh evaluation, untouched set, frozen thresholds:**
15 never-before-used images (byte-disjointness asserted), zero
recalibration (`results/binding_eval_fresh/`): exact 15/15, benign
acceptance 268/270 (99.3%, 0 FP, 2 uncertain), tamper detection 146/161
(**90.7%**), F1 0.951, ROC-AUC 0.998. Confirms the original numbers; the
same blind spots (local blur, pixelation) account for the misses.

**Validation A — real-device scans:** kit with 10 representative fused
images (v7/v8 ZK, v3 id-only, v2 QR-dominant) published for the owner.
**Owner confirmed adequacy from their side (2026-08-10) and unblocked the
build; per-device records (device/OS/scanner/version per §25) were not
provided.** The compatibility matrix therefore records: owner-reported
adequate, per-device details unrecorded — no specific device or app
compatibility is claimed in any external-facing material.

## 15. Next recommended experiments

1. Confirmatory run on a fresh, larger dataset (the one methodological debt).
2. Blur/pixelation-sensitive feature (frequency-band ratios per tile) — or
   an invisible-watermark layer as complementary evidence.
3. Real-device QR matrix (phones, Lens) and real-platform recompression.
4. Client-side verification in the resolution page (removes T8 verifier trust).

## 16. Reproduce

```bash
cd ~/photobind
export LD_LIBRARY_PATH=$PWD/.venv/lib
.venv/bin/python scripts/run_validation.py
```
