"""Transformation classes for tamper-evidence evaluation.

Every transformation here is SYNTHETIC: produced by local code, not by real
messaging platforms, printers, or cameras. Names say precisely what was done.
Print/scan and real-platform recompression are NOT TESTED (no hardware /
no platform access in this environment) and are recorded as such.

Transforms take and return encoded image BYTES — the unit a real verifier
receives. Malicious edits re-encode at JPEG 92 so the content change, not
compression noise, is what a detector must find.

kind: "benign" (should verify as derived) | "malicious" (should be detected)
"""

from dataclasses import dataclass
from typing import Callable

import cv2
import numpy as np


@dataclass(frozen=True)
class Transform:
    name: str
    kind: str  # benign | malicious
    fn: Callable[[bytes, np.random.Generator], bytes | None]

    def apply(self, data: bytes, rng: np.random.Generator) -> bytes | None:
        """None means 'not applicable to this image' (recorded, not hidden)."""
        return self.fn(data, rng)


def _decode(data: bytes) -> np.ndarray:
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("undecodable test input")
    return img


def _jpeg(img: np.ndarray, q: int = 92) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    return buf.tobytes()


def _png(img: np.ndarray) -> bytes:
    ok, buf = cv2.imencode(".png", img)
    assert ok
    return buf.tobytes()


# --------------------------------------------------------------------------
# benign
# --------------------------------------------------------------------------

def _recompress(q):
    return lambda d, r: _jpeg(_decode(d), q)


def _resize(scale):
    def fn(d, r):
        img = _decode(d)
        h, w = img.shape[:2]
        out = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_AREA)
        return _jpeg(out)
    return fn


def _strip_metadata(d, r):
    """Byte-level JPEG APPn/COM segment removal — pixel data untouched, so
    this tests the Layer 1 (breaks) vs Layer 2 (must not break) distinction.
    Returns None for non-JPEG input."""
    if d[:2] != b"\xff\xd8":
        return None
    out = bytearray(b"\xff\xd8")
    i = 2
    while i < len(d) - 1:
        if d[i] != 0xFF:
            out.extend(d[i:])  # entropy-coded data begins; copy the rest
            break
        marker = d[i + 1]
        if marker == 0xDA:  # start of scan: copy the rest verbatim
            out.extend(d[i:])
            break
        seg_len = int.from_bytes(d[i + 2:i + 4], "big")
        if 0xE0 <= marker <= 0xEF or marker == 0xFE:  # APPn / COM: drop
            i += 2 + seg_len
        else:
            out.extend(d[i:i + 2 + seg_len])
            i += 2 + seg_len
    return bytes(out)


def _to_png(d, r):
    return _png(_decode(d))


def _png_roundtrip_jpeg(d, r):
    return _jpeg(_decode(_png(_decode(d))), 90)


def _brightness(factor):
    return lambda d, r: _jpeg(np.clip(_decode(d).astype(np.float32) * factor,
                                      0, 255).astype(np.uint8))


def _contrast(factor):
    def fn(d, r):
        img = _decode(d).astype(np.float32)
        out = np.clip((img - 128.0) * factor + 128.0, 0, 255).astype(np.uint8)
        return _jpeg(out)
    return fn


def _sharpen(d, r):
    img = _decode(d).astype(np.float32)
    blur = cv2.GaussianBlur(img, (0, 0), 1.2)
    out = np.clip(img + 0.6 * (img - blur), 0, 255).astype(np.uint8)
    return _jpeg(out)


def _mild_blur(d, r):
    return _jpeg(cv2.GaussianBlur(_decode(d), (0, 0), 0.6))


def _screenshot_sim(d, r):
    """SYNTHETIC screenshot simulation: rescale to a 1080-wide viewport and
    re-encode as PNG->JPEG80. Not a real device screenshot."""
    img = _decode(d)
    h, w = img.shape[:2]
    s = 1080 / w
    img = cv2.resize(img, (1080, int(h * s)), interpolation=cv2.INTER_AREA)
    return _jpeg(_decode(_png(img)), 80)


def _social_sim(d, r):
    """SYNTHETIC social-media-style recompression: resize 0.8 + JPEG 70.
    Not tested on any real platform."""
    img = _decode(d)
    h, w = img.shape[:2]
    img = cv2.resize(img, (int(w * 0.8), int(h * 0.8)), interpolation=cv2.INTER_AREA)
    return _jpeg(img, 70)


