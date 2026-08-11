"""The signed photo-binding record.

Serialization (documented per spec §3):
- Format: JSON, UTF-8, sorted keys, separators (",", ":"), no floats in
  signed fields (all integers/strings), version pinned inside the payload.
- Domain separation: the signature is over
      b"photobind:binding:v1\\x00" + canonical_json_bytes
  so a binding signature can never be confused with any other signed object
  in this system.
- Signed fields: every field of the record EXCEPT the signature envelope
  itself (key_id and signature live in the envelope; key_id is ALSO repeated
  inside the signed payload so it cannot be swapped undetected).
- Signature algorithm: Ed25519 (RFC 8032) via the `cryptography` library.
- Verification procedure: reconstruct canonical bytes from the received
  record fields -> check envelope.key_id == record.signing_key_id ->
  resolve public key -> Ed25519 verify. Any failure is a hard failure.

ID vocabulary (spec §26) — no ambiguous "id" fields anywhere:
- PHOTO_ID: identifies the registered canonical image.
- CREDENTIAL_ID: identifies this signed binding (photo <-> credential).
- SHARE_ID: a user-labeled share minted from a credential.
- OPAQUE_RESOLUTION_ID: the CSPRNG value carried in the QR; resolves to a
  share. Never derived from any of the above.
- SIGNING_KEY_ID: which key signed the binding.
"""

import json
from dataclasses import dataclass, asdict, field

from cryptography.exceptions import InvalidSignature

from . import canonical, fingerprint
from .keys import DevKeyStore, SIGNATURE_ALGO

DOMAIN = b"photobind:binding:v1\x00"
BINDING_VERSION = 1


@dataclass(frozen=True)
class PhotoBinding:
    binding_version: int
    signature_algorithm: str        # "ed25519"
    canonicalization: str           # canonical.CANONICALIZATION_ID
    global_fingerprint_algorithm: str
    region_fingerprint_algorithm: str
    exact_sha256: str
    global_fingerprint: str
    region_fingerprints: list[str]     # per-tile phash64, hex
    region_chroma: list[list[int]]     # per-tile mean CIELAB (a,b) x10
    region_energy: list[int]           # per-tile log10(laplacian var+1) x1000
    photo_width: int
    photo_height: int
    photo_id: str
    credential_id: str
    signing_key_id: str
    created_at: str                 # ISO-8601 UTC, supplied by caller

    def canonical_bytes(self) -> bytes:
        return json.dumps(asdict(self), sort_keys=True,
                          separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class SignedBinding:
    record: PhotoBinding
    key_id: str
    signature_hex: str

    def to_dict(self) -> dict:
        return {"record": asdict(self.record), "key_id": self.key_id,
                "signature": self.signature_hex}

    @staticmethod
    def from_dict(d: dict) -> "SignedBinding":
        return SignedBinding(record=PhotoBinding(**d["record"]),
                             key_id=d["key_id"], signature_hex=d["signature"])


def build_binding(image_bytes: bytes, *, photo_id: str, credential_id: str,
                  signing_key_id: str, created_at: str) -> PhotoBinding:
    bgr = canonical.decode_bgr(image_bytes)
    gray = canonical.decode_gray(image_bytes)
    tiles = fingerprint.tile_features(bgr)
    h, w = bgr.shape[:2]
    return PhotoBinding(
        binding_version=BINDING_VERSION,
        signature_algorithm=SIGNATURE_ALGO,
        canonicalization=canonical.CANONICALIZATION_ID,
        global_fingerprint_algorithm=fingerprint.GLOBAL_ALGO,
        region_fingerprint_algorithm=fingerprint.REGION_ALGO,
        exact_sha256=canonical.exact_sha256(image_bytes),
        global_fingerprint=fingerprint.phash_global(gray),
        region_fingerprints=tiles["phash"],
        region_chroma=tiles["chroma"],
        region_energy=tiles["energy"],
        photo_width=w,
        photo_height=h,
        photo_id=photo_id,
        credential_id=credential_id,
        signing_key_id=signing_key_id,
        created_at=created_at,
    )


def sign_binding(record: PhotoBinding, keystore: DevKeyStore) -> SignedBinding:
    kid = record.signing_key_id
    if keystore.status(kid) != "active":
        raise RuntimeError(f"key {kid} is not the active signing key")
    sig = keystore.private_key(kid).sign(DOMAIN + record.canonical_bytes())
    return SignedBinding(record=record, key_id=kid, signature_hex=sig.hex())


def verify_signature(signed: SignedBinding, keystore: DevKeyStore) -> str:
    """Returns 'ok', or a failure reason. Callers must treat anything except
    'ok' as a hard, fail-closed error."""
    if signed.key_id != signed.record.signing_key_id:
        return "key_id_mismatch"
    status = keystore.status(signed.key_id)
    if status == "unknown":
        return "unknown_key"
    if status == "revoked":
        return "revoked_key"
    try:
        keystore.public_key(signed.key_id).verify(
            bytes.fromhex(signed.signature_hex),
            DOMAIN + signed.record.canonical_bytes())
    except (InvalidSignature, ValueError):
        return "invalid_signature"
    return "ok"
