"""Toast: one small auto-hiding note for success messages.

Replaces the old pattern of stacking a second modal ("Printed!", "Saved!",
"Exported!") on top of the dialog that did the work — operators had to tap
through two popups for every single action. A toast shows, then goes away
on its own; errors and questions stay as real dialogs because those need
an answer. Stdlib + Qt only, no animation timers to go wrong on old PCs.
"""
from __future__ import annotations
from PySide6.QtWidgets import QLabel
from PySide6.QtCore import Qt, QTimer

_active: QLabel | None = None

# Semantic toast backgrounds — same solids as styles.SEMANTIC so toasts,
# banners, badges and buttons agree. ok/error hexes are frozen (tests +
# floor familiarity): success #166534, error #991B1B.
TOAST_BG: dict[str, str] = {
    "success": "#166534",
    "error": "#991B1B",
    "warning": "#92400E",
    "info": "#1E40AF",
}

_KIND_ALIASES = {"ok": "success", "success": "success",
                 "err": "error", "error": "error",
                 "warn": "warning", "warning": "warning",
                 "info": "info"}


def toast(parent, text: str, ok: bool = True, ms: int = 2600,
          kind: str | None = None) -> QLabel | None:
    """Show a toast above parent's window. A new toast replaces the old one.

    `kind` is the semantic variant (success/error/warning/info); when omitted
    the legacy `ok` bool decides (True -> success green, False -> error red).
    An explicit `kind` always wins over `ok` so old `toast(p, msg, ok=...)`
    calls keep working unchanged.
    """
    global _active
    try:
        win = parent.window() if parent is not None else None
    except Exception:
        win = None
    if _active is not None:
        try:
            _active.close()
        except Exception:
            pass
        _active = None
    try:
        if kind is not None:
            bg = TOAST_BG.get(
                _KIND_ALIASES.get(str(kind).strip().lower(), ""), None)
            if bg is None:
                bg = "#166534" if ok else "#991B1B"
        else:
            bg = "#166534" if ok else "#991B1B"
        t = QLabel(str(text), win, Qt.WindowType.ToolTip)
        t.setStyleSheet(
            f"background: {bg}; color: white; padding: 10px 16px; "
            "border-radius: 8px; font-size: 13px; font-weight: 600;")
        t.setWordWrap(True)
        t.setMaximumWidth(480)
        t.setMinimumWidth(120)
        t.adjustSize()
        if win is not None:
            try:
                g = win.geometry()
                x = g.x() + (g.width() - t.width()) // 2
                y = g.y() + g.height() - t.height() - 64
                t.move(max(g.x() + 16, x), max(g.y() + 16, y))
            except Exception:
                pass
        t.show()
        _active = t
        tm = QTimer(t)  # child of the toast: dies with it, never dangles
        tm.setSingleShot(True)
        tm.timeout.connect(t.close)
        tm.start(max(800, ms))
        return t
    except Exception:
        return None


def dismiss():
    """Hide the current toast immediately (tests, shutdown)."""
    global _active
    if _active is not None:
        try:
            _active.close()
        except Exception:
            pass
        _active = None
