"""In-memory credential registry — the reference model the future backend
(apps/api, Phase 2) must implement against PostgreSQL.

Identity model (spec §26):

    PHOTO (photo_id)
      └── CREDENTIAL (credential_id)  — signed photo-binding, photo <-> evidence
            ├── SHARE (share_id, label="LinkedIn")   → OPAQUE_RESOLUTION_ID in QR
            ├── SHARE (share_id, label="Email")      → OPAQUE_RESOLUTION_ID in QR
            └── SHARE (share_id, label="Conference") → OPAQUE_RESOLUTION_ID in QR

One photograph may carry many shares, each independently revocable and
traceable. The QR carries only the OPAQUE_RESOLUTION_ID — CSPRNG, never
derived from any other identifier.
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .record import SignedBinding


def new_photo_id() -> str:
    return "p_" + secrets.token_urlsafe(12)


def new_credential_id() -> str:
    return "c_" + secrets.token_urlsafe(12)


def new_share_id() -> str:
    return "s_" + secrets.token_urlsafe(12)


def new_opaque_resolution_id() -> str:
    # Spec §15: CSPRNG, 128 bits.
    return secrets.token_urlsafe(16)


@dataclass
class Share:
    share_id: str
    credential_id: str
    label: str
    opaque_resolution_id: str
    created_at: str
    revoked_at: str | None = None
    expires_at: str | None = None
    max_scans: int | None = None
    scan_count: int = 0

    def state(self, now: datetime | None = None) -> str:
        if self.revoked_at is not None:
            return "revoked"
        now = now or datetime.now(timezone.utc)
        if self.expires_at and now >= datetime.fromisoformat(self.expires_at):
            return "expired"
        if self.max_scans is not None and self.scan_count >= self.max_scans:
            return "scan_cap_reached"
        return "active"


@dataclass
class Credential:
    credential_id: str
    photo_id: str
    signed_binding: SignedBinding
    created_at: str
    revoked_at: str | None = None

    def state(self) -> str:
        return "revoked" if self.revoked_at else "active"


class CredentialRegistry:
    def __init__(self):
        self._credentials: dict[str, Credential] = {}
        self._shares: dict[str, Share] = {}           # by share_id
        self._by_opaque: dict[str, str] = {}          # opaque_resolution_id -> share_id

    # -- issuance ----------------------------------------------------------
    def register_credential(self, photo_id: str, signed_binding: SignedBinding,
                            created_at: str) -> Credential:
        cred = Credential(credential_id=signed_binding.record.credential_id,
                          photo_id=photo_id, signed_binding=signed_binding,
                          created_at=created_at)
        self._credentials[cred.credential_id] = cred
        return cred

    def mint_share(self, credential_id: str, label: str, created_at: str,
                   expires_at: str | None = None,
                   max_scans: int | None = None) -> Share:
        if credential_id not in self._credentials:
            raise KeyError(f"unknown credential: {credential_id}")
        share = Share(share_id=new_share_id(), credential_id=credential_id,
                      label=label, opaque_resolution_id=new_opaque_resolution_id(),
                      created_at=created_at, expires_at=expires_at,
                      max_scans=max_scans)
        self._shares[share.share_id] = share
        self._by_opaque[share.opaque_resolution_id] = share.share_id
        return share

    # -- resolution ---------------------------------------------------------
    def resolve(self, opaque_resolution_id: str) -> Share | None:
        sid = self._by_opaque.get(opaque_resolution_id)
        return self._shares.get(sid) if sid else None

    def credential(self, credential_id: str) -> Credential | None:
        return self._credentials.get(credential_id)

    def record_scan(self, share: Share) -> None:
        share.scan_count += 1

    # -- revocation ----------------------------------------------------------
    def revoke_share(self, share_id: str, at: str) -> None:
        self._shares[share_id].revoked_at = at

    def revoke_credential(self, credential_id: str, at: str) -> None:
        self._credentials[credential_id].revoked_at = at
