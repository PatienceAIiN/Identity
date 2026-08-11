# Poster / carousel — Identity, the whole product

Everything the product does, including the developer console and the free API.
A single poster listing eight features is a brochure, so this is a **carousel**:
one idea per slide, each slide minimal, the set adding up to the whole thing.
A one-image version for LinkedIn is at the end.

Every number here is what the server actually enforces. Do not round them.

## Formats

| Where | Size | Notes |
|---|---|---|
| Instagram carousel | 1080 × 1350 × 9 slides | the primary |
| LinkedIn document | 1200 × 1500 × 9 | same slides, exported as a PDF |
| LinkedIn single image | 1200 × 627 | the summary version at the end |

## Palette — exactly these

| Role | Hex | Where |
|---|---|---|
| Ground (slides 1–7) | `#FFFFFF` | product slides are light |
| Ground (slides 8–9) | `#0E0D12` | the developer slides flip dark |
| Ink | `#201E1D` light · `#ECE9F2` dark | headlines |
| Muted | `#605D5D` light · `#8E8A99` dark | the one supporting line |
| Accent | `#FF48B0` | one element per slide. Never two. |
| Live | `#00A95C` | the dot after the wordmark, and "live" states |
| Revoked | `#FF6C2F` | only on slide 3 |

## Type

Monospace throughout (JetBrains Mono or Courier New).
Headline `56 px` bold, `-0.03em`. Supporting line `22 px` muted. Wordmark `26 px`.
Margin `72 px`. Everything left-aligned to it. Nothing centred except artwork.

---

## The nine slides

Each slide: wordmark top-left, headline, **one** supporting line, and a piece of
artwork. Nothing else. If a slide needs two supporting lines, it is two slides.

### 1 — the hook
```
Identity ▪

The photo
is the code.
```
> supporting: *Your picture, still your picture — and any camera reads it.*
> **art:** the fused portrait, full width, face clear, module dots visible in
> hair and background.

### 2 — it stays your photo
```
Still a photo.
Not a barcode.
```
> supporting: *The code spans the whole image. A face gets extra room.*
> **art:** side-by-side — an ordinary black-and-white QR at 30% width, the fused
> portrait at 70%. No labels; the difference is the point.

### 3 — off means off
```
Share it.
Then take it back.
```
> supporting: *Switch any copy off after you've shared it. The next scan is told, and told nothing else.*
> **art:** one flat `#FF48B0` button that has drained to a grey outline, and the
> word `REVOKED` in `#FF6C2F`.

### 4 — we can't read it
```
Encrypted before
it reaches us.
```
> supporting: *Locked on your device. The key rides in the part of a link servers never receive.*
> **art:** the link, broken across two lines, with everything after the `#` in
> `#FF48B0`:
> `identity.patienceai.in/r/AS5gpHmfXtk` / `#your-key-never-leaves`

### 5 — one photo, many copies
```
One photo.
Many codes.
```
> supporting: *Every copy has its own scan history, so a leak points at one copy.*
> **art:** four identical small prints fanned in a row, each with a different
> paper label: `linkedin` `badge` `email` `poster`.

### 6 — tamper evidence
```
Check it later.
```
> supporting: *Same picture, harmless re-save, or changed — we tell you which.*
> **art:** three stacked rows, monospace, only the middle one accented:
> `EXACT MATCH` / `CONSISTENT COPY` / `CONTENT CHANGED`

### 7 — try it
```
Five codes.
No account.
```
> supporting: *Free trial in the browser, plus an Android app with its own scanner.*
> **art:** the phone with the pink frame snapped to a portrait, green dot at the
> corner.

### 8 — the developer console *(ground flips to `#0E0D12`)*
```
Put them in
your app. free.
```
> supporting: *10 calls/min · 300 a month · resets the 1st. Keys, usage and peak graphs in the console.*
> **art:** the code block, panel `#17161C`, padding 32px:
> ```
> POST /api/v1/codes
> X-Api-Key:       pbk_...
> X-Api-Signature: hmac-sha256(...)
>
> → 201  your code, ready to print
> ```

### 9 — the close *(dark)*
```
identity
.patienceai.in
```
> supporting: *Free to try. Developer console at /dev.*
> **art:** none. Empty space, the URL in `#FF48B0`, and at the very bottom in
> muted: `A product of Patience AI`

---

## LinkedIn single image — 1200 × 627

Dark ground. Margin `56 px`. Two columns, `560 px` and `520 px`, `64 px` gutter.

- **Left:** wordmark · headline `The photo is the code.` at `48 px` ·
  the URL in accent at the bottom.
- **Right:** six lines, `20 px`, muted except the accent bullet:
  ```
  ▪ still looks like your photo
  ▪ any camera reads it
  ▪ encrypted before it reaches us
  ▪ switch any copy off, anytime
  ▪ every copy has its own history
  ▪ free API — 300 calls a month
  ```
- **Bottom-left, under the URL:** `A product of Patience AI`, muted, plain text.

---

## Caption — Instagram

> Identity is live.
>
> A photo that still looks like your photo, and that any camera reads. What it
> opens is encrypted before it leaves your device. You can switch any copy off
> after you've shared it — and every copy keeps its own scan history, so a leak
> points at one copy instead of at "the QR code". You can also check a picture
> later and be told whether it's the one the code was made for.
>
> Five codes free, no account. Android app with its own scanner.
>
> And there's now a developer console: put photo-bound codes in your own app,
> free — 10 calls a minute, 300 a month.
>
> identity.patienceai.in
>
> A product of Patience AI

## Caption — LinkedIn

> We've launched Identity.
>
> A printed QR code is normally a permanent decision. It carries your data in the
> open, it works forever, and if it turns up somewhere you didn't expect there's
> no way to tell which copy leaked.
>
> Identity puts a meaningless id in the code instead. What it opens is encrypted
> on your device, and the key travels in the part of a link browsers never send to
> a server — so the scanner's own browser decrypts it and we hold the locked
> version without the key. That makes three things possible a static code can't
> do: switch any copy off after sharing, give every copy its own scan history, and
> check a photo later for tampering.
>
> The photo still looks like the photo. Every code is scanned by real decoders —
> compressed, tilted, shrunk — before you ever see it, and you're shown the number.
>
> Also live: a developer console. Create a key and put photo-bound codes in your
> own product, free — 10 requests a minute, 300 a month. Requests are signed with
> HMAC rather than bearing a token, so nothing usable sits inside your app, and
> the API refuses plaintext payloads outright — the same rule our own apps follow.
>
> Free to try, no account: identity.patienceai.in
> Developer console: identity.patienceai.in/dev
>
> A product of Patience AI

---

## Accuracy rules

- **"Free", never "free forever" or "unlimited".** The limits are real and are on
  slide 8 for that reason.
- **No world-first claim.** Putting a picture inside a scannable code is prior art
  from 2013; our own terms say so and an ad must not contradict them.
- **No Google Lens or iPhone-camera claim.** Consumer-scanner testing isn't
  finished. "Any camera reads it" is the product's design intent and is fine;
  naming a specific scanner as verified is not.
- **Slide 6 must not say "detects tampering".** It reports evidence and can miss
  small local edits — "we tell you which" is the honest framing.
- **No stock photo of a person holding a phone.** No scanning-beam graphic. No
  "Scan me" instruction: if the artwork needs to say it's scannable, it failed.
