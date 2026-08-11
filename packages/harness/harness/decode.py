"""Three independent decoders. They disagree; real-world scanners are all
three. Success = exact payload match, not merely 'something decoded'."""

import cv2
import numpy as np
import zxingcpp
from pyzbar import pyzbar

DECODERS = ("zxing", "pyzbar", "opencv")

_cv_detector = None


def _opencv(img_bgr: np.ndarray) -> set[str]:
    global _cv_detector
    if _cv_detector is None:
        _cv_detector = cv2.QRCodeDetector()
    try:
        ok, texts, _, _ = _cv_detector.detectAndDecodeMulti(img_bgr)
    except cv2.error:
        return set()
    return {t for t in texts if t} if ok else set()


def _zxing(img_bgr: np.ndarray) -> set[str]:
    results = zxingcpp.read_barcodes(img_bgr, formats=zxingcpp.BarcodeFormat.QRCode,
                                     try_rotate=True, try_downscale=True)
    return {r.text for r in results if r.valid}


def _pyzbar(img_bgr: np.ndarray) -> set[str]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    results = pyzbar.decode(gray, symbols=[pyzbar.ZBarSymbol.QRCODE])
    out = set()
    for r in results:
        try:
            out.add(r.data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return out


def decode_all(img_bgr: np.ndarray, expected: str) -> dict[str, bool]:
    """Run all three decoders; True only when the exact payload comes back."""
    return {
        "zxing": expected in _zxing(img_bgr),
        "pyzbar": expected in _pyzbar(img_bgr),
        "opencv": expected in _opencv(img_bgr),
    }
