"""Outbound email: verification codes, the welcome tutorial, account
deletion confirmation, and operational reports.

Transports, in preference order:

1. BREVO HTTP API (PHOTOBIND_BREVO_API_KEY) — the right choice on serverless.
   Brevo's SMTP relay authorises by source IP, and Cloud Run egress IPs are
   dynamic, so SMTP there fails with "525 Unauthorized IP address". The HTTP
   API authenticates by key and does not care about the source address.
2. SMTP (PHOTOBIND_SMTP_*) — for hosts with a stable, allowlisted IP.
3. CONSOLE — messages are logged instead of sent.

Every send returns the transport actually used, and callers surface it, so a
run without working email is never mistaken for a working one.

    PHOTOBIND_BREVO_API_KEY
    PHOTOBIND_SMTP_HOST / _PORT / _USER / _PASSWORD
    PHOTOBIND_MAIL_FROM   ("Identity <support@patienceai.in>")
    PHOTOBIND_ADMIN_EMAIL (where reports go)
"""

import json
import logging
import os
import smtplib
import urllib.error
import urllib.request
from email.message import EmailMessage

log = logging.getLogger("photobind.mail")

APP_NAME = "Identity"
COMPANY = "Patience AI"
SITE = "https://patienceai.in"


def _cfg(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


def _brevo_key() -> str:
    return _cfg("PHOTOBIND_BREVO_API_KEY")


def _smtp_configured() -> bool:
    return bool(_cfg("PHOTOBIND_SMTP_HOST") and _cfg("PHOTOBIND_SMTP_USER")
                and _cfg("PHOTOBIND_SMTP_PASSWORD"))


def configured() -> bool:
    return bool(_brevo_key()) or _smtp_configured()


def mode() -> str:
    if _brevo_key():
        return "brevo-api"
    return "smtp" if _smtp_configured() else "console"


def admin_email() -> str:
    return _cfg("PHOTOBIND_ADMIN_EMAIL", "support@patienceai.in")


def _sender() -> dict:
    raw = _cfg("PHOTOBIND_MAIL_FROM", f"{APP_NAME} <support@patienceai.in>")
    if "<" in raw and ">" in raw:
        name = raw.split("<")[0].strip().strip('"')
        addr = raw.split("<")[1].split(">")[0].strip()
    else:
        name, addr = APP_NAME, raw.strip()
    return {"name": name or APP_NAME, "email": addr}


def _send_brevo(to: str, subject: str, text: str, html: str | None) -> str:
    payload = {
        "sender": _sender(),
        "to": [{"email": to}],
        "subject": subject,
        "textContent": text,
    }
    if html:
        payload["htmlContent"] = html
    req = urllib.request.Request(
        BREVO_ENDPOINT,
        data=json.dumps(payload).encode(),
        headers={"api-key": _brevo_key(), "content-type": "application/json",
                 "accept": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            if r.status in (200, 201, 202):
                return "brevo-api"
            log.error("brevo api unexpected status %s to=%s", r.status, to)
            return "failed"
    except urllib.error.HTTPError as e:
        body = e.read()[:300].decode(errors="replace")
        log.error("brevo api %s to=%s: %s", e.code, to, body)
        return "failed"
    except Exception as e:                     # noqa: BLE001
        log.error("brevo api send failed to=%s: %s", to, e)
        return "failed"


def _send_smtp(to: str, subject: str, text: str, html: str | None) -> str:
    msg = EmailMessage()
    sender = _sender()
    msg["From"] = f"{sender['name']} <{sender['email']}>"
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    host, port = _cfg("PHOTOBIND_SMTP_HOST"), int(_cfg("PHOTOBIND_SMTP_PORT", "587"))
    try:
        with smtplib.SMTP(host, port, timeout=20) as s:
            s.starttls()
            s.login(_cfg("PHOTOBIND_SMTP_USER"), _cfg("PHOTOBIND_SMTP_PASSWORD"))
            s.send_message(msg)
        return "smtp"
    except Exception as e:                     # noqa: BLE001
        log.error("smtp send failed to=%s: %s", to, e)
        return "failed"


def send(to: str, subject: str, text: str, html: str | None = None) -> str:
    """Returns the transport actually used: brevo-api | smtp | console | failed.

    Mail failure never raises: a signup must not 500 because an email could
    not be delivered. The caller reports the transport instead.
    """
    if _brevo_key():
        result = _send_brevo(to, subject, text, html)
        if result != "failed" or not _smtp_configured():
            return result
        log.warning("brevo api failed, falling back to smtp for %s", to)
        return _send_smtp(to, subject, text, html)
    if _smtp_configured():
        return _send_smtp(to, subject, text, html)
    log.info("[console-mail] to=%s subject=%s\n%s", to, subject, text)
    return "console"


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------

def send_code(to: str, code: str, minutes: int) -> str:
    text = (
        f"Your {APP_NAME} verification code is {code}\n\n"
        f"Enter it on the sign-up screen within {minutes} minutes.\n"
        f"If you didn't ask to create an account, ignore this email — "
        f"nothing was created.\n\n"
        f"{APP_NAME} · a product of {COMPANY} · {SITE}\n")
    html = f"""<div style="font-family:system-ui,sans-serif;color:#201e1d;max-width:520px">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#1F3FB5;margin:0 0 24px">{APP_NAME.lower()} · verify your email</p>
<p style="font-size:15px;margin:0 0 12px">Your verification code:</p>
<p style="font-family:ui-monospace,monospace;font-size:34px;font-weight:700;letter-spacing:.18em;margin:0 0 12px">{code}</p>
<p style="font-size:14px;margin:0 0 12px">Enter it on the sign-up screen within {minutes} minutes.</p>
<p style="font-size:14px;color:#605d5d;margin:0 0 24px">If you didn't ask to create an account, ignore this email — nothing was created.</p>
<hr style="border:0;border-top:2px solid rgba(32,30,29,.25);margin:24px 0">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#605d5d;margin:0">{APP_NAME} · a product of {COMPANY} · <a href="{SITE}" style="color:#B00A6F">patienceai.in</a></p>
</div>"""
    return send(to, f"{code} is your {APP_NAME} verification code", text, html)


def send_welcome(to: str, name: str) -> str:
    first = (name or "there").split(" ")[0]
    text = f"""Welcome to {APP_NAME}, {first}.

{APP_NAME} turns a photo into a code any camera can read — while the photo
still looks like you. Here's the whole thing in four steps.

1. Create a code
   Sign in, open "New code", type what a scan should open, and pick a photo
   with a clear face. We place the code away from the face, then check it
   actually scans before showing it to you. You'll see the measured decode
   confidence — not a promise, a number.

2. Share it, one copy at a time
   Every share you make is its own instance with its own link and its own
   scan log. Label them ("LinkedIn", "conference badge") so a leak is
   traceable to the copy that leaked.

3. Revoke whenever you want
   Revoke works after you've shared it. The next scan sees "Revoked" and a
   plain explanation — never your data.

4. Verify a photo later
   Open "Scan", drop in an image and its share id, and we compare it against
   the code it was issued for: exact copy, harmless recompression, or content
   change. Treat it as evidence, not a verdict — small local edits can slip
   past it.

Two things worth knowing:

- The content behind your code is encrypted in your browser before it reaches
  us, and the key lives in the part of the link after the "#", which browsers
  never send to servers. We can't read what you bound.
- Face detection runs only while your code is being made, in memory. We never
  store face embeddings or any biometric template.

Terms: {SITE}/app/terms.html
Privacy: {SITE}/app/privacy.html

{APP_NAME} · a product of {COMPANY} · {SITE}
"""
    html = f"""<div style="font-family:system-ui,sans-serif;color:#201e1d;max-width:560px;line-height:1.55">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#1F3FB5;margin:0 0 12px">{APP_NAME.lower()} · getting started</p>
<h1 style="font-size:26px;letter-spacing:-.02em;margin:0 0 12px">Welcome, {first}.</h1>
<p style="font-size:15px;margin:0 0 24px">{APP_NAME} turns a photo into a code any camera can read — while the photo still looks like you. Here's the whole thing in four steps.</p>
{"".join(f'''<div style="margin:0 0 20px">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#1F3FB5;margin:0 0 4px">{i}</p>
<p style="font-weight:700;font-size:16px;margin:0 0 4px">{h}</p>
<p style="font-size:14px;margin:0;color:#3a3736">{b}</p></div>'''
for i, h, b in [
 ("step 1", "Create a code",
  'Open “New code”, type what a scan should open, and pick a photo with a clear face. We place the code away from the face and check it actually scans before showing it to you — you’ll see the measured decode confidence.'),
 ("step 2", "Share it, one copy at a time",
  'Every share is its own instance with its own link and scan log. Label them (“LinkedIn”, “conference badge”) so a leak is traceable to the copy that leaked.'),
 ("step 3", "Revoke whenever you want",
  'Revoke works after you’ve shared it. The next scan sees “Revoked” and a plain explanation — never your data.'),
 ("step 4", "Verify a photo later",
  'Open “Scan”, drop in an image and its share id, and we compare it against the code it was issued for: exact copy, harmless recompression, or content change. Treat it as evidence, not a verdict.'),
])}
<div style="background:#eae9e9;padding:16px;margin:0 0 24px">
<p style="font-size:14px;margin:0 0 8px"><strong>We can't read your payload.</strong> It's encrypted in your browser first, and the key lives after the “#” in your link — browsers never send that to servers.</p>
<p style="font-size:14px;margin:0"><strong>We never store face data.</strong> Detection runs only while your code is being made, in memory. No embeddings, no templates.</p>
</div>
<p style="font-size:13px;margin:0 0 24px"><a href="{SITE}/app/terms.html" style="color:#B00A6F">Terms</a> · <a href="{SITE}/app/privacy.html" style="color:#B00A6F">Privacy</a></p>
<hr style="border:0;border-top:2px solid rgba(32,30,29,.25);margin:0 0 12px">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#605d5d;margin:0">{APP_NAME} · a product of {COMPANY} · <a href="{SITE}" style="color:#B00A6F">patienceai.in</a></p>
</div>"""
    return send(to, f"Welcome to {APP_NAME} — how it works", text, html)


def send_account_deleted(to: str, name: str, codes_revoked: int) -> str:
    first = (name or "there").split(" ")[0]
    text = f"""Your {APP_NAME} account has been deleted, {first}.

What happened:
- {codes_revoked} code(s) were revoked. Anyone scanning them now sees
  "Revoked" and gets nothing else.
- The photos you uploaded were removed from our storage.
- Your session was ended.

This cannot be undone. If you delete by mistake you can sign up again, but
the old codes stay dead — that is the point of revocation.

If you did not ask for this, reply to this email immediately.

{APP_NAME} · a product of {COMPANY} · {SITE}
"""
    html = f"""<div style="font-family:system-ui,sans-serif;color:#201e1d;max-width:520px;line-height:1.55">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#B23C0F;margin:0 0 12px">{APP_NAME.lower()} · account deleted</p>
<h1 style="font-size:22px;margin:0 0 12px">Your account has been deleted, {first}.</h1>
<div style="border-top:4px solid #FF6C2F;background:#fff;padding:16px;margin:0 0 16px">
<p style="font-size:14px;margin:0 0 8px"><strong>{codes_revoked} code(s) revoked.</strong> Anyone scanning them now sees “Revoked” and gets nothing else.</p>
<p style="font-size:14px;margin:0 0 8px">Your uploaded photos were removed from our storage.</p>
<p style="font-size:14px;margin:0">Your session was ended.</p>
</div>
<p style="font-size:14px">This cannot be undone. You can sign up again, but the old codes stay dead — that is the point of revocation.</p>
<p style="font-size:14px">If you did not ask for this, reply to this email immediately.</p>
<hr style="border:0;border-top:2px solid rgba(32,30,29,.25);margin:24px 0 12px">
<p style="font-family:ui-monospace,monospace;font-size:11px;color:#605d5d;margin:0">{APP_NAME} · a product of {COMPANY} · <a href="{SITE}" style="color:#B00A6F">patienceai.in</a></p>
</div>"""
    return send(to, f"Your {APP_NAME} account has been deleted", text, html)


def send_report_to_admin(kind: str, summary: str, detail: str,
                         reporter: str | None, diagnostics: dict | None) -> str:
    """Feedback, bug reports, and automatic crash reports all land here.

    Diagnostics are included only when the sender opted in; the payload is
    whatever the client chose to attach and is reproduced verbatim so nothing
    is quietly added on the server side.
    """
    lines = [
        f"kind:      {kind}",
        f"from:      {reporter or 'anonymous'}",
        f"summary:   {summary}",
        "",
        detail or "(no detail given)",
    ]
    if diagnostics:
        lines += ["", "-- diagnostics (sender opted in) --",
                  json.dumps(diagnostics, indent=2, sort_keys=True)[:6000]]
    else:
        lines += ["", "(no diagnostics attached — the sender declined or none were available)"]
    text = "\n".join(lines) + f"\n\n{APP_NAME} · {SITE}\n"
    subject = f"[{APP_NAME}] {kind}: {summary[:70]}"
    return send(admin_email(), subject, text)


def send_dev_welcome(to: str, name: str, console_url: str) -> str:
    """Sent once, when a developer account is created."""
    first = (name or "there").split(" ")[0]
    text = f"""Welcome to the {APP_NAME} API, {first}.

Your developer account is separate from any {APP_NAME} account you use to make
your own codes, and it is the only place API keys appear.

Console: {console_url}

The rules worth knowing before you build:

1. Every request is signed. There is no bearer token.
   Send X-Api-Key, X-Api-Timestamp, X-Api-Nonce and X-Api-Signature. The
   signature is HMAC-SHA256 over the method, the path, the timestamp, the nonce
   and a SHA-256 of the body, joined by newlines. The console shows the exact
   string and will sign a test request for you.

2. A secret is shown once.
   We keep it encrypted and cannot show it again. Lose it and you revoke the key
   and make another.

3. Never ship a secret inside an app.
   Anything in an APK or a web page is readable by anyone who has it. Put the
   secret on a server you control and have your app call that.

4. Timestamps and nonces are checked.
   More than five minutes out of step is refused, and a nonce cannot be reused —
   which is what stops a captured request being replayed.

5. Thirty requests a minute per key.
   Over that you get 429 with Retry-After. Ask if you need more.

6. We never accept a plaintext payload.
   Encrypt with AES-256-GCM on your side and send ciphertext. The key belongs in
   the part of the link after the # so it never reaches us. That is the whole
   point of the product, and the API holds the same line as the apps.

— {COMPANY}
"""
    return send(to, f"Your {APP_NAME} developer account", text)


def send_dev_key_created(to: str, name: str, key_id: str, scopes: str,
                         console_url: str) -> str:
    """Sent whenever a key is minted — so a key you did not create reaches you."""
    first = (name or "there").split(" ")[0]
    text = f"""{first}, a new API key was created on your developer account.

  key id:  {key_id}
  scopes:  {scopes}

The secret was shown in the console at the moment of creation and is not in this
email — we keep it encrypted and cannot recover it, and an emailed secret is a
secret sitting in a mailbox.

If this was not you, revoke it now: {console_url}

Reminders:
  - never ship the secret inside an app or a web page
  - sign every request; a nonce may not be reused and the clock must be within
    five minutes
  - thirty requests a minute per key
  - payloads must arrive encrypted; we refuse plaintext

— {COMPANY}
"""
    return send(to, f"New API key on your {APP_NAME} developer account", text)


def send_dev_deleted(to: str, name: str, keys_revoked: int) -> str:
    """Confirms in writing what a developer account deletion destroyed."""
    first = (name or "there").split(" ")[0]
    text = f"""{first}, your {APP_NAME} developer account has been deleted.

  API keys revoked: {keys_revoked}

Any integration still using those keys stops working immediately. This cannot be
undone, and we kept nothing that would let us restore it.

Codes you created through the API keep answering that they have been switched
off, so anything already printed or shared reports its state rather than looking
like it never existed.

— {COMPANY}
"""
    return send(to, f"Your {APP_NAME} developer account has been deleted", text)
