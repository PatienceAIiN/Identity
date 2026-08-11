"""QR/photo fusion engine (canonical implementation; the harness re-exports this).

Rendering model (halftone-style, minimal intervention):
- Function patterns (finders, separators, timing, alignment, format info)
  render at full black/white contrast — non-negotiable: the scanner locks
  the 1:1:3:1:1 finder ratio before decoding begins. 4-module quiet zone
  is whitened around the grid.
- Each data module gets a center dot blended toward the module color with
  the *minimum* alpha that pushes the dot's luminance past a binarization
  threshold (t_dark / t_light). Where the photo already agrees with the
  module color the edit costs ~nothing; scanners sample module centers, so
  the rest of the module stays pure photo.
- Over the face-protection mask, alpha is capped at alpha_protected and the
  dot shrinks, relying on EC level H to absorb the resulting errors.
- Placement: the grid does not have to cover the whole photo. Timing row 6
  crossing the eyes is the single worst thing that can happen to face SSIM,
  and placement is the only lever against it because fixed patterns must stay
  full-contrast. Selection is empirical: render every candidate placement,
  keep those that survive a mild degradation decode, take the best face SSIM.
"""

from dataclasses import dataclass, asdict

import cv2
import numpy as np
import segno

from .decoders import decode_all
from .degrade import Condition, apply
from .faces import FaceGeometry, detect_face
from .metrics import ssim, ssim_face
from .qrspec import function_pattern_mask, matrix_size

QUIET_MODULES = 4

# Binarization targets: dot luminance must end up below t_dark (dark module)
# or above t_light (light module). "strong" scans best, "soft" looks best.
CONTRAST_LEVELS = {
    "strong": (60, 200),
    "medium": (78, 182),
    "soft": (95, 165),
}

# (label, code_scale, anchor_x, anchor_y); anchors are fractions of the free
# space left on the canvas after scaling the code region down.
PLACEMENTS = (
    ("full", 1.00, 0.5, 0.5),
    ("tl", 0.84, 0.0, 0.0),
    ("tr", 0.84, 1.0, 0.0),
    ("bl", 0.84, 0.0, 1.0),
    ("br", 0.84, 1.0, 1.0),
    ("bc", 0.84, 0.5, 1.0),
    ("tl7", 0.72, 0.0, 0.0),
    ("tr7", 0.72, 1.0, 0.0),
    ("bl7", 0.72, 0.0, 1.0),
    ("br7", 0.72, 1.0, 1.0),
    ("bc7", 0.72, 0.5, 1.0),
    ("tc7", 0.72, 0.5, 0.0),
    ("tl6", 0.62, 0.0, 0.0),
    ("tr6", 0.62, 1.0, 0.0),
    ("bl6", 0.62, 0.0, 1.0),
    ("br6", 0.62, 1.0, 1.0),
)

# Placement survival check: mild but realistic degradation, decoded by the two
# strong decoders. OpenCV's detector is too weak to gate placement on.
_PLACEMENT_CHECK = Condition(jpeg_q=75, scale=0.5)


@dataclass(frozen=True)
class FuseParams:
    version: int = 3              # QR version (2 or 3 per spec)
    contrast: str = "medium"      # key into CONTRAST_LEVELS
    alpha_protected: float = 0.4  # alpha cap over protected face regions
    center_ratio: float = 0.45    # side of the center dot, as module fraction
    canvas_px: int = 1110         # output canvas side
    # Measured, not guessed: on 10 photos x 5 gate conditions, decode stays at
    # 100% down to 0.50 and first fails at 0.45, so 0.55 keeps two steps of
    # margin while giving back visible photo at the border. The ring itself
    # cannot go: at 0.0 decode collapses to 0.67 because the scanner needs the
    # contrast to find the code at all.
    quiet_alpha: float = 0.55
    # "full"  — grid spans the whole canvas; the photo shows through every
    #           module. This is the product's thesis: the photo IS the code.
    # "auto"  — search placements/scales and keep the best face SSIM, which
    #           tends to move the code off the face into a corner.
    coverage: str = "full"

    def label(self) -> str:
        return (f"v{self.version}_{self.contrast}"
                f"_ap{self.alpha_protected:.2f}_cr{self.center_ratio:.2f}")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FusedResult:
    image: np.ndarray            # BGR uint8, the fused candidate
    reference: np.ndarray        # BGR uint8, cover-cropped photo (same canvas)
    payload: str
    face_bbox: tuple[int, int, int, int]
    params: FuseParams
    placement: str = "full"
    has_face: bool = True


