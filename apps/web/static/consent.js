/* Cookie and DPDP consent.
 *
 * The banner is not decoration: refusing analytics makes the server send a
 * Content-Security-Policy that omits Cloudflare's measurement host, so the
 * beacon cannot load at all. A choice that only sets a flag and lets the script
 * run anyway is worse than no banner, because it tells the person something
 * untrue.
 *
 * Everything else we set is a sign-in cookie or the free-trial counter. Those
 * are how the site functions rather than a preference, so they are stated in the
 * notice and not offered as a toggle — an app cannot keep you signed in without
 * remembering that you are.
 */

const CONSENT_COOKIE = "pb_consent";
const CONSENT_MONTHS = 6;

function consentValue() {
  const m = document.cookie.match(/(?:^|;\s*)pb_consent=([^;]+)/);
  return m ? decodeURIComponent(m[1]) : "";
}

function saveConsent(value) {
  const maxAge = CONSENT_MONTHS * 30 * 24 * 60 * 60;
  const secure = location.protocol === "https:" ? "; Secure" : "";
  document.cookie = `${CONSENT_COOKIE}=${value}; Path=/; Max-Age=${maxAge}`
    + `; SameSite=Lax${secure}`;
}

function closeConsent() {
  document.getElementById("consent-bar")?.remove();
}

/* Reload after a change so the server can apply — or stop applying — the policy
   that blocks the measurement script. Without the reload the page you are on
   would keep whatever it was loaded with, and the banner would be describing a
   future page rather than this one. */
function applyConsent(value) {
  saveConsent(value);
  closeConsent();
  location.reload();
}

function consentNotice() {
  return `
    <div style="max-width:1280px;margin:0 auto;display:grid;gap:12px">
      <p class="mono" style="font-size:11px;color:var(--pb-blue);margin:0">
        cookies and your data</p>
      <p style="font-size:13.5px;margin:0;max-width:78ch;line-height:1.7">
        We set a small number of cookies so the site works: one to keep you
        signed in, one to count free trial codes, and one to remember this
        choice. Those are not optional — without them you cannot stay signed in.
        Separately, our network provider can run a page-view measurement script
        that sends your IP address and the page address to Cloudflare. That one
        is your call, and saying no blocks it outright.
      </p>
      <p class="mono muted" style="font-size:11px;margin:0">
        you can change this whenever you like — the “Cookies” link in the footer
        reopens it. read the
        <a href="/app/privacy.html">privacy policy</a> for the full list and how
        to withdraw consent.
      </p>
      <div style="display:flex;gap:12px;flex-wrap:wrap;margin-top:4px">
        <button class="btn btn-primary" id="consent-all">Accept measurement</button>
        <button class="btn btn-secondary" id="consent-min">Only what's necessary</button>
      </div>
    </div>`;
}

function openConsent() {
  closeConsent();
  const bar = document.createElement("div");
  bar.id = "consent-bar";
  bar.setAttribute("role", "dialog");
  bar.setAttribute("aria-label", "Cookies and your data");
  bar.style.cssText = "position:fixed;left:0;right:0;bottom:0;z-index:80;"
    + "background:var(--pb-bg);border-top:2px solid var(--pb-accent);"
    + "padding:24px;box-shadow:0 -8px 32px rgba(0,0,0,.18)";
  bar.innerHTML = consentNotice();
  document.body.appendChild(bar);
  document.getElementById("consent-all")
    .addEventListener("click", () => applyConsent("all"));
  document.getElementById("consent-min")
    .addEventListener("click", () => applyConsent("necessary"));
}

/* A footer entry point, so withdrawing is as easy as giving. */
function mountConsentLink() {
  document.querySelectorAll("footer").forEach((f) => {
    if (f.querySelector(".consent-link")) return;
    const a = document.createElement("a");
    a.className = "consent-link";
    a.href = "#";
    a.textContent = "Cookies";
    a.addEventListener("click", (e) => { e.preventDefault(); openConsent(); });
    const privacy = f.querySelector('a[href*="privacy"]');
    if (privacy && privacy.parentNode) {
      privacy.parentNode.insertBefore(a, privacy);
      privacy.parentNode.insertBefore(document.createTextNode(" "), privacy);
    } else {
      f.appendChild(a);
    }
  });
}

document.addEventListener("DOMContentLoaded", () => {
  mountConsentLink();
  // The resolution page handles decryption keys and carries a strict policy of
  // its own; it gets no banner and no third-party anything.
  if (location.pathname.startsWith("/r/") || location.pathname.startsWith("/c/")) return;
  if (!consentValue()) openConsent();
});