BENIGN = [
    Transform("jpeg_q95", "benign", _recompress(95)),
    Transform("jpeg_q85", "benign", _recompress(85)),
    Transform("jpeg_q75", "benign", _recompress(75)),
    Transform("jpeg_q60", "benign", _recompress(60)),
    Transform("resize_90pct", "benign", _resize(0.9)),
    Transform("resize_75pct", "benign", _resize(0.75)),
    Transform("resize_50pct", "benign", _resize(0.5)),
    Transform("metadata_strip", "benign", _strip_metadata),
    Transform("jpeg_to_png", "benign", _to_png),
    Transform("png_to_jpeg", "benign", _png_roundtrip_jpeg),
    Transform("brightness_+8pct", "benign", _brightness(1.08)),
    Transform("brightness_-8pct", "benign", _brightness(0.92)),
    Transform("contrast_+10pct", "benign", _contrast(1.10)),
    Transform("contrast_-10pct", "benign", _contrast(0.90)),
    Transform("mild_sharpen", "benign", _sharpen),
    Transform("mild_blur_s0.6", "benign", _mild_blur),
    Transform("screenshot_sim_synthetic", "benign", _screenshot_sim),
    Transform("social_recompress_synthetic", "benign", _social_sim),
]


# --------------------------------------------------------------------------
# malicious / semantic modifications
# --------------------------------------------------------------------------

def _region(img: np.ndarray, rng: np.random.Generator, frac: float = 0.14,
            accept=None, tries: int = 24) -> tuple[int, int, int, int] | None:
    """Deterministic-per-rng region inside the central 70% of the image.

    `accept(x, y, side)` lets a transform demand a MEANINGFUL edit target
    (e.g. textured content for blur/removal). Editing sky-onto-sky is not a
    content modification; counting such no-ops as 'missed tampering' would
    misstate detection rates. Returns None when no acceptable region exists
    (recorded upstream as not_applicable, never silently dropped)."""
    h, w = img.shape[:2]
    side = max(16, int(min(h, w) * frac))
    for _ in range(tries):
        x = int(rng.integers(int(w * 0.15), max(int(w * 0.85) - side, int(w * 0.15) + 1)))
        y = int(rng.integers(int(h * 0.15), max(int(h * 0.85) - side, int(h * 0.15) + 1)))
        if accept is None or accept(x, y, side):
            return x, y, side, side
    return None


def _gray_var(img: np.ndarray, x: int, y: int, side: int) -> float:
    g = cv2.cvtColor(img[y:y + side, x:x + side], cv2.COLOR_BGR2GRAY)
    return float(g.astype(np.float32).var())


