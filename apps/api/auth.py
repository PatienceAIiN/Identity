"""Authentication: email+password (argon2id) and Google ID-token exchange.

Sessions are opaque CSPRNG tokens stored hashed server-side, delivered in an
httpOnly cookie (Secure+SameSite=Lax when PHOTOBIND_SECURE_COOKIES=1; dev
over plain http needs it off). No JWTs in localStorage, ever.

Google: the client obtains an ID token (GIS / Credential Manager) and posts
it here. Verification calls google-auth over Google's JWKS when
PHOTOBIND_GOOGLE_CLIENT_ID is configured; in dev/test, a stub verifier can
be injected. Signature, aud, iss, and exp are all checked — a decode-only
path is never used in production configuration.
"""

import hashlib
import os
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from db import SessionToken, User, utcnow

_ph = PasswordHasher()  # argon2id defaults
SESSION_TTL = timedelta(days=14)
COOKIE_NAME = "pb_session"

# Email verification
OTP_TTL = timedelta(minutes=15)
OTP_MAX_ATTEMPTS = 5          # wrong guesses before the pending signup dies
OTP_MAX_SENDS = 5             # resends per pending signup
# Bumped whenever terms/privacy change materially, so recorded consent
# always says which version was agreed to.
TERMS_VERSION = "2026-08-10"


def new_otp() -> str:
    """6 digits from a CSPRNG. Not time-derived, not sequential."""
    return f"{secrets.randbelow(1_000_000):06d}"


def hash_otp(code: str) -> str:
    """Stored hashed: a database read must not reveal a live code."""
    return hashlib.sha256(code.encode()).hexdigest()


def otp_matches(stored_hash: str, code: str) -> bool:
    return secrets.compare_digest(stored_hash, hash_otp(code.strip()))


def new_user_id() -> str:
    return "u_" + secrets.token_urlsafe(12)


def hash_password(pw: str) -> str:
    if len(pw) < 12:
        raise ValueError("Passwords need at least 12 characters.")
    return _ph.hash(pw)


def check_password(hash_: str | None, pw: str) -> bool:
    if not hash_:
        return False
    try:
        _ph.verify(hash_, pw)
        return True
    except VerifyMismatchError:
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_session(db, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    db.add(SessionToken(token_hash=_token_hash(token), user_id=user_id,
                        expires_at=utcnow() + SESSION_TTL))
    db.commit()
    return token


def resolve_session(db, token: str | None) -> User | None:
    if not token:
        return None
    st = db.get(SessionToken, _token_hash(token))
    if st is None or st.revoked_at is not None:
        return None
    exp = st.expires_at
    if exp.tzinfo is None:  # SQLite returns naive datetimes
        from datetime import timezone
        exp = exp.replace(tzinfo=timezone.utc)
    if utcnow() >= exp:
        return None
    return db.get(User, st.user_id)


def revoke_session(db, token: str | None) -> None:
    if not token:
        return
    st = db.get(SessionToken, _token_hash(token))
    if st:
        st.revoked_at = utcnow()
        db.commit()


class GoogleVerifier:
    """Verifies Google ID tokens.

    A project usually has more than one OAuth client — a Web client (whose id is
    the audience Credential Manager and the browser both request) and an Android
    client (which exists so Google trusts a particular package + signing key).
    Tokens can therefore legitimately arrive with different `aud` values, so the
    audience is checked against an explicit allow-list rather than a single id.
    It is still strict: an id not on the list is refused.

    Without configuration this raises rather than pretending to verify.
    """

    def __init__(self):
        self.client_id = os.environ.get("PHOTOBIND_GOOGLE_CLIENT_ID", "")
        extra = os.environ.get("PHOTOBIND_GOOGLE_ALLOWED_AUDIENCES", "")
        self.allowed = [a.strip() for a in
                        ([self.client_id] + extra.split(",")) if a.strip()]

    def verify(self, id_token_str: str) -> dict:
        if not self.allowed:
            raise RuntimeError("Google sign-in is not configured "
                               "(PHOTOBIND_GOOGLE_CLIENT_ID unset)")
        from google.oauth2 import id_token as gid
        from google.auth.transport import requests as grequests

        # audience=None so the library verifies signature and expiry, then the
        # audience is checked here against the allow-list. Skipping the aud check
        # entirely would accept a token minted for someone else's app.
        info = gid.verify_oauth2_token(id_token_str, grequests.Request(),
                                       audience=None)
        if info.get("iss") not in ("accounts.google.com",
                                   "https://accounts.google.com"):
            raise ValueError("wrong issuer")
        aud = info.get("aud", "")
        if aud not in self.allowed:
            raise ValueError(f"token audience {aud!r} is not one of this "
                             f"project's OAuth clients")
        if not info.get("email_verified", False):
            raise ValueError("Google has not verified that email address")
        return {"sub": info["sub"], "email": info.get("email", ""),
                "name": info.get("name", "")}
