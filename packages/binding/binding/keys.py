"""Signing-key lifecycle. Ed25519 via the `cryptography` library — no custom
primitives.

*** DEVELOPMENT / TEST KEY MANAGEMENT ONLY ***
Keys live as files under a local directory. This is NOT a KMS or HSM and is
not represented as one. Production deployment requires a real KMS/HSM behind
this same interface; until that exists, treat every key produced here as a
test key.

Lifecycle model:
- Every key has a SIGNING_KEY_ID: "k" + 16 hex chars of the SHA-256 of the
  public key (stable, content-derived).
- Exactly one key is ACTIVE (used for new signatures).
- Rotation creates a new active key; old keys remain VERIFY-ONLY so
  historical bindings keep verifying.
- Revoking a key makes signatures under it UNTRUSTED: verification of a
  binding signed by a revoked key must fail closed (the caller decides
  whether that means re-issue or reject).
"""

import json
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey, Ed25519PublicKey)

import hashlib

SIGNATURE_ALGO = "ed25519"


def _key_id(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw,
                              serialization.PublicFormat.Raw)
    return "k" + hashlib.sha256(raw).hexdigest()[:16]


@dataclass
class KeyRecord:
    key_id: str
    status: str  # active | verify_only | revoked


class DevKeyStore:
    """File-backed keystore. See module docstring: dev/test only."""

    def __init__(self, root: Path | str):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._state_path = self.root / "state.json"
        if not self._state_path.exists():
            self._write_state({"active": None, "keys": {}})

    # -- state ------------------------------------------------------------
    def _read_state(self) -> dict:
        return json.loads(self._state_path.read_text())

    def _write_state(self, state: dict) -> None:
        self._state_path.write_text(json.dumps(state, indent=2, sort_keys=True))

    # -- lifecycle ---------------------------------------------------------
    def generate(self, activate: bool = True) -> str:
        priv = Ed25519PrivateKey.generate()
        kid = _key_id(priv.public_key())
        (self.root / f"{kid}.priv.pem").write_bytes(priv.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption()))
        (self.root / f"{kid}.pub.pem").write_bytes(priv.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo))
        state = self._read_state()
        state["keys"][kid] = "verify_only"
        if activate:
            if state["active"]:
                state["keys"][state["active"]] = "verify_only"
            state["keys"][kid] = "active"
            state["active"] = kid
        self._write_state(state)
        return kid

    def rotate(self) -> str:
        """New active key; the previous key stays verify-only."""
        return self.generate(activate=True)

    def revoke(self, key_id: str) -> None:
        state = self._read_state()
        if key_id not in state["keys"]:
            raise KeyError(f"unknown key: {key_id}")
        state["keys"][key_id] = "revoked"
        if state["active"] == key_id:
            state["active"] = None
        self._write_state(state)

    # -- queries -----------------------------------------------------------
    def active_key_id(self) -> str:
        kid = self._read_state()["active"]
        if not kid:
            raise RuntimeError("no active signing key (generate or rotate first)")
        return kid

    def status(self, key_id: str) -> str:
        return self._read_state()["keys"].get(key_id, "unknown")

    def list_keys(self) -> list[KeyRecord]:
        return [KeyRecord(k, s) for k, s in sorted(self._read_state()["keys"].items())]

    # -- crypto ------------------------------------------------------------
    def private_key(self, key_id: str) -> Ed25519PrivateKey:
        path = self.root / f"{key_id}.priv.pem"
        if not path.exists():
            raise KeyError(f"no private key material for {key_id}")
        return serialization.load_pem_private_key(path.read_bytes(), password=None)

    def public_key(self, key_id: str) -> Ed25519PublicKey:
        path = self.root / f"{key_id}.pub.pem"
        if not path.exists():
            raise KeyError(f"unknown key: {key_id}")
        return serialization.load_pem_public_key(path.read_bytes())