def _object_insertion(d, r):
    img = _decode(d)
    reg = _region(img, r, 0.10)
    if reg is None:
        return None
    x, y, sw, sh = reg
    color = tuple(int(v) for v in r.integers(0, 255, 3))
    cv2.rectangle(img, (x, y), (x + sw, y + sh), color, -1)
    cv2.circle(img, (x + sw // 2, y + sh // 2), sw // 3,
               tuple(int(v) for v in r.integers(0, 255, 3)), -1)
    return _jpeg(img)


def _object_removal(d, r):
    img = _decode(d)
    # Removing something requires something visible to remove.
    reg = _region(img, r, 0.14, accept=lambda x, y, s: _gray_var(img, x, y, s) >= 150)
    if reg is None:
        return None
    x, y, sw, sh = reg
    mask = np.zeros(img.shape[:2], np.uint8)
    mask[y:y + sh, x:x + sw] = 255
    return _jpeg(cv2.inpaint(img, mask, 7, cv2.INPAINT_TELEA))


def _object_replacement(d, r):
    img = _decode(d)
    h, w = img.shape[:2]

    def differs(x, y, s):
        sx, sy = (x + w // 2) % (w - s), (y + h // 2) % (h - s)
        diff = np.abs(img[y:y + s, x:x + s].astype(np.float32)
                      - img[sy:sy + s, sx:sx + s].astype(np.float32))
        return float(diff.mean()) >= 10.0

    reg = _region(img, r, 0.14, accept=differs)
    if reg is None:
        return None
    x, y, sw, sh = reg
    sx, sy = (x + w // 2) % (w - sw), (y + h // 2) % (h - sh)
    img[y:y + sh, x:x + sw] = img[sy:sy + sh, sx:sx + sw]
    return _jpeg(img)


def _clone_copy(d, r):
    img = _decode(d)
    w = img.shape[1]

    def differs(x, y, s):
        dx = (x + s * 2) % (w - s)
        diff = np.abs(img[y:y + s, x:x + s].astype(np.float32)
                      - img[y:y + s, dx:dx + s].astype(np.float32))
        return float(diff.mean()) >= 10.0

    reg = _region(img, r, 0.12, accept=differs)
    if reg is None:
        return None
    x, y, sw, sh = reg
    dx = (x + sw * 2) % (w - sw)
    img[y:y + sh, dx:dx + sw] = img[y:y + sh, x:x + sw]
    return _jpeg(img)


def _local_blur(d, r):
    img = _decode(d)
    # Blurring an already-smooth region is not a content modification.
    reg = _region(img, r, 0.16, accept=lambda x, y, s: _gray_var(img, x, y, s) >= 150)
    if reg is None:
        return None
    x, y, sw, sh = reg
    img[y:y + sh, x:x + sw] = cv2.GaussianBlur(img[y:y + sh, x:x + sw], (0, 0), 8)
    return _jpeg(img)


def _face_pixelate(d, r):
    """SYNTHETIC face-region modification test case. Face detection runs
    in-memory for this single call; nothing biometric is stored (spec §2).
    Returns None when no face is present."""
    from .faces import detect_face
    img = _decode(d)
    face = detect_face(img)
    if face is None:
        return None
    x, y, w, h = face.bbox
    roi = img[y:y + h, x:x + w]
    if roi.size == 0:
        return None
    small = cv2.resize(roi, (max(1, w // 16), max(1, h // 16)),
                       interpolation=cv2.INTER_AREA)
    img[y:y + h, x:x + w] = cv2.resize(small, (w, h),
                                       interpolation=cv2.INTER_NEAREST)
    return _jpeg(img)


def _color_replace(d, r):
    img = _decode(d)
    reg = _region(img, r, 0.16)
    if reg is None:
        return None
    x, y, sw, sh = reg
    hsv = cv2.cvtColor(img[y:y + sh, x:x + sw], cv2.COLOR_BGR2HSV)
    # Force saturation so the recolor is visible even on gray content —
    # hue-rotating an unsaturated region is a visual no-op, not an edit.
    hsv[..., 1] = np.maximum(hsv[..., 1], 130)
    hsv[..., 0] = (hsv[..., 0].astype(int) + 90) % 180
    img[y:y + sh, x:x + sw] = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    return _jpeg(img)


def _significant_crop(d, r):
    """Center crop keeping 60% of each side (36% of area). Listed under
    modifications because this system's fingerprint does not support crop
    tolerance — a documented limitation, quantified by the boundary search."""
    img = _decode(d)
    h, w = img.shape[:2]
    dy, dx = int(h * 0.2), int(w * 0.2)
    return _jpeg(img[dy:h - dy, dx:w - dx])


def make_splice(other_image_bytes: bytes) -> Transform:
    def fn(d, r):
        img = _decode(d)
        src = _decode(other_image_bytes)
        reg = _region(img, r, 0.18)
        if reg is None:
            return None
        x, y, sw, sh = reg
        patch = cv2.resize(src[:src.shape[0] // 2, :src.shape[1] // 2], (sw, sh))
        img[y:y + sh, x:x + sw] = patch
        return _jpeg(img)
    return Transform("splice_from_other_image", "malicious", fn)


def _generated_region(d, r):
    """SYNTHETIC stand-in for a generated replacement region: structured
    procedural texture blended into a region. Not a diffusion model output."""
    img = _decode(d)
    reg = _region(img, r, 0.16)
    if reg is None:
        return None
    x, y, sw, sh = reg
    yy, xx = np.mgrid[0:sh, 0:sw].astype(np.float32)
    tex = 127 + 60 * np.sin(xx / 6.0) * np.cos(yy / 9.0)
    tex = cv2.merge([tex, np.roll(tex, 3, 0), np.roll(tex, 7, 1)])
    tex += r.normal(0, 10, tex.shape)
    img[y:y + sh, x:x + sw] = np.clip(tex, 0, 255).astype(np.uint8)
    return _jpeg(img)


MALICIOUS = [
    Transform("object_insertion_small", "malicious", _object_insertion),
    Transform("object_removal_inpaint", "malicious", _object_removal),
    Transform("object_replacement", "malicious", _object_replacement),
    Transform("clone_copy_region", "malicious", _clone_copy),
    Transform("local_blur_over_content", "malicious", _local_blur),
    Transform("face_region_pixelate_synthetic", "malicious", _face_pixelate),
    Transform("color_replacement_region", "malicious", _color_replace),
    Transform("significant_crop_60pct_sides", "malicious", _significant_crop),
    Transform("generated_region_synthetic", "malicious", _generated_region),
]

NOT_TESTED = [
    "print_scan (no hardware available)",
    "real WhatsApp/Instagram/Telegram recompression (no platform access)",
    "real device screenshot (no display in test environment)",
    "text_modification (dataset contains no reliable text regions)",
    "real Google Lens / iOS camera scanning (no device in test environment)",
]
