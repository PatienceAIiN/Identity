/* Sign in / sign up: password reveal, consent gate, email verification.
   Sign-in for an address with no account says so and moves you to sign-up
   rather than failing generically. */

const $ = (id) => document.getElementById(id);
let mode = "signin";
let pendingEmail = "";

/* -- password reveal ------------------------------------------------------ */
$("eye").addEventListener("click", () => {
  const f = $("au-pass");
  const show = f.type === "password";
  f.type = show ? "text" : "password";
  $("eye").setAttribute("aria-pressed", String(show));
  $("eye").setAttribute("aria-label", show ? "Hide password" : "Show password");
  $("eye-open").hidden = show;
  $("eye-shut").hidden = !show;
  f.focus();
});

/* -- mode ------------------------------------------------------------------ */
function setMode(m) {
  mode = m;
  const up = m === "signup";
  $("f-name").hidden = !up;
  $("terms-row").hidden = !up;
  $("gnote").hidden = !up;
  $("title").textContent = up ? "Create your account" : "Sign in";
  $("blurb").textContent = up
    ? "Email and a password, or your Google account. Either way the key to your codes stays out of our reach."
    : "Welcome back. Your codes are where you left them.";
  $("submit").textContent = up ? "Create account" : "Sign in";
  $("au-pass").placeholder = up ? "at least 12 characters" : "your password";
  $("au-pass").autocomplete = up ? "new-password" : "current-password";
  $("err").hidden = true;
  gate();
}
document.querySelectorAll('input[name="mode"]').forEach((r) =>
  r.addEventListener("change", (e) => setMode(e.target.value)));

/* Create account stays disabled until the terms box is ticked. */
function gate() {
  $("submit").disabled = mode === "signup" && !$("accept").checked;
}
$("accept").addEventListener("change", gate);

function fail(msg) {
  $("err").textContent = msg;
  $("err").hidden = false;
}

function switchToSignUp(msg) {
  document.querySelector('input[name="mode"][value="signup"]').checked = true;
  setMode("signup");
  fail(msg);
}

/* -- submit ---------------------------------------------------------------- */
$("submit").addEventListener("click", async () => {
  const email = $("au-email").value.trim();
  const password = $("au-pass").value;
  if (!email.includes("@")) return fail("That email address is missing an @. Check it and try again.");
  if (mode === "signup") {
    if (!$("au-name").value.trim()) return fail("Add the name you want on your codes.");
    if (!$("accept").checked) return fail("Accept the terms and privacy policy to continue.");
  }
  const done = withSpinner($("submit"), mode === "signup" ? "Sending code" : "Signing in");
  try {
    if (mode === "signup") {
      const r = await api("/v1/auth/signup", {
        method: "POST", headers: { "Content-Type": "application/json" },
        // The box's real state, like the Google path. The check above already
        // blocks an unticked box; the server deciding on the real value is what
        // makes that a gate rather than a decoration.
        body: JSON.stringify({ name: $("au-name").value.trim(), email, password,
                               accept_terms: $("accept").checked }),
      });
      pendingEmail = r.email;
      $("otp-email").textContent = r.email;
      $("otp-mins").textContent = r.expires_in_minutes;
      if (r.delivery !== "smtp") {
        $("otp-dev").textContent =
          "email isn't configured on this server, so the code was written to the server log instead of sent";
        $("otp-dev").hidden = false;
      }
      $("step-form").hidden = true;
      $("step-otp").hidden = false;
      $("otp").focus();
    } else {
      await api("/v1/auth/signin", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, password }),
      });
      toast("Signed in");
      setTimeout(() => (location.href = "/app/new.html"), 350);
    }
  } catch (e) {
    if (e.status === 404) switchToSignUp("No account for that email yet. Create one below.");
    else if (e.status === 409 && /isn't finished/.test(e.message)) {
      pendingEmail = email;
      $("otp-email").textContent = email;
      $("step-form").hidden = true;
      $("step-otp").hidden = false;
      $("otp-err").textContent = e.message;
      $("otp-err").hidden = false;
    } else fail(e.message);
  } finally {
    done();
    gate();
  }
});