# Deterministic stand-in for a 256-bit AES-GCM key, base64url (43 chars).
_KEY256 = "Ab3xK9mQpZw4TfGhJk2LnPqRsTuVwXyZ01dEfGhIjKm"


def make_payload(version: int) -> str:
    """Deterministic payload that fits EC-H at the given version.

    v2/v3: short-domain id-only URLs (the original §3 design — cannot carry
    a key fragment; retained for comparison).
    v7/v8: the full zero-knowledge design with the 256-bit key kept intact:
    bare short domain + opaque id + "#" + 43-char base64url key.
    v7 fits a 64-bit id (63/64 bytes); v8 fits the spec's 128-bit
    token_urlsafe(16) id (74/84 bytes). v4/v5 cannot carry the 256-bit key
    at all — demonstrated by capacity.py, not silently worked around.
    """
    if version == 2:
        return "pb.id/r/x7Qm2K"                              # 14 bytes
    if version == 3:
        return "pb.id/r/hK4mZq9TvW3nXbRd"                    # 24 bytes
    if version == 7:
        return f"pb.id/r/hK4mZq9TvW#{_KEY256}"               # 63 bytes, 64-bit id
    if version == 8:
        return f"pb.id/r/hK4mZq9TvW3nXbRdQw2Zt7#{_KEY256}"   # 74 bytes, 128-bit id
    raise ValueError("harness sweeps versions 2, 3, 7, 8")


def _qr_matrix(payload: str, version: int) -> np.ndarray:
    qr = segno.make(payload, error="h", version=version, boost_error=False)
    m = np.array([[bool(v) for v in row] for row in qr.matrix])
    assert m.shape[0] == matrix_size(version)
    return m


def _cover_crop(img: np.ndarray, size: int) -> np.ndarray:
    """Center-crop to square, resize to (size, size)."""
    h, w = img.shape[:2]
    s = min(h, w)
    y0, x0 = (h - s) // 2, (w - s) // 2
    sq = img[y0:y0 + s, x0:x0 + s]
    interp = cv2.INTER_AREA if s > size else cv2.INTER_CUBIC
    return cv2.resize(sq, (size, size), interpolation=interp)


