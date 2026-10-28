"""Packaging workspace: Reel packing + Box packing.

Reel packing: the operator weighs one reel -> Type-1 sticker (NO QR). The
manual core weight applies to every reel of the session and is never reset
by saving.

Box packing: the operator weighs the packed box; the next loose reels in
production order are consumed into it, the manual box/tare weight applies to
every box, and the sticker is queued (never printed on pack) for one-by-one
printing from the queue.
"""

from __future__ import annotations
import statistics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                               QPushButton, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
                               QHeaderView, QMessageBox, QLineEdit, QSplitter, QFrame,
                               QAbstractItemView, QScrollArea, QSizePolicy, QSpinBox,
                               QStackedWidget)
from PySide6.QtCore import Qt
from factory import data_access as DA
from factory.db import bare_carton_no
from factory.hardware import mock as HW
from factory.hardware import devices as DEV
from factory.hardware import labels as LabelArt
from factory.ui.label_preview import (
    ReelLabelDialog, CartonLabelDialog, pil_to_pixmap, set_preview_label,
    PreviewLabel,
)
from factory.ui.styles import style_button, status_color
from factory.ui.device_strip import DeviceStrip

# Fixed Citizen media: 4in x 3in (see hardware/labels.py). No size options.
PRINTER_WIDTH_MM = LabelArt.LABEL_WIDTH_MM
PRINTER_HEIGHT_MM = LabelArt.LABEL_HEIGHT_MM

# Preview renders wider so the whole 100x50mm sticker fits vertically.
PREVIEW_MAX_WIDTH = 440


def _toast(parent, text: str, ok: bool = True, kind: str | None = None):
    """Brief success/error note. Replaces the old persistent banner."""
    try:
        from factory.ui.toast import toast as _show
        _show(parent, text, ok=ok, kind=kind)
    except Exception:
        pass


def _unit_of(obj: dict) -> str:
    """Product/job unit: 'pieces' | 'kg' | '' (legacy unknown, convertible).

    Explicit unit wins; otherwise inferred from the wrappers count so old
    rows keep their meaning (counted -> pieces, blank -> unknown).
    """
    try:
        u = str((obj or {}).get("unit") or "").strip().lower()
    except Exception:
        u = ""
    if u in ("pieces", "kg"):
        return u
    try:
        return "pieces" if (obj or {}).get("wrappers_per_reel") else ""
    except Exception:
        return ""


def _unit_label(obj: dict) -> str:
    """Short floor label: '16500/reel' | 'kg only' | 'pieces unknown'."""
    u = _unit_of(obj)
    if u == "kg":
        return "kg only"
    try:
        w = (obj or {}).get("wrappers_per_reel")
        if w:
            return f"{int(w)}/reel"
    except (TypeError, ValueError):
        pass
    return "pieces unknown" if u == "" else "kg only"


