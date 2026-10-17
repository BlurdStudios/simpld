#!/usr/bin/env python3
"""Build Simpl'd brand assets: vector masters -> PNG + Windows .ico.

Why this script exists
----------------------
AI image generation is the wrong tool for UI iconography: it produces soft,
photographic shapes that turn to mush at 16px and cannot emit a multi-frame
Windows .ico. Instead the mark + icon set are authored as vector paths here,
written out as real .svg masters (editable in Figma/Inkscape/Illustrator) and
rasterised deterministically with Qt at every size the app actually uses.

Run (offline, no new dependencies — PySide6.QtSvg + Pillow are already
requirements):

    python3 tools/build_brand_assets.py

Outputs
    brand/svg/*.svg                     vector masters (source of truth)
    factory/ui/assets/brand/mark.png    512px mark on transparent (app icon)
    factory/ui/assets/brand/*.png       in-app icon set, default + `-on`
    factory/ui/assets/simpld.ico        multi-size Windows icon (16..256)
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_DIR = os.path.join(ROOT, "brand", "svg")
ASSETS = os.path.join(ROOT, "factory", "ui", "assets")
BRAND_DIR = os.path.join(ASSETS, "brand")

# ---- brand tokens (mirrored in factory/ui/brand.py — keep in sync) ---------
ACCENT = "#7C3AED"          # violet-600  primary accent
ACCENT_DEEP = "#6D28D9"     # violet-700  hover
# The bare logomark ships DARK (it sits on white/light surfaces in-app). The
# tiled fallback needs the opposite. Two clearly-named constants — a single
# generic "INK" here is exactly what painted the mark white-on-white once.
MARK_INK = "#18181B"        # bare mark, light surfaces  (primary)
MARK_INK_REVERSE = "#FFFFFF"  # bare mark, dark surfaces
INK_ON_TILE = "#FFFFFF"     # glyph inside a dark tile
INK_SOFT = "#D4D3DC"        # icon default on light surfaces -> overridden below
TILE_TOP = "#33323D"
TILE_BOT = "#141318"

# In-app icon strokes. `default` sits on the #F4F4F5 sidebar, `on` is used
# when a nav row is checked (white text on the near-black pill).
ICON_DEFAULT = "#52525B"    # zinc-600
ICON_ON = "#FFFFFF"

STROKE = 1.7                # 24px grid

# ---- the mark ------------------------------------------------------------
# The supplied artwork, verbatim. Its two visible shapes are:
#   1. the carton body (top face + left face + front edge)
#   2. the right face, with the slot cut up from its bottom edge
#
# These are the ORIGINAL coordinates from the artwork, not a reconstruction.
# An earlier hand-rebuilt cube was noticeably wrong: the real shape is NOT a
# symmetric isometric cube — its two top edges have different slopes (-0.549
# and +0.589) and the left vertex sits lower than the right. Any rebuilt-from-
# -formulas cube loses that. Keeping the source paths means future artwork
# updates are a straight paste, with no chance of drift.
#
# `mark_svg()` parses these, applies a uniform scale+translate onto the 512
# brand canvas, and re-emits them, so the bezier control points (including the
# generous corner rounding, which is a big part of the character) survive
# untouched.
CARTON_SRC = (
    # carton body
    "M 667.562 331.219 C 668.393 331.181 669.225 331.149 670.057 331.122 "
     "C 689.632 330.502 709.234 346.138 725.937 356.048 L 791.282 394.451 "
     "L 855.622 432.288 C 863.716 437.052 884.715 448.708 890.749 454.348 "
     "C 890.422 460.477 865.718 472.407 860 475.591 L 813.278 501.549 "
     "L 661.667 585.889 C 641.056 597.261 617.756 608.699 598.893 622.452 "
     "C 590.288 628.726 592.841 660.756 592.846 671.796 L 592.864 736.005 "
     "C 592.881 813.708 594.247 895.631 592.627 973.028 "
     "C 586.768 970.136 579.291 965.468 573.514 962.088 L 569.785 959.954 "
     "L 426.176 875.725 L 380.214 848.971 C 372.572 844.521 350.855 832.765 "
     "347.026 826.303 C 339.18 813.063 341.48 781.391 341.518 765.938 "
     "L 341.652 689.665 L 341.524 586.99 C 341.501 572.426 339.294 524.268 "
     "345.913 513.292 C 348.935 508.327 353.082 504.142 358.019 501.075 "
     "C 370.52 493.206 390.513 482.983 404.032 475.464 L 497.613 423.371 "
     "L 611.277 360.226 C 621.856 354.386 657.953 333.547 667.562 331.219 z")

# The right face is split into three parts so the slot can be scaled on its own.
# PRE ends at the slot's RIGHT mouth, POST begins at its LEFT mouth.
RIGHT_FACE_PRE_SRC = (
    "M 922.524 500.215 C 935.636 499.201 953.723 505.14 958.43 517.097 "
     "C 964.214 531.788 962.037 565.563 962.024 581.423 L 961.958 673.639 "
     "C 961.942 698.487 964.78 810.263 959.392 827.04 "
     "C 957.621 832.556 954.201 836.244 949.737 839.776 "
     "C 936.953 849.892 920.215 857.381 905.959 865.318 "
     "C 880.822 879.313 855.776 893.948 830.072 906.873 ")

SLOT_SRC = (
    "C 828.9 868.344 830.663 827.551 830.051 788.806 "
     "C 829.879 777.926 831.035 745.972 828.77 737.345 "
     "C 820.224 728.822 708.987 664.423 697.402 660.411 "
     "C 693.16 661.03 694.679 660.34 691.452 662.823 "
     "C 687.098 672.212 688.938 748.305 688.959 763.25 L 688.96 982.383 ")

RIGHT_FACE_POST_SRC = (
    "C 681.484 986.63 655.792 1002.47 648.179 1002.33 "
     "C 641.967 996.74 644.382 926.188 644.396 914.564 L 644.39 776.113 "
     "L 644.362 698.148 C 644.369 687.826 643.759 663.249 645.905 654.064 "
     "C 656.654 644.041 699.784 622.46 714.152 614.494 L 824.808 552.88 "
     "C 837.147 546.023 914.081 502.282 922.524 500.215 z")

# How big the slot cut into the right face is, as a fraction of the original
# supplied geometry. 1.0 = exactly as delivered. The original read as a heavy
# bite at small sizes, so it ships smaller; 1.0 stays available in the source.
SLOT_SCALE = 0.68

# Which variant becomes the Windows .ico (taskbar, titlebar, shortcuts,
# installer). NOT the same as the in-app mark.
#
# The bare glyph is what the UI uses, but a dark glyph on a transparent
# background is essentially invisible on a DARK Windows titlebar or taskbar
# (Windows 11 dark mode). Measured: at 16-32px on #202024 the bare mark drops
# to near-zero contrast, while the tiled variant stays legible on both.
# Set to "mark" to ship the bare glyph anyway.
ICO_VARIANT = "mark-tile"

# Glyph height inside the 512 canvas. The bare mark fills more of the frame
# (no tile to frame it); the tiled fallbacks need padding so the glyph sits
# inside the rounded square.
GLYPH_H_BARE = 400.0
GLYPH_H_TILED = 372.0

_TOK = re.compile(r"[MLCZmlczHhVvSsQqTtAa]|-?\d*\.?\d+(?:e-?\d+)?")
_ARITY = {"M": 2, "L": 2, "C": 6, "Z": 0}


def _parse_path(d: str):
    """Minimal M/L/C/Z parser (absolute only — that is all the source uses)."""
    toks = _TOK.findall(d)
    cmds, i = [], 0
    while i < len(toks):
        t = toks[i]
        if re.match(r"[A-Za-z]", t):
            i += 1
            if t in "Zz":
                cmds.append(("Z", []))
                break
        n = _ARITY[t]
        cmds.append((t, [float(v) for v in toks[i:i + n]]))
        i += n
    return cmds


def _transform(cmds, s: float, dx: float, dy: float) -> str:
    out = []
    for c, v in cmds:
        if c == "Z":
            out.append("z")
            continue
        nums = []
        for x, y in zip(v[0::2], v[1::2]):
            nums.append(f"{x * s + dx:.2f}")
            nums.append(f"{y * s + dy:.2f}")
        out.append(c + " ".join(nums))
    return " ".join(out)


def _right_face(scale: float) -> list:
    """The right face, with the slot optionally shrunk.

    The slot's two "mouth" points sit on the bottom edge of the face. Scaling
    uniformly about the midpoint of those mouths keeps both mouths ON that edge
    — a non-uniform scale would lift them off it and leave a visible notch.
    """
    pre = _parse_path(RIGHT_FACE_PRE_SRC)
    slot = _parse_path(SLOT_SRC)
    post = _parse_path(RIGHT_FACE_POST_SRC)

    if scale <= 0 or abs(scale - 1.0) < 1e-9:
        return pre + slot + post

    # mouths: end of pre, and end of slot
    a = pre[-1][1][-2:]          # right mouth
    b = slot[-1][1][-2:]         # left mouth
    mx, my = (a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0

    shrunk = []
    for cmd, v in slot:
        if cmd in ("Z",):
            shrunk.append((cmd, v))
            continue
        nv = []
        for x, y in zip(v[0::2], v[1::2]):
            nv.append(mx + (x - mx) * scale)
            nv.append(my + (y - my) * scale)
        shrunk.append((cmd, nv))
    return pre + shrunk + post


def mark_paths(glyph_h: float, box: int = 512, slot_scale: float | None = None
               ) -> list[str]:
    """The source paths, uniformly scaled onto the brand canvas."""
    if slot_scale is None:
        slot_scale = SLOT_SCALE
    parsed = [_parse_path(CARTON_SRC), _right_face(slot_scale)]
    pts = []
    for cm in parsed:
        for c, v in cm:
            if c in ("M", "L"):
                pts.append((v[0], v[1]))
            elif c == "C":
                pts.append((v[4], v[5]))
    x0 = min(p[0] for p in pts); x1 = max(p[0] for p in pts)
    y0 = min(p[1] for p in pts); y1 = max(p[1] for p in pts)
    s = glyph_h / (y1 - y0)
    dx = (box - (x1 - x0) * s) / 2 - x0 * s
    dy = (box - glyph_h) / 2 - y0 * s
    return [_transform(cm, s, dx, dy) for cm in parsed]


def mark_svg(glyph_fill: str, tile_top=None, tile_bot=None,
             accent: str | None = None, glyph_h: float | None = None,
             slot_scale: float | None = None) -> str:
    """The logomark.

    No tile (`tile_top=None`) emits the BARE glyph on transparent — that is the
    primary. The carton itself is the logo; a tile behind it only adds a shape
    that wasn't asked for.
    """
    tiled = tile_top is not None and tile_bot is not None
    if glyph_h is None:
        glyph_h = GLYPH_H_TILED if tiled else GLYPH_H_BARE
    paths = mark_paths(glyph_h, slot_scale=slot_scale)

    defs, tile_rect = "", ""
    if tiled:
        defs = (f'<linearGradient id="tile" x1="0" y1="0" x2="0" y2="1">'
                f'<stop offset="0" stop-color="{tile_top}"/>'
                f'<stop offset="1" stop-color="{tile_bot}"/></linearGradient>')
        tile_rect = ('<rect x="26" y="26" width="460" height="460" rx="104" '
                     'fill="url(#tile)"/>')
        if accent:
            defs += (f'<radialGradient id="sh" cx="0.26" cy="0.14" r="0.8">'
                     f'<stop offset="0" stop-color="{accent}" stop-opacity="0.26"/>'
                     f'<stop offset="1" stop-color="{accent}" stop-opacity="0"/>'
                     f'</radialGradient>')
            tile_rect += ('<rect x="26" y="26" width="460" height="460" rx="104" '
                          'fill="url(#sh)"/>')

    body = "\n".join(f'  <path d="{d}" fill="{glyph_fill}"/>' for d in paths)
    head = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" '
            'width="512" height="512">\n' + (f"  <defs>{defs}</defs>\n" if tiled else ""))
    return f"{head}  {tile_rect}\n{body}\n</svg>\n"


# (filename stem, glyph colour, tile gradient or None, sheen accent)
MARK_VARIANTS = [
    # primary — bare dark glyph on transparent. In-app + the .ico.
    ("mark", MARK_INK, None, None, None),
    # bare light glyph, for placing the mark on a dark surface
    ("mark-inverse", MARK_INK_REVERSE, None, None, None),
    # tiled fallbacks, for contexts that need a bounded shape
    ("mark-tile", INK_ON_TILE, TILE_TOP, TILE_BOT, ACCENT),
    ("mark-tile-accent", INK_ON_TILE, "#8B5CF6", "#6D28D9", None),
]


# ---- in-app icon set ------------------------------------------------------
# Icons are NOT defined here: they are vendored from HugeIcons (free, MIT,
# 6,000+ stroke-rounded glyphs, no attribution required) into
# brand/svg/icons/ by tools/import_hugeicons.py. That tool is the single place
# to swap or add a glyph; this script only rasterises what it finds.
#
# Vendored files keep `stroke="currentColor"`, so one file serves both colour
# variants: main() substitutes the concrete hex before rasterising.
ICON_SVGS = os.path.join(SVG_DIR, "icons")


def icon_svg_path(name: str) -> str:
    return os.path.join(ICON_SVGS, f"icon-{name}.svg")


def list_icons() -> list[str]:
    if not os.path.isdir(ICON_SVGS):
        return []
    return sorted(f[len("icon-"):-len(".svg")]
                  for f in os.listdir(ICON_SVGS)
                  if f.startswith("icon-") and f.endswith(".svg"))

def write_svg(path: str, svg: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(svg)


def render(svg_path: str, out_path: str, px: int) -> None:
    """Rasterise an SVG to a transparent RGBA PNG at exactly px x px.

    Rendering straight to the target size (rather than downsampling a large
    bitmap) keeps small icons sharp: Qt re-evaluates the vector geometry with
    proper anti-aliasing at whatever resolution we ask for.
    """
    from PySide6.QtCore import QSize, Qt
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])

    renderer = QSvgRenderer(svg_path)
    if not renderer.isValid():
        raise RuntimeError(f"invalid SVG: {svg_path}")

    img = QImage(QSize(px, px), QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.GlobalColor.transparent)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter)
    painter.end()
    del app

    if not img.save(out_path, "PNG"):
        raise RuntimeError(f"failed to write {out_path}")


def main() -> int:
    os.makedirs(SVG_DIR, exist_ok=True)
    os.makedirs(BRAND_DIR, exist_ok=True)

    # 1. mark masters — one SVG per tile variant, written next to the icons
    mark_svgs = {}
    for stem, glyph, t_top, t_bot, sheen in MARK_VARIANTS:
        p = os.path.join(SVG_DIR, f"{stem}.svg")
        write_svg(p, mark_svg(glyph, t_top, t_bot, sheen))
        mark_svgs[stem] = p
    primary_svg = mark_svgs[ICO_VARIANT]

    # 2. mark raster. A generous size ladder so every consumer (app icon,
    #    sidebar lockup, login lockup, splash, installer) gets an EXACT
    #    bitmap instead of Qt upscaling a nearest-neighbour match.
    for px in (16, 20, 24, 32, 40, 48, 56, 60, 64, 72, 80, 96, 128, 160,
               192, 256, 512):
        for stem, _p in mark_svgs.items():
            render(_p, os.path.join(BRAND_DIR, f"{stem}-{px}.png"), px)

    # 3. in-app icon set, default + `-on` (checked nav row) variants.
    #    Rendered at 24/32/48/64 so HiDPI (150%/200% Windows scaling) stays
    #    crisp instead of upscaling a 24px bitmap.
    names = list_icons()
    if not names:
        raise SystemExit(
            f"No icons in {ICON_SVGS}.\n"
            "Run: python3 tools/import_hugeicons.py --package <extracted pkg> --import")
    icon_sizes = (24, 32, 48, 64)
    written = []
    scratch = os.path.join(BRAND_DIR, "_tmp.svg")
    for name in names:
        with open(icon_svg_path(name), encoding="utf-8") as fh:
            base_svg = fh.read()
        for suffix, color in (("", ICON_DEFAULT), ("-on", ICON_ON)):
            # Vendored files carry stroke="currentColor"; swap in the real hex
            # so Qt's renderer (which has no CSS engine) can draw them.
            write_svg(scratch, base_svg.replace("currentColor", color))
            for px in icon_sizes:
                out = os.path.join(BRAND_DIR, f"{name}{suffix}-{px}.png")
                render(scratch, out, px)
                written.append(out)

    # 4. Windows .ico — true multi-frame, each frame rasterised from vector at
    #    its own size rather than downsampled from the 256px master.
    from PIL import Image
    ico_sizes = (16, 20, 24, 32, 40, 48, 64, 128, 256)
    frames = []
    for px in ico_sizes:
        png = os.path.join(BRAND_DIR, f"_ico-{px}.png")
        render(primary_svg, png, px)
        im = Image.open(png).convert("RGBA")
        os.remove(png)
        frames.append(im)

    ico_path = os.path.join(ASSETS, "simpld.ico")
    # Pillow only writes a size when it is <= the BASE image's dimensions, so
    # the base frame must be the largest (256) or every smaller frame is
    # silently dropped and you ship a 1-frame .ico.
    base, rest = frames[-1], frames[:-1]
    base.save(ico_path, format="ICO",
              sizes=[(px, px) for px in ico_sizes], append_images=rest)

    print(f"marks           -> {len(mark_svgs)} variants -> "
          f"{os.path.relpath(SVG_DIR, ROOT)}/{','.join(s + '.svg' for s in mark_svgs)}")
    print(f"simpld.ico      -> {os.path.relpath(ico_path, ROOT)} "
          f"({len(frames)} frames: {', '.join(str(s) for s in ico_sizes)})")
    print(f"icons           -> {len(names)} glyphs x 2 variants x "
          f"{len(icon_sizes)} sizes = {len(written)} PNGs in "
          f"{os.path.relpath(BRAND_DIR, ROOT)}")
    try:
        os.remove(scratch)
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())