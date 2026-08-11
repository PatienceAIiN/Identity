package `in`.photobind.app.ui

/**
 * Terms and privacy text, mirroring apps/web/static/legal.js word for word.
 * If one changes, change both — the modal and the website must never say
 * different things about the same product.
 */
object LegalText {
    const val UPDATED = "10 August 2026"
    const val FOOTER = "A product of Patience AI · patienceai.in"

    val TERMS: List<Pair<String, String>> = listOf(
        "What Identity does" to
            "Identity turns a photo you upload into a scannable code. The photo stays recognisable and any standard QR scanner can read the code. Each code resolves through our servers, which lets you revoke it after you have shared it and see a scan log for every share you create.",
        "Your account" to
            "You need an account to create codes. Keep your password to yourself — anyone who signs in as you can revoke your codes or create new ones. Tell us at once if you think someone else has your credentials.",
        "What you may upload" to
            "Upload photos you have the right to use. Do not upload images of other people without their agreement, and do not use Identity to impersonate anyone or to point a code at unlawful content. We can revoke codes that break these rules.",
        "Scanning is not proof of identity" to
            "A code that resolves tells you the code is live and who issued it. It does not verify that the person in the photo is present, consenting, or who they claim to be. Do not use Identity as the only check where identity actually matters.",
        "Photo verification, honestly stated" to
            "Identity can compare a photo against the code it was issued for. It reports an exact match, a consistent copy, a content change, or an inconclusive result. On our own test set it flagged 87.5–90.7% of deliberate edits with a false-positive rate under 1%. It does not catch everything: small local blurs, pixelation over a face, and edits smaller than roughly 3% of the image can pass undetected, and cropping breaks the match entirely. Treat the result as evidence, not as a verdict.",
        "Availability" to
            "Codes resolve through our servers. If our service is down or discontinued, existing codes stop resolving. This is the trade for being able to revoke a code after you have shared it.",
        "Ending your account" to
            "You can delete your account at any time from your profile. Deleting it revokes every code you made — scans stop working immediately — and removes your photos from our storage.",
        "Liability" to
            "Identity is provided as is. We are not liable for losses arising from a code being scanned, revoked, expired, unavailable, or from a verification result being relied on as proof. Nothing here limits liability that cannot be limited by law.",
        "Changes" to
            "If we change these terms we will update the date at the top and, for material changes, tell you in the app before they take effect.",
    )

    val PRIVACY: List<Pair<String, String>> = listOf(
        "We cannot read your payload" to
            "The content behind your code is encrypted in your browser or on your phone with AES-256-GCM before it reaches us. The key lives in the part of the link after the # symbol, which browsers never send to servers. We store the encrypted bytes and never hold the key, so we cannot read what you bound — that is an architectural property, not a promise about our conduct. One documented exception: when you ask our servers to draw the code into the photo, the link (including its key) passes through server memory during that step. It is never written to storage and never written to logs.",
        "We never store face data" to
            "Creating a code runs face detection so the code can be placed away from your face. That detection happens in memory and is discarded when the image is finished. We do not store, log, or transmit face embeddings, landmarks, or any biometric template — nowhere in our database, caches, or files. This design reduces biometric-data exposure; it is not by itself a determination of compliance with GDPR, India's DPDP Act, BIPA, or any other law, which requires independent legal review.",
        "What we do store" to
            "Your email address and name; the photos you upload with codes drawn into them; the encrypted payload and its nonce; the signed record that binds a code to its photo; and, for each scan, a timestamp, an approximate country, and a one-way hash of the browser's user-agent string. We do not store the IP address of people who scan your codes.",
        "Scan logs" to
            "Every share you create has its own scan log so you can tell which shared copy is being used. Those logs are visible to you as the code's owner.",
        "Who else sees your data" to
            "We do not sell your data and we do not share it with advertisers. We use service providers for hosting and storage; they process data on our instructions.",
        "How long we keep it" to
            "Codes and their photos stay until you delete them or delete your account. Deleting your account revokes every code and removes your photos.",
        "Your choices" to
            "You can revoke any share at any time, see its scan log, and delete your account entirely from your profile. For questions about your data, contact us at patienceai.in.",
        "Cookies" to
            "We use one cookie to keep you signed in. It is httpOnly, so page scripts cannot read it. We do not use advertising or analytics cookies.",
    )
}
