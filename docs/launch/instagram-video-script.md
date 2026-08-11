# Identity — 30-second Instagram video

For generation in Google Gemini (Veo) on the free tier. Written as separate
shot prompts because free-tier generation is capped at ~8 seconds per clip:
generate four, then join them in any free editor (CapCut, Canva, Photos).

**Format** 1080×1920 vertical · 30 fps · 4 shots · 28–32 s with the end card
**Palette** near-white `#FFFFFF`, ink `#201E1D`, one accent pink `#FF48B0`,
live green `#00A95C`. One accent per shot — never two.
**Type** a monospace face (Courier New or JetBrains Mono) for all on-screen text.
**Sound** no voice-over. One soft mechanical *click* on each cut, a low room tone
underneath. Silence for the last beat.
**Rule for every shot** the photo is never covered by the code. If a shot reads
as "QR pasted on a picture", it is wrong.

---

## Shot 1 — the reveal (0:00–0:07)

**Prompt for Gemini/Veo**
> Vertical 9:16, 30fps. Extreme close-up of a printed portrait photograph lying
> on a plain white paper surface, soft diffuse daylight from the top left, no
> hard shadows. The camera pushes in very slowly, 8 seconds, no cuts. As it
> pushes in, the photograph's own grain resolves into a faint grid of small
> square dots that follow the tones already in the picture — dark dots in dark
> hair, light dots in bright cloth. The face stays completely clear and
> recognisable. Minimalist, editorial, calm. No text, no logos, no QR code
> pasted on top. Shallow depth of field. Muted natural colour.

**Movement** slow dolly-in only. No handheld shake.
**Text overlay** none.

## Shot 2 — the scan (0:07–0:14)

**Prompt**
> Vertical 9:16. A hand holds a phone above the same printed portrait on a white
> desk. Over 8 seconds the phone lowers slightly and steadies. On the phone
> screen the portrait appears with a thin bright pink rectangle snapping to its
> edges, then a small green dot appearing at the corner. Clean product-video
> lighting, white surface, no clutter. The printed photo remains clearly a
> portrait, not a barcode. No text on screen. Realistic, understated.

**Movement** the hand settles; the pink frame snaps in one step, not a pulse.
**Text overlay** (add in the editor, appears at 0:10, monospace, 34 px, ink)
`Any camera reads it.`

## Shot 3 — switching it off (0:14–0:22)

**Prompt**
> Vertical 9:16. Macro shot of a thumb pressing a single flat pink rectangular
> button on a white phone screen. 8 seconds. After the press the pink drains out
> of the button and it becomes a thin grey outline. No other motion. Very clean,
> very minimal, white background, soft even light. No text, no icons, no faces.

**Movement** one press. Then stillness — hold on the drained button for 2 s.
**Text overlay** (0:16) `Then take it back.`
**Sound** the click here is the only loud one.

## Shot 4 — one photo, many copies (0:22–0:28)

**Prompt**
> Vertical 9:16. Overhead flat-lay on white paper. One printed portrait
> photograph sits centre. Over 8 seconds, three identical smaller prints of the
> same portrait slide out from beneath it and fan into a neat row, each with a
> different small handwritten paper label beside it. Even overhead light, no
> shadows, no hands, no text on the prints. Stop-motion feel, precise, tidy.

**Movement** the three copies slide out in sequence, 0.3 s apart. They stop
square, not scattered.
**Text overlay** (0:24) `Every copy has its own history.`

## End card (0:28–0:31) — make in Canva, not Gemini

Pure white. Centred, monospace:

```
                Identity
        the photo is the code

           identity.patienceai.in
```

Then, on its own line and noticeably smaller, in grey:

```
        A product of Patience AI
```

The wordmark fades up over 0.4 s; the two lines under it follow 0.2 s later.
No motion after that — the last second is completely still.

---

## Caption

> Your picture, still your picture — and any phone camera reads it.
> Encrypted before it leaves your device. Switch any copy off after you've
> shared it. Every copy keeps its own scan history.
>
> Free to try, no account: identity.patienceai.in
>
> A product of Patience AI

**Do not write in the caption:** "world first", "revolutionary", "patented", or
anything about Google Lens or iPhone compatibility. Putting a picture inside a
scannable code has existed since 2013, and phone-camera testing here is not
finished. The interesting part is what happens after the scan, and that is true.

## If a clip comes back wrong

- *A QR code sitting on top of the photo* → add "the dot pattern must follow the
  photograph's own light and dark areas; no black-and-white square pasted on it".
- *The face is destroyed* → add "the face must stay sharp and untouched".
- *Too much colour* → add "monochrome except one pink element".
