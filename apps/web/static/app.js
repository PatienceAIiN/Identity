/* photobind web — shared: API client, WebCrypto AES-256-GCM, toasts,
   and "The Resolve" canvas (ported from the design prototype). */

const API = ""; // same origin

async function api(path, opts = {}) {
  const r = await fetch(API + path, { credentials: "include", ...opts });
  let body = null;
  try { body = await r.json(); } catch (_) {}
  if (!r.ok) {
    // FastAPI reports validation failures as a list of objects, so using detail
    // directly rendered "[object Object]" at the person reading the screen.
    let detail = body && body.detail;
    if (Array.isArray(detail)) {
      detail = detail
        .map((d) => (d && (d.msg || d.message)) || (typeof d === "string" ? d : ""))
        .filter(Boolean)
        .join("; ");
    } else if (detail && typeof detail === "object") {
      detail = detail.msg || detail.message || JSON.stringify(detail);
    }
    throw Object.assign(new Error(detail || r.statusText),
                        { status: r.status, body });
  }
  return body;
}

function toast(msg) {
  document.querySelectorAll(".toast").forEach(t => t.remove());
  const el = document.createElement("div");
  el.className = "toast"; el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 2200);
}

/* -- crypto: AES-256-GCM, key never leaves the browser ------------------- */
const b64u = {
  enc: (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)))
    .replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, ""),
  dec: (s) => Uint8Array.from(
    atob(s.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - s.length % 4) % 4)),
    c => c.charCodeAt(0)),
};

async function encryptPayload(text) {
  const keyBytes = crypto.getRandomValues(new Uint8Array(32));
  const nonce = crypto.getRandomValues(new Uint8Array(12));
  const key = await crypto.subtle.importKey("raw", keyBytes, "AES-GCM", false, ["encrypt"]);
  const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv: nonce }, key,
                                         new TextEncoder().encode(text));
  return { ciphertext_b64: b64u.enc(ct), nonce_b64: b64u.enc(nonce),
           fragment_key: b64u.enc(keyBytes) };
}

async function decryptPayload(ciphertext_b64, nonce_b64, fragment_key) {
  const key = await crypto.subtle.importKey("raw", b64u.dec(fragment_key),
                                            "AES-GCM", false, ["decrypt"]);
  const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64u.dec(nonce_b64) },
                                         key, b64u.dec(ciphertext_b64));
  return new TextDecoder().decode(pt);
}

/* -- The Resolve — photo dissolving into its module grid and back --------
   Used in exactly three places: landing hero (ambient), generator progress
   (real), code card press (flicker). prefers-reduced-motion => cross-fade. */
class Resolve {
  constructor(canvas, imgUrl) {
    this.el = canvas; this.N = 33; this.grid = null; this.img = null;
    const img = new Image();
    img.onload = () => { this.img = img; this.build(); };
    img.src = imgUrl;
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  }
  setImage(img) { this.img = img; this.build(); }
  build() {
    const N = this.N, img = this.img, c = document.createElement("canvas");
    c.width = c.height = N;
    const x = c.getContext("2d"), s = Math.min(img.width, img.height);
    x.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, N, N);
    const d = x.getImageData(0, 0, N, N).data, T = N * N;
    const L = new Float32Array(T);
    for (let i = 0; i < T; i++)
      L[i] = (0.299 * d[i*4] + 0.587 * d[i*4+1] + 0.114 * d[i*4+2]) / 255;
    const rand = i => { const v = Math.sin(i * 127.1 + 311.7) * 43758.5453; return v - Math.floor(v); };
    const mods = new Uint8Array(T);
    for (let i = 0; i < T; i++) mods[i] = rand(i) < 0.5 + (0.5 - L[i]) * 0.6 ? 1 : 0;
    const finder = new Uint8Array(T);
    const setF = (ox, oy) => { for (let y = 0; y < 7; y++) for (let x2 = 0; x2 < 7; x2++) {
      const ring = x2 === 0 || x2 === 6 || y === 0 || y === 6 || (x2 >= 2 && x2 <= 4 && y >= 2 && y <= 4);
      const i = (oy + y) * N + ox + x2; mods[i] = ring ? 1 : 0; finder[i] = 1; } };
    setF(0, 0); setF(N - 7, 0); setF(0, N - 7);
    // texture energy ranks the reveal order — busy regions resolve first
    const E = new Float32Array(T);
    for (let y = 0; y < N; y++) for (let x2 = 0; x2 < N; x2++) {
      let m = 0, n = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const yy = y + dy, xx = x2 + dx;
        if (yy >= 0 && yy < N && xx >= 0 && xx < N) { m += L[yy*N+xx]; n++; } }
      m /= n; let v = 0;
      for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
        const yy = y + dy, xx = x2 + dx;
        if (yy >= 0 && yy < N && xx >= 0 && xx < N) { const e = L[yy*N+xx] - m; v += e * e; } }
      E[y*N+x2] = v;
    }
    const idx = [...Array(T).keys()].sort((a, b) => E[b] - E[a]);
    const thr = new Float32Array(T);
    idx.forEach((ci, r) => { thr[ci] = 0.1 + 0.85 * r / T; });
    for (let i = 0; i < T; i++) if (finder[i]) thr[i] = 0.04 + rand(i) * 0.08;
    this.grid = { mods, thr, finder };
  }
  draw(p) {
    const el = this.el, ctx = el.getContext("2d"), S = el.width;
    ctx.fillStyle = "#eae9e9"; ctx.fillRect(0, 0, S, S);
    if (this.img) {
      const img = this.img, s = Math.min(img.width, img.height);
      ctx.filter = "grayscale(1) contrast(1.08)";
      ctx.drawImage(img, (img.width - s) / 2, (img.height - s) / 2, s, s, 0, 0, S, S);
      ctx.filter = "none";
    }
    if (!this.grid) return;
    const N = this.N, cs = S / N, g = this.grid;
    if (this.reduced) {
      ctx.globalAlpha = Math.max(0, Math.min(1, p));
      for (let i = 0; i < N * N; i++) {
        ctx.fillStyle = g.mods[i] ? "#201e1d" : "#f6f4f4";
        ctx.fillRect((i % N) * cs, (i / N | 0) * cs, cs, cs);
      }
      ctx.globalAlpha = 1; return;
    }
    for (let i = 0; i < N * N; i++) {
      const f = Math.max(0, Math.min(1, (p * 1.18 - g.thr[i]) / 0.16));
      if (f <= 0) continue;
      const o = 1 + 0.3 * Math.sin(f * Math.PI);
      let sz = cs * f * o; if (sz > cs * 1.12) sz = cs * 1.12;
      const px = (i % N) * cs, py = (i / N | 0) * cs;
      ctx.globalAlpha = Math.min(1, f * 1.4);
      ctx.fillStyle = g.mods[i] ? "#201e1d" : "#f6f4f4";
      ctx.fillRect(px + (cs - sz) / 2, py + (cs - sz) / 2, sz, sz);
    }
    ctx.globalAlpha = 1;
  }
  ambient() { // landing hero loop
    const ease = t => t * t * (3 - 2 * t), T = 12000, t0 = performance.now();
    const tick = now => {
      const ph = ((now - t0) % T) / T;
      let p;
      if (ph < 0.35) p = ease(ph / 0.35);
      else if (ph < 0.5) p = 1;
      else if (ph < 0.85) p = 1 - ease((ph - 0.5) / 0.35);
      else p = 0;
      this.draw(p);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }
}

