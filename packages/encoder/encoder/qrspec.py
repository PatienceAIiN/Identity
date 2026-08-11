"""QR structural facts the fusion engine needs.

Function patterns (finders, separators, timing, alignment, format info,
version info) are NOT covered by error correction — scanners lock onto the
1:1:3:1:1 finder ratio before decoding begins — so the fusion engine must
render them at full contrast. segno's matrix doesn't distinguish them, so we
reconstruct the map from ISO/IEC 18004. Valid for versions 1-8.
"""

import numpy as np

# Alignment pattern center coordinates per version (ISO/IEC 18004 Annex E).
_ALIGNMENT_CENTERS = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
    6: [6, 34],
    7: [6, 22, 38],
    8: [6, 24, 42],
}

# Byte-mode data capacity at EC level H.
BYTE_CAPACITY_H = {1: 7, 2: 14, 3: 24, 4: 34, 5: 44, 6: 58, 7: 64, 8: 84}


def matrix_size(version: int) -> int:
    return 17 + 4 * version


def function_pattern_mask(version: int) -> np.ndarray:
    """Boolean (n, n) array, True where the module is a function pattern,
    format information, or version information (i.e. must be rendered at
    full contrast)."""
    if version not in _ALIGNMENT_CENTERS:
        raise ValueError(f"version {version} out of supported range 1-8")
    n = matrix_size(version)
    m = np.zeros((n, n), dtype=bool)

    # Finder patterns + separators: 8x8 blocks in three corners.
    m[:8, :8] = True
    m[:8, n - 8:] = True
    m[n - 8:, :8] = True

    # Timing patterns: row 6 and column 6.
    m[6, :] = True
    m[:, 6] = True

    # Format information: around the finders (includes the dark module).
    m[8, :9] = True
    m[8, n - 8:] = True
    m[:9, 8] = True
    m[n - 8:, 8] = True

    # Alignment patterns: 5x5 at every (r, c) pair of center coordinates,
    # except the three that would collide with the finder patterns.
    centers = _ALIGNMENT_CENTERS[version]
    last = n - 7
    for r in centers:
        for c in centers:
            if (r, c) in ((6, 6), (6, last), (last, 6)):
                continue
            m[r - 2:r + 3, c - 2:c + 3] = True

    # Version information (v7+): 3x6 block above the bottom-left finder and
    # its 6x3 transpose left of the top-right finder.
    if version >= 7:
        m[:6, n - 11:n - 8] = True
        m[n - 11:n - 8, :6] = True

    return m
