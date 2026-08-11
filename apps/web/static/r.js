/* Resolution page logic. Self-contained on purpose: no shared bundle, no
   third-party code, nothing that could exfiltrate the fragment key.

   The key lives in location.hash and is never put into any URL, fetch body,
   header, or console line. */

(function () {
  const out = document.getElementById("out");
  const id = location.pathname.split("/r/")[1] || "";
  document.getElementById("path").textContent = "/r/" + id;

  const b64uDec = (s) => Uint8Array.from(
    atob(s.replaceAll("-", "+").replaceAll("_", "/") + "=".repeat((4 - s.length % 4) % 4)),
    (c) => c.charCodeAt(0));

  function render(kind, title, body, payload) {
    const cls = { live: "", revoked: "revoked", expiring: "expiring" }[kind] || "revoked";
    const tag = { live: "tag-live", revoked: "tag-revoked", expiring: "tag-expiring" }[kind] || "tag-revoked";
    const label = { live: "Live", revoked: "Revoked", expiring: "Expired" }[kind] || "Unavailable";
    out.innerHTML = "";
    const card = document.createElement("div");
    card.className = "result " + cls;
    const t = document.createElement("span");
    t.className = "tag " + tag; t.textContent = label;
    const h = document.createElement("h4");
    h.style.margin = "24px 0 12px"; h.textContent = title;
    const p = document.createElement("p");
    p.style.fontSize = "14px"; p.textContent = body;
    card.append(t, h, p);
    if (payload) {
      const box = document.createElement("div");
      box.className = "codebox";
      box.textContent = payload;               // textContent: never innerHTML
      const note = document.createElement("p");
      note.className = "muted";
      note.style.cssText = "font-size:13px;margin:12px 0 0";
      note.textContent = "Decrypted in your browser. The key came with the link and never reached the server.";
      card.append(box, note);
    }
    out.appendChild(card);
  }

  async function run() {
    let r;
    try {
      r = await fetch("/r/" + encodeURIComponent(id), { headers: { Accept: "application/json" } });
    } catch (_) {
      return render("revoked", "Couldn't reach the server",
                    "Check your connection and reload this page.");
    }
    if (r.status === 404)
      return render("revoked", "This code doesn't exist",
                    "The link may be mistyped. Ask the owner for a fresh one.");
    if (r.status === 429)
      return render("revoked", "Too many requests",
                    "This code is being requested too often right now. Wait a minute and reload.");
    if (r.status === 410) {
      const d = await r.json().catch(() => ({}));
      if (d.status === "EXPIRED")
        return render("expiring", "This code expired",
                      "It was set to stop working, and it did. Nothing was decrypted. " +
                      "Ask the owner for a fresh link if you still need it.");
      return render("revoked", "This code was revoked",
                    "The owner turned it off after sharing it. Nothing was decrypted — " +
                    "without their key the payload is unreadable, including to us. " +
                    "If you need access, ask them for a fresh link.");
    }
    if (!r.ok)
      return render("revoked", "Something went wrong",
                    "The server couldn't resolve this code. Try reloading.");

    const data = await r.json();
    const key = location.hash.slice(1);
    if (!key)
      return render("expiring", "This link is missing its key",
                    "The part after the # was dropped somewhere — some apps strip it. " +
                    "Open the original link, or ask the owner to send it again.");
    try {
      const cryptoKey = await crypto.subtle.importKey(
        "raw", b64uDec(key), "AES-GCM", false, ["decrypt"]);
      const pt = await crypto.subtle.decrypt(
        { name: "AES-GCM", iv: b64uDec(data.nonce) }, cryptoKey, b64uDec(data.ciphertext));
      render("live", "Scan verified", "", new TextDecoder().decode(pt));
    } catch (_) {
      render("revoked", "The key in this link doesn't fit this code",
             "Decryption failed. The link may be truncated or from a different code — " +
             "ask the owner to send it again.");
    }
  }

  run();
})();
