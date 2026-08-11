"""Test helper: complete the signup + email-verification flow.

Signing up no longer creates an account on its own, so every test that needs
an authenticated client goes through verification. Kept in one place so the
flow's shape lives in exactly one file.
"""

import re

import httpx

import mailer

PASSWORD = "a-long-password-123"


class Outbox:
    """Captures outbound mail so tests can read verification codes."""

    def __init__(self):
        self.sent: list[dict] = []

    def install(self, monkeypatch):
        def fake(to, subject, text, html=None):
            self.sent.append({"to": to, "subject": subject, "text": text})
            return "smtp"
        monkeypatch.setattr(mailer, "send", fake)
        monkeypatch.setattr(mailer, "configured", lambda: True)
        return self

    def code_for(self, email: str) -> str | None:
        for m in reversed(self.sent):
            if m["to"] == email:
                found = re.search(r"code is (\d{6})", m["text"])
                if found:
                    return found.group(1)
        return None

    def welcome_for(self, email: str) -> list[dict]:
        return [m for m in self.sent
                if m["to"] == email and "Welcome" in m["subject"]]


def register(base: str, email: str, outbox: Outbox, name: str = "Test User",
             password: str = PASSWORD) -> httpx.Client:
    """Signs up, reads the emailed code, verifies, returns a signed-in client."""
    c = httpx.Client(base_url=base, timeout=60)
    r = c.post("/v1/auth/signup", json={"name": name, "email": email,
                                        "password": password,
                                        "accept_terms": True})
    assert r.status_code == 202, r.text
    code = outbox.code_for(email)
    assert code, f"no verification code captured for {email}"
    r = c.post("/v1/auth/verify-email", json={"email": email, "code": code})
    assert r.status_code == 201, r.text
    return c
