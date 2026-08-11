/* The app download, as a navbar destination. It reads the live release manifest,
   so the version and size it advertises are the file it actually links to, and
   it stays hidden when there is nothing published. */

const ANDROID_MARK = `<svg viewBox="0 0 24 24" width="15" height="15"
  aria-hidden="true" fill="currentColor" style="flex:none">
  <path d="M6 9h12v8.5A1.5 1.5 0 0 1 16.5 19h-9A1.5 1.5 0 0 1 6 17.5V9Z"/>
  <rect x="3" y="9" width="2.2" height="6.6" rx="1.1"/>
  <rect x="18.8" y="9" width="2.2" height="6.6" rx="1.1"/>
  <rect x="8.4" y="19.4" width="2.2" height="4.1" rx="1.1"/>
  <rect x="13.4" y="19.4" width="2.2" height="4.1" rx="1.1"/>
  <path d="M6.4 8a5.6 5.6 0 0 1 11.2 0H6.4Z"/>
  <path d="M8.1 2.2l1 1.7M15.9 2.2l-1 1.7" stroke="currentColor"
        stroke-width="1.1" stroke-linecap="round" fill="none"/>
</svg>`;

async function mountDownloadCta() {
  const links = document.querySelectorAll("#nav-download");
  if (!links.length) return;
  let m;
  try {
    m = await (await fetch("/v1/app/latest")).json();
  } catch (_) {
    return;                                // no manifest, no button
  }
  if (!m.available) return;

  links.forEach((el) => {
    el.href = m.url;
    el.setAttribute("download", "");
    el.title = `Android ${m.version_name} · ${m.size_mb} MB · needs `
             + `${m.min_android} or newer`;
    el.style.display = "inline-flex";
    el.style.alignItems = "center";
    el.style.gap = "7px";
    el.hidden = false;
    el.innerHTML = `${ANDROID_MARK}<span>Get the app</span>`;
  });
}

document.addEventListener("DOMContentLoaded", () => mountDownloadCta());
