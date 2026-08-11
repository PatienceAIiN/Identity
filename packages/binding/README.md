# binding — photo-bound credential evidence

Answers one question with measurable evidence: **"does this credential
correspond to this image?"** It never claims more than the evidence supports.

## Evidence layers

| Layer | What | Algorithm | Survives | Breaks on |
|---|---|---|---|---|
| 1 | Exact digest | SHA-256 over stored bytes | nothing but byte-identity | any resave |
| 2 | Global perceptual fingerprint | DCT pHash, 256-bit (`phash256-v1`) | recompression, resize, mild tone changes (measured, see harness) | content change, crop (measured) |
| 3 | Region evidence | 4×4 tile pHash, 64-bit/tile | as layer 2, localizes change | crop/reframe (grid alignment lost — known limitation) |

No face embeddings, no landmark templates, no biometric persistence,
anywhere in this package.

## Statuses (fail-closed)

`AUTHENTIC_EXACT` · `AUTHENTIC_DERIVED` · `CONTENT_MODIFIED` ·
`INSUFFICIENT_EVIDENCE` · `CANNOT_VERIFY_PHOTO` · `REVOKED` · `EXPIRED` ·
`INVALID_CREDENTIAL` · `UNKNOWN_KEY`

- A resolving QR alone is never authentic.
- `AUTHENTIC_DERIVED` ≠ "original file". It means: consistent with the
  registered image under the transformations this system was tested against.
- Distances between the derived and modified thresholds stay
  `INSUFFICIENT_EVIDENCE`. Uncertainty never rounds up.

## Identity model (no ambiguous "id")

```
PHOTO (photo_id)
  └── CREDENTIAL (credential_id) — Ed25519-signed binding record
        ├── SHARE (share_id, "LinkedIn")   → OPAQUE_RESOLUTION_ID (in QR)
        ├── SHARE (share_id, "Email")      → OPAQUE_RESOLUTION_ID (in QR)
        └── SHARE (share_id, "Conference") → OPAQUE_RESOLUTION_ID (in QR)
```

`SIGNING_KEY_ID` identifies the key that signed a binding. Opaque resolution
IDs are `secrets.token_urlsafe(16)` — CSPRNG, never derived from anything.

## Signed binding record

Serialization: JSON, UTF-8, sorted keys, compact separators. Signature is
Ed25519 over `b"photobind:binding:v1\x00" + canonical_json`. The signing key
id is inside the signed payload *and* the envelope; a mismatch fails.
Verification: reconstruct canonical bytes → check key id consistency →
resolve key (unknown/revoked keys fail closed) → verify.

Key lifecycle (`keys.py`): content-derived key ids, one active key, rotation
keeps old keys verify-only, key revocation invalidates their signatures.
**The file-backed store is development/test key management, not a KMS.**

## Thresholds

`verify.py` loads `thresholds.json`, produced by the harness calibration run
(calibration set only, disjoint from held-out evaluation — see
`packages/harness`). Without that artifact, defaults are deliberately
paranoid and labeled uncalibrated.