/* -- verification ---------------------------------------------------------- */
$("verify").addEventListener("click", async () => {
  const code = $("otp").value.trim();
  $("otp-err").hidden = true;
  if (!/^\d{6}$/.test(code)) {
    $("otp-err").textContent = "Enter the 6-digit code from the email.";
    $("otp-err").hidden = false;
    return;
  }
  const done = withSpinner($("verify"), "Verifying");
  try {
    await api("/v1/auth/verify-email", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: pendingEmail, code }),
    });
    toast("Account created");
    setTimeout(() => (location.href = "/app/new.html"), 350);
  } catch (e) {
    $("otp-err").textContent = e.message;
    $("otp-err").hidden = false;
  } finally {
    done();
  }
});

$("resend").addEventListener("click", async () => {
  try {
    const r = await api("/v1/auth/resend-code", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: pendingEmail }),
    });
    toast(r.delivery === "smtp" ? "New code sent" : "New code written to the server log");
  } catch (e) {
    $("otp-err").textContent = e.message;
    $("otp-err").hidden = false;
  }
});

$("back").addEventListener("click", () => {
  $("step-otp").hidden = true;
  $("step-form").hidden = false;
});

/* ── Google sign-in ────────────────────────────────────────────────────────
   Google's button hands us an ID token; the server verifies its signature,
   audience, issuer, and expiry before trusting anything in it. The client
   secret plays no part in this flow and is never sent to the browser.

   Consent: creating an account through Google needs the same agreement as the
   email path, and the server enforces it too — a client that skips the box is
   rejected, not quietly allowed. */
let googleReady = false;
let googleClientId = "";

async function initGoogle() {
  let cfg;
  try { cfg = await api("/v1/config"); } catch (_) { return disableGoogle(); }
  if (!cfg.google_enabled) return disableGoogle();

  const start = () => {
    if (!window.google?.accounts?.id) return false;
    googleClientId = cfg.google_client_id;
    google.accounts.id.initialize({
      client_id: cfg.google_client_id,
      callback: onGoogleCredential,
      // No auto-prompt: signing someone in before they have agreed to the
      // terms would make the consent gate meaningless.
      auto_select: false,
      cancel_on_tap_outside: true,
      itp_support: true,
      // Browsers have been withdrawing the third-party cookie access the old
      // flow depended on. Without these, the account chooser can decline to
      // appear and the callback simply never fires — a button that looks broken.
      use_fedcm_for_prompt: true,
      use_fedcm_for_button: true,
      // Without this, a browser that declines to hand over an account fails
      // inside Google's own code and the page shows nothing at all — which is
      // exactly what "I click it and nothing happens" looks like.
      error_callback: (err) => {
        const why = (err && (err.type || err.message)) || "unknown";
        fail("Google couldn't complete sign-in in this browser ("
           + why + "). Use the redirect method below, or email and password.");
        $("gsi-retry").hidden = false;
      },
    });
    google.accounts.id.renderButton($("gsi-host"), {
      theme: effectiveThemeIsDark() ? "filled_black" : "outline",
      size: "large", text: "continue_with", shape: "rectangular",
      width: Math.min(340, $("gsi-host").clientWidth || 340),
      logo_alignment: "left",
    });
    googleReady = true;
    return true;
  };

  // The GIS script is async; poll briefly rather than assuming it has landed.
  if (!start()) {
    let tries = 0;
    const t = setInterval(() => {
      if (start() || ++tries > 40) clearInterval(t);
      if (tries > 40) disableGoogle("Google sign-in didn't load. Use email and password.");
    }, 150);
  }
}

$("gsi-retry")?.addEventListener("click", googleFallbackPrompt);

function effectiveThemeIsDark() {
  return document.documentElement.classList.contains("pb-dark");
}

function disableGoogle(msg) {
  $("gsi-host").hidden = true;
  const b = $("google");
  b.hidden = false;
  b.addEventListener("click", () => fail(
    msg || "Google sign-in isn't set up on this server. Use email and password."));
}

/* A second way in that cannot fail silently. The rendered button opens a
   pop-up; if that is blocked, or the browser declines to hand over the account,
   there is nothing to report and nothing happens. This re-renders the button in
   Google's redirect mode: a full navigation to Google and back to the server,
   which posts the token as a form. Consent travels in `state` so the server can
   still refuse to create an account without it. */
