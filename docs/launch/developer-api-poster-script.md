# Poster — developer console + free API

Announcement post for Instagram and LinkedIn. Same idea, two crops. The numbers
below are the real limits in production; do not round them up or call the API
"unlimited", because the first developer who hits 300 will notice.

## Formats

| Where | Size | Notes |
|---|---|---|
| Instagram feed | 1080 × 1350 | the primary; portrait reads bigger in-feed |
| Instagram square | 1080 × 1080 | crop of the same layout, drop the code block to 4 lines |
| LinkedIn | 1200 × 627 | landscape; two columns instead of stacked |

## Palette — exactly these, nothing else

| Role | Hex | Where |
|---|---|---|
| Ground | `#0E0D12` | the whole poster. Dark, because it is a developer post. |
| Ink | `#ECE9F2` | headline and code |
| Muted | `#8E8A99` | labels, the limits line |
| Accent | `#FF48B0` | one word in the headline, and the URL. Nothing else. |
| Live | `#00A95C` | the single dot after the wordmark |

No gradient, no glow, no glassmorphism, no drop shadow.

## Type

- Everything is monospace. One family (JetBrains Mono, or Courier New).
- Headline `64 px` bold, `letter-spacing: -0.03em`, line-height 1.15
- Code block `24 px` regular, line-height 1.75
- Labels and limits `18 px`, muted
- Wordmark `28 px` bold

---

## Instagram 1080 × 1350 — exact layout

Margin `72 px` on all sides. Everything left-aligned to that margin.

```
┌──────────────────────────────────────────────┐
│                                              │  y=72   Identity ▪            (28px, ink)
│  Identity ▪                                  │         ▪ is #00A95C, 9px square,
│                                              │         baseline-aligned, 12px gap
│                                              │  y=190
│  Put photo-bound                             │         headline, 64px bold, ink
│  codes in your                               │         "free" in #FF48B0
│  app. free.                                  │
│                                              │  y=470
│  ┌────────────────────────────────────────┐  │         code block:
│  │ POST /api/v1/codes                     │  │         panel #17161C, no border,
│  │ X-Api-Key:       pbk_...               │  │         padding 32px, 24px mono
│  │ X-Api-Signature: hmac-sha256(...)      │  │         width = full column
│  │                                        │  │         The signature line is the
│  │ → 201  your code, ready to print       │  │         point: it says "signed",
│  └────────────────────────────────────────┘  │         not "paste a token".
│                                              │  y=880
│  10 calls/min · 300/month · resets the 1st   │         18px muted, one line
│  keys, usage and graphs in the console       │
│                                              │  y=1010
│  identity.patienceai.in/dev                  │         28px, #FF48B0
│                                              │
│                                              │  y=1240
│  A product of Patience AI                    │         18px muted, plain text,
│                                              │         no link, no logo
└──────────────────────────────────────────────┘
```

## LinkedIn 1200 × 627 — same content, two columns

Margin `56 px`. Left column `520 px`, right column `560 px`, `64 px` gutter.

- **Left:** wordmark at top, then the headline at `48 px` (three lines), then
  the URL in accent at the bottom of the column.
- **Right:** the code block, vertically centred, `20 px` type.
- **Bottom-left, under the URL:** the limits line, then `A product of Patience AI`.

Nothing else moves. Do not add a person, a laptop, or a phone mockup.

---

## Copy — the complete text, both formats

```
Identity ▪

Put photo-bound
codes in your
app. free.

POST /api/v1/codes
X-Api-Key:       pbk_...
X-Api-Signature: hmac-sha256(...)

→ 201  your code, ready to print

10 calls/min · 300/month · resets the 1st
keys, usage and graphs in the console

identity.patienceai.in/dev

A product of Patience AI
```

Square crop: drop the two `X-Api-*` lines to one line reading
`X-Api-Signature: hmac-sha256(...)`, keep everything else.

---

## Caption — Instagram

> Identity now has a developer console.
>
> Create an API key, and your app can make photo-bound codes of its own: a
> picture that still looks like the picture, that any camera reads, and that you
> can switch off after you've shared it.
>
> Every request is signed — no bearer token sitting in your APK. Your key's
> secret is shown once and stored encrypted.
>
> Free: 10 calls a minute, 300 a month, resets on the 1st.
>
> identity.patienceai.in/dev
>
> A product of Patience AI

## Caption — LinkedIn

> We've opened the Identity API.
>
> Identity turns a photo into a code any standard scanner reads, while the photo
> stays recognisably a photo. What the code opens is encrypted before it leaves
> the device, any copy can be switched off after it's been shared, and every copy
> keeps its own scan history.
>
> The developer console is live at identity.patienceai.in/dev — create a key, see
> your usage and peak hours, and integrate in a few lines.
>
> Two decisions worth naming:
>
> Requests are signed rather than bearing a token. HMAC-SHA256 over the method,
> path, timestamp, nonce and body digest. A stale timestamp or a reused nonce is
> refused, so a captured request can't be replayed — and a mobile app never has to
> carry a credential that works on its own.
>
> The API will not accept a plaintext payload. Encrypt on your side and send
> ciphertext. That's the same rule our own apps follow, so an integration can't
> reach a weaker path than we use ourselves.
>
> Free tier: 10 requests a minute per key, 300 a month per account, resetting on
> the 1st. The monthly budget is per account, not per key.
>
> Docs and keys: identity.patienceai.in/dev
>
> A product of Patience AI

---

## Accuracy rules for whoever produces this

- **Say "free", not "free forever"** and not "unlimited". The limits are real and
  they are on the poster for a reason.
- **Do not write "no rate limits", "enterprise-grade", "military-grade" or
  "unhackable".**
- **Do not claim Google Lens or iPhone camera compatibility.** Consumer-scanner
  testing is not finished.
- **Do not claim a world first.** Putting a picture in a scannable code is prior
  art from 2013 — that position is stated in our terms and should not be
  contradicted in an ad.
- The `→ 201` line is illustrative; keep it as shown rather than inventing a
  fuller response body.
