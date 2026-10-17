"""Shared native Qt theme v2 — Buridan visual language recreated with Qt widgets.

Grounded in https://buridan-ui.reflex.run/ docs (button/input/select/tabs/
dialog/frame/typography/message + theming tokens). This is NOT Reflex: no web
framework, no electron/webview — native PySide6 widgets styled to the same
neutral system: white cards on a muted shell, dark primary actions, muted
secondary/outline/ghost variants, compact 12-13px labels, 10px radius cards,
segmented tabs, quiet read-only tables, bordered info banners.

Token mapping (theming/default neutral):
  background #FAFAFA, card/popover #FFFFFF, primary #18181B,
  secondary/muted/accent #F4F4F5, muted-fg #71717A / #52525B,
  border/input #E4E4E7 / #D4D4D8, ring #18181B focus,
  destructive #B91C1C, radius 10px, card-padding ~16-20px.

Semantic tokens (success/warning/error/info + neutral):
  success #166534 on #F0FDF4 border #BBF7D0, solid #166534,
  warning #92400E on #FFFBEB border #FDE68A, solid #B45309,
  error   #991B1B on #FEF2F2 border #FECACA, solid #991B1B,
  info    #1E40AF on #EFF6FF border #BFDBFE, solid #1D4ED8,
  neutral #52525B on #F4F4F5 border #E4E4E7.
Use `badge_for_status()` to map domain statuses (Ready/loading/pending/...)
to a badge kind, and `set_badge()` / `set_banner()` to apply + repolish.
"""
# ---- semantic palette (single source of truth for Python + QSS below) ----
SEMANTIC: dict[str, dict[str, str]] = {
    "success": {"fg": "#166534", "bg": "#F0FDF4", "border": "#BBF7D0",
                "solid": "#166534", "solid_hover": "#15803D",
                "solid_pressed": "#14532D"},
    "warning": {"fg": "#92400E", "bg": "#FFFBEB", "border": "#FDE68A",
                "solid": "#B45309", "solid_hover": "#92400E",
                "solid_pressed": "#78350F"},
    "error": {"fg": "#991B1B", "bg": "#FEF2F2", "border": "#FECACA",
              "solid": "#991B1B", "solid_hover": "#7F1D1D",
              "solid_pressed": "#7F1D1D"},
    "info": {"fg": "#1E40AF", "bg": "#EFF6FF", "border": "#BFDBFE",
             "solid": "#1D4ED8", "solid_hover": "#1E40AF",
             "solid_pressed": "#1E3A8A"},
    "neutral": {"fg": "#52525B", "bg": "#F4F4F5", "border": "#E4E4E7",
                "solid": "#18181B", "solid_hover": "#27272A",
                "solid_pressed": "#09090B"},
}
import os as _os
from factory.ui import brand as _brand

_ASSETS = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "assets")
_CHEV_DOWN = _os.path.join(_ASSETS, "chev_down.png").replace("\\", "/")
_CHECK = _os.path.join(_ASSETS, "check.png").replace("\\", "/")
_CHEV_UP = _os.path.join(_ASSETS, "chev_up.png").replace("\\", "/")

