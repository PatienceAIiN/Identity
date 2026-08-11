"""Perceptual distortion metrics: full-image SSIM and face-region SSIM.

Face-region SSIM is the one the gate cares about — it measures whether the
person is still recognisably themselves where identity lives.
"""

import cv2
import numpy as np
from skimage.metrics import structural_similarity


def _gray(img: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def ssim_full(reference: np.ndarray, fused: np.ndarray) -> float:
    return float(structural_similarity(_gray(reference), _gray(fused)))


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
    return float(structural_similarity(ref, fus))
