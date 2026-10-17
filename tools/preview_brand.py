#!/usr/bin/env python3
"""Contact sheet for eyeballing the generated brand assets at real sizes.

Renders the mark at taskbar sizes and the icon set at 24px on both the
sidebar background (#F4F4F5) and the checked-nav pill (#18181B), plus the
mark/wordmark lockup, into one PNG for review.

    QT_QPA_PLATFORM=offscreen python3 tools/preview_brand.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PIL import Image, ImageDraw  # noqa: E402

BRAND = os.path.join(ROOT, "factory", "ui", "assets", "brand")
OUT = os.path.join(ROOT, "brand", "preview.png")

SIDEBAR_BG = (244, 244, 245)
SIDEBAR_BG = (244, 244, 245)
NAV_ACTIVE_BG = (24, 24, 27)

NAMES = ["packaging", "stock", "dispatch", "jobs", "history", "summary",
         "devices", "users", "info", "signout", "scan", "scale", "printer",
         "refresh", "factory"]

PAD = 24
ROW_H = 68


MARK_STEMS = ["mark", "mark-inverse", "mark-tile", "mark-tile-accent"]


def sheet() -> Image.Image:
    # --- section 1: the mark, primary at real Windows sizes
    mark = Image.open(os.path.join(BRAND, "mark-512.png")).convert("RGBA")
    tiles = [(16, "16"), (24, "24"), (32, "32"), (48, "48"), (64, "64"),
             (128, "128")]
    w = PAD + sum(t[0] + 22 for t in tiles) + PAD
    cols = 5
    n_rows = (len(NAMES) + cols - 1) // cols
    h = PAD + 150 + 112 * len(MARK_STEMS) + PAD + ROW_H * n_rows + PAD

    out = Image.new("RGBA", (max(w, 800), h), (255, 255, 255, 255))
    d = ImageDraw.Draw(out)

    d.text((PAD, PAD), "MARK at real Windows sizes (primary: bare, transparent)",
           fill=(24, 24, 27))
    x = PAD
    for px, label in tiles:
        out.alpha_composite(mark.resize((px, px), Image.LANCZOS), (x, PAD + 26))
        d.text((x, PAD + 26 + px + 6), label, fill=(113, 113, 122))
        x += px + 22

    # --- section 1b: every variant, on a checker so transparency is obvious
    y = PAD + 150
    for stem in MARK_STEMS:
        d.text((PAD, y + 34), stem, fill=(113, 113, 122))
        im = Image.open(os.path.join(BRAND, f"{stem}-96.png")).convert("RGBA")
        tile = Image.new("RGBA", (96, 96), (255, 255, 255, 255))
        for yy in range(0, 96, 12):
            for xx in range(0, 96, 12):
                if (xx // 12 + yy // 12) % 2 == 0:
                    tile.paste((226, 226, 232, 255), (xx, yy, xx + 12, yy + 12))
        tile.alpha_composite(im)
        out.alpha_composite(tile, (PAD + 210, y))
        y += 112

    # --- section 2: icon set, 24px, on sidebar + on active pill
    y = PAD + 150 + 112 * len(MARK_STEMS) + PAD
    d.text((PAD, y), "ICON SET  24px  —  sidebar (#F4F4F5)  |  active nav (#18181B)",
           fill=(24, 24, 27))
    y += 22

    cols = 5
    cell = 112
    half = 50
    for i, name in enumerate(NAMES):
        r, c = divmod(i, cols)
        cx = PAD + c * cell
        cy = y + r * ROW_H

        d.text((cx, cy), name, fill=(24, 24, 27))
        sy = cy + 16
        # light surface
        d.rectangle([cx, sy, cx + half - 6, sy + 40], fill=SIDEBAR_BG)
        out.alpha_composite(
            Image.open(os.path.join(BRAND, f"{name}-24.png")).convert("RGBA"),
            (cx + (half - 6 - 24) // 2, sy + 8))
        # active pill
        d.rectangle([cx + half, sy, cx + cell - 16, sy + 40], fill=NAV_ACTIVE_BG)
        out.alpha_composite(
            Image.open(os.path.join(BRAND, f"{name}-on-24.png")).convert("RGBA"),
            (cx + half + (cell - 16 - half - 24) // 2, sy + 8))
    return out


if __name__ == "__main__":
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    img = sheet().convert("RGB")
    img.save(OUT)
    print("wrote", OUT, img.size)