APP_QSS = """
QMainWindow { background: #FAFAFA; }
QDialog { background: #FFFFFF; }
QWidget { font-family: 'Segoe UI', 'Noto Sans', sans-serif; font-size: 13px; color: #202124; }
QLabel { background: transparent; }
/* ---- page headers (typography h2 + muted description) ---- */
QLabel[page="title"] { font-size: 20px; font-weight: 700; color: #18181B; }
QLabel[page="desc"] { font-size: 13px; color: #52525B; }
QLabel[empty="true"] { font-size: 13px; color: #71717A; font-style: italic; }
QLabel[count="true"] { font-size: 13px; font-weight: 600; color: #18181B; }
/* ---- field captions (input labels: text-sm medium, compact) ---- */
QLabel[field="caption"] { font-size: 12px; font-weight: 600; color: #52525B; }
QLabel[stat="value"] { font-size: 30px; font-weight: 700; color: #18181B; }
QLabel[status="true"] { font-size: 14px; font-weight: 600; color: #18181B; }
/* ---- buttons: default / secondary / outline / ghost / link / destructive ---- */
QPushButton { background: white; border: 1px solid #D4D4D8; border-radius: 8px;
    padding: 7px 14px; font-size: 13px; font-weight: 600; min-height: 26px; }
QPushButton:hover { background: #F4F4F5; border-color: #A1A1AA; }
QPushButton:pressed { background: #E4E4E7; }
QPushButton:focus { border: 2px solid __ACCENT__; }
QPushButton:disabled { color: #A1A1AA; background: #F4F4F5; border-color: #E4E4E7; }
QPushButton[kind="primary"] { background: #18181B; color: white; border: 1px solid #18181B; }
QPushButton[kind="primary"]:hover { background: #27272A; border-color: #27272A; }
QPushButton[kind="primary"]:pressed { background: #09090B; }
QPushButton[kind="primary"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
/* ---- accent (brand) solid — for the single most important action on a page ---- */
QPushButton[kind="accent"] { background: __ACCENT__; color: white; border: 1px solid __ACCENT__; }
QPushButton[kind="accent"]:hover { background: __ACCENT_HOVER__; border-color: __ACCENT_HOVER__; }
QPushButton[kind="accent"]:pressed { background: __ACCENT_PRESSED__; border-color: __ACCENT_PRESSED__; }
QPushButton[kind="accent"]:focus { border: 2px solid __ACCENT_PRESSED__; }
QPushButton[kind="accent"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
/* ---- semantic solids (success/warning/error/info) ---- */
QPushButton[kind="success"] { background: #166534; color: white; border: 1px solid #166534; }
QPushButton[kind="success"]:hover { background: #15803D; border-color: #15803D; }
QPushButton[kind="success"]:pressed { background: #14532D; }
QPushButton[kind="success"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
QPushButton[kind="warning"] { background: #B45309; color: white; border: 1px solid #B45309; }
QPushButton[kind="warning"]:hover { background: #92400E; border-color: #92400E; }
QPushButton[kind="warning"]:pressed { background: #78350F; }
QPushButton[kind="warning"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
QPushButton[kind="info"] { background: #1D4ED8; color: white; border: 1px solid #1D4ED8; }
QPushButton[kind="info"]:hover { background: #1E40AF; border-color: #1E40AF; }
QPushButton[kind="info"]:pressed { background: #1E3A8A; }
QPushButton[kind="info"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
QPushButton[kind="danger-solid"] { background: #991B1B; color: white; border: 1px solid #991B1B; }
QPushButton[kind="danger-solid"]:hover { background: #7F1D1D; border-color: #7F1D1D; }
QPushButton[kind="danger-solid"]:pressed { background: #7F1D1D; }
QPushButton[kind="danger-solid"]:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
QPushButton[kind="secondary"] { background: #F4F4F5; color: #18181B; border: 1px solid #E4E4E7; }
QPushButton[kind="secondary"]:hover { background: #E9E9EB; border-color: #D4D4D8; }
QPushButton[kind="outline"] { background: white; color: #18181B; border: 1px solid #D4D4D8; }
QPushButton[kind="outline"]:hover { background: #F4F4F5; border-color: #18181B; }
QPushButton[kind="ghost"] { background: transparent; color: #18181B; border: 1px solid transparent; }
QPushButton[kind="ghost"]:hover { background: #F4F4F5; border-color: transparent; }
QPushButton[kind="link"] { background: transparent; color: #18181B; border: none; text-decoration: underline; padding: 4px 6px; }
QPushButton[kind="link"]:hover { color: #09090B; }
QPushButton[kind="danger"] { background: white; color: #B91C1C; border: 1px solid #D4D4D8; }
QPushButton[kind="danger"]:hover { background: #FEF2F2; border-color: #B91C1C; }
QPushButton[kind="danger"]:disabled { color: #A1A1AA; background: #F4F4F5; border-color: #E4E4E7; }
QPushButton[size="sm"] { padding: 5px 10px; font-size: 12px; min-height: 22px; border-radius: 6px; }
/* ---- sidebar (sidebar / sidebar-accent / sidebar-primary tokens) ---- */
QWidget#sidebar { background: #F4F4F5; border-right: 1px solid #E4E4E7; }
QLabel#sideTitle { font-size: 22px; font-weight: 700; color: #18181B; }
QLabel#sideSub, QLabel#offlineDot, QLabel#dbPath { color: #52525B; font-size: 12px; }
QLabel#brandParent { color: #71717A; font-size: 10px; }
QLabel#brandTag { color: #71717A; font-size: 11px; }
/* Nav glyphs: 18px, offset so the icon is not flush against the row edge. */
QPushButton[kind="nav"] { background: transparent; border: 1px solid transparent; text-align: left; padding: 9px 12px; font-size: 13px; border-radius: 8px; icon-size: 18px; }
QPushButton[kind="nav"]:hover { background: #E9E9EB; border-color: transparent; }
QPushButton[kind="nav"]:checked { background: __ACCENT__; color: white; font-weight: 700; border-color: __ACCENT__; }
QPushButton[kind="nav"]:checked:hover { background: __ACCENT_HOVER__; }
QPushButton[kind="nav"]:focus { border: 2px solid __ACCENT__; }
/* ---- frame cards (frame.root/panel: bordered white panels, 10px radius) ---- */
QFrame[card="true"] { background: white; border: 1px solid #E4E4E7; border-radius: 10px; }
QLabel[step="true"] { font-size: 15px; font-weight: 700; color: #18181B; }
QLabel[hint="true"] { color: #52525B; font-size: 12px; }
QLabel[jobinfo="true"] { color: #52525B; font-size: 12px; }
/* ---- inputs (input/select: bordered, compact, focus ring) ---- */
QLineEdit, QComboBox, QDoubleSpinBox, QTextEdit, QSpinBox { background: white; border: 1px solid #D4D4D8;
    border-radius: 8px; padding: 7px 10px; min-height: 24px; font-size: 13px; color: #18181B; }
QLineEdit:hover, QComboBox:hover, QDoubleSpinBox:hover, QTextEdit:hover { border-color: #A1A1AA; }
QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QTextEdit:focus { border: 2px solid __ACCENT__; }
QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QTextEdit:disabled { background: #F4F4F5; color: #A1A1AA; border-color: #E4E4E7; }
QLineEdit[scan="true"] { font-size: 16px; font-weight: 600; }
QDoubleSpinBox[big="true"] { font-size: 30px; font-weight: 700; }
QComboBox { padding-right: 32px; combobox-popup: 0; }
QComboBox::drop-down { width: 28px; border: none; }
QComboBox::down-arrow { image: url("__CHEV_DOWN__"); width: 12px; height: 12px; }
QComboBox QAbstractItemView { background: white; color: #202124; selection-background-color: #F4F4F5; selection-color: #27272A; padding: 4px; border: 1px solid #E4E4E7; }
QDoubleSpinBox { padding-right: 34px; }
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button { width: 28px; border-left: 1px solid #E4E4E7; background: #F4F4F5; }
QDoubleSpinBox::up-arrow { image: url("__CHEV_UP__"); width: 12px; height: 12px; }
QDoubleSpinBox::down-arrow { image: url("__CHEV_DOWN__"); width: 12px; height: 12px; }
QTextEdit[label="true"] { background: #FAFAFA; font-family: monospace; font-size: 12px; }
/* ---- quiet read-only tables (typography table: bordered header, muted) ---- */
QTableWidget { background: white; border: 1px solid #E4E4E7; border-radius: 8px; font-size: 13px;
    gridline-color: #F4F4F5; selection-background-color: #18181B; selection-color: white;
    alternate-background-color: #FAFAFA; }
QTableWidget::item { padding: 6px; border: none; }
QTableWidget::item:selected { background: #18181B; color: white; }
QTableWidget::item:selected:!active { background: #27272A; color: white; }
QTableWidget:focus { border: 1px solid #A1A1AA; }
QTableWidget::indicator { width: 22px; height: 22px; }
QTableWidget::indicator:unchecked { border: 1px solid #71717A; border-radius: 4px; background: white; }
QTableWidget::indicator:checked { border: 2px solid #27272A; border-radius: 4px; background: #18181B; image: url("__CHECK__"); }
QHeaderView::section { background: #FAFAFA; color: #52525B; font-size: 12px; font-weight: 600; padding: 8px 6px; border: none; border-bottom: 1px solid #E4E4E7; }
/* ---- scale readout + banners (message bubble variants) ---- */
/* ---- device strip (per-station hardware status pills) ---- */
QLabel#deviceDetail { color: #71717A; font-size: 12px; background: transparent; }
/* ---- scale readout + banners (message bubble variants) ---- */
QLabel[lcd="gross"] { font-size: 34px; font-weight: 700; }
QLabel[lcd="net-ok"], QLabel[lcd="net-idle"], QLabel[lcd="net-bad"], QLabel[lcd="net-warn"], QLabel[lcd="net-info"] { font-size: 18px; font-weight: 700; background: #F4F4F5; border: 1px solid #E4E4E7; border-radius: 8px; padding: 10px; }
QLabel[lcd="net-ok"] { color: #166534; background: #F0FDF4; border-color: #BBF7D0; }
QLabel[lcd="net-bad"] { color: #991B1B; background: #FEF2F2; border-color: #FECACA; }
QLabel[lcd="net-warn"] { color: #92400E; background: #FFFBEB; border-color: #FDE68A; }
QLabel[lcd="net-info"] { color: #1E40AF; background: #EFF6FF; border-color: #BFDBFE; }
QLabel[banner="ok"], QLabel[banner="err"], QLabel[banner="info"], QLabel[banner="warn"], QLabel[banner="neutral"] { font-size: 12px; padding: 8px 10px; border-radius: 8px; border: 1px solid transparent; }
QLabel[banner="ok"] { color: #166534; background: #F0FDF4; border-color: #BBF7D0; }
QLabel[banner="err"] { color: #991B1B; background: #FEF2F2; border-color: #FECACA; }
QLabel[banner="info"] { color: #1E40AF; background: #EFF6FF; border-color: #BFDBFE; }
QLabel[banner="warn"] { color: #92400E; background: #FFFBEB; border-color: #FDE68A; }
QLabel[banner="neutral"] { color: #52525B; background: #F4F4F5; border-color: #E4E4E7; }
/* ---- status badges (small semantic pills for order/job/carton/reel states) ---- */
QLabel[badge="success"], QLabel[badge="warning"], QLabel[badge="error"], QLabel[badge="info"], QLabel[badge="neutral"] { font-size: 11px; font-weight: 700; padding: 3px 10px; border-radius: 10px; border: 1px solid transparent; }
QLabel[badge="success"] { color: #166534; background: #F0FDF4; border-color: #BBF7D0; }
QLabel[badge="warning"] { color: #92400E; background: #FFFBEB; border-color: #FDE68A; }
QLabel[badge="error"] { color: #991B1B; background: #FEF2F2; border-color: #FECACA; }
QLabel[badge="info"] { color: #1E40AF; background: #EFF6FF; border-color: #BFDBFE; }
QLabel[badge="neutral"] { color: #52525B; background: #F4F4F5; border-color: #E4E4E7; }
/* ---- dialog chrome (dialog.popup: white, title + description) ---- */
QLabel[dialog="title"] { font-size: 16px; font-weight: 700; color: #18181B; }
QLabel[dialog="desc"] { font-size: 12px; color: #52525B; }
/* ---- sticker preview (4-in label renders, aspect-preserved) ---- */
QLabel[sticker="true"] { background: white; border: 1px solid #E4E4E7; border-radius: 8px; padding: 8px; }
QDialogButtonBox QPushButton { min-width: 90px; }
QProgressBar { background: #F4F4F5; border: 1px solid #E4E4E7; border-radius: 4px; text-align: center; font-size: 12px; color: #52525B; }
QProgressBar::chunk { background: __ACCENT__; border-radius: 3px; }
QProgressBar[accent="success"]::chunk { background: #166534; }
QProgressBar[accent="warning"]::chunk { background: #B45309; }
QProgressBar[accent="error"]::chunk { background: #991B1B; }
QProgressBar[accent="info"]::chunk { background: #1D4ED8; }
QScrollArea { border: none; background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
/* ---- segmented tabs (tabs.list: muted track, white selected panel tab) ---- */
QTabBar { background: #F4F4F5; border: 1px solid #E4E4E7; border-radius: 8px; padding: 3px; }
QTabBar::tab { background: transparent; color: #52525B; padding: 7px 12px; margin: 1px; border: 1px solid transparent; border-radius: 6px; font-size: 13px; }
QTabBar::tab:selected { background: white; color: #18181B; border: 1px solid #D4D4D8; font-weight: 700; }
QTabBar::tab:hover:!selected { color: #18181B; background: #E9E9EB; }
QTabBar::tab:disabled { color: #A1A1AA; }
QScrollBar:vertical { width: 10px; background: #FAFAFA; }
QScrollBar::handle:vertical { background: #D4D4D8; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
QScrollBar:horizontal { height: 10px; background: #FAFAFA; }
QScrollBar::handle:horizontal { background: #D4D4D8; min-width: 30px; border-radius: 4px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: none; }
QStatusBar { color: #52525B; font-size: 12px; }
QStatusBar::item { border: none; }
"""

