"""Developer API keys, the third-party API, and the admin panel.

Three audiences, one module, because they share one security model:

  * Developers sign in with an ordinary account and mint API keys. A key is an
    id plus a secret. The secret is shown exactly once, at creation, and is held
    here encrypted rather than hashed — signed requests need the server to
    recompute the HMAC, which a one-way hash cannot do. The decryption key lives
    in a deployment secret, so a stolen database on its own forges nothing.

  * Third-party apps call /api/v1/* with that key. Requests are signed
    (HMAC-SHA256 over method, path, timestamp, nonce and a digest of the body),
    which is what CLAUDE.md §4 requires: a bearer token in a mobile app is a
    token in everyone's hands, whereas a signature proves the caller holds the
    secret without ever sending it. Timestamps outside a five-minute window are
    refused and nonces cannot be reused inside it, so a captured request cannot
    be replayed.

  * An operator signs in to /admin with a password that lives in a deployment
    secret as an argon2 hash — never in this repository, and never in plaintext
    on disk. Admin sessions are separate from user sessions, so an admin cookie
    can never act as a user's and vice versa.

What the admin panel structurally cannot do: read what any code opens. Payloads
are stored as ciphertext and the keys live in URL fragments that never reach the
server, so there is nothing here for an operator to decrypt. That is a property
of the storage model, not a permission check, and nothing in this file weakens
it.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy import ForeignKey, Integer, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from auth import (OTP_MAX_ATTEMPTS, OTP_MAX_SENDS, OTP_TTL, hash_otp, new_otp,
                  otp_matches)
from db import (Base, Credential, Photo, RevokedShare, ScanEvent, Share, User,
                utcnow)

# Signed requests are accepted this far either side of now. Long enough to
# survive clock skew and a slow mobile network; short enough that a captured
# request is stale before it is useful.
SIGNATURE_WINDOW_S = 300
# Per key, per minute. A signed request is cheap to make and each one can create
# a code, which is not cheap to serve — so the ceiling is on the key, not on the
# address: a key used from a hundred phones is still one integration's budget.
RATE_PER_MINUTE = 30
ADMIN_SESSION_TTL = timedelta(hours=8)
ADMIN_COOKIE = "pb_admin"
DEV_COOKIE = "pb_dev"
DEV_SESSION_TTL = timedelta(days=14)
API_KEY_PREFIX = "pbk_"

SCOPES = ("codes:read", "codes:write")


class ApiKey(Base):
    """A developer's key, with its secret encrypted at rest."""
    __tablename__ = "api_keys"
    key_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    name: Mapped[str] = mapped_column(String, default="")
    # The secret, encrypted at rest — not hashed. Signed requests require the
    # server to recompute the HMAC, which a one-way hash cannot do; this is the
    # same trade AWS makes with its secret access keys. Encryption means a stolen
    # database alone does not let anyone forge a signature: the key that decrypts
    # these lives in the deployment secret, not in the database.
    secret_enc: Mapped[str] = mapped_column(String)
    scopes: Mapped[str] = mapped_column(String, default="codes:read,codes:write")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)

    @property
    def scope_list(self) -> list[str]:
        return [s for s in self.scopes.split(",") if s]

    @property
    def active(self) -> bool:
        return self.revoked_at is None


class ApiCallDay(Base):
    """Calls per key per day.

    Aggregated rather than one row per request: the operator needs volume, and
    a per-request log of a third party's traffic is a liability we have no use
    for.
    """
    __tablename__ = "api_call_days"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String, index=True)
    day: Mapped[str] = mapped_column(String, index=True)      # YYYY-MM-DD
    count: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)


class Developer(Base):
    """A developer account, with its own credentials.

    Deliberately not a flag on a user: an integrator signing in to manage keys is
    a different person doing a different job from someone making codes for
    themselves, and a normal account should never see an API key.

    Each developer owns a `user_id` — a shadow account that owns the codes their
    key creates. That keeps every foreign key and every ownership check in the
    rest of the system exactly as it was, rather than teaching all of it about a
    second kind of owner. When a developer signs up with the same address as an
    existing account, that account becomes the shadow, so keys they already hold
    keep working.
    """
    __tablename__ = "developers"
    developer_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingDeveloper(Base):
    """A developer signup awaiting its emailed code.

    Signing up does not create the account. Without this, anyone could create a
    developer account on an address they don't control — and the welcome email,
    which spells out the key rules, would land on a stranger.
    """
    __tablename__ = "pending_developers"
    email: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String)
    code_hash: Mapped[str] = mapped_column(String)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sends: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DevSession(Base):
    """Developer sessions — a third cookie namespace, so a developer session can
    never act as a user's or an operator's."""
    __tablename__ = "dev_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    developer_id: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class AdminSession(Base):
    """Operator sessions, deliberately a different table from user sessions."""
    __tablename__ = "admin_sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)


class CreateKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    scopes: list[str] | None = None


class DevSignUp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = ""
    email: str
    password: str


class DevVerify(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    code: str


class DevResend(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


class DevSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class AdminLogin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str


class AdminUpdateUser(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None


class UpdateCodeViaApi(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    expires_at: str | None = None
    max_scans: int | None = None


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def body_digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def signing_string(method: str, path: str, timestamp: str, nonce: str,
                   raw_body: bytes) -> str:
    """Exactly what both sides sign. Documented so an integrator can reproduce
    it without reading our source."""
    return "\n".join([method.upper(), path, timestamp, nonce, body_digest(raw_body)])


def sign(secret: str, method: str, path: str, timestamp: str, nonce: str,
         raw_body: bytes = b"") -> str:
    return hmac.new(secret.encode(),
                    signing_string(method, path, timestamp, nonce, raw_body).encode(),
                    hashlib.sha256).hexdigest()


def make_router(SessionLocal, secret_box, check_secret, hash_secret,
                new_user_id, resolve_user, encode_and_store,
                notify=None, mailer=None, public_host="") -> APIRouter:
    """
    secret_box encrypts and decrypts API key secrets (AES-256-GCM, key from the
    deployment secret). check_secret is the app's argon2 verifier, used for the
    admin password — a human-chosen password, which is exactly what a slow hash
    is for.
    """
    router = APIRouter()

    # Seen nonces, per key, with their expiry. In-process: correct for a single
    # instance, and the honest limitation on more than one — production wants
    # Redis here, the same as the rate limiter.
    seen_nonces: dict[str, float] = {}
    call_times: dict[str, list[float]] = {}

    def db_session():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def announce(user_id: str, event: str, data: dict) -> None:
        if notify:
            notify(user_id, event, data)

    # ── developer accounts ──────────────────────────────────────────────────
    def require_dev(request: Request, db) -> Developer:
        token = request.cookies.get(DEV_COOKIE)
        if not token:
            raise HTTPException(401, "Developer sign-in required.")
        row = db.get(DevSession, _token_hash(token))
        if row is None or row.revoked_at is not None:
            raise HTTPException(401, "Developer sign-in required.")
        if row.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
            raise HTTPException(401, "Developer session expired.")
        dev = db.get(Developer, row.developer_id)
        if dev is None:
            raise HTTPException(401, "Developer sign-in required.")
        return dev

    def issue_dev_session(db, resp: Response, dev: Developer) -> None:
        token = secrets.token_urlsafe(32)
        db.add(DevSession(token_hash=_token_hash(token), developer_id=dev.developer_id,
                          expires_at=utcnow() + DEV_SESSION_TTL))
        db.commit()
        resp.set_cookie(DEV_COOKIE, token, httponly=True, samesite="lax",
                        secure=os.environ.get("PHOTOBIND_SECURE_COOKIES", "0") == "1",
                        max_age=int(DEV_SESSION_TTL.total_seconds()), path="/")

    def _create_developer(db, email: str, name: str, password_hash: str) -> Developer:
        """Turn a verified signup into an account plus its shadow code owner."""
        existing = db.query(User).filter_by(email=email).first()
        if existing is not None:
            shadow_id = existing.user_id
        else:
            shadow = User(user_id=new_user_id(), email=email,
                          name=name or "developer",
                          password_hash=hash_secret(secrets.token_urlsafe(32)),
                          email_verified_at=utcnow())
            db.add(shadow)
            db.flush()
            shadow_id = shadow.user_id
        dev = Developer(developer_id="dv_" + secrets.token_urlsafe(9), email=email,
                        name=name, password_hash=password_hash, user_id=shadow_id)
        db.add(dev)
        db.commit()
        return dev

    @router.post("/v1/dev/auth/signup", status_code=202)
    def dev_signup(body: DevSignUp, db=Depends(db_session)):
        """Emails a code. The account is created by /verify, not by this."""
        email = body.email.strip().lower()
        if "@" not in email:
            raise HTTPException(422, "That email address is missing an @.")
        if len(body.password) < 12:
            raise HTTPException(422, "Developer passwords need at least 12 "
                                     "characters.")
        if db.query(Developer).filter_by(email=email).first():
            raise HTTPException(409, "A developer account already exists for that "
                                     "address. Sign in instead.")
        row = db.get(PendingDeveloper, email)
        code = new_otp()
        if row is None:
            row = PendingDeveloper(email=email, name=body.name.strip()[:120],
                                   password_hash=hash_secret(body.password),
                                   code_hash=hash_otp(code),
                                   expires_at=utcnow() + OTP_TTL)
            db.add(row)
        else:
            if row.sends >= OTP_MAX_SENDS:
                raise HTTPException(429, "Too many codes sent to that address. "
                                         "Try again later.")
            # A repeat signup replaces the pending one: the latest attempt is the
            # one the person is actually looking at.
            row.name = body.name.strip()[:120]
            row.password_hash = hash_secret(body.password)
            row.code_hash = hash_otp(code)
            row.attempts = 0
            row.sends += 1
            row.expires_at = utcnow() + OTP_TTL
        db.commit()
        delivery = (mailer.send_code(email, code, int(OTP_TTL.total_seconds() // 60))
                    if mailer else None)
        return {"email": email, "delivery": delivery,
                "expires_in_minutes": int(OTP_TTL.total_seconds() // 60)}

    @router.post("/v1/dev/auth/verify", status_code=201)
    def dev_verify(body: DevVerify, resp: Response, db=Depends(db_session)):
        email = body.email.strip().lower()
        row = db.get(PendingDeveloper, email)
        if row is None:
            raise HTTPException(404, "No signup waiting for that address.")
        if row.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
            db.delete(row)
            db.commit()
            raise HTTPException(410, "That code has expired. Start again.")
        if row.attempts >= OTP_MAX_ATTEMPTS:
            raise HTTPException(429, "Too many wrong codes. Start again.")
        if not otp_matches(row.code_hash, body.code.strip()):
            row.attempts += 1
            db.commit()
            raise HTTPException(401, "That code doesn't match.")
        dev = _create_developer(db, email, row.name, row.password_hash)
        db.delete(db.get(PendingDeveloper, email))
        db.commit()
        issue_dev_session(db, resp, dev)
        delivery = None
        if mailer:
            delivery = mailer.send_dev_welcome(dev.email, dev.name,
                                               f"{public_host}/dev")
        return {"developer_id": dev.developer_id, "email": dev.email,
                "name": dev.name, "welcome_email": delivery}

    @router.post("/v1/dev/auth/resend")
    def dev_resend(body: DevResend, db=Depends(db_session)):
        email = body.email.strip().lower()
        row = db.get(PendingDeveloper, email)
        if row is None:
            raise HTTPException(404, "No signup waiting for that address.")
        if row.sends >= OTP_MAX_SENDS:
            raise HTTPException(429, "Too many codes sent to that address.")
        code = new_otp()
        row.code_hash = hash_otp(code)
        row.attempts = 0
        row.sends += 1
        row.expires_at = utcnow() + OTP_TTL
        db.commit()
        delivery = (mailer.send_code(email, code, int(OTP_TTL.total_seconds() // 60))
                    if mailer else None)
        return {"email": email, "delivery": delivery}

    @router.post("/v1/dev/auth/signin")
    def dev_signin(body: DevSignIn, resp: Response, db=Depends(db_session)):
        dev = db.query(Developer).filter_by(email=body.email.strip().lower()).first()
        # One message for a missing account and a wrong password, so this cannot
        # be used to enumerate who has a developer account.
        if dev is None or not check_secret(dev.password_hash, body.password):
            raise HTTPException(401, "Wrong email or password.")
        issue_dev_session(db, resp, dev)
        return {"developer_id": dev.developer_id, "email": dev.email,
                "name": dev.name}

    @router.post("/v1/dev/auth/signout")
    def dev_signout(request: Request, resp: Response, db=Depends(db_session)):
        token = request.cookies.get(DEV_COOKIE)
        if token:
            row = db.get(DevSession, _token_hash(token))
            if row:
                row.revoked_at = utcnow()
                db.commit()
        resp.delete_cookie(DEV_COOKIE, path="/")
        return {"signed_in": False}

    @router.get("/v1/dev/me")
    def dev_me(request: Request, db=Depends(db_session)):
        dev = require_dev(request, db)
        return {"developer_id": dev.developer_id, "email": dev.email,
                "name": dev.name}

    @router.post("/v1/dev/keys", status_code=201)
    def create_key(body: CreateKey, request: Request, db=Depends(db_session)):
        u = require_dev(request, db)
        if db.query(ApiKey).filter_by(user_id=u.user_id, revoked_at=None).count() >= 20:
            raise HTTPException(409, "You already have 20 live keys. Revoke one "
                                     "before creating another.")
        scopes = body.scopes or list(SCOPES)
        unknown = [s for s in scopes if s not in SCOPES]
        if unknown:
            raise HTTPException(422, f"unknown scopes {unknown}; "
                                     f"valid scopes are {list(SCOPES)}")
        key_id = API_KEY_PREFIX + secrets.token_urlsafe(9)
        secret = secrets.token_urlsafe(32)
        db.add(ApiKey(key_id=key_id, user_id=u.user_id, name=body.name.strip()[:80],
                      secret_enc=secret_box.encrypt(secret), scopes=",".join(scopes)))
        db.commit()
        announce(u.user_id, "keys.changed", {"reason": "created"})
        if mailer:
            # The secret is deliberately not in it: an emailed secret is a secret
            # sitting in a mailbox.
            mailer.send_dev_key_created(u.email, u.name, key_id,
                                        " ".join(scopes), f"{public_host}/dev")
        # The only time the secret exists outside the caller's own storage.
        return {"key_id": key_id, "secret": secret, "scopes": scopes,
                "shown_once": True,
                "note": "Store this secret now. It is hashed on our side and "
                        "cannot be shown again — revoke the key and make another "
                        "if you lose it."}

    @router.get("/v1/dev/keys")
    def list_keys(request: Request, db=Depends(db_session)):
        u = require_dev(request, db)
        keys = (db.query(ApiKey).filter_by(user_id=u.user_id)
                .order_by(ApiKey.created_at.desc()).all())
        return {"keys": [{
            "key_id": k.key_id, "name": k.name, "scopes": k.scope_list,
            "created_at": k.created_at.isoformat(),
            "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
            "revoked_at": k.revoked_at.isoformat() if k.revoked_at else None,
            "call_count": k.call_count,
        } for k in keys]}

    @router.delete("/v1/dev/keys/{key_id}")
    def revoke_key(key_id: str, request: Request, db=Depends(db_session)):
        u = require_dev(request, db)
        k = db.get(ApiKey, key_id)
        if k is None or k.user_id != u.user_id:
            raise HTTPException(404, "unknown key")
        k.revoked_at = k.revoked_at or utcnow()
        db.commit()
        announce(u.user_id, "keys.changed", {"reason": "revoked"})
        return {"key_id": key_id, "revoked": True}

    @router.get("/v1/dev/usage")
    def usage(request: Request, db=Depends(db_session)):
        u = require_dev(request, db)
        key_ids = [k.key_id for k in db.query(ApiKey).filter_by(user_id=u.user_id)]
        if not key_ids:
            return {"days": [], "total": 0}
        rows = (db.query(ApiCallDay)
                .filter(ApiCallDay.key_id.in_(key_ids))
                .order_by(ApiCallDay.day.desc()).limit(60).all())
        return {"days": [{"day": r.day, "key_id": r.key_id, "count": r.count,
                          "rejected": r.rejected} for r in rows],
                "total": sum(r.count for r in rows)}

    @router.delete("/v1/dev/account")
    def delete_dev_account(request: Request, resp: Response, confirm: str = "",
                           db=Depends(db_session)):
        """Delete the developer account and revoke every key it holds.

        The shadow account that owns codes created through the API is left alone:
        those codes may be in use, and deleting someone's codes is a different
        decision from closing a developer account. Keys stop working immediately.
        """
        dev = require_dev(request, db)
        if confirm != "DELETE":
            raise HTTPException(422, 'Type DELETE to confirm.')
        keys = db.query(ApiKey).filter_by(user_id=dev.user_id).all()
        now = utcnow()
        for k in keys:
            k.revoked_at = k.revoked_at or now
        email, name = dev.email, dev.name
        (db.query(DevSession).filter_by(developer_id=dev.developer_id)
           .delete(synchronize_session=False))
        db.delete(db.get(Developer, dev.developer_id))
        db.commit()
        resp.delete_cookie(DEV_COOKIE, path="/")
        delivery = None
        if mailer:
            delivery = mailer.send_dev_deleted(email, name, len(keys))
        return {"deleted": True, "keys_revoked": len(keys),
                "confirmation_email": delivery}

    # ── third-party API: signed requests only ───────────────────────────────
    async def authenticate(request: Request, db, need: str) -> ApiKey:
        key_id = request.headers.get("x-api-key", "")
        ts = request.headers.get("x-api-timestamp", "")
        nonce = request.headers.get("x-api-nonce", "")
        signature = request.headers.get("x-api-signature", "")
        if not (key_id and ts and nonce and signature):
            raise HTTPException(401, "Sign the request: X-Api-Key, "
                                     "X-Api-Timestamp, X-Api-Nonce and "
                                     "X-Api-Signature are all required.")
        key = db.get(ApiKey, key_id)
        # A missing key and a revoked key answer the same way: whether an id
        # exists is not something an unauthenticated caller needs to learn.
        if key is None or not key.active:
            raise HTTPException(401, "Unknown or revoked key.")

        try:
            drift = abs(time.time() - float(ts))
        except ValueError:
            raise HTTPException(401, "X-Api-Timestamp must be unix seconds.")
        if drift > SIGNATURE_WINDOW_S:
            _count(db, key, rejected=True)
            raise HTTPException(401, f"Timestamp is {int(drift)}s out; requests "
                                     f"are accepted within {SIGNATURE_WINDOW_S}s. "
                                     f"Check the clock on the calling machine.")

        now = time.time()
        for used, expiry in list(seen_nonces.items()):
            if expiry < now:
                seen_nonces.pop(used, None)
        nonce_key = f"{key_id}:{nonce}"
        if nonce_key in seen_nonces:
            _count(db, key, rejected=True)
            raise HTTPException(401, "That nonce has already been used. Send a "
                                     "fresh nonce per request.")

        raw = await request.body()
        if not check_signature(key, request.method, request.url.path, ts, nonce,
                               raw, signature):
            _count(db, key, rejected=True)
            raise HTTPException(401, "Signature does not match.")

        if need not in key.scope_list:
            _count(db, key, rejected=True)
            raise HTTPException(403, f"This key lacks the {need} scope.")

        # Checked only once the signature has proved the caller holds the secret,
        # so nobody can burn someone else's allowance by spraying forged calls.
        recent = [t for t in call_times.get(key_id, []) if now - t < 60]
        if len(recent) >= RATE_PER_MINUTE:
            _count(db, key, rejected=True)
            raise HTTPException(429, f"{RATE_PER_MINUTE} requests a minute per "
                                     f"key. Retry in a moment.",
                                headers={"Retry-After": "60"})
        recent.append(now)
        call_times[key_id] = recent

        seen_nonces[nonce_key] = now + SIGNATURE_WINDOW_S
        key.last_used_at = utcnow()
        _count(db, key)
        db.commit()
        return key

    def check_signature(key: ApiKey, method: str, path: str, ts: str, nonce: str,
                        raw: bytes, presented: str) -> bool:
        """Recompute the expected signature and compare in constant time."""
        try:
            secret = secret_box.decrypt(key.secret_enc)
        except Exception:
            # An undecryptable secret means the signing key changed. Refuse
            # rather than fall back to something weaker.
            return False
        want = sign(secret, method, path, ts, nonce, raw)
        return hmac.compare_digest(want, presented)

    def _count(db, key: ApiKey, rejected: bool = False) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = (db.query(ApiCallDay)
               .filter_by(key_id=key.key_id, day=today).first())
        if row is None:
            row = ApiCallDay(key_id=key.key_id, day=today, count=0, rejected=0)
            db.add(row)
        if rejected:
            row.rejected += 1
        else:
            row.count += 1
            key.call_count += 1
        db.commit()

    def code_dict(db, cred: Credential, share: Share) -> dict:
        return {"credential_id": cred.credential_id, "share_id": share.share_id,
                "opaque_resolution_id": share.opaque_resolution_id,
                "label": share.label,
                "state": "revoked" if cred.revoked_at else share.state(),
                "scan_count": share.scan_count,
                "decode_rate": cred.decode_rate,
                "created_at": share.created_at.isoformat(),
                "expires_at": share.expires_at.isoformat() if share.expires_at else None,
                "max_scans": share.max_scans}

    @router.get("/api/v1/codes")
    async def api_list(request: Request, db=Depends(db_session)):
        key = await authenticate(request, db, "codes:read")
        out = []
        for cred in db.query(Credential).filter_by(user_id=key.user_id).all():
            for share in db.query(Share).filter_by(credential_id=cred.credential_id):
                out.append(code_dict(db, cred, share))
        return {"codes": out, "count": len(out)}

    @router.get("/api/v1/codes/{credential_id}")
    async def api_get(credential_id: str, request: Request, db=Depends(db_session)):
        key = await authenticate(request, db, "codes:read")
        cred = db.get(Credential, credential_id)
        if cred is None or cred.user_id != key.user_id:
            raise HTTPException(404, "unknown code")
        shares = db.query(Share).filter_by(credential_id=credential_id).all()
        return {"code": [code_dict(db, cred, s) for s in shares]}

    @router.post("/api/v1/codes", status_code=201)
    async def api_create(request: Request, db=Depends(db_session)):
        """Create a code from ciphertext.

        Same rule as every other ingress: the payload arrives encrypted or not at
        all. An integrator that wants us to encrypt for them is asking for a
        product we deliberately do not offer.
        """
        key = await authenticate(request, db, "codes:write")
        raw = await request.body()
        try:
            payload = json.loads(raw or b"{}")
        except ValueError:
            raise HTTPException(422, "body must be JSON")
        for field in ("photo_b64", "ciphertext_b64", "nonce_b64"):
            if not payload.get(field):
                raise HTTPException(422, f"{field} is required")
        if "plaintext" in payload or "payload" in payload:
            raise HTTPException(422, "this endpoint accepts ciphertext only; "
                                     "encrypt on your side before sending")
        import base64
        try:
            photo = base64.b64decode(payload["photo_b64"], validate=True)
        except Exception:
            raise HTTPException(422, "photo_b64 is not valid base64")
        result = encode_and_store(
            db, key.user_id, photo, payload["ciphertext_b64"], payload["nonce_b64"],
            str(payload.get("label", "API"))[:120],
            str(payload.get("fragment_key", "")),
            str(payload.get("coverage", "full")))
        announce(key.user_id, "codes.changed", {"reason": "created-via-api"})
        return result

    @router.patch("/api/v1/shares/{share_id}")
    async def api_update(share_id: str, request: Request, db=Depends(db_session)):
        key = await authenticate(request, db, "codes:write")
        raw = await request.body()
        try:
            body = UpdateCodeViaApi(**json.loads(raw or b"{}"))
        except ValueError as e:
            raise HTTPException(422, str(e))
        share = db.get(Share, share_id)
        cred = db.get(Credential, share.credential_id) if share else None
        if share is None or cred is None or cred.user_id != key.user_id:
            raise HTTPException(404, "unknown share")
        if body.label is not None:
            share.label = body.label.strip()[:120]
        if body.expires_at is not None:
            if body.expires_at == "":
                share.expires_at = None
            else:
                try:
                    when = datetime.fromisoformat(body.expires_at)
                except ValueError:
                    raise HTTPException(422, "expires_at must be ISO 8601 or empty")
                share.expires_at = (when if when.tzinfo
                                    else when.replace(tzinfo=timezone.utc))
        if body.max_scans is not None:
            if body.max_scans < 0:
                raise HTTPException(422, "max_scans cannot be negative")
            share.max_scans = body.max_scans or None
        db.commit()
        announce(key.user_id, "codes.changed", {"reason": "updated-via-api"})
        return code_dict(db, cred, share)

    @router.delete("/api/v1/shares/{share_id}")
    async def api_revoke(share_id: str, request: Request, db=Depends(db_session)):
        key = await authenticate(request, db, "codes:write")
        share = db.get(Share, share_id)
        cred = db.get(Credential, share.credential_id) if share else None
        if share is None or cred is None or cred.user_id != key.user_id:
            raise HTTPException(404, "unknown share")
        share.revoked_at = share.revoked_at or utcnow()
        db.commit()
        announce(key.user_id, "codes.changed", {"reason": "revoked-via-api"})
        return {"share_id": share_id, "state": "revoked"}

    @router.delete("/api/v1/codes/{credential_id}")
    async def api_delete(credential_id: str, request: Request, db=Depends(db_session)):
        key = await authenticate(request, db, "codes:write")
        cred = db.get(Credential, credential_id)
        if cred is None or cred.user_id != key.user_id:
            raise HTTPException(404, "unknown code")
        shares = db.query(Share).filter_by(credential_id=credential_id).all()
        now = utcnow()
        for s in shares:
            if not db.get(RevokedShare, s.opaque_resolution_id):
                db.add(RevokedShare(opaque_resolution_id=s.opaque_resolution_id,
                                    revoked_at=s.revoked_at or now))
        db.flush()
        ids = [s.share_id for s in shares]
        if ids:
            (db.query(ScanEvent).filter(ScanEvent.share_id.in_(ids))
               .delete(synchronize_session=False))
            (db.query(Share).filter(Share.share_id.in_(ids))
               .delete(synchronize_session=False))
        photo_id = cred.photo_id
        db.query(Credential).filter_by(credential_id=credential_id).delete(
            synchronize_session=False)
        db.query(Photo).filter_by(photo_id=photo_id).delete(synchronize_session=False)
        db.expire_all()
        db.commit()
        announce(key.user_id, "codes.changed", {"reason": "deleted-via-api"})
        return {"credential_id": credential_id, "deleted": True,
                "copies_switched_off": len(shares)}

    # ── admin ───────────────────────────────────────────────────────────────
    def admin_password_hash() -> str:
        return os.environ.get("PHOTOBIND_ADMIN_PASSWORD_HASH", "")

    def require_admin(request: Request, db) -> None:
        token = request.cookies.get(ADMIN_COOKIE)
        if not token:
            raise HTTPException(401, "Admin sign-in required.")
        row = db.get(AdminSession, _token_hash(token))
        if row is None or row.revoked_at is not None:
            raise HTTPException(401, "Admin sign-in required.")
        if row.expires_at.replace(tzinfo=timezone.utc) <= utcnow():
            raise HTTPException(401, "Admin session expired.")

    admin_attempts: dict[str, list[float]] = {}

    @router.post("/v1/admin/login")
    def admin_login(body: AdminLogin, request: Request, resp: Response,
                    db=Depends(db_session)):
        stored = admin_password_hash()
        if not stored:
            raise HTTPException(503, "Admin access is not configured on this "
                                     "server.")
        # Five attempts a minute per address. The panel exposes every account, so
        # it does not get to be brute-forceable.
        who = request.client.host if request.client else "unknown"
        now = time.time()
        tries = [t for t in admin_attempts.get(who, []) if now - t < 60]
        if len(tries) >= 5:
            raise HTTPException(429, "Too many attempts. Wait a minute.")
        tries.append(now)
        admin_attempts[who] = tries

        if not check_secret(stored, body.password):
            raise HTTPException(401, "Wrong password.")
        token = secrets.token_urlsafe(32)
        db.add(AdminSession(token_hash=_token_hash(token),
                            expires_at=utcnow() + ADMIN_SESSION_TTL))
        db.commit()
        resp.set_cookie(ADMIN_COOKIE, token, httponly=True, samesite="strict",
                        secure=os.environ.get("PHOTOBIND_SECURE_COOKIES", "0") == "1",
                        max_age=int(ADMIN_SESSION_TTL.total_seconds()), path="/")
        return {"admin": True, "expires_in_hours": ADMIN_SESSION_TTL // timedelta(hours=1)}

    @router.post("/v1/admin/logout")
    def admin_logout(request: Request, resp: Response, db=Depends(db_session)):
        token = request.cookies.get(ADMIN_COOKIE)
        if token:
            row = db.get(AdminSession, _token_hash(token))
            if row:
                row.revoked_at = utcnow()
                db.commit()
        resp.delete_cookie(ADMIN_COOKIE, path="/")
        return {"admin": False}

    @router.get("/v1/admin/session")
    def admin_session(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        return {"admin": True}

    @router.get("/v1/admin/users")
    def admin_users(request: Request, db=Depends(db_session), q: str = "",
                    page: int = 0, per_page: int = 25):
        require_admin(request, db)
        per_page = max(1, min(per_page, 100))
        query = db.query(User)
        if q:
            like = f"%{q.lower()}%"
            query = query.filter(func.lower(User.email).like(like))
        total = query.count()
        rows = (query.order_by(User.created_at.desc())
                .offset(page * per_page).limit(per_page).all())
        out = []
        for u in rows:
            creds = db.query(Credential).filter_by(user_id=u.user_id).count()
            keys = db.query(ApiKey).filter_by(user_id=u.user_id).count()
            out.append({
                "user_id": u.user_id, "email": u.email, "name": u.name,
                "created_at": u.created_at.isoformat(),
                "google_linked": bool(u.google_sub),
                "codes": creds, "api_keys": keys,
            })
        return {"users": out, "total": total, "page": page, "per_page": per_page}

    @router.get("/v1/admin/users/{user_id}")
    def admin_user(user_id: str, request: Request, db=Depends(db_session)):
        require_admin(request, db)
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "unknown user")
        creds = db.query(Credential).filter_by(user_id=user_id).all()
        codes = []
        for cred in creds:
            for s in db.query(Share).filter_by(credential_id=cred.credential_id):
                codes.append({"share_id": s.share_id,
                              "opaque_resolution_id": s.opaque_resolution_id,
                              "label": s.label, "scan_count": s.scan_count,
                              "state": "revoked" if cred.revoked_at else s.state()})
        return {"user_id": u.user_id, "email": u.email, "name": u.name,
                "created_at": u.created_at.isoformat(),
                "codes": codes,
                # Stated explicitly so nobody looks for it: what a code opens is
                # stored encrypted and the key never reaches this server.
                "payloads_readable": False,
                "keys": [{"key_id": k.key_id, "name": k.name,
                          "call_count": k.call_count,
                          "revoked": k.revoked_at is not None}
                         for k in db.query(ApiKey).filter_by(user_id=user_id)]}

    @router.patch("/v1/admin/users/{user_id}")
    def admin_update_user(user_id: str, body: AdminUpdateUser, request: Request,
                          db=Depends(db_session)):
        require_admin(request, db)
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "unknown user")
        # Name only. An operator changing someone's email address would take
        # over their account, so that is not offered here.
        if body.name is not None:
            u.name = body.name.strip()[:120]
        db.commit()
        return {"user_id": user_id, "name": u.name}

    @router.delete("/v1/admin/users/{user_id}")
    def admin_delete_user(user_id: str, request: Request, db=Depends(db_session),
                          confirm: str = ""):
        require_admin(request, db)
        if confirm != "DELETE":
            raise HTTPException(422, "pass ?confirm=DELETE")
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(404, "unknown user")
        deleted = delete_user_cascade(db, u)
        return {"user_id": user_id, "deleted": True, **deleted}

    @router.get("/v1/admin/developers")
    def admin_developers(request: Request, db=Depends(db_session)):
        require_admin(request, db)
        out = []
        for k in db.query(ApiKey).order_by(ApiKey.created_at.desc()).all():
            u = db.get(User, k.user_id)
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            today_row = db.query(ApiCallDay).filter_by(key_id=k.key_id,
                                                       day=today).first()
            out.append({
                "key_id": k.key_id, "name": k.name,
                "user_id": k.user_id,
                "email": u.email if u else "(deleted)",
                "scopes": k.scope_list,
                "created_at": k.created_at.isoformat(),
                "last_used_at": k.last_used_at.isoformat() if k.last_used_at else None,
                "revoked": k.revoked_at is not None,
                "calls_total": k.call_count,
                "calls_today": today_row.count if today_row else 0,
                "rejected_today": today_row.rejected if today_row else 0,
            })
        return {"developers": out, "count": len(out)}

    @router.delete("/v1/admin/keys/{key_id}")
    def admin_revoke_key(key_id: str, request: Request, db=Depends(db_session)):
        require_admin(request, db)
        k = db.get(ApiKey, key_id)
        if k is None:
            raise HTTPException(404, "unknown key")
        k.revoked_at = k.revoked_at or utcnow()
        db.commit()
        announce(k.user_id, "keys.changed", {"reason": "revoked-by-admin"})
        return {"key_id": key_id, "revoked": True}

    return router


def delete_user_cascade(db, u: User) -> dict:
    """Delete an account the same way the account owner's own deletion does.

    Children before parents, and a tombstone per share so codes already printed
    keep answering "switched off" rather than "never existed". Kept in one place
    so the operator path and the self-service path cannot drift apart.
    """
    from db import Card, PendingSignup, SessionToken
    creds = db.query(Credential).filter_by(user_id=u.user_id).all()
    cred_ids = [c.credential_id for c in creds]
    photo_ids = [c.photo_id for c in creds]
    shares = (db.query(Share).filter(Share.credential_id.in_(cred_ids)).all()
              if cred_ids else [])
    now = utcnow()
    revoked = sum(1 for s in shares if s.revoked_at is None)
    for s in shares:
        if not db.get(RevokedShare, s.opaque_resolution_id):
            db.add(RevokedShare(opaque_resolution_id=s.opaque_resolution_id,
                                revoked_at=s.revoked_at or now))
    db.flush()
    share_ids = [s.share_id for s in shares]
    if share_ids:
        (db.query(ScanEvent).filter(ScanEvent.share_id.in_(share_ids))
           .delete(synchronize_session=False))
        (db.query(Share).filter(Share.share_id.in_(share_ids))
           .delete(synchronize_session=False))
    if cred_ids:
        (db.query(Credential).filter(Credential.credential_id.in_(cred_ids))
           .delete(synchronize_session=False))
    if photo_ids:
        (db.query(Photo).filter(Photo.photo_id.in_(photo_ids))
           .delete(synchronize_session=False))
    db.query(ApiKey).filter_by(user_id=u.user_id).delete(synchronize_session=False)
    db.query(Card).filter_by(user_id=u.user_id).delete(synchronize_session=False)
    db.query(SessionToken).filter_by(user_id=u.user_id).delete(
        synchronize_session=False)
    db.query(PendingSignup).filter_by(email=u.email).delete(
        synchronize_session=False)
    db.expire_all()
    db.delete(db.get(User, u.user_id))
    db.commit()
    return {"codes_switched_off": revoked}
