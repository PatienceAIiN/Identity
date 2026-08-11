"""The fail-closed photo-binding verifier (spec §5, §21).

Statuses never overstate. In particular:
- A resolving QR alone NEVER yields any AUTHENTIC_* status.
- AUTHENTIC_DERIVED means "consistent with the registered image under the
  transformations this system was tested against" — it does not prove the
  file is the original.
- Every uncertainty path terminates in a non-authentic status.

Decision rule (v2):
- exact SHA-256 match                          -> AUTHENTIC_EXACT
- global distance >= modified_min
  OR any tile feature exceeds its threshold    -> CONTENT_MODIFIED
- global distance <= derived_max AND no
  tile flagged                                 -> AUTHENTIC_DERIVED
- otherwise                                    -> INSUFFICIENT_EVIDENCE

Tile features (hash / chroma / energy) are median-centered across the 8x8
grid, so benign global transformations cancel out; see fingerprint.py.

Thresholds come from a calibration artifact produced by the harness on a
calibration set that is disjoint from the held-out evaluation set. If no
calibration artifact exists, the defaults below are deliberately paranoid
(everything between exact-match and obvious-modification collapses to
INSUFFICIENT_EVIDENCE) and are labeled uncalibrated.
"""

import json
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import canonical, fingerprint
from .keys import DevKeyStore
from .record import SignedBinding, verify_signature
from .registry import CredentialRegistry

DEFAULT_THRESHOLDS_PATH = Path(__file__).resolve().parent / "thresholds.json"


@dataclass(frozen=True)
class Thresholds:
    derived_max: float      # global distance <= this -> candidate for DERIVED
    modified_min: float     # global distance >= this -> CONTENT_MODIFIED
    tile_hash_min: float    # centered per-tile phash distance
    tile_chroma_min: float  # centered per-tile chroma delta (CIELAB ab units)
    tile_energy_min: float  # centered per-tile log-energy delta
    calibrated: bool
    provenance: str

    @staticmethod
    def load(path: Path | str = DEFAULT_THRESHOLDS_PATH) -> "Thresholds":
        p = Path(path)
        if not p.exists():
            return Thresholds(derived_max=0.0, modified_min=0.35,
                              tile_hash_min=0.45, tile_chroma_min=8.0,
                              tile_energy_min=1.2, calibrated=False,
                              provenance="uncalibrated defaults (fail-closed)")
        d = json.loads(p.read_text())
        return Thresholds(derived_max=d["derived_max"],
                          modified_min=d["modified_min"],
                          tile_hash_min=d["tile_hash_min"],
                          tile_chroma_min=d["tile_chroma_min"],
                          tile_energy_min=d["tile_energy_min"],
                          calibrated=True, provenance=d.get("provenance", p.name))


@dataclass
class VerificationResult:
    status: str
    credential: str                 # valid | revoked | expired | unknown | invalid
    photo_binding: str              # exact | derived | mismatch | uncertain | unverified
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    latency_ms: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _fail(status: str, credential: str, reason: str, t0: float,
          evidence: dict | None = None) -> VerificationResult:
    return VerificationResult(status=status, credential=credential,
                              photo_binding="unverified", reason=reason,
                              evidence=evidence or {},
                              latency_ms=(time.perf_counter() - t0) * 1000)


