"""photobind resolution API — production-shaped development implementation.

Everything demonstrated by the additive-phase security tests still holds and
is re-tested here: ciphertext-only ingress (no plaintext payload field can
exist), fragment scrubbing, revoked=410≠404, per-client rate limiting on
/r/*, fail-closed photo verification.

Documented deviation from strict zero-knowledge (see docs/threat-model.md):
when the client asks the server to fuse the QR into the photo, the QR
content includes the key fragment, so the key transits server memory during
encoding. It is never persisted and never logged (the scrubber plus tests
cover logs); storage remains ciphertext-only. The strict fix — client-side
encoding — is future work.

DEV defaults: SQLite, file keystore, in-memory rate buckets, inline
encoding. Production requires PostgreSQL, KMS, Redis, a worker queue, and
S3 — none of which are claimed here.
"""

import base64
import hashlib
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import text

from fastapi import (Depends, FastAPI, File, Form, HTTPException, Request,
                     Response, UploadFile)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict

import auth as auth_mod
import shared
import mailer
from auth import (COOKIE_NAME, OTP_MAX_ATTEMPTS, OTP_MAX_SENDS, OTP_TTL,
                  TERMS_VERSION, GoogleVerifier, check_password, hash_otp,
                  hash_password, issue_session, new_otp, new_user_id,
                  otp_matches, resolve_session, revoke_session)
from sqlalchemy.orm import sessionmaker

from db import (Base, Card, CodeQuota, Credential, PendingSignup, Photo, Report,
                RevokedShare,
                ScanEvent, SessionToken, Share, TrialUsage, User,
                make_engine, make_session_factory, utcnow)

from binding.keys import DevKeyStore
from binding.record import SignedBinding, build_binding, sign_binding
from binding.registry import (new_credential_id, new_opaque_resolution_id,
                              new_photo_id, new_share_id)
from binding.verify import Thresholds, VerificationResult, verify_photo
from binding.registry import CredentialRegistry

access_logger = logging.getLogger("photobind.access")


def configure_logging() -> None:
    """Make our loggers actually emit under uvicorn.

    uvicorn installs its own handlers on the root and its own loggers; a
    library logger with no handler stays silent. Console-mode email in
    particular is useless if it cannot be read, so wire the photobind
    namespace to stderr explicitly and let it inherit nothing.
    """
    root = logging.getLogger("photobind")
    if root.handlers:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(levelname)s:     [%(name)s] %(message)s"))
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    root.propagate = False

RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_MAX = 30
RATE_LIMIT_GLOBAL_MAX = 600
PUBLIC_HOST = os.environ.get("PHOTOBIND_PUBLIC_HOST", "http://localhost:8000")
SECURE_COOKIES = os.environ.get("PHOTOBIND_SECURE_COOKIES", "0") == "1"
STATIC_DIR = Path(__file__).resolve().parents[1] / "web" / "static"

# Changes on every deploy (Cloud Run sets K_REVISION). Appended to asset URLs so
# a new build can never be served from a cache entry for the old one.
ASSET_VERSION = os.environ.get("K_REVISION") or str(int(Path(__file__).stat().st_mtime))


def _versioned(html: str) -> str:
    """Stamp /static/*.css and *.js references with the build version."""
    return (html.replace('.css"', f'.css?v={ASSET_VERSION}"')
                .replace('.js"', f'.js?v={ASSET_VERSION}"'))

# CLAUDE.md §8.5 — the resolution route handles the decryption key, so no
# inline script, no third-party origin, nothing embeddable.
# Applied to signed-in pages and every API response. Without no-store, the
# back button after sign-out redisplays a cached authenticated page.
NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
    "Pragma": "no-cache",
    "Expires": "0",
}

CSP_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
}


# Consent is enforced here, not in the banner. Cloudflare injects its page-view
# beacon at the edge, so our HTML never contains it and no amount of client-side
# code can reliably stop it running. A policy header can: this omits the
# measurement host, which makes the browser refuse the script outright. Our own
# inline scripts and Google's sign-in are named explicitly because they must keep
# working — a consent control that breaks sign-in would just be turned off again.
CONSENT_COOKIE = "pb_consent"

NO_ANALYTICS_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://accounts.google.com "
    "https://apis.google.com; "
    # Google's sign-in button loads its own stylesheet. Leaving this off blocked
    # it and rendered the button unstyled — caught in a browser, not in review.
    "style-src 'self' 'unsafe-inline' https://accounts.google.com; "
    "img-src 'self' data: https://*.googleusercontent.com; "
    "font-src 'self'; "
    "connect-src 'self' https://accounts.google.com; "
    "frame-src https://accounts.google.com; "
    "object-src 'none'; base-uri 'none'; form-action 'self'"
)


def consent_headers(request: Request) -> dict:
    """Extra response headers implied by the visitor's consent choice."""
    if request.cookies.get(CONSENT_COOKIE) == "all":
        return {}          # measurement allowed; nothing to restrict
    return {"Content-Security-Policy": NO_ANALYTICS_CSP}


# Codes an account may create per calendar month, resetting at 00:00 IST on the
# 1st — the same clock the API budget uses, so a person with both does not have
# to hold two different month boundaries in their head.
USER_MONTHLY_CODES = 1000


def scrub_fragment(target: str) -> str:
    """Nothing after a fragment separator may be logged, ever."""
    for sep in ("#", "%23", "%2523"):
        if sep in target:
            target = target.split(sep, 1)[0] + "#[scrubbed]"
    return target


class SignUp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    email: str
    password: str
    accept_terms: bool = False