function googleFallbackPrompt() {
  if (!googleReady) return fail("Google sign-in hasn't loaded yet. Give it a moment.");
  google.accounts.id.initialize({
    client_id: googleClientId,
    login_uri: location.origin + "/v1/auth/google/redirect",
    ux_mode: "redirect",
    state: $("accept").checked ? "accept-terms" : "no-terms",
    auto_select: false,
  });
  const host = $("gsi-host");
  host.innerHTML = "";
  host.hidden = false;
  google.accounts.id.renderButton(host, {
    theme: effectiveThemeIsDark() ? "filled_black" : "outline",
    size: "large", text: "continue_with", shape: "rectangular",
    width: Math.min(340, host.clientWidth || 340),
    logo_alignment: "left",
  });
  $("gsi-retry").hidden = true;
  fail("Use the Google button again — this time it takes you to Google and "
     + "brings you straight back.");
}

/* The server sends people back here with a reason when the redirect path can't
   finish, so a failure is never a blank page. */
(function reportRedirectOutcome() {
  const why = new URLSearchParams(location.search).get("google");
  if (!why) return;
  const said = {
    terms: "Accept the terms and privacy policy, then continue with Google "
         + "again — this creates a new account.",
    failed: "Google couldn't confirm that account. Try again, or use email and "
          + "password.",
    csrf: "That sign-in couldn't be verified as coming from you. Try again.",
  }[why];
  if (!said) return;
  if (why === "terms") switchToSignUp(said); else fail(said);
  history.replaceState(null, "", location.pathname);
})();

$("gsi-retry")?.addEventListener("click", googleFallbackPrompt);

function effectiveThemeIsDark() {
  return document.documentElement.classList.contains("pb-dark");
}

function disableGoogle(msg) {
  $("gsi-host").hidden = true;
  const b = $("google");
  b.hidden = false;
  b.addEventListener("click", () => fail(
    msg || "Google sign-in isn't set up on this server. Use email and password."));
}

/* A second way in, and a reason when there isn't one. Google's rendered button
   fails silently — a blocked pop-up or a browser that won't hand over the
   account reads as "the button does nothing". prompt() reports why, so the
   person is told rather than left clicking. */
function googleFallbackPrompt() {
  if (!googleReady) return fail("Google sign-in hasn't loaded yet. Give it a moment.");
  google.accounts.id.prompt((n) => {
    if (!n) return;
    const why = (n.isNotDisplayed && n.isNotDisplayed() && n.getNotDisplayedReason())
      || (n.isSkippedMoment && n.isSkippedMoment() && n.getSkippedReason())
      || (n.isDismissedMoment && n.isDismissedMoment() && n.getDismissedReason());
    if (!why || why === "credential_returned") return;
    const said = {
      opt_out_or_no_session: "You're not signed in to Google in this browser. "
        + "Sign in to Google first, or use email and password.",
      suppressed_by_user: "Google sign-in was dismissed too many times in this "
        + "browser. Use email and password, or clear this site's data.",
      unregistered_origin: "This site isn't registered with the Google client. "
        + "Use email and password while that's fixed.",
      browser_not_supported: "This browser won't hand over a Google account. "
        + "Use email and password.",
      secure_http_required: "Google sign-in needs a secure connection.",
    }[why];
    fail(said || `Google sign-in couldn't continue (${why}). Use email and password.`);
  });
}

async function onGoogleCredential(response) {
  $("err").hidden = true;
  try {
    await api("/v1/auth/google", {
      method: "POST", headers: { "Content-Type": "application/json" },
      // The real state of the box, never an assumed true. Signing in with Google
      // creates the account when there isn't one yet, so consent has to be the
      // person's actual answer — the server is what decides, and it refuses with
      // 422 when the answer is no.
      body: JSON.stringify({
        id_token: response.credential,
        accept_terms: $("accept").checked,
      }),
    });
    // Confirm the session actually took before leaving this page. Navigating on
    // the strength of a 200 alone means a cookie that didn't stick looks like
    // "Google sign-in does nothing": the next page just bounces back here with
    // nothing said.
    await api("/v1/me");
    toast("Signed in");
    setTimeout(() => (location.href = "/app/new.html"), 350);
  } catch (e) {
    if (e.status === 422) {
      // A new account needs the terms accepted. Say so where the box is, and
      // put the person in the mode that shows it.
      switchToSignUp("Accept the terms and privacy policy, then continue with "
        + "Google again — this creates a new account.");
      $("accept").focus();
      $("accept").scrollIntoView({ block: "center", behavior: "smooth" });
    } else if (e.status === 401) {
      fail("Google couldn't confirm that account. Try again, or use email and password.");
    } else {
      fail(e.message || "Google sign-in didn't complete. Try again.");
    }
  }
}

initGoogle();
setMode("signin");
