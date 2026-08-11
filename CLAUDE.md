# Build Spec — Photo-Bound Identity Codes

## 0. What you are building

A platform where a user uploads a photo and gets back **the same photo, still recognisable, that any standard QR scanner can read** (Google Lens, native iOS/Android camera, any scanner app). No special reader app required.

The scannable-photo generator is **not** the differentiator — that technology exists (Visualead, 2013; Denso Wave Frame QR; diffusion QR art). Do not build marketing copy or architecture around it being novel. The three pillars below are what make this product defensible. Treat them as non-negotiable requirements, not stretch goals.

### Pillar 1 — Zero-knowledge payload
The server must be **structurally incapable** of reading user payload data.

- Client generates a 256-bit key (`crypto.getRandomValues` / `SecureRandom`).
- Client encrypts the payload JSON with **AES-256-GCM** before upload.
- Server stores ciphertext + nonce only. The key never touches the server.
- QR encodes: `https://<short-domain>/r/{opaque_id}#{base64url_key}`
- The key lives in the **URL fragment**. Browsers do not transmit fragments to the server. The scanner's browser fetches ciphertext by `opaque_id`, then decrypts client-side.

### Pillar 2 — Revocable
Because the QR carries an opaque ID and not the data itself, resolution stays under user control forever: revoke instantly, set expiry, cap total scans, restrict to an allowlist. A printed code goes dead the moment the user says so.

### Pillar 3 — Per-share tracing
One photo, many codes. Each *share* mints a distinct `opaque_id` with a user label ("LinkedIn", "conference badge", "email signature"). Scan logs are per-instance, so a leak is attributable to a specific share. Every existing generator emits dead static payloads — this is the gap.

---

## 1. Repository layout

```
/apps
  /api          FastAPI (Python 3.12)
  /web          Next.js 15 App Router + TypeScript
  /android      Kotlin + Jetpack Compose
/packages
  /encoder      Python — QR/photo fusion engine (importable, no web deps)
  /harness      Python — decode-rate test rig
  /tokens       design tokens: tokens.json → CSS vars + Kotlin object
/infra          Terraform / docker-compose
```

---

## 2. Phase 0 — Validation harness. **Build this first.**

**Do not write a single line of UI until this phase passes its gate.** The entire product is worthless if generated images don't reliably scan. Find that out on day one, not month three.

Build `/packages/harness`:

**Generate** candidate images across a parameter sweep (module alpha over protected regions, QR version, texture-blend weights).

**Decode** each with three independent decoders — they disagree, and real-world scanners are all three:
- `pyzbar` (ZBar)
- `zxing-cpp` Python bindings
- `opencv` `QRCodeDetector`

**Degrade** across a realistic matrix, since images travel through messaging platforms:
- JPEG quality: 95 / 75 / 50 / 30
- Rotation: 0° / 10° / 20° / 30°
- Brightness: 0.6x / 1.0x / 1.4x
- Downscale: 100% / 50% / 25%
- Gaussian blur: σ 0 / 1.0 / 2.0

**Measure** two axes and plot the frontier:
- Decode rate (%) per condition
- Perceptual distortion: SSIM plus a face-region-only SSIM

**Gate — do not proceed to Phase 1 unless:** ≥ 85% decode at JPEG 75 / ±15° / 50% scale, at a face-region SSIM ≥ 0.90. If you can't hit it, report the actual achievable frontier and stop for a decision. Do not quietly lower the bar.

---

## 3. Phase 1 — Encoder (`/packages/encoder`)

Pipeline, in order:

1. **Detect protected regions.** MediaPipe Face Mesh or an OpenCV DNN face detector. Build a mask over eyes, nose bridge, mouth, and smooth cheek gradients — the configural features that carry identity.
   **In memory only. Discard immediately. Never persist a face embedding or landmark set.** See §8.
2. **Compute texture energy.** Sobel/Laplacian map. High-energy regions (hair, clothing, background, foliage) tolerate full-contrast modules invisibly.
3. **Encode QR.** Use `segno`. **Error correction level H** (~30% recovery — this budget is what lets the photo dominate). Version 2–3 only: encode a short opaque ID, never a long URL. Fewer, larger modules scan far more reliably.
4. **Fuse.** Per-module alpha = f(local texture energy, protection mask). Full contrast in texture, heavily attenuated over protected regions, letting EC level H recover the difference.
5. **Fixed patterns are non-negotiable.** The three finder patterns, timing track, and alignment patterns are **not** protected by error correction — the scanner locks the 1:1:3:1:1 finder ratio *before* decoding begins. Render them at full contrast. Stylise shape and colour if you like, but never break the ratio geometry. Enforce the 4-module quiet zone.
6. **Validate before returning.** Run the Phase 0 decoders on the output. If below threshold, re-tune and retry. **Never return an unvalidated image.** Surface the measured decode confidence to the user.

