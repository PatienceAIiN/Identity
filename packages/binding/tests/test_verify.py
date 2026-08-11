"""Verifier semantics: swap matrix (§13), revocation, fail-closed paths.

These tests use UNCALIBRATED default thresholds on purpose: the swap matrix
must hold regardless of calibration, because photo A vs photo B distances are
far beyond any sane threshold and exact-match needs no threshold at all.
"""

import cv2
import dataclasses
import numpy as np

from binding.verify import Thresholds, verify_photo
from conftest import NOW, issue, synthetic_photo

TH = Thresholds(derived_max=0.10, modified_min=0.25, tile_hash_min=0.45,
                tile_chroma_min=8.0, tile_energy_min=1.2,
                calibrated=False, provenance="test-fixed")

PHOTO_A = synthetic_photo(1)
PHOTO_B = synthetic_photo(2)


def test_swap_matrix(keystore, registry):
    share_a, _, _ = issue(PHOTO_A, keystore, registry)
    share_b, _, _ = issue(PHOTO_B, keystore, registry)

    # Credential A + Photo A -> valid (exact)
    r = verify_photo(share_a.opaque_resolution_id, PHOTO_A, registry, keystore, TH)
    assert r.status == "AUTHENTIC_EXACT" and r.credential == "valid"
    # Credential A + Photo B -> mismatch
    r = verify_photo(share_a.opaque_resolution_id, PHOTO_B, registry, keystore, TH)
    assert r.status == "CONTENT_MODIFIED" and r.photo_binding == "mismatch"
    # Credential B + Photo B -> valid
    r = verify_photo(share_b.opaque_resolution_id, PHOTO_B, registry, keystore, TH)
    assert r.status == "AUTHENTIC_EXACT"
    # Credential B + Photo A -> mismatch
    r = verify_photo(share_b.opaque_resolution_id, PHOTO_A, registry, keystore, TH)
    assert r.status == "CONTENT_MODIFIED"


def test_qr_resolution_alone_is_never_authentic(keystore, registry):
    share, _, _ = issue(PHOTO_A, keystore, registry)
    r = verify_photo(share.opaque_resolution_id, b"", registry, keystore, TH)
    assert r.status == "CANNOT_VERIFY_PHOTO"
    assert not r.status.startswith("AUTHENTIC")


def test_recompression_is_derived_not_exact(keystore, registry):
    share, _, _ = issue(PHOTO_A, keystore, registry)
    img = cv2.imdecode(np.frombuffer(PHOTO_A, np.uint8), cv2.IMREAD_COLOR)
    _, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    r = verify_photo(share.opaque_resolution_id, buf.tobytes(), registry, keystore, TH)
    assert r.status == "AUTHENTIC_DERIVED"
    assert r.photo_binding == "derived"
    assert "original" in r.reason  # explicit does-not-prove-original language


def test_revoked_share_wins_over_valid_photo(keystore, registry):
    share, _, _ = issue(PHOTO_A, keystore, registry)
    registry.revoke_share(share.share_id, NOW)
    r = verify_photo(share.opaque_resolution_id, PHOTO_A, registry, keystore, TH)
    assert r.status == "REVOKED" and r.credential == "revoked"
    # Revoked is a distinct state, not "missing".
    assert registry.resolve(share.opaque_resolution_id) is not None


def test_revoking_one_share_leaves_siblings_active(keystore, registry):
    share1, cred, _ = issue(PHOTO_A, keystore, registry, label="LinkedIn")
    share2 = registry.mint_share(cred.credential_id, "Email", NOW)
    registry.revoke_share(share1.share_id, NOW)
    assert verify_photo(share1.opaque_resolution_id, PHOTO_A, registry,
                        keystore, TH).status == "REVOKED"
    assert verify_photo(share2.opaque_resolution_id, PHOTO_A, registry,
                        keystore, TH).status == "AUTHENTIC_EXACT"


def test_unknown_opaque_id_is_insufficient_evidence(keystore, registry):
    r = verify_photo("nonexistent-opaque-id", PHOTO_A, registry, keystore, TH)
    assert r.status == "INSUFFICIENT_EVIDENCE" and r.credential == "unknown"


def test_undecodable_candidate_fails_closed(keystore, registry):
    share, _, _ = issue(PHOTO_A, keystore, registry)
    r = verify_photo(share.opaque_resolution_id, b"not an image", registry,
                     keystore, TH)
    assert r.status == "CANNOT_VERIFY_PHOTO"


def test_tampered_binding_invalidates_credential(keystore, registry):
    share, cred, _ = issue(PHOTO_A, keystore, registry)
    # Malicious server operator edits the stored binding (points it at B's hash).
    from binding.canonical import exact_sha256
    tampered_record = dataclasses.replace(cred.signed_binding.record,
                                          exact_sha256=exact_sha256(PHOTO_B))
    cred.signed_binding = dataclasses.replace(cred.signed_binding,
                                              record=tampered_record)
    r = verify_photo(share.opaque_resolution_id, PHOTO_B, registry, keystore, TH)
    assert r.status == "INVALID_CREDENTIAL"


def test_uncertainty_band_never_rounds_up(keystore, registry):
    share, _, _ = issue(PHOTO_A, keystore, registry)
    # Force a distance into the (derived_max, modified_min) band using a
    # degenerate threshold pair around any nonzero distance.
    img = cv2.imdecode(np.frombuffer(PHOTO_A, np.uint8), cv2.IMREAD_COLOR)
    img = cv2.GaussianBlur(img, (0, 0), 3)  # strong blur: some distance > 0
    _, buf = cv2.imencode(".jpg", img)
    band = Thresholds(derived_max=0.0, modified_min=0.99, tile_hash_min=99,
                      tile_chroma_min=999, tile_energy_min=99,
                      calibrated=False, provenance="band-test")
    r = verify_photo(share.opaque_resolution_id, buf.tobytes(), registry,
                     keystore, band)
    assert r.status == "INSUFFICIENT_EVIDENCE"
    assert r.photo_binding == "uncertain"
