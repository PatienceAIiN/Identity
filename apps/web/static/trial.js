/* Free trial on the generator page.

   Signed in  -> the real flow: encrypted, saved, revocable, traceable.
   Signed out -> up to five trial codes. Nothing is stored, so a trial code
                 carries the link inside the picture and cannot be switched off
                 later. The page says that before and after generating, because
                 the difference is the whole product.

   The quota is enforced by the server. This file only reflects it — a counter in
   the browser would be a suggestion, not a limit.
*/

let TRIAL = { active: false, remaining: null, limit: 5 };

async function refreshTrial() {
  try {
    const s = await api("/v1/trial/status");
    TRIAL = {
      active: !!s.trial,
      remaining: s.remaining ?? null,
      limit: s.limit ?? 5,
    };
  } catch (_) {
    TRIAL = { active: true, remaining: null, limit: 5 };
  }
  renderTrialBanner();
  return TRIAL;
}

function renderTrialBanner() {
  let host = document.getElementById("trial-banner");
  if (!host) {
    host = document.createElement("div");
    host.id = "trial-banner";
    // The wrap on this page is a two-column grid, so inserting into it made the
    // banner a grid item: it took the first cell, pushed the form into the
    // second column and the preview down into the next row. Put the banner above
    // the grid instead, where it belongs and where it can't reflow anything.
    const anchor = document.querySelector(".wrap");
    if (!anchor) return;
    if (anchor.classList.contains("cols")) {
      // Same gutters and max width as the grid it sits above, so it lines up
      // with the columns instead of running to the window edges.
      host.className = "wrap";
      host.style.padding = "42px 42px 0";
      anchor.parentNode.insertBefore(host, anchor);
    } else {
      host.style.margin = "0 0 24px";
      anchor.insertBefore(host, anchor.firstChild);
    }
  }
  if (!TRIAL.active) {
    host.innerHTML = "";
    return;
  }
  const left = TRIAL.remaining;
  host.innerHTML = `
    <div class="trial-note">
      <strong>Trial${left !== null ? ` · ${left} of ${TRIAL.limit} left` : ""}</strong>
      <span>Trial codes aren't saved. Your link sits inside the picture, so
        anyone who scans it reads it — and it can't be switched off later.
        <a href="/app/auth.html">Create an account</a> for codes you can revoke,
        trace, and keep.</span>
    </div>`;
}

/* Shown when the fifth code has been used. Not dismissable into a dead end:
   the visitor can always read what they'd get, or leave. */
function trialExhaustedModal(message) {
  if (document.getElementById("trial-done")) return;
  const back = document.createElement("div");
  back.className = "modal-back";
  back.id = "trial-done";
  back.innerHTML = `
    <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="td-t"
         style="width:min(480px,100%)">
      <h4 id="td-t" style="margin-bottom:12px">That's all ${TRIAL.limit} free codes</h4>
      <p style="font-size:15px">${message || "Create an account to keep going."}</p>
      <p style="font-size:14px">With an account, a code becomes something you
        control: what it opens is locked before it leaves your device, you can
        switch any copy off after sharing it, and each copy keeps its own scan
        history.</p>
      <div style="display:flex;gap:12px;margin-top:24px">
        <a class="btn btn-secondary" href="/" style="flex:1;justify-content:center">Not now</a>
        <a class="btn btn-primary" href="/app/auth.html" style="flex:1;justify-content:center">Create an account</a>
      </div>
      <p class="mono muted" style="font-size:10.5px;margin:12px 0 0">
        already have one? <a href="/app/auth.html">sign in</a></p>
    </div>`;
  document.body.appendChild(back);
}

/* The generator calls this instead of /v1/codes when there is no session. */
async function generateTrial({ photoFile, payload, coverage }) {
  const fd = new FormData();
  fd.append("photo", photoFile);
  fd.append("payload", payload);
  fd.append("coverage", coverage);
  try {
    const r = await api("/v1/trial/codes", { method: "POST", body: fd });
    TRIAL.remaining = r.remaining;
    renderTrialBanner();
    return r;
  } catch (e) {
    if (e.status === 402) {
      TRIAL.remaining = 0;
      renderTrialBanner();
      trialExhaustedModal(e.message);
      throw Object.assign(new Error("trial_exhausted"), { handled: true });
    }
    throw e;
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  await refreshTrial();
  // Signing in on another tab flips this page to the real flow without a reload.
  document.addEventListener("pb:session", refreshTrial);
  if (TRIAL.active && TRIAL.remaining === 0) trialExhaustedModal();
});
