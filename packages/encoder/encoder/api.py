"""Public entry point: encode_photo().

Contract (CLAUDE.md §3.6): never return an unvalidated image. Every result
carries a measured decode-confidence report; if no parameter escalation
reaches the required rate, EncodeValidationError is raised instead of
returning something that might not scan.

Validation decoders: zxing-cpp always; pyzbar when the system libzbar
exists. OpenCV QRCodeDetector is excluded — measured 0% on fused codes
(see project compatibility matrix). Real-device scanning is a separate,
explicit validation step and is never implied by this report.

Biometric rule (§8): face detection runs in memory for the duration of one
encode call and is discarded. Nothing biometric is returned or persisted.
"""

from dataclasses import dataclass, field, asdict

import cv2
import numpy as np

from .decoders import available_decoders, decode_all
from .degrade import Condition, apply
from .fusion import FuseParams, FusedResult, fuse
from .metrics import ssim, ssim_face
from .qrspec import BYTE_CAPACITY_H

SUPPORTED_VERSIONS = tuple(sorted(BYTE_CAPACITY_H))


class EncodeError(ValueError):
    pass


class PayloadTooLargeError(EncodeError):
    pass


class NoFaceFoundError(EncodeError):
    """Kept for callers that catch it. encode_photo no longer raises this:
    photos without a face encode fine, they just have nothing to protect."""


class EncodeValidationError(EncodeError):
    """No parameter escalation reached the required decode rate."""


# Conditions the confidence report measures. The 'gate' pair mirrors the
# Phase 0 acceptance condition (JPEG 75 / ±15° / 50% scale).
VALIDATION_CONDITIONS = (
    ("as_delivered", Condition(jpeg_q=95)),
    ("jpeg75", Condition(jpeg_q=75)),
    ("gate_rot+15", Condition(jpeg_q=75, rotation=15, scale=0.5)),
    ("gate_rot-15", Condition(jpeg_q=75, rotation=-15, scale=0.5)),
    ("quarter_scale", Condition(jpeg_q=85, scale=0.25)),
)

# Escalation ladder: appearance degrades gracefully toward scannability.
_LADDER = (
    {},                                        # requested params as-is
    {"contrast": "strong"},
    {"contrast": "strong", "center_ratio": 0.6},
    {"contrast": "strong", "center_ratio": 0.75},
    {"contrast": "strong", "center_ratio": 0.9},  # QR-dominant last resort
)


@dataclass(frozen=True)
class EncodeOptions:
    contrast: str = "strong"
    alpha_protected: float = 0.45
    center_ratio: float = 0.5
    # How strongly the 4-module quiet ring is pushed to white. The ring is
    # required for scanning; how opaque it has to be is a measured question, and
    # every point of it costs visible photo at the border.
    quiet_alpha: float = 0.55
    canvas_px: int = 1110
    min_decode_rate: float = 0.85   # over conditions x available decoders
    allow_escalation: bool = True
    coverage: str = "full"          # "full" = code spans the whole image
                                    # "auto" = keep the face clearest


@dataclass
class EncodeResult:
    image_png: bytes
    version: int
    payload: str
    placement: str
    params: dict
    decode_confidence: dict          # condition -> {decoder: bool}
    decode_rate: float
    decoders_used: tuple[str, ...]
    image_ssim: float                # whole-image visual quality
    face_ssim: float | None          # None when the photo has no face.
                                     # Visual quality only — NOT authentication.
    has_face: bool
    escalations: int

    def summary(self) -> dict:
        d = asdict(self)
        d.pop("image_png")
        return d


def choose_version(payload: str) -> int:
    n = len(payload.encode("utf-8"))
    for v in SUPPORTED_VERSIONS:
        if BYTE_CAPACITY_H[v] >= n:
            return v
    raise PayloadTooLargeError(
        f"payload is {n} bytes; EC-H caps at {BYTE_CAPACITY_H[max(SUPPORTED_VERSIONS)]} "
        f"bytes (version {max(SUPPORTED_VERSIONS)})")


def _confidence(image: np.ndarray, payload: str) -> tuple[dict, float]:
    report = {}
    hits = total = 0
    for name, cond in VALIDATION_CONDITIONS:
        verdicts = decode_all(apply(image, cond), payload)
        report[name] = verdicts
        hits += sum(verdicts.values())
        total += len(verdicts)
    return report, hits / total


def encode_photo(photo_bgr: np.ndarray | bytes, payload: str,
                 options: EncodeOptions = EncodeOptions()) -> EncodeResult:
    if isinstance(photo_bgr, (bytes, bytearray)):
        photo_bgr = cv2.imdecode(np.frombuffer(photo_bgr, np.uint8),
                                 cv2.IMREAD_COLOR)
    if photo_bgr is None:
        raise EncodeError("input is not a decodable image")

    version = choose_version(payload)
    ladder = _LADDER if options.allow_escalation else (_LADDER[0],)
    best_failure = None

    for i, override in enumerate(ladder):
        params = FuseParams(
            version=version,
            contrast=override.get("contrast", options.contrast),
            alpha_protected=options.alpha_protected,
            center_ratio=override.get("center_ratio", options.center_ratio),
            quiet_alpha=options.quiet_alpha,
            canvas_px=options.canvas_px, coverage=options.coverage)
        fused: FusedResult = fuse(photo_bgr, params, payload=payload)
        report, rate = _confidence(fused.image, payload)
        if rate >= options.min_decode_rate:
            ok, buf = cv2.imencode(".png", fused.image)
            if not ok:
                raise EncodeError("PNG encode failed")
            return EncodeResult(
                image_png=buf.tobytes(), version=version, payload=payload,
                placement=fused.placement, params=params.to_dict(),
                decode_confidence=report, decode_rate=round(rate, 4),
                decoders_used=available_decoders(),
                image_ssim=round(ssim(fused.reference, fused.image), 4),
                face_ssim=(round(ssim_face(fused.reference, fused.image,
                                           fused.face_bbox), 4)
                           if fused.has_face else None),
                has_face=fused.has_face,
                escalations=i)
        best_failure = (rate, params.label())

    rate, label = best_failure
    raise EncodeValidationError(
        f"decode rate {rate:.0%} < required {options.min_decode_rate:.0%} "
        f"after {len(ladder)} attempts (last: {label}); refusing to return "
        f"an unvalidated image")
