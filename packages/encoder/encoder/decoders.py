"""Decoders used for validate-before-return.

zxing-cpp is required. pyzbar is optional (needs a system libzbar); when it
is missing, validation runs on the decoders that exist and says so in the
confidence report — it never silently pretends the missing decoder passed.

OpenCV's QRCodeDetector is deliberately absent: recorded in the project
compatibility matrix as INCOMPATIBLE WITH CURRENT PHOTO-FUSION
REPRESENTATION (decodes plain QRs; 0% on fused codes, measured in Phase 0).
"""

import cv2
import numpy as np
import zxingcpp

try:
    from pyzbar import pyzbar as _pyzbar
    _HAVE_PYZBAR = True
except Exception:  # missing libzbar
    _pyzbar = None
    _HAVE_PYZBAR = False


def available_decoders() -> tuple[str, ...]:
    return ("zxing", "pyzbar") if _HAVE_PYZBAR else ("zxing",)


def _zxing(img_bgr: np.ndarray) -> set[str]:
    results = zxingcpp.read_barcodes(img_bgr, formats=zxingcpp.BarcodeFormat.QRCode,
                                     try_rotate=True, try_downscale=True)
    return {r.text for r in results if r.valid}


def _pyzbar_decode(img_bgr: np.ndarray) -> set[str]:
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    out = set()
    for r in _pyzbar.decode(gray, symbols=[_pyzbar.ZBarSymbol.QRCODE]):
        try:
            out.add(r.data.decode("utf-8"))
        except UnicodeDecodeError:
            pass
    return out


def decode_all(img_bgr: np.ndarray, expected: str) -> dict[str, bool]:
    """Exact-payload verdict per available decoder."""
    verdicts = {"zxing": expected in _zxing(img_bgr)}
    if _HAVE_PYZBAR:
        verdicts["pyzbar"] = expected in _pyzbar_decode(img_bgr)
    return verdicts
