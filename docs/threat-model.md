# Threat model — photo-bound identity codes

Scope: the binding/verification layer built in this repository plus the
reference dev API. The production backend, web app, and Android app do not
exist yet; rows referencing them say so. "Test" cites the automated test or
measurement that demonstrates the mitigation — no test, no protection claim.

Statuses: **mitigated** (test exists) · **partial** (measured, with known
gaps) · **designed** (architecture supports it; not yet testable end-to-end)
· **not prevented** (architecture cannot stop it; stated residual risk).

---

## T1 — Casual image editor
Modifies the photo (crop, recolor, object edit) and re-shares it with the QR intact.

- **Asset at risk:** integrity of what the credential vouches for.
- **Existing mitigation:** none before this phase (QR resolved regardless).
- **New mitigation:** photo-binding verification; held-out measurement: 87.5%
  of semantic edits detected, 99.4% benign acceptance, FPR 0.6%
  (`packages/harness/results/binding_eval/metrics.json`).
- **Residual risk:** small local blur/pixelation can evade (measured
  boundaries in `results/boundaries.json`); detection is probabilistic, not
  a guarantee. **Status: partial.**
- **Test:** `harness/binding_eval.py` held-out run; `test_verify.py`.

## T2 — Attacker copies a valid QR onto another image
- **Asset:** trust in the visual identity of a share.
- **Existing:** none — the QR alone resolves and "looks valid".
- **New:** verification never returns AUTHENTIC on resolution alone; the
  spliced composite is flagged (44 regions in the recorded run) while the QR
  still resolves to the original share — attribution preserved.
- **Residual:** a scanner that only resolves the URL and never calls
  verification sees a valid share; the resolution page must surface
  verification affordances (Phase 3 work).
- **Test:** `harness/qr_survival.py::replay_test` — passes. **Status:
  mitigated at the verification layer; UI surface pending.**

## T3 — Attacker modifies pixels while retaining the QR
Same as T1 but deliberate, tuned to evade.

- **New:** as T1, plus adversarial boundary search quantifying the evasion
  margin (solid edits ≤~1.5–3% of min dimension evade; blur below σ≈10–20
  evades; `results/boundaries.json`).
- **Residual:** a knowledgeable adversary constrained to those magnitudes
  operates undetected; content-aware edits below tile scale evade. This is
  a **fundamental limit of fingerprint-based detection** — state it, don't
  paper over it. **Status: partial, quantified.**

## T4 — Attacker replaces the entire image
- **New:** unrelated-image control in the held-out run: 9/9 flagged
  CONTENT_MODIFIED; swap matrix (credential A + photo B) fails closed.
- **Test:** `test_verify.py::test_swap_matrix`, eval control rows.
  **Status: mitigated.**

## T5 — Attacker replays a valid credential
- **Existing (design):** per-share opaque IDs; revocation; scan caps.
- **New tests:** revoked → REVOKED (never silently missing), scan-cap state,
  sibling shares unaffected.
- **Residual:** replay of a *live* credential with the *unmodified* photo is
  legitimate resolution by design — tracing (per-share scan logs) is the
  control, not prevention. **Status: mitigated per design; scan-log
  analytics not yet built.**
- **Test:** `test_verify.py` revocation tests; `test_security.py::test_revoked_is_410_not_404`.

## T6 — Attacker enumerates IDs
- **Existing (design):** `secrets.token_urlsafe(16)` (128-bit CSPRNG).
- **New:** reference API rate limiting (per-client + global), enumeration
  test slams into 429.
- **Residual:** rate limiting slows enumeration; it cannot make a leaked or
  guessed ID unusable. 128-bit space makes blind guessing computationally
  irrelevant; the real leak vector is logs/referrers, covered by T-fragment
  handling. In-memory limiter is DEV ONLY — production needs Redis and this
  test re-run against it. **Status: mitigated for the reference API.**
- **Test:** `test_security.py::test_unknown_ids_404_then_rate_limited`.

## T7 — XSS through /r/{id}
- **Design:** strict CSP mandated (CLAUDE.md §8.5); resolution page must be
  static and dependency-minimal.
- **Current:** the reference API returns JSON only; no HTML resolution page
  exists yet, so the primary XSS surface **does not exist yet**. CSP tests
  must be written with the Phase 3 page. **Status: designed, not testable
  yet.**

## T8 — Malicious server operator
- **Asset:** payload confidentiality (Pillar 1) and binding integrity.
- **Existing (design):** ciphertext-only storage; key in URL fragment never
  transmitted.
- **New:** schema rejects any plaintext field (real HTTP test); binding is
  Ed25519-signed — server-side tampering of a stored binding invalidates it
  (`test_tampered_binding_invalidates_credential`).
- **Residual:** the operator can *delete* data (availability), can log scan
  metadata, and — critically — if the operator also runs the verification
  endpoint, a dishonest verifier can lie about results. Client-side
  verification of the signed binding is the eventual answer (Phase 3);
  today's verifier runs server-side. The operator cannot *read* payloads
  (no key) or *forge* bindings (no signing key compromise assumed — see T9).
  **Status: partial.**

## T9 — Stolen signing key
- **New:** key lifecycle with revocation: bindings signed by a revoked key
  fail closed (`test_revoked_signing_key_fails_closed`); rotation keeps
  historical bindings verifiable; key ids are content-derived.
- **Residual:** everything signed between theft and revocation is
  indistinguishable from legitimate; there is no transparency log to bound
  that window. Current keystore is file-based DEV ONLY — production KMS/HSM
  integration is required and **not implemented**. **Status: partial.**

## T10 — Compromised client/browser
- **Asset:** the decryption key (fragment) and plaintext payload at the only
  place they legitimately exist.
- **Mitigation:** none possible from the server side; this is outside the
  architecture's control. Strict CSP (T7) reduces in-page exfiltration
  vectors; a compromised OS/browser reads everything the user reads.
  **Status: not prevented — by design honesty, stated.**

---

## Cross-cutting: URL fragment secrecy

Fragments are never transmitted by compliant clients (verified: httpx strips
them; resolution succeeds without the fragment arriving). For non-compliant
clients that place a fragment in the HTTP request line, the ASGI stack
truncates it before the app layer, and the access-log middleware scrubs
`#`/`%23` remnants as defense in depth (`test_fragment_*`). This is
demonstrated for the reference stack only; Sentry/analytics/reverse-proxy
layers do not exist yet and MUST get equivalent tests when added.

## Privacy posture

No face embeddings, landmark sets, or biometric templates are persisted
anywhere (face detection in the Phase 0 harness is in-memory, test-data
only). Scan events in the data model carry country + hashed UA — IP
retention policy is a Phase 2 decision that must be audited then. This
design **reduces biometric-data exposure; it does not by itself constitute
GDPR/DPDP/BIPA compliance, which requires independent legal review.**
