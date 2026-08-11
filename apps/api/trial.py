"""Free trial: five codes, no account, nothing stored.

What a trial code is, precisely — because the difference matters and the UI says
it too: the QR carries the link you typed *directly in the picture*. There is no
server record, so anyone who scans it reads your link, and it can never be
switched off or traced. Signing up is what turns a code into something private
and revocable.

Quota is counted server-side against two keys: a salted hash of the client
address and a browser token. Both are evadable (new network, cleared cookie), so
this is friction, not a security boundary — and nothing behind it is worth
attacking, since no data is created. It exists to stop the endpoint being used
as free compute.
"""

import hashlib
import os
import secrets
import time
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile

from db import TrialUsage, utcnow

TRIAL_LIMIT = int(os.environ.get("PHOTOBIND_TRIAL_LIMIT", "5"))
TRIAL_COOKIE = "pb_trial"
MAX_UPLOAD_BYTES = 12 * 1024 * 1024      # a photo, not a payload delivery system
MAX_PAYLOAD_CHARS = 74                    # what a v8 code can carry at EC-H
# Per-address burst control, independent of the lifetime quota: encoding is CPU
# work and must not be a free denial-of-service lever.
BURST_WINDOW_S = 60
# Comfortably above the lifetime quota, so someone working quickly through their
# free codes is never rate-limited instead of being told the trial is over.
BURST_MAX = 12


def _salt() -> str:
    # Stable per deployment; without it, hashes would be guessable from an IP.
    return os.environ.get("PHOTOBIND_TRIAL_SALT", "") or os.environ.get(
        "PHOTOBIND_ADMIN_TOKEN", "photobind-trial-salt")


def _key(kind: str, value: str) -> str:
    return kind + ":" + hashlib.sha256((_salt() + value).encode()).hexdigest()[:32]


def proxy_hops() -> int:
    """How many proxies genuinely sit in front of this process.

    0 locally (nothing in front), 1 on Cloud Run, 2 with Cloudflare in front of
    it. This has to be configured, not guessed: X-Forwarded-For is a list a
    caller can prepend to freely, so only knowing how many entries were appended
    by real infrastructure tells you which entry is trustworthy.
    """
    return int(os.environ.get("PHOTOBIND_PROXY_HOPS", "0"))


def unforgeable_address(request: Request) -> str:
    """An address the caller cannot choose for itself.

    Each hop appends to X-Forwarded-For, so with N real hops the Nth entry from
    the end was written by infrastructure, not by the caller. With no proxies the
    header is meaningless and the socket address is used instead.

    uvicorn folds these headers into request.client by default, which would make
    even that value caller-controlled — hence --no-proxy-headers on the server.
    """
    hops = proxy_hops()
    peer = request.client.host if request.client else "unknown"
    if hops <= 0:
        return peer
    raw = request.headers.get("x-forwarded-for")
    if not raw:
        return peer
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    # Not enough entries to have come through the expected chain: treat the whole
    # header as untrustworthy rather than believing a short, possibly forged one.
    if len(parts) < hops:
        return peer
    return parts[-hops]


def client_address(request: Request) -> str:
    """Best-effort client identity for the quota.

    Forwarded headers are only believed when the deployment says a trusted
    proxy sets them (PHOTOBIND_TRUST_PROXY_HEADERS=1, which production does
    because Cloudflare and Cloud Run both front this service). Otherwise a
    client could simply send its own X-Forwarded-For and mint a fresh quota.

    Even when trusted, this is a friction control: a different network gives a
    different address. Anything that must actually hold is keyed on
    socket_address() instead.
    """
    if os.environ.get("PHOTOBIND_TRUST_PROXY_HEADERS", "0") == "1":
        for header in ("cf-connecting-ip", "x-forwarded-for"):
            raw = request.headers.get(header)
            if raw:
                first = raw.split(",")[0].strip()
                if first:
                    return first
    return unforgeable_address(request)


