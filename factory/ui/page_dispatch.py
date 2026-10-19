"""Dispatch workspace: customer orders, bay scanning, load tracking.

Loading flow: dispatcher creates an order (customer + product job + pieces
required), scans Ready boxes (QR or plain code) into the loading order.
Pieces are reels: a 2-reel box = 2 pieces. Scanning stops once the piece
target is reached — the crossing box is accepted whole (21 needed, 2-reel
boxes: 22 loaded). Completion requires loaded pieces >= required; legacy
bundle orders (required_pieces NULL) stay exact. Cancelling is allowed only
while loading; completed/cancelled orders are immutable.

Authorization is session-resolved and distinct from production: dispatch
mutations require dispatch.mutate (dispatcher/admin); deletions stay
admin-only via services.
"""
from __future__ import annotations
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QHeaderView, QMessageBox, QLineEdit,
                               QAbstractItemView, QSpinBox, QSizePolicy)
from PySide6.QtCore import Qt
from factory import data_access as DA
from factory.db import bare_carton_no
from factory.hardware import status as HWStatus
from factory.ui.styles import style_button, set_banner
from factory.ui.device_strip import DeviceStrip


def _toast(parent, text: str, ok: bool = True, kind: str | None = None):
    try:
        from factory.ui.toast import toast as _show
        _show(parent, text, ok=ok, kind=kind)
    except Exception:
        pass


# Order status -> progress banner kind (mirrors styles.badge_for_status,
# but banners use ok/err aliases instead of success/error).
_ORDER_BANNER: dict[str, str] = {
    "loading": "info",
    "complete": "ok",
    "completed": "ok",
    "cancelled": "neutral",
}