/* -- session guard --------------------------------------------------------
   Signed-in pages start hidden and only reveal once /v1/me confirms a live
   session. The check also re-runs when a page is restored from the browser's
   back-forward cache, which is how a signed-out page could otherwise be
   redisplayed intact by the back button. */
function hidePage() { document.documentElement.style.visibility = "hidden"; }
function showPage() {
  // Must be explicit: clearing the inline value falls back to the
  // guard-preload rule (html{visibility:hidden}) and the page stays blank.
  document.documentElement.style.visibility = "visible";
}

async function requireAuth({ redirect = "/app/auth.html" } = {}) {
  try {
    const me = await api("/v1/me");
    showPage();
    return me;
  } catch (_) {
    hidePage();
    location.replace(redirect);   // replace: no signed-in entry in history
    return null;
  }
}

/* A bfcache restore does not re-run scripts, so re-verify on pageshow. */
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  if (document.body && document.body.dataset.requiresAuth === "1") requireAuth();
});

/* Sign out everywhere: revoke server-side, then land on the public page with
   no signed-in history entry to go back to. */
async function signOutAndLeave() {
  try { await api("/v1/auth/signout", { method: "POST" }); } catch (_) {}
  location.replace("/");
}

/* -- button busy state ----------------------------------------------------- */
function withSpinner(btn, label) {
  const original = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = `<span class="spin" aria-hidden="true"></span>${label || btn.textContent}`;
  return () => { btn.disabled = false; btn.innerHTML = original; };
}

/* -- confirmation dialog --------------------------------------------------- */
function confirmAction({ title, body, confirmLabel = "Confirm",
                         cancelLabel = "Cancel", destructive = false,
                         requireText = null }) {
  return new Promise((resolve) => {
    const back = document.createElement("div");
    back.className = "modal-back";
    back.innerHTML = `
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="cf-t">
        <h4 id="cf-t" style="margin-bottom:12px">${title}</h4>
        <p style="font-size:14px">${body}</p>
        ${requireText ? `<div class="field" style="margin:12px 0">
          <label for="cf-in">Type ${requireText} to confirm</label>
          <input id="cf-in" class="input mono"></div>` : ""}
        <div style="display:flex;gap:12px;margin-top:24px">
          <button class="btn btn-secondary" id="cf-no" style="flex:1">${cancelLabel}</button>
          <button class="btn btn-primary" id="cf-yes" style="flex:1${
            destructive ? ";background:var(--pb-revoked)" : ""}">${confirmLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(back);
    const yes = back.querySelector("#cf-yes");
    const input = back.querySelector("#cf-in");
    if (input) { yes.disabled = true;
      input.addEventListener("input", () => { yes.disabled = input.value !== requireText; });
      input.focus();
    } else yes.focus();

    const close = (v) => { back.remove(); document.removeEventListener("keydown", onKey); resolve(v); };
    const onKey = (e) => { if (e.key === "Escape") close(false); };
    yes.addEventListener("click", () => close(true));
    back.querySelector("#cf-no").addEventListener("click", () => close(false));
    back.addEventListener("click", (e) => { if (e.target === back) close(false); });
    document.addEventListener("keydown", onKey);
  });
}
