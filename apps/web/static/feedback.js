/* Feedback, bug reports, and automatic crash reporting.

   Two rules this file exists to honour:
   - Diagnostics are opt-in and shown before sending. You can read exactly
     what would be attached; declining means it is not stored at all.
   - Nothing that could carry a decryption key is ever collected. The URL is
     recorded without its fragment, because the fragment IS the key.
*/

/* What we would attach — assembled fresh each time so the preview cannot
   drift from what is actually sent. */
function collectDiagnostics(extra = {}) {
  const d = {
    // location.pathname + search only: never location.hash (that is the key).
    page: location.pathname + (location.search || ""),
    referrer_host: (() => { try { return new URL(document.referrer).host; } catch { return ""; } })(),
    user_agent: navigator.userAgent,
    language: navigator.language,
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    device_pixel_ratio: window.devicePixelRatio,
    online: navigator.onLine,
    timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
    reduced_motion: matchMedia("(prefers-reduced-motion: reduce)").matches,
    recent_errors: window.__pbErrors ? window.__pbErrors.slice(-5) : [],
    ...extra,
  };
  return d;
}

/* Ring buffer of recent errors, so a bug report can carry what actually
   happened instead of "it broke". */
window.__pbErrors = window.__pbErrors || [];
function recordError(entry) {
  window.__pbErrors.push({ ...entry, at: new Date().toISOString() });
  if (window.__pbErrors.length > 20) window.__pbErrors.shift();
}

async function sendReport({ kind, summary, detail = "", diagnostics = null,
                            reporter_email = null }) {
  return api("/v1/reports", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      kind, summary, detail,
      platform: "web",
      app_version: document.documentElement.dataset.appVersion || "web",
      reporter_email,
      include_diagnostics: diagnostics !== null,
      diagnostics,
    }),
  });
}

/* -- automatic crash reporting -------------------------------------------
   Unhandled errors are reported without diagnostics unless the person has
   already agreed to share them (stored choice). Either way they get told,
   rather than having something sent silently behind their back. */
const CRASH_CONSENT_KEY = "pb_crash_consent";

function crashConsent() { return localStorage.getItem(CRASH_CONSENT_KEY); }
function setCrashConsent(v) { localStorage.setItem(CRASH_CONSENT_KEY, v); }

let crashReported = 0;
async function reportCrash(summary, detail) {
  if (crashReported >= 3) return;      // never spam on a render loop
  crashReported += 1;
  const consented = crashConsent() === "yes";
  try {
    await sendReport({
      kind: "crash", summary, detail,
      diagnostics: consented ? collectDiagnostics() : null,
    });
  } catch (_) { /* reporting must never itself throw into the page */ }
  showCrashNotice(consented);
}

function showCrashNotice(withDiagnostics) {
  if (document.getElementById("pb-crash-note")) return;
  const el = document.createElement("div");
  el.id = "pb-crash-note";
  el.className = "crash-note";
  el.innerHTML = `
    <strong>Something broke on this page.</strong>
    <span>We've told the team${withDiagnostics ? " and included your device details" : ""}.</span>
    <button class="btn btn-ghost" id="pb-crash-more">Send details</button>
    <button class="btn btn-ghost" id="pb-crash-x" aria-label="Dismiss">Dismiss</button>`;
  document.body.appendChild(el);
  document.getElementById("pb-crash-x").onclick = () => el.remove();
  document.getElementById("pb-crash-more").onclick = () => {
    el.remove();
    openFeedback({ kind: "bug", prefill: "Something broke on this page" });
  };
}

window.addEventListener("error", (e) => {
  recordError({ type: "error", message: String(e.message || ""),
                source: (e.filename || "").split("/").pop(), line: e.lineno });
  reportCrash(`js error: ${String(e.message || "").slice(0, 120)}`,
              `${e.filename}:${e.lineno}:${e.colno}\n${e.error?.stack || ""}`.slice(0, 3000));
});
window.addEventListener("unhandledrejection", (e) => {
  const msg = String(e.reason?.message || e.reason || "");
  recordError({ type: "unhandled_rejection", message: msg.slice(0, 200) });
  reportCrash(`unhandled promise rejection: ${msg.slice(0, 120)}`,
              (e.reason?.stack || msg).slice(0, 3000));
});

