"""Packaging workspace: ONE scale, explicit Reel/Carton mode.

Real floor flow: the operator puts either a reel or a packed carton on the
scale and says which it is (the scale cannot infer object type from weight).
Reel mode weighs one reel -> Type-1 sticker (NO QR). Carton mode weighs one
packed box -> Type-2 carton QR sticker; cartons are BOX WEIGHT ONLY, so the
measured box weight is stored as-is with no reel links, no count and no
core breakdown.

The loose-reel list is display-only history; carton packing never consumes
or guesses reels from it.
"""
from __future__ import annotations
import statistics
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                               QPushButton, QDoubleSpinBox, QTableWidget, QTableWidgetItem,
                               QHeaderView, QMessageBox, QLineEdit, QSplitter, QFrame,
                               QAbstractItemView, QScrollArea, QSizePolicy, QTabBar)
from PySide6.QtCore import Qt
from factory import data_access as DA
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

# Preview renders wider so the whole 4x3in sticker fits vertically.
PREVIEW_MAX_WIDTH = 440


def _toast(parent, text: str, ok: bool = True, kind: str | None = None):
    """Brief success/error note. Replaces the old persistent banner."""
    try:
        from factory.ui.toast import toast as _show
        _show(parent, text, ok=ok, kind=kind)
    except Exception:
        pass


