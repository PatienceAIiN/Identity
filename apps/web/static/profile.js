/* Profile: name, password, legal modal, sign out, account deletion. */

const $ = (id) => document.getElementById(id);
let me = null;

/* password reveal on every .pw-eye in the page */
document.querySelectorAll(".pw-eye[data-eye]").forEach((btn) => {
  btn.addEventListener("click", () => {
    const f = $(btn.dataset.eye);
    const show = f.type === "password";
    f.type = show ? "text" : "password";
    btn.setAttribute("aria-pressed", String(show));
    btn.setAttribute("aria-label", show ? "Hide password" : "Show password");
    f.focus();
  });
});

(async function load() {
  me = await requireAuth();
  if (!me) return;
  $("name").value = me.name || "";
  $("email").value = me.email;
  const since = new Date(me.member_since).toLocaleDateString(undefined,
    { year: "numeric", month: "long", day: "numeric" });
  $("meta").textContent = `${me.email} · signed in with ${me.auth} · member since ${since}`;
  $("consent").textContent = me.terms_version
    ? `you accepted the terms and privacy policy dated ${me.terms_version}`
    : "";
  // A Google account has no password to change here.
  if (me.auth === "google") {
    $("pw-section").innerHTML =
      '<p class="kicker">password</p>' +
      '<p style="font-size:14px;max-width:52ch">This account signs in with Google, ' +
      'so there is no password here to change. Manage it in your Google account.</p>';
  }
})();

$("save").addEventListener("click", async () => {
  $("saved").textContent = "";
  const done = withSpinner($("save"), "Saving");
  try {
    const r = await api("/v1/me", {
      method: "PATCH", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: $("name").value.trim() }),
    });
    me = { ...me, ...r };
    $("saved").textContent = "Saved";
    toast("Saved");
    setTimeout(() => ($("saved").textContent = ""), 2500);
  } catch (e) {
    $("saved").textContent = e.message;
  } finally { done(); }
});

$("change").addEventListener("click", async () => {
  $("pw-err").hidden = true;
  $("changed").textContent = "";
  const current_password = $("cur").value, new_password = $("new").value;
  if (new_password.length < 12) {
    $("pw-err").textContent = "New passwords need at least 12 characters.";
    $("pw-err").hidden = false;
    return;
  }
  const done = withSpinner($("change"), "Changing");
  try {
    await api("/v1/auth/password", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_password, new_password }),
    });
    $("cur").value = $("new").value = "";
    $("changed").textContent = "Changed";
    toast("Password changed");
    setTimeout(() => ($("changed").textContent = ""), 2500);
  } catch (e) {
    $("pw-err").textContent = e.message;
    $("pw-err").hidden = false;
  } finally { done(); }
});

/* legal modal — same text as the website pages and the Android app */
document.querySelectorAll("[data-legal]").forEach((b) =>
  b.addEventListener("click", () => {
    $("modal-body").innerHTML = legalHTML(b.dataset.legal);
    $("modal").hidden = false;
  }));
$("modal-close").addEventListener("click", () => ($("modal").hidden = true));
$("modal").addEventListener("click", (e) => {
  if (e.target === $("modal")) $("modal").hidden = true;
});

$("signout").addEventListener("click", async () => {
  const ok = await confirmAction({
    title: "Sign out?",
    body: "Your codes keep working — they resolve whether or not you're signed in. "
        + "You'll need to sign in again to manage them.",
    confirmLabel: "Sign out",
  });
  if (!ok) return;
  withSpinner($("signout"), "Signing out");
  await signOutAndLeave();
});

/* deletion needs the exact word typed — no accidental clicks */
$("del").addEventListener("click", async () => {
  const ok = await confirmAction({
    title: "Delete your account?",
    body: "Every code you made will be revoked and stop resolving. Your photos "
        + "are removed. This cannot be undone.",
    confirmLabel: "Delete account", destructive: true, requireText: "DELETE",
  });
  if (!ok) return;
  const done = withSpinner($("del"), "Deleting");
  try {
    await api("/v1/me", {
      method: "DELETE", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm: "DELETE" }),
    });
    location.replace("/");
  } catch (e) {
    done();
    toast(e.message);
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") $("modal").hidden = true;
});

/* Feedback entry points (profile only — signed-in people). */
document.querySelectorAll("[data-feedback]").forEach((b) =>
  b.addEventListener("click", () => openFeedback({ kind: b.dataset.feedback })));

/* ── the card page a code can point at ─────────────────────────────────────
   Loaded and saved on its own, separate from the account fields, because it is
   the one part of the profile that is deliberately public. */
const CARD_FIELDS = ["display_name", "headline", "email", "phone", "website"];
const cardEl = (f) => $("card-" + f.replace("display_name", "name"));

async function loadCard() {
  if (!$("card-save")) return;
  try {
    const card = await api("/v1/me/card");
    CARD_FIELDS.forEach((f) => { const el = cardEl(f); if (el) el.value = card[f] || ""; });
    $("card-view").href = card.url;
  } catch (_) { /* signed out; the guard handles that */ }
}

$("card-save")?.addEventListener("click", async () => {
  const done = withSpinner($("card-save"), "Saving");
  const body = {};
  CARD_FIELDS.forEach((f) => { body[f] = (cardEl(f)?.value || "").trim(); });
  try {
    const card = await api("/v1/me/card", {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    $("card-view").href = card.url;
    $("card-saved").textContent = "Saved";
    setTimeout(() => ($("card-saved").textContent = ""), 2000);
  } catch (e) {
    toast(e.message);
  } finally { done(); }
});

loadCard();