---

## 4. Phase 2 — Backend (`/apps/api`)

**Stack:** FastAPI, PostgreSQL, Redis, S3 + SSE-KMS. Encoding runs on a worker queue (arq or Celery), never inline in the request.

### Auth — Google SSO only, gating everything
- OAuth 2.0 Authorization Code + **PKCE**.
- **Web:** session in an `httpOnly`, `Secure`, `SameSite=Lax` cookie. No tokens in `localStorage`.
- **Android:** **Credential Manager API** with Google ID — this is the current API; the legacy Google Sign-In SDK is deprecated. Exchange the ID token server-side for your own session.
- Verify Google ID token signature, `aud`, `iss`, and `exp` server-side on every exchange.
- Short-lived access tokens (15 min) + rotating refresh tokens with **reuse detection** — a replayed refresh token revokes the whole family.

### Data model
```
users(id, google_sub UNIQUE, email, created_at)
photos(id, user_id, s3_key, created_at)
codes(id, user_id, photo_id, ciphertext, nonce, created_at)
code_instances(
  id,                    -- CSPRNG opaque_id, the value in the QR
  code_id, label, created_at, revoked_at,
  expires_at, max_scans, scan_count
)
scan_events(id, instance_id, ts, country, ua_hash)
```

### Endpoints
```
POST   /v1/codes                    create (accepts ciphertext, never plaintext)
POST   /v1/codes/{id}/instances     mint a new shareable instance
DELETE /v1/instances/{id}           revoke
POST   /v1/instances/{id}/rotate    rotate
GET    /v1/instances/{id}/scans     scan log
GET    /r/{opaque_id}               public resolution endpoint
```

Third-party API access: API keys with **HMAC request signing** (timestamp + nonce, reject replays outside a 5-minute window).

---

## 5. Phase 3 — Web (`/apps/web`)

Next.js 15 App Router. Landing page, Google SSO gate, generator, code manager (revoke / expiry / scan logs), and the public `/r/{id}` resolution page.

**The resolution page is the highest-risk surface in the entire product.** It handles the decryption key. It must be static, dependency-minimal, and under a strict CSP (§8). Treat any third-party script on this route as a key-exfiltration vector.

---

## 6. Phase 4 — Android (`/apps/android`)

Kotlin + Jetpack Compose, min SDK 26. Camera capture, upload, code management, share sheet integration. Encryption via `javax.crypto` AES-256-GCM with `SecureRandom` — mirror the web crypto exactly so payloads are cross-platform readable. Store session tokens in `EncryptedSharedPreferences`.

---

## 7. Design system

**Material 3 Expressive**, as specified. On Android use the expressive Material 3 APIs: `MaterialExpressiveTheme`, spring-based `MotionScheme.expressive()`, shape morphing on interaction, button groups, and the expressive loading indicators. On web there is no official M3 Expressive component library — build a token layer from `/packages/tokens` (JSON → CSS custom properties → Tailwind theme) so web and Android render from one source of truth.

Within M3E, use this specific direction — not default Material blue-on-grey:

**Colour** — derive full M3 tonal palettes from these source colours:
- Primary `#5B3DF5` — electric indigo
- Secondary `#00D9A3` — mint; reserved for the **live/scannable** state
- Tertiary `#FF5C7A` — rose; reserved for **revoked**
- Amber `#FFB020` — **expiring soon**
- Neutral with a slight cool cast, not pure grey

Colour carries state meaning here. Never use mint or rose decoratively — if it's mint, it's live.

**Type**
- Display: **Bricolage Grotesque** (variable width + weight; used with restraint, large and tight)
- Body: **Inter**
- Mono: **JetBrains Mono** — for opaque IDs, keys, and scan logs. IDs are the product's atoms; they should look like it.

**Signature element — "The Resolve."** The product's whole idea is that a photo and a code are the same object. Build one reusable module-field component that animates a photo dissolving into its QR module grid and back, driven by M3E spring physics. Use it in exactly three places: the landing hero, the generation loading state, and the code card on hover/press. Nowhere else — it's the memorable thing, and repetition kills it. Respect `prefers-reduced-motion`: cross-fade instead.

