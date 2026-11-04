"""Label Layout Editor: stock + content-box + margin setter.

Settings entry (admin): About -> "Label layout..." opens the full editor
for one template. Read-only mini previews sit in the print dialogs
(Reel/Carton/Client) so the floor sees what will print.

Canvas is a custom QWidget.paintEvent (scene units are millimetres):
stock, printable overlay, grid, rulers, centre guides, live preview
(same render_content() as print — pixel-identical geometry), content
box with 8 drag handles. All numeric fields honour the units switch.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPen, QPixmap,
                           QImage)
from PySide6.QtWidgets import (QWidget, QDialog, QVBoxLayout, QHBoxLayout,
                               QLabel, QPushButton, QDoubleSpinBox, QSpinBox,
                               QComboBox, QCheckBox, QGridLayout, QMessageBox,
                               QFileDialog, QMenu, QInputDialog, QTabWidget,
                               QGroupBox, QFormLayout, QSizePolicy,
                               QScrollArea, QFrame)

from factory.hardware import layout as L
from factory import data_access as DA
from factory.ui.styles import style_button

ACCENT = QColor("#4F46E5")
HANDLE_PX = 9.0
MIN_HANDLE_MM = 5.0


def _pixmap_of(pil_img) -> QPixmap | None:
    try:
        import io as _io
        buf = _io.BytesIO()
        pil_img.save(buf, format="PNG")
        return QPixmap.fromImage(QImage.fromData(buf.getvalue()))
    except Exception:
        return None


class LayoutCanvas(QWidget):
    """Millimetre canvas: drag the content box, drag its 8 handles."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout: dict = dict(L.DEFAULTS["reel"])
        self._layout["template_id"] = "reel"
        self._printable: dict | None = None
        self._preview_px: QPixmap | None = None
        self._preview_mm: tuple[float, float] = (0.0, 0.0)
        self._zoom = 1.0
        self._drag: str | None = None
        self._anchor: tuple[float, float, float, float] = (0, 0, 0, 0)
        self._press_mm: tuple[float, float] = (0, 0)
        self._show_printable = True
        self._show_grid = True
        self._aspect_lock = False
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumSize(QSize(420, 320))
        self.setMouseTracking(True)
        self.changed = None  # callback set by the dialog: fn(layout)

    # ----- model -----
    def set_model(self, layout: dict, printable: dict | None,
                  preview_px: QPixmap | None = None,
                  preview_mm: tuple[float, float] | None = None) -> None:
        self._layout = dict(layout)
        self._printable = dict(printable) if printable else None
        if preview_px is not None:
            self._preview_px = preview_px
        if preview_mm is not None:
            self._preview_mm = preview_mm
        self.update()

    def layout(self) -> dict:
        return dict(self._layout)

    # ----- geometry -----
    def _view(self) -> tuple[float, float, float]:
        """(scale px/mm, origin-x px, origin-y px) letterboxed to fit."""
        sw = max(1.0, float(self._layout.get("stock_w_mm") or 1))
        sh = max(1.0, float(self._layout.get("stock_h_mm") or 1))
        pad = 34.0  # room for rulers
        try:
            avail_w = max(50.0, float(self.width()) - pad)
            avail_h = max(50.0, float(self.height()) - pad)
        except Exception:
            avail_w, avail_h = 400.0, 300.0
        base = min(avail_w / sw, avail_h / sh)
        s = base * self._zoom
        ox = pad + (avail_w - sw * s) / 2.0
        oy = pad + (avail_h - sh * s) / 2.0
        return s, ox, oy

    def _to_px(self, x_mm: float, y_mm: float) -> tuple[float, float]:
        s, ox, oy = self._view()
        return ox + x_mm * s, oy + y_mm * s

    def _to_mm(self, x_px: float, y_px: float) -> tuple[float, float]:
        s, ox, oy = self._view()
        return (x_px - ox) / s, (y_px - oy) / s

    def _handles(self) -> dict[str, tuple[float, float]]:
        l = self._layout
        x = float(l["content_x_mm"])
        y = float(l["content_y_mm"])
        w = float(l["content_w_mm"])
        h = float(l["content_h_mm"])
        return {"nw": (x, y), "n": (x + w / 2, y), "ne": (x + w, y),
                "e": (x + w, y + h / 2), "se": (x + w, y + h),
                "s": (x + w / 2, y + h), "sw": (x, y + h),
                "w": (x, y + h / 2)}

    def _hit_handle(self, px: float, py: float) -> str | None:
        for name, (hx, hy) in self._handles().items():
            qx, qy = self._to_px(hx, hy)
            if abs(qx - px) <= HANDLE_PX and abs(qy - py) <= HANDLE_PX:
                return name
        return None

    def _in_content(self, px: float, py: float) -> bool:
        x, y = self._to_mm(px, py)
        l = self._layout
        return (float(l["content_x_mm"]) <= x
                <= float(l["content_x_mm"]) + float(l["content_w_mm"])
                and float(l["content_y_mm"]) <= y
                <= float(l["content_y_mm"]) + float(l["content_h_mm"]))

    # ----- painting -----
    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            p.setRenderHint(QPainter.SmoothPixmapTransform, True)
            p.fillRect(self.rect(), QColor("#F8FAFC"))
            s, ox, oy = self._view()
            lay = self._layout
            sw = float(lay["stock_w_mm"])
            sh = float(lay["stock_h_mm"])
            cx = float(lay["content_x_mm"])
            cy = float(lay["content_y_mm"])
            cw = float(lay["content_w_mm"])
            ch = float(lay["content_h_mm"])

            def R(x, y, w, h):
                return QRectF(ox + x * s, oy + y * s, w * s, h * s)

            # shadow + stock
            p.fillRect(R(2.2, 2.2, sw, sh), QColor(0, 0, 0, 28))
            p.fillRect(R(0, 0, sw, sh), QColor("#FFFFFF"))
            p.setPen(QPen(QColor("#0F172A"), 1.2))
            p.drawRect(R(0, 0, sw, sh))

            # printable overlay: hatch the non-printable strips
            if self._show_printable and self._printable:
                try:
                    px = float(self._printable.get("origin_x_mm", 0) or 0)
                    py = float(self._printable.get("origin_y_mm", 0) or 0)
                    pw = float(self._printable.get("print_w_mm", 0) or 0)
                    ph = float(self._printable.get("print_h_mm", 0) or 0)
                    hatch = QBrush(QColor("#CBD5E1"), Qt.Dense4Pattern)
                    p.setPen(Qt.NoPen)
                    p.setBrush(hatch)
                    for sx, sy, ww, hh in (
                            (0, 0, px, sh),
                            (px + pw, 0, sw - (px + pw), sh),
                            (px, 0, pw, py),
                            (px, py + ph, pw, sh - (py + ph))):
                        if ww > 0.01 and hh > 0.01:
                            p.fillRect(R(sx, sy, ww, hh), hatch)
                except Exception:
                    pass

            # grid
            if self._show_grid:
                p.setPen(QPen(QColor("#F1F5F9"), 1.0))
                x = 5.0
                while x < sw:
                    p.drawLine(*[int(v) for v in
                                 (*self._to_px(x, 0), *self._to_px(x, sh))])
                    x += 5.0
                y = 5.0
                while y < sh:
                    p.drawLine(*[int(v) for v in
                                 (*self._to_px(0, y), *self._to_px(sw, y))])
                    y += 5.0
                p.setPen(QPen(QColor("#E2E8F0"), 1.0))
                x = 10.0
                while x < sw:
                    p.drawLine(*[int(v) for v in
                                 (*self._to_px(x, 0), *self._to_px(x, sh))])
                    x += 10.0
                y = 10.0
                while y < sh:
                    p.drawLine(*[int(v) for v in
                                 (*self._to_px(0, y), *self._to_px(sw, y))])
                    y += 10.0

            # rulers (top + left, ticks 1mm, numbers 10mm)
            p.setPen(QPen(QColor("#64748B"), 1.0))
            f = QFont()
            f.setPointSize(7)
            p.setFont(f)
            mm = 0.0
            while mm <= sw + 1e-9:
                x, y0 = self._to_px(mm, 0)
                tick = 6 if abs(mm % 10) < 1e-9 else 3
                p.drawLine(int(x), int(y0 - tick - 2), int(x), int(y0 - 2))
                if abs(mm % 10) < 1e-9:
                    p.drawText(int(x) + 2, int(y0) - 10, f"{mm:.0f}")
                mm += 1.0
            mm = 0.0
            while mm <= sh + 1e-9:
                x0, y = self._to_px(0, mm)
                tick = 6 if abs(mm % 10) < 1e-9 else 3
                p.drawLine(int(x0) - tick - 2, int(y), int(x0) - 2, int(y))
                if abs(mm % 10) < 1e-9:
                    p.drawText(int(x0) - 24, int(y) + 3, f"{mm:.0f}")
                mm += 1.0

            # centre guides while dragging
            if self._drag:
                p.setPen(QPen(QColor("#94A3B8"), 1.0, Qt.DashLine))
                gx0, gy0 = self._to_px(sw / 2, 0)
                gx1, gy1 = self._to_px(sw / 2, sh)
                p.drawLine(int(gx0), int(gy0), int(gx1), int(gy1))
                hx0, hy0 = self._to_px(0, sh / 2)
                hx1, hy1 = self._to_px(sw, sh / 2)
                p.drawLine(int(hx0), int(hy0), int(hx1), int(hy1))

            # live preview (same bitmap geometry as print)
            if self._preview_px is not None and not self._preview_px.isNull():
                pw_mm, ph_mm = self._preview_mm
                if pw_mm <= 0 or ph_mm <= 0:
                    pw_mm, ph_mm = cw, ch
                p.drawPixmap(R(cx, cy, pw_mm, ph_mm), self._preview_px,
                             QRectF(self._preview_px.rect()))

            # content box
            p.setPen(QPen(ACCENT, 2.0))
            p.setBrush(QBrush(QColor(79, 70, 229, 36)))
            p.drawRect(R(cx, cy, cw, ch))
            # handles
            p.setBrush(QBrush(QColor("#FFFFFF")))
            p.setPen(QPen(ACCENT, 1.4))
            for _name, (hx, hy) in self._handles().items():
                qx, qy = self._to_px(hx, hy)
                p.drawRect(QRectF(qx - HANDLE_PX / 2, qy - HANDLE_PX / 2,
                                  HANDLE_PX, HANDLE_PX))
        finally:
            p.end()

    # ----- interaction -----
    def _snap(self, v: float, modifiers) -> float:
        step = 0.1 if (modifiers & Qt.AltModifier) else 0.5
        return round(round(v / step) * step, 2)

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.RightButton:
            self._context_menu(ev.globalPos())
            return
        if ev.button() != Qt.LeftButton:
            return
        h = self._hit_handle(float(ev.position().x()),
                             float(ev.position().y()))
        lay = self._layout
        self._anchor = (float(lay["content_x_mm"]),
                        float(lay["content_y_mm"]),
                        float(lay["content_w_mm"]),
                        float(lay["content_h_mm"]))
        self._press_mm = self._to_mm(float(ev.position().x()),
                                     float(ev.position().y()))
        if h:
            self._drag = h
        elif self._in_content(float(ev.position().x()),
                              float(ev.position().y())):
            self._drag = "move"
        else:
            self._drag = None
        self.update()

    def mouseMoveEvent(self, ev) -> None:
        # hover tooltip over unprintable strips
        if self._drag is None and self._printable:
            try:
                x, y = self._to_mm(float(ev.position().x()),
                                   float(ev.position().y()))
                px = float(self._printable.get("origin_x_mm", 0) or 0)
                py = float(self._printable.get("origin_y_mm", 0) or 0)
                pw = float(self._printable.get("print_w_mm", 0) or 0)
                ph = float(self._printable.get("print_h_mm", 0) or 0)
                inside = (px <= x <= px + pw and py <= y <= py + ph)
                self.setToolTip("" if inside else "unprintable")
            except Exception:
                pass
        if not self._drag:
            return
        x, y = self._to_mm(float(ev.position().x()),
                           float(ev.position().y()))
        dx = self._snap(x - self._press_mm[0], ev.modifiers())
        dy = self._snap(y - self._press_mm[1], ev.modifiers())
        ax, ay, aw, ah = self._anchor
        sw = float(self._layout["stock_w_mm"])
        sh = float(self._layout["stock_h_mm"])
        lock = self._aspect_lock or bool(ev.modifiers() & Qt.ShiftModifier)

        def emit():
            self._layout = L.clamp_to_stock(self._layout)
            self.update()
            if callable(self.changed):
                self.changed(dict(self._layout))

        if self._drag == "move":
            self._layout["content_x_mm"] = round(
                max(0.0, min(ax + dx, sw - aw)), 2)
            self._layout["content_y_mm"] = round(
                max(0.0, min(ay + dy, sh - ah)), 2)
            emit()
            return
        # resize from the anchor edge(s)
        nx, ny, nw, nh = ax, ay, aw, ah
        if "w" in self._drag:
            nx = round(max(0.0, ax + dx), 2)
            nw = aw - (nx - ax)
        if "e" in self._drag:
            nw = aw + dx
        if "n" in self._drag:
            ny = round(max(0.0, ay + dy), 2)
            nh = ah - (ny - ay)
        if "s" in self._drag:
            nh = ah + dy
        if lock and aw > 0 and ah > 0:
            # keep aspect from the dominant axis
            if abs(nw - aw) >= abs(nh - ah):
                nh = round(nw * ah / aw, 2)
                if "n" in self._drag:
                    ny = round(ay + (ah - nh), 2)
            else:
                nw = round(nh * aw / ah, 2)
                if "w" in self._drag:
                    nx = round(ax + (aw - nw), 2)
        nw = max(MIN_HANDLE_MM, round(nw, 2))
        nh = max(MIN_HANDLE_MM, round(nh, 2))
        if "w" in self._drag and not lock:
            nx = round(min(nx, sw - MIN_HANDLE_MM), 2)
        if "n" in self._drag and not lock:
            ny = round(min(ny, sh - MIN_HANDLE_MM), 2)
        self._layout["content_x_mm"] = nx
        self._layout["content_y_mm"] = ny
        self._layout["content_w_mm"] = nw
        self._layout["content_h_mm"] = nh
        emit()

    def mouseReleaseEvent(self, ev) -> None:
        self._drag = None
        self.update()

    def mouseDoubleClickEvent(self, ev) -> None:
        lay = self._layout
        txt, ok = QInputDialog.getText(
            self, "Content box",
            "x, y, w, h in mm (e.g. 3, 3, 94, 44):",
            text=(f"{lay['content_x_mm']}, {lay['content_y_mm']}, "
                  f"{lay['content_w_mm']}, {lay['content_h_mm']}"))
        if not ok:
            return
        try:
            parts = [float(v) for v in str(txt).replace(";", ",").split(",")]
            if len(parts) != 4:
                raise ValueError("need four numbers")
            x, y, w, h = parts
        except (TypeError, ValueError):
            return
        lay["content_x_mm"] = max(0.0, x)
        lay["content_y_mm"] = max(0.0, y)
        lay["content_w_mm"] = max(MIN_HANDLE_MM, w)
        lay["content_h_mm"] = max(MIN_HANDLE_MM, h)
        self._layout = L.clamp_to_stock(lay)
        self.update()
        if callable(self.changed):
            self.changed(dict(self._layout))

    def _context_menu(self, pos) -> None:
        m = QMenu(self)
        a_reset = m.addAction("Reset to 3 mm")
        a_fit = m.addAction("Fit to printable")
        a_copy = m.addAction("Copy size")
        act = m.exec(pos)
        if act == a_reset:
            self._layout = L.reset_margins(self._layout)
        elif act == a_fit and self._printable:
            try:
                self._layout["content_x_mm"] = round(
                    float(self._printable.get("origin_x_mm", 0) or 0), 2)
                self._layout["content_y_mm"] = round(
                    float(self._printable.get("origin_y_mm", 0) or 0), 2)
                self._layout["content_w_mm"] = round(
                    float(self._printable.get("print_w_mm", 0) or 0), 2)
                self._layout["content_h_mm"] = round(
                    float(self._printable.get("print_h_mm", 0) or 0), 2)
                self._layout = L.clamp_to_stock(self._layout)
            except (TypeError, ValueError):
                return
        elif act == a_copy:
            try:
                from PySide6.QtWidgets import QApplication
                lay = self._layout
                QApplication.clipboard().setText(
                    f"{lay['content_w_mm']} x {lay['content_h_mm']} mm")
            except Exception:
                return
        else:
            return
        self.update()
        if callable(self.changed):
            self.changed(dict(self._layout))

    def wheelEvent(self, ev) -> None:
        try:
            steps = ev.angleDelta().y() / 120.0
        except Exception:
            steps = 0.0
        if steps:
            self._zoom = max(0.25, min(4.0, self._zoom * (1.12 ** steps)))
            self.update()

    def keyPressEvent(self, ev) -> None:
        step = 1.0 if (ev.modifiers() & Qt.ShiftModifier) else 0.1
        k = ev.key()
        if k == Qt.Key_0 and (ev.modifiers() & Qt.ControlModifier):
            self._zoom = 1.0
            self.update()
            return
        moved = True
        if k == Qt.Key_Left:
            self._layout["content_x_mm"] = round(
                max(0.0, float(self._layout["content_x_mm"]) - step), 2)
        elif k == Qt.Key_Right:
            self._layout["content_x_mm"] = round(
                float(self._layout["content_x_mm"]) + step, 2)
        elif k == Qt.Key_Up:
            self._layout["content_y_mm"] = round(
                max(0.0, float(self._layout["content_y_mm"]) - step), 2)
        elif k == Qt.Key_Down:
            self._layout["content_y_mm"] = round(
                float(self._layout["content_y_mm"]) + step, 2)
        else:
            moved = False
        if moved:
            self._layout = L.clamp_to_stock(self._layout)
            self.update()
            if callable(self.changed):
                self.changed(dict(self._layout))
        else:
            super().keyPressEvent(ev)