def verify_photo(opaque_resolution_id: str, candidate_bytes: bytes,
                 registry: CredentialRegistry, keystore: DevKeyStore,
                 thresholds: Thresholds | None = None) -> VerificationResult:
    t0 = time.perf_counter()
    th = thresholds or Thresholds.load()

    # 1-2. resolve credential/share and validate state.
    share = registry.resolve(opaque_resolution_id)
    if share is None:
        return _fail("INSUFFICIENT_EVIDENCE", "unknown",
                     "opaque resolution id does not resolve", t0)
    share_state = share.state()
    cred = registry.credential(share.credential_id)
    if cred is None:
        return _fail("INVALID_CREDENTIAL", "invalid",
                     "share points at a missing credential", t0)
    if share_state == "revoked" or cred.state() == "revoked":
        return _fail("REVOKED", "revoked", "share or credential revoked", t0)
    if share_state == "expired":
        return _fail("EXPIRED", "expired", "share expired", t0)
    if share_state == "scan_cap_reached":
        return _fail("REVOKED", "revoked", "scan cap reached", t0)

    # 3. cryptographic signature over the binding.
    sig = verify_signature(cred.signed_binding, keystore)
    if sig == "unknown_key":
        return _fail("UNKNOWN_KEY", "valid", "binding signed by unknown key", t0)
    if sig != "ok":
        return _fail("INVALID_CREDENTIAL", "invalid",
                     f"binding signature check failed: {sig}", t0)

    record = cred.signed_binding.record

    # 4-6. candidate evidence.
    if not candidate_bytes:
        return _fail("CANNOT_VERIFY_PHOTO", "valid", "no candidate image supplied", t0)
    exact = canonical.exact_sha256(candidate_bytes)
    if exact == record.exact_sha256:
        return VerificationResult(
            status="AUTHENTIC_EXACT", credential="valid", photo_binding="exact",
            reason="candidate is byte-identical to the registered image",
            evidence={"exact_sha256": exact},
            latency_ms=(time.perf_counter() - t0) * 1000)
    try:
        bgr = canonical.decode_bgr(candidate_bytes)
        gray = canonical.decode_gray(candidate_bytes)
    except ValueError:
        return _fail("CANNOT_VERIFY_PHOTO", "valid",
                     "candidate is not a decodable image", t0)

    # 7. compare evidence.
    d_global = fingerprint.distance(fingerprint.phash_global(gray),
                                    record.global_fingerprint)
    tiles = fingerprint.compare_tiles(
        {"phash": record.region_fingerprints, "chroma": record.region_chroma,
         "energy": record.region_energy},
        fingerprint.tile_features(bgr))
    flagged = sorted(set(
        [i for i, v in enumerate(tiles["hash_centered"]) if v >= th.tile_hash_min] +
        [i for i, v in enumerate(tiles["chroma_centered"]) if v >= th.tile_chroma_min] +
        [i for i, v in enumerate(tiles["energy_centered"]) if v >= th.tile_energy_min]))
    evidence = {
        "global_distance": round(d_global, 4),
        "max_tile_hash": tiles["max_hash"],
        "max_tile_chroma": tiles["max_chroma"],
        "max_tile_energy": tiles["max_energy"],
        "changed_regions": flagged,   # 8x8 grid indices, row-major
        "thresholds": {"derived_max": th.derived_max,
                       "modified_min": th.modified_min,
                       "tile_hash_min": th.tile_hash_min,
                       "tile_chroma_min": th.tile_chroma_min,
                       "tile_energy_min": th.tile_energy_min,
                       "calibrated": th.calibrated,
                       "provenance": th.provenance},
    }

    # 8. structured result. Between thresholds, uncertainty stays
    # uncertainty — it never rounds up to authentic.
    lat = (time.perf_counter() - t0) * 1000
    if d_global >= th.modified_min or flagged:
        return VerificationResult(
            status="CONTENT_MODIFIED", credential="valid", photo_binding="mismatch",
            reason=("global fingerprint distance exceeds modification threshold"
                    if d_global >= th.modified_min else
                    f"{len(flagged)} tile(s) exceed local-change thresholds"),
            evidence=evidence, latency_ms=lat)
    if d_global <= th.derived_max:
        return VerificationResult(
            status="AUTHENTIC_DERIVED", credential="valid", photo_binding="derived",
            reason="within tested benign-transformation tolerance "
                   "(does not prove the file is the original)",
            evidence=evidence, latency_ms=lat)
    return VerificationResult(
        status="INSUFFICIENT_EVIDENCE", credential="valid", photo_binding="uncertain",
        reason="distance falls between tested tolerance and modification "
               "threshold; no authenticity determination",
        evidence=evidence, latency_ms=lat)
