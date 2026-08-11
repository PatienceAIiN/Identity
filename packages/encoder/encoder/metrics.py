"""SSIM for placement ranking, OpenCV-only.

Reproduces skimage.metrics.structural_similarity defaults (win_size=7,
uniform filter, K1=0.01, K2=0.03, L=255) so placement decisions match the
Phase 0 harness bit-for-bit without a scikit-image dependency.
"""

import cv2
import numpy as np

_WIN = 7
_C1 = (0.01 * 255) ** 2
_C2 = (0.03 * 255) ** 2


def _gray(img: np.ndarray) -> np.ndarray:
    if img.ndim == 3:
        return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return img


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    x = _gray(a).astype(np.float64)
    y = _gray(b).astype(np.float64)
    if x.shape != y.shape:
        raise ValueError("shape mismatch")
    # skimage crops the (win_size-1)/2 border and uses unnormalized variance
    # correction cov_norm = NP/(NP-1) with NP = win^2.
    np_ = _WIN * _WIN
    cov_norm = np_ / (np_ - 1)
    f = lambda im: cv2.boxFilter(im, -1, (_WIN, _WIN),
                                 borderType=cv2.BORDER_REFLECT)
    ux, uy = f(x), f(y)
    uxx, uyy, uxy = f(x * x), f(y * y), f(x * y)
    vx = cov_norm * (uxx - ux * ux)
    vy = cov_norm * (uyy - uy * uy)
    vxy = cov_norm * (uxy - ux * uy)
    s = ((2 * ux * uy + _C1) * (2 * vxy + _C2)) / \
        ((ux * ux + uy * uy + _C1) * (vx + vy + _C2))
    pad = (_WIN - 1) // 2
    return float(s[pad:-pad, pad:-pad].mean())


def ssim_face(reference: np.ndarray, fused: np.ndarray,
              bbox: tuple[int, int, int, int], expand: float = 0.15) -> float:
    """SSIM restricted to the (slightly expanded) face bounding box."""
    h, w = reference.shape[:2]
    x, y, bw, bh = bbox
    dx, dy = int(bw * expand), int(bh * expand)
    x0, y0 = max(0, x - dx), max(0, y - dy)
    x1, y1 = min(w, x + bw + dx), min(h, y + bh + dy)
    ref = _gray(reference)[y0:y1, x0:x1]
    fus = _gray(fused)[y0:y1, x0:x1]
    if min(ref.shape) < 8:
        raise ValueError("face region too small for SSIM")
    return ssim(ref, fus)
