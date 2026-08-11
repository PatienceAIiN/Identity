"""Signature and serialization integrity."""

import dataclasses

import pytest

from binding.keys import DevKeyStore
from binding.record import (DOMAIN, build_binding, sign_binding,
                            verify_signature, SignedBinding)
from conftest import NOW, synthetic_photo


def _binding(keystore):
    return build_binding(synthetic_photo(1), photo_id="p_x", credential_id="c_x",
                         signing_key_id=keystore.active_key_id(), created_at=NOW)


def test_sign_verify_roundtrip(keystore):
    signed = sign_binding(_binding(keystore), keystore)
    assert verify_signature(signed, keystore) == "ok"


def test_any_field_tamper_is_detected(keystore):
    signed = sign_binding(_binding(keystore), keystore)
    for field, value in [
        ("exact_sha256", "0" * 64),
        ("global_fingerprint", "ff" * 32),
        ("credential_id", "c_other"),
        ("photo_id", "p_other"),
        ("created_at", "2027-01-01T00:00:00+00:00"),
        ("photo_width", 1),
    ]:
        tampered = SignedBinding(
            record=dataclasses.replace(signed.record, **{field: value}),
            key_id=signed.key_id, signature_hex=signed.signature_hex)
        assert verify_signature(tampered, keystore) == "invalid_signature", field


def test_key_id_swap_detected(keystore):
    signed = sign_binding(_binding(keystore), keystore)
    other = keystore.rotate()
    swapped = SignedBinding(record=signed.record, key_id=other,
                            signature_hex=signed.signature_hex)
    assert verify_signature(swapped, keystore) == "key_id_mismatch"


def test_unknown_key_fails_closed(keystore, tmp_path):
    signed = sign_binding(_binding(keystore), keystore)
    empty_store = DevKeyStore(tmp_path / "other-keys")
    assert verify_signature(signed, empty_store) == "unknown_key"


def test_revoked_signing_key_fails_closed(keystore):
    signed = sign_binding(_binding(keystore), keystore)
    keystore.revoke(signed.key_id)
    assert verify_signature(signed, keystore) == "revoked_key"


def test_rotation_keeps_historical_verification(keystore):
    signed_old = sign_binding(_binding(keystore), keystore)
    keystore.rotate()
    # Old binding still verifies (verify-only key)...
    assert verify_signature(signed_old, keystore) == "ok"
    # ...but the old key can no longer sign.
    with pytest.raises(RuntimeError):
        sign_binding(signed_old.record, keystore)


def test_domain_separation_prefix_is_applied(keystore):
    signed = sign_binding(_binding(keystore), keystore)
    raw = signed.record.canonical_bytes()
    pub = keystore.public_key(signed.key_id)
    from cryptography.exceptions import InvalidSignature
    with pytest.raises(InvalidSignature):
        pub.verify(bytes.fromhex(signed.signature_hex), raw)  # without domain
    pub.verify(bytes.fromhex(signed.signature_hex), DOMAIN + raw)  # with domain
