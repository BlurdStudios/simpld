#!/usr/bin/env python3
"""Import HugeIcons (free, MIT) into brand/svg/icons/ and/or preview candidates.

Why HugeIcons
-------------
The bespoke set inlined in build_brand_assets.py is fine, but HugeIcons free is
MIT-licensed, has 6,000+ stroke-rounded glyphs on the same 24px grid with
stroke-width 1.5 / round caps / round joins, and needs no attribution — so it
drops straight into this app's existing renderer with zero style drift. It also
has proper glyphs for the three real devices (WeightScaleIcon, BarcodeScanIcon,
PrinterIcon) instead of hand-rolled approximations.

Vendoring, not depending
------------------------
The extracted SVGs are committed to brand/svg/icons/, so the app build and the
factory PC never need npm or the network. This script only runs when you want
to change or add an icon.

Usage
-----
    # 1. get the package once (needs internet, dev machine only)
    npm pack @hugeicons/core-free-icons@4.3.5
    tar xzf hugeicons-core-free-icons-4.3.5.tgz

    # 2. see the candidates for every role
    python3 tools/import_hugeicons.py --package package --preview

    # 3. lock in the winners (edit CHOSEN below first), vendor the SVGs
    python3 tools/import_hugeicons.py --package package --import

After --import, run `python3 tools/build_brand_assets.py` to rasterise.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

ICON_DIR = os.path.join(ROOT, "brand", "svg", "icons")
PKG_VERSION = "4.3.5"

# role -> candidates to eyeball. First entry is the current preference.
CANDIDATES: dict[str, list[str]] = {
    "packaging": ["PackageIcon", "BoxIcon", "DeliveryBox01Icon", "BoxesIcon"],
    "reel": ["FilmRoll02Icon", "FilmRoll01Icon", "Film01Icon", "Cylinder01Icon"],
    "box": ["Package03Icon", "Package01Icon", "BoxIcon", "DeliveryBox01Icon"],
    "stock": ["BoxesIcon", "WarehouseIcon", "BoxIcon", "PackageIcon"],
    "dispatch": ["DeliveryTruck01Icon", "TruckIcon", "TruckDeliveryIcon",
                 "ShipmentTrackingIcon"],
    "jobs": ["ClipboardListIcon", "ClipboardIcon", "ClipboardCheckIcon"],
    "history": ["Clock01Icon", "ClockFadingIcon", "CalendarAnalysisIcon"],
    "summary": ["ChartNoAxesColumnIncreasingIcon", "Analytics01Icon",
                "ChartIcon", "ChartColumnIncreasingIcon"],
    "devices": ["Settings01Icon", "Settings02Icon", "Settings03Icon"],
    "users": ["UserMultipleIcon", "UserGroupIcon", "UserMultiple02Icon"],
    "info": ["InformationCircleIcon"],
    "signout": ["LogOutIcon", "Logout01Icon", "LogoutSquare01Icon"],
    "scan": ["BarcodeScanIcon", "BarCode01Icon", "BarcodeIcon"],
    "refresh": ["RefreshIcon", "RefreshCwIcon", "ReloadIcon"],
    "scale": ["WeightScaleIcon", "WeightIcon", "ScaleIcon"],
    "printer": ["PrinterIcon", "PrinterCheckIcon"],
    "factory": ["FactoryIcon", "Factory01Icon"],
}

# The winners. Kept explicit so the build is reproducible and reviewable.
CHOSEN: dict[str, str] = {
    "packaging": "PackageIcon",
    "reel": "FilmRoll02Icon",
    "box": "Package03Icon",
    "stock": "BoxesIcon",
    "dispatch": "DeliveryTruck01Icon",
    "jobs": "ClipboardListIcon",
    "history": "Clock01Icon",
    "summary": "ChartNoAxesColumnIncreasingIcon",
    "devices": "Settings01Icon",
    "users": "UserMultipleIcon",
    "info": "InformationCircleIcon",
    "signout": "LogOutIcon",
    "scan": "BarcodeScanIcon",
    "refresh": "RefreshIcon",
    "scale": "WeightScaleIcon",
    "printer": "PrinterIcon",
    "factory": "FactoryIcon",
}

# attr order we emit, so two runs produce byte-identical files
_ATTR_ORDER = ("d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2",
               "points", "fill", "fillRule", "opacity", "stroke", "strokeWidth",
               "strokeLinecap", "strokeLinejoin", "strokeDasharray")

_ELEM_RE = re.compile(r'\["(\w+)"\s*,\s*\{(.*?)\}\]', re.S)
_STR_RE = re.compile(r'(\w+)\s*:\s*"([^"]*)"')
_NUM_RE = re.compile(r'(\w+)\s*:\s*(-?[\d.]+(?:e-?\d+)?)\s*(?=[,}])', re.I)

# JS camelCase attr -> SVG kebab-case attr
_RENAME = {"strokeWidth": "stroke-width",
           "strokeLinecap": "stroke-linecap",
           "strokeLinejoin": "stroke-linejoin",
           "strokeDasharray": "stroke-dasharray",
           "fillRule": "fill-rule"}


def parse_icon(js_path: str) -> list[tuple[str, dict]]:
    """Parse one HugeIcons ESM file into [(tag, attrs), ...].

    The files are plain data arrays like
        const PrinterIcon = [["path", { d: "...", stroke: "currentColor", ... }]];
    so a regex parse is enough and avoids pulling in a JS runtime.
    """
    with open(js_path, encoding="utf-8") as fh:
        src = fh.read()
    out: list[tuple[str, dict]] = []
    for tag, blob in _ELEM_RE.findall(src):
        attrs: dict[str, str] = {}
        for k, v in _STR_RE.findall(blob):
            attrs[k] = v
        for k, v in _NUM_RE.findall(blob):
            attrs.setdefault(k, v)
        attrs.pop("key", None)
        if not attrs:
            continue
        out.append((tag, attrs))
    if not out:
        raise ValueError(f"parsed no elements from {js_path}")
    return out


def to_svg(elements: list[tuple[str, dict]]) -> str:
    """Render parsed elements into a standalone 24x24 stroked SVG.

    `stroke="currentColor"` is preserved verbatim: build_brand_assets.py
    substitutes the concrete hex per colour variant, so one vendored file
    serves both the sidebar and the active-pill renders.
    """
    parts = []
    for tag, attrs in elements:
        a = dict(attrs)
        a.setdefault("stroke", "currentColor")
        keys = [k for k in _ATTR_ORDER if k in a]
        keys += [k for k in a if k not in keys]
        bits = [f'{_RENAME.get(k, k)}="{a[k]}"' for k in keys]
        parts.append(f"  <{tag} {' '.join(bits)}/>")
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'width="24" height="24" fill="none">\n'
        + "\n".join(parts) + "\n</svg>\n"
    )


def find_pkg(pkg_dir: str, name: str) -> str:
    for sub in ("dist/esm", "esm", "dist/cjs", "cjs"):
        p = os.path.join(pkg_dir, sub, f"{name}.js")
        if os.path.isfile(p):
            return p
    raise FileNotFoundError(f"{name}.js not found under {pkg_dir}")


def do_import(pkg_dir: str) -> int:
    os.makedirs(ICON_DIR, exist_ok=True)
    for role, name in CHOSEN.items():
        js = find_pkg(pkg_dir, name)
        svg = to_svg(parse_icon(js))
        out = os.path.join(ICON_DIR, f"icon-{role}.svg")
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(svg)
        print(f"  {role:<10} <- {name}")
    lic = os.path.join(ICON_DIR, "LICENSE.txt")
    with open(lic, "w", encoding="utf-8") as fh:
        fh.write(
            f"Icons from HugeIcons (free set) v{PKG_VERSION}\n"
            "https://hugeicons.com  —  MIT License, no attribution required.\n\n"
            "MIT License\n\n"
            "Copyright (c) Hugeicons\n\n"
            'Permission is hereby granted, free of charge, to any person obtaining a\n'
            'copy of this software and associated documentation files (the "Software"),\n'
            "to deal in the Software without restriction, including without limitation\n"
            "the rights to use, copy, modify, merge, publish, distribute, sublicense,\n"
            'and/or sell copies of the Software, subject to the conditions that the\n'
            "Software is furnished to do so, subject to the following conditions:\n\n"
            'The above copyright notice and this permission notice shall be included in\n'
            "all copies or substantial portions of the Software.\n\n"
            'THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR\n'
            "IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,\n"
            "FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.\n")
    print(f"\nwrote {len(CHOSEN)} icons + LICENSE.txt -> "
          f"{os.path.relpath(ICON_DIR, ROOT)}")
    return 0


def do_preview(pkg_dir: str) -> int:
    from PIL import Image, ImageDraw
    from tools.build_brand_assets import render, ICON_DEFAULT

    tmp = "/tmp/opencode/hugeicons-preview"
    os.makedirs(tmp, exist_ok=True)
    PAD = 20
    ROW = 92
    ICON = 64
    W = 200 + max(len(c) for c in CANDIDATES.values()) * 230 + PAD
    H = PAD + len(CANDIDATES) * ROW + PAD

    sheet = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    d = ImageDraw.Draw(sheet)
    y = PAD
    for role, cands in CANDIDATES.items():
        d.text((PAD, y + ICON // 2 - 6), role, fill=(24, 24, 27))
        x = PAD + 170
        for name in cands:
            try:
                svg = to_svg(parse_icon(find_pkg(pkg_dir, name)))
            except Exception as e:
                d.text((x, y + 26), f"{name}: {e}", fill=(200, 0, 0))
                x += 230
                continue
            p = os.path.join(tmp, f"{role}-{name}.svg")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write(svg.replace("currentColor", ICON_DEFAULT))
            png = p[:-4] + ".png"
            render(p, png, ICON)
            sheet.alpha_composite(Image.open(png).convert("RGBA"), (x, y))
            d.text((x, y + ICON + 6), name, fill=(90, 90, 100))
            x += 230
        y += ROW
    out = os.path.join(tmp, "candidates.png")
    sheet.convert("RGB").save(out)
    print("wrote", out)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--package", default="package",
                    help="extracted @hugeicons/core-free-icons directory")
    ap.add_argument("--import", dest="do_import", action="store_true")
    ap.add_argument("--preview", action="store_true")
    a = ap.parse_args()
    if not (a.do_import or a.preview):
        ap.error("pass --import and/or --preview")
    if not os.path.isdir(a.package):
        ap.error(f"package dir not found: {a.package}")
    rc = 0
    if a.preview:
        rc |= do_preview(a.package)
    if a.do_import:
        rc |= do_import(a.package)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())