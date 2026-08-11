# Identity — poster

One idea, almost no words. The restraint is the design: a poster that explains
the mechanism is a diagram, and a diagram is not a poster.

**Sizes** A2 (420×594 mm) for print at 300 dpi · 1080×1350 for Instagram
**Bleed** 3 mm on print, safe margin 18 mm
**Stock** uncoated matte. The photograph must look like a photograph, not a screen.

## Palette

| Role | Value | Where |
|---|---|---|
| Ground | `#FFFFFF` | everything |
| Ink | `#201E1D` | wordmark, the one line of copy |
| Accent | `#FF48B0` | exactly one mark, nowhere else |
| Live | `#00A95C` | the single dot after the wordmark |

No gradient. No shadow. No glass. No second accent.

## Type

- Wordmark **Identity** — monospace, bold, tight (`letter-spacing: -0.04em`)
- One line of copy — same monospace, regular, roughly a quarter of the wordmark's size
- The attribution line — same face, smaller again, in `#605D5D`

One typeface for the whole poster. Two sizes plus the small attribution.

## Layout (A2, coordinates from the top-left of the trim)

```
┌─────────────────────────────────────────┐
│                                         │  ← 90 mm of empty white. Not a mistake.
│                                         │
│      ███████████████████████████        │
│      ███████████████████████████        │  the photograph: 240 mm square,
│      ███████  the portrait  ████        │  centred horizontally, top at 90 mm.
│      ███████████████████████████        │  its own grain resolves into the
│      ███████████████████████████        │  module grid at the edges only —
│      ███████████████████████████        │  the face stays completely clear
│                                         │
│                                         │  ← 40 mm
│      Identity ▪                          │  wordmark, left-aligned to the
│                                         │  photograph's left edge. ▪ is the
│      the photo is the code               │  green dot, baseline-aligned.
│                                         │
│                                         │  ← 60 mm
│      identity.patienceai.in              │  in the accent pink. The only
│                                         │  pink on the poster.
│                                         │
│      A product of Patience AI            │  grey, small, plain text.
│                                         │  Not a logo. Not a link.
└─────────────────────────────────────────┘
```

Everything sits on one left margin — the wordmark, the line of copy, the URL and
the attribution all start at the photograph's left edge. Nothing is centred
except the photograph itself.

## The photograph

Use a real portrait with an obviously readable face, shot plainly against a
light ground. The fused code must span the whole image, with the module dots
visible in hair, clothing and background and nearly invisible across the face.

Generate it with the product, then check it: the printed poster should scan.
A poster for a scannable code that does not scan is the only genuinely
embarrassing outcome, so print a proof and test it before the run.

## Copy — the whole of it

```
Identity ▪
the photo is the code
identity.patienceai.in
A product of Patience AI
```

That is the complete text. No feature list, no bullet points, no QR-code
explainer, no tagline about revolutionising anything.

## Variants

- **Instagram 1080×1350** — same layout, photograph 820 px square, margins scaled
  to 6% of the width. Keep the empty top third.
- **Dark** — ground `#17161C`, ink `#ECE9F2`, same pink and green. Use only where
  it will be seen on a screen; on paper the white version is the one.

## What not to do

No phone mockup holding the poster. No scanning-beam graphic. No before/after
split. No stock photo of a person smiling at a phone. No "Scan me" instruction —
if the poster needs to tell you it is scannable, the image has failed.
