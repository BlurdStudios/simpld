"""Simpl'd brand system — tokens, icon loading and the wordmark lockup.

Everything visual about Simpl'd's identity lives here so the app chrome, the
login card and the installer all pull from one place.

Design notes
------------
* The wordmark is drawn with **native Qt text**, not a rasterised PNG. Qt
  picks the real system font at the real DPI, so "Simpl'd" stays crisp on a
  150%/200% scaled Windows display and picks up font-hinting for free. The
  accent-coloured apostrophe is rich text, tying the wordmark to the mark.
* The icons **are** rasterised, because a multi-frame icon set at 6 weights of
  HiDPI is what Qt wants as pixmaps. `tools/build_brand_assets.py` generates
  them from the vector masters in `brand/svg/`.
* Icons are loaded once and cached; `icon()` is cheap to call from paint paths.
* Re-branding means editing the hexes below and re-running the build script —
  nothing else in the app hardcodes a brand colour.
"""
from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

# The one version source (stdlib-only module) — the release workflow and the
# updater read the same value. See factory/version.py + UPDATES.md.
from factory.version import VERSION

# ---- tokens (mirror of tools/build_brand_assets.py) -----------------------
ACCENT = "#7C3AED"           # violet-600 — primary
ACCENT_HOVER = "#6D28D9"     # violet-700
ACCENT_PRESSED = "#5B21B6"   # violet-800
ACCENT_TEXT = "#5B21B6"      # violet-800, for accent text on light surfaces
ACCENT_SOFT = "#F5F3FF"      # violet-50  — tinted fill
ACCENT_SOFT_BORDER = "#DDD6FE"  # violet-200

INK = "#18181B"              # zinc-900 — primary text / solid actions
INK_MUTED = "#52525B"        # zinc-600
INK_FAINT = "#71717A"        # zinc-500

PRODUCT_NAME = "Simpl'd"
PARENT_NAME = "Blur'd"

_ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
_BRAND = os.path.join(_ASSETS, "brand")
_ICO = os.path.join(_ASSETS, "simpld.ico")

# Sizes rendered by tools/build_brand_assets.py, ascending.
_ICON_SIZES = (24, 32, 48, 64)
# Rasterised mark sizes. `_MARK_SIZES` is the fallback ladder: asking for an
# unrendered size (e.g. 34) snaps UP to the next available bitmap instead of
# returning a null QPixmap, so a new lockup size can never silently lose the mark.
_MARK_SIZES = (16, 20, 24, 32, 40, 48, 56, 60, 64, 72, 80, 96, 128, 160,
               192, 256, 512)

_icon_cache: dict[tuple[str, str], QIcon] = {}
_pixmap_cache: dict[tuple[str, str, int], QPixmap] = {}
_app_icon: QIcon | None = None

# icon(name, variant=...) -> filename suffix rendered by the asset build.
# "default" sits on the light sidebar, "accent" marks the active nav row,
# "on" is white for dark surfaces.
_ICON_VARIANTS = {"default": "", "accent": "-accent", "on": "-on"}


def _pixmap(name: str, variant: str, px: int) -> QPixmap:
    key = (name, variant, px)
    hit = _pixmap_cache.get(key)
    if hit is not None:
        return hit
    suffix = _ICON_VARIANTS.get(variant, "")
    pm = QPixmap(os.path.join(_BRAND, f"{name}{suffix}-{px}.png"))
    if pm.isNull() and suffix:  # missing variant -> fall back to default
        pm = QPixmap(os.path.join(_BRAND, f"{name}-{px}.png"))
    _pixmap_cache[key] = pm
    return pm


def icon(name: str, variant: str = "default") -> QIcon:
    """A nav/page icon.

    `variant` picks the stroke colour rendered by the asset build: "default"
    (zinc, light surfaces), "accent" (the active nav row) or "on" (white, for
    dark surfaces). A variant missing on disk falls back to default.

    Every rendered size is added to the QIcon so Qt can pick a 48px frame on a
    200%-scaled display instead of upscaling the 24px one.
    """
    key = (name, variant)
    hit = _icon_cache.get(key)
    if hit is not None:
        return hit
    ic = QIcon()
    for px in _ICON_SIZES:
        pm = _pixmap(name, variant, px)
        if not pm.isNull():
            ic.addPixmap(pm)
    _icon_cache[key] = ic
    return ic