/* -- the form -------------------------------------------------------------- */
function openFeedback({ kind = "feedback", prefill = "" } = {}) {
  if (document.getElementById("pb-fb")) return;
  const back = document.createElement("div");
  back.className = "modal-back";
  back.id = "pb-fb";
  back.innerHTML = `
    <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="fb-t" style="width:min(520px,100%)">
      <p class="kicker">tell us</p>
      <h4 id="fb-t" style="margin-bottom:12px">Feedback or a bug?</h4>
      <div style="display:inline-flex;border:1px solid rgba(32,30,29,.4);margin-bottom:12px">
        <label style="display:inline-flex;align-items:center;gap:6px;padding:8px 12px;font-size:13px;cursor:pointer">
          <input type="radio" name="fb-kind" value="feedback" ${kind === "feedback" ? "checked" : ""}>Feedback</label>
        <label style="display:inline-flex;align-items:center;gap:6px;padding:8px 12px;font-size:13px;cursor:pointer;border-left:1px solid rgba(32,30,29,.4)">
          <input type="radio" name="fb-kind" value="bug" ${kind === "bug" ? "checked" : ""}>Bug</label>
      </div>
      <div class="field" style="margin-bottom:12px">
        <label for="fb-sum">In one line, what happened?</label>
        <input id="fb-sum" class="input" value="${prefill.replace(/"/g, "&quot;")}"
               placeholder="Revoke didn't take effect">
      </div>
      <div class="field" style="margin-bottom:12px">
        <label for="fb-det">Anything else — what you expected, what you saw</label>
        <textarea id="fb-det" class="input" rows="4" style="min-height:90px;resize:vertical"></textarea>
      </div>
      <div class="field" id="fb-email-row" style="margin-bottom:12px">
        <label for="fb-email">Email, if you want a reply</label>
        <input id="fb-email" type="email" class="input" placeholder="optional">
      </div>
      <label style="display:flex;gap:8px;align-items:flex-start;font-size:13px;cursor:pointer">
        <input type="checkbox" id="fb-diag" style="margin-top:3px;flex:none">
        <span>Include a diagnostics report — page, browser, screen size, and any
          recent errors. <button type="button" class="btn btn-ghost" id="fb-peek"
          style="padding:0 4px">See exactly what</button></span>
      </label>
      <pre id="fb-diag-view" hidden class="codebox" style="max-height:150px;overflow:auto;margin-top:12px"></pre>
      <p class="mono muted" style="font-size:10.5px;margin:12px 0 0">
        never included: your payload, your keys, or the part of a link after the #</p>
      <p class="err" id="fb-err" hidden style="margin-top:12px"></p>
      <div style="display:flex;gap:12px;margin-top:24px">
        <button class="btn btn-secondary" id="fb-cancel" style="flex:1">Cancel</button>
        <button class="btn btn-primary" id="fb-send" style="flex:1">Send</button>
      </div>
    </div>`;
  document.body.appendChild(back);

  const $$ = (id) => document.getElementById(id);
  if (crashConsent() === "yes") $$("fb-diag").checked = true;

  $$("fb-peek").onclick = () => {
    const v = $$("fb-diag-view");
    v.hidden = !v.hidden;
    if (!v.hidden) v.textContent = JSON.stringify(collectDiagnostics(), null, 2);
    $$("fb-peek").textContent = v.hidden ? "See exactly what" : "Hide";
  };
  const close = () => back.remove();
  $$("fb-cancel").onclick = close;
  back.addEventListener("click", (e) => { if (e.target === back) close(); });

  $$("fb-send").onclick = async () => {
    const summary = $$("fb-sum").value.trim();
    if (!summary) {
      $$("fb-err").textContent = "Add one line about what happened so we can act on it.";
      $$("fb-err").hidden = false;
      return;
    }
    const wantsDiag = $$("fb-diag").checked;
    setCrashConsent(wantsDiag ? "yes" : "no");
    const done = withSpinner($$("fb-send"), "Sending");
    try {
      const r = await sendReport({
        kind: document.querySelector('input[name="fb-kind"]:checked').value,
        summary,
        detail: $$("fb-det").value.trim(),
        reporter_email: $$("fb-email").value.trim() || null,
        diagnostics: wantsDiag ? collectDiagnostics() : null,
      });
      close();
      toast(r.delivery === "failed"
        ? "Saved — we'll see it even though the email didn't go out"
        : "Sent. Thank you.");
    } catch (e) {
      done();
      $$("fb-err").textContent = e.message;
      $$("fb-err").hidden = false;
    }
  };
  $$("fb-sum").focus();
}

/* No footer entry point: feedback is for people with an account, reachable
   from Profile. A crash notice can still open the form for anyone, because
   someone hitting a crash needs to be able to say so. */
