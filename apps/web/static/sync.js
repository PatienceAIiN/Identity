/* Instant updates. One event stream per tab; the page re-reads what changed
   the moment the server says so, so nothing ever needs a manual refresh.

   Design rule: an event never carries state, only "this changed". The page
   refetches, which means a dropped or duplicated event can never leave the UI
   showing something the server does not agree with.
*/

const SYNC = {
  source: null,
  backoff: 1000,          // grows on failure, resets on a live connection
  handlers: new Map(),    // event name -> Set<fn>
  connected: false,
};

function onSync(event, fn) {
  if (!SYNC.handlers.has(event)) SYNC.handlers.set(event, new Set());
  SYNC.handlers.get(event).add(fn);
}

function emitLocal(event, data) {
  (SYNC.handlers.get(event) || []).forEach((fn) => {
    try { fn(data); } catch (e) { console.error("sync handler failed", event, e); }
  });
  (SYNC.handlers.get("*") || []).forEach((fn) => {
    try { fn({ ...data, event }); } catch (_) {}
  });
}

function connectSync() {
  if (SYNC.source) SYNC.source.close();
  // EventSource reconnects by itself, but only for network drops — an HTTP
  // error closes it for good, so failures are handled explicitly below.
  const es = new EventSource("/v1/events");
  SYNC.source = es;

  es.addEventListener("ready", () => {
    SYNC.connected = true;
    SYNC.backoff = 1000;
    document.documentElement.dataset.live = "1";
  });

  es.onmessage = (m) => {
    let payload;
    try { payload = JSON.parse(m.data); } catch { return; }
    const { event, ...data } = payload;
    emitLocal(event, data);
  };

  es.onerror = () => {
    SYNC.connected = false;
    delete document.documentElement.dataset.live;
    es.close();
    // Reconnect with backoff, capped, so a server restart heals on its own
    // without hammering it.
    setTimeout(connectSync, SYNC.backoff);
    SYNC.backoff = Math.min(SYNC.backoff * 2, 30000);
  };
}

/* A tab that was hidden for a while may have missed events; refetch on
   return rather than trusting a possibly-stale view. */
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  if (!SYNC.connected) connectSync();
  emitLocal("resync", { reason: "tab_visible" });
});
window.addEventListener("online", () => {
  if (!SYNC.connected) connectSync();
  emitLocal("resync", { reason: "back_online" });
});

/* ── default wiring, so pages get live behaviour without extra code ──────── */
document.addEventListener("DOMContentLoaded", () => {
  connectSync();

  // Codes list: any change to codes, or a scan, re-renders it.
  if (typeof load === "function" && document.getElementById("grid")) {
    const reload = () => load();
    onSync("codes.changed", reload);
    onSync("scan.recorded", reload);
    onSync("resync", reload);
  }

  // Download button follows a new release with no reload.
  if (document.getElementById("download-cta")) {
    onSync("release.published", () => mountDownloadCta("#download-cta"));
  }

  // Signing out anywhere ends the session everywhere: the other tabs leave
  // immediately instead of sitting on a page they no longer have access to.
  onSync("session.ended", () => {
    if (document.body.dataset.requiresAuth === "1") location.replace("/");
    else if (typeof refreshSession === "function") refreshSession();
  });

  // Name changes show up in any other open tab.
  onSync("profile.changed", () => {
    if (typeof refreshSession === "function") refreshSession();
  });
});
