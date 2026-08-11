# Identity — NotebookLM podcast source

Paste this file into NotebookLM as a source and generate an Audio Overview. It is
written as briefing material rather than dialogue, because NotebookLM writes the
banter itself and a script fights it.

Companion sources worth adding: `docs/evidence-report.md`, `docs/threat-model.md`,
and `CLAUDE.md`. They contain the measured numbers and the limitations, which is
what keeps the episode from drifting into marketing.

**Target length** 10–14 minutes.
**Tone note for the prompt box:** "Curious and specific. Do not oversell. When the
sources state a limitation, say it plainly. Never claim this is a world first."

---

## The one-sentence version

Identity turns a photo into a code that any standard scanner can read, where the
code carries a meaningless id instead of your data — so the payload is encrypted
before it leaves your device, you can switch any copy off after you have shared
it, and every copy keeps its own scan history.

## What is genuinely not new, and say so early

Putting a picture inside a scannable code has existed since 2013 — Visualead did
it, Denso Wave shipped Frame QR, and diffusion-model QR art is everywhere. None
of that is ours and no world first is claimed. The episode should get this out of
the way in the first two minutes, because everything interesting is downstream of
it.

## The actual idea: a printed code is normally a permanent decision

An ordinary QR code contains your data in the open. Once it is printed on a badge,
a poster or a business card, it works forever, and anyone who photographs it keeps
it. There is no way to change your mind and no way to tell which copy leaked.

Identity puts an opaque id in the code instead. Resolution goes through a server
the owner controls, which makes three things possible that a static code cannot do:

1. **The contents are encrypted before upload.** A 256-bit AES-GCM key is generated
   on the device. The server stores ciphertext and a nonce. The key travels in the
   part of a link after the `#`, which browsers never send to a server — so the
   scanner's own browser decrypts it and the server structurally cannot read it.
2. **Any copy can be switched off.** The next person to scan is told it has been
   switched off, and told nothing else. There is no grace period and no cached copy.
3. **Every copy is its own code** with its own scan history, so a leak points at
   the copy it came from rather than at "the QR code".

## The engineering story worth telling: the gate that failed

The build started with a validation harness rather than a UI, on the theory that
the whole product is worthless if the images do not reliably scan. The gate was:
85% decode at JPEG 75, ±15° rotation, 50% scale, with a face-region SSIM of 0.90,
across three independent decoders — ZBar, zxing, and OpenCV.

**It failed, and it is recorded as failed.** OpenCV's detector reads ordinary QR
codes fine and returns 0% on any fused code — a structural incompatibility, not a
tuning problem. Rather than quietly lower the bar, the failure stayed on the record
and the acceptance criteria were rewritten in the open as a "consumer scanner
acceptance gate", with the honest note that phone-camera testing is still not
complete. That decision — write down the failure, change the product, do not move
the threshold — is the most interesting thing about how this was built.

## A concrete example of measuring instead of guessing

A scannable code needs a quiet zone: a lighter border around the grid that lets a
scanner find the code at all. It also washes out the edge of the photograph, and
the request was to remove it.

It cannot be removed — at zero, decode collapses to 67%. But how opaque it has to
be is a measurable question. Across ten photos and five degradation conditions,
decode stayed at 100% down to an opacity of 0.50 and first failed at 0.45. The
value shipped is 0.55: two steps of margin, decode unchanged, and measurably more
photograph visible. The general point: "make it less obtrusive" became a number
rather than an opinion.

## Tamper evidence, stated with its limits

A code can be checked against a photograph later. The answer is one of: the same
picture exactly, a harmless re-save, content changed, or inconclusive. On the
project's own held-out test set it flagged 87.5–90.7% of deliberate edits with a
false-positive rate under 1%.

It does not catch everything, and the sources say so: small local blurs,
pixelation over a face, and edits smaller than roughly 3% of the image can pass
undetected, and cropping breaks the match entirely. It is evidence, not a verdict.
An episode that skips this part is doing the listener a disservice.

## The deliberate legal choice

Face detection runs in memory and is discarded. No embedding, no landmark set, no
template is ever stored — not in the database, not in logs, not in a cache.
Storing them would put the project under GDPR Article 9, India's DPDP Act and
Illinois BIPA. It is the largest legal exposure in a product like this and it is
entirely avoidable, so it was avoided by design rather than by policy.

## Good questions for the hosts to chase

- If resolution depends on a server, what happens when the company disappears?
  (Stated plainly in the terms: existing codes stop resolving. That is the price
  of being able to revoke one.)
- Does "we can't read it" survive scrutiny? (For storage, yes. There is a
  documented deviation: when the server fuses the code into the photo, the key
  passes through server memory. It is never persisted or logged, and the strict
  fix — encoding on the device — is named as future work.)
- What is this actually for? (Conference badges, name tags, printed ID, an
  email signature — anywhere a code outlives the moment it was made for.)
- Who is it not for? (Anything where identity actually matters: the terms say
  outright that a resolving code proves the code is live, not that the person in
  the photo is present or consenting.)

## Facts to keep straight

- Free to try: five codes, no account. Trial codes are not stored and cannot be
  revoked, because the link sits inside the picture itself.
- Web plus an Android app; the app has an on-device scanner and installs its own
  updates.
- There is a signed third-party API — HMAC per request, five-minute window, no
  nonce reuse, thirty requests a minute per key.
- Live at identity.patienceai.in.
- A product of Patience AI.

## Do not say

"World first." "Patented." "Unhackable." "Tested with Google Lens." "AI-powered."
The sources support none of those, and two of them are actively contradicted.
