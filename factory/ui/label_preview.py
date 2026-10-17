"""Label previews. Type-1 = single reel (NO QR, matches old photo).
Type-2 = carton/bundle (WITH QR). Packing slip matches old A4 format."""
from __future__ import annotations
import io
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QLabel, QPushButton,
                               QHBoxLayout, QTextEdit, QMessageBox, QSizePolicy)
from PySide6.QtGui import QPixmap, QImage
from PySide6.QtCore import Qt, QObject, QEvent
from factory.db import COMPANY, fmt_date_ddmmyy
from factory import data_access as DA
from factory.ui.styles import style_button


def make_qr_pixmap(text: str, size: int = 180):
    try:
        import qrcode
        q = qrcode.make(text, box_size=6, border=2)
        buf = io.BytesIO()
        q.save(buf, format="PNG")
        img = QImage.fromData(buf.getvalue())
        return QPixmap.fromImage(img).scaledToWidth(size)
    except Exception:
        return None


def pil_to_pixmap(img, max_width: int = 260):
    """Convert a PIL label render to a display QPixmap, preserving aspect.

    Print art stays 1:1 (203dpi, full label size); only the on-screen
    preview is scaled down. Aspect is never stretched. The cap is 260px so
    the pixmap always fits the narrowest preview panel (labels center a
    wider pixmap and its edges get clipped — that was the overflow bug).
    """
    try:
        import io as _io
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        qimg = QImage.fromData(buf.getvalue())
        px = QPixmap.fromImage(qimg)
        if px.width() > max_width:
            px = px.scaledToWidth(max_width, Qt.SmoothTransformation)
        return px
    except Exception:
        return None


# Usable sticker area = label contentsRect (Qt already subtracts the
# stylesheet padding/border: QLabel[sticker] in styles.py pledges 8px
# padding + 1px border each side, so contentsRect is the padded content
# bounds). Never add padding back — usable is content MINUS padding.
# Placeholder rows stay compact so the card never reserves sticker room.
_PREVIEW_TEXT_MIN_HEIGHT = 60
# Sticker label claims this content height when art is shown: compact,
# fixed claim derived from the ORIGINAL pixmap (never the fitted one),
# so refits cannot ratchet the layout down to zero.
_PREVIEW_COMPACT_H = 180


def _preview_hint_size(label):
    """Stable compact sizeHint from the stored original pixmap."""
    from PySide6.QtCore import QSize
    orig = getattr(label, "_orig_pixmap", None)
    try:
        if orig is not None and not orig.isNull() and orig.height() > 0:
            w = max(1, round(_PREVIEW_COMPACT_H * orig.width() / orig.height()))
            return QSize(w, _PREVIEW_COMPACT_H)
    except Exception:
        pass
    return None


class PreviewLabel(QLabel):
    """Sticker preview label with a stable compact sizeHint.

    sizeHint is derived from the stored ORIGINAL pixmap at a fixed
    compact height, so it never tracks the fitted pixmap (which would
    feed layout size back into the next fit and collapse to zero).
    minimumSizeHint stays (0, 0) so small windows can still compress it;
    the resize filter then scales the art down inside.
    """

    def sizeHint(self):
        s = _preview_hint_size(self)
        return s if s is not None else super().sizeHint()

    def minimumSizeHint(self):
        from PySide6.QtCore import QSize
        return QSize(0, 0)


def _fit_preview_pixmap(label) -> None:
    """Rescale the stored original pixmap to the padded content bounds.

    Keeps aspect ratio (Qt.KeepAspectRatio) with smooth scaling. The fit
    is idempotent (always scaled from the stored original, so a stable
    layout produces a stable pixmap) and the label never takes a minimum
    size from the pixmap — so the pixmap sizeHint cannot drive layout
    feedback. The card's maximumHeight caps vertical claims; the pixmap
    always fits inside, even when the window shrinks.
    """
    orig = getattr(label, "_orig_pixmap", None)
    if orig is None or orig.isNull():
        return
    try:
        # contentsRect() is the usable area with actual padding/border
        # already subtracted by Qt — use it directly, never add back.
        try:
            cr = label.contentsRect()
            avail_w, avail_h = int(cr.width()), int(cr.height())
        except Exception:
            avail_w = label.width()
            avail_h = label.height()
        avail_w = max(1, int(avail_w))
        avail_h = max(1, int(avail_h))
        # Not laid out yet (offscreen init): keep the original until the
        # first real resize delivers valid bounds.
        if label.width() <= 1 or label.height() <= 1:
            label.setPixmap(orig)
            return
        label.setPixmap(orig.scaled(
            avail_w, avail_h, Qt.KeepAspectRatio, Qt.SmoothTransformation))
    except Exception:
        pass