class MiniLayoutPreview(QWidget):
    """Read-only stock/printable/content overview for print dialogs."""

    def __init__(self, template_id: str, parent=None):
        super().__init__(parent)
        self._tid = str(template_id or "reel")
        self._layout: dict | None = None
        self._printable: dict | None = None
        self.setMinimumHeight(96)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.refresh()

    def refresh(self) -> None:
        try:
            self._layout = DA.get_label_layout(self._tid)
        except Exception:
            self._layout = None
        try:
            from factory.hardware import config as _cfg
            pname = str((_cfg.load().get("printer_name") or ""))
            self._printable = DA.get_printer_probe(pname) if pname else None
        except Exception:
            self._printable = None
        self.update()

    def status_text(self) -> str:
        if not self._layout:
            return "layout unavailable"
        return L.status_line(self._layout, self._printable)

    def paintEvent(self, ev) -> None:
        p = QPainter(self)
        try:
            p.fillRect(self.rect(), QColor("#F8FAFC"))
            if not self._layout:
                p.drawText(self.rect(), Qt.AlignCenter, "layout unavailable")
                return
            lay = self._layout
            sw = float(lay["stock_w_mm"])
            sh = float(lay["stock_h_mm"])
            pad = 8.0
            s = min((self.width() - pad * 2) / sw,
                    (self.height() - pad * 2 - 16) / sh)
            ox = (self.width() - sw * s) / 2
            oy = pad + (self.height() - pad * 2 - 16 - sh * s) / 2

            def R(x, y, w, h):
                return QRectF(ox + x * s, oy + y * s, w * s, h * s)

            p.fillRect(R(0, 0, sw, sh), QColor("#FFFFFF"))
            p.setPen(QPen(QColor("#0F172A"), 1.0))
            p.drawRect(R(0, 0, sw, sh))
            if self._printable:
                try:
                    px = float(self._printable.get("origin_x_mm", 0) or 0)
                    py = float(self._printable.get("origin_y_mm", 0) or 0)
                    pw = float(self._printable.get("print_w_mm", 0) or 0)
                    ph = float(self._printable.get("print_h_mm", 0) or 0)
                    p.setPen(Qt.NoPen)
                    p.setBrush(QBrush(QColor("#CBD5E1"), Qt.Dense4Pattern))
                    for sx, sy, ww, hh in (
                            (0, 0, px, sh), (px + pw, 0, sw - (px + pw), sh),
                            (px, 0, pw, py), (px, py + ph, pw, sh - (py + ph))):
                        if ww > 0.01 and hh > 0.01:
                            p.fillRect(R(sx, sy, ww, hh),
                                       QBrush(QColor("#CBD5E1"),
                                              Qt.Dense4Pattern))
                except Exception:
                    pass
            p.setPen(QPen(ACCENT, 1.6))
            p.setBrush(QBrush(QColor(79, 70, 229, 36)))
            p.drawRect(R(float(lay["content_x_mm"]),
                         float(lay["content_y_mm"]),
                         float(lay["content_w_mm"]),
                         float(lay["content_h_mm"])))
            f = QFont("Consolas, Menlo, monospace")
            f.setPointSize(7)
            p.setFont(f)
            p.setPen(QColor("#334155"))
            tok = L.status_token(lay, self._printable)
            col = {"OK": "#166534", "TIGHT": "#92400E",
                   "WON'T FIT": "#991B1B"}.get(tok, "#64748B")
            p.setPen(QColor(col))
            p.drawText(QRectF(0, self.height() - 16, self.width(), 16),
                       Qt.AlignCenter, tok)
        finally:
            p.end()


