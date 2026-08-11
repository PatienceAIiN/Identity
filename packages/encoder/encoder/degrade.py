"""Degradation matrix: what messaging platforms, cameras, and screens do to
an image between generation and scan.

Order matters and mirrors reality: geometric transforms (rotation, rescale)
and lighting happen at capture/display; JPEG recompression happens last, when
a platform re-encodes the upload.
"""

from dataclasses import dataclass

import cv2
import numpy as np

JPEG_QUALITIES = (95, 75, 50, 30)
ROTATIONS_DEG = (0, 10, 15, 20, 30)   # 15 added: the Phase 0 gate is ±15°
BRIGHTNESS = (0.6, 1.0, 1.4)
SCALES = (1.0, 0.5, 0.25)
BLUR_SIGMAS = (0.0, 1.0, 2.0)

NOMINAL = dict(jpeg_q=95, rotation=0, brightness=1.0, scale=1.0, blur=0.0)


@dataclass(frozen=True)
class Condition:
    jpeg_q: int = 95
    rotation: float = 0.0
    brightness: float = 1.0
    scale: float = 1.0
    blur: float = 0.0
    tag: str = ""  # which part of the matrix this condition belongs to


def axis_conditions() -> list[Condition]:
    """One-factor-at-a-time sweep: every value of each axis with the other
    axes at nominal."""
    conds = [Condition(tag="nominal")]
    for q in JPEG_QUALITIES:
        conds.append(Condition(jpeg_q=q, tag="jpeg"))
    for r in ROTATIONS_DEG[1:]:
        conds.append(Condition(rotation=r, tag="rotation"))
        conds.append(Condition(rotation=-r, tag="rotation"))
    for b in BRIGHTNESS:
        if b != 1.0:
            conds.append(Condition(brightness=b, tag="brightness"))
    for s in SCALES[1:]:
        conds.append(Condition(scale=s, tag="scale"))
    for g in BLUR_SIGMAS[1:]:
        conds.append(Condition(blur=g, tag="blur"))
    return conds


def gate_conditions() -> list[Condition]:
    """The Phase 0 gate: JPEG 75 + ±15° rotation + 50% scale, combined."""
    return [
        Condition(jpeg_q=75, rotation=15, scale=0.5, tag="gate"),
        Condition(jpeg_q=75, rotation=-15, scale=0.5, tag="gate"),
    ]


def stress_conditions() -> list[Condition]:
    """Realistic combined abuse: forwarded-through-a-messaging-app territory."""
    return [
        Condition(jpeg_q=50, scale=0.5, blur=1.0, tag="stress"),
        Condition(jpeg_q=30, scale=0.25, tag="stress"),
        Condition(jpeg_q=75, rotation=10, brightness=0.6, scale=0.5, tag="stress"),
        Condition(jpeg_q=75, rotation=20, brightness=1.4, scale=0.5, tag="stress"),
    ]


def all_conditions() -> list[Condition]:
    return axis_conditions() + gate_conditions() + stress_conditions()


def apply(img_bgr: np.ndarray, c: Condition) -> np.ndarray:
    out = img_bgr

    if c.rotation:
        h, w = out.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), c.rotation, 1.0)
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        nw, nh = int(h * sin + w * cos), int(h * cos + w * sin)
        m[0, 2] += nw / 2 - w / 2
        m[1, 2] += nh / 2 - h / 2
        out = cv2.warpAffine(out, m, (nw, nh), flags=cv2.INTER_LINEAR,
                             borderMode=cv2.BORDER_CONSTANT,
                             borderValue=(255, 255, 255))

    if c.scale != 1.0:
        h, w = out.shape[:2]
        out = cv2.resize(out, (max(1, int(w * c.scale)), max(1, int(h * c.scale))),
                         interpolation=cv2.INTER_AREA)

    if c.brightness != 1.0:
        out = np.clip(out.astype(np.float32) * c.brightness, 0, 255).astype(np.uint8)

    if c.blur > 0:
        out = cv2.GaussianBlur(out, (0, 0), sigmaX=c.blur)

    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, int(c.jpeg_q)])
    if not ok:
        raise RuntimeError("JPEG encode failed")
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)
