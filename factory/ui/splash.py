"""Branded splash + About dialog.

The splash is deliberately cheap: drawn once as a QPixmap and shown while the
database bootstraps, then handed off. Nothing blocks on a timer — it is closed
as soon as the real window comes up.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QRectF
from PySide6.QtGui import (QPixmap, QPainter, QColor, QFont, QLinearGradient,
                           QGuiApplication, QTextDocument)
from PySide6.QtWidgets import (QSplashScreen, QDialog, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QFrame)

from factory.ui import brand as _brand


def _splash_pixmap(w: int = 460, h: int = 262) -> QPixmap:
    """Compose the splash in Qt so all text uses real system fonts."""
    screen = QGuiApplication.primaryScreen()
    dpr = screen.devicePixelRatio() if screen else 1.0
    pm = QPixmap(int(w * dpr), int(h * dpr))
    pm.setDevicePixelRatio(dpr)
    pm.fill(QColor("#FFFFFF"))

    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # accent hairline across the top edge
    grad = QLinearGradient(0, 0, w, 0)
    grad.setColorAt(0.0, QColor(_brand.ACCENT))
    grad.setColorAt(1.0, QColor(_brand.ACCENT_HOVER))
    p.fillRect(0, 0, w, 3, grad)

    # Mark: draw the 256px master into a logical rect, so it stays sharp at any
    # device pixel ratio instead of scaling a 1:1 bitmap.
    side = 84
    p.drawPixmap(QRect((w - side) // 2, 46, side, side), _brand.mark_pixmap(256))

    # Wordmark through QTextDocument: a plain drawText() cannot colour the
    # apostrophe differently from the rest of the word.
    doc = QTextDocument()
    doc.setDocumentMargin(0)
    # Literal U+2019, not &#8217; — Qt's HTML parser does not decode numeric
    # character references and would print the entity verbatim.
    doc.setHtml(
        f'<div align="center" style="font-size:26pt;font-weight:700;'
        f'color:{_brand.INK};margin:0;padding:0;">'
        f"Simpl<span style=\"color:{_brand.ACCENT};\">\u2019</span>d</div>")
    doc.setTextWidth(w)
    p.save()
    p.translate(0, 146)
    doc.drawContents(p, QRectF(0, 0, w, 54))
    p.restore()

    f = QFont()
    f.setFamilies(["Segoe UI", "Noto Sans", "Inter", "sans-serif"])
    f.setPointSize(10)
    p.setFont(f)
    p.setPen(QColor(_brand.INK_FAINT))
    p.drawText(QRect(0, 208, w, 20), Qt.AlignmentFlag.AlignCenter,
               f"Version {_brand.VERSION}")
    p.drawText(QRect(0, 230, w, 20), Qt.AlignmentFlag.AlignCenter,
               f"A {_brand.PARENT_NAME} product")
    p.end()
    return pm


def show_splash() -> QSplashScreen | None:
    """Show the branded splash. Returns None if anything is unavailable."""
    try:
        sc = QSplashScreen(_splash_pixmap())
        sc.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        sc.show()
        QGuiApplication.processEvents()   # let it actually paint
        return sc
    except Exception:
        return None


def close_splash(sc) -> None:
    if sc is None:
        return
    try:
        sc.close()
    except Exception:
        pass


class AboutDialog(QDialog):
    """Version, attribution and the identity lockup."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"About {_brand.PRODUCT_NAME}")
        self.setFixedWidth(440)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(14)

        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(_brand.lockup(size=28, mark_px=44, parent=True))
        holder.addStretch(1)
        outer.addLayout(holder)

        card = QFrame()
        card.setProperty("card", True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(7)
        for k, v in (("Version", _brand.VERSION),
                     ("Product of", _brand.PARENT_NAME),
                     ("Works offline", "Yes — no data leaves this computer"),
                     ("Storage", "Local SQLite database on this PC")):
            row = QHBoxLayout()
            row.setSpacing(10)
            a = QLabel(k)
            a.setProperty("field", "caption")
            a.setFixedWidth(110)
            b = QLabel(v)
            b.setProperty("hint", True)
            b.setWordWrap(True)
            row.addWidget(a)
            row.addWidget(b, 1)
            cl.addLayout(row)
        outer.addWidget(card)

        note = QLabel("The company name prints where the old paper did — "
                      "stickers and packing slips. The app itself is Simpl'd.")
        note.setProperty("hint", True)
        note.setWordWrap(True)
        outer.addWidget(note)

        outer.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton("Close")
        close.setMinimumHeight(36)
        close.setMinimumWidth(96)
        from factory.ui.styles import style_button
        style_button(close, "accent")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        outer.addLayout(row)