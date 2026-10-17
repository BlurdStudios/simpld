# The Simpl'd mark

A carton: a seam down the front edge and a slot cut into the right face. Flat
fills only — no strokes, no gradients on the glyph — so every size renders as a
clean silhouette.

## Variants

**The primary is the bare glyph** — the carton itself is the logo, on a
transparent background. No tile behind it.

| file | use |
|------|-----|
| `svg/mark.svg` | **primary** — bare dark glyph (`#18181B`), transparent. App icon, sidebar + login lockup, splash, `.ico` |
| `svg/mark-inverse.svg` | bare white glyph, transparent — for placing the mark on a dark surface |
| `svg/mark-tile.svg` | fallback — dark rounded tile + white glyph, faint violet sheen |
| `svg/mark-tile-accent.svg` | fallback — violet tile + white glyph |

The tiled variants exist only for contexts that need a bounded shape (favicons,
social avatars, a dark taskbar). `python3 tools/preview_brand.py` shows all
four on a checker background.

### One trap worth naming

The glyph colour constants are deliberately explicit — `MARK_INK`,
`MARK_INK_REVERSE`, `INK_ON_TILE`. An earlier version had a single generic
`INK = "#FFFFFF"` (correct for "glyph *inside* a dark tile"), which the bare
variant then reused and rendered **white on the white login card**: the mark
silently vanished with no error anywhere. Don't merge those three back into
one name.

## Geometry — sourced, not derived

`MARK_SRC` in `tools/build_brand_assets.py` holds the **supplied artwork's two
paths, verbatim** (original 2048x1365 coordinates). `mark_svg()` parses them,
applies one uniform scale + translate onto the 512 canvas, and re-emits them.

An earlier version rebuilt the cube from formulas (`MARK_W`, `MARK_D`,
`MARK_GAP`, `MARK_NOTCH`) and was visibly wrong. The reason, measured from the
paths:

- The shape is **not a symmetric isometric cube**. Its two top edges have
  different slopes — **-0.549** on the left, **+0.589** on the right.
- Consequently the **left vertex sits lower than the right** (~510 vs ~454),
  which no symmetric cube can produce.
- The corner rounding is generous and asymmetric too.

Rebuilt from formulas, all of that flattens into a generic cube. Keeping the
source paths means a future artwork update is a straight paste with zero drift.

Measured source bbox: `x 341.5 → 962.0`, `y 331.1 → 1002.3` — **620.5 x 671.2,
aspect 0.9245** (taller than wide; my first reconstruction was square).

### Tunable

| constant | value | why |
|----------|-------|-----|
| `SLOT_SCALE` | 0.68 | shrinks the slot cut out of the right face. `1.0` = exactly as supplied; the original read as a heavy bite, especially at 16px. Scaling is uniform about the midpoint of the slot's two mouth points, which keeps both mouths ON the bottom edge — a non-uniform scale lifts them off it and leaves a notch |
| `ICO_VARIANT` | `"mark-tile"` | which variant becomes the Windows `.ico`. See below — this one is not cosmetic |
| `GLYPH_H_BARE` | 400 | bare mark height inside the 512 canvas; the remainder is padding |
| `GLYPH_H_TILED` | 372 | tiled fallbacks need padding inside the rounded square |

Aspect is preserved automatically; both fit a 512 canvas.

### The `.ico` deliberately uses the TILED mark, not the bare one

The in-app mark is the bare dark glyph (what you asked for, and it's right —
the app chrome is always light). The Windows icon is a different problem:

| background | bare glyph | tiled glyph |
|-----------|-----------|-------------|
| light titlebar / taskbar | clear | clear |
| **dark** titlebar / taskbar | **near-invisible** | **clear** |

A `#18181B` glyph on a transparent background drops to roughly zero contrast on
a dark Windows titlebar (Win11 dark mode) — measured at 16–32px on `#202024`.
The tiled variant survives both, so `ICO_VARIANT = "mark-tile"` is the default.

Set it to `"mark"` to ship the bare glyph everywhere instead — one line in
`tools/build_brand_assets.py`, then re-run the build.

Where the `.ico` ends up:

| target | source | wired in |
|--------|--------|----------|
| `Simpld.exe` icon | embedded by PyInstaller `icon=` | `build/windows/simpld.spec` |
| titlebar + taskbar + Alt-Tab | `QApplication.setWindowIcon` at runtime | `factory/ui/brand.py: apply_app_icon()` |
| installer `SetupIconFile` | the same `.ico` on disk | `build/windows/simpld.iss` |
| Start Menu / desktop shortcuts | inherited from the exe | `build/windows/simpld.iss` `[Icons]` |

### Wordmark lockup

```
[mark]  Simpl'd
        A Blur'd product
```

The parent line sits in the column beside the mark, so it reads as a caption on
the **wordmark** rather than as a separate block. There is no tagline — the app
name and the parent attribution are the whole identity.

Mark height is held near the wordmark's own visual height (not 2x it), with
generous padding inside the mark canvas:

| surface | wordmark | mark |
|---------|----------|------|
| login card | 30px | 46px |
| splash | 28px | 44px |
| sidebar | 21px | 26px |

### Small sizes

The carton, seam and slot all survive down to **16px**. The bare mark is
marginally denser there than the tiled one — if the taskbar ever feels muddy,
switching the `.ico` to `mark-tile` is a one-line change.

## Rebuilding

```
python3 tools/build_brand_assets.py   # MARK_SRC paths -> SVG + PNG ladder + 9-frame .ico
```

Writes the SVG masters, every raster size, and the 9-frame `.ico`
(16/20/24/32/40/48/64/128/256, each rasterised from vector at its own size).

> Pillow only writes a `.ico` size when it is `<=` the **base** image's
> dimensions. The base frame must stay the largest (256px) or every smaller
> frame is silently dropped and you ship a 1-frame icon.

## Colour

The glyph itself is monochrome. The brand accent (violet `#7C3AED`) lives in
the tile's sheen and in the **wordmark's apostrophe**, not in the mark — which
is what keeps the mark readable on both light and dark surfaces.

---

Earlier candidates and the prompts used to generate them are kept in
`LOGOMARK_PROMPTS.md` for reference.