class VerifyEmail(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    code: str


class ResendCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str
    password: str


class GoogleSignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id_token: str
    accept_terms: bool = False


class UpdateCard(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = None
    headline: str | None = None
    email: str | None = None
    phone: str | None = None
    website: str | None = None


class MintShare(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str


class UpdateShare(BaseModel):
    """Editable parts of one copy. Revocation is not among them on purpose."""
    model_config = ConfigDict(extra="forbid")
    label: str | None = None
    expires_at: str | None = None      # ISO 8601, or "" to clear
    max_scans: int | None = None       # 0 clears the cap


class UpdateMe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = None
    phone: str | None = None


class ChangePassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str
    new_password: str


class DeleteMe(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: str


class SubmitReport(BaseModel):
    """Feedback, a bug report, or an automatic crash report.

    `include_diagnostics` is the sender's explicit choice. When false the
    diagnostics field is discarded server-side rather than stored and ignored,
    so declining actually means not kept.
    """
    model_config = ConfigDict(extra="forbid")
    kind: str = "feedback"                  # feedback | bug | crash
    summary: str
    detail: str = ""
    platform: str = "web"
    app_version: str = ""
    reporter_email: str | None = None
    include_diagnostics: bool = False
    diagnostics: dict | None = None


def _valid_b64(v: str, what: str) -> str:
    try:
        std = v.translate(str.maketrans("-_", "+/")) + "=" * (-len(v) % 4)
        base64.b64decode(std.encode("ascii"), validate=True)
    except Exception:
        raise HTTPException(422, f"{what} is not valid base64url")
    return v


def create_app(keys_dir: str | Path = "dev-keys", db_url: str | None = None,
               google_verifier=None, thresholds: Thresholds | None = None) -> FastAPI:
    configure_logging()
    shared.reset_local()      # fresh fallback counters per app instance
    app = FastAPI(title="photobind API (production-shaped, DEV)", docs_url=None)
    import releases  # noqa: F401 — registers AppRelease on Base before create_all
    import platformapi  # noqa: F401 — registers ApiKey/AdminSession likewise
    engine = make_engine(db_url)
    # Schema setup is best-effort at startup: if the database is unreachable
    # right now, still boot and serve /healthz so the platform has a running
    # revision and the failure is diagnosable, instead of a container that
    # cannot start at all.
    schema_state = {"ready": False, "error": None}

    def ensure_schema() -> bool:
        if schema_state["ready"]:
            return True
        try:
            from migrate import migrate as migrate_schema
            migrate_schema(engine)
            Base.metadata.create_all(engine)
            schema_state.update(ready=True, error=None)
        except Exception as e:                       # noqa: BLE001
            schema_state["error"] = f"{type(e).__name__}: {e}"
            access_logger.error("schema not ready: %s", schema_state["error"])
        return schema_state["ready"]

    ensure_schema()
    SessionLocal = sessionmaker(engine, expire_on_commit=False)
    keystore = DevKeyStore(keys_dir)
    try:
        keystore.active_key_id()
    except RuntimeError:
        keystore.generate(activate=True)
    th = thresholds or Thresholds.load()
    google = google_verifier or GoogleVerifier()
    buckets: dict[str, list[float]] = {}
    global_bucket: list[float] = []

    cors = os.environ.get("PHOTOBIND_CORS_ORIGIN", "http://localhost:3000")
    app.add_middleware(CORSMiddleware, allow_origins=[cors],
                       allow_credentials=True, allow_methods=["*"],
                       allow_headers=["*"])

    # -- infrastructure ------------------------------------------------------
    @app.middleware("http")
    async def access_log(request: Request, call_next):
        target = scrub_fragment(request.url.path
                                + (f"?{request.url.query}" if request.url.query else ""))
        try:
            response = await call_next(request)
        except Exception:
            # Full trace to the server log; the client gets nothing internal.
            access_logger.exception("EXC %s %s", request.method, target)
            return JSONResponse({"detail": "internal error"}, status_code=500)
        # Baseline security headers everywhere. The resolution route adds a
        # stricter CSP on top; these apply to the whole site.
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("Permissions-Policy",
                                    "geolocation=(), microphone=(), camera=(self)")
        if request.url.scheme == "https" or request.headers.get(
                "x-forwarded-proto") == "https":
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains")

        # Never let a browser (or a proxy) cache an authenticated surface.
        path = request.url.path
        if path.startswith(("/v1/", "/app/", "/r/")) or path == "/":
            for k, v in NO_STORE.items():
                response.headers[k] = v
        access_logger.info("%s %s %s", request.method, target, response.status_code)
        return response

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def current_user(request: Request, db) -> User:
        user = resolve_session(db, request.cookies.get(COOKIE_NAME))
        if user is None:
            raise HTTPException(401, "Sign in to continue.")
        return user

    def rate_limit(who: str) -> None:
        """Per-caller and global ceiling on the public resolution route.

        This is the enumeration guard from CLAUDE.md §8.3, so it is the one that
        most wants to be shared: counted per process, a second instance simply
        doubles what an attacker is allowed to walk.
        """
        if shared.hits_in_window(f"r:{who}", RATE_LIMIT_WINDOW_S) > RATE_LIMIT_MAX:
            raise HTTPException(429, "Too many lookups. Slow down.")
        if shared.hits_in_window("r:global", RATE_LIMIT_WINDOW_S) > RATE_LIMIT_GLOBAL_MAX:
            raise HTTPException(429, "The service is busy. Try again shortly.")


    def set_cookie(resp: Response, token: str):
        resp.set_cookie(COOKIE_NAME, token, httponly=True, samesite="lax",
                        secure=SECURE_COOKIES, max_age=14 * 86400, path="/")

    def registry_for(db, share: Share) -> CredentialRegistry:
        """Adapter: hydrate the binding verifier's registry from DB rows."""
        reg = CredentialRegistry()
        cred = db.get(Credential, share.credential_id)
        signed = SignedBinding.from_dict(cred.signed_binding)
        c = reg.register_credential(cred.photo_id, signed,
                                    cred.created_at.isoformat())
        if cred.revoked_at:
            c.revoked_at = cred.revoked_at.isoformat()
        s = reg.mint_share(cred.credential_id, share.label,
                           share.created_at.isoformat())
        s.opaque_resolution_id = share.opaque_resolution_id
        reg._by_opaque = {share.opaque_resolution_id: s.share_id}
        s.revoked_at = share.revoked_at.isoformat() if share.revoked_at else None
        s.expires_at = share.expires_at.isoformat() if share.expires_at else None
        s.max_scans, s.scan_count = share.max_scans, share.scan_count
        return reg

    # -- auth -----------------------------------------------------------------
    @app.post("/v1/auth/signup", status_code=202)
    def signup(body: SignUp, db=Depends(get_db)):
        """Starts a signup. No account exists until the emailed code is
        confirmed, so an unverified address can never sign in or hold codes."""
        email = body.email.strip().lower()
        if "@" not in email or "." not in email.split("@")[-1]:
            raise HTTPException(422, "That email address doesn't look right. "
                                     "Check it and try again.")
        if not body.accept_terms:
            raise HTTPException(422, "Accept the terms and privacy policy to "
                                     "create an account.")
        if db.query(User).filter_by(email=email).first():
            raise HTTPException(409, "That email already has an account. "
                                     "Sign in instead.")
        try:
            ph = hash_password(body.password)
        except ValueError as e:
            raise HTTPException(422, str(e))

        code = new_otp()
        pending = db.get(PendingSignup, email)
        if pending is None:
            pending = PendingSignup(email=email)
            db.add(pending)
        elif pending.sends >= OTP_MAX_SENDS:
            raise HTTPException(429, "Too many codes sent to that address. "
                                     "Wait a while, then start again.")
        else:
            pending.sends += 1
        pending.name = body.name.strip()
        pending.password_hash = ph
        pending.code_hash = hash_otp(code)
        pending.terms_version = TERMS_VERSION
        pending.attempts = 0
        pending.expires_at = utcnow() + OTP_TTL
        db.commit()

        transport = mailer.send_code(email, code,
                                     int(OTP_TTL.total_seconds() // 60))
        return {"status": "verification_sent", "email": email,
                "expires_in_minutes": int(OTP_TTL.total_seconds() // 60),
                # Says plainly when mail was only logged, so a dev run is never
                # mistaken for working email delivery.
                "delivery": transport}

    @app.post("/v1/auth/resend-code")
    def resend_code(body: ResendCode, db=Depends(get_db)):
        email = body.email.strip().lower()
        pending = db.get(PendingSignup, email)
        if pending is None:
            raise HTTPException(404, "Start the sign-up again — that request "
                                     "expired.")
        if pending.sends >= OTP_MAX_SENDS:
            raise HTTPException(429, "Too many codes sent to that address. "
                                     "Wait a while, then start again.")
        code = new_otp()
        pending.code_hash = hash_otp(code)
        pending.sends += 1
        pending.attempts = 0
        pending.expires_at = utcnow() + OTP_TTL
        db.commit()
        transport = mailer.send_code(email, code,
                                     int(OTP_TTL.total_seconds() // 60))
        return {"status": "verification_sent", "delivery": transport}

    @app.post("/v1/auth/verify-email", status_code=201)
    def verify_email(body: VerifyEmail, resp: Response, db=Depends(get_db)):
        """Confirms the code and only then creates the account."""
        email = body.email.strip().lower()
        pending = db.get(PendingSignup, email)
        if pending is None:
            raise HTTPException(404, "Start the sign-up again — that request "
                                     "expired.")
        expires = pending.expires_at
        if expires.tzinfo is None:
            from datetime import timezone as _tz
            expires = expires.replace(tzinfo=_tz.utc)
        if utcnow() >= expires:
            db.delete(pending)
            db.commit()
            raise HTTPException(410, "That code expired. Start the sign-up "
                                     "again to get a new one.")
        if pending.attempts >= OTP_MAX_ATTEMPTS:
            db.delete(pending)
            db.commit()
            raise HTTPException(429, "Too many wrong codes. Start the sign-up "
                                     "again.")
        if not otp_matches(pending.code_hash, body.code):
            pending.attempts += 1
            db.commit()
            left = OTP_MAX_ATTEMPTS - pending.attempts
            raise HTTPException(401, f"That code doesn't match. "
                                     f"{left} attempt(s) left.")

        now = utcnow()
        user = User(user_id=new_user_id(), email=email, name=pending.name,
                    password_hash=pending.password_hash,
                    email_verified_at=now, terms_accepted_at=now,
                    terms_version=pending.terms_version or TERMS_VERSION)
        db.add(user)
        db.delete(pending)
        db.commit()
        mailer.send_welcome(email, user.name)
        set_cookie(resp, issue_session(db, user.user_id))
        return {"user_id": user.user_id, "name": user.name, "email": user.email,
                "email_verified": True}

    @app.post("/v1/auth/signin")
    def signin(body: SignIn, resp: Response, db=Depends(get_db)):
        email = body.email.strip().lower()
        user = db.query(User).filter_by(email=email).first()
        if user is None:
            # No account: say so plainly instead of a generic failure. There
            # is no account to enumerate here that signup does not already
            # reveal via its own 409.
            pending = db.get(PendingSignup, email)
            if pending is not None:
                raise HTTPException(409, "That sign-up isn't finished. Enter "
                                         "the code we emailed you.")
            raise HTTPException(404, "No account for that email yet. "
                                     "Create one first.")
        if user.password_hash is None:
            raise HTTPException(409, "That account was created with Google. "
                                     "Continue with Google instead.")
        if not check_password(user.password_hash, body.password):
            raise HTTPException(401, "Email and password don't match. "
                                     "Check both and try again.")
        set_cookie(resp, issue_session(db, user.user_id))
        return {"user_id": user.user_id, "name": user.name, "email": user.email}

    @app.post("/v1/auth/google")
    def google_signin(body: GoogleSignIn, resp: Response, db=Depends(get_db)):
        try:
            info = google.verify(body.id_token)
        except Exception:
            raise HTTPException(401, "Google sign-in failed. Try again.")
        user = db.query(User).filter_by(google_sub=info["sub"]).first()
        created = False
        if user is None:
            user = db.query(User).filter_by(email=info["email"].lower()).first()
            if user:
                user.google_sub = info["sub"]
            else:
                # First time through Google is a sign-up, so it needs the same
                # recorded consent as the email path.
                if not body.accept_terms:
                    raise HTTPException(422, "Accept the terms and privacy "
                                             "policy to create an account.")
                now = utcnow()
                user = User(user_id=new_user_id(), email=info["email"].lower(),
                            name=info.get("name", ""), google_sub=info["sub"],
                            email_verified_at=now, terms_accepted_at=now,
                            terms_version=TERMS_VERSION)
                db.add(user)
                created = True
            db.commit()
        if created:
            mailer.send_welcome(user.email, user.name)
        set_cookie(resp, issue_session(db, user.user_id))
        return {"user_id": user.user_id, "name": user.name, "email": user.email}

    def card_for(db, user: User) -> Card:
        """The signed-in person's card, created on first use with their name
        already filled in and nothing else."""
        card = db.query(Card).filter_by(user_id=user.user_id).first()
        if card is None:
            card = Card(card_id=secrets.token_urlsafe(6), user_id=user.user_id,
                        display_name=user.name or "")
            db.add(card)
            db.commit()
        return card

    def card_dict(card: Card) -> dict:
        return {"card_id": card.card_id,
                "url": f"{PUBLIC_HOST}/c/{card.card_id}",
                "display_name": card.display_name, "headline": card.headline,
                "email": card.email, "phone": card.phone,
                "website": card.website}

    @app.get("/v1/me/card")
    def get_card(request: Request, db=Depends(get_db)):
        return card_dict(card_for(db, current_user(request, db)))

    @app.put("/v1/me/card")
    def put_card(body: UpdateCard, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        card = card_for(db, u)
        for field in ("display_name", "headline", "email", "phone", "website"):
            value = getattr(body, field)
            if value is not None:
                setattr(card, field, value.strip()[:200])
        card.updated_at = utcnow()
        db.commit()
        notify(u.user_id, "card.changed", {"card_id": card.card_id})
        return card_dict(card)

    @app.get("/c/{card_id}")
    def public_card(card_id: str, request: Request, db=Depends(get_db)):
        """The page a scan lands on.

        Rendered server-side with every value escaped and no script of any kind,
        under the same strict policy as the resolution page: this URL is handed to
        strangers, and the less it can execute the less it can leak.
        """
        from trial import unforgeable_address
        rate_limit(unforgeable_address(request))
        card = db.get(Card, card_id)
        if card is None:
            raise HTTPException(404, "unknown card")
        if "application/json" in request.headers.get("accept", ""):
            return JSONResponse({k: v for k, v in card_dict(card).items()
                                 if k != "card_id"})

        import html as _html
        e = _html.escape
        rows = []
        if card.headline:
            rows.append(f'<p class="head">{e(card.headline)}</p>')
        if card.email:
            rows.append(f'<p><a href="mailto:{e(card.email)}">{e(card.email)}</a></p>')
        if card.phone:
            rows.append(f'<p><a href="tel:{e(card.phone)}">{e(card.phone)}</a></p>')
        if card.website:
            site = card.website
            if not site.startswith(("http://", "https://")):
                site = "https://" + site
            rows.append(f'<p><a href="{e(site)}" rel="noopener nofollow">'
                        f'{e(card.website)}</a></p>')
        name = e(card.display_name) or "Identity"
        body = "\n".join(rows) or '<p class="muted">No details added yet.</p>'
        page = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name}</title>
<meta name="robots" content="noindex, nofollow, noarchive">
<link rel="icon" href="/static/favicon.svg" type="image/svg+xml">
<link rel="stylesheet" href="/static/styles.css">
</head><body>
<main style="max-width:34rem;margin:0 auto;padding:84px 24px">
  <h1 style="font-size:clamp(30px,6vw,46px);letter-spacing:-.04em;margin:0 0 12px">{name}</h1>
  {body}
  <p class="mono muted" style="font-size:11px;margin-top:42px">
    reached by scanning a photo-bound code
  </p>
</main></body></html>"""
        return Response(page, media_type="text/html",
                        headers={**CSP_HEADERS, **NO_STORE,
                                 "X-Robots-Tag": "noindex, nofollow, noarchive"})

    @app.post("/v1/auth/google/redirect")
    async def google_redirect(request: Request, db=Depends(get_db)):
        """Google's redirect mode: the browser posts the ID token here as a form.

        This exists because the pop-up flow can fail with nothing to show for it
        — a blocked pop-up, or a browser that won't hand the account over, looks
        exactly like a button that does nothing. A full navigation to Google and
        back has none of those failure modes.

        Google protects this form post with a double-submit token: the same value
        arrives in a cookie and in the body, and a forged cross-site post can set
        neither. No client secret is involved, and the ID token is verified the
        same way as every other path.
        """
        form = await request.form()
        body_csrf = str(form.get("g_csrf_token", ""))
        cookie_csrf = request.cookies.get("g_csrf_token", "")
        if not body_csrf or not secrets.compare_digest(body_csrf, cookie_csrf):
            return RedirectResponse("/app/auth.html?google=csrf", status_code=303)
        try:
            info = google.verify(str(form.get("credential", "")))
        except Exception:
            return RedirectResponse("/app/auth.html?google=failed", status_code=303)

        # Consent rides along in the state Google echoes back, so creating an
        # account through this path needs the same tick as every other.
        accepted = str(form.get("state", "")) == "accept-terms"
        user = db.query(User).filter_by(google_sub=info["sub"]).first()
        created = False
        if user is None:
            user = db.query(User).filter_by(email=info["email"].lower()).first()
            if user:
                user.google_sub = info["sub"]
            else:
                if not accepted:
                    return RedirectResponse("/app/auth.html?google=terms",
                                            status_code=303)
                now = utcnow()
                user = User(user_id=new_user_id(), email=info["email"].lower(),
                            name=info.get("name", ""), google_sub=info["sub"],
                            email_verified_at=now, terms_accepted_at=now,
                            terms_version=TERMS_VERSION)
                db.add(user)
                created = True
            db.commit()
        if created:
            mailer.send_welcome(user.email, user.name)
        resp = RedirectResponse("/app/new.html", status_code=303)
        set_cookie(resp, issue_session(db, user.user_id))
        return resp

    @app.post("/v1/auth/signout")
    def signout(request: Request, resp: Response, db=Depends(get_db)):
        # Revokes the stored session row, so a replayed cookie is dead even if
        # the browser kept it.
        user = resolve_session(db, request.cookies.get(COOKIE_NAME))
        revoke_session(db, request.cookies.get(COOKIE_NAME))
        resp.delete_cookie(COOKIE_NAME, path="/")
        for k, v in NO_STORE.items():
            resp.headers[k] = v
        if user:
            notify(user.user_id, "session.ended", {})
        return {"status": "signed_out"}

    @app.get("/v1/me")
    def me(request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        return {"user_id": u.user_id, "name": u.name, "email": u.email,
                "email_verified": u.email_verified_at is not None,
                "auth": "google" if u.google_sub else "password",
                "terms_version": u.terms_version,
                "member_since": u.created_at.isoformat()}

    @app.patch("/v1/me")
    def update_me(body: UpdateMe, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        if body.name is not None:
            u.name = body.name.strip()
        db.commit()
        notify(u.user_id, "profile.changed", {"name": u.name})
        return {"user_id": u.user_id, "name": u.name, "email": u.email}

    @app.post("/v1/auth/password")
    def change_password(body: ChangePassword, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        if not check_password(u.password_hash, body.current_password):
            raise HTTPException(401, "Current password doesn't match.")
        try:
            u.password_hash = hash_password(body.new_password)
        except ValueError as e:
            raise HTTPException(422, str(e))
        db.commit()
        return {"status": "changed"}

    @app.delete("/v1/me")
    def delete_me(body: DeleteMe, request: Request, resp: Response, db=Depends(get_db)):
        u = current_user(request, db)
        if body.confirm != "DELETE":
            raise HTTPException(422, 'Type DELETE to confirm.')
        now = utcnow()
        email, name = u.email, u.name

        creds = db.query(Credential).filter_by(user_id=u.user_id).all()
        cred_ids = [c.credential_id for c in creds]
        photo_ids = [c.photo_id for c in creds]
        shares = (db.query(Share).filter(Share.credential_id.in_(cred_ids)).all()
                  if cred_ids else [])
        revoked = sum(1 for s in shares if s.revoked_at is None)

        # Every code this account issued keeps answering REVOKED, because copies
        # of it are already in the world. The tombstone carries the opaque id
        # alone; everything that could identify anyone goes below.
        for s in shares:
            if not db.get(RevokedShare, s.opaque_resolution_id):
                db.add(RevokedShare(opaque_resolution_id=s.opaque_resolution_id,
                                    revoked_at=s.revoked_at or now))
        db.flush()

        # Children before parents. Deleting photos first — which is what this
        # used to do — leaves credentials pointing at rows that no longer exist,
        # and Postgres rejects the whole transaction with a foreign-key
        # violation, so account deletion failed outright.
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
        (db.query(Card).filter_by(user_id=u.user_id)
           .delete(synchronize_session=False))
        (db.query(SessionToken).filter_by(user_id=u.user_id)
           .delete(synchronize_session=False))
        (db.query(PendingSignup).filter_by(email=u.email)
           .delete(synchronize_session=False))
        db.expire_all()
        db.delete(db.get(User, u.user_id))
        db.commit()
        resp.delete_cookie(COOKIE_NAME, path="/")
        # Confirm in writing what was destroyed — deletion is irreversible and
        # the person should have a record of it.
        delivery = mailer.send_account_deleted(email, name, revoked)
        return {"status": "deleted", "codes_revoked": revoked,
                "confirmation_email": delivery}

    # -- codes ---------------------------------------------------------------
    @app.post("/v1/codes", status_code=201)
    async def create_code(request: Request,
                          photo: UploadFile = File(...),
                          ciphertext_b64: str = Form(...),
                          nonce_b64: str = Form(...),
                          label: str = Form("Share 1"),
                          encode_qr: str = Form("1"),
                          fragment_key: str = Form(""),
                          coverage: str = Form("full"),
                          db=Depends(get_db)):
        u = current_user(request, db)
        form = await request.form()
        unknown = set(form.keys()) - {"photo", "ciphertext_b64", "nonce_b64",
                                      "label", "encode_qr", "fragment_key",
                                      "coverage"}
        if unknown:
            raise HTTPException(422, f"unexpected fields {sorted(unknown)}: this "
                                     f"endpoint accepts ciphertext only, never "
                                     f"plaintext payloads")
        _valid_b64(ciphertext_b64, "ciphertext_b64")
        _valid_b64(nonce_b64, "nonce_b64")

        # Checked before the photo is read and long before it is encoded: fusion
        # is the expensive part, and someone over their limit should be told so
        # immediately rather than after a wait.
        from platformapi import billing_month, next_reset_iso
        month = billing_month()
        quota = (db.query(CodeQuota).filter_by(user_id=u.user_id, month=month)
                 .first())
        if quota is None:
            quota = CodeQuota(user_id=u.user_id, month=month, count=0)
            db.add(quota)
            db.flush()
        if quota.count >= USER_MONTHLY_CODES:
            raise HTTPException(429, f"You've made {USER_MONTHLY_CODES} codes "
                                     f"this month, which is the limit. It resets "
                                     f"on the 1st. Your existing codes keep "
                                     f"working.",
                                headers={"X-Quota-Resets": next_reset_iso()})

        data = await photo.read()

        photo_id = new_photo_id()
        credential_id = new_credential_id()
        opaque = new_opaque_resolution_id()
        decode_rate = 0
        confidence = None
        has_face = None
        image_ssim = None

        if encode_qr == "1":
            from encoder import EncodeError, EncodeOptions, encode_photo
            # QR content: resolution URL (+ key fragment if the client chose
            # server-side encoding). fragment_key is used HERE ONLY — never
            # stored, never logged (see module docstring).
            qr_payload = f"{PUBLIC_HOST.split('://', 1)[-1]}/r/{opaque[:11]}"
            opaque = opaque[:11]  # v7 capacity: 64-bit opaque id (validated C)
            if fragment_key:
                qr_payload += f"#{fragment_key}"
            try:
                if coverage not in ("full", "auto"):
                    raise HTTPException(422, 'coverage must be "full" or "auto"')
                enc = encode_photo(data, qr_payload,
                                   EncodeOptions(coverage=coverage))
            except EncodeError as e:
                raise HTTPException(422, f"Encoding failed: {e}")
            data = enc.image_png
            decode_rate = round(enc.decode_rate * 100)
            confidence = enc.decode_confidence
            has_face = enc.has_face
            image_ssim = enc.image_ssim
        else:
            import cv2
            import numpy as np
            if cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR) is None:
                raise HTTPException(422, "photo is not a decodable image")

        rec = build_binding(data, photo_id=photo_id, credential_id=credential_id,
                            signing_key_id=keystore.active_key_id(),
                            created_at=utcnow().isoformat())
        signed = sign_binding(rec, keystore)
        db.add(Photo(photo_id=photo_id, user_id=u.user_id, image_png=data))
        # Insert the photo before the credential that points at it. Nothing in
        # the ORM establishes that order: Credential.photo_id is a plain foreign
        # key with no relationship() behind it, so the unit of work sorted the
        # tables alphabetically and sent "credentials" first, which Postgres
        # rejects with a foreign-key violation.
        db.flush()
        db.add(Credential(credential_id=credential_id, user_id=u.user_id,
                          photo_id=photo_id, ciphertext_b64=ciphertext_b64,
                          nonce_b64=nonce_b64, signed_binding=signed.to_dict(),
                          decode_rate=decode_rate))
        db.add(Share(share_id=new_share_id(), credential_id=credential_id,
                     opaque_resolution_id=opaque, label=label))
        quota.count += 1
        db.commit()
        notify(u.user_id, "codes.changed", {"reason": "created",
                                            "credential_id": credential_id})
        return {"photo_id": photo_id, "credential_id": credential_id,
                "opaque_resolution_id": opaque,
                "resolution_url": f"{PUBLIC_HOST}/r/{opaque}",
                "decode_rate": decode_rate, "decode_confidence": confidence,
                "has_face": has_face, "image_ssim": image_ssim,
                "image_png_b64": base64.b64encode(data).decode()}

    @app.get("/v1/me/quota")
    def my_quota(request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        from platformapi import billing_month, next_reset_iso
        month = billing_month()
        row = (db.query(CodeQuota).filter_by(user_id=u.user_id, month=month)
               .first())
        used = row.count if row else 0
        return {"month": month, "limit": USER_MONTHLY_CODES, "used": used,
                "remaining": max(0, USER_MONTHLY_CODES - used),
                "resets_at": next_reset_iso()}

    @app.get("/v1/me/usage")
    def my_usage(request: Request, days: int = 30, db=Depends(get_db)):
        """This account's own activity.

        Deliberately separate from the developer console's usage: that one is
        about API keys and belongs to a different account type. Someone making
        codes for themselves wants to know how many they have, how many are still
        live, and where they are being scanned.
        """
        u = current_user(request, db)
        from platformapi import billing_month, next_reset_iso
        month = billing_month()
        row = (db.query(CodeQuota).filter_by(user_id=u.user_id, month=month)
               .first())
        used = row.count if row else 0

        creds = db.query(Credential).filter_by(user_id=u.user_id).all()
        cred_ids = [c.credential_id for c in creds]
        shares = (db.query(Share).filter(Share.credential_id.in_(cred_ids)).all()
                  if cred_ids else [])
        revoked_creds = {c.credential_id for c in creds if c.revoked_at}

        live = off = 0
        per_code = []
        for sh in shares:
            state = "revoked" if sh.credential_id in revoked_creds else sh.state()
            live += state == "active"
            off += state != "active"
            per_code.append({"opaque_resolution_id": sh.opaque_resolution_id,
                             "label": sh.label, "state": state,
                             "scans": sh.scan_count,
                             "created_at": sh.created_at.isoformat()})
        per_code.sort(key=lambda x: x["scans"], reverse=True)

        # Scans by day, from the events themselves rather than the running total,
        # so the chart shows when they happened and not just how many there were.
        share_ids = [sh.share_id for sh in shares]
        by_day, by_hour = {}, {}
        # Bounded by time and by row count. Reading every scan event an account has
        # ever had is fine at ten codes and falls over at ten thousand — and the
        # charts only ever draw a window anyway.
        days = max(1, min(days, 365))
        since = utcnow() - timedelta(days=days)
        if share_ids:
            for ev in (db.query(ScanEvent)
                       .filter(ScanEvent.share_id.in_(share_ids),
                               ScanEvent.ts >= since)
                       .order_by(ScanEvent.ts.desc()).limit(20000).all()):
                d = ev.ts.strftime("%Y-%m-%d")
                by_day[d] = by_day.get(d, 0) + 1
                by_hour[ev.ts.strftime("%Y-%m-%dT%H")] = \
                    by_hour.get(ev.ts.strftime("%Y-%m-%dT%H"), 0) + 1

        # Codes made by day, so the two series can be read against each other.
        made_by_day = {}
        for sh in shares:
            d = sh.created_at.strftime("%Y-%m-%d")
            made_by_day[d] = made_by_day.get(d, 0) + 1

        peak_hour, peak_count = "", 0
        for h, c in by_hour.items():
            if c > peak_count:
                peak_hour, peak_count = h, c

        return {
            "month": month,
            "codes_limit": USER_MONTHLY_CODES,
            "codes_used_this_month": used,
            "codes_remaining_this_month": max(0, USER_MONTHLY_CODES - used),
            "resets_at": next_reset_iso(),
            "codes_total": len(shares),
            "codes_live": live,
            "codes_off": off,
            "scans_total": sum(sh.scan_count for sh in shares),
            "peak_hour": peak_hour,
            "peak_hour_scans": peak_count,
            "scans_by_day": [{"day": d, "count": c} for d, c in sorted(by_day.items())],
            "scans_by_hour": [{"hour": h, "count": c} for h, c in sorted(by_hour.items())],
            "codes_by_day": [{"day": d, "count": c} for d, c in sorted(made_by_day.items())],
            # Enough for the page to paginate without a second round trip.
            "top_codes": per_code[:200],
        }

    @app.get("/v1/codes")
    def list_codes(request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        out = []
        for cred in (db.query(Credential).filter_by(user_id=u.user_id)
                     .order_by(Credential.created_at.desc()).all()):
            for share in cred.shares:
                scans = (db.query(ScanEvent).filter_by(share_id=share.share_id)
                         .order_by(ScanEvent.ts.desc()).limit(5).all())
                out.append({
                    "credential_id": cred.credential_id,
                    "share_id": share.share_id,
                    "opaque_resolution_id": share.opaque_resolution_id,
                    "label": share.label,
                    "state": "revoked" if cred.revoked_at else share.state(),
                    "created_at": share.created_at.isoformat(),
                    "scan_count": share.scan_count,
                    "decode_rate": cred.decode_rate,
                    "photo_id": cred.photo_id,
                    "log": [f"{s.ts.strftime('%Y-%m-%d %H:%M')} scan · {s.country}"
                            for s in scans],
                })
        return {"codes": out, "count": len(out)}

    @app.post("/v1/codes/{credential_id}/shares", status_code=201)
    def mint_share(credential_id: str, body: MintShare, request: Request,
                   db=Depends(get_db)):
        u = current_user(request, db)
        cred = db.get(Credential, credential_id)
        if cred is None or cred.user_id != u.user_id:
            raise HTTPException(404, "unknown credential")
        share = Share(share_id=new_share_id(), credential_id=credential_id,
                      opaque_resolution_id=new_opaque_resolution_id(),
                      label=body.label)
        db.add(share)
        db.commit()
        notify(u.user_id, "codes.changed", {"reason": "share_minted",
                                            "share_id": share.share_id})
        return {"share_id": share.share_id,
                "opaque_resolution_id": share.opaque_resolution_id,
                "label": share.label}

    @app.delete("/v1/shares/{share_id}")
    def revoke_share(share_id: str, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        share = db.get(Share, share_id)
        if share is None or db.get(Credential, share.credential_id).user_id != u.user_id:
            raise HTTPException(404, "unknown share")
        share.revoked_at = share.revoked_at or utcnow()
        db.commit()
        notify(u.user_id, "codes.changed", {"reason": "revoked",
                                            "share_id": share_id})
        return {"share_id": share_id, "state": "revoked"}

    @app.patch("/v1/shares/{share_id}")
    def update_share(share_id: str, body: UpdateShare, request: Request,
                     db=Depends(get_db)):
        """Change a copy's label, expiry, or scan cap.

        Deliberately cannot un-revoke: "off means off" is the promise the product
        is built on, and a switch that can be flipped back is a different promise.
        """
        u = current_user(request, db)
        share = db.get(Share, share_id)
        if share is None or db.get(Credential, share.credential_id).user_id != u.user_id:
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
                    raise HTTPException(422, "expires_at must be an ISO 8601 "
                                             "timestamp, or empty to clear it")
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                share.expires_at = when
        if body.max_scans is not None:
            if body.max_scans < 0:
                raise HTTPException(422, "max_scans cannot be negative")
            share.max_scans = body.max_scans or None
        db.commit()
        notify(u.user_id, "codes.changed", {"reason": "updated",
                                            "share_id": share_id})
        return {"share_id": share_id, "label": share.label,
                "state": "revoked" if db.get(Credential, share.credential_id).revoked_at
                         else share.state(),
                "expires_at": share.expires_at.isoformat() if share.expires_at else None,
                "max_scans": share.max_scans}

    @app.delete("/v1/codes/{credential_id}")
    def delete_code(credential_id: str, request: Request, db=Depends(get_db)):
        """Delete a code and its picture outright.

        Every copy of it keeps answering REVOKED afterwards, through the same
        tombstone account deletion uses: copies of a code are already out in the
        world, and a scanner meeting one deserves "switched off" rather than
        "never existed". Children go before parents — the ordering Postgres
        enforces.
        """
        u = current_user(request, db)
        cred = db.get(Credential, credential_id)
        if cred is None or cred.user_id != u.user_id:
            raise HTTPException(404, "unknown code")

        shares = db.query(Share).filter_by(credential_id=credential_id).all()
        now = utcnow()
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
        photo_id = cred.photo_id
        db.query(Credential).filter_by(credential_id=credential_id).delete(
            synchronize_session=False)
        db.query(Photo).filter_by(photo_id=photo_id).delete(
            synchronize_session=False)
        db.expire_all()
        db.commit()
        notify(u.user_id, "codes.changed", {"reason": "deleted",
                                            "credential_id": credential_id})
        return {"credential_id": credential_id, "deleted": True,
                "copies_switched_off": len(shares)}

    @app.get("/v1/shares/{share_id}/scans")
    def scan_log(share_id: str, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        share = db.get(Share, share_id)
        if share is None or db.get(Credential, share.credential_id).user_id != u.user_id:
            raise HTTPException(404, "unknown share")
        scans = (db.query(ScanEvent).filter_by(share_id=share_id)
                 .order_by(ScanEvent.ts.desc()).all())
        return {"share_id": share_id, "scan_count": share.scan_count,
                "scans": [{"ts": s.ts.isoformat(), "country": s.country}
                          for s in scans]}

    @app.get("/v1/photos/{photo_id}.png")
    def photo_png(photo_id: str, request: Request, db=Depends(get_db)):
        u = current_user(request, db)
        p = db.get(Photo, photo_id)
        if p is None or p.user_id != u.user_id:
            raise HTTPException(404, "unknown photo")
        return Response(p.image_png, media_type="image/png")

    # -- feedback, bug reports, crash reports ---------------------------------
    @app.post("/v1/reports", status_code=201)
    def submit_report(body: SubmitReport, request: Request, db=Depends(get_db)):
        """Open to signed-out users too: someone who cannot sign in is exactly
        the person who most needs to report it."""
        kind = body.kind if body.kind in ("feedback", "bug", "crash") else "feedback"
        summary = body.summary.strip()
        if not summary:
            raise HTTPException(422, "Say briefly what happened so we can act on it.")
        if len(summary) > 300:
            summary = summary[:300]

        user = resolve_session(db, request.cookies.get(COOKIE_NAME))
        # Declining diagnostics means they are not stored at all.
        diagnostics = body.diagnostics if body.include_diagnostics else None
        if diagnostics is not None:
            # Never accept anything that could carry a decryption key.
            diagnostics = {k: v for k, v in diagnostics.items()
                           if "key" not in k.lower() and "fragment" not in k.lower()}
            diagnostics["server_seen_at"] = utcnow().isoformat()

        report = Report(
            report_id="rep_" + secrets.token_urlsafe(9),
            kind=kind, summary=summary, detail=(body.detail or "")[:8000],
            platform=body.platform[:32], app_version=body.app_version[:32],
            user_id=user.user_id if user else None,
            reporter_email=(body.reporter_email or (user.email if user else None)),
            diagnostics=diagnostics)
        db.add(report)
        db.commit()

        delivery = mailer.send_report_to_admin(
            kind, summary, report.detail, report.reporter_email, diagnostics)
        report.delivered = delivery
        db.commit()
        return {"report_id": report.report_id, "kind": kind,
                "diagnostics_included": diagnostics is not None,
                "delivery": delivery}

    @app.get("/v1/admin/reports")
    def list_reports(request: Request, limit: int = 50, db=Depends(get_db)):
        admin_token = os.environ.get("PHOTOBIND_ADMIN_TOKEN", "")
        if not admin_token or request.headers.get("x-admin-token") != admin_token:
            raise HTTPException(401, "bad admin token")
        rows = (db.query(Report).order_by(Report.created_at.desc())
                .limit(min(limit, 200)).all())
        return {"count": len(rows), "reports": [
            {"report_id": r.report_id, "kind": r.kind, "summary": r.summary,
             "platform": r.platform, "app_version": r.app_version,
             "reporter": r.reporter_email, "delivered": r.delivered,
             "has_diagnostics": r.diagnostics is not None,
             "created_at": r.created_at.isoformat()} for r in rows]}

    # -- public resolution + verification -------------------------------------
    @app.get("/r/{opaque_resolution_id}")
    def resolve(opaque_resolution_id: str, request: Request, db=Depends(get_db)):
        # A browser navigation gets the static resolution page, which then
        # fetches this same URL as JSON and decrypts client-side. The key
        # fragment is never sent by the browser in either request.
        if "text/html" in request.headers.get("accept", ""):
            html = _versioned((STATIC_DIR / "r.html").read_text())
            # Per-share links must never be indexed or cached: the fragment
            # key makes every one of them a secret.
            headers = {**CSP_HEADERS, "X-Robots-Tag": "noindex, nofollow, noarchive",
                       "Cache-Control": "no-store"}
            return Response(html, media_type="text/html", headers=headers)

        from trial import unforgeable_address
        rate_limit(unforgeable_address(request))
        share = (db.query(Share)
                 .filter_by(opaque_resolution_id=opaque_resolution_id).first())
        if share is None:
            # The owning account was deleted. The code still exists on whatever
            # it was printed on, so say revoked rather than pretending the id
            # was never real.
            if db.get(RevokedShare, opaque_resolution_id):
                return JSONResponse({"status": "REVOKED"}, status_code=410)
            raise HTTPException(404, "unknown id")
        cred = db.get(Credential, share.credential_id)
        state = "revoked" if cred.revoked_at else share.state()
        if state != "active":
            return JSONResponse({"status": state.upper()}, status_code=410)
        share.scan_count += 1
        ua = request.headers.get("user-agent", "")
        db.add(ScanEvent(share_id=share.share_id,
                         ua_hash=hashlib.sha256(ua.encode()).hexdigest()[:16]))
        db.commit()
        notify(cred.user_id, "scan.recorded",
               {"share_id": share.share_id, "scan_count": share.scan_count})
        return {"status": "ACTIVE", "ciphertext": cred.ciphertext_b64,
                "nonce": cred.nonce_b64}

    @app.post("/v1/verify-photo")
    async def verify(opaque_resolution_id: str = Form(...),
                     photo: UploadFile = File(...), db=Depends(get_db)):
        data = await photo.read()
        share = (db.query(Share)
                 .filter_by(opaque_resolution_id=opaque_resolution_id).first())
        if share is None:
            return VerificationResult(status="INSUFFICIENT_EVIDENCE",
                                      credential="unknown",
                                      photo_binding="unverified",
                                      reason="opaque resolution id does not resolve"
                                      ).to_dict()
        reg = registry_for(db, share)
        return verify_photo(opaque_resolution_id, data, reg, keystore, th).to_dict()

    # -- free trial (registered after the session resolver below) -------------
    def encode_trial(image_bytes: bytes, payload: str, coverage: str) -> dict:
        """Encodes and returns; writes nothing anywhere.

        The QR carries the payload directly rather than a resolution id, because
        there is no record to resolve — that is what makes it a trial.
        """
        from encoder import EncodeError, EncodeOptions, encode_photo
        try:
            enc = encode_photo(image_bytes, payload,
                               EncodeOptions(coverage=coverage))
        except EncodeError as e:
            raise HTTPException(422, f"Couldn't make a code from that: {e}")
        return {
            "decode_rate": round(enc.decode_rate * 100),
            "decode_confidence": enc.decode_confidence,
            "has_face": enc.has_face,
            "image_ssim": enc.image_ssim,
            "image_png_b64": base64.b64encode(enc.image_png).decode(),
        }

    # -- instant updates -------------------------------------------------------
    from events import BROADCAST, bus, make_router as make_events_router

    def _resolve_for_events(request: Request):
        db = SessionLocal()
        try:
            return resolve_session(db, request.cookies.get(COOKIE_NAME))
        finally:
            db.close()

    app.include_router(make_events_router(_resolve_for_events))

    from trial import make_router as make_trial_router
    app.include_router(make_trial_router(SessionLocal, encode_trial,
                                         _resolve_for_events))

    def notify(user_id: str | None, event: str, data: dict | None = None) -> None:
        """Push a change to every open tab and phone for this account. Events
        only say what changed; clients refetch, so a missed event can never
        produce a wrong write."""
        bus.publish(user_id or BROADCAST, event, data)

    # -- app releases / OTA ----------------------------------------------------
    from releases import Storage as ReleaseStorage
    from releases import make_router as make_release_router

    def store_kind() -> str:
        return ReleaseStorage().kind

    app.include_router(make_release_router(SessionLocal, PUBLIC_HOST, notify))

    # -- developer API, admin panel -------------------------------------------
    import platformapi
    from platformapi import make_router as make_platform_router

    class SecretBox:
        """AES-256-GCM for API key secrets.

        The key comes from PHOTOBIND_API_SIGNING_KEY. In development, when that
        is unset, it is generated once into the keystore directory so restarts
        keep working — a generated-and-forgotten key would silently invalidate
        every existing key on every boot.
        """
        def __init__(self, keys_dir: Path):
            raw = os.environ.get("PHOTOBIND_API_SIGNING_KEY", "")
            if raw:
                self.key = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            else:
                path = Path(keys_dir) / "api-signing.key"
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_bytes(base64.urlsafe_b64encode(secrets.token_bytes(32)))
                    path.chmod(0o600)
                self.key = base64.urlsafe_b64decode(path.read_bytes())
            if len(self.key) != 32:
                raise RuntimeError("PHOTOBIND_API_SIGNING_KEY must be 32 bytes, "
                                   "base64url encoded")

        def encrypt(self, plaintext: str) -> str:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            nonce = secrets.token_bytes(12)
            ct = AESGCM(self.key).encrypt(nonce, plaintext.encode(), None)
            return base64.urlsafe_b64encode(nonce + ct).decode().rstrip("=")

        def decrypt(self, stored: str) -> str:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
            blob = base64.urlsafe_b64decode(stored + "=" * (-len(stored) % 4))
            return AESGCM(self.key).decrypt(blob[:12], blob[12:], None).decode()

    def encode_and_store(db, user_id: str, photo: bytes, ciphertext_b64: str,
                         nonce_b64: str, label: str, fragment_key: str,
                         coverage: str) -> dict:
        """Create a code on a user's behalf — the API path into the same pipeline
        the apps use, so a third party cannot reach a weaker one."""
        _valid_b64(ciphertext_b64, "ciphertext_b64")
        _valid_b64(nonce_b64, "nonce_b64")
        if coverage not in ("full", "auto"):
            raise HTTPException(422, 'coverage must be "full" or "auto"')
        photo_id, credential_id = new_photo_id(), new_credential_id()
        opaque = new_opaque_resolution_id()[:11]
        from encoder import EncodeError, EncodeOptions, encode_photo
        qr_payload = f"{PUBLIC_HOST.split('://', 1)[-1]}/r/{opaque}"
        if fragment_key:
            qr_payload += f"#{fragment_key}"
        try:
            enc = encode_photo(photo, qr_payload, EncodeOptions(coverage=coverage))
        except EncodeError as e:
            raise HTTPException(422, f"Encoding failed: {e}")
        data = enc.image_png
        rec = build_binding(data, photo_id=photo_id, credential_id=credential_id,
                            signing_key_id=keystore.active_key_id(),
                            created_at=utcnow().isoformat())
        signed = sign_binding(rec, keystore)
        db.add(Photo(photo_id=photo_id, user_id=user_id, image_png=data))
        db.flush()          # photos before credentials, as the foreign key needs
        db.add(Credential(credential_id=credential_id, user_id=user_id,
                          photo_id=photo_id, ciphertext_b64=ciphertext_b64,
                          nonce_b64=nonce_b64, signed_binding=signed.to_dict(),
                          decode_rate=round(enc.decode_rate * 100)))
        db.add(Share(share_id=new_share_id(), credential_id=credential_id,
                     opaque_resolution_id=opaque, label=label))
        db.commit()
        return {"photo_id": photo_id, "credential_id": credential_id,
                "opaque_resolution_id": opaque,
                "resolution_url": f"{PUBLIC_HOST}/r/{opaque}",
                "decode_rate": round(enc.decode_rate * 100),
                "image_png_b64": base64.b64encode(data).decode()}

    app.include_router(make_platform_router(
        SessionLocal, SecretBox(keys_dir),
        lambda stored, presented: check_password(stored, presented),
        hash_password, new_user_id,
        lambda request, db: resolve_session(db, request.cookies.get(COOKIE_NAME)),
        encode_and_store, notify, mailer, PUBLIC_HOST))

    # -- static web app --------------------------------------------------------
    @app.get("/static/{path:path}")
    def static_file(path: str):
        target = (STATIC_DIR / path).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve())) or not target.is_file():
            raise HTTPException(404, "not found")
        media = {".html": "text/html", ".css": "text/css",
                 ".js": "application/javascript", ".jpg": "image/jpeg",
                 ".png": "image/png", ".svg": "image/svg+xml",
                 ".webmanifest": "application/manifest+json",
                 ".ico": "image/x-icon", ".txt": "text/plain",
                 ".xml": "application/xml"}.get(target.suffix,
                                                "application/octet-stream")
        headers = dict(CSP_HEADERS) if target.name in ("r.html", "r.js") else {}
        if target.suffix in (".css", ".js", ".html", ".webmanifest"):
            # Always revalidate: a stale stylesheet or script is a broken page.
            headers["Cache-Control"] = "no-cache, must-revalidate"
        else:
            # Images and icons are safe to cache; their content is stable and a
            # changed image ships under a new build anyway.
            headers["Cache-Control"] = "public, max-age=3600, must-revalidate"
        body = target.read_bytes()
        if target.suffix in (".css", ".js"):
            body = _versioned(body.decode()).encode()   # nested imports too
        return Response(body, media_type=media, headers=headers)

    @app.get("/")
    def index(request: Request):
        return Response(_versioned((STATIC_DIR / "index.html").read_text()),
                        media_type="text/html",
                        headers=consent_headers(request))

    # Cloud Run's frontend reserves /healthz and answers it itself, so the
    # container never sees that path. Serve the same handler somewhere the
    # platform will actually forward, and keep /healthz for other hosts.
    @app.get("/v1/config")
    def public_config():
        """Non-secret settings the browser needs. The OAuth client id is public
        by design; the client secret is never used by this flow and is not
        read anywhere in this codebase."""
        return {
            "google_client_id": os.environ.get("PHOTOBIND_GOOGLE_CLIENT_ID", ""),
            "google_enabled": bool(os.environ.get("PHOTOBIND_GOOGLE_CLIENT_ID")),
            "terms_version": TERMS_VERSION,
        }

    @app.get("/v1/health")
    @app.get("/healthz")
    def healthz():
        """Liveness plus an honest readiness report. Returns 200 even when the
        database is down so the revision stays serving and diagnosable; the
        body says exactly what is wrong."""
        db_ok, db_error = False, None
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_ok = True
        except Exception as e:                       # noqa: BLE001
            db_error = f"{type(e).__name__}: {str(e)[:200]}"
        if db_ok:
            ensure_schema()
        return {
            "service": "identity",
            "database": {
                "ok": db_ok,
                "backend": engine.url.get_backend_name(),
                "host": engine.url.host or "local-file",
                "schema_ready": schema_state["ready"],
                "error": db_error or schema_state["error"],
            },
            "mail": mailer.mode(),
            "storage": store_kind(),
        }

    # -- search-console surface ------------------------------------------------
    @app.get("/dev")
    def dev_console_page(request: Request):
        """The developer console, on its own URL and its own sign-in."""
        return Response(_versioned((STATIC_DIR / "dev.html").read_text()),
                        media_type="text/html",
                        headers={**NO_STORE, **consent_headers(request),
                                 "X-Robots-Tag": "noindex, nofollow"})

    @app.get("/admin")
    def admin_page(request: Request):
        """The operator panel. Never indexed, never cached."""
        return Response(_versioned((STATIC_DIR / "admin.html").read_text()),
                        media_type="text/html",
                        headers={**NO_STORE, **consent_headers(request),
                                 "X-Robots-Tag": "noindex, nofollow, noarchive"})

    @app.get("/robots.txt")
    def robots():
        return Response((STATIC_DIR / "robots.txt").read_text(),
                        media_type="text/plain")

    @app.get("/sitemap.xml")
    def sitemap():
        return Response((STATIC_DIR / "sitemap.xml").read_text(),
                        media_type="application/xml")

    @app.get("/favicon.svg")
    def favicon():
        return Response((STATIC_DIR / "favicon.svg").read_text(),
                        media_type="image/svg+xml")

    @app.get("/{token}.html", include_in_schema=False)
    def google_verification(token: str):
        """Google Search Console HTML-file verification. Set
        PHOTOBIND_GSC_TOKEN to the token from the googleXXXX.html filename
        Search Console gives you; any other path 404s."""
        expected = os.environ.get("PHOTOBIND_GSC_TOKEN", "")
        if expected and token == expected:
            return Response(f"google-site-verification: {token}.html",
                            media_type="text/html")
        raise HTTPException(404, "not found")

    @app.get("/app/{page}")
    def app_page(page: str, request: Request):
        target = (STATIC_DIR / page).resolve()
        if (not str(target).startswith(str(STATIC_DIR.resolve()))
                or not target.is_file() or target.suffix != ".html"):
            raise HTTPException(404, "not found")
        return Response(_versioned(target.read_text()), media_type="text/html",
                        headers={**NO_STORE, **consent_headers(request)})

    return app


app = create_app()
