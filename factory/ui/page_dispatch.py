"""Dispatch workspace: customer orders, bay scanning, load tracking.

Loading flow: a dispatcher creates an order (customer + product job +
customer PO + pieces required), then scans Ready boxes (QR or plain code)
into the loading order. New orders count WRAPPERS: a 3-coil box at 38,740
pieces/coil is 116,220 pieces. Boxes are numbered 1..N for that client order
in scan order and renumbered when one is removed; that client number is what
prints as BOX NO on the wide sticker, never the scanned carton code.
Scanning stops once the target is reached — the crossing box is accepted
whole. Completion requires loaded pieces >= required; legacy bundle/reel
orders (piece_unit 'reel' or required_pieces NULL) stay exact. Cancelling is
allowed only while loading; completed/cancelled orders are immutable.

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
from factory.hardware import mock as HW
from factory.hardware import devices as DEV
from factory.hardware import status as HWStatus
from factory.ui.label_preview import ClientLabelDialog
from factory.ui.styles import style_button, set_banner
from factory.ui.device_strip import DeviceStrip


def _piece_alert(loaded: int, required: int, unit: str = "pcs"):
    """Progressive loading alert: 75% on track, 90% almost done, target/over.

    Returns (text, toast/banner kind) or None below 75%. The backend still
    owns the hard stop; this only tells the operator how close they are.
    """
    try:
        loaded, required = int(loaded), int(required)
    except (TypeError, ValueError):
        return None
    if required <= 0:
        return None
    pct = loaded * 100.0 / required
    if loaded >= required:
        over = loaded - required
        if over > 0:
            return (f"Target reached — over by {over:,} {unit}.", "warning")
        return (f"Target reached — {loaded:,}/{required:,} {unit}.", "success")
    left = required - loaded
    if pct >= 90:
        return (f"Almost done — {left:,} {unit} left "
                f"({loaded:,}/{required:,}).", "warning")
    if pct >= 75:
        return (f"{left:,} {unit} left ({pct:.0f}% loaded).", "info")
    return None


def _compact_pcs(pcs) -> str:
    """Short stock count for picker labels: 116220 -> '1.2L', 9500 -> '9.5k'."""
    try:
        p = int(pcs)
    except (TypeError, ValueError):
        return "—"
    if p >= 100_000:
        return f"{p / 100_000:.1f}L"
    if p >= 1_000:
        return f"{p / 1_000:.1f}k"
    return f"{p:,}"


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
        self._alert = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 12, 0, 12)
        root.setSpacing(8)

        title = QLabel("Dispatch")
        title.setProperty("page", "title")
        root.addWidget(title)

        # ---- station devices (dispatch owns the QR scanner) ----
        self.devices = DeviceStrip("dispatch", show_station_label=True,
                                     demo_switch=True)
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
        self.btn_sticker = QPushButton("Sticker")
        self.btn_sticker.setMinimumHeight(48)
        self.btn_sticker.setMinimumWidth(96)
        style_button(self.btn_sticker, "secondary")
        self.btn_sticker.setToolTip(
            "Print the wide client sticker for the selected box.")
        scan.addWidget(self.btn_sticker)
        self.btn_unscan = QPushButton("Remove")
        self.btn_unscan.setMinimumHeight(48)
        style_button(self.btn_unscan, "secondary")
        self.btn_unscan.setToolTip("Remove the selected box (loading orders only).")
        scan.addWidget(self.btn_unscan)
        # Test aid: only visible when mock_qr is enabled on this PC. It loads a
        # real Ready box through the same scan_into_order path as a scanner, so
        # the label preview/print can be tested without a QR scanner. It never
        # changes the printer mode — a configured real printer still prints.
        self.btn_test_scan = QPushButton("Test scan")
        self.btn_test_scan.setMinimumHeight(48)
        self.btn_test_scan.setMinimumWidth(104)
        style_button(self.btn_test_scan, "outline")
        self.btn_test_scan.setToolTip(
            "Testing aid: load the next Ready box as if its QR was scanned.\n"
            "Enable/disable in About (admin). It does not change printer mode.")
        scan.addWidget(self.btn_test_scan)
        root.addLayout(scan)

        # ---- loaded table ----
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Client box", "Box", "Reels", "Pieces", "Net · kg", "Scanned"])
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
        self.btn_slip = QPushButton("Slip")
        self.btn_slip.setMinimumHeight(56)
        self.btn_slip.setMinimumWidth(96)
        style_button(self.btn_slip, "secondary")
        self.btn_slip.setToolTip(
            "Open the dispatch slip (whole order details) — saved at completion, reprintable.")
        row.addWidget(self.btn_complete, 2)
        row.addWidget(self.btn_cancel, 1)
        row.addWidget(self.btn_slip, 0)
        root.addLayout(row)

        self.btn_new.clicked.connect(self.new_order)
        self.btn_refresh.clicked.connect(self.refresh)
        self.order_combo.currentIndexChanged.connect(self.order_changed)
        self.scan.returnPressed.connect(self.do_scan)
        self.btn_sticker.clicked.connect(self.print_sticker)
        self.btn_unscan.clicked.connect(self.do_unscan)
        self.btn_test_scan.clicked.connect(self.do_test_scan)
        self.btn_complete.clicked.connect(self.do_complete)
        self.btn_cancel.clicked.connect(self.do_cancel)
        self.btn_slip.clicked.connect(self.open_slip)
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

    def _sync_test_scan(self) -> None:
        """Show the test aid only when this PC has mock_qr enabled."""
        try:
            on = DEV.mock_qr_enabled()
        except Exception:
            on = False
        try:
            self.btn_test_scan.setVisible(bool(on))
        except Exception:
            pass

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
                unit = ("pcs" if str(o.get("piece_unit") or "reel") != "reel"
                        else "coils")
                return (f"{int(o.get('loaded_pieces') or 0)}/"
                        f"{int(o['required_pieces'])} {unit}")
            return f"{int(o.get('loaded') or 0)}/{int(o.get('required_bundles') or 0)} boxes"
        except Exception:
            return "?"

    # ----- data -----
    def refresh(self, select_id: int | None = None):
        self._sync_test_scan()
        try:
            orders = [o for o in DA.list_orders()
                      if o.get("status") != "cancelled"]
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
        unit = str(prog.get("piece_unit") or "reel")
        wpr = int(prog.get("wrappers_per_reel") or 0)
        self.table.blockSignals(True)
        self.table.setRowCount(0)
        for it in self._loaded:
            r = self.table.rowCount()
            self.table.insertRow(r)
            box_no = int(it.get("client_box_no") or (r + 1))
            n = int(it.get("num_reels") or 0)
            pieces = n * wpr if unit == "wrapper" else 0
            self.table.setItem(r, 0, QTableWidgetItem(str(box_no)))
            self.table.setItem(r, 1, QTableWidgetItem(bare_carton_no(it.get("carton_no") or "")))
            self.table.setItem(r, 2, QTableWidgetItem(str(n) if n else "—"))
            self.table.setItem(r, 3, QTableWidgetItem(f"{pieces:,}" if pieces else "—"))
            self.table.setItem(r, 4, QTableWidgetItem(f"{float(it.get('total_net') or 0):.3f}"))
            self.table.setItem(r, 5, QTableWidgetItem(str(it.get("scanned_at") or "")[:16]))
        self.table.blockSignals(False)
        for o in self._rows:
            if o["id"] == self.order_id:
                o["loaded"] = prog.get("loaded", 0)
                o["loaded_pieces"] = prog.get("loaded_pieces", 0)
                o["remaining"] = prog.get("remaining", 0)
        o = self.selected_order()
        self._alert = None
        if o:
            alert = None
            unit_word = "pcs" if unit != "reel" else "coils"
            if o.get("required_pieces") is not None:
                required = int(o["required_pieces"])
                loaded_pieces = int(prog.get("loaded_pieces") or 0)
                counts = (f"loaded {prog.get('loaded', 0)} boxes /"
                          f" {loaded_pieces:,} {unit_word} of {required:,}")
                alert = _piece_alert(loaded_pieces, required, unit_word)
            else:
                counts = f"loaded {prog.get('loaded', 0)}/{int(o.get('required_bundles') or 0)} boxes"
            self._alert = alert
            text = (f"{o['order_no']} → {o['customer']} · "
                    f"J:{o.get('job_no','')} {o.get('job_name','')} · "
                    f"{counts} · {o['status']}")
            if alert:
                text += f" · {alert[0]}"
            self._set_progress(
                text,
                alert[1] if alert
                else _ORDER_BANNER.get(str(o.get('status') or '').lower(), "neutral"))
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
            self.btn_sticker.setEnabled(bool(can and has_sel))
            self.btn_sticker.setToolTip(
                "Print the wide client sticker for the selected box."
                if (can and has_sel) else "Select a loaded box first.")
            self.btn_new.setEnabled(can)
            self.btn_slip.setEnabled(bool(can and o))
            if not can:
                self.btn_complete.setToolTip("Dispatcher or admin only.")
                self.btn_cancel.setToolTip("Dispatcher or admin only.")
        except Exception:
            pass

    # ----- actions -----
    def new_order(self):
        from PySide6.QtWidgets import (QDialog, QFormLayout, QLineEdit as _LE,
                                       QDialogButtonBox)
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        try:
            jobs = DA.list_jobs(status="open")
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}", ok=False)
        if not jobs:
            return self.say("No open jobs — create a packaging job first.", ok=False)
        by_id = {int(j["id"]): j for j in jobs}
        # Live Ready totals for the picker: ONE aggregate query (local or one
        # LAN round trip), never per-job reads. Non-fatal — the dialog still
        # opens with plain labels if the backend can't answer.
        try:
            _raw = DA.ready_stock_summary() or {}
            stock = {int(k): (v or {}) for k, v in _raw.items()}
        except Exception:
            stock = {}
        d = QDialog(self)
        d.setWindowTitle("New dispatch order")
        f = QFormLayout(d)
        cust = _LE()
        cust.setPlaceholderText("e.g. PARLE AGRO HYD")
        cust.setMinimumWidth(360)
        f.addRow("Customer:", cust)
        combo = QComboBox()
        combo.setMinimumWidth(480)
        for j in jobs:
            jid = int(j["id"])
            s = stock.get(jid, {})
            boxes = int(s.get("boxes") or 0)
            coils = int(s.get("coils") or 0)
            pcs = s.get("pieces")
            if boxes <= 0:
                have = "no ready stock"
            elif pcs is None:
                have = (f"{boxes} box{'es' if boxes != 1 else ''} · "
                        f"{coils} coil{'s' if coils != 1 else ''}")
            else:
                have = (f"{boxes} box{'es' if boxes != 1 else ''} · "
                        f"{coils} coil{'s' if coils != 1 else ''} · "
                        f"{_compact_pcs(pcs)} pcs")
            label = f"J:{j['job_no']} — {j['job_name']} [{have}]"
            combo.addItem(label, userData=j["id"])
            combo.setItemData(combo.count() - 1, label,
                              Qt.ItemDataRole.ToolTipRole)
        f.addRow("Product job:", combo)
        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setMinimumWidth(360)
        hint.setProperty("hint", True)
        f.addRow("", hint)
        po = _LE()
        po.setPlaceholderText("Client PO number — always printed on the sticker")
        po.setMinimumWidth(360)
        f.addRow("Customer PO:", po)
        batch = _LE()
        batch.setPlaceholderText("Optional — blank = SVSF/MON-DD from coil mfg date")
        batch.setMinimumWidth(360)
        f.addRow("Batch no:", batch)
        need = QSpinBox()
        need.setRange(1, 100_000_000)
        need.setValue(100_000)
        need.setSingleStep(1000)
        need.setGroupSeparatorShown(True)
        need.setMinimumWidth(180)
        f.addRow("Pieces required (wrappers):", need)
        note = QLabel("Client orders are in pieces (wrappers), not boxes. "
                      "The sticker numbers boxes 1..N as they are scanned.")
        note.setWordWrap(True)
        note.setProperty("hint", True)
        f.addRow("", note)
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        f.addRow(bb)
        save_btn = bb.button(QDialogButtonBox.Save)

        def current_wpr() -> int:
            j = by_id.get(int(combo.currentData() or 0)) or {}
            return int(j.get("wrappers_per_reel") or 0)

        def update_hint():
            w = current_wpr()
            jid = int(combo.currentData() or 0)
            s = stock.get(jid, {})
            boxes = int(s.get("boxes") or 0)
            if s.get("pieces") is None:
                avail = (f"Ready stock: {boxes} box{'es' if boxes != 1 else ''}."
                         if boxes else "Ready stock: none — pack boxes first.")
            else:
                avail = (f"Ready stock: {boxes} box{'es' if boxes != 1 else ''} · "
                         f"{int(s.get('coils') or 0):,} coils · "
                         f"{int(s['pieces']):,} pcs.")
            if w > 0:
                hint.setText(f"{avail} Pieces per coil: {w:,}. "
                             "The order target is total wrappers (pieces).")
                hint.setStyleSheet("")
                save_btn.setEnabled(True)
            else:
                hint.setText(f"{avail} This job has no wrappers-per-reel count. "
                             "Set it on the product/job first.")
                hint.setStyleSheet("color: #B45309; font-weight: 600;")
                save_btn.setEnabled(False)

        combo.currentIndexChanged.connect(lambda _i: update_hint())
        update_hint()

        def validate_and_accept():
            if not cust.text().strip():
                QMessageBox.warning(d, "New order", "Customer name required.")
                return
            if not po.text().strip():
                QMessageBox.warning(
                    d, "New order",
                    "Customer PO required — it always prints on the sticker.")
                return
            if current_wpr() <= 0:
                QMessageBox.warning(
                    d, "New order",
                    "Set wrappers per reel on this job/product first.")
                return
            d.accept()

        bb.accepted.connect(validate_and_accept)
        bb.rejected.connect(d.reject)
        if d.exec() != QDialog.Accepted:
            return
        try:
            o = DA.create_dispatch_order(
                cust.text(), combo.currentData(),
                required_pieces=int(need.value()),
                customer_po=po.text(), batch_no=batch.text(),
                piece_unit="wrapper")
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh, check the order exists, then retry.", ok=False)
        self.say(f"Order {o['order_no']} created"
                 f" ({int(o['required_pieces']):,} pcs required).", ok=True)
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
        self.refresh_loaded()
        n = int(ct.get("num_reels") or 0)
        box_no = int(ct.get("client_box_no") or 0)
        msg = f"Box {box_no} loaded"
        if n:
            msg += f" ({n} coil{'s' if n != 1 else ''})"
        msg += "."
        alert = self._alert
        if alert:
            msg += " " + alert[0]
        self.say(msg, ok=True, kind=alert[1] if alert else None)
        if self.on_stock_changed:
            self.on_stock_changed()
        # New wrapper orders print the wide client sticker from the dispatch
        # station. Legacy bundle/reel orders have no client sticker, so they
        # stay on the scan-only path (the Sticker button can still preview).
        cur = self.selected_order() or {}
        auto = (str(cur.get("piece_unit") or "reel") == "wrapper"
                and cur.get("required_pieces") is not None)
        if auto:
            self._open_client_sticker(ct["id"])

    def do_test_scan(self) -> None:
        """Testing aid: scan the next Ready box for this order without a scanner.

        Uses the same payload/path as a real keyboard-wedge scan
        (`VASAVI:<carton_no>` -> scan_into_order), so it exercises product
        matching, client box numbering, the progress alerts, and the wide
        sticker preview exactly like a physical QR. It does not touch the
        printer/scale selection: a real configured printer still prints.
        """
        if not DEV.mock_qr_enabled():
            return self.say("Test scan is off for this PC. Enable it in About.",
                            ok=False, kind="warning")
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        order = self.selected_order() or {}
        if str(order.get("status") or "") != "loading":
            return self.say(
                f"Order is {order.get('status') or 'not loading'}. "
                "Only loading orders accept scans.", ok=False, kind="warning")
        try:
            ready = DA.ready_stock(order.get("job_id"))
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}"
                            " Refresh and try again.", ok=False)
        loaded = {int(it.get("id")) for it in self._loaded
                  if it.get("id") is not None}
        candidates = [r for r in ready
                      if int(r.get("id") or 0) not in loaded]
        if not candidates:
            return self.say(
                "No Ready boxes left for this order's job. "
                "Pack another box first.", ok=False, kind="warning")
        box = candidates[0]
        self.scan.setText(DA.qr_payload(str(box.get("carton_no") or "")))
        self.do_scan()

    def _open_client_sticker(self, carton_id: int) -> None:
        """Preview + print the wide sticker for one loaded box."""
        try:
            DEV.require_printer()
            detail = DA.client_label_detail(self.order_id, carton_id)
            ClientLabelDialog(self, detail, HW.printer).exec()
        except RuntimeError as e:
            self.say(f"Box loaded. Sticker not printed: {e}",
                     ok=False, kind="warning")
        except (ValueError, PermissionError) as e:
            self.say(f"Box loaded. Sticker unavailable: {e}",
                     ok=False, kind="warning")
        except Exception as e:
            self.say(f"Box loaded. Sticker unavailable: {self._err(e)}",
                     ok=False, kind="warning")

    def print_sticker(self):
        """Reprint the wide client sticker for the selected loaded box."""
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        cid = self.selected_carton_id()
        if cid is None:
            return self.say("Select a loaded box row first.", ok=False, kind="warning")
        self._open_client_sticker(cid)

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
            unit = "pieces (wrappers)" if str(o.get("piece_unit") or "reel") != "reel" else "pieces (coils, legacy)"
            body = ("Complete this order? Boxes leave Ready stock.\n"
                    f"Requires {int(o['required_pieces']):,} {unit} —"
                    " the last box may overshoot by its own piece count.")
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
            unit = "pcs" if str(done.get("piece_unit") or "reel") != "reel" else "coils"
            self.say(f"Order {done['order_no']} dispatched"
                     f" ({done.get('loaded', 0)} boxes, "
                     f"{int(done.get('loaded_pieces') or 0):,} {unit}).",
                     ok=True)
        else:
            self.say(f"Order {done['order_no']} dispatched ({done['loaded']} boxes).", ok=True)
        self.refresh(select_id=self.order_id)
        if self.on_stock_changed:
            self.on_stock_changed()
        # The whole order details print now: the slip was frozen in the same
        # transaction as the completion, so this preview is the saved record.
        try:
            from factory.ui.label_preview import DispatchSlipDialog
            DispatchSlipDialog(self, self.order_id,
                               slip=(done.get("slip") if isinstance(done, dict) else None)).exec()
        except Exception as e:
            self.say(f"Order dispatched, but the slip would not open: {self._err(e)}",
                     ok=False)

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

    def open_slip(self):
        """Reprint the saved dispatch slip for the selected order."""
        if not self._can_dispatch():
            return self.say("Dispatcher or admin only.", ok=False)
        if self.order_id is None:
            return self.say("Create/select an order first.", ok=False, kind="warning")
        try:
            from factory.ui.label_preview import DispatchSlipDialog
            DispatchSlipDialog(self, self.order_id).exec()
        except (ValueError, PermissionError) as e:
            return self.say(str(e), ok=False)
        except Exception as e:
            return self.say(f"Connection issue: {self._err(e)}", ok=False)