# Resolve arrow-image placeholders to absolute asset paths (works both from
# run.py and from tests, independent of CWD).
APP_QSS = APP_QSS.replace("__CHEV_DOWN__", _CHEV_DOWN).replace("__CHEV_UP__", _CHEV_UP).replace("__CHECK__", _CHECK)

# Resolve brand colour placeholders. The zinc neutrals stay inline above (they
# are structural); the accent lives only in factory/ui/brand.py so a re-brand is
# a one-file edit plus `python3 tools/build_brand_assets.py`.
APP_QSS = (APP_QSS
           .replace("__ACCENT_HOVER__", _brand.ACCENT_HOVER)
           .replace("__ACCENT_PRESSED__", _brand.ACCENT_PRESSED)
           .replace("__ACCENT__", _brand.ACCENT))


def style_button(btn, kind: str):
    """Apply a themed kind. Call before the widget is shown."""
    btn.setProperty("kind", kind)
    # re-polish in case it was already shown
    try:
        s = btn.style()
        s.unpolish(btn)
        s.polish(btn)
    except Exception:
        pass
    return btn


def _repolish(widget):
    """Re-apply QSS after a dynamic property change."""
    try:
        s = widget.style()
        s.unpolish(widget)
        s.polish(widget)
    except Exception:
        pass
    return widget