def _render(ref: np.ndarray, protection: np.ndarray, modules: np.ndarray,
            fixed: np.ndarray, params: FuseParams,
            placement: tuple[str, float, float, float]) -> np.ndarray:
    n = modules.shape[0]
    canvas_px = params.canvas_px
    _, scale, ax, ay = placement

    mp = int(canvas_px * scale) // (n + 2 * QUIET_MODULES)
    code_px = mp * (n + 2 * QUIET_MODULES)
    ox = int((canvas_px - code_px) * ax)
    oy = int((canvas_px - code_px) * ay)
    q = QUIET_MODULES * mp

    t_dark, t_light = CONTRAST_LEVELS[params.contrast]
    img = ref.astype(np.float32)
    lum = cv2.cvtColor(ref, cv2.COLOR_BGR2GRAY).astype(np.float32)

    # Quiet zone: whiten the ring around the grid so finders have breathing
    # room. Photo outside the code region is untouched.
    quiet = np.zeros((canvas_px, canvas_px), dtype=np.float32)
    quiet[oy:oy + code_px, ox:ox + code_px] = params.quiet_alpha
    quiet[oy + q:oy + q + n * mp, ox + q:ox + q + n * mp] = 0.0
    quiet = cv2.GaussianBlur(quiet, (0, 0), sigmaX=mp * 0.25)
    img = img * (1 - quiet[..., None]) + 255.0 * quiet[..., None]

    for r in range(n):
        for c in range(n):
            y0, x0 = oy + q + r * mp, ox + q + c * mp
            dark = bool(modules[r, c])
            target = 0.0 if dark else 255.0

            if fixed[r, c]:
                img[y0:y0 + mp, x0:x0 + mp] = target
                continue

            cell_prot = float(protection[y0:y0 + mp, x0:x0 + mp].mean())

            # Dot shrinks a little over protected regions: less face damage,
            # and a smaller wrong-colored sample reads as photo, not module.
            ratio = params.center_ratio * (1.0 - 0.25 * cell_prot)
            side = max(3, int(round(mp * ratio)))
            off = (mp - side) // 2
            cy, cx = y0 + off, x0 + off

            # Minimum alpha that pushes the dot's mean luminance past the
            # binarization threshold.
            dot_lum = float(lum[cy:cy + side, cx:cx + side].mean())
            if dark:
                a = 0.0 if dot_lum <= t_dark else 1.0 - t_dark / max(dot_lum, 1e-3)
            else:
                a = 0.0 if dot_lum >= t_light else (t_light - dot_lum) / max(255.0 - dot_lum, 1e-3)
            a = min(a * 1.08, 1.0)  # small safety margin over the exact minimum

            # Protection cap: the face keeps its detail, EC-H eats the errors.
            a_cap = 1.0 + (params.alpha_protected - 1.0) * cell_prot
            a = float(np.clip(min(a, a_cap), 0.0, 1.0))
            if a == 0.0:
                continue

            dot = img[cy:cy + side, cx:cx + side]
            dot *= (1 - a)
            dot += target * a

    return np.clip(img, 0, 255).astype(np.uint8)


def fuse(photo_bgr: np.ndarray, params: FuseParams,
         face: FaceGeometry | None = None,
         payload: str | None = None) -> FusedResult:
    """Render all candidate placements, keep those whose code still decodes
    under a mild degradation, return the best-looking one.

    A face is optional. When one is present its features are protected and
    placements are ranked on face SSIM; with no face (landscapes, objects,
    products) there is nothing to protect, so the mask is empty and ranking
    falls back to whole-image SSIM. Falls back to full-bleed if no placement
    survives the decode check."""
    ref = _cover_crop(photo_bgr, params.canvas_px)
    if face is None:
        face = detect_face(ref)
    has_face = face is not None
    if not has_face:
        face = FaceGeometry(
            bbox=(0, 0, ref.shape[1], ref.shape[0]),
            protection=np.zeros(ref.shape[:2], dtype=np.float32))

    if payload is None:
        payload = make_payload(params.version)
    modules = _qr_matrix(payload, params.version)
    fixed = function_pattern_mask(params.version)

    # Full-bleed: render one placement covering everything. No search, so the
    # code always spans the image edge to edge.
    if params.coverage == "full":
        image = _render(ref, face.protection, modules, fixed, params,
                        PLACEMENTS[0])
        return FusedResult(image=image, reference=ref, payload=payload,
                           face_bbox=face.bbox, params=params,
                           placement="full", has_face=has_face)

    best = None       # (ssim, label, image)
    fallback = None   # full-bleed render, if nothing survives the check
    for placement in PLACEMENTS:
        img = _render(ref, face.protection, modules, fixed, params, placement)
        if placement[0] == "full":
            fallback = img
        verdicts = decode_all(apply(img, _PLACEMENT_CHECK), payload)
        if not all(verdicts.values()):
            continue
        score = ssim_face(ref, img, face.bbox) if has_face else ssim(ref, img)
        if best is None or score > best[0]:
            best = (score, placement[0], img)

    if best is None:
        image, label = fallback, "full"
    else:
        _, label, image = best
    return FusedResult(image=image, reference=ref, payload=payload,
                       face_bbox=face.bbox, params=params, placement=label,
                       has_face=has_face)