class DispatchPage(QWidget):
    def __init__(self, on_stock_changed=None, actor: dict | None = None):
        super().__init__()
        self.on_stock_changed = on_stock_changed
        # Caller-supplied actor dicts are NEVER trusted.
        self._ignored_actor = actor
        self.order_id: int | None = None
        self._rows: list[dict] = []
        self._loaded: list[dict] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 12)
        root.setSpacing(8)

        title = QLabel("Dispatch")
        title.setProperty("page", "title")
        root.addWidget(title)

        # ---- station devices (dispatch owns the QR scanner) ----
        self.devices = DeviceStrip("dispatch", show_station_label=True)
        root.addWidget(self.devices)

        # ---- order bar ----
        bar = QHBoxLayout()
        bar.setSpacing(8)
        lab = QLabel("Order")
        lab.setProperty("field", "caption")
        bar.addWidget(lab)
        self.order_combo = QComboBox()
        self.order_combo.setMinimumHeight(40)
        self.order_combo.setMinimumWidth(260)
        self.order_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        bar.addWidget(self.order_combo, 1)
        self.btn_new = QPushButton("New order")
        self.btn_new.setMinimumHeight(40)
        style_button(self.btn_new, "secondary")
        bar.addWidget(self.btn_new)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setMinimumHeight(40)
        style_button(self.btn_refresh, "secondary")
        bar.addWidget(self.btn_refresh)
        root.addLayout(bar)

        self.progress = QLabel("No orders yet — create one to start loading.")
        self.progress.setProperty("status", True)
        self.progress.setWordWrap(True)
        root.addWidget(self.progress)

        # ---- scan row ----
        scan = QHBoxLayout()
        scan.setSpacing(8)
        scan.addWidget(self._caption("Scan box QR:"))
        self.scan = QLineEdit()
        self.scan.setMinimumHeight(48)
        self.scan.setPlaceholderText("Scan or type box code, Enter to load")
        self.scan.setClearButtonEnabled(True)
        scan.addWidget(self.scan, 1)
        self.btn_unscan = QPushButton("Remove")
        self.btn_unscan.setMinimumHeight(48)
        style_button(self.btn_unscan, "secondary")
        self.btn_unscan.setToolTip("Remove the selected box (loading orders only).")
        scan.addWidget(self.btn_unscan)
        root.addLayout(scan)

        # ---- loaded table ----
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Box", "Reels", "Net · kg", "Scanned"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        root.addWidget(self.table, 1)

        # ---- actions ----
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_complete = QPushButton("Complete dispatch")
        self.btn_complete.setMinimumHeight(56)
        style_button(self.btn_complete, "primary")
        self.btn_cancel = QPushButton("Cancel order")
        self.btn_cancel.setMinimumHeight(56)
        style_button(self.btn_cancel, "danger")
        row.addWidget(self.btn_complete, 2)
        row.addWidget(self.btn_cancel, 1)
        root.addLayout(row)

        self.btn_new.clicked.connect(self.new_order)
        self.btn_refresh.clicked.connect(self.refresh)
        self.order_combo.currentIndexChanged.connect(self.order_changed)
        self.scan.returnPressed.connect(self.do_scan)
        self.btn_unscan.clicked.connect(self.do_unscan)
        self.btn_complete.clicked.connect(self.do_complete)
        self.btn_cancel.clicked.connect(self.do_cancel)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.refresh()

    @staticmethod
    def _caption(t: str) -> QLabel:
        c = QLabel(t)
        c.setProperty("field", "caption")
        return c

    @property
    def msg(self) -> str:
        return getattr(self, "_last_say", "")

    def say(self, t, ok=True, kind: str | None = None):
        self._last_say = str(t)
        _toast(self, str(t), ok=ok, kind=kind)

    def _set_progress(self, text: str, kind: str = "neutral"):
        """Order-status line with a semantic banner bubble."""
        self.progress.setText(text)
        try:
            set_banner(self.progress, kind)
        except Exception:
            pass

    def _can_dispatch(self) -> bool:
        try:
            me = DA.current_user()
        except Exception:
            return False
        return bool(me) and str(me.get("role") or "") in ("dispatcher", "admin")

    def _err(self, e) -> str:
        try:
            msg, _kind = DA.describe_error(e)
        except Exception:
            msg = str(e) or "Request failed."
        return msg

    def _need_text(self, o: dict) -> str:
        """Order target as seen by the floor: pieces for new orders, legacy
        bundle counts for old rows (required_pieces NULL)."""
        try:
            if o.get("required_pieces") is not None:
                return f"{int(o.get('loaded_pieces') or 0)}/{int(o['required_pieces'])} pcs"
            return f"{int(o.get('loaded') or 0)}/{int(o.get('required_bundles') or 0)} boxes"
        except Exception:
            return "?"

    # ----- data -----
    def refresh(self, select_id: int | None = None):
        try:
            orders = DA.list_orders()
        except Exception as e:
            self._set_progress(f"Connection issue — {self._err(e)}", "err")
            return
        self._rows = orders
        self.order_combo.blockSignals(True)
        self.order_combo.clear()
        for o in orders:
            self.order_combo.addItem(
                f"{o['order_no']} → {o['customer']} ({self._need_text(o)}, {o['status']})",
                userData=o["id"])
            if select_id and o["id"] == select_id:
                self.order_combo.setCurrentIndex(self.order_combo.count() - 1)
        self.order_combo.blockSignals(False)
        if orders:
            idx = self.order_combo.currentIndex()
            self.order_changed(idx if idx >= 0 else 0)
        else:
            self.order_id = None
            self._loaded = []
            self.table.setRowCount(0)
            self._set_progress("No orders yet — create one to start loading.", "neutral")
        self._update_buttons()

    def selected_order(self) -> dict | None:
        if self.order_id is None:
            return None
        for o in self._rows:
            if o["id"] == self.order_id:
                return o
        return None

    def order_changed(self, i):
        if i < 0 or not self._rows:
            self.order_id = None
            return
        self.order_id = self.order_combo.itemData(i)
        self.refresh_loaded()

    def refresh_loaded(self):
        if self.order_id is None:
            return
        try:
            prog = DA.order_progress(self.order_id)
        except ValueError as e:
            self._loaded = []
            self.table.setRowCount(0)
            self._set_progress(str(e), "warn")
            return
        except Exception as e:
            self._set_progress(f"Connection issue — {self._err(e)}", "err")
            return
        self._loaded = prog.get("items", [])
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for it in self._loaded:
            r = self.table.rowCount()
            self.table.insertRow(r)
            self.table.setItem(r, 0, QTableWidgetItem(bare_carton_no(it.get("carton_no") or "")))
            n = int(it.get("num_reels") or 0)
            self.table.setItem(r, 1, QTableWidgetItem(str(n) if n else "—"))
            self.table.setItem(r, 2, QTableWidgetItem(f"{float(it.get('total_net') or 0):.3f}"))
            self.table.setItem(r, 3, QTableWidgetItem(str(it.get("scanned_at") or "")[:16]))
        self.table.blockSignals(False)
        for o in self._rows:
            if o["id"] == self.order_id:
                o["loaded"] = prog.get("loaded", 0)
                o["loaded_pieces"] = prog.get("loaded_pieces", 0)
                o["remaining"] = prog.get("remaining", 0)
        o = self.selected_order()
        if o:
            if o.get("required_pieces") is not None:
                counts = (f"loaded {prog.get('loaded', 0)} boxes /"
                          f" {prog.get('loaded_pieces', 0)} pcs of {int(o['required_pieces'])}")
            else:
                counts = f"loaded {prog.get('loaded', 0)}/{int(o.get('required_bundles') or 0)} boxes"
            self._set_progress(
                f"{o['order_no']} → {o['customer']} · J:{o.get('job_no','')} {o.get('job_name','')} · "
                f"{counts} · {o['status']}",
                _ORDER_BANNER.get(str(o.get('status') or '').lower(), "neutral"))
            self.order_combo.setItemText(
                self.order_combo.currentIndex(),
                f"{o['order_no']} → {o['customer']} ({self._need_text(o)}, {o['status']})")
        self._update_buttons()

    def _update_buttons(self):
        o = self.selected_order()
        loading = bool(o and o.get("status") == "loading")
        can = self._can_dispatch()
        has_sel = self.table.selectionModel() is not None and bool(
            self.table.selectionModel().selectedRows())
        try:
            self.btn_complete.setEnabled(bool(loading and can))
            self.btn_cancel.setEnabled(bool(loading and can))
            self.btn_unscan.setEnabled(bool(loading and can and has_sel))
            self.btn_new.setEnabled(can)
            if not can:
                self.btn_complete.setToolTip("Dispatcher or admin only.")
                self.btn_cancel.setToolTip("Dispatcher or admin only.")
        except Exception:
            pass

    # ----- actions -----
    def new_order(self):
        from PySide6.QtWidgets import QDialog, QFormLayout, QLineEdit as _LE, QDialogButtonBox
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        try:
            jobs = DA.list_jobs(status="open")
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}", ok=False)
        if not jobs:
            return self.say("No open jobs — create a packaging job first.", ok=False)
        d = QDialog(self)
        d.setWindowTitle("New dispatch order")
        f = QFormLayout(d)
        cust = _LE()
        cust.setPlaceholderText("e.g. PARLE AGRO HYD")
        cust.setMinimumWidth(300)
        f.addRow("Customer:", cust)
        combo = QComboBox()
        combo.setMinimumWidth(300)
        for j in jobs:
            combo.addItem(f"J:{j['job_no']} — {j['job_name']}", userData=j["id"])
        f.addRow("Product job:", combo)
        need = QSpinBox()
        need.setRange(1, 5000)
        need.setValue(2)
        f.addRow("Pieces required:", need)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        f.addRow(bb)
        bb.accepted.connect(d.accept)
        bb.rejected.connect(d.reject)
        if d.exec() != QDialog.Accepted:
            return
        if not cust.text().strip():
            return self.say("Customer name required.", ok=False, kind="warning")
        try:
            o = DA.create_dispatch_order(cust.text(), combo.currentData(),
                                         required_pieces=int(need.value()))
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh, check the order exists, then retry.", ok=False)
        self.say(f"Order {o['order_no']} created"
                 f" ({int(o['required_pieces'])} pieces required).", ok=True)
        self.refresh(select_id=o["id"])

    def do_scan(self):
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        text = self.scan.text()
        if not (text or "").strip():
            return self.say("Scan the box QR again.", ok=False, kind="warning")
        try:
            ct = DA.scan_into_order(self.order_id, text)
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh the order before retrying — the scan may have landed.",
                            ok=False)
        self.scan.clear()
        self.scan.setFocus()
        # A landed scan is the only honest proof the keyboard wedge is reaching
        # this app — flip the scanner pill to active (see hardware/status.py).
        HWStatus.note_scan()
        self.devices.refresh()
        n = int(ct.get("num_reels") or 0)
        self.say(f"Box {bare_carton_no(ct['carton_no'])} loaded"
                 + (f" ({n} reel{'s' if n != 1 else ''})." if n else "."), ok=True)
        self.refresh_loaded()
        if self.on_stock_changed:
            self.on_stock_changed()

    def selected_carton_id(self) -> int | None:
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._loaded[i]["id"] if 0 <= i < len(self._loaded) else None

    def do_unscan(self):
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        cid = self.selected_carton_id()
        if cid is None:
            return self.say("Select a loaded box row first.", ok=False, kind="warning")
        try:
            DA.remove_from_order(self.order_id, cid)
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh before retrying.", ok=False)
        self.say("Box removed (order still loading).", ok=True)
        self.refresh_loaded()
        if self.on_stock_changed:
            self.on_stock_changed()

    def do_complete(self):
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        o = self.selected_order()
        if o and o.get("required_pieces") is not None:
            body = ("Complete this order? Boxes leave Ready stock.\n"
                    f"Requires {int(o['required_pieces'])} pieces (reels) —"
                    " the last box may overshoot by its reel count.")
        else:
            body = ("Complete this order? Boxes leave Ready stock.\n"
                    "Requires the exact bundle count — partial loads are blocked.")
        ans = QMessageBox.question(self, "Complete dispatch", body)
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            done = DA.complete_dispatch(self.order_id)
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh the order before retrying — it may have completed.",
                            ok=False)
        if done.get("required_pieces") is not None:
            self.say(f"Order {done['order_no']} dispatched"
                     f" ({done.get('loaded', 0)} boxes, {done.get('loaded_pieces', 0)} pieces).",
                     ok=True)
        else:
            self.say(f"Order {done['order_no']} dispatched ({done['loaded']} boxes).", ok=True)
        self.refresh(select_id=self.order_id)
        if self.on_stock_changed:
            self.on_stock_changed()

    def do_cancel(self):
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        ans = QMessageBox.question(
            self, "Cancel order",
            "Cancel this loading order? Scanned boxes return to Ready.\n"
            "Completed orders cannot be cancelled.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.cancel_order(self.order_id)
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh before retrying.", ok=False)
        self.say("Order cancelled — boxes back to Ready.", ok=True)
        self.refresh(select_id=self.order_id)
        if self.on_stock_changed:
            self.on_stock_changed()
