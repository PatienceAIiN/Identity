"""Validation B dataset: images NEVER used in calibration or any prior
held-out run. Mix of portraits and non-portrait content, public-domain /
openly-licensed, from Wikimedia Commons. Saved to photos_fresh/."""

import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np

# None of these appear in photos/ (checked by design; the eval script also
# asserts sha256 disjointness against the original dataset).
CANDIDATES = [
    # portraits (US gov / NASA public domain)
    "Sally_Ride_(1984).jpg",
    "Christa_McAuliffe.jpg",
    "Fred_Haise.jpg",
    "Michelle_Obama_official_portrait_headshot.jpg",
    "George_W._Bush.jpg",
    "Condoleezza_Rice.jpg",
    "Colin_Powell_official_Secretary_of_State_photo.jpg",
    "Neil_Armstrong_pose.jpg",
    "Buzz_Aldrin.jpg",
    "Jerrie_Cobb.jpg",
    # non-portrait content (NASA / US gov public domain)
    "Hubble_ultra_deep_field.jpg",
    "Aldrin_Apollo_11.jpg",
    "Grand_Canyon_view_from_Pima_Point_2010.jpg",
    "Mount_Rushmore_detail_view_(100MP).jpg",
    "The_Earth_seen_from_Apollo_17.jpg",
    "White_House_north_and_south_sides.jpg",
]

BASE = "https://commons.wikimedia.org/wiki/Special:FilePath/{}?width=1400"
OUT = Path(__file__).resolve().parent.parent / "photos_fresh"


def _fetch(url: str) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": "photobind-harness/0.1 (local test rig; contact: dev)"})
    for attempt in range(3):
        try:
            return urllib.request.urlopen(req, timeout=45).read()
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
        out = OUT / (Path(name).stem.replace(",", "").replace(".", "_")
                     .replace("(", "").replace(")", "") + ".jpg")
        if out.exists():
            print(f"HAVE {out.name}")
            kept += 1
            continue
        try:
            data = _fetch(BASE.format(urllib.parse.quote(name)))
        except Exception as e:
            print(f"SKIP {name}: fetch failed ({e})")
            continue
        finally:
            time.sleep(8)
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None or min(img.shape[:2]) < 400:
            print(f"SKIP {name}: not a usable image")
            continue
        h, w = img.shape[:2]
        if max(h, w) > 1600:
            s = 1600 / max(h, w)
            img = cv2.resize(img, (int(w * s), int(h * s)), interpolation=cv2.INTER_AREA)
        cv2.imwrite(str(out), img, [cv2.IMWRITE_JPEG_QUALITY, 96])
        kept += 1
        print(f"OK   {name} -> {out.name} ({img.shape[1]}x{img.shape[0]})")
    print(f"\nkept {kept} fresh images in {OUT}")


if __name__ == "__main__":
    main()