class NewJobDialogMixin:
    @staticmethod
    def ask_new_product(parent, initial_name: str = "", initial_wrappers: str = "",
                        initial_unit: str = "pieces"):
        """Add-once product entry with explicit unit (Appy Fizz = pieces,
        Bailey = kg). Retains typed values across validation failures."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit, QComboBox,
                                       QDialogButtonBox, QLabel)
        name_val, wrap_val, unit_val = initial_name, initial_wrappers, initial_unit
        while True:
            d = QDialog(parent)
            d.setWindowTitle("New product")
            f = QFormLayout(d)
            name = QLineEdit()
            name.setPlaceholderText("e.g. APPY FIZZ-160ML (SEP.28) BODY")
            name.setMinimumWidth(340)
            name.setText(name_val)
            unit = QComboBox()
            unit.addItem("Counted in pieces (e.g. Appy Fizz)", userData="pieces")
            unit.addItem("Weighed in kg only (e.g. Bailey)", userData="kg")
            unit.setCurrentIndex(0 if unit_val != "kg" else 1)
            wrappers = QLineEdit()
            wrappers.setPlaceholderText("e.g. 16500")
            wrappers.setText(wrap_val)
            f.addRow("Product Name:", name)
            f.addRow("Calculated via:", unit)
            f.addRow("Pieces / reel:", wrappers)
            hint = QLabel("Kg products never use piece counts — dispatch, targets"
                          " and labels stay in kg.")
            hint.setWordWrap(True)
            hint.setProperty("hint", True)
            f.addRow("", hint)
            def _sync_unit():
                is_kg = unit.currentData() == "kg"
                wrappers.setEnabled(not is_kg)
                if is_kg:
                    wrappers.setPlaceholderText("kg product — no count")
                else:
                    wrappers.setPlaceholderText("e.g. 16500")
            unit.currentIndexChanged.connect(lambda _i: _sync_unit())
            _sync_unit()
            bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            f.addRow(bb)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            if d.exec() != QDialog.Accepted:
                return None
            name_val = name.text()
            unit_val = unit.currentData() or "pieces"
            wrap_val = "" if unit_val == "kg" else wrappers.text()
            try:
                return DA.create_product(name_val, wrap_val or None, unit=unit_val)
            except PermissionError as e:
                QMessageBox.warning(parent, "Cannot save product", str(e))
                return None
            except ValueError as e:
                QMessageBox.warning(parent, "Cannot save product", str(e))
                continue  # loop with values retained above
            except Exception as e:
                try:
                    msg, _k = DA.describe_error(e)
                except Exception:
                    msg = str(e)
                QMessageBox.warning(parent, "Cannot save product", msg)
                return None

    @staticmethod
    def ask_set_wrappers(parent, product: dict):
        """Set pieces for a migrated-unknown product. Catalog only —
        historical job snapshots keep their NULL and are never rewritten."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit,
                                       QDialogButtonBox, QLabel)
        wrap_val = ""
        while True:
            d = QDialog(parent)
            d.setWindowTitle(f"Pieces — {product['name']}")
            f = QFormLayout(d)
            info = QLabel("Historical jobs keep 'unknown'; only future jobs use this.")
            info.setWordWrap(True)
            f.addRow(info)
            wrappers = QLineEdit()
            wrappers.setPlaceholderText("e.g. 16500")
            wrappers.setText(wrap_val or str(product.get("wrappers_per_reel") or ""))
            f.addRow("Pieces / reel:", wrappers)
            bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            f.addRow(bb)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            if d.exec() != QDialog.Accepted:
                return None
            wrap_val = wrappers.text()
            try:
                return DA.set_product_wrappers(product["id"], wrap_val)
            except PermissionError as e:
                QMessageBox.warning(parent, "Cannot save pieces", str(e))
                return None
            except ValueError as e:
                QMessageBox.warning(parent, "Cannot save pieces", str(e))
                continue
            except Exception as e:
                try:
                    msg, _k = DA.describe_error(e)
                except Exception:
                    msg = str(e)
                QMessageBox.warning(parent, "Cannot save pieces", msg)
                return None

    @staticmethod
    def ask_new_job(parent):
        """New packaging job from the products master. Retains the selected
        product and typed target across validation failures. No Size/Core
        fields (legacy size/core rows stay in the DB, core default unchanged).
        The single optional target is the product's own unit."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QComboBox, QPushButton,
                                       QDialogButtonBox, QHBoxLayout, QWidget, QLabel,
                                       QLineEdit)
        select_id = None
        t_unit_val = ""
        while True:
            d = QDialog(parent)
            d.setWindowTitle("New job")
            f = QFormLayout(d)
            combo = QComboBox()
            combo.setMinimumWidth(340)

            def _reload(sel=None):
                combo.blockSignals(True)
                combo.clear()
                try:
                    prods = DA.list_products()
                except Exception:
                    prods = []
                for p in prods:
                    combo.addItem(f"{p['name']} — {_unit_label(p)}", userData=p["id"])
                    if sel and p["id"] == sel:
                        combo.setCurrentIndex(combo.count() - 1)
                combo.blockSignals(False)

            _reload(select_id)
            add_btn = QPushButton("New product…")
            wrap_btn = QPushButton("Set pieces…")
            wrap_btn.setToolTip("Set pieces for an unknown product (history untouched).")
            def _add():
                _p = NewJobDialogMixin.ask_new_product(d)
                if _p:
                    _reload(_p["id"])
            def _wrap():
                pid = combo.currentData()
                try:
                    prods = {p["id"]: p for p in DA.list_products()}
                except Exception:
                    prods = {}
                prod = prods.get(pid)
                if not prod:
                    QMessageBox.warning(d, "Pieces", "Select a product first.")
                    return
                if _unit_of(prod) == "kg":
                    QMessageBox.warning(d, "Pieces",
                                        f"'{prod['name']}' is a kg product — pieces do not apply.")
                    return
                if prod.get("wrappers_per_reel"):
                    QMessageBox.warning(d, "Pieces",
                                        f"'{prod['name']}' already has"
                                        f" {prod['wrappers_per_reel']}/reel.")
                    return
                upd = NewJobDialogMixin.ask_set_wrappers(d, prod)
                if upd:
                    _reload(upd["id"])
            add_btn.clicked.connect(_add)
            wrap_btn.clicked.connect(_wrap)
            row = QWidget()
            hl = QHBoxLayout(row)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.addWidget(combo, 1)
            hl.addWidget(add_btn)
            hl.addWidget(wrap_btn)
            f.addRow("Product:", row)
            # One target at creation: the product's own unit (pieces or kg).
            # Reels/boxes needed are derived from it (pieces ÷ wrappers,
            # kg ÷ typical reel net) — never typed.
            t_unit = QLineEdit()
            t_unit.setText(t_unit_val)
            unit_lab = QLabel("Target:")
            def _unit_update():
                try:
                    prods = {p["id"]: p for p in DA.list_products()}
                except Exception:
                    prods = {}
                prod = prods.get(combo.currentData())
                if prod and _unit_of(prod) == "kg":
                    unit_lab.setText("Target net kg (optional):")
                    if not t_unit.text():
                        t_unit.setPlaceholderText("e.g. 500")
                else:
                    unit_lab.setText("Target pieces (wrappers, optional):")
                    if not t_unit.text():
                        t_unit.setPlaceholderText("e.g. 165000")
            combo.currentIndexChanged.connect(lambda _i: _unit_update())
            _unit_update()
            f.addRow(unit_lab, t_unit)
            hint = QLabel("Jobs run continuously across months; reel/box numbers restart at 1"
                          " each month for each product.")
            hint.setWordWrap(True)
            f.addRow(hint)
            bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            f.addRow(bb)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            if d.exec() != QDialog.Accepted:
                return None
            pid = combo.currentData()
            select_id = pid  # retain selection if the save fails validation
            if pid is None:
                QMessageBox.warning(parent, "Cannot save job",
                                    "Select a product first (or add it once, then reuse it).")
                continue
            # Retain typed target across validation failures.
            t_unit_val = t_unit.text()
            try:
                prods = {p["id"]: p for p in DA.list_products()}
            except Exception:
                prods = {}
            prod = prods.get(pid)
            is_piece = _unit_of(prod) != "kg"
            def _opt_num(v, whole=True):
                t = (v or "").strip()
                if not t:
                    return None
                try:
                    n = int(t) if whole else float(t)
                except (TypeError, ValueError):
                    return "bad"
                if whole and (isinstance(n, bool) or n <= 0):
                    return "bad"
                if not whole and not (n > 0):
                    return "bad"
                return n
            tu = _opt_num(t_unit_val, whole=is_piece)
            if tu == "bad":
                QMessageBox.warning(parent, "Cannot save job",
                                    "Target must be blank or a positive number.")
                continue
            try:
                job = DA.create_job_for_product(pid)
            except PermissionError as e:
                QMessageBox.warning(parent, "Cannot save job", str(e))
                return None
            except ValueError as e:
                QMessageBox.warning(parent, "Cannot save job", str(e))
                continue
            except Exception as e:
                try:
                    msg, _k = DA.describe_error(e)
                except Exception:
                    msg = str(e)
                QMessageBox.warning(parent, "Cannot save job", msg)
                return None
            if tu is not None:
                try:
                    if is_piece:
                        DA.set_job_targets(job["id"], target_pieces=tu)
                    else:
                        DA.set_job_targets(job["id"], target_weight_kg=tu)
                except Exception as e:
                    try:
                        msg, _k = DA.describe_error(e)
                    except Exception:
                        msg = str(e)
                    QMessageBox.warning(parent, "Target not saved",
                                        f"Job J:{job['job_no']} created, but target failed:\n{msg}")
            return job


class PackagingPage(QWidget, NewJobDialogMixin):
    """Packaging workspace: Reel packing + Box packing (nested nav pages).

    Reel packing: weigh one reel -> save -> print its Type-1 sticker (NO QR).
    The manual core weight applies to every reel of the session (it is not
    reset by each save).

    Box packing: the operator weighs the packed box; the next loose reels in
    production order (oldest first, session "reels per box") are consumed and
    the manual box/tare weight applies to every box of the session. Packing
    never prints: box stickers queue up and are printed one-by-one later.
    """

    def __init__(self, on_stock_changed=None, actor: dict | None = None):
        super().__init__()
        self.on_stock_changed = on_stock_changed
        # Caller-supplied actor dicts are NEVER trusted (forged-role bypass).
        # Authorization resolves from the authenticated session on every use.
        self._ignored_actor = actor
        self.job_id: int | None = None
        self._mode = "reel"
        self._core_job_id: int | None = None
        self._history_rows: list[dict] = []
        self._box_rows: list[dict] = []
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 12)
        root.setSpacing(8)

        title = QLabel("Packaging")
        title.setProperty("page", "title")
        root.addWidget(title)

        # ---- job bar ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        lab = QLabel("Job")
        lab.setProperty("field", "caption")
        bar.addWidget(lab)
        self.job_combo = QComboBox()
        self.job_combo.setMinimumHeight(40)
        self.job_combo.setMinimumWidth(280)
        self.job_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar.addWidget(self.job_combo, 1)
        self.btn_new = QPushButton("New job")
        self.btn_new.setMinimumHeight(40)
        style_button(self.btn_new, "secondary")
        bar.addWidget(self.btn_new)
        self.btn_product = QPushButton("+ Product")
        self.btn_product.setMinimumHeight(40)
        style_button(self.btn_product, "secondary")
        bar.addWidget(self.btn_product)
        self.btn_targets = QPushButton("Targets…")
        self.btn_targets.setMinimumHeight(40)
        style_button(self.btn_targets, "secondary")
        self.btn_targets.setToolTip("Set packaging targets (reels / boxes / kg / pieces) for this job.")
        bar.addWidget(self.btn_targets)
        self.job_info = QLabel("")
        self.job_info.setProperty("jobinfo", True)
        self.job_info.setWordWrap(True)
        self.job_info.setMinimumWidth(220)
        self.job_info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(self.job_info, 2)
        root.addLayout(bar)

        # ---- mode caption (the scale cannot infer object type) ----
        self.stage_caption = QLabel(
            "Reel packing — weigh one reel, save it, print its sticker (no QR).")
        self.stage_caption.setProperty("hint", True)
        self.stage_caption.setWordWrap(True)
        root.addWidget(self.stage_caption)

        # ---- station devices (weigh machine + label printer) ----
        self.devices = DeviceStrip("packaging", show_station_label=True, demo_switch=True)
        root.addWidget(self.devices)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(8)
        root.addWidget(split, 1)

        # ---- left: weigh card (60%) over sticker preview (40%) ----
        left = QSplitter(Qt.Vertical)
        left.setHandleWidth(8)
        left.setChildrenCollapsible(False)

        a = QFrame()
        a.setProperty("card", True)
        la = QVBoxLayout(a)
        la.setContentsMargins(16, 16, 16, 16)
        la.setSpacing(10)
        self.weigh_step = QLabel("Weigh")
        self.weigh_step.setProperty("step", True)
        self.weigh_step.setWordWrap(True)
        la.addWidget(self.weigh_step)

        self.weigh_cap = self._caption("Gross reel weight · kg")
        la.addWidget(self.weigh_cap)
        self.gross = QDoubleSpinBox()
        self.gross.setRange(0, 500)
        self.gross.setDecimals(3)
        self.gross.setSingleStep(0.1)
        self.gross.setMinimumHeight(78)
        self.gross.setAlignment(Qt.AlignCenter)
        self.gross.setProperty("big", True)
        la.addWidget(self.gross)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        self.btn_scale = QPushButton("Read scale")
        self.btn_stop = QPushButton("Stop")
        for b in (self.btn_scale, self.btn_stop):
            b.setMinimumHeight(40)
            style_button(b, "secondary")
            row2.addWidget(b)
        la.addLayout(row2)

        # No mock/test-data helpers in production: blank hardware config
        # blocks weighing/printing via DEV.require_scale/require_printer
        # instead of fabricating records. The mock drivers stay only for
        # test compatibility (SIMPLD_ALLOW_MOCK=1); the floor sees none of
        # this. The one exception is the explicit demo switch (Demo button on
        # the device strip above): user-visible, confirmed, pills read "demo".
        self._mock_wrap = None
        self._hw_was_mock = isinstance(HW.scale, HW.MockEssaeDS215)

        # Session core weight (Reel packing): one manual value for every reel
        # of this session — it is never reset by saving a reel.
        self.core_wrap = QWidget()
        row_core = QHBoxLayout(self.core_wrap)
        row_core.setContentsMargins(0, 0, 0, 0)
        row_core.setSpacing(8)
        row_core.addWidget(self._caption("Core / reel (session):"))
        self.core = QDoubleSpinBox()
        self.core.setRange(0.001, 50)
        self.core.setDecimals(3)
        self.core.setValue(0.280)
        self.core.setMinimumHeight(40)
        self.core.setMinimumWidth(130)
        row_core.addWidget(self.core)
        row_core.addStretch(1)
        la.addWidget(self.core_wrap)
        self._core_row = row_core

        # Session box settings (Box packing): tare + reels per box apply to
        # every box of this session, exactly like the core weight does for
        # reels.
        self.box_wrap = QWidget()
        box_rows = QVBoxLayout(self.box_wrap)
        box_rows.setContentsMargins(0, 0, 0, 0)
        box_rows.setSpacing(6)
        row_tare = QHBoxLayout()
        row_tare.setSpacing(8)
        row_tare.addWidget(self._caption("Box / tare (session):"))
        self.tare = QDoubleSpinBox()
        self.tare.setRange(0.0, 50)
        self.tare.setDecimals(3)
        self.tare.setValue(0.300)
        self.tare.setMinimumHeight(40)
        self.tare.setMinimumWidth(130)
        row_tare.addWidget(self.tare)
        row_tare.addStretch(1)
        box_rows.addLayout(row_tare)
        row_rpb = QHBoxLayout()
        row_rpb.setSpacing(8)
        row_rpb.addWidget(self._caption("Reels / box (session):"))
        self.reels_per_box = QSpinBox()
        self.reels_per_box.setRange(1, 20)
        self.reels_per_box.setValue(2)
        self.reels_per_box.setMinimumHeight(40)
        self.reels_per_box.setMinimumWidth(130)
        row_rpb.addWidget(self.reels_per_box)
        row_rpb.addStretch(1)
        box_rows.addLayout(row_rpb)
        la.addWidget(self.box_wrap)

        self.lcd = QLabel("— put reel or box on scale —")
        self.lcd.setProperty("lcd", "net-idle")
        self.lcd.setAlignment(Qt.AlignCenter)
        self.lcd.setWordWrap(True)
        self.lcd.setMinimumHeight(84)
        la.addWidget(self.lcd)
        la.addStretch(1)
        a.setMinimumWidth(300)
        scroll_a = QScrollArea()
        scroll_a.setWidgetResizable(True)
        scroll_a.setFrameShape(QFrame.NoFrame)
        scroll_a.setWidget(a)
        scroll_a.setMinimumWidth(320)
        left.addWidget(scroll_a)

        pv = QFrame()
        pv.setProperty("card", True)
        lp = QVBoxLayout(pv)
        lp.setContentsMargins(12, 12, 12, 12)
        lp.setSpacing(6)
        self.sticker_cap = self._caption("Sticker preview · 100 × 50 mm")
        lp.addWidget(self.sticker_cap)
        self.sticker_preview = PreviewLabel("Weigh to preview sticker.")
        self.sticker_preview.setProperty("sticker", True)
        self.sticker_preview.setAlignment(Qt.AlignCenter)
        self.sticker_preview.setWordWrap(True)
        self.sticker_preview.setMinimumHeight(60)
        self.sticker_preview.setMinimumWidth(0)
        self.sticker_preview.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Preferred)
        self.sticker_preview.setScaledContents(False)
        lp.addWidget(self.sticker_preview, 1)
        left.addWidget(pv)
        left.setSizes([420, 280])
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)
        split.addWidget(left)
        self._left_split = left
        self._left_lay = lp
        self._preview_card = pv

        # ---- right: the active mode's job data + the stage action ----
        b = QFrame()
        b.setProperty("card", True)
        lb = QVBoxLayout(b)
        lb.setContentsMargins(16, 16, 16, 16)
        lb.setSpacing(10)
        self.right_step = QLabel("Reels of this job")
        self.right_step.setProperty("step", True)
        self.right_step.setWordWrap(True)
        lb.addWidget(self.right_step)

        self.right_stack = QStackedWidget()

        # -- reel panel: ALL reels (loose + packed + dispatched) --
        self.reel_panel = QWidget()
        rp = QVBoxLayout(self.reel_panel)
        rp.setContentsMargins(0, 0, 0, 0)
        rp.setSpacing(8)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Roll", "Net · kg", "Status", "Label"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        rp.addWidget(self.table, 1)
        self.sel_info = QLabel("No reels yet — weigh a reel in Reel packing to start.")
        self.sel_info.setProperty("status", True)
        self.sel_info.setWordWrap(True)
        rp.addWidget(self.sel_info)
        hist_row = QHBoxLayout()
        hist_row.setSpacing(8)
        self.btn_hist_print = QPushButton("Print / Reprint")
        self.btn_hist_reweigh = QPushButton("Reweigh")
        self.btn_hist_delete = QPushButton("Delete")
        for hb in (self.btn_hist_print, self.btn_hist_reweigh, self.btn_hist_delete):
            hb.setMinimumHeight(44)
            style_button(hb, "secondary")
            hist_row.addWidget(hb)
        rp.addLayout(hist_row)
        self.right_stack.addWidget(self.reel_panel)

        # -- box panel: packed boxes + the sticker queue --
        self.box_panel = QWidget()
        bp = QVBoxLayout(self.box_panel)
        bp.setContentsMargins(0, 0, 0, 0)
        bp.setSpacing(8)
        self.box_info = QLabel("Select a job to pack boxes.")
        self.box_info.setProperty("status", True)
        self.box_info.setWordWrap(True)
        bp.addWidget(self.box_info)
        self.box_table = QTableWidget(0, 6)
        self.box_table.setHorizontalHeaderLabels(
            ["Box", "Reels", "Gross · kg", "Tare · kg", "Net · kg", "Sticker"])
        self.box_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.box_table.verticalHeader().setVisible(False)
        self.box_table.verticalHeader().setDefaultSectionSize(44)
        self.box_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.box_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.box_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.box_table.setShowGrid(False)
        self.box_table.setAlternatingRowColors(True)
        self.box_table.setFocusPolicy(Qt.NoFocus)
        self.box_table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        bp.addWidget(self.box_table, 1)
        self.box_queue = QLabel("Sticker queue: 0 waiting")
        self.box_queue.setProperty("status", True)
        self.box_queue.setWordWrap(True)
        bp.addWidget(self.box_queue)
        box_row = QHBoxLayout()
        box_row.setSpacing(8)
        self.btn_box_print = QPushButton("Print next sticker")
        self.btn_box_reprint = QPushButton("Reprint selected")
        self.btn_box_delete = QPushButton("Delete box")
        for bb in (self.btn_box_print, self.btn_box_reprint, self.btn_box_delete):
            bb.setMinimumHeight(44)
            style_button(bb, "secondary")
            box_row.addWidget(bb)
        bp.addLayout(box_row)
        self.right_stack.addWidget(self.box_panel)

        lb.addWidget(self.right_stack, 1)
        self.btn_action = QPushButton("Weigh first")
        self.btn_action.setMinimumHeight(66)
        style_button(self.btn_action, "secondary")
        self.btn_action.setEnabled(False)
        lb.addWidget(self.btn_action)
        b.setMinimumWidth(300)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        split.addWidget(b)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 1)
        split.setSizes([500, 500])
        self._right_card = b
        self._right_lay = lb
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)

        # signals
        self.btn_new.clicked.connect(self.new_job)
        self.btn_product.clicked.connect(self.new_product)
        self.btn_targets.clicked.connect(self.edit_targets)
        self.job_combo.currentIndexChanged.connect(self.job_changed)
        self.gross.valueChanged.connect(lambda *_: self._refresh_state())
        self.core.valueChanged.connect(lambda *_: self._refresh_state())
        self.tare.valueChanged.connect(lambda *_: self._refresh_state())
        self.reels_per_box.valueChanged.connect(lambda *_: self._refresh_state())
        self.btn_scale.clicked.connect(self.read_scale)
        self.btn_stop.clicked.connect(self.stop_wt)
        self.btn_hist_print.clicked.connect(self.history_print)
        self.btn_hist_reweigh.clicked.connect(self.history_reweigh)
        self.btn_hist_delete.clicked.connect(self.history_delete)
        self.btn_box_print.clicked.connect(self.box_print_next)
        self.btn_box_reprint.clicked.connect(self.box_reprint)
        self.btn_box_delete.clicked.connect(self.box_delete)
        self.table.itemSelectionChanged.connect(self._update_history_buttons)
        self.box_table.itemSelectionChanged.connect(self._update_history_buttons)
        self.btn_action.clicked.connect(self.do_action)
        self.set_mode("reel")
        self.refresh_jobs()
        self._refresh_state()
        self._update_history_buttons()

    @staticmethod
    def _caption(t: str) -> QLabel:
        c = QLabel(t)
        c.setProperty("field", "caption")
        return c

    # ----- mode (nested nav: Reel packing / Box packing) -----
    def set_mode(self, mode: str):
        """Switch the whole page between 'reel' and 'carton' (nav-driven)."""
        m = "carton" if str(mode).lower() in ("carton", "box", "box packing") else "reel"
        self._mode = m
        try:
            self.right_stack.setCurrentIndex(0 if m == "reel" else 1)
        except Exception:
            pass
        self._refresh_state()

    @property
    def pack_mode(self) -> str:
        """Explicit mode choice: 'reel' (Reel packing) or 'carton' (Box packing)."""
        return self._mode

    def _reels_per_box(self) -> int:
        try:
            return max(1, int(self.reels_per_box.value()))
        except Exception:
            return 2

    def _next_box_reels(self) -> list[dict]:
        return self._loose()[: self._reels_per_box()]

    def _me(self) -> dict | None:
        """Real session actor (backend-resolved); None when signed out/forged."""
        try:
            return DA.current_user()
        except Exception:
            return None

    def _role(self) -> str:
        me = self._me()
        return str((me or {}).get("role") or "") if me else ""

    def _can_produce(self) -> bool:
        return self._role() in ("packager", "admin")

    def _can_reweigh(self) -> bool:
        return self._role() in ("packager", "admin")

    def _is_admin(self) -> bool:
        return self._role() == "admin"

    def _backend_error(self, e) -> str:
        """User message for a backend failure; no auto-retry, no fallback."""
        try:
            msg, _kind = DA.describe_error(e)
        except Exception:
            msg = str(e) or "Request failed."
        return msg

    # ----- sticker preview (fixed 100x50mm Citizen stock, live what-will-print) -----
    @staticmethod
    def _next_roll_no(job_id: int) -> int:
        try:
            return DA.predict_next_reel_seq(job_id=job_id)
        except Exception:
            try:
                return DA.predict_next_reel_seq()
            except Exception:
                return 1

    @staticmethod
    def _next_bundle_no(job_id: int) -> int:
        try:
            return DA.next_bundle_no(job_id)
        except Exception:
            return 1

    @staticmethod
    def _next_carton_no(job_id: int | None = None) -> str:
        try:
            from factory.db import current_period, carton_no_for
            if job_id is not None:
                try:
                    seq = DA.predict_next_box_seq(job_id=job_id)
                except Exception:
                    seq = DA.predict_next_box_seq()
            else:
                seq = DA.predict_next_box_seq()
            return carton_no_for(current_period(), seq)
        except Exception:
            return "C????"

    def _render_previews(self):
        """One live sticker preview under the Weigh card. Read-only.

        Reel packing -> Type-1 reel art from the live scale weight only;
        Box packing -> Type-2 box art from the live weight + the next reels;
        anything else -> hint text. Never writes to the database.
        """
        lbl = getattr(self, "sticker_preview", None)
        cap = getattr(self, "sticker_cap", None)
        if lbl is None:
            return
        job = self.current_job()
        if job is None:
            if cap is not None:
                cap.setText("Sticker preview · 100 × 50 mm")
            set_preview_label(lbl, "Select a job to preview stickers.")
            return
        try:
            if self.pack_mode == "carton":
                if self.gross.value() <= 0:
                    if cap is not None:
                        cap.setText("Sticker preview · box sticker (WITH QR)")
                    set_preview_label(lbl, "Weigh the packed box to preview its sticker.")
                else:
                    if cap is not None:
                        cap.setText("Sticker preview · box sticker (WITH QR) — queued, printed later")
                    set_preview_label(lbl, self._carton_art(job))
            else:
                if self.gross.value() <= 0:
                    if cap is not None:
                        cap.setText("Sticker preview · 100 × 50 mm")
                    set_preview_label(lbl, "Weigh to preview sticker.")
                else:
                    if cap is not None:
                        cap.setText("Sticker preview · Type-1 reel (NO QR)")
                    set_preview_label(lbl, self._reel_art(job))
        except Exception:
            try:
                set_preview_label(lbl, "Preview unavailable.")
            except Exception:
                pass

    def _reel_art(self, job: dict):
        """Type-1 art from the live scale inputs only. Returns QPixmap or text."""
        from factory.db import now_iso, current_period, roll_code_for
        scale = self.gross.value()
        core = self.core.value()
        if scale <= 0:
            return "Weigh to preview sticker."
        if round(scale - core, 3) <= 0:
            return "Check gross/core to preview the reel sticker."
        try:
            jid = job.get("id") if isinstance(job, dict) else self.job_id
            # P0: sticker shows the per-product 1..N (prod_seq).
            pseq = DA.predict_next_reel_seq(job_id=jid) if jid else DA.predict_next_reel_seq()
            per = current_period()
            # Global code for DB identity (not shown on sticker).
            try:
                gseq = DA.predict_next_reel_seq()
            except Exception:
                gseq = pseq
        except Exception:
            pseq, gseq, per = 1, 1, current_period()
        reel = {"gross": round(scale, 3), "core": round(core, 3),
                "net": round(scale - core, 3), "created_at": now_iso(),
                "roll_no": pseq, "prod_seq": pseq, "roll_period": per,
                "roll_code": roll_code_for(per, gseq)}
        return pil_to_pixmap(LabelArt.render_reel(
            job, reel, width_mm=PRINTER_WIDTH_MM, height_mm=PRINTER_HEIGHT_MM),
            max_width=PREVIEW_MAX_WIDTH)

    def _carton_art(self, job: dict):
        """Type-2 box art from the live weight + the next loose reels."""
        from factory.db import now_iso, current_period, carton_no_for
        scale = self.gross.value()
        if scale <= 0:
            return "Weigh the packed box to preview its sticker."
        nexts = self._next_box_reels()
        if not nexts:
            return "No loose reels to pack. Weigh reels in Reel packing first."
        bw = round(self.tare.value(), 3)
        if bw >= scale:
            return "Check tare/gross to preview the box sticker."
        tc = round(sum(float(r["core"]) for r in nexts), 3)
        tn = round(scale - bw - tc, 3)
        if tn <= 0:
            return "Check tare/gross/core to preview the box sticker."
        try:
            jid = job.get("id") if isinstance(job, dict) else self.job_id
            if jid:
                try:
                    pseq = DA.predict_next_box_seq(job_id=jid)
                except Exception:
                    pseq = DA.predict_next_box_seq()
            else:
                pseq = DA.predict_next_box_seq()
            per = current_period()
            try:
                gseq = DA.predict_next_box_seq()
            except Exception:
                gseq = pseq
        except Exception:
            pseq, gseq, per = 1, 1, current_period()
        cn = carton_no_for(per, gseq)
        detail = {"job_no": job["job_no"], "job_name": job["job_name"],
                  "packed_at": now_iso(),
                  "num_reels": len(nexts), "carton_no": cn,
                  "prod_seq": pseq,
                  "bundle_no": self._next_bundle_no(job["id"]),
                  "box_period": per, "box_wt": bw,
                  "total_gross": round(scale, 3),
                  "total_core": tc, "total_net": tn,
                  "reels": [{"roll_no": r.get("prod_seq") or r["roll_no"],
                             "prod_seq": r.get("prod_seq") or r["roll_no"]} for r in nexts],
                  "qr": DA.qr_payload(cn)}
        return pil_to_pixmap(LabelArt.render_carton(
            detail, width_mm=PRINTER_WIDTH_MM, height_mm=PRINTER_HEIGHT_MM),
            max_width=PREVIEW_MAX_WIDTH)

    # ----- data -----
    def refresh_jobs(self, select_no=None):
        self.job_combo.blockSignals(True)
        self.job_combo.clear()
        try:
            jobs = DA.list_jobs()
        except Exception as e:
            self.job_combo.blockSignals(False)
            self.job_id = None
            return self.say(f"Connection issue: {self._backend_error(e)}", ok=False)
        for j in jobs:
            self.job_combo.addItem(f"J:{j['job_no']} — {j['job_name']}",
                                   userData=j["id"])
            if select_no and j["job_no"] == select_no:
                self.job_combo.setCurrentIndex(self.job_combo.count() - 1)
        self.job_combo.blockSignals(False)
        if jobs:
            self.job_changed(0 if select_no is None else self.job_combo.currentIndex())
        else:
            # No jobs at all: clear the job-scoped tables/queues so rows
            # from a previously selected job cannot linger in the views.
            self.job_id = None
            self.refresh_history()
            self._refresh_state()

    def current_job(self):
        if self.job_id is None:
            return None
        try:
            return DA.get_job_by_id(self.job_id)
        except Exception:
            return None

    def new_job(self):
        j = self.ask_new_job(self)
        if j:
            self.say(f"Job J:{j['job_no']} created.", ok=True)
            self.refresh_jobs(select_no=j["job_no"])

    def new_product(self):
        p = self.ask_new_product(self)
        if p:
            self.say(f"Product '{p['name']}' added — pick it in New job.", ok=True)

    def edit_targets(self):
        """Set the job's target: pieces for piece products, net kg for kg
        products. Reels/boxes needed are derived — never typed."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit,
                                       QDialogButtonBox, QLabel)
        if self.job_id is None:
            return self.say("Create/select a job first.", ok=False, kind="warning")
        job = self.current_job()
        if not job:
            return self.say("Job not found — refresh first.", ok=False)
        d = QDialog(self)
        d.setWindowTitle(f"Target — J:{job['job_no']}")
        f = QFormLayout(d)
        info = QLabel("Blank = no target. Targets never block packing; they only"
                      " drive the green FULFILLED state. Reels/boxes needed are"
                      " calculated from this.")
        info.setWordWrap(True)
        info.setProperty("hint", True)
        f.addRow(info)
        is_piece = _unit_of(job) != "kg"
        e_unit = QLineEdit()
        if is_piece:
            e_unit.setText(str(job.get("target_pieces") or ""))
            e_unit.setPlaceholderText("e.g. 165000 (wrappers)")
            f.addRow("Target pieces:", e_unit)
        else:
            e_unit.setText(str(job.get("target_weight_kg") or ""))
            e_unit.setPlaceholderText("e.g. 500 (net kg)")
            f.addRow("Target net kg:", e_unit)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        f.addRow(bb)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        if d.exec() != QDialog.Accepted:
            return
        def _opt(v):
            t = (v or "").strip()
            return t or None
        try:
            if is_piece:
                upd = DA.set_job_targets(self.job_id, target_pieces=_opt(e_unit.text()))
            else:
                upd = DA.set_job_targets(self.job_id, target_weight_kg=_opt(e_unit.text()))
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}", ok=False)
        self.say("Target saved.", ok=True)
        self._refresh_job_info()
        self._refresh_state()

    def job_changed(self, i):
        if i < 0:
            return
        self.job_id = self.job_combo.itemData(i)
        job = self.current_job()
        if job:
            # Session core default follows the job ONLY when the job changes;
            # saves never reset the manual session value.
            if self._core_job_id != self.job_id:
                self._core_job_id = self.job_id
                try:
                    self.core.blockSignals(True)
                    self.core.setValue(float(job["core_wt"]))
                finally:
                    try:
                        self.core.blockSignals(False)
                    except Exception:
                        pass
            self._refresh_job_info()
        self.refresh_history()

    def _refresh_job_info(self):
        job = self.current_job()
        if job is None:
            return
        try:
            st = DA.job_stats(job["id"])
        except Exception:
            self.job_info.setText(
                f"J:{job['job_no']} {job['job_name']} — connection issue.")
            return
        w = job.get("wrappers_per_reel")
        wtxt = _unit_label(job)
        base = (f"J:{job['job_no']} {job['job_name']} ({wtxt})  ·  "
                f"Loose {st.get('loose', 0)}  ·  Ready {st.get('ready_cartons', 0)}")
        # Target = the product's own unit only; reels/boxes needed are
        # derived (pieces ÷ wrappers, kg ÷ typical reel net). Vivid
        # fulfilled state so the floor sees it at a glance.
        import math as _math
        try:
            rpb = max(1, self._reels_per_box())
        except Exception:
            rpb = 2
        tgt = ""
        try:
            if _unit_of(job) != "kg" and job.get("target_pieces"):
                tp = int(job["target_pieces"])
                made = int(st.get("pieces") or 0)
                need_r = max(0, _math.ceil(max(0, tp - made) / int(w)))
                need_b = max(0, _math.ceil(need_r / rpb)) if need_r else 0
                tgt = (f"Target {made:,}/{tp:,} pcs"
                       + (f" · needs {need_r} reels ({need_b} boxes)" if need_r else ""))
            elif _unit_of(job) == "kg" and job.get("target_weight_kg"):
                tw = float(job["target_weight_kg"])
                made_w = float(st.get("reel_wt") or 0)
                try:
                    med = self._median_gross(self._loose())
                    avg = (float(med) - float(job.get("core_wt") or 0.28)) if med else 0
                except Exception:
                    avg = 0.0
                if not avg or avg <= 0:
                    try:
                        n = int(st.get("reels") or 0)
                        avg = (made_w / n) if n else 10.0
                    except Exception:
                        avg = 10.0
                need_r = max(0, _math.ceil(max(0.0, tw - made_w) / avg)) if avg > 0 else 0
                need_b = max(0, _math.ceil(need_r / rpb)) if need_r else 0
                tgt = (f"Target {made_w:.1f}/{tw:.1f} kg"
                       + (f" · needs ~{need_r} reels ({need_b} boxes)" if need_r else ""))
        except Exception:
            tgt = ""
        if tgt:
            base += "  ·  " + tgt
            if st.get("any_target_met"):
                base += "  ✓ FULFILLED"
        self.job_info.setText(base)
        # Vivid fulfilled indication: job bar turns green + action button
        # goes success-green when a packaging target is met.
        try:
            from factory.ui.styles import set_banner
            if st.get("any_target_met"):
                set_banner(self.job_info, "ok")
            else:
                # Neutral banner (clears any previous green).
                try:
                    self.job_info.setProperty("banner", "neutral")
                    s = self.job_info.style()
                    s.unpolish(self.job_info)
                    s.polish(self.job_info)
                except Exception:
                    pass
        except Exception:
            pass

    @property
    def msg(self) -> str:
        """Last toast text (kept for tests; no persistent banner widget)."""
        return getattr(self, "_last_say", "")

    def say(self, t, ok=True, kind: str | None = None):
        self._last_say = str(t)
        _toast(self, str(t), ok=ok, kind=kind)

    # ----- weigh / mode state -----
    def _set_lcd(self, text: str, mode: str):
        self.lcd.setText(text)
        self.lcd.setProperty("lcd", mode)
        try:
            s = self.lcd.style()
            s.unpolish(self.lcd)
            s.polish(self.lcd)
        except Exception:
            pass
        # Every weigh/mode path sets the LCD, so previews stay live.
        try:
            self._render_previews()
        except Exception:
            pass

    def _set_action(self, text: str, kind: str, enabled: bool):
        self.btn_action.setText(text)
        style_button(self.btn_action, kind)
        self.btn_action.setEnabled(enabled)

    def _loose(self) -> list[dict]:
        if self.job_id is None:
            return []
        try:
            return DA.loose_reels(self.job_id)
        except Exception:
            return []

    @staticmethod
    def _median_gross(loose: list[dict]) -> float | None:
        if not loose:
            return None
        return statistics.median(sorted(r["gross"] for r in loose))

    def _update_box_info(self):
        if not hasattr(self, "box_info"):
            return
        pending = sum(1 for b in self._box_rows
                      if str(b.get("print_status") or "pending") != "printed")
        try:
            self.box_queue.setText(f"Sticker queue: {pending} waiting")
        except Exception:
            pass
        if self.job_id is None:
            self.box_info.setText("Select a job to pack boxes.")
            return
        n = self._reels_per_box()
        loose = self._loose()
        if not loose:
            self.box_info.setText(
                "No loose reels — weigh reels in Reel packing first.")
            return
        nxt = loose[:n]
        rolls = ", ".join(self._roll_text(r) for r in nxt)
        tail = f" (partial — only {len(nxt)} loose left)" if len(nxt) < n else ""
        self.box_info.setText(f"Next box takes rolls {rolls}{tail}.")

    def _refresh_state(self):
        """Mode-gated brain: only the active mode's controls are visible and
        enabled; no weight-based object detection (the operator picks the
        mode via the nested nav)."""
        scale, core = self.gross.value(), self.core.value()
        is_reel = self.pack_mode == "reel"
        try:
            self.core_wrap.setVisible(is_reel)
            self.box_wrap.setVisible(not is_reel)
        except Exception:
            pass
        try:
            if is_reel:
                self.weigh_step.setText("Weigh one reel")
                self.stage_caption.setText(
                    "Reel packing — weigh one reel, save it, print its sticker (no QR)."
                    " The session core applies to every reel.")
                self.right_step.setText("Reels of this job")
            else:
                n = self._reels_per_box()
                self.weigh_step.setText("Weigh the packed box")
                self.stage_caption.setText(
                    f"Box packing — weigh the packed box; the next {n} loose reel(s)"
                    " in production order go in and the sticker is queued, not printed.")
                self.right_step.setText("Boxes of this job")
        except Exception:
            pass
        self._update_box_info()

        if self.job_id is None:
            self._set_lcd("— select a job first —", "net-warn")
            self._set_action("Select a job", "secondary", False)
            self._mode = self._mode  # explicit
            return
        if not self._can_produce():
            self._set_lcd("Read-only — dispatcher cannot weigh or pack.", "net-idle")
            self._set_action("Read only", "secondary", False)
            return

        if not is_reel:
            loose = self._loose()
            if not loose:
                self._set_lcd("No loose reels — weigh reels first.", "net-idle")
                self._set_action("No loose reels", "secondary", False)
                return
            if scale <= 0:
                self._set_lcd("Put the packed box on the scale", "net-idle")
                self._set_action("Weigh first", "secondary", False)
                return
            bw = round(self.tare.value(), 3)
            if bw >= scale:
                self._set_lcd(
                    f"Tare {bw:.3f} ≥ box {scale:.3f} — check the session tare.", "net-bad")
                self._set_action("Check tare", "secondary", False)
                return
            nexts = self._next_box_reels()
            tc = round(sum(float(r["core"]) for r in nexts), 3)
            tn = round(scale - bw - tc, 3)
            if tn <= 0:
                self._set_lcd(f"Net {tn:.3f} — not possible. Check tare/gross/core.", "net-bad")
                self._set_action("Check weights", "secondary", False)
                return
            k = len(nexts)
            # Vivid fulfilled: green success button + checkmark when a target is met.
            try:
                _st = DA.job_stats(self.job_id) if self.job_id else {}
            except Exception:
                _st = {}
            if _st.get("any_target_met"):
                self._set_lcd(
                    f"✓ TARGET FULFILLED — Box: {k} reel{'s' if k != 1 else ''} · net {tn:.3f} Kg.",
                    "net-ok")
                self._set_action(
                    f"✓ Pack box ({k} reel{'s' if k != 1 else ''})", "success", True)
            else:
                self._set_lcd(
                    f"Box: {k} reel{'s' if k != 1 else ''} · net {tn:.3f} Kg — ready to pack.",
                    "net-ok")
                self._set_action(
                    f"Pack box ({k} reel{'s' if k != 1 else ''})", "primary", True)
            return

        if scale <= 0:
            self._set_lcd("Put a reel on the scale", "net-idle")
            self._set_action("Weigh first", "secondary", False)
            return
        net = round(scale - core, 3)
        if net <= 0:
            self._set_lcd(f"Net {net:.3f} — not possible. Check gross and core.", "net-bad")
            self._set_action("Check weights", "secondary", False)
            return
        try:
            _st2 = DA.job_stats(self.job_id) if self.job_id else {}
        except Exception:
            _st2 = {}
        if _st2.get("any_target_met"):
            self._set_lcd(f"✓ TARGET FULFILLED — Net: {net:.3f} Kg.", "net-ok")
            self._set_action("✓ Save & print reel", "success", True)
        else:
            self._set_lcd(f"Net: {net:.3f} Kg — single reel.", "net-ok")
            self._set_action("Save & print reel", "success", True)

    def _typical_reel_gross(self) -> float:
        """Job-aware mock default: median loose reel gross, else last resort."""
        try:
            med = self._median_gross(self._loose())
            if med:
                return round(float(med), 3)
        except Exception:
            pass
        return 11.380

    def read_scale(self):
        if self.job_id is None:
            return self.say("Create/select a job first.", ok=False, kind="warning")
        try:
            DEV.require_scale()
        except RuntimeError as e:
            self.gross.setValue(0)
            self._refresh_state()
            return self.say(str(e), ok=False)
        if isinstance(HW.scale, HW.MockEssaeDS215) and (DEV.mock_allowed() or DEV.demo_mode()):
            # Test-compat (SIMPLD_ALLOW_MOCK, source checkout only) or the explicit
            # demo switch: echo the manual entry or a fresh simulated reading. Demo
            # unfreezes first so every Read starts a new reel, never a stale freeze.
            try:
                if DEV.demo_mode() and hasattr(HW.scale, "unfreeze"):
                    HW.scale.unfreeze()
                self.gross.setValue(HW.scale.read_weight())
            except Exception as e:
                self.gross.setValue(0)
                self._refresh_state()
                return self.say(f"Scale error: {e}", ok=False)
        else:
            # Real Essae: fresh measured reading, or an error — never a
            # fake/stale value written into the weigh card. On failure the
            # card is invalidated to 0 (action disarmed by _refresh_state)
            # so a prior positive gross cannot be saved/packed as a fresh
            # reading. Manual entry in the spinbox stays available — it is
            # just never written by a failed read.
            try:
                if hasattr(HW.scale, "unfreeze"):
                    HW.scale.unfreeze()
                self.gross.setValue(HW.scale.read_weight())
            except Exception as e:
                self.gross.setValue(0)
                self._refresh_state()
                return self.say(f"Scale error: {e}", ok=False)
        self._refresh_state()
        if self.gross.value() > 0:
            self.say("Box on scale." if self.pack_mode == "carton" else "Reel on scale.",
                     ok=True)

    def stop_wt(self):
        try:
            DEV.require_scale()
        except RuntimeError as e:
            self.gross.setValue(0)
            self._refresh_state()
            return self.say(str(e), ok=False)
        # No `or old_value` fallback: a genuine 0.000 kg freeze must land
        # as 0 (action disarmed), never preserve a stale positive gross.
        try:
            self.gross.setValue(float(HW.scale.freeze()))
        except Exception as e:
            self.gross.setValue(0)
            self._refresh_state()
            return self.say(f"Scale error: {e}", ok=False)
        self._refresh_state()
        self.say("Weight noted.", ok=True)

    def refresh_hardware(self):
        """Re-apply shared hardware state (call after Devices save).

        A real<->mock switch invalidates the card to 0: a weight read
        under one scale mode must never stay armed under the other.
        """
        try:
            now_mock = isinstance(HW.scale, HW.MockEssaeDS215)
            if now_mock != getattr(self, "_hw_was_mock", now_mock):
                self.gross.setValue(0)
            self._hw_was_mock = now_mock
        except Exception:
            pass
        try:
            self.devices.refresh()
        except Exception:
            pass
        try:
            self._refresh_state()
        except Exception:
            pass

    def do_action(self):
        if not self._can_produce():
            return self.say("Read-only role cannot weigh or pack.", ok=False)
        if self.job_id is None:
            return self.say("Create/select a job first.", ok=False, kind="warning")
        if self.gross.value() <= 0:
            return self.say("Place a reel or box on the scale first.", ok=False,
                            kind="warning")
        if self.pack_mode == "carton":
            self.pack_box()
        else:
            self.save_reel()

    def save_reel(self):
        if not self._can_produce():
            return self.say("Read-only role cannot save reels.", ok=False)
        if self.job_id is None:
            return self.say("Create/select a job first.", ok=False, kind="warning")
        try:
            DEV.require_scale()
        except RuntimeError as e:
            return self.say(str(e), ok=False)
        try:
            # No shift / supervisor / customer on the sticker: customer lives
            # on the dispatch order only. Stored defaults keep history intact.
            # The manual session core applies to every reel of this session.
            # Single attempt: an unknown-outcome write is NOT auto-retried;
            # on connection failure refresh before deciding to retry.
            reel = DA.create_reel(self.job_id, self.gross.value(), self.core.value())
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh, check the record exists, then retry.", ok=False)
        try:
            DEV.require_printer()
        except RuntimeError as e:
            self.say(f"Roll {reel['roll_no']} saved ({reel['net']:.3f} Kg)."
                     f" {e} Label stays pending.", ok=True)
            self.gross.setValue(0)
            self._refresh_after_change()
            return
        job = self.current_job()
        self.say(f"Roll {reel['roll_no']} saved ({reel['net']:.3f} Kg).", ok=True)
        self._refresh_after_change()
        ReelLabelDialog(self, job, reel, HW.printer).exec()
        self.refresh_history()
        if self.on_stock_changed:
            self.on_stock_changed()
        self.gross.setValue(0)

    def pack_box(self):
        """Pack the weighed box from the next loose reels. No printing: the
        box sticker is queued and printed later from the sticker queue."""
        if not self._can_produce():
            return self.say("Read-only role cannot pack boxes.", ok=False)
        if self.job_id is None:
            return self.say("Select a job first.", ok=False, kind="warning")
        if self.gross.value() <= 0:
            return self.say("Put the packed box on the scale first.", ok=False,
                            kind="warning")
        try:
            DEV.require_scale()
        except RuntimeError as e:
            return self.say(str(e), ok=False)
        try:
            ct = DA.pack_next_box(self.job_id, self.gross.value(),
                                  self.tare.value(),
                                  reels_per_box=self._reels_per_box())
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh, check the record exists, then retry.", ok=False)
        self.say(f"Box {bare_carton_no(ct['carton_no'])} packed ({int(ct['num_reels'])} reels)."
                 " Sticker queued — print it from the queue.", ok=True)
        self.gross.setValue(0)
        self._refresh_after_change()

    # Back-compat alias: the floor flow packs the weighed box.
    pack = pack_box

    def _refresh_after_change(self):
        """Job stats + tables + stock views after a write. Never resets the
        manual session weights (core/tare/reels-per-box)."""
        try:
            self._refresh_job_info()
        except Exception:
            pass
        self.refresh_history()
        if self.on_stock_changed:
            self.on_stock_changed()

    # ----- job history: ALL reels (loose + packed + dispatched) -----
    def _label_text(self, r: dict) -> str:
        st = str(r.get("print_status") or "pending")
        n = int(r.get("print_count") or 0)
        if st == "printed" and n > 0:
            return f"Printed ×{n}"
        return "Pending"

    @staticmethod
    def _roll_text(r: dict) -> str:
        """Per-product reel number (P0) — the floor's visual identity."""
        try:
            if r.get("prod_seq") is not None:
                return str(int(r["prod_seq"]))
        except (TypeError, ValueError):
            pass
        return str(r.get("roll_no"))

    def refresh_history(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._history_rows = []
        self.box_table.blockSignals(True)
        self.box_table.setRowCount(0)
        self._box_rows = []
        if self.job_id is not None:
            try:
                rows = DA.job_reels(self.job_id)
            except Exception as e:
                rows = []
                self.sel_info.setText(
                    f"Connection issue — {self._backend_error(e)}")
                self.table.blockSignals(False)
                self.box_table.blockSignals(False)
                self._update_history_buttons()
                return
            self._history_rows = rows
            from PySide6.QtGui import QColor as _QColor
            for r in rows:
                i = self.table.rowCount()
                self.table.insertRow(i)
                self.table.setItem(i, 0, QTableWidgetItem(self._roll_text(r)))
                self.table.setItem(i, 1, QTableWidgetItem(f"{r['net']:.3f}"))
                _st = QTableWidgetItem(str(r.get("status") or ""))
                try:
                    _st.setForeground(_QColor(status_color(str(r.get("status") or ""))))
                except Exception:
                    pass
                self.table.setItem(i, 2, _st)
                _lb = QTableWidgetItem(self._label_text(r))
                try:
                    _printed = str(r.get("print_status") or "pending").lower() == "printed"
                    _lb.setForeground(_QColor(status_color("printed" if _printed else "pending")))
                except Exception:
                    pass
                self.table.setItem(i, 3, _lb)
            try:
                boxes = DA.job_boxes(self.job_id)
            except Exception:
                boxes = []
            self._box_rows = boxes
            from PySide6.QtGui import QColor as _QColor2
            for bx in boxes:
                i = self.box_table.rowCount()
                self.box_table.insertRow(i)
                # Per-product box number when present (P0), else global bare.
                try:
                    _bno = str(int(bx.get("prod_seq"))) if bx.get("prod_seq") is not None else bare_carton_no(bx.get("carton_no") or "")
                except (TypeError, ValueError):
                    _bno = bare_carton_no(bx.get("carton_no") or "")
                self.box_table.setItem(i, 0, QTableWidgetItem(_bno))
                self.box_table.setItem(i, 1, QTableWidgetItem(
                    ",".join(str(x) for x in (bx.get("rolls") or [])) or "—"))
                self.box_table.setItem(i, 2, QTableWidgetItem(
                    f"{float(bx.get('total_gross') or 0):.3f}"))
                self.box_table.setItem(i, 3, QTableWidgetItem(
                    f"{float(bx.get('box_wt') or 0):.3f}"))
                self.box_table.setItem(i, 4, QTableWidgetItem(
                    f"{float(bx.get('total_net') or 0):.3f}"))
                _lb = QTableWidgetItem(self._label_text(bx))
                try:
                    _printed = str(bx.get("print_status") or "pending").lower() == "printed"
                    _lb.setForeground(_QColor2(status_color("printed" if _printed else "pending")))
                except Exception:
                    pass
                self.box_table.setItem(i, 5, _lb)
        self.table.blockSignals(False)
        self.box_table.blockSignals(False)
        self.update_history_info()
        self._update_history_buttons()

    # Back-compat: old callers used the loose-only name.
    def refresh_loose(self):
        self.refresh_history()

    def update_history_info(self):
        rows = self._history_rows
        if not rows:
            self.sel_info.setText("No reels yet — weigh a reel in Reel packing to start.")
        else:
            loose = sum(1 for r in rows if r.get("status") == "loose")
            self.sel_info.setText(
                f"{len(rows)} reels ({loose} loose) — select a row to Print / Reweigh / Delete.")
        self._update_box_info()
        self._refresh_state()

    # Back-compat alias.
    def update_carton_info(self):
        self.update_history_info()

    def selected_history(self) -> dict | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._history_rows[i] if 0 <= i < len(self._history_rows) else None

    def selected_box(self) -> dict | None:
        rows = self.box_table.selectionModel().selectedRows() if self.box_table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._box_rows[i] if 0 <= i < len(self._box_rows) else None

    def _update_history_buttons(self):
        sel = self.selected_history()
        bx = self.selected_box()
        has = sel is not None
        has_box = bx is not None
        can_prod = self._can_produce()
        can_print = self._role() in ("packager", "admin")
        admin = self._is_admin()
        pending = sum(1 for b in self._box_rows
                      if str(b.get("print_status") or "pending") != "printed")
        try:
            self.btn_hist_print.setEnabled(bool(has))
            self.btn_hist_reweigh.setEnabled(bool(has and can_prod))
            self.btn_hist_delete.setEnabled(bool(has and admin))
            self.btn_box_print.setEnabled(bool(self.job_id is not None and can_print and pending))
            self.btn_box_reprint.setEnabled(bool(has_box and can_print))
            self.btn_box_delete.setEnabled(bool(has_box and admin))
            if has and not can_prod:
                self.btn_hist_reweigh.setToolTip("Read-only role.")
            if has and not admin:
                self.btn_hist_delete.setToolTip("Admin only.")
            if has_box and not admin:
                self.btn_box_delete.setToolTip("Admin only.")
        except Exception:
            pass

    def history_print(self):
        sel = self.selected_history()
        if not sel:
            return self.say("Select a reel row first.", ok=False, kind="warning")
        if self._role() not in ("packager", "admin"):
            return self.say("Read-only role cannot print.", ok=False)
        try:
            DEV.require_printer()
        except RuntimeError as e:
            return self.say(f"{e} Record stays pending.", ok=False)
        job = self.current_job()
        try:
            fresh = DA.job_reels(self.job_id)
            rec = next((x for x in fresh if x["id"] == sel["id"]), sel)
        except Exception:
            rec = sel
        ReelLabelDialog(self, job, rec, HW.printer).exec()
        self.refresh_history()

    def box_print_next(self):
        """Print the OLDEST box sticker still waiting (the sticker queue)."""
        if self._role() not in ("packager", "admin"):
            return self.say("Read-only role cannot print.", ok=False)
        if self.job_id is None:
            return self.say("Create/select a job first.", ok=False, kind="warning")
        try:
            pending = DA.pending_box_stickers(self.job_id)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}", ok=False)
        if not pending:
            return self.say("No box stickers waiting — pack a box first.", ok=False,
                            kind="warning")
        try:
            DEV.require_printer()
        except RuntimeError as e:
            return self.say(f"{e} Sticker stays queued.", ok=False)
        nxt = pending[0]
        try:
            detail = DA.carton_detail(nxt["id"])
        except Exception as e:
            return self.say(f"Box {bare_carton_no(nxt.get('carton_no'))} details unreadable:"
                            f" {self._backend_error(e)} Sticker stays queued.", ok=False)
        CartonLabelDialog(self, detail, HW.printer).exec()
        self.refresh_history()

    def box_reprint(self):
        bx = self.selected_box()
        if not bx:
            return self.say("Select a box row first.", ok=False, kind="warning")
        if self._role() not in ("packager", "admin"):
            return self.say("Read-only role cannot print.", ok=False)
        try:
            DEV.require_printer()
        except RuntimeError as e:
            return self.say(f"{e} Sticker stays queued.", ok=False)
        try:
            detail = DA.carton_detail(int(bx["id"]))
        except Exception as e:
            return self.say(f"Box details unreadable: {self._backend_error(e)}", ok=False)
        CartonLabelDialog(self, detail, HW.printer).exec()
        self.refresh_history()

    def box_delete(self):
        if not self._is_admin():
            return self.say("Only admin may delete boxes.", ok=False)
        bx = self.selected_box()
        if not bx:
            return self.say("Select a box row first.", ok=False, kind="warning")
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Delete box",
            f"Delete box {bare_carton_no(bx['carton_no'])}"
            f" ({int(bx.get('num_reels') or 0)} reels)?\n"
            "The linked reels go back to loose; the number is never reused.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.delete_carton(int(bx["id"]))
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh before retrying.", ok=False)
        self.say(f"Box {bare_carton_no(bx['carton_no'])} deleted — reels back to loose.", ok=True)
        self.refresh_history()

    def history_reweigh(self):
        """Reweigh the selected reel from a FRESH scale measurement only.

        The weigh card (gross spinbox) must hold a fresh reading: the old
        stored gross is never reused as the new value. After the write the
        card is reset to 0 and the row selection cleared so a stale reading
        cannot be applied twice.
        """
        if not self._can_reweigh():
            return self.say("Read-only role cannot reweigh.", ok=False)
        sel = self.selected_history()
        if not sel:
            return self.say("Select a reel row first.", ok=False, kind="warning")
        try:
            DEV.require_scale()
        except RuntimeError as e:
            return self.say(str(e), ok=False)
        fresh = self.gross.value()
        if fresh <= 0:
            return self.say(
                f"Place roll {self._roll_text(sel)} on the scale for a fresh"
                " measurement first — the old weight is never reused.",
                ok=False, kind="warning")
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Reweigh reel",
            f"Reweigh roll {self._roll_text(sel)} with the fresh scale reading"
            f" {fresh:.3f} kg?\nOld gross {float(sel.get('gross') or 0):.3f} kg is replaced;"
            " print is invalidated.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.reweigh_reel(int(sel["id"]), float(fresh))
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh, check the record exists, then retry.", ok=False)
        self.say(f"Roll {self._roll_text(sel)} reweighed — print invalidated,"
                 " reprint needed.", ok=True)
        self.gross.setValue(0)
        try:
            self.table.clearSelection()
        except Exception:
            pass
        self.refresh_history()
        self._refresh_job_info()

    def history_delete(self):
        if not self._is_admin():
            return self.say("Only admin may delete reels.", ok=False)
        sel = self.selected_history()
        if not sel:
            return self.say("Select a reel row first.", ok=False, kind="warning")
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Delete reel",
            f"Delete roll {self._roll_text(sel)} ({float(sel.get('net') or 0):.3f} Kg)?\n"
            "The number is never reused. Packed/dispatched reels are blocked.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.delete_reel(int(sel["id"]))
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh before retrying.", ok=False)
        self.say(f"Roll {self._roll_text(sel)} deleted (number retired).", ok=True)
        self.refresh_history()
        self._refresh_job_info()