class NewJobDialogMixin:
    @staticmethod
    def ask_new_product(parent, initial_name: str = "", initial_wrappers: str = ""):
        """Add-once product entry. Retains typed values across validation
        failures (fields are prefilled from the previous attempt, not reset)."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit,
                                       QDialogButtonBox)
        name_val, wrap_val = initial_name, initial_wrappers
        while True:
            d = QDialog(parent)
            d.setWindowTitle("New product")
            f = QFormLayout(d)
            name = QLineEdit()
            name.setPlaceholderText("e.g. APPY FIZZ-160ML (SEP.28) BODY")
            name.setMinimumWidth(340)
            name.setText(name_val)
            wrappers = QLineEdit()
            wrappers.setPlaceholderText("e.g. 16500")
            wrappers.setText(wrap_val)
            f.addRow("Product Name:", name)
            f.addRow("Wrappers / reel:", wrappers)
            bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
            f.addRow(bb)
            bb.accepted.connect(d.accept)
            bb.rejected.connect(d.reject)
            if d.exec() != QDialog.Accepted:
                return None
            name_val, wrap_val = name.text(), wrappers.text()
            try:
                return DA.create_product(name_val, wrap_val)
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
        """Set wrappers for a migrated-unknown product. Catalog only —
        historical job snapshots keep their NULL and are never rewritten."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit,
                                       QDialogButtonBox, QLabel)
        wrap_val = ""
        while True:
            d = QDialog(parent)
            d.setWindowTitle(f"Wrappers — {product['name']}")
            f = QFormLayout(d)
            info = QLabel("Historical jobs keep 'unknown'; only future jobs use this.")
            info.setWordWrap(True)
            f.addRow(info)
            wrappers = QLineEdit()
            wrappers.setPlaceholderText("e.g. 16500")
            wrappers.setText(wrap_val or str(product.get("wrappers_per_reel") or ""))
            f.addRow("Wrappers / reel:", wrappers)
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
                QMessageBox.warning(parent, "Cannot save wrappers", str(e))
                return None
            except ValueError as e:
                QMessageBox.warning(parent, "Cannot save wrappers", str(e))
                continue
            except Exception as e:
                try:
                    msg, _k = DA.describe_error(e)
                except Exception:
                    msg = str(e)
                QMessageBox.warning(parent, "Cannot save wrappers", msg)
                return None

    @staticmethod
    def ask_new_job(parent):
        """New packaging job from the products master. Retains the selected
        product across validation failures. No Size/Core fields
        (legacy size/core rows stay in the DB, core default unchanged)."""
        from PySide6.QtWidgets import (QDialog, QFormLayout, QComboBox, QPushButton,
                                       QDialogButtonBox, QHBoxLayout, QWidget, QLabel)
        select_id = None
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
                    w = p["wrappers_per_reel"]
                    label = f"{p['name']} — {w}/reel" if w else f"{p['name']} — wrappers unknown"
                    combo.addItem(label, userData=p["id"])
                    if sel and p["id"] == sel:
                        combo.setCurrentIndex(combo.count() - 1)
                combo.blockSignals(False)

            _reload(select_id)
            add_btn = QPushButton("New product…")
            wrap_btn = QPushButton("Set wrappers…")
            wrap_btn.setToolTip("Set wrappers for an unknown product (history untouched).")
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
                    QMessageBox.warning(d, "Wrappers", "Select a product first.")
                    return
                if prod.get("wrappers_per_reel"):
                    QMessageBox.warning(d, "Wrappers",
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
            hint = QLabel("Jobs run continuously across months; roll/box numbers reset monthly.")
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
            try:
                return DA.create_job_for_product(pid)
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


class PackagingPage(QWidget, NewJobDialogMixin):
    def __init__(self, on_stock_changed=None, actor: dict | None = None):
        super().__init__()
        self.on_stock_changed = on_stock_changed
        # Caller-supplied actor dicts are NEVER trusted (forged-role bypass).
        # Authorization resolves from the authenticated session on every use.
        self._ignored_actor = actor
        self.job_id: int | None = None
        self._mode = "idle"
        self._history_rows: list[dict] = []
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
        self.job_info = QLabel("")
        self.job_info.setProperty("jobinfo", True)
        self.job_info.setWordWrap(True)
        self.job_info.setMinimumWidth(220)
        self.job_info.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        bar.addWidget(self.job_info, 2)
        root.addLayout(bar)

        # ---- explicit stage: the scale cannot infer object type ----
        self.mode_tab = QTabBar()
        self.mode_tab.addTab("Stage 1 — Reel sticker")
        self.mode_tab.addTab("Stage 2 — Box packing")
        self.mode_tab.setExpanding(False)
        root.addWidget(self.mode_tab)
        self.stage_caption = QLabel("Stage 1 — weigh a single reel, save it, print its sticker (no QR).")
        self.stage_caption.setProperty("hint", True)
        self.stage_caption.setWordWrap(True)
        root.addWidget(self.stage_caption)

        # ---- station devices (weigh machine + label printer) ----
        # Lives in the ROOT layout, not the weigh card: the left pane is only
        # ~360px wide and two pills need ~560px, so inside the card they were
        # squeezed until the names overlapped the detail text. This is
        # page-level status anyway, so it belongs above the split.
        self.devices = DeviceStrip("packaging", show_station_label=True, demo_switch=True)
        root.addWidget(self.devices)

        split = QSplitter(Qt.Horizontal)
        split.setHandleWidth(8)
        root.addWidget(split, 1)

        # ---- left: the one scale ----
        a = QFrame()
        a.setProperty("card", True)
        la = QVBoxLayout(a)
        la.setContentsMargins(16, 16, 16, 16)
        la.setSpacing(10)
        step_a = QLabel("Weigh")
        step_a.setProperty("step", True)
        step_a.setWordWrap(True)
        la.addWidget(step_a)

        la.addWidget(self._caption("Gross weight · kg"))
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
        # Hardware status below is display-only.
        self._mock_wrap = None
        self._hw_was_mock = isinstance(HW.scale, HW.MockEssaeDS215)

        self.core_wrap = QWidget()
        row_core = QHBoxLayout(self.core_wrap)
        row_core.setContentsMargins(0, 0, 0, 0)
        row_core.setSpacing(8)
        row_core.addWidget(self._caption("Core (kg):"))
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

        self.lcd = QLabel("— put reel or carton on scale —")
        self.lcd.setProperty("lcd", "net-idle")
        self.lcd.setAlignment(Qt.AlignCenter)
        self.lcd.setWordWrap(True)
        self.lcd.setMinimumHeight(84)
        la.addWidget(self.lcd)
        la.addStretch(1)
        a.setMinimumWidth(340)
        scroll_a = QScrollArea()
        scroll_a.setWidgetResizable(True)
        scroll_a.setFrameShape(QFrame.NoFrame)
        scroll_a.setWidget(a)
        scroll_a.setMinimumWidth(360)
        # Left pane: weigh card (scrolls) gets the stretch + the one live
        # sticker preview in a compact fixed card below it. The preview
        # holds NO layout stretch so it never squeezes the weigh card;
        # the sticker pixmap scales to fit inside (see set_preview_label).
        left_pane = QWidget()
        left_lay = QVBoxLayout(left_pane)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(8)
        left_lay.addWidget(scroll_a, 1)
        pv = QFrame()
        pv.setProperty("card", True)
        pv.setMaximumHeight(288)
        lp = QVBoxLayout(pv)
        lp.setContentsMargins(12, 12, 12, 12)
        lp.setSpacing(6)
        self.sticker_cap = self._caption("Sticker preview · 4 x 3 in")
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
        left_lay.addWidget(pv, 0)
        split.addWidget(left_pane)
        self._left_lay = left_lay
        self._preview_card = pv

        # ---- right: job history (ALL reels, not merely loose) + stage action ----
        b = QFrame()
        b.setProperty("card", True)
        lb = QVBoxLayout(b)
        lb.setContentsMargins(16, 16, 16, 16)
        lb.setSpacing(10)
        step_b = QLabel("Stage history — all reels of this job")
        step_b.setProperty("step", True)
        step_b.setWordWrap(True)
        lb.addWidget(step_b)
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
        lb.addWidget(self.table, 1)
        self.sel_info = QLabel("No reels yet — weigh a reel in Stage 1 to start.")
        self.sel_info.setProperty("status", True)
        self.sel_info.setWordWrap(True)
        lb.addWidget(self.sel_info)
        hist_row = QHBoxLayout()
        hist_row.setSpacing(8)
        self.btn_hist_print = QPushButton("Print / Reprint")
        self.btn_hist_reweigh = QPushButton("Reweigh")
        self.btn_hist_delete = QPushButton("Delete")
        for hb in (self.btn_hist_print, self.btn_hist_reweigh, self.btn_hist_delete):
            hb.setMinimumHeight(44)
            style_button(hb, "secondary")
            hist_row.addWidget(hb)
        lb.addLayout(hist_row)
        self.btn_action = QPushButton("Weigh first")
        self.btn_action.setMinimumHeight(66)
        style_button(self.btn_action, "secondary")
        self.btn_action.setEnabled(False)
        lb.addWidget(self.btn_action)
        b.setMinimumWidth(340)
        b.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        split.addWidget(b)
        split.setStretchFactor(1, 1)
        self._right_card = b
        self._right_lay = lb
        split.setCollapsible(0, False)
        split.setCollapsible(1, False)
        split.setSizes([480, 580])

        # signals
        self.btn_new.clicked.connect(self.new_job)
        self.btn_product.clicked.connect(self.new_product)
        self.job_combo.currentIndexChanged.connect(self.job_changed)
        self.mode_tab.currentChanged.connect(lambda *_: self._refresh_state())
        self.gross.valueChanged.connect(lambda *_: self._refresh_state())
        self.core.valueChanged.connect(lambda *_: self._refresh_state())
        self.btn_scale.clicked.connect(self.read_scale)
        self.btn_stop.clicked.connect(self.stop_wt)
        self.btn_hist_print.clicked.connect(self.history_print)
        self.btn_hist_reweigh.clicked.connect(self.history_reweigh)
        self.btn_hist_delete.clicked.connect(self.history_delete)
        self.table.itemSelectionChanged.connect(self._update_history_buttons)
        self.btn_action.clicked.connect(self.do_action)
        self.refresh_jobs()
        self._refresh_state()
        self._update_history_buttons()

    @staticmethod
    def _caption(t: str) -> QLabel:
        c = QLabel(t)
        c.setProperty("field", "caption")
        return c

    @property
    def pack_mode(self) -> str:
        """Explicit stage choice: 'reel' (Stage 1) or 'carton' (Stage 2)."""
        return "carton" if self.mode_tab.currentIndex() == 1 else "reel"

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

    # ----- sticker preview (fixed 4 x 3in Citizen stock, live what-will-print) -----
    @staticmethod
    def _next_roll_no(job_id: int) -> int:
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
    def _next_carton_no() -> str:
        try:
            from factory.db import current_period, carton_no_for
            return carton_no_for(current_period(), DA.predict_next_box_seq())
        except Exception:
            return "C????"

    def _render_previews(self):
        """One live sticker preview under the Weigh card. Read-only.

        Reel mode -> Type-1 reel art from the live scale weight only;
        Carton mode -> Type-2 box-only art from the live scale weight only;
        anything else -> hint text. Never writes to the database.
        """
        lbl = getattr(self, "sticker_preview", None)
        cap = getattr(self, "sticker_cap", None)
        if lbl is None:
            return
        job = self.current_job()
        if job is None:
            if cap is not None:
                cap.setText("Sticker preview · 4 x 3 in")
            set_preview_label(lbl, "Select a job to preview stickers.")
            return
        try:
            if self.pack_mode == "carton":
                if self.gross.value() <= 0:
                    if cap is not None:
                        cap.setText("Sticker preview · 4 x 3 in")
                    set_preview_label(lbl, "Weigh to preview sticker.")
                else:
                    if cap is not None:
                        cap.setText("Sticker preview · Type-2 carton (WITH QR)")
                    set_preview_label(lbl, self._carton_art(job))
            else:
                if self.gross.value() <= 0:
                    if cap is not None:
                        cap.setText("Sticker preview · 4 x 3 in")
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
            seq = DA.predict_next_reel_seq()
            per = current_period()
        except Exception:
            seq, per = 1, current_period()
        reel = {"gross": round(scale, 3), "core": round(core, 3),
                "net": round(scale - core, 3), "created_at": now_iso(),
                "roll_no": seq, "roll_period": per,
                "roll_code": roll_code_for(per, seq)}
        return pil_to_pixmap(LabelArt.render_reel(
            job, reel, width_mm=PRINTER_WIDTH_MM, height_mm=PRINTER_HEIGHT_MM),
            max_width=PREVIEW_MAX_WIDTH)

    def _carton_art(self, job: dict):
        """Type-2 box-only art from the live scale weight only. No reel links."""
        from factory.db import now_iso, current_period, carton_no_for
        scale = self.gross.value()
        if scale <= 0:
            return "Weigh to preview sticker."
        try:
            seq = DA.predict_next_box_seq()
            per = current_period()
        except Exception:
            seq, per = 1, current_period()
        cn = carton_no_for(per, seq)
        detail = {"job_no": job["job_no"], "job_name": job["job_name"],
                  "packed_at": now_iso(),
                  "num_reels": 0, "carton_no": cn,
                  "bundle_no": self._next_bundle_no(job["id"]),
                  "box_period": per,
                  "total_gross": round(scale, 3),
                  "total_core": 0, "total_net": round(scale, 3),
                   "reels": [],
                   "qr": DA.qr_payload(cn)}
        return pil_to_pixmap(LabelArt.render_weight_carton(
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
            self.job_id = None

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

    def job_changed(self, i):
        if i < 0:
            return
        self.job_id = self.job_combo.itemData(i)
        job = self.current_job()
        if job:
            try:
                self.core.blockSignals(True)
                self.core.setValue(float(job["core_wt"]))
            finally:
                try:
                    self.core.blockSignals(False)
                except Exception:
                    pass
            try:
                st = DA.job_stats(job["id"])
            except Exception as e:
                self.job_info.setText(
                    f"J:{job['job_no']} {job['job_name']} — connection issue.")
                self.refresh_history()
                return
            w = job.get("wrappers_per_reel")
            wtxt = f"{w}/reel" if w else "wrappers unknown"
            self.job_info.setText(
                f"J:{job['job_no']} {job['job_name']} ({wtxt})  ·  "
                f"Loose {st['loose']}  ·  Ready {st['ready_cartons']}")
        self.refresh_history()

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

    def _refresh_state(self):
        """Stage-gated brain: only the active stage's controls are visible
        and enabled; the other stage's controls are hidden. No weight-based
        object detection: the operator picks Stage 1 (reel) / Stage 2 (box).
        """
        scale, core = self.gross.value(), self.core.value()
        is_reel = self.pack_mode == "reel"
        # stage visibility: core entry only matters in Stage 1
        try:
            self.core_wrap.setVisible(is_reel)
        except Exception:
            pass
        try:
            if is_reel:
                self.stage_caption.setText(
                    "Stage 1 — weigh a single reel, save it, print its sticker (no QR).")
            else:
                self.stage_caption.setText(
                    "Stage 2 — weigh a packed box, pack it (gross only), print its QR sticker."
                    " Boxes are independent of reels.")
        except Exception:
            pass

        if self.job_id is None:
            self._set_lcd("— select a job first —", "net-warn")
            self._set_action("Select a job", "secondary", False)
            self._mode = "nojob"
            return
        if not self._can_produce():
            self._set_lcd("Read-only — dispatcher cannot weigh or pack.", "net-idle")
            self._set_action("Read only", "secondary", False)
            self._mode = "readonly"
            return

        if not is_reel:
            self._mode = "carton"
            if scale <= 0:
                self._set_lcd("Put a packed carton on the scale", "net-idle")
                self._set_action("Weigh first", "secondary", False)
            else:
                self._set_lcd(f"Box: {scale:.3f} Kg — ready to pack.", "net-ok")
                self._set_action("Pack & print carton", "primary", True)
            return

        self._mode = "reel"
        if scale <= 0:
            self._set_lcd("Put a reel on the scale", "net-idle")
            self._set_action("Weigh first", "secondary", False)
            return
        net = round(scale - core, 3)
        if net <= 0:
            self._set_lcd(f"Net {net:.3f} — not possible. Check gross and core.", "net-bad")
            self._set_action("Check weights", "secondary", False)
            return
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
        if self.pack_mode == "reel" and self.gross.value() > 0:
            self.say("Reel on scale.", ok=True)
        elif self.pack_mode == "carton" and self.gross.value() > 0:
            self.say("Carton on scale.", ok=True)

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
            return self.say("Place a reel or carton on the scale first.", ok=False, kind="warning")
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
            # Static core default preserved (job core_wt); no .720 assumption.
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
            self.say(f"Roll saved ({reel['net']:.3f} Kg). {e} Label stays pending.", ok=True)
            self.refresh_history()
            self.job_changed(self.job_combo.currentIndex())
            self.gross.setValue(0)
            if self.on_stock_changed:
                self.on_stock_changed()
            return
        job = self.current_job()
        ident = str(reel.get("roll_code") or reel["roll_no"])
        self.say(f"Roll {ident} saved ({reel['net']:.3f} Kg).", ok=True)
        self.refresh_history()
        self.job_changed(self.job_combo.currentIndex())
        ReelLabelDialog(self, job, reel, HW.printer).exec()
        self.refresh_history()
        if self.on_stock_changed:
            self.on_stock_changed()
        self.gross.setValue(0)

    def pack_box(self):
        """Pack the weighed box: measured gross stored as-is, no reel links."""
        if not self._can_produce():
            return self.say("Read-only role cannot pack boxes.", ok=False)
        if self.job_id is None:
            return self.say("Select a job first.", ok=False, kind="warning")
        if self.gross.value() <= 0:
            return self.say("Put the packed carton on the scale first.", ok=False, kind="warning")
        try:
            DEV.require_scale()
        except RuntimeError as e:
            return self.say(str(e), ok=False)
        try:
            ct = DA.pack_weight_carton(self.job_id, self.gross.value())
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._backend_error(e)}"
                            " Refresh, check the record exists, then retry.", ok=False)
        try:
            DEV.require_printer()
        except RuntimeError as e:
            self.say(f"Carton {ct['carton_no']} packed ({ct['total_gross']:.3f} Kg). {e} Label stays pending.", ok=True)
            self.refresh_history()
            self.job_changed(self.job_combo.currentIndex())
            self.gross.setValue(0)
            if self.on_stock_changed:
                self.on_stock_changed()
            return
        try:
            detail = DA.carton_detail(ct["id"])
        except Exception as e:
            self.say(f"Carton {ct['carton_no']} packed but details unreadable:"
                     f" {self._backend_error(e)} Label stays pending.", ok=True)
            self.refresh_history()
            self.job_changed(self.job_combo.currentIndex())
            self.gross.setValue(0)
            if self.on_stock_changed:
                self.on_stock_changed()
            return
        self.say(f"Carton {ct['carton_no']} packed ({ct['total_gross']:.3f} Kg).", ok=True)
        self.refresh_history()
        self.job_changed(self.job_combo.currentIndex())
        CartonLabelDialog(self, detail, HW.printer).exec()
        self.refresh_history()
        if self.on_stock_changed:
            self.on_stock_changed()
        self.gross.setValue(0)

    # Back-compat alias: the floor flow packs the weighed box.
    pack = pack_box

    # ----- loose reels (display-only; never consumed by carton packing) -----
    # ----- job history: ALL reels (loose + packed + dispatched) -----
    def _label_text(self, r: dict) -> str:
        st = str(r.get("print_status") or "pending")
        n = int(r.get("print_count") or 0)
        if st == "printed" and n > 0:
            return f"Printed ×{n}"
        return "Pending"

    def _roll_text(self, r: dict) -> str:
        try:
            from factory.db import full_roll_label
            return full_roll_label(r)
        except Exception:
            return str(r.get("roll_no"))

    def refresh_history(self):
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        self._history_rows = []
        if self.job_id is not None:
            try:
                rows = DA.job_reels(self.job_id)
            except Exception as e:
                rows = []
                self.sel_info.setText(
                    f"Connection issue — {self._backend_error(e)}")
                self.table.blockSignals(False)
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
        self.table.blockSignals(False)
        self.update_history_info()
        self._update_history_buttons()

    # Back-compat: old callers used the loose-only name.
    def refresh_loose(self):
        self.refresh_history()

    def update_history_info(self):
        rows = self._history_rows
        if not rows:
            self.sel_info.setText("No reels yet — weigh a reel in Stage 1 to start.")
        else:
            loose = sum(1 for r in rows if r.get("status") == "loose")
            self.sel_info.setText(f"{len(rows)} reels ({loose} loose) — select a row to Print / Reweigh / Delete.")
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

    def _update_history_buttons(self):
        sel = self.selected_history()
        has = sel is not None
        can_prod = self._can_produce()
        try:
            self.btn_hist_print.setEnabled(bool(has))
            self.btn_hist_reweigh.setEnabled(bool(has and can_prod))
            self.btn_hist_delete.setEnabled(bool(has and self._is_admin()))
            if has and not can_prod:
                self.btn_hist_reweigh.setToolTip("Read-only role.")
            if has and not self._is_admin():
                self.btn_hist_delete.setToolTip("Admin only.")
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
                f"Place {self._roll_text(sel)} on the scale for a fresh measurement first"
                " — the old weight is never reused.", ok=False, kind="warning")
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Reweigh reel",
            f"Reweigh {self._roll_text(sel)} with the fresh scale reading"
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
        self.say(f"{self._roll_text(sel)} reweighed — print invalidated, reprint needed.", ok=True)
        self.gross.setValue(0)
        try:
            self.table.clearSelection()
        except Exception:
            pass
        self.refresh_history()
        self.job_changed(self.job_combo.currentIndex())

    def history_delete(self):
        if not self._is_admin():
            return self.say("Only admin may delete reels.", ok=False)
        sel = self.selected_history()
        if not sel:
            return self.say("Select a reel row first.", ok=False, kind="warning")
        from PySide6.QtWidgets import QMessageBox
        ans = QMessageBox.question(
            self, "Delete reel",
            f"Delete {self._roll_text(sel)} ({float(sel.get('net') or 0):.3f} Kg)?\n"
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
        self.say(f"{self._roll_text(sel)} deleted (number retired).", ok=True)
        self.refresh_history()
        self.job_changed(self.job_combo.currentIndex())