def make_router(SessionLocal, encode_trial, resolve_user) -> APIRouter:
    router = APIRouter()
    burst: dict[str, list[float]] = {}

    def counters(db, request: Request) -> list[TrialUsage]:
        keys = [("ip", _key("ip", client_address(request)))]
        token = request.cookies.get(TRIAL_COOKIE)
        if token:
            keys.append(("browser", _key("browser", token)))
        rows = []
        for kind, key in keys:
            row = db.get(TrialUsage, key)
            if row is None:
                row = TrialUsage(key=key, kind=kind, used=0)
                db.add(row)
            rows.append(row)
        return rows

    def used_count(rows) -> int:
        # The stricter of the two wins, so clearing one does not reset the quota.
        return max((r.used for r in rows), default=0)

    def check_burst(request: Request) -> None:
        # Keyed on an address the caller cannot choose: this one has to hold,
        # because it is what stops the encoder being used as free compute.
        now = time.monotonic()
        addr = unforgeable_address(request)
        window = burst.setdefault(addr, [])
        window[:] = [t for t in window if now - t < BURST_WINDOW_S]
        if len(window) >= BURST_MAX:
            raise HTTPException(429, "That's a lot of codes very quickly. "
                                     "Wait a minute and try again.")
        window.append(now)

    @router.get("/v1/trial/status")
    def trial_status(request: Request, response: Response):
        db = SessionLocal()
        try:
            # Signed-in people have no trial: they have the real thing.
            if resolve_user(request) is not None:
                return {"trial": False, "signed_in": True}
            rows = counters(db, request)
            db.commit()
            used = used_count(rows)
            if not request.cookies.get(TRIAL_COOKIE):
                response.set_cookie(TRIAL_COOKIE, secrets.token_urlsafe(16),
                                    httponly=True, samesite="lax",
                                    max_age=int(timedelta(days=30).total_seconds()),
                                    secure=os.environ.get(
                                        "PHOTOBIND_SECURE_COOKIES", "0") == "1",
                                    path="/")
            return {"trial": True, "signed_in": False, "limit": TRIAL_LIMIT,
                    "used": min(used, TRIAL_LIMIT),
                    "remaining": max(0, TRIAL_LIMIT - used)}
        finally:
            db.close()

    @router.post("/v1/trial/codes", status_code=201)
    async def trial_code(request: Request, response: Response,
                         photo: UploadFile = File(...),
                         payload: str = Form(...),
                         coverage: str = Form("full")):
        """Generates a code and returns it. Stores nothing."""
        if resolve_user(request) is not None:
            raise HTTPException(409, "You're signed in — use New code, which "
                                     "saves the code and lets you revoke it.")

        # Quota is checked before the burst limiter: someone who has finished
        # their trial must always be told that, not handed a rate-limit error at
        # the one moment the message matters.
        db = SessionLocal()
        try:
            if used_count(counters(db, request)) >= TRIAL_LIMIT:
                db.commit()
                raise HTTPException(402, f"You've used all {TRIAL_LIMIT} free "
                                         f"codes. Create an account to keep "
                                         f"going — and to be able to switch a "
                                         f"code off after sharing it.")
            db.commit()
        finally:
            db.close()

        check_burst(request)

        payload = payload.strip()
        if not payload:
            raise HTTPException(422, "Type what the code should open.")
        if len(payload) > MAX_PAYLOAD_CHARS:
            raise HTTPException(422, f"That's {len(payload)} characters. A code "
                                     f"can carry {MAX_PAYLOAD_CHARS}. Shorten it "
                                     f"or use a link.")
        if coverage not in ("full", "auto"):
            raise HTTPException(422, 'coverage must be "full" or "auto"')

        data = await photo.read()
        if not data:
            raise HTTPException(422, "Choose a photo.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "That image is larger than 12 MB. "
                                     "Use a smaller one.")

        db = SessionLocal()
        try:
            rows = counters(db, request)
            if used_count(rows) >= TRIAL_LIMIT:
                raise HTTPException(402, f"You've used all {TRIAL_LIMIT} free "
                                         f"codes. Create an account to keep "
                                         f"going — and to be able to switch a "
                                         f"code off after sharing it.")
            result = encode_trial(data, payload, coverage)

            now = utcnow()
            for row in rows:
                row.used += 1
                row.last_at = now
            db.commit()
            remaining = max(0, TRIAL_LIMIT - used_count(rows))
        finally:
            db.close()

        if not request.cookies.get(TRIAL_COOKIE):
            response.set_cookie(TRIAL_COOKIE, secrets.token_urlsafe(16),
                                httponly=True, samesite="lax",
                                max_age=int(timedelta(days=30).total_seconds()),
                                secure=os.environ.get(
                                    "PHOTOBIND_SECURE_COOKIES", "0") == "1",
                                path="/")
        return {
            **result,
            "saved": False,
            "remaining": remaining,
            "limit": TRIAL_LIMIT,
            # Said out loud, every time, so a trial is never mistaken for the
            # product's actual guarantees.
            "note": ("This trial code carries your link inside the picture, so "
                     "anyone who scans it reads it directly. Nothing was saved, "
                     "so it cannot be switched off or traced later."),
        }

    return router
