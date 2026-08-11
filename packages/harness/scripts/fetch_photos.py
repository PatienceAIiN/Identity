"""Fetch a small public-domain/openly-licensed portrait test set from
Wikimedia Commons for the Phase 0 sweep. Keeps only files that decode as
images and contain a detectable face. Test data only — not shipped."""

import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness.faces import detect_face  # noqa: E402

# Wikimedia Commons filenames (US-gov / NASA public domain or GODL).
CANDIDATES = [
    "Official_portrait_of_Barack_Obama.jpg",
    "Joe_Biden_presidential_portrait.jpg",
    "Kamala_Harris_Vice_Presidential_Portrait.jpg",
    "Donald_Trump_official_portrait.jpg",
    "Mae_Carol_Jemison.jpg",
    "Kalpana_Chawla,_NASA_photo_portrait_in_orange_suit.jpg",
    "Sunita_Williams.jpg",
    "Guion_Bluford.jpg",
    "Ellen_Ochoa.jpg",
    "A._P._J._Abdul_Kalam.jpg",
    "Sonia_Sotomayor_in_SCOTUS_robe.jpg",
    "Ketanji_Brown_Jackson_official_portrait.jpg",
    "John_Glenn_Low_Res.jpg",
    "Ronald_McNair.jpg",
]

BASE = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=1400"
OUT = Path(__file__).resolve().parent.parent / "photos"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "photobind-harness/0.1 (local test rig; contact: dev)"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=30).read()
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 2:
                time.sleep(30 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def main():
    OUT.mkdir(exist_ok=True)
    kept = 0
    for name in CANDIDATES:
        out = OUT / (Path(name).stem.replace(",", "").replace(".", "_") + ".jpg")
        if out.exists():
            print(f"HAVE {out.name}")
            kept += 1
            continue
        url = BASE.format(urllib.parse.quote(name))
        try:
            data = _fetch(url)
        except Exception as e:
            print(f"SKIP {name}: fetch failed ({e})")
            continue
        finally:
            time.sleep(8)  # stay under Wikimedia's rate limit
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None or min(img.shape[:2]) < 500:
            print(f"SKIP {name}: not a usable image")
            continue
        # Normalize size: fusion canvases are ~1000-1100px, huge originals just
        # slow detection down.
        h, w = img.shape[:2]
        if max(h, w) > 1600:
            s = 1600 / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        if detect_face(img) is None:
            print(f"SKIP {name}: no face detected")
            continue
        cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 96])
        kept += 1
        print(f"OK   {name} -> {out.name} ({img.shape[1]}x{img.shape[0]})")
    print(f"\nkept {kept} photos in {OUT}")


if __name__ == "__main__":
    main()