class _PreviewFitFilter(QObject):
    """Resize event filter: refit the sticker pixmap to padded bounds."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Resize:
            try:
                _fit_preview_pixmap(obj)
            except Exception:
                pass
        return False


def set_preview_label(label, px_or_text):
    """Show a sticker pixmap fitted inside the label, or placeholder text.

    Pixmap path: stores the full-res original, installs a resize event
    filter once, and scales smoothly to the padded content bounds.
    Horizontal policy is Ignored (pixmap width must never force the card
    wider); vertical is Preferred (claims compact room, capped by the
    card's maximumHeight, shrinkable to 0). Minimum size never comes
    from the pixmap, so there is no layout feedback loop.
    Text/None path: compact placeholder (small min height, no pixmap).
    Dialog callers are unaffected (they use QTextEdit, not this helper).
    """
    from PySide6.QtCore import Qt
    if px_or_text is None or isinstance(px_or_text, str):
        try:
            label._orig_pixmap = None
        except Exception:
            pass
        try:
            label.setPixmap(QPixmap())
        except Exception:
            pass
        label.setText("Preview unavailable." if px_or_text is None else px_or_text)
        try:
            # Word wrap back on: with a pixmap it must stay off, otherwise
            # the layout uses text height-for-width and collapses the label
            # to one text row instead of the pixmap's sizeHint height.
            label.setWordWrap(True)
            label.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
            label.setMinimumHeight(_PREVIEW_TEXT_MIN_HEIGHT)
            label.setMinimumWidth(0)
        except Exception:
            pass
        return
    try:
        label._orig_pixmap = px_or_text
    except Exception:
        pass
    try:
        label.setText("")
        label.setAlignment(Qt.AlignCenter)
        # Word wrap OFF while a pixmap is shown: with wrap on, the layout
        # uses text height-for-width and collapses the label to one row,
        # ignoring the pixmap sizeHint (then the fit sees ~0 height).
        label.setWordWrap(False)
        # Horizontal Ignored: pixmap width must not force the card wider.
        # Vertical Preferred: claim room up to the card's maximumHeight,
        # still shrinkable (minimum stays 0, never the pixmap height).
        label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        label.setMinimumSize(0, 0)
        label.setScaledContents(False)
        if getattr(label, "_fit_filter", None) is None:
            try:
                f = _PreviewFitFilter(label)
                label.installEventFilter(f)
                label._fit_filter = f
            except Exception:
                pass
        _fit_preview_pixmap(label)
    except Exception:
        try:
            label.setPixmap(px_or_text)
        except Exception:
            pass


def _pieces_txt(job: dict) -> str:
    wpr = job.get("wrappers_per_reel")
    try:
        return f"No of Pieces:{int(wpr)}" if wpr is not None else "No of Pieces:unknown"
    except (TypeError, ValueError):
        return "No of Pieces:unknown"


def reel_label_text(job: dict, reel: dict) -> str:
    ident = str(reel.get("roll_code") or reel["roll_no"])
    per = str(reel.get("roll_period") or "")
    per_txt = f"  Period:{per}" if per else ""
    return (
        f"{COMPANY}   J:{job['job_no']}\n"
        f"Job Name:{job['job_name']}\n"
        f"Date:{fmt_date_ddmmyy(reel['created_at'])}{per_txt}  |  No of Reels:1\n"
        f"{_pieces_txt(job)}\n"
        f"Gross Wt:  {reel['gross']:.3f} Kg   |  Roll No# {ident}\n"
        f"Core Wt:   {reel['core']:.3f} Kg   |\n"
        f"Net Wt:    {reel['net']:.3f} Kg   |\n"
        f"(Type-1 single-reel sticker — NO QR)"
    )


def carton_label_text(detail: dict) -> str:
    if int(detail.get("num_reels") or 0) == 0:
        return (
            f"{COMPANY}   J:{detail['job_no']}\n"
            f"Job Name:{detail['job_name']}\n"
            f"Carton:{detail['carton_no']}  Bundle:{detail['bundle_no']}\n"
            f"Date:{fmt_date_ddmmyy(detail['packed_at'])}\n"
            f"Box Wt:  {detail['total_gross']:.3f} Kg\n"
            f"QR:{detail['qr']}"
        )
    rolls = ",".join(str(r["roll_no"]) for r in detail["reels"])
    return (
        f"{COMPANY}   J:{detail['job_no']}\n"
        f"Job Name:{detail['job_name']}\n"
        f"Carton:{detail['carton_no']}  Bundle:{detail['bundle_no']}  Rolls:{rolls}\n"
        f"Date:{fmt_date_ddmmyy(detail['packed_at'])}  |  No of Reels:{detail['num_reels']}\n"
        f"Total Gross: {detail['total_gross']:.3f} Kg\n"
        f"Total Core:  {detail['total_core']:.3f} Kg\n"
        f"Total Net:   {detail['total_net']:.3f} Kg\n"
        f"QR:{detail['qr']}"
    )


class ReelLabelDialog(QDialog):
    def __init__(self, parent, job: dict, reel: dict, printer):
        super().__init__(parent)
        ident = str((reel or {}).get("roll_code") or (reel or {}).get("roll_no"))
        self.setWindowTitle(f"Reel sticker — Roll {ident} (NO QR)")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        ht = QLabel("Type-1: single reel sticker (no QR)")
        ht.setProperty("dialog", "title")
        lay.addWidget(ht)
        hd = QLabel("Matches the printed reel sticker layout exactly.")
        hd.setProperty("dialog", "desc")
        hd.setWordWrap(True)
        lay.addWidget(hd)
        t = QTextEdit()
        t.setReadOnly(True)
        t.setProperty("label", True)
        t.setMinimumHeight(220)
        t.setText(reel_label_text(job, reel))
        lay.addWidget(t)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_print = QPushButton("PRINT REEL STICKER")
        self.btn_print.setMinimumHeight(56)
        style_button(self.btn_print, "success")
        self.btn_close = QPushButton("Close")
        self.btn_close.setMinimumHeight(56)
        style_button(self.btn_close, "secondary")
        row.addWidget(self.btn_print, 2)
        row.addWidget(self.btn_close, 1)
        lay.addLayout(row)
        self.btn_close.clicked.connect(self.accept)
        self.btn_print.clicked.connect(self.do_print)
        self._job, self._reel, self._printer = job, reel, printer

    def do_print(self):
        # Complete rendering inputs — the real Qt driver renders the full
        # sticker from job+reel; the mock only file-logs the dict.
        payload = {"job": dict(self._job), "reel": dict(self._reel)}
        try:
            ok = bool(self._printer.print_reel_label(payload))
        except Exception as e:
            return QMessageBox.warning(
                self, "Could not print", str(e).strip() or
                "Could not print. Check the printer and try again.")
        if not ok:
            return QMessageBox.warning(
                self, "Could not print", "Could not print. Check the printer and try again.")
        # Persist print count/last state ONLY on printer-accepted success.
        # Errors stay pending and the record remains reprintable.
        # Local artwork prints on this machine; the accepted state is
        # recorded through the facade (local DB or LAN server).
        try:
            rid = (self._reel or {}).get("id")
            if rid:
                DA.mark_reel_printed(int(rid))
        except Exception:
            pass
        from factory.ui.toast import toast
        toast(self, f"Roll #{self._reel['roll_no']} sticker sent to the printer.")
        self.accept()


class CartonLabelDialog(QDialog):
    def __init__(self, parent, detail: dict, printer):
        super().__init__(parent)
        self.setWindowTitle(f"Carton sticker — {detail['carton_no']} (WITH QR)")
        self.setMinimumWidth(500)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        ht = QLabel("Type-2: carton / bundle sticker (with QR)")
        ht.setProperty("dialog", "title")
        lay.addWidget(ht)
        hd = QLabel("Matches the printed carton sticker layout exactly.")
        hd.setProperty("dialog", "desc")
        hd.setWordWrap(True)
        lay.addWidget(hd)
        t = QTextEdit()
        t.setReadOnly(True)
        t.setProperty("label", True)
        t.setMinimumHeight(220)
        t.setText(carton_label_text(detail))
        lay.addWidget(t)
        px = make_qr_pixmap(detail["qr"])
        if px:
            ql = QLabel()
            ql.setPixmap(px)
            ql.setAlignment(Qt.AlignCenter)
            lay.addWidget(ql)
        scan_info = QLabel(f"QR: {detail['qr']}")
        scan_info.setWordWrap(True)
        lay.addWidget(scan_info)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_print = QPushButton("PRINT CARTON STICKER")
        self.btn_print.setMinimumHeight(56)
        style_button(self.btn_print, "primary")
        self.btn_close = QPushButton("Close")
        self.btn_close.setMinimumHeight(56)
        style_button(self.btn_close, "secondary")
        row.addWidget(self.btn_print, 2)
        row.addWidget(self.btn_close, 1)
        lay.addLayout(row)
        self.btn_close.clicked.connect(self.accept)
        self.btn_print.clicked.connect(self.do_print)
        self._detail, self._printer = detail, printer

    def do_print(self):
        # Complete rendering inputs — the real Qt driver renders the full
        # sticker from the carton detail; the mock only file-logs the dict.
        payload = {"detail": dict(self._detail)}
        try:
            ok = bool(self._printer.print_carton_label(payload))
        except Exception as e:
            return QMessageBox.warning(
                self, "Could not print", str(e).strip() or
                "Could not print. Check the printer and try again.")
        if not ok:
            return QMessageBox.warning(
                self, "Could not print", "Could not print. Check the printer and try again.")
        try:
            cid = (self._detail or {}).get("id")
            if cid:
                DA.mark_carton_printed(int(cid))
        except Exception:
            pass
        from factory.ui.toast import toast
        toast(self, f"{self._detail['carton_no']} sticker sent to the printer.")
        self.accept()


class VerifyDialog(QDialog):
    """Scanner re-verification: scan a carton QR, see its batch + box details.

    Shows the job (batch), carton/bundle/rolls, weights, stock status and,
    if dispatched, which order took it. Read-only — no printing, no edits.
    """

    def __init__(self, parent, found: dict):
        super().__init__(parent)
        d = found["detail"]
        o = found.get("order")
        self.setWindowTitle(f"Verify — {d['carton_no']} ({d['status']})")
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        ht = QLabel(f"{d['carton_no']} · J:{d['job_no']} {d['job_name']}")
        ht.setProperty("dialog", "title")
        ht.setWordWrap(True)
        lay.addWidget(ht)
        try:
            from factory.ui.styles import badge_for_status, set_badge
            _badge = QLabel(f"{d['status']}" + (
                f" · {o['order_no']} → {o['customer']}" if o else " · in Ready stock"))
            set_badge(_badge, badge_for_status(str(d.get('status') or '')))
            _badge.setWordWrap(True)
            lay.addWidget(_badge)
        except Exception:
            pass
        rolls = ",".join(str(r["roll_no"]) for r in d["reels"])
        lines = [
            f"Batch: J:{d['job_no']} {d['job_name']} ({d.get('size_mm', '')}mm)",
            f"Box: {d['carton_no']}  Bundle:{d['bundle_no']}"
            + (f"  Rolls:{rolls}" if d["reels"] else "  (box weight only)"),
            f"Status: {d['status']}",
        ]
        if d["reels"]:
            lines.insert(2,
                f"Weights: Gross {d['total_gross']:.3f} / Core {d['total_core']:.3f} / "
                f"Net {d['total_net']:.3f} Kg")
        else:
            lines.insert(2, f"Box Wt: {d['total_gross']:.3f} Kg")
        if o:
            lines.append(f"Order: {o['order_no']} → {o['customer']} "
                         f"({o['status']}, {o['order_date']})")
        else:
            lines.append("Order: — (not dispatched)")
        lines.append(f"QR: {d['qr']}")
        t = QTextEdit()
        t.setReadOnly(True)
        t.setProperty("label", True)
        t.setMinimumHeight(200)
        t.setText("\n".join(lines))
        lay.addWidget(t)
        row = QHBoxLayout()
        row.setSpacing(8)
        b_close = QPushButton("Close")
        b_close.setMinimumHeight(48)
        style_button(b_close, "secondary")
        row.addStretch(1)
        row.addWidget(b_close, 1)
        lay.addLayout(row)
        b_close.clicked.connect(self.accept)


class PackingSlipDialog(QDialog):
    """A4 packing slip (matches the old paper slip).

    Left: internal bundle table from the database. Right: exact print preview
    (same painter as the printer/PDF output). Prints on a normal office
    printer via the printer box, or saves a PDF for local testing.
    """

    def __init__(self, parent, job_id: int):
        super().__init__(parent)
        self._job_id = job_id
        try:
            # Slip evidence through the facade: same dict locally or over
            # LAN. Artwork rendering below stays local (PDF task owns it).
            self._slip = DA.slip_data(job_id)
        except ValueError as e:
            QMessageBox.warning(self, "Packing slip", str(e))
            self._slip = None
            return
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            QMessageBox.warning(self, "Packing slip", f"Connection issue: {msg}")
            self._slip = None
            return
        self.setWindowTitle(f"Packing Slip — J:{self._slip['job_no']} (A4)")
        self.setMinimumSize(1020, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        ht = QLabel(f"Packing slip — J:{self._slip['job_no']} {self._slip['job_name']}")
        ht.setProperty("dialog", "title")
        ht.setWordWrap(True)
        lay.addWidget(ht)

        from PySide6.QtWidgets import (QSplitter, QTableWidget, QTableWidgetItem,
                                       QHeaderView, QAbstractItemView, QScrollArea,
                                       QComboBox, QFileDialog, QFrame, QWidget)
        from PySide6.QtGui import QPixmap
        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(8)
        lay.addWidget(split, 1)

        # ---- left: internal table ----
        left = QFrame()
        left.setProperty("card", True)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(12, 12, 12, 12)
        ll.setSpacing(8)
        cap = QLabel("Slip rows")
        cap.setProperty("field", "caption")
        ll.addWidget(cap)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Type", "Roll / Carton No.", "Gross / Box Wt", "Core Wt", "Net Wt", "Packed In / Note"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        for r in self._slip["rows"]:
            i = self.table.rowCount()
            self.table.insertRow(i)
            kind = r.get("kind", "Box" if r.get("weight_only") else "Reel")
            if kind == "Reel":
                self.table.setItem(i, 0, QTableWidgetItem("Reel"))
                self.table.setItem(
                    i, 1,
                    QTableWidgetItem(str(r.get("roll_label") or f"Roll #{r['roll_no']}")))
                self.table.setItem(i, 2, QTableWidgetItem(f"{r['gross']:.3f}"))
                self.table.setItem(i, 3, QTableWidgetItem(f"{r['core']:.2f}"))
                self.table.setItem(i, 4, QTableWidgetItem(f"{r['net']:.3f}"))
                self.table.setItem(i, 5, QTableWidgetItem(
                    r.get("carton_no") or "loose"))
            else:
                self.table.setItem(i, 0, QTableWidgetItem("Box"))
                self.table.setItem(i, 1, QTableWidgetItem(str(r.get("carton_no", ""))))
                self.table.setItem(i, 2, QTableWidgetItem(f"{r['gross']:.3f}"))
                self.table.setItem(i, 3, QTableWidgetItem("—"))
                self.table.setItem(i, 4, QTableWidgetItem("—"))
                self.table.setItem(i, 5, QTableWidgetItem(
                    f"Bundle {r.get('bundle_no', '')} · weighed box (independent)"))
        ll.addWidget(self.table, 1)
        t = self._slip["totals"]
        tot_text = (f"Reels: {t['reels']}  ·  Gross {t['gross']:.3f}"
                    f"  ·  Core {t['core']:.3f}  ·  Net {t['net']:.3f} Kg"
                    f"  |  Boxes (independent): {t.get('boxes', 0)}"
                    f"  ·  Box {t.get('box_wt', 0.0):.3f} Kg"
                    f"  |  Bundles/Cartons: {t['bundles']}")
        if t.get("legacy_cartons"):
            tot_text += (f"  ·  {t['legacy_cartons']} legacy reel-linked carton(s)"
                         " (rolls in table, weights in reel totals only)")
        wpr = self._slip.get("wrappers_per_reel")
        tot_text += f"  |  Wrappers/Reel: {int(wpr)}" if wpr is not None \
            else "  |  Wrappers/Reel: unknown"
        tot = QLabel(tot_text)
        tot.setProperty("count", True)
        tot.setWordWrap(True)
        ll.addWidget(tot)
        split.addWidget(left)

        # ---- right: print preview (same art as printer/PDF) ----
        right = QFrame()
        right.setProperty("card", True)
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 12, 12, 12)
        rl.setSpacing(8)
        cap2 = QLabel("Print preview (A4)")
        cap2.setProperty("field", "caption")
        rl.addWidget(cap2)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        holder = QWidget()
        vl = QVBoxLayout(holder)
        vl.setSpacing(10)
        try:
            # Same renderer locally and over LAN: the dialog fetched the
            # authoritative slip once via DA.slip_data(job_id) (local DB or
            # LAN get_slip op) and reuses it here, so preview never opens
            # local SQLite itself. No server image op is needed.
            from factory import slip_pdf as _SP
            _, pages = _SP.preview_images(job_id, slip=self._slip)
        except Exception:
            pages = []
        if not pages:
            empty = QLabel("Nothing packed for this job yet — no slip to print.")
            empty.setProperty("empty", True)
            empty.setWordWrap(True)
            vl.addWidget(empty)
        for img in pages:
            ql = QLabel()
            ql.setPixmap(QPixmap.fromImage(img).scaledToWidth(
                460, Qt.SmoothTransformation))
            ql.setAlignment(Qt.AlignHCenter)
            vl.addWidget(ql)
        vl.addStretch(1)
        scroll.setWidget(holder)
        rl.addWidget(scroll, 1)
        split.addWidget(right)
        split.setSizes([420, 560])

        # ---- bottom: printer + outputs ----
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel("Printer:"))
        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumHeight(44)
        self.printer_combo.setMinimumWidth(220)
        try:
            from PySide6.QtPrintSupport import QPrinterInfo
            names = [p.printerName() for p in QPrinterInfo.availablePrinters()]
        except Exception:
            names = []
        if names:
            self.printer_combo.addItems(names)
        else:
            self.printer_combo.addItem("(no printer found — Save PDF instead)")
            self.printer_combo.setEnabled(False)
        row.addWidget(self.printer_combo, 1)
        b_print = QPushButton("Print")
        style_button(b_print, "primary")
        b_pdf = QPushButton("Save PDF")
        style_button(b_pdf, "secondary")
        b_close = QPushButton("Close")
        style_button(b_close, "secondary")
        for b in (b_print, b_pdf, b_close):
            b.setMinimumHeight(44)
            row.addWidget(b)
        lay.addLayout(row)
        b_close.clicked.connect(self.accept)
        b_print.clicked.connect(self.do_print)
        b_pdf.clicked.connect(self.do_pdf)

    def _out_path(self, ext: str) -> str:
        import os
        try:
            from factory.db import app_data_dir
            return os.path.join(app_data_dir(), f"packing_slip_J{self._slip['job_no']}.{ext}")
        except Exception:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            return os.path.join(base, "data", f"packing_slip_J{self._slip['job_no']}.{ext}")

    def do_print(self):
        from factory import slip_pdf as SP
        if not getattr(self, "_slip", None):
            return QMessageBox.warning(
                self, "Print", "Slip not loaded. Refresh and try again.")
        name = self.printer_combo.currentText()
        if not self.printer_combo.isEnabled():
            return QMessageBox.warning(self, "Print", "No printer found. Use Save PDF.")
        try:
            # Local office hardware prints the prefetched authoritative
            # slip (local DB or LAN get_slip op); never re-reads SQLite.
            # Real printer errors raise and are shown — never false success.
            ok = SP.print_slip(self, self._job_id, printer_name=name,
                               slip=self._slip)
        except Exception as e:
            return QMessageBox.warning(self, "Print", f"Could not print:\n{e}")
        if not ok:
            return QMessageBox.warning(
                self, "Print", "Could not print. Check the printer and try again.")
        from factory.ui.toast import toast
        toast(self, "Packing slip sent to the printer.")

    def do_pdf(self):
        from PySide6.QtWidgets import QFileDialog
        from factory import slip_pdf as SP
        if not getattr(self, "_slip", None):
            return QMessageBox.warning(
                self, "Save PDF", "Slip not loaded. Refresh and try again.")
        path, _ = QFileDialog.getSaveFileName(
            self, "Save packing slip PDF", self._out_path("pdf"),
            "PDF (*.pdf)")
        if not path:
            return
        try:
            # Same prefetched slip as preview/print; no local DB read.
            SP.save_pdf(self._job_id, path, slip=self._slip)
        except Exception as e:
            return QMessageBox.warning(self, "Save PDF", f"Could not save:\n{e}")
        from factory.ui.toast import toast
        toast(self, "Slip saved as PDF.")
