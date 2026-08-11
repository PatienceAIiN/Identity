# encoder — QR/photo fusion engine

The canonical fusion implementation (Phase 1). The Phase 0 harness re-exports
these modules, so there is exactly one implementation; the harness remains the
measurement rig around it.

```python
from encoder import encode_photo, EncodeOptions

result = encode_photo(photo_bytes, "pb.id/r/hK4mZq9TvW#<43-char-key>")
result.image_png          # the validated fused image
result.decode_rate        # measured, over conditions x available decoders
result.decode_confidence  # per condition, per decoder
result.face_ssim          # visual-quality metric — NOT authentication
result.escalations        # how far up the appearance ladder validation went
```

## Guarantees

- **Never returns an unvalidated image.** Every result was decoded from
  degraded variants (as-delivered, JPEG 75, the Phase 0 gate pair
  JPEG75/±15°/50%, and 25% scale) by every available decoder; below the
  required rate the encoder escalates appearance (contrast → larger center
  dots → QR-dominant) and, if the ladder is exhausted, raises
  `EncodeValidationError` instead of returning.
- **QR version is chosen from the payload** (smallest EC-H fit, v1–8).
  Fixed patterns render at full contrast; the 4-module quiet zone is
  enforced; placement is searched empirically to keep full-contrast
  structures off the face.
- **No biometric persistence** (§8): face detection (YuNet, bundled ONNX)
  lives in memory for one call; results carry no landmarks, boxes, or masks.

## Validation decoders

zxing-cpp (required) and pyzbar (optional — needs system libzbar; on this
machine: `LD_LIBRARY_PATH=<repo>/.venv/lib`). The confidence report names
the decoders actually used. OpenCV's QRCodeDetector is excluded: measured
0% on fused codes (project compatibility matrix — INCOMPATIBLE WITH CURRENT
PHOTO-FUSION REPRESENTATION). **Library decode rates are not device
compatibility claims**; real-device scanning is a separate validation step.

## Acceptance

`scripts/acceptance.py` encodes the full portrait set with the production
v7 zero-knowledge payload shape and writes `acceptance_report.json`
(per-image decode rate, face SSIM, escalations, failures included).
