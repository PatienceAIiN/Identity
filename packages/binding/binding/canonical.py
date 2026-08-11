"""Canonicalization: the exact definitions every evidence layer depends on.

Two distinct canonical forms, used by different layers:

1. CANONICAL BYTES (Layer 1, exact digest): the byte stream of the registered
   file, exactly as stored. SHA-256 over these bytes answers "is this the
   same file". Any re-encode, metadata strip, or resave changes it. That is
   the point of Layer 1, not a defect.

2. CANONICAL PIXELS (Layers 2-3, perceptual): decode the byte stream with a
   fixed pipeline — image codec decode -> 8-bit BGR -> Rec.601 grayscale.
   Metadata (EXIF, ICC intent, XMP) is deliberately ignored, so stripping it
   never changes perceptual evidence. Orientation EXIF is ALSO ignored — a
   rotated-by-metadata image presents different pixels to a viewer and is
   treated as the pixels the codec yields. Known limitation, documented.

canonicalization id: "pixels-bgr8-gray601-v1"
"""

import hashlib

import cv2
import numpy as np

CANONICALIZATION_ID = "pixels-bgr8-gray601-v1"


def exact_sha256(data: bytes) -> str:
    """Layer 1: exact digest over the stored byte stream."""
    return hashlib.sha256(data).hexdigest()


def decode_bgr(data: bytes) -> np.ndarray:
    """Canonical color decode. Raises ValueError on undecodable input — the
    verifier must fail closed, never guess."""
    img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("input is not a decodable image")
    return img


def decode_gray(data: bytes) -> np.ndarray:
    """Canonical grayscale form for the global fingerprint."""
    return cv2.cvtColor(decode_bgr(data), cv2.COLOR_BGR2GRAY)


def decode_dimensions(data: bytes) -> tuple[int, int]:
    """(width, height) of the canonical decoded image."""
    g = decode_gray(data)
    h, w = g.shape[:2]
    return w, h