def set_banner(label, kind: str):
    """Apply a semantic banner kind: ok/err/info/warn/neutral.

    `err` is kept as the alias for the error/red banner (login uses it).
    Unknown kinds fall back to neutral so a typo never leaves a label
    unstyled.
    """
    mapping = {"ok": "ok", "success": "ok",
               "err": "err", "error": "err",
               "info": "info", "warn": "warn", "warning": "warn",
               "neutral": "neutral"}
    label.setProperty("banner", mapping.get(str(kind or "").lower(), "neutral"))
    return _repolish(label)


def set_badge(label, kind: str):
    """Apply a semantic badge pill: success/warning/error/info/neutral."""
    mapping = {"success": "success", "ok": "success",
               "warning": "warning", "warn": "warning",
               "error": "error", "err": "error",
               "info": "info", "neutral": "neutral"}
    label.setProperty("badge", mapping.get(str(kind or "").lower(), "neutral"))
    return _repolish(label)


def set_lcd(label, mode: str):
    """Apply a scale-readout mode: net-ok/net-bad/net-warn/net-info/net-idle."""
    allowed = {"net-ok", "net-bad", "net-warn", "net-info", "net-idle",
               "ok", "bad", "warn", "info", "idle"}
    raw = str(mode or "net-idle").lower()
    if raw in ("ok", "bad", "warn", "info", "idle"):
        raw = f"net-{raw}"
    if raw not in ("net-ok", "net-bad", "net-warn", "net-info", "net-idle"):
        raw = "net-idle"
    label.setProperty("lcd", raw)
    return _repolish(label)