Keep everything around that signature quiet and disciplined. Generous whitespace, one accent per screen, no gradient meshes, no glassmorphism.

**Copy:** active voice, sentence case, plain verbs. Name things by what the person controls — "Revoke this code", not "Invalidate instance". An action keeps its name through the whole flow: the button that says "Revoke" produces a toast that says "Revoked". Errors state what happened and how to fix it; they don't apologise.

---

## 8. Security — hard rules

Violating any of these is a build failure, not a tradeoff.

1. **Never persist biometric data.** No face embeddings, no landmark sets, no templates — in DB, logs, caches, or S3. Detection is in-memory and discarded. Storing templates puts you under GDPR Art. 9, India's DPDP Act, and Illinois BIPA. This is the single largest legal risk in the build, and it is entirely avoidable.
2. **Opaque IDs must be CSPRNG.** `secrets.token_urlsafe(16)`. Sequential or timestamp-derived IDs let anyone enumerate the whole database.
3. **Rate-limit `/r/{id}` hard** (Redis, per-IP and global). Without it, enumeration walks your ID space regardless of entropy.
4. **Strip URL fragments everywhere server-side** — access logs, analytics, Sentry, error handlers. A fragment in a log is a leaked decryption key.
5. **Strict CSP on `/r/{id}`**: `default-src 'self'; script-src 'self'; object-src 'none'; base-uri 'none'`. No inline scripts, no third-party tags. XSS on this route means key theft.
6. **The server must never accept plaintext payloads.** If an endpoint can receive unencrypted user data, Pillar 1 is a marketing claim rather than an architectural property. Enforce it in the schema.
7. Photos in S3 with SSE-KMS, served only via short-TTL presigned URLs.
8. Full account deletion cascading to S3 objects.

---

## 9. Build order

Do not parallelise. Each phase gates the next.

1. Harness → hit the Phase 0 gate, report real numbers
2. Encoder → passes harness thresholds on a 50-photo test set across skin tones, lighting, and framing
3. Backend + Google SSO → auth, crypto, revocation, tracing
4. Web → landing, generator, manager, resolution page
5. Android

**Start with Phase 0 only.** Build the harness, run the sweep, and show me the decode-rate vs face-SSIM frontier before writing anything else. If the frontier is bad, we change the product, not the threshold.

---

## Dev environment notes (this machine)

- Repo root: `/home/harsh/photobind`. Python venv at `.venv/` (Python 3.14).
- `zbar` is built from source into `.venv/` (no sudo available); run harness tools with `LD_LIBRARY_PATH=$PWD/.venv/lib` so `pyzbar` finds `libzbar.so`.
- Live: **https://identity.patienceai.in** (Cloud Run us-central1, project
  gen-lang-client-0839484503). Postgres on the `patienceai` VM over Direct VPC
  egress (10.128.0.2, ufw rule + GCP rule, never public). Email via Brevo HTTP
  API. APK releases in Cloudflare R2 bucket `identity`. Health: /v1/health.
  Secrets for local scripts live in run/ (gitignored).
- Phase status: **Phase 0 strict 3-decoder gate: FAILED (permanent record —
  do not rewrite as passed).** Owner decision 2026-08-10 ("Option A+"):
  proceed photo-dominant under a new **Consumer Scanner Acceptance Gate**:
  (1) Google Lens, (2) native iOS camera, (3) Android/Google camera — each
  "if available", no compatibility claimed until actually tested — plus
  (4) zxing and (5) pyzbar. OpenCV QRCodeDetector is recorded in the
  compatibility matrix as **INCOMPATIBLE WITH CURRENT PHOTO-FUSION
  REPRESENTATION** (structural: decodes plain QRs, 0% on any fused code).
- Pre-Phase-1 validations (owner-mandated): A) real-device scans on
  representative samples, B) fresh binding evaluation on an untouched set
  with frozen thresholds, C) payload-capacity experiment keeping 256-bit
  AES-GCM (no key reduction to fit a version). Production backend does not
  start until these complete.
- **Additive phase (photo binding / tamper evidence) complete.** See
  `docs/evidence-report.md` for held-out results, limitations, and the
  claim-status ledger; `docs/threat-model.md`; `docs/standards.md`.
  `packages/binding` is the evidence/verification layer; `apps/api` is a
  REFERENCE dev API only (in-memory, dev keystore — do not deploy).
  Thresholds in `packages/binding/binding/thresholds.json` are calibrated
  artifacts — regenerate via `harness.binding_eval`, never hand-edit.
  Reproduce all numbers: `scripts/run_validation.py`.
