/* Changelog. Plain language on purpose: what a person notices, never how it
   works inside. No internals, no architecture, no vendor names. */

const CHANGELOG = [
  {
    version: "1.0",
    date: "10 August 2026",
    title: "Identity is live",
    lines: [
      "Turn any photo into a code that any phone camera can read — the photo still looks like your photo.",
      "Use any picture you like. A face gets extra care so it stays recognisable; pictures without a face work too.",
      "Choose how much of the picture the code covers: the whole image, or a smaller code kept clear of a face.",
      "Every copy you share is its own thing, with its own link and its own scan history, so you can tell which copy is being used.",
      "Turn any copy off whenever you want — even after you've shared it. The next person to scan it is told it's been turned off, and nothing else.",
      "Check a picture later: we tell you whether it's the same picture the code was made for, a harmless re-save, or something that's been changed.",
      "Before we show you a new code, we check it actually scans, and we show you how confident we are as a number.",
      "Sign up with your email — we send a 6-digit code to make sure it's really you.",
      "Light and dark, and it follows your device unless you pick one.",
      "Send feedback or report a bug from your profile. If something crashes, we're told automatically so it can be fixed.",
      "Android app available, with updates that arrive on their own.",
      "In the app: a home screen that shows your codes at a glance, and a built-in scanner that recognises a code you made and tells you whether it's still on.",
      "In the app: a light/dark switch of your own, starting light, and terms and privacy that slide up where you are instead of taking you somewhere else.",
      "In the app: back takes you a step back through the app, and only asks before closing when you're already at the start.",
      "Save any code as a picture, straight to your phone's gallery.",
      "Tap a code to open it on its own, with its picture, where it has been scanned, and every control in one place.",
      "Less white edging around a code, so more of your picture shows. How much can come off is measured — past a point the code stops being readable, and readable comes first.",
      "Long lists come in pages now, on the website and in the app.",
      "Signing in stays signed in when the app updates itself.",
      "Don't have a link to point a code at? Use your own card page — a small page with your name and whatever details you choose to show. Empty fields aren't shown, and it's kept out of search results.",
      "Get the app from the top of any page.",
      "Try it without an account: five free codes, no sign-up. Trial codes aren't kept — your link sits inside the picture, so anyone who scans it reads it, and it can't be switched off later. An account is what makes a code private and switch-off-able.",
    ],
  },
];

document.addEventListener("DOMContentLoaded", () => {
  const host = document.getElementById("entries");
  host.innerHTML = CHANGELOG.map((e) => `
    <section style="margin-bottom:42px">
      <div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px">
        <h2 style="font-size:26px;margin:0">${e.title}</h2>
        <span class="mono muted" style="font-size:11px">${e.version} · ${e.date}</span>
      </div>
      <ul style="margin:0;padding-left:20px;display:grid;gap:9px">
        ${e.lines.map((l) => `<li style="font-size:15px">${l}</li>`).join("")}
      </ul>
    </section>`).join("");
});