class LabelLayoutDialog(QDialog):
    """Full editor: canvas left (~70%), controls right (~30%)."""

    def __init__(self, parent=None, template_id: str = "reel",
                 printer_name: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Label layout — stock + content area")
        self.setMinimumSize(1140, 720)
        self._printer = str(printer_name or "")
        self._units_factor = 1.0  # display multiplier is handled per-field

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(8)
        top.addWidget(QLabel("Template:"))
        self.template_combo = QComboBox()
        self.template_combo.addItems(list(L.TEMPLATES))
        self.template_combo.setCurrentText(
            template_id if template_id in L.TEMPLATES else "reel")
        self.template_combo.currentTextChanged.connect(self._switch_template)
        top.addWidget(self.template_combo)
        top.addWidget(QLabel("Printer:"))
        self.printer_combo = QComboBox()
        self.printer_combo.setMinimumWidth(220)
        try:
            from factory.hardware.qtprint import available_printers
            names = available_printers()
        except Exception:
            names = []
        if self._printer and self._printer not in names:
            names = [self._printer] + names
        if names:
            self.printer_combo.addItems(names)
            if self._printer in names:
                self.printer_combo.setCurrentText(self._printer)
        else:
            self.printer_combo.addItem("(no printer found)")
            self.printer_combo.setEnabled(False)
        self.printer_combo.currentTextChanged.connect(self._printer_changed)
        top.addWidget(self.printer_combo, 1)
        outer.addLayout(top)

        body = QHBoxLayout()
        body.setSpacing(10)
        self.canvas = LayoutCanvas()
        self.canvas.changed = self._from_canvas
        body.addWidget(self.canvas, 7)

        side = QVBoxLayout()
        side.setSpacing(8)

        def _form(box) -> QFormLayout:
            """Form rows that never squeeze or overlap at 400px panel width."""
            f = QFormLayout(box)
            f.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
            f.setRowWrapPolicy(QFormLayout.DontWrapRows)
            f.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
            f.setFormAlignment(Qt.AlignLeft | Qt.AlignTop)
            return f

        # stock group
        stock_box = QGroupBox("Stock")
        sf = _form(stock_box)
        self.stock_w = QDoubleSpinBox()
        self.stock_h = QDoubleSpinBox()
        for sp in (self.stock_w, self.stock_h):
            sp.setRange(1.0, 1000.0)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
        self.stock_w.valueChanged.connect(lambda v: self._stock_changed())
        self.stock_h.valueChanged.connect(lambda v: self._stock_changed())
        sf.addRow("Width", self.stock_w)
        sf.addRow("Height", self.stock_h)
        self.dpi_combo = QComboBox()
        self.dpi_combo.addItems(["152", "200", "203", "300", "400", "600"])
        self.dpi_combo.currentTextChanged.connect(self._dpi_changed)
        sf.addRow("DPI", self.dpi_combo)
        self.preset_combo = QComboBox()
        self.preset_combo.addItems([p[0] for p in L.PRESETS])
        self.preset_combo.currentTextChanged.connect(self._preset_changed)
        sf.addRow("Preset", self.preset_combo)
        side.addWidget(stock_box)

        # margins group
        marg_box = QGroupBox("Margins")
        mf = _form(marg_box)
        self.m_left = QDoubleSpinBox()
        self.m_top = QDoubleSpinBox()
        self.m_right = QDoubleSpinBox()
        self.m_bottom = QDoubleSpinBox()
        for sp in (self.m_left, self.m_top, self.m_right, self.m_bottom):
            sp.setRange(0.0, 500.0)
            sp.setDecimals(1)
            sp.setSingleStep(0.5)
        self.m_left.valueChanged.connect(lambda v: self._margin_changed("l", v))
        self.m_top.valueChanged.connect(lambda v: self._margin_changed("t", v))
        self.m_right.valueChanged.connect(lambda v: self._margin_changed("r", v))
        self.m_bottom.valueChanged.connect(
            lambda v: self._margin_changed("b", v))
        mf.addRow("Left", self.m_left)
        mf.addRow("Top", self.m_top)
        mf.addRow("Right", self.m_right)
        mf.addRow("Bottom", self.m_bottom)
        link_row = QHBoxLayout()
        self.chain = QCheckBox("Link all four")
        self.chain.setChecked(True)
        self.chain.toggled.connect(self._chain_toggled)
        link_row.addWidget(self.chain)
        self.btn_reset = QPushButton("Reset 3 mm")
        style_button(self.btn_reset, "secondary")
        self.btn_reset.clicked.connect(self._reset_margins)
        link_row.addWidget(self.btn_reset)
        mf.addRow(link_row)
        quick = QHBoxLayout()
        for q in (0, 2, 3, 5):
            b = QPushButton(str(q))
            b.setMaximumWidth(44)
            style_button(b, "secondary")
            b.clicked.connect(lambda _=False, v=float(q): self._quick(v))
            quick.addWidget(b)
        quick.addStretch(1)
        mf.addRow("Quick", quick)
        side.addWidget(marg_box)

        # result group
        res_box = QGroupBox("Result")
        rf = _form(res_box)
        self.content_lbl = QLabel("—")
        self.content_lbl.setWordWrap(True)
        rf.addRow("Content", self.content_lbl)
        self.units_combo = QComboBox()
        self.units_combo.addItems(["mm", "inch"])
        self.units_combo.currentTextChanged.connect(self._units_changed)
        rf.addRow("Units", self.units_combo)
        self.coils_spin = QSpinBox()
        self.coils_spin.setRange(1, 60)
        self.coils_spin.setValue(3)
        self.coils_spin.valueChanged.connect(lambda _v: self._refresh_preview())
        rf.addRow("Sample coils", self.coils_spin)
        side.addWidget(res_box)

        # driver group (read-only probe + repair)
        drv_box = QGroupBox("Driver")
        df = QVBoxLayout(drv_box)
        self.probe_lbl = QLabel("not probed")
        self.probe_lbl.setWordWrap(True)
        self.probe_lbl.setProperty("hint", True)
        df.addWidget(self.probe_lbl)
        drv_row = QHBoxLayout()
        self.btn_probe = QPushButton("Probe")
        style_button(self.btn_probe, "secondary")
        self.btn_probe.clicked.connect(self._probe)
        self.btn_repair = QPushButton("Repair paper")
        style_button(self.btn_repair, "secondary")
        self.btn_repair.clicked.connect(self._repair)
        drv_row.addWidget(self.btn_probe)
        drv_row.addWidget(self.btn_repair)
        df.addLayout(drv_row)
        side.addWidget(drv_box)

        # options group
        opt_box = QGroupBox("Options")
        of = QVBoxLayout(opt_box)
        self.aspect_cb = QCheckBox("Lock aspect ratio")
        self.aspect_cb.toggled.connect(self._aspect_toggled)
        of.addWidget(self.aspect_cb)
        fit_row = QHBoxLayout()
        fit_row.addWidget(QLabel("Fit"))
        self.fit_combo = QComboBox()
        self.fit_combo.addItems(["reflow", "stretch"])
        self.fit_combo.currentTextChanged.connect(self._fit_changed)
        fit_row.addWidget(self.fit_combo, 1)
        of.addLayout(fit_row)
        self.overlay_cb = QCheckBox("Show printable overlay")
        self.overlay_cb.setChecked(True)
        self.overlay_cb.toggled.connect(self._overlay_toggled)
        of.addWidget(self.overlay_cb)
        self.grid_cb = QCheckBox("Show grid")
        self.grid_cb.setChecked(True)
        self.grid_cb.toggled.connect(
            lambda on: (setattr(self.canvas, "_show_grid", bool(on)),
                        self.canvas.update()))
        of.addWidget(self.grid_cb)
        side.addWidget(opt_box)

        # actions
        act_box = QGroupBox("Actions")
        af = QVBoxLayout(act_box)
        self.btn_test = QPushButton("Print test label")
        style_button(self.btn_test, "primary")
        self.btn_test.clicked.connect(self._print_test)
        af.addWidget(self.btn_test)
        prof_row = QHBoxLayout()
        self.btn_export = QPushButton("Export…")
        style_button(self.btn_export, "secondary")
        self.btn_export.clicked.connect(self._export)
        self.btn_import = QPushButton("Import…")
        style_button(self.btn_import, "secondary")
        self.btn_import.clicked.connect(self._import)
        prof_row.addWidget(self.btn_export)
        prof_row.addWidget(self.btn_import)
        af.addLayout(prof_row)
        save_row = QHBoxLayout()
        self.btn_save = QPushButton("Save")
        style_button(self.btn_save, "accent")
        self.btn_save.clicked.connect(self._save)
        self.btn_cancel = QPushButton("Cancel")
        style_button(self.btn_cancel, "secondary")
        self.btn_cancel.clicked.connect(self.reject)
        save_row.addWidget(self.btn_save)
        save_row.addWidget(self.btn_cancel)
        af.addLayout(save_row)
        side.addWidget(act_box)
        side.addStretch(1)

        # Fixed-width scrollable panel: rows can scroll vertically on short
        # windows but never squeeze horizontally, so labels and fields can
        # never overlap however small the dialog gets.
        side_holder = QWidget()
        side_holder.setLayout(side)
        for _cls in (QDoubleSpinBox, QSpinBox, QComboBox):
            for _w in side_holder.findChildren(_cls):
                _w.setMinimumWidth(150)
        scroll = QScrollArea()
        scroll.setWidget(side_holder)
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setFixedWidth(400)
        body.addWidget(scroll, 0)
        outer.addLayout(body, 1)

        self.status = QLabel("")
        mono = QFont("Consolas, Menlo, monospace")
        mono.setPointSize(8)
        self.status.setFont(mono)
        self.status.setWordWrap(True)
        outer.addWidget(self.status)

        self._loading: dict | None = None
        self._printable: dict | None = None
        self._suppress = False
        self._load_template(self.template_combo.currentText())

    # ----- unit helpers -----
    def _unit(self) -> str:
        return str(self.units_combo.currentText() or "mm")

    def _to_disp(self, mm: float) -> float:
        return round(float(mm) / 25.4, 3) if self._unit() == "inch" else mm

    def _to_mm(self, v: float) -> float:
        return float(v) * 25.4 if self._unit() == "inch" else float(v)

    # ----- load / sync -----
    def _load_template(self, tid: str) -> None:
        try:
            lay = DA.get_label_layout(tid)
        except Exception as e:
            QMessageBox.warning(self, "Layout", f"Could not load layout:\n{e}")
            return
        self._loading = dict(lay)
        self._printable = self._cached_probe()
        if tid == "client":
            self._sync_client_height()
        self._sync_controls()
        self._refresh_preview()
        self._refresh_status()

    def _cached_probe(self) -> dict | None:
        try:
            name = self._printer_name()
            if not name:
                return None
            return DA.get_printer_probe(name)
        except Exception:
            return None

    def _printer_name(self) -> str:
        try:
            if not self.printer_combo.isEnabled():
                return self._printer
            return str(self.printer_combo.currentText() or "").strip()
        except Exception:
            return self._printer

    def _switch_template(self, tid: str) -> None:
        self._load_template(tid)

    def _printer_changed(self, _v: str) -> None:
        self._printable = self._cached_probe()
        self._refresh_preview()
        self._refresh_status()

    def _sync_client_height(self) -> None:
        """Client content height follows the coil count (reflow)."""
        if not self._loading or self._loading.get("template_id") != "client":
            return
        try:
            from factory.hardware.labels import wide_label_height_mm
            h = wide_label_height_mm(int(self.coils_spin.value()))
            self._loading["content_h_mm"] = round(h - 2 * float(
                self._loading.get("content_y_mm") or 3.0), 2)
            self._loading = L.clamp_to_stock(self._loading)
        except Exception:
            pass

    def _sync_controls(self) -> None:
        lay = self._loading or {}
        self._suppress = True
        try:
            self.stock_w.setValue(float(lay.get("stock_w_mm") or 0))
            self.stock_h.setValue(float(lay.get("stock_h_mm") or 0))
            dpi = str(int(lay.get("dpi") or 203))
            if dpi in [self.dpi_combo.itemText(i)
                       for i in range(self.dpi_combo.count())]:
                self.dpi_combo.setCurrentText(dpi)
            m = L.margins_of(lay)
            u = self._unit()
            f = (lambda v: round(v / 25.4, 3)) if u == "inch" else (lambda v: v)
            for sp, k in ((self.m_left, "left"), (self.m_top, "top"),
                          (self.m_right, "right"), (self.m_bottom, "bottom")):
                sp.setSuffix(" in" if u == "inch" else " mm")
                sp.setValue(f(m[k]))
            self.chain.setChecked(bool(lay.get("margin_locked", True)))
            if str(lay.get("units") or "mm") in ("mm", "inch"):
                self.units_combo.setCurrentText(str(lay["units"]))
            self.fit_combo.setCurrentText(str(lay.get("fit_mode") or "reflow"))
            self.aspect_cb.setChecked(bool(getattr(self.canvas,
                                                   "_aspect_lock", False)))
            self.canvas.set_model(lay, self._printable)
        finally:
            self._suppress = False

    def _refresh_preview(self) -> None:
        lay = self._loading or {}
        if lay.get("template_id") == "client":
            self._sync_client_height()
            lay = self._loading or {}
        try:
            detail = L.sample_detail(
                str(lay.get("template_id") or "reel"),
                int(self.coils_spin.value()))
            art = L.render_content(str(lay.get("template_id") or "reel"),
                                   detail, lay)
            px = _pixmap_of(art)
            dpi = int(lay.get("dpi") or 203)
            self.canvas.set_model(
                lay, self._printable, px,
                (art.width / dpi * 25.4, art.height / dpi * 25.4))
        except Exception:
            self.canvas.set_model(lay, self._printable)
        self._refresh_readouts()

    def _refresh_readouts(self) -> None:
        lay = self._loading or {}
        try:
            cw, ch = L.content_px(lay)
            self.content_lbl.setText(
                f"{float(lay.get('content_w_mm') or 0):.1f} x "
                f"{float(lay.get('content_h_mm') or 0):.1f} mm "
                f"({cw} x {ch} dots)")
        except Exception:
            self.content_lbl.setText("—")

    def _refresh_status(self) -> None:
        lay = self._loading or {}
        line = L.status_line(lay, self._printable)
        tok = L.status_token(lay, self._printable)
        col = {"OK": "#166534", "TIGHT": "#92400E",
               "WON'T FIT": "#991B1B"}.get(tok, "#64748B")
        self.status.setText(line)
        self.status.setStyleSheet(f"color: {col};")
        issues = L.validate(lay, self._printable)
        if issues:
            self.status.setToolTip("\n".join(
                f"{i['severity']}: {i['message']}" for i in issues))
        else:
            self.status.setToolTip("")

    def _commit_canvas(self, lay: dict) -> None:
        self._loading = dict(lay)
        self._suppress = True
        try:
            m = L.margins_of(lay)
            f = ((lambda v: round(v / 25.4, 3))
                 if self._unit() == "inch" else (lambda v: v))
            self.m_left.setValue(f(m["left"]))
            self.m_top.setValue(f(m["top"]))
            self.m_right.setValue(f(m["right"]))
            self.m_bottom.setValue(f(m["bottom"]))
        finally:
            self._suppress = False
        self._refresh_preview()
        self._refresh_status()

    def _from_canvas(self, lay: dict) -> None:
        self._commit_canvas(lay)

    # ----- control slots -----
    def _stock_changed(self) -> None:
        if self._suppress or not self._loading:
            return
        self._loading["stock_w_mm"] = round(float(self.stock_w.value()), 2)
        self._loading["stock_h_mm"] = round(float(self.stock_h.value()), 2)
        self._loading = L.clamp_to_stock(self._loading)
        self._refresh_preview()
        self._refresh_status()

    def _dpi_changed(self, v: str) -> None:
        if self._suppress or not self._loading:
            return
        try:
            new_dpi = int(v)
        except (TypeError, ValueError):
            return
        self._loading["dpi"] = new_dpi  # mm values unchanged by design
        self._refresh_preview()
        self._refresh_status()

    def _preset_changed(self, name: str) -> None:
        if self._suppress or not self._loading:
            return
        for p_name, w, h in L.PRESETS:
            if p_name == name and w and h:
                self._loading["stock_w_mm"] = w
                self._loading["stock_h_mm"] = h
                self._loading = L.reset_margins(self._loading)
                self._sync_controls()
                self._refresh_preview()
                self._refresh_status()
                return

    def _margin_changed(self, _side: str, _v: float) -> None:
        if self._suppress or not self._loading:
            return
        cur = {k: self._to_mm(float(sp.value())) for k, sp in (
            ("left", self.m_left), ("top", self.m_top),
            ("right", self.m_right), ("bottom", self.m_bottom))}
        if self.chain.isChecked():
            v = cur[{"l": "left", "t": "top", "b": "bottom"}.get(
                _side, "left")]
            # chain: the edited side sets all four
            if _side == "t":
                v = cur["top"]
            elif _side == "r":
                v = cur["right"]
            elif _side == "b":
                v = cur["bottom"]
            else:
                v = cur["left"]
            self._loading = L.set_margins(self._loading, v, v, v, v, True)
        else:
            self._loading = L.set_margins(
                self._loading, cur["left"], cur["top"],
                cur["right"], cur["bottom"], False)
        self._suppress = True
        try:
            m = L.margins_of(self._loading)
            f = ((lambda v: round(v / 25.4, 3))
                 if self._unit() == "inch" else (lambda v: v))
            self.m_left.setValue(f(m["left"]))
            self.m_top.setValue(f(m["top"]))
            self.m_right.setValue(f(m["right"]))
            self.m_bottom.setValue(f(m["bottom"]))
        finally:
            self._suppress = False
        self.canvas.set_model(self._loading, self._printable)
        self._refresh_preview()
        self._refresh_status()

    def _chain_toggled(self, on: bool) -> None:
        if not self._loading:
            return
        self._loading["margin_locked"] = bool(on)
        if on:
            self._reset_margins()

    def _reset_margins(self) -> None:
        if not self._loading:
            return
        self._loading = L.reset_margins(self._loading, 3.0)
        self._loading["margin_locked"] = self.chain.isChecked()
        self._sync_controls()
        self._refresh_preview()
        self._refresh_status()

    def _quick(self, v: float) -> None:
        if not self._loading:
            return
        self._loading = L.set_margins(self._loading, v, v, v, v, True)
        self.chain.setChecked(True)
        self._sync_controls()
        self._refresh_preview()
        self._refresh_status()

    def _units_changed(self, u: str) -> None:
        if self._loading:
            self._loading["units"] = str(u)
        self._sync_controls()
        self._refresh_status()

    def _aspect_toggled(self, on: bool) -> None:
        self.canvas._aspect_lock = bool(on)

    def _fit_changed(self, v: str) -> None:
        if self._loading and not self._suppress:
            self._loading["fit_mode"] = str(v)

    def _overlay_toggled(self, on: bool) -> None:
        self.canvas._show_printable = bool(on)
        self.canvas.update()

    # ----- driver -----
    def _probe(self) -> None:
        name = self._printer_name()
        if not name:
            return QMessageBox.warning(self, "Probe", "Pick a printer first.")
        lay = self._loading or {}
        try:
            from factory.hardware.qtprint import QtCitizenCLS621
            ox = oy = 0.0
            if self._printable:
                try:
                    ox = float(self._printable.get("origin_x_mm") or 0.0)
                    oy = float(self._printable.get("origin_y_mm") or 0.0)
                except (TypeError, ValueError):
                    ox = oy = 0.0
            g = QtCitizenCLS621.probe(
                name, float(lay.get("stock_w_mm") or 0),
                float(lay.get("stock_h_mm") or 0),
                int(lay.get("dpi") or 203), ox, oy)
        except Exception as e:
            return QMessageBox.warning(self, "Probe",
                                       f"Could not probe {name!r}:\n{e}")
        self._printable = {"origin_x_mm": g["origin_x_mm"],
                           "origin_y_mm": g["origin_y_mm"],
                           "print_w_mm": g["print_w_mm"],
                           "print_h_mm": g["print_h_mm"],
                           "ticket_delta_mm": g.get("ticket_delta_mm", 0),
                           "dpi": g.get("dpi", 203),
                           "probed_at": g.get("probed_at", "")}
        try:
            from factory.hardware import layout as _L
            from factory import services as _svc
            for k, v in _L.printable_to_settings(name, self._printable).items():
                try:
                    _svc.set_setting(k, str(v))
                except Exception:
                    pass
        except Exception:
            pass
        self.probe_lbl.setText(
            f"origin {g['origin_x_mm']:.1f},{g['origin_y_mm']:.1f}mm  "
            f"printable {g['print_w_mm']:.1f}x{g['print_h_mm']:.1f}mm  "
            f"ticket {g['ticket_w_mm']:.1f}x{g['ticket_h_mm']:.1f}mm  "
            f"dpi {g['dpi']}")
        self.canvas.set_model(self._loading or {}, self._printable)
        self._refresh_status()

    def _repair(self) -> None:
        name = self._printer_name()
        if not name:
            return QMessageBox.warning(self, "Repair", "Pick a printer first.")
        lay = self._loading or {}
        try:
            from factory.hardware.qtprint import QtCitizenCLS621
            g, state = QtCitizenCLS621.ensure_paper(
                name, float(lay.get("stock_w_mm") or 0),
                float(lay.get("stock_h_mm") or 0),
                int(lay.get("dpi") or 203))
        except Exception as e:
            return QMessageBox.warning(self, "Repair",
                                       f"Could not repair {name!r}:\n{e}")
        self._printable = {"origin_x_mm": g["origin_x_mm"],
                           "origin_y_mm": g["origin_y_mm"],
                           "print_w_mm": g["print_w_mm"],
                           "print_h_mm": g["print_h_mm"],
                           "ticket_delta_mm": g.get("ticket_delta_mm", 0),
                           "dpi": g.get("dpi", 203),
                           "probed_at": g.get("probed_at", "")}
        self.probe_lbl.setText(
            f"state={state}  origin {g['origin_x_mm']:.1f},"
            f"{g['origin_y_mm']:.1f}mm  printable {g['print_w_mm']:.1f}x"
            f"{g['print_h_mm']:.1f}mm  ticket {g['ticket_w_mm']:.1f}x"
            f"{g['ticket_h_mm']:.1f}mm")
        self.canvas.set_model(self._loading or {}, self._printable)
        self._refresh_status()

    # ----- actions -----
    def _print_test(self) -> None:
        name = self._printer_name()
        if not name:
            return QMessageBox.warning(self, "Test print",
                                       "Pick a printer first.")
        lay = self._loading or {}
        try:
            art = L.render_test_pattern(lay)
            from factory.hardware.qtprint import QtCitizenCLS621
            ok = QtCitizenCLS621(name).print_label(
                str(lay.get("template_id") or "reel"), art,
                "Simpld layout test", dict(lay))
        except Exception as e:
            return QMessageBox.warning(self, "Test print",
                                       f"Could not print:\n{e}")
        if not ok:
            return QMessageBox.warning(self, "Test print",
                                       "Could not print. Check the printer.")
        QMessageBox.information(
            self, "Test print",
            "Measure the four paper margins — they should match the "
            "status bar within 0.5 mm.")

    def _export(self) -> None:
        lay = self._loading or {}
        data = L.export_profile(str(lay.get("template_id") or "reel"),
                                lay, self._printable)
        import json as _json
        path, _ = QFileDialog.getSaveFileName(
            self, "Export label profile",
            f"label-{data['template_id']}.json", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                _json.dump(data, f, indent=2)
        except OSError as e:
            return QMessageBox.warning(self, "Export",
                                       f"Could not write:\n{e}")

    def _import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import label profile", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = L.parse_profile_text(f.read())
        except (OSError, ValueError) as e:
            return QMessageBox.warning(self, "Import",
                                       f"Not a label profile:\n{e}")
        ans = QMessageBox.question(
            self, "Import profile", L.bundle_summary(data)
            + "\n\nApply?")
        if ans != QMessageBox.StandardButton.Yes:
            return
        applied = 0
        for p in L.bundle_profiles(data):
            try:
                _tid, lay, printable = L.import_profile(p)
            except ValueError:
                continue
            lay["template_id"] = _tid
            try:
                DA.save_label_layout(_tid, lay)
                applied += 1
            except Exception:
                continue
            if _tid == str((self._loading or {}).get("template_id") or ""):
                self._loading = lay
                if printable:
                    self._printable = printable
        self._sync_controls()
        self._refresh_preview()
        self._refresh_status()
        QMessageBox.information(self, "Import",
                                f"Applied {applied} template(s).")

    def _save(self) -> None:
        lay = self._loading or {}
        issues = L.fit_report(lay, self._printable)
        if issues:
            return QMessageBox.warning(
                self, "Layout will not fit",
                "Content " + "; ".join(issues) + ".\n"
                "Move the content inside the printable area, use "
                "'Fit to printable' (right-click), or press 'Repair paper'.")
        try:
            DA.save_label_layout(str(lay.get("template_id") or "reel"), lay)
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Save", str(e))
        except Exception as e:
            try:
                from factory.data_access import describe_error
                msg, _kind = describe_error(e)
            except Exception:
                msg = str(e)
            return QMessageBox.warning(self, "Save", f"Could not save:\n{msg}")
        self.accept()


def open_layout_editor(parent, template_id: str = "reel",
                       printer_name: str = "") -> bool:
    return LabelLayoutDialog(parent, template_id, printer_name).exec() == QDialog.Accepted
