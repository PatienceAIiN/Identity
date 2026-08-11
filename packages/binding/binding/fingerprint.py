"""Layer 2/3: transformation-tolerant visual fingerprints.

LAYER 2 — global fingerprint ("phash256-v1"):
DCT perceptual hash, the standard construction described in Zauner,
"Implementation and Benchmarking of Perceptual Image Hash Functions" (2010):
grayscale -> 64x64 (INTER_AREA) -> 2D DCT -> 16x16 low-frequency block minus
DC -> threshold at median -> 255 bits (hex-packed to 256).
Distance: normalized Hamming in [0, 1]. Deterministic, no learned parts.

LAYER 3 — region evidence ("tile-evidence-8x8-v2"):
The image is resized to a 512x512 canonical frame and cut into an 8x8 grid.
Per tile, three deterministic features:
  - phash64: DCT hash (32x32 -> 8x8 block), 64 bits — structural change
  - chroma:  mean CIELAB (a*, b*), quantized to 0.1 units — hue/color change
             (the global hash is grayscale; color swaps are invisible to it)
  - energy:  log10(Laplacian variance + 1), quantized to 0.001 — texture/
             sharpness change (local blur keeps means intact; this does not)

Comparison MEDIAN-CENTERS each feature's per-tile deltas across all 64
tiles: benign global transformations (recompression, resize, brightness,
contrast, mild blur/sharpen) move every tile roughly equally and cancel out,
while a local edit leaves outlier tiles. The centered deltas are what gets
thresholded.

Known limitation: region evidence assumes the candidate is the same framing.
Crop/reframe misaligns the grid and flags broadly — crop is DETECTED but not
LOCALIZED, and no crop tolerance is provided.
"""

import cv2
import numpy as np

GLOBAL_ALGO = "phash256-v1"
REGION_ALGO = "tile-evidence-8x8x2-v3"
GRID = 8
CANON_SIZE = 512
TILE = CANON_SIZE // GRID

# Staggered dual grid: grid A is the aligned 8x8 grid (64 tiles); grid B is
# offset by half a tile (7x7 = 49 interior tiles). An edit that straddles
# grid-A boundaries — diluting its per-tile signal — lands mostly inside a
# grid-B tile, and vice versa. Feature arrays concatenate A then B; median
# centering happens within each grid separately.
_OFFSETS = [(0, GRID), (TILE // 2, GRID - 1)]  # (pixel offset, tiles per side)

# Flat-tile handling. A DCT hash of a near-flat tile is noise (median
# thresholding of near-zero coefficients), and Laplacian variance of a flat
# tile is dominated by codec noise. Both would inflate calibrated thresholds
# and mask real edits elsewhere, so:
# - energy uses log10(var + ENERGY_FLOOR): codec noise on flat tiles is
#   compressed, real texture changes still move the statistic
# - the hash feature is only compared where the REGISTERED tile has texture
#   (energy >= TEXTURE_GATE); flat tiles rely on chroma + energy evidence
ENERGY_FLOOR = 25.0
TEXTURE_GATE = 2300  # log10(var+25)*1000; var >= ~175


def _phash_bits(gray: np.ndarray, size: int, k: int) -> np.ndarray:
    img = cv2.resize(gray, (size, size), interpolation=cv2.INTER_AREA)
    dct = cv2.dct(img.astype(np.float32))
    block = dct[:k, :k].flatten()[1:]  # drop DC
    return (block > np.median(block)).astype(np.uint8)


def _bits_to_hex(bits: np.ndarray) -> str:
    return np.packbits(bits).tobytes().hex()


def _hex_to_bits(h: str) -> np.ndarray:
    return np.unpackbits(np.frombuffer(bytes.fromhex(h), np.uint8))


def phash_global(gray: np.ndarray) -> str:
    """256-bit DCT pHash of the whole canonical image, hex-encoded."""
    return _bits_to_hex(_phash_bits(gray, 64, 16))


def distance(hex_a: str, hex_b: str) -> float:
    """Normalized Hamming distance in [0, 1]."""
    a, b = _hex_to_bits(hex_a), _hex_to_bits(hex_b)
    if a.shape != b.shape:
        raise ValueError("fingerprint length mismatch")
    return float(np.count_nonzero(a != b)) / len(a)


# --------------------------------------------------------------------------
# Layer 3
# --------------------------------------------------------------------------

def tile_count() -> int:
    return sum(n * n for _, n in _OFFSETS)


def tile_features(bgr: np.ndarray) -> dict:
    """Per-tile evidence on the 512x512 canonical frame across the staggered
    dual grid. All values are integers (signable JSON, no float drift)."""
    frame = cv2.resize(bgr, (CANON_SIZE, CANON_SIZE), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB).astype(np.float32)
    hashes, chroma, energy = [], [], []
    for off, n in _OFFSETS:
        for r in range(n):
            for c in range(n):
                y0, x0 = off + r * TILE, off + c * TILE
                g = gray[y0:y0 + TILE, x0:x0 + TILE]
                l = lab[y0:y0 + TILE, x0:x0 + TILE]
                hashes.append(_bits_to_hex(_phash_bits(g, 32, 8)))
                chroma.append([int(round(float(l[..., 1].mean()) * 10)),
                               int(round(float(l[..., 2].mean()) * 10))])
                lap = cv2.Laplacian(g.astype(np.float32), cv2.CV_32F)
                energy.append(int(round(
                    np.log10(float(lap.var()) + ENERGY_FLOOR) * 1000)))
    return {"phash": hashes, "chroma": chroma, "energy": energy}


def compare_tiles(ref: dict, cand: dict) -> dict:
    """Median-centered per-tile deltas (within each grid of the staggered
    pair). Returns per-tile arrays plus the maximum of each — the statistics
    the verifier thresholds."""
    n = tile_count()
    if not (len(ref["phash"]) == len(cand["phash"]) == n):
        raise ValueError("tile grid mismatch")
    textured = np.array([e >= TEXTURE_GATE for e in ref["energy"]])
    hash_d = np.array([distance(a, b) if textured[i] else 0.0
                       for i, (a, b) in enumerate(zip(ref["phash"], cand["phash"]))])
    chroma_d = np.array([
        float(np.hypot(ca[0] - ra[0], ca[1] - ra[1])) / 10.0
        for ra, ca in zip(ref["chroma"], cand["chroma"])])
    energy_d = np.array([abs(ce - re) / 1000.0
                         for re, ce in zip(ref["energy"], cand["energy"])])

    def centered(x):
        out = np.empty_like(x)
        i = 0
        for _, g in _OFFSETS:  # center within each grid separately
            j = i + g * g
            out[i:j] = np.maximum(x[i:j] - np.median(x[i:j]), 0.0)
            i = j
        return out

    h, c, e = centered(hash_d), centered(chroma_d), centered(energy_d)
    return {
        "hash_centered": [round(float(v), 4) for v in h],
        "chroma_centered": [round(float(v), 4) for v in c],
        "energy_centered": [round(float(v), 4) for v in e],
        "max_hash": round(float(h.max()), 4),
        "max_chroma": round(float(c.max()), 4),
        "max_energy": round(float(e.max()), 4),
    }