def app_icon() -> QIcon:
    """The mark as an application icon (taskbar, window, shortcuts)."""
    global _app_icon
    if _app_icon is None:
        ic = QIcon()
        if os.path.isfile(_ICO):
            from PySide6.QtGui import QImageReader
            if QImageReader(_ICO).canRead():
                ic.addFile(_ICO)          # true multi-frame .ico, best on Windows
        if ic.isNull():
            for px in (256, 128, 64, 48, 32, 24, 16):
                pm = QPixmap(os.path.join(_BRAND, f"mark-{px}.png")) \
                    if os.path.isfile(os.path.join(_BRAND, f"mark-{px}.png")) else QPixmap()
                if not pm.isNull():
                    ic.addPixmap(pm)
        _app_icon = ic
    return _app_icon


def mark_pixmap(px: int = 128) -> QPixmap:
    """The mark. Snaps up to the nearest rendered size if `px` is not built."""
    want = max(16, int(px))
    for size in _MARK_SIZES:
        if size >= want:
            pm = QPixmap(os.path.join(_BRAND, f"mark-{size}.png"))
            if not pm.isNull():
                return pm
    return QPixmap(os.path.join(_BRAND, "mark-512.png"))


def apply_app_icon(app) -> None:
    """Set the icon on the QApplication so every window inherits it."""
    ic = app_icon()
    if not ic.isNull():
        app.setWindowIcon(ic)


def wordmark_text(size: int = 22, color: str = INK,
                  apostrophe: str = ACCENT) -> str:
    """Rich-text 'Simpl'd' with the apostrophe in the accent colour.

    Uses a literal U+2019, NOT `&#8217;` — Qt's rich-text parser only decodes
    &amp;/&lt;/&gt;/&quot;/&apos;/&nbsp; and renders numeric character
    references verbatim (you get "Simpl|d").
    """
    return (
        f"<span style=\"font-size:{size}px;font-weight:700;color:{color};\">"
        f"Simpl<span style=\"color:{apostrophe};\">\u2019</span>d</span>"
    )


def wordmark(size: int = 22, color: str = INK, mark_px: int | None = None) -> QLabel:
    """A QLabel containing just the wordmark (for next to a separate mark)."""
    lbl = QLabel(wordmark_text(size, color))
    lbl.setTextFormat(Qt.TextFormat.RichText)
    lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    return lbl


def lockup(size: int = 28, mark_px: int = 56, color: str = INK,
           tagline: str | None = None, parent: bool = False) -> QWidget:
    """Mark + wordmark, with the Blur'd parent line sitting UNDER the wordmark.

    Layout, as it appears in the login card and the sidebar:

        [mark]  Simpl'd
                A Blur'd product

    The parent line is in the column beside the mark rather than under the whole
    row, so it reads as a caption on the wordmark. Used on the login card, the
    sidebar and the About dialog so all three present the identity identically.
    """
    w = QWidget()
    outer = QVBoxLayout(w)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    row = QWidget()
    lay = QHBoxLayout(row)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(13)

    mk = QLabel()
    mk.setPixmap(mark_pixmap(mark_px))
    mk.setFixedSize(mark_px, mark_px)
    mk.setStyleSheet("background: transparent;")
    lay.addWidget(mk, 0, Qt.AlignmentFlag.AlignVCenter)

    # wordmark + caption share one column, so the caption aligns to the word
    col = QWidget()
    cl = QVBoxLayout(col)
    cl.setContentsMargins(0, 0, 0, 0)
    cl.setSpacing(1)
    cl.addWidget(wordmark(size, color), 0, Qt.AlignmentFlag.AlignLeft)
    if tagline:
        t = QLabel(tagline)
        t.setObjectName("brandTag")
        t.setStyleSheet("background: transparent;")
        cl.addWidget(t)
    if parent:
        p = QLabel(f"A {PARENT_NAME} product")
        p.setObjectName("brandParent")
        p.setStyleSheet("background: transparent;")
        cl.addWidget(p)
    # Sit the column on the wordmark's own optical baseline, not the mark's.
    cl.setContentsMargins(0, 0, 0, 0)
    lay.addWidget(col, 0, Qt.AlignmentFlag.AlignVCenter)
    lay.addStretch(1)

    outer.addWidget(row)
    return w