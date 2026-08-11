"""Face detection for the fusion engine: protection mask + face bbox for face-SSIM.

Uses OpenCV's YuNet detector (bbox + 5 landmarks: eyes, nose tip, mouth
corners). Everything stays in memory for the lifetime of one image — nothing
is written to disk, returned structures carry only geometry needed for the
current fusion, per the §8 hard rule on biometric data.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MODEL_PATH = Path(__file__).resolve().parent / "models" / "face_detection_yunet_2023mar.onnx"


@dataclass
class FaceGeometry:
    bbox: tuple[int, int, int, int]  # x, y, w, h in image coords
    protection: np.ndarray           # float32 (H, W) in [0, 1]; 1 = fully protected


def _detector(size: tuple[int, int]):
    det = cv2.FaceDetectorYN.create(str(MODEL_PATH), "", size, score_threshold=0.6)
    return det


def detect_face(img_bgr: np.ndarray) -> FaceGeometry | None:
    """Detect the most prominent face. Returns None if no face found."""
    h, w = img_bgr.shape[:2]
    det = _detector((w, h))
    _, faces = det.detect(img_bgr)
    if faces is None or len(faces) == 0:
        return None
    # Most prominent = largest area.
    face = max(faces, key=lambda f: f[2] * f[3])
    x, y, fw, fh = (int(v) for v in face[:4])
    landmarks = face[4:14].reshape(5, 2)  # right eye, left eye, nose, mouth-r, mouth-l

    protection = _protection_mask((h, w), (x, y, fw, fh), landmarks)
    return FaceGeometry(bbox=(x, y, fw, fh), protection=protection)


def _protection_mask(shape, bbox, landmarks) -> np.ndarray:
    """Soft mask over the configural identity features: eyes, nose bridge,
    mouth, and the smooth cheek gradients between them. Feathered so module
    alpha ramps rather than steps."""
    h, w = shape
    mask = np.zeros((h, w), dtype=np.float32)
    x, y, fw, fh = bbox
    r_eye, l_eye, nose, m_r, m_l = landmarks

    eye_r = max(6, int(0.16 * fw))
    for cx, cy in (r_eye, l_eye):
        cv2.circle(mask, (int(cx), int(cy)), eye_r, 1.0, -1)

    # Nose bridge: thick line from between-the-eyes down to the nose tip.
    mid_eyes = ((r_eye + l_eye) / 2).astype(int)
    cv2.line(mask, tuple(mid_eyes), (int(nose[0]), int(nose[1])),
             1.0, thickness=max(6, int(0.14 * fw)))

    # Mouth: ellipse spanning the two corners.
    mouth_c = ((m_r + m_l) / 2).astype(int)
    mouth_w = int(np.linalg.norm(m_r - m_l) * 0.75) + eye_r
    cv2.ellipse(mask, tuple(mouth_c), (mouth_w, max(6, int(0.10 * fh))),
                0, 0, 360, 1.0, -1)

    # Cheeks / inner-face gradients: soft ellipse over the central face at
    # lower weight, so it attenuates rather than blanks modules there.
    inner = np.zeros_like(mask)
    cv2.ellipse(inner, (x + fw // 2, y + fh // 2),
                (int(fw * 0.42), int(fh * 0.48)), 0, 0, 360, 0.55, -1)
    mask = np.maximum(mask, inner)

    # Feather.
    k = max(9, (int(0.22 * fw) // 2) * 2 + 1)
    mask = cv2.GaussianBlur(mask, (k, k), 0)
    return np.clip(mask, 0.0, 1.0)
