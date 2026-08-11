# Standards context — how this project relates to established provenance work

This project did not invent QR codes, dynamic/revocable QR resolution,
encrypted payloads, perceptual hashing, image tamper detection, invisible
watermarking, or content-provenance binding. All of these are established
technologies or active research areas. This document places our photo-binding
evidence in that landscape and states what we do **not** attempt to replace.

Reviewed against: **C2PA Specification v2.2 (May 2025)**,
https://spec.c2pa.org/specifications/specifications/2.2/specs/C2PA_Specification.html
(checked 2026-08-10).

## C2PA hard bindings

C2PA hard bindings are "one or more cryptographic hashes that uniquely
identify the entire asset or a portion thereof" (byte-range or box-based
hashes embedded in a signed manifest). Our **Layer 1** (SHA-256 over the
stored byte stream, inside an Ed25519-signed binding record) is the same
*idea* — exact integrity — but is **not** a C2PA hard binding: we do not
produce C2PA manifests, JUMBF boxes, or C2PA claim signatures.

## C2PA soft bindings

C2PA soft bindings are content identifiers that are "not statistically
unique, such as a fingerprint, or embedded as an invisible watermark",
used to match derived assets and renditions where raw bytes differ. Our
**Layer 2/3** (256-bit DCT pHash + staggered-grid tile evidence) is a
fingerprint-style soft binding in C2PA's vocabulary. C2PA maintains a
registry of soft-binding algorithms; ours is not registered.

## Durable Content Credentials

C2PA v2.2 defines a Durable Content Credential as a credential recoverable
via soft bindings from a manifest repository even when metadata is stripped.
Our architecture reaches the same goal differently: recovery travels **in
the pixels via the QR code** (survives metadata stripping by construction),
and the binding record lives server-side, keyed by the credential the QR
resolves. The trade: we depend on our resolution service being reachable;
C2PA manifests are self-contained but strippable.

## Invisible watermarking

Watermarking (e.g., the systems used commercially for broadcast and stock
imagery) embeds an imperceptible signal; it survives many transformations
and needs no visible artifact. We deliberately use a **visible** QR carrier
instead, because Pillar-1 requirements (any standard scanner, no special
reader) rule out watermarks — a phone camera cannot read one. A watermark
as an *additional* evidence layer is a plausible future experiment,
particularly for the local-blur/pixelation blind spots measured in
`packages/harness/results/binding_eval/`; it is **not implemented**.

## How this project differs (and what it does not replace)

- We do not replace C2PA. A C2PA manifest asserts *provenance history*
  signed at creation/edit time by participating tools. Our binding asserts
  a narrower fact: "this credential was issued for this image, and the
  candidate image is/is not consistent with it" — plus revocation and
  per-share tracing, which C2PA does not aim to provide.
- Interop path (investigated, not implemented): our binding record could be
  carried as an assertion inside a C2PA manifest, and our fingerprint could
  be registered as a soft-binding algorithm. Both require producing real
  C2PA manifests and passing an actual C2PA validator. **No C2PA
  compatibility or compliance is claimed**; nothing here has been run
  through a C2PA validator.

## References

- C2PA Specification v2.2 (May 2025) — spec.c2pa.org (link above).
- ISO/IEC 18004 — QR Code bar code symbology specification (the QR standard
  our encoder targets via `segno`).
- C. Zauner, "Implementation and Benchmarking of Perceptual Image Hash
  Functions", Master's thesis, Upper Austria University of Applied Sciences,
  2010 — the DCT pHash construction used in Layer 2/3.

Claims in this document are limited to what the cited documents say and what
this repository's tests measure.