# Domain status -> badge kind. Keys are lower-cased backend statuses.
_BADGE_FOR_STATUS: dict[str, str] = {
    # ready to go (green)
    "ready": "success",
    "complete": "success",
    "completed": "success",
    "dispatched": "success",
    "printed": "success",
    "open": "success",
    # in progress / needs attention (amber/blue)
    "loading": "info",
    "loose": "warning",
    "pending": "warning",
    "partial": "warning",
    # blocked / gone (red/neutral)
    "cancelled": "neutral",
    "closed": "neutral",
    "dispatched_gone": "neutral",
    "failed": "error",
    "error": "error",
    "unknown": "neutral",
}


def badge_for_status(status: str) -> str:
    """Map a domain status string to a badge kind."""
    return _BADGE_FOR_STATUS.get(str(status or "").strip().lower(), "neutral")


def status_color(status: str) -> str:
    """Foreground hex for a domain status (for table-cell text)."""
    kind = badge_for_status(status)
    try:
        return str(SEMANTIC[kind]["fg"])
    except Exception:
        return str(SEMANTIC["neutral"]["fg"])


# ---- backward-compat names (unused). Kept empty so legacy imports do not
# override APP_QSS with incompatible inline styles. ----
BIG_BUTTON = ""
NAV_BUTTON = ""
STAT_CARD = ""
OK_MSG = "#15803D"
ERR_MSG = "#DC2626"
WEIGHT_STYLE = ""
