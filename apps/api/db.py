"""Persistence for the resolution API.

SQLAlchemy models mirroring CLAUDE.md §4's data model plus the photo-binding
tables from the additive phase. Default engine is SQLite (dev); set
PHOTOBIND_DB_URL to a PostgreSQL URL for production — the models are plain
SQLAlchemy and carry over.

Ciphertext only, never plaintext (§8.6). No biometric columns exist (§8.1).
"""

import os
from datetime import datetime, timezone

from sqlalchemy import (JSON, DateTime, ForeignKey, Integer, LargeBinary,
                        String, create_engine, event)
from sqlalchemy.orm import (DeclarativeBase, Mapped, Session, mapped_column,
                            relationship, sessionmaker)

DB_URL = os.environ.get("PHOTOBIND_DB_URL", "sqlite:///photobind-dev.db")


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    email: Mapped[str] = mapped_column(String, unique=True, index=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    google_sub: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    # Which policy version the person accepted, and when. Consent is a fact
    # to record, not a checkbox to forget.
    terms_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True)
    terms_version: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PendingSignup(Base):
    """A signup awaiting email verification. No User row exists yet, so an
    unverified address cannot sign in, cannot hold codes, and expires on its
    own."""
    __tablename__ = "pending_signups"
    email: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    password_hash: Mapped[str] = mapped_column(String)
    code_hash: Mapped[str] = mapped_column(String)
    terms_version: Mapped[str] = mapped_column(String, default="")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    sends: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class Photo(Base):
    __tablename__ = "photos"
    photo_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    # Exactly one of these holds the image. object_key points into R2 and is
    # what production uses; image_png is the fallback for a checkout with no
    # cloud credentials, and still holds the bytes for rows written before the
    # move. Readers must check object_key first.
    image_png: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    object_key: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Credential(Base):
    __tablename__ = "credentials"
    credential_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    photo_id: Mapped[str] = mapped_column(ForeignKey("photos.photo_id"))
    ciphertext_b64: Mapped[str] = mapped_column(String)   # ciphertext ONLY
    nonce_b64: Mapped[str] = mapped_column(String)
    signed_binding: Mapped[dict] = mapped_column(JSON)    # Ed25519-signed record
    decode_rate: Mapped[int] = mapped_column(Integer)     # percent, measured
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    shares: Mapped[list["Share"]] = relationship(back_populates="credential")


class Share(Base):
    __tablename__ = "shares"
    share_id: Mapped[str] = mapped_column(String, primary_key=True)
    credential_id: Mapped[str] = mapped_column(ForeignKey("credentials.credential_id"), index=True)
    opaque_resolution_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    label: Mapped[str] = mapped_column(String, default="")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    max_scans: Mapped[int | None] = mapped_column(Integer, nullable=True)
    scan_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    credential: Mapped[Credential] = relationship(back_populates="shares")

    def state(self) -> str:
        if self.revoked_at is not None:
            return "revoked"
        if self.expires_at is not None and utcnow() >= self.expires_at.replace(tzinfo=timezone.utc):
            return "expired"
        if self.max_scans is not None and self.scan_count >= self.max_scans:
            return "scan_cap_reached"
        return "active"


class ScanEvent(Base):
    __tablename__ = "scan_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    share_id: Mapped[str] = mapped_column(ForeignKey("shares.share_id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    country: Mapped[str] = mapped_column(String, default="unknown")
    ua_hash: Mapped[str] = mapped_column(String, default="")


class Card(Base):
    """The small public page a code can point at.

    This exists so "what a scan opens" can be filled in for you. It is public by
    definition — anyone who scans the code reaches it — so it holds only what the
    owner puts here, each field is theirs to leave empty, and the page is marked
    not-indexable so a contact detail shared on a badge doesn't become a search
    result. Nothing here is required to use the product.
    """
    __tablename__ = "cards"
    card_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"),
                                         index=True, unique=True)
    display_name: Mapped[str] = mapped_column(String, default="")
    headline: Mapped[str] = mapped_column(String, default="")
    email: Mapped[str] = mapped_column(String, default="")
    phone: Mapped[str] = mapped_column(String, default="")
    website: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CodeQuota(Base):
    """Codes created per account per month.

    A counter rather than a count of rows: if the budget were derived from how
    many codes currently exist, deleting one would hand back allowance, and the
    limit would only bind people who keep their codes.
    """
    __tablename__ = "code_quotas"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(String, index=True)
    month: Mapped[str] = mapped_column(String, index=True)     # YYYY-MM, IST
    count: Mapped[int] = mapped_column(Integer, default=0)


class RevokedShare(Base):
    """A share whose account was deleted.

    Deleting an account removes the photo, the credential and the user outright,
    which leaves nothing behind to answer a scan of a code that is already
    printed on someone's badge. This table keeps the opaque id and nothing else
    — no label, no ciphertext, no owner — so the scanner is told REVOKED rather
    than "unknown id". Off means off, and it keeps meaning that after the
    account is gone.
    """
    __tablename__ = "revoked_shares"
    opaque_resolution_id: Mapped[str] = mapped_column(String, primary_key=True)
    revoked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),
                                                 default=utcnow)


class Report(Base):
    """Feedback, bug reports, and automatic crash reports.

    Kept server-side as well as emailed, so a mail outage never loses a
    report. `diagnostics` is only populated when the sender explicitly opted
    in (see the /v1/reports endpoint).
    """
    __tablename__ = "reports"
    report_id: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)        # feedback | bug | crash
    summary: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String, default="")
    platform: Mapped[str] = mapped_column(String, default="")   # web | android
    app_version: Mapped[str] = mapped_column(String, default="")
    user_id: Mapped[str | None] = mapped_column(String, nullable=True)
    reporter_email: Mapped[str | None] = mapped_column(String, nullable=True)
    diagnostics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    delivered: Mapped[str] = mapped_column(String, default="")  # mail transport
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TrialUsage(Base):
    """Free-trial quota, counted server-side.

    Keyed by a salted hash of the client address (never the raw IP) and, in a
    second row, by a browser token. Both are evadable — a new IP or a cleared
    cookie starts fresh — so this is a friction control, not an entitlement
    boundary. Nothing of value sits behind it: trial codes are not stored and
    cannot be revoked or traced.
    """
    __tablename__ = "trial_usage"
    key: Mapped[str] = mapped_column(String, primary_key=True)
    kind: Mapped[str] = mapped_column(String)            # ip | browser
    used: Mapped[int] = mapped_column(Integer, default=0)
    first_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SessionToken(Base):
    __tablename__ = "sessions"
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.user_id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


def make_engine(url: str | None = None):
    engine = create_engine(url or DB_URL,
                           connect_args={"check_same_thread": False}
                           if (url or DB_URL).startswith("sqlite") else {})
    if engine.dialect.name == "sqlite":
        # SQLite ignores foreign keys unless asked; Postgres never does. Without
        # this, dev and the test suite are more permissive than production, and
        # two ordering bugs that Postgres rejected outright — creating a code and
        # deleting an account — passed here for exactly that reason.
        @event.listens_for(engine, "connect")
        def _fk_on(dbapi_connection, _record):
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def make_session_factory(engine) -> sessionmaker[Session]:
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)
