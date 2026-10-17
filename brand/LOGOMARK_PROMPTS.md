# Logomark prompts — ARCHIVED

> **Status: the mark is final.** The prompts below are kept for reference (and
> in case you ever want to explore a different direction). The shipped mark is
> documented in `README.md` in this folder.
>
> The winning concept — an isometric carton with a front seam and a slot cut
> into the right face — is now vector art, not a raster. See
> `../tools/build_brand_assets.py` for the geometry.

The wordmark is settled; only the mark needed replacing. Copy a prompt below
into ChatGPT (GPT-4o image generation), generate, then send me your favourite
output as an image.

---

## How to use

1. Open ChatGPT, start a **new** chat (a clean context gives a much better
   result than a chat already full of code).
2. Paste **one** of the prompts below.
3. Ask for **4 separate variants** in one go, then send me your favourite.

---

## ⚠️ Read this before you accept an output

ChatGPT's image model is a raster painter, not a vector tool. Whatever comes
back will be beautiful at 512px+ and **unusable at 16px** — the Windows taskbar
size. So:

**What to do with a result:** pick the one you like for its *shape idea*, and
send me the image. I will rebuild it as exact vector geometry at the real grid
size, re-cut the `.ico` at every size, and keep the violet accent consistent.
You are choosing the design, not shipping the PNG.

**What to reject immediately:** any output that comes back as a mockup, a
business card, a 3D render, a logo *presentation sheet*, or anything containing
the word "Simpl'd". I only need the bare symbol.

---

## Prompt A — sealed carton (closest to current, refined)

```
Design a minimal flat vector app icon / logo mark for an industrial inventory
and dispatch desktop app called "Simpl'd".

Concept: a sealed cardboard carton, front-facing, with a single strip of packing
tape running down the middle. Clean, geometric, confident.

CRITICAL TECHNICAL REQUIREMENTS — this must be a pure flat vector mark:
- Absolutely flat. Solid fills only.
- NO gradients, NO shadows, NO 3D perspective, NO bevels, NO highlights.
- Maximum 2 colors: white, and violet #7C3AED.
- Rendered on a plain solid near-black rounded-square tile (#141318).
- The symbol must be a thick, solid silhouette that still reads clearly when
  scaled down to 16x16 pixels. No hairline strokes, no thin details, no gaps
  narrower than about 1/12 of the symbol width.
- Geometric construction, straight edges and consistent corner radii. Think
  Swiss/industrial signage, not clip-art.
- Centered with generous even padding on all four sides.
- Simplest possible reading: one box, one tape strip.

DO NOT include: the app name, any text, letters, words, mockups, business-card
layouts, gradients, shadows, 3D, texture, or background patterns.

Output: 4 clearly different variations of the same flat vector mark, each
presented on its own plain near-black rounded-square tile.
```

---

## Prompt B — the apostrophe (most brandable)

```
Design a minimal flat vector app icon / logo mark for an industrial inventory
and dispatch desktop app called "Simpl'd".

Concept: the brand's core idea is SIMPLICITY — a stylised apostrophe, the mark
that makes the word different. The apostrophe becomes a solid geometric form:
a clean circle with a single tapered tail sweeping down and to the left, like a
comma. Make it feel engineered and industrial, not like a text glyph.

CRITICAL TECHNICAL REQUIREMENTS — pure flat vector:
- Absolutely flat. Solid fills only.
- NO gradients, NO shadows, NO 3D, NO bevels, NO highlights.
- Maximum 2 colors: white and violet #7C3AED.
- On a plain solid near-black rounded-square tile (#141318).
- Must read clearly at 16x16 pixels. Thick, confident shapes only.
- Geometric and balanced, not hand-drawn or sketchy.
- One centred symbol, generous even padding.

DO NOT include: text, the app name, letters, words, mockups, business-card
layouts, gradients, shadows, 3D, or texture.

Output: 4 clearly different variations, each on its own plain near-black
rounded-square tile.
```

---

## Prompt C — box + motion (for dispatch energy)

```
Design a minimal flat vector app icon / logo mark for an industrial inventory
and dispatch desktop app called "Simpl'd".

Concept: a sealed carton with a small arrow sweeping away from it — goods going
out. One box, one arrow, nothing else. Reads as "stock in, shipped out".

CRITICAL TECHNICAL REQUIREMENTS — pure flat vector:
- Absolutely flat. Solid fills only.
- NO gradients, NO shadows, NO 3D, NO bevels, NO highlights.
- Maximum 2 colors: white and violet #7C3AED.
- On a plain solid near-black rounded-square tile (#141318).
- Must read clearly at 16x16 pixels. Thick shapes, no hairline strokes.
- Geometric construction, straight edges, consistent corner radii.
- One centred symbol, generous even padding.

DO NOT include: text, the app name, letters, words, mockups, business-card
layouts, gradients, shadows, 3D, or texture.

Output: 4 clearly different variations, each on its own plain near-black
rounded-square tile.
```

---

## Prompt D — scale weight (if you want the weighing angle)

```
Design a minimal flat vector app icon / logo mark for an industrial inventory
and dispatch desktop app called "Simpl'd", used on a factory floor.

Concept: an abstract precision weighing scale — a simple balance or platform
form reduced to its most basic geometric shapes. Industrial, calibrated,
reliable.

CRITICAL TECHNICAL REQUIREMENTS — pure flat vector:
- Absolutely flat. Solid fills only.
- NO gradients, NO shadows, NO 3D, NO bevels, NO highlights.
- Maximum 2 colors: white and violet #7C3AED.
- On a plain solid near-black rounded-square tile (#141318).
- Must read clearly at 16x16 pixels. Thick shapes only, no thin detail.
- Geometric, symmetrical, engineered. Not playful.
- One centred symbol, generous even padding.

DO NOT include: text, the app name, letters, words, mockups, business-card
layouts, gradients, shadows, 3D, or texture.

Output: 4 clearly different variations, each on its own plain near-black
rounded-square tile.
```

---

## Quick judging checklist

- Reads at **16px**? (squint at it)
- Flat, no gradient or shadow?
- Only white + violet?
- Would it look at home next to a clipboard and a delivery-truck icon?
- Distinct from the other 3 candidates?
- Not a mockup, no text?

Send the winner as an image and I'll vectorise it.