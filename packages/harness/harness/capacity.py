"""Validation C, part 1: can the encrypted-URL design fit each QR version at
EC level H? The 256-bit AES-GCM key is NOT reduced to force a fit (owner
directive); this script records where the design actually lands.

URL shape: <domain>/r/<opaque_id>#<base64url(256-bit key)>  (43-char key)
Domain "pb.id" is the shortest-practical placeholder (5 chars); a real
registration this short is plausible (.id allows 2-char SLDs) but unverified.
"""

import json
from pathlib import Path

import segno

KEY256 = "A" * 43
DOMAIN = "pb.id"

ID_VARIANTS = {
    "128-bit id (spec token_urlsafe(16), 22 chars)": "x" * 22,
    "96-bit id (16 chars)": "x" * 16,
    "64-bit id (11 chars)": "x" * 11,
}


def main():
    rows = []
    for id_label, opaque in ID_VARIANTS.items():
        for scheme in ("", "https://"):
            url = f"{scheme}{DOMAIN}/r/{opaque}#{KEY256}"
            fits_at = None
            for v in range(2, 11):
                try:
                    segno.make(url, error="h", version=v, boost_error=False)
                    fits_at = v
                    break
                except (segno.DataOverflowError, ValueError):
                    continue
            rows.append({"id": id_label, "scheme": scheme or "bare",
                         "url_chars": len(url), "min_version_at_EC_H": fits_at})
    # And the explicit v4/v5 question:
    for v in (4, 5):
        url = f"{DOMAIN}/r/x#{KEY256}"  # even a 1-char id
        try:
            segno.make(url, error="h", version=v, boost_error=False)
            verdict = "fits"
        except (segno.DataOverflowError, ValueError):
            verdict = "DOES NOT FIT (even with a 1-char id)"
        rows.append({"id": "minimum possible", "scheme": "bare",
                     "url_chars": len(url),
                     "min_version_at_EC_H": f"v{v}: {verdict}"})

    out = Path("results/capacity.json")
    out.write_text(json.dumps(rows, indent=2))
    for r in rows:
        print(r)


if __name__ == "__main__":
    main()
