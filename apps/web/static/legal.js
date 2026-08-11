/* Shared legal copy + footer. One source of truth for the web pages and the
   Android app's legal modal (mirrored in LegalText.kt).

   The text describes what this system actually does and what has and has not
   been measured. Nothing here asserts security or compliance the repository's
   tests do not demonstrate. */

const LEGAL = {
  updated: "10 August 2026",
  product: "Identity",
  company: "Patience AI",
  site: "patienceai.in",

  terms: [
    ["What Identity does",
     "Identity turns a photo you upload into a scannable code. The photo stays recognisable and any standard QR scanner can read the code. Each code resolves through our servers, which lets you revoke it after you have shared it and see a scan log for every share you create."],
    ["Your account",
     "You need an account to create codes. Keep your password to yourself — anyone who signs in as you can revoke your codes or create new ones. Tell us at once if you think someone else has your credentials."],
    ["What you may upload",
     "Upload photos you have the right to use. Do not upload images of other people without their agreement, and do not use Identity to impersonate anyone or to point a code at unlawful content. We can revoke codes that break these rules."],
    ["Scanning is not proof of identity",
     "A code that resolves tells you the code is live and who issued it. It does not verify that the person in the photo is present, consenting, or who they claim to be. Do not use Identity as the only check where identity actually matters."],
    ["Photo verification, honestly stated",
     "Identity can compare a photo against the code it was issued for. It reports an exact match, a consistent copy, a content change, or an inconclusive result. On our own test set it flagged 87.5–90.7% of deliberate edits with a false-positive rate under 1%. It does not catch everything: small local blurs, pixelation over a face, and edits smaller than roughly 3% of the image can pass undetected, and cropping breaks the match entirely. Treat the result as evidence, not as a verdict."],
    ["How we describe this product",
     "Putting a picture inside a scannable code is not new and is not ours \u2014 it has been done since 2013. We claim no world first for it, and we do not claim to have invented QR codes, perceptual hashing, or any of the other established techniques this service is built on. What is ours is what happens after the scan: an opaque code whose contents are encrypted before they leave your device, the ability to switch a copy off after you have shared it, a separate scan history per copy, and evidence about whether a photo is the one a code was issued for. Where we state a measurement \u2014 a decode rate, a detection rate \u2014 it comes from our own published tests and we say what those tests did not cover."],
    ["Availability",
     "Codes resolve through our servers. If our service is down or discontinued, existing codes stop resolving. This is the trade for being able to revoke a code after you have shared it."],
    ["Ending your account",
     "You can delete your account at any time from your profile. Deleting it revokes every code you made — scans stop working immediately — and removes your photos from our storage."],
    ["Liability",
     "Identity is provided as is. We are not liable for losses arising from a code being scanned, revoked, expired, unavailable, or from a verification result being relied on as proof. Nothing here limits liability that cannot be limited by law."],
    ["Changes",
     "If we change these terms we will update the date at the top and, for material changes, tell you in the app before they take effect."],
  ],

  privacy: [
    ["We cannot read your payload",
     "The content behind your code is encrypted in your browser or on your phone with AES-256-GCM before it reaches us. The key lives in the part of the link after the # symbol, which browsers never send to servers. We store the encrypted bytes and never hold the key, so we cannot read what you bound — that is an architectural property, not a promise about our conduct. One documented exception: when you ask our servers to draw the code into the photo, the link (including its key) passes through server memory during that step. It is never written to storage and never written to logs."],
    ["We never store face data",
     "Creating a code runs face detection so the code can be placed away from your face. That detection happens in memory and is discarded when the image is finished. We do not store, log, or transmit face embeddings, landmarks, or any biometric template — nowhere in our database, caches, or files. This design reduces biometric-data exposure; it is not by itself a determination of compliance with GDPR, India's DPDP Act, BIPA, or any other law, which requires independent legal review."],
    ["What we do store",
     "Your email address and name; the photos you upload with codes drawn into them; the encrypted payload and its nonce; the signed record that binds a code to its photo; and, for each scan, a timestamp, an approximate country, and a one-way hash of the browser's user-agent string. We do not store the IP address of people who scan your codes."],
    ["Scan logs",
     "Every share you create has its own scan log so you can tell which shared copy is being used. Those logs are visible to you as the code's owner."],
    ["Who else sees your data",
     "We do not sell your data and we do not share it with advertisers. We use service providers for hosting and storage; they process data on our instructions."],
    ["How long we keep it",
     "Codes and their photos stay until you delete them or delete your account. Deleting your account revokes every code and removes your photos."],
    ["Your choices",
     "You can revoke any share at any time, see its scan log, and delete your account entirely from your profile. For questions about your data, contact us at patienceai.in."],
    ["Cookies, in full",
     "Every cookie we set, and why. pb_session keeps you signed in. pb_trial counts your free trial codes so the limit means something. pb_dev signs you in to the developer console, and pb_admin to the operator panel \u2014 separate cookies on purpose, so one cannot act as another. pb_consent remembers the choice you made in the banner. All of them are first-party; the sign-in ones are httpOnly, so page scripts cannot read them. If you sign in with Google, Google briefly sets g_csrf_token to protect that exchange. None of these are advertising cookies, and we do not sell or share them with anyone."],
    ["The one thing that is your choice",
     "Our network provider, Cloudflare, can run a page-view measurement script. It sets no cookie, but it does send your IP address and the address of the page to Cloudflare. That is the only non-essential processing on this site, so it is the only thing the banner asks about \u2014 and refusing is enforced, not recorded: we send a content security policy that stops your browser loading the script at all. You can change your mind at any time from the Cookies link in the footer."],
    ["Your rights under the DPDP Act",
     "Under India's Digital Personal Data Protection Act 2023 you may ask what personal data we hold about you, ask us to correct or complete it, withdraw a consent you have given, and ask us to erase your data. Deleting your account from your profile does the last of these immediately and without needing to ask us: it removes your photos and your account, and every code you made reports that it has been switched off. Withdrawing measurement consent takes effect on your next page load. For anything else, or to nominate someone to act for you if you are unable to, contact us at patienceai.in. If you are not satisfied with how we handle a request, you may complain to the Data Protection Board of India."],
  ],
};

function legalHTML(kind) {
  const sections = LEGAL[kind];
  const title = kind === "terms" ? "Terms of use" : "Privacy policy";
  return `<h3 style="margin-bottom:6px">${title}</h3>
    <p class="mono muted" style="font-size:11px;margin-bottom:24px">${LEGAL.product} · a product of ${LEGAL.company} · updated ${LEGAL.updated}</p>
    ${sections.map(([h, b]) => `<h4 style="margin:24px 0 6px">${h}</h4><p style="font-size:14px;max-width:68ch">${b}</p>`).join("")}`;
}

/* The footer is built by chrome.js, which owns page chrome for every page. */
