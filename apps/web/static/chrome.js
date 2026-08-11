/* Page chrome shared by every page: theme, navbar gating, footer.

   Loaded before the page's own script so the theme is applied before paint
   (no flash of the wrong theme) and the navbar never briefly shows signed-in
   links to a signed-out visitor.
*/

/* ── theme ──────────────────────────────────────────────────────────────── */
const THEME_KEY = "pb_theme";

function applyTheme(mode) {
  // "system" removes the attribute so the OS preference governs again.
  if (mode === "system") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", mode);
}

function currentTheme() {
  // Default light, not "system": the product should look the same for everyone
  // on a first visit. An explicit choice is remembered and always wins.
  return localStorage.getItem(THEME_KEY) || "light";
}

function effectiveTheme() {
  const t = currentTheme();
  if (t !== "system") return t;
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/* Mirror the effective theme onto a class so the toggle icon is correct even
   when dark came from the OS rather than an explicit choice. */
function markEffectiveTheme() {
  document.documentElement.classList.toggle("pb-dark", effectiveTheme() === "dark");
}
markEffectiveTheme();
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", markEffectiveTheme);

// Applied immediately, not on DOMContentLoaded — otherwise the first paint is
// the wrong colour.
applyTheme(currentTheme());

function themeToggleButton() {
  const b = document.createElement("button");
  b.className = "theme-toggle";
  b.type = "button";
  b.setAttribute("aria-label", "Switch theme");
  b.title = "Switch theme";
  // One morphing icon: a sun whose rays retract while a mask slides across to
  // carve a crescent. Same element in both states, so the change reads as a
  // transformation rather than a swap.
  b.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"
         stroke-linecap="round" aria-hidden="true">
      <mask id="tt-m">
        <rect width="24" height="24" fill="white"/>
        <circle class="tt-mask" cx="24" cy="0" r="9" fill="black"/>
      </mask>
      <circle class="tt-core" cx="12" cy="12" r="5" fill="currentColor"
              stroke="none" mask="url(#tt-m)"/>
      <g class="tt-rays" stroke="currentColor">
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2"/>
        <path d="M5 5l1.5 1.5M17.5 17.5L19 19M19 5l-1.5 1.5M6.5 17.5L5 19"/>
      </g>
    </svg>`;
  const sync = () => {
    markEffectiveTheme();
    const dark = effectiveTheme() === "dark";
    b.setAttribute("aria-pressed", String(dark));
    b.title = dark ? "Switch to light" : "Switch to dark";
    b.setAttribute("aria-label", b.title);
  };
  sync();
  b.addEventListener("click", () => {
    const next = effectiveTheme() === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyTheme(next);
    sync();
  });
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", sync);
  return b;
}

/* ── navbar: the app download sits with the other top-level destinations ───
   It goes in via script rather than into each page's markup so every page has
   it, and so it can be left out entirely when no release is published — a
   download button with nothing behind it is worse than none. */
function mountNavDownload() {
  document.querySelectorAll(".nav").forEach((nav) => {
    if (nav.querySelector("#nav-download")) return;
    const a = document.createElement("a");
    a.id = "nav-download";
    a.className = "btn btn-ghost";
    a.hidden = true;                       // until the manifest confirms a build
    a.style.display = "none";
    const signIn = nav.querySelector('a[href="/app/auth.html"]');
    if (signIn) nav.insertBefore(a, signIn); else nav.appendChild(a);
  });
}
mountNavDownload();

/* The developer console is a separate product with its own sign-in at /dev, so
   the link goes there rather than into the signed-in app. It stays out of the
   profile and shows no keys to a normal account — being findable and being
   readable are different things. */
function mountNavDeveloper() {
  document.querySelectorAll(".nav").forEach((nav) => {
    if (nav.querySelector("#nav-dev")) return;
    const a = document.createElement("a");
    a.id = "nav-dev";
    a.className = "btn btn-ghost";
    a.href = "/dev";
    a.textContent = "Developers";
    const signIn = nav.querySelector('a[href="/app/auth.html"]');
    if (signIn) nav.insertBefore(a, signIn); else nav.appendChild(a);
  });
}
mountNavDeveloper();

/* ── navbar: signed-in destinations are hidden until there is a session ──── */
const PRIVATE_LINKS = ["/app/codes.html", "/app/new.html", "/app/scan.html",
                       "/app/profile.html"];

function hidePrivateNav() {
  document.querySelectorAll(".nav a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (PRIVATE_LINKS.some((p) => href.startsWith(p))) a.hidden = true;
  });
}
function showPrivateNav() {
  document.querySelectorAll(".nav a").forEach((a) => {
    const href = a.getAttribute("href") || "";
    if (PRIVATE_LINKS.some((p) => href.startsWith(p))) a.hidden = false;
  });
  document.querySelectorAll('.nav a[href="/app/auth.html"]').forEach((a) => {
    a.hidden = true;                       // no "Sign in" once signed in
  });
  // The developer console is a different product with its own sign-in. Someone
  // already signed in here is using the app, not integrating against it.
  document.querySelectorAll("#nav-dev").forEach((a) => {
    a.hidden = true;
    a.style.display = "none";
  });
}

/* Hide first, reveal only after /v1/me confirms. Doing it the other way round
   flashes signed-in links at signed-out visitors. */
hidePrivateNav();

let sessionKnown = null;
async function refreshSession() {
  try {
    const me = await api("/v1/me");
    sessionKnown = me;
    showPrivateNav();
    document.body.dataset.signedIn = "1";
  } catch (_) {
    sessionKnown = null;
    hidePrivateNav();
    delete document.body.dataset.signedIn;
  }
  document.dispatchEvent(new CustomEvent("pb:session", { detail: sessionKnown }));
  return sessionKnown;
}

/* ── footer ─────────────────────────────────────────────────────────────── */

/* Inside the signed-in app the footer is pinned to the bottom of the window
   rather than sitting at the end of the document. These pages are short, tool-like
   screens: a footer that scrolls away takes the theme switch and the legal links
   with it. The marketing pages keep a flowing footer, because there the page
   really is something you read to the end. */
const PINNED_CHROME_PAGES = ["/app/codes.html", "/app/new.html",
                             "/app/scan.html", "/app/profile.html", "/dev"];

function chromeIsPinned() {
  return PINNED_CHROME_PAGES.some((p) => location.pathname.startsWith(p));
}

/* The navbar is pinned on the same pages as the footer, for the same reason:
   these are tool screens, and the way out of one should not require scrolling
   back to find it. The clearance is measured rather than assumed — the nav wraps
   to two rows on a narrow window, and a hardcoded offset would either clip the
   first heading or leave a gap. */
function pinNavOnAppPages() {
  if (!chromeIsPinned()) return;
  const nav = document.querySelector(".nav");
  if (!nav) return;
  Object.assign(nav.style, {
    position: "fixed", top: "0", left: "0", right: "0", zIndex: "50",
    background: "var(--pb-bg)", borderBottom: "2px solid rgba(32,30,29,.25)",
  });
  const spacer = document.createElement("style");
  spacer.id = "nav-clearance";
  document.head.appendChild(spacer);
  const fit = () => {
    spacer.textContent = `body{padding-top:${nav.offsetHeight}px}`;
  };
  fit();
  addEventListener("resize", fit);
  // The nav's own contents change once a session is known (links appear, "Sign
  // in" disappears), which can change its height — so re-measure then too.
  new MutationObserver(fit).observe(nav, { childList: true, subtree: true,
                                           attributes: true });
}

function pinFooterOnAppPages(footer) {
  if (!chromeIsPinned()) return;
  footer.style.position = "fixed";
  footer.style.left = "0";
  footer.style.right = "0";
  footer.style.bottom = "0";
  footer.style.zIndex = "40";
  footer.style.margin = "0";
  footer.style.background = "var(--pb-bg)";
  footer.style.borderTop = "2px solid rgba(32,30,29,.25)";
  // Clearance, so the last control on the page never ends up underneath it.
  const pad = document.createElement("style");
  pad.textContent = "body{padding-bottom:96px}";
  document.head.appendChild(pad);
}
function mountChrome() {
  if (!document.querySelector("footer")) {
    const f = document.createElement("footer");
    f.innerHTML = `
      <span class="footer-mark">Identity</span>
      <a href="/app/terms.html">Terms</a>
      <a href="/app/privacy.html">Privacy</a>
      <a href="/app/changelog.html">What's new</a>
      <span style="margin-left:auto;display:inline-flex;align-items:center;gap:16px">
        <span class="footer-by">A product of Patience AI</span>
      </span>`;
    document.body.appendChild(f);
    f.querySelector("span[style]").appendChild(themeToggleButton());
    pinFooterOnAppPages(f);
  }
  pinNavOnAppPages();
  refreshSession();
}

if (document.readyState === "loading")
  document.addEventListener("DOMContentLoaded", mountChrome);
else mountChrome();
