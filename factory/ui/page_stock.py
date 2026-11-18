"""Ready Stock: packed boxes available for dispatch. No customer shown (decoupled).

Main view is one card per product (job) with its stock numbers. Clicking a
card opens that product's box table on its own page; Back returns to cards.
"""
from __future__ import annotations
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
                               QMessageBox, QAbstractItemView, QFrame, QGridLayout,
                               QScrollArea, QStackedWidget, QSizePolicy)
from factory import data_access as DA
from factory.db import bare_carton_no, job_label
from factory.hardware import mock as HW
from factory.ui.label_preview import CartonLabelDialog, PackingSlipDialog, VerifyDialog
from factory.ui.styles import style_button


def _show_backend_error(parent, exc) -> None:
    """Surface LAN/auth failures without crashing or misleading success."""
    try:
        msg, kind = DA.describe_error(exc)
    except Exception:
        msg, kind = (str(exc) or "Request failed."), "other"
    if kind == "auth":
        QMessageBox.warning(parent, "Session expired",
                            f"{msg}")
    elif kind == "connection":
        QMessageBox.warning(parent, "Connection issue", msg)
    elif kind == "remote":
        QMessageBox.warning(parent, "Not available", msg)
    else:
        QMessageBox.warning(parent, "Stock", msg)


class ProductCard(QFrame):
    """Clickable product card: job identity plus stock numbers."""

    clicked = Signal(int)

    def __init__(self, job_id: int, title: str, name: str, big: str, sub: str):
        super().__init__()
        self.job_id = job_id
        self.setProperty("card", True)
        self.setCursor(Qt.PointingHandCursor)
        self.setMinimumHeight(130)
        self.setMinimumWidth(220)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        v = QVBoxLayout(self)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(4)
        t = QLabel(title)
        t.setProperty("field", "caption")
        t.setWordWrap(True)
        v.addWidget(t)
        n = QLabel(name)
        n.setWordWrap(True)
        v.addWidget(n)
        # Boxes are the headline number the floor packs and dispatches by;
        # weight + coils + pieces read as the detail line below.
        big = QLabel(big)
        big.setProperty("stat", "value")
        v.addWidget(big)
        s = QLabel(sub)
        s.setProperty("hint", True)
        s.setWordWrap(True)
        v.addWidget(s)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.job_id)
        super().mousePressEvent(event)


class StockPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        ht = QLabel("Ready stock")
        ht.setProperty("page", "title")
        lay.addWidget(ht)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search box / job…  (Enter)")
        self.search.setMinimumHeight(40)
        self.search.returnPressed.connect(self.refresh)
        bar.addWidget(self.search, 1)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setMinimumHeight(40)
        style_button(self.btn_refresh, "secondary")
        bar.addWidget(self.btn_refresh)
        self.btn_reprint = QPushButton("Reprint label")
        self.btn_reprint.setMinimumHeight(40)
        style_button(self.btn_reprint, "secondary")
        bar.addWidget(self.btn_reprint)
        self.btn_slip = QPushButton("Packing slip")
        self.btn_slip.setMinimumHeight(40)
        style_button(self.btn_slip, "secondary")
        bar.addWidget(self.btn_slip)
        lay.addLayout(bar)
        scan = QHBoxLayout()
        scan.setSpacing(8)
        scan_cap = QLabel("Scan QR to verify")
        scan_cap.setProperty("field", "caption")
        scan.addWidget(scan_cap)
        self.verify_scan = QLineEdit()
        self.verify_scan.setPlaceholderText("Click here, scan box… (Enter)")
        self.verify_scan.setMinimumHeight(40)
        self.verify_scan.setProperty("scan", True)
        self.verify_scan.returnPressed.connect(self.verify)
        scan.addWidget(self.verify_scan, 1)
        lay.addLayout(scan)
        self.count = QLabel("")
        self.count.setProperty("count", True)
        self.count.setWordWrap(True)
        lay.addWidget(self.count)

        # Page 0: product cards. Page 1: one product's boxes.
        self.stack = QStackedWidget()
        cards_page = QWidget()
        cards_lay = QVBoxLayout(cards_page)
        cards_lay.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.grid_host = QWidget()
        self.grid = QGridLayout(self.grid_host)
        self.grid.setSpacing(10)
        self.grid.setContentsMargins(0, 4, 0, 4)
        self.grid.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self.grid_host)
        cards_lay.addWidget(self.scroll, 1)
        self.stack.addWidget(cards_page)

        detail_page = QWidget()
        detail_lay = QVBoxLayout(detail_page)
        detail_lay.setContentsMargins(0, 0, 0, 0)
        detail_lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(8)
        self.btn_back = QPushButton("← Back to products")
        self.btn_back.setMinimumHeight(40)
        style_button(self.btn_back, "secondary")
        head.addWidget(self.btn_back)
        self.detail_title = QLabel("")
        self.detail_title.setProperty("field", "caption")
        head.addWidget(self.detail_title, 1)
        detail_lay.addLayout(head)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["Box No", "Job", "Bundle", "Reels", "Rolls", "Wt Kg", "Packed"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        detail_lay.addWidget(self.table, 1)
        self.stack.addWidget(detail_page)
        lay.addWidget(self.stack, 1)

        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_reprint.clicked.connect(self.reprint)
        self.btn_slip.clicked.connect(self.slip)
        self.btn_back.clicked.connect(self.show_cards)
        self._rows: list[dict] = []
        self._view_rows: list[dict] = []
        self._job_id: int | None = None
        self._offline = False
        self.refresh()
        if DA.is_lan():
            # Remote dispatcher auto-refresh without restarting; errors
            # surface clearly with no local fallback.
            self._timer = QTimer(self)
            self._timer.timeout.connect(self.refresh)
            self._timer.start(15000)

    def _set_offline(self, offline: bool, msg: str = ""):
        self._offline = offline
        try:
            self.btn_reprint.setEnabled(not offline)
            self.btn_slip.setEnabled(not offline)
        except Exception:
            pass
        if offline and msg:
            self.count.setText(f"Connection issue — {msg}")
            self.count.setProperty("empty", True)
            try:
                s = self.count.style()
                s.unpolish(self.count)
                s.polish(self.count)
            except Exception:
                pass

    def refresh(self):
        q = (self.search.text() or "").strip().upper()
        try:
            rows = DA.ready_stock()
        except Exception as e:
            self._set_offline(True, DA.describe_error(e)[0])
            return
        self._set_offline(False)
        if q:
            rows = [r for r in rows if q in r["carton_no"].upper()
                    or q == bare_carton_no(r["carton_no"])
                    or q in str(r["job_no"]) or q in r["job_name"].upper()
                    or q in str(r.get("job_code") or "").upper()]
        self._rows = rows
        if self._job_id is not None:
            self._render_table(self._job_id)
        else:
            self._render_cards()
        self._update_count()

    def _jobs(self) -> dict[int, list[dict]]:
        jobs: dict[int, list[dict]] = {}
        for r in self._rows:
            jobs.setdefault(int(r["job_id"]), []).append(r)
        return jobs

    def _render_cards(self):
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()
        jobs = self._jobs()
        ordered = sorted(jobs.items(), key=lambda kv: str(kv[1][0]["job_no"]))
        for i, (jid, rows) in enumerate(ordered):
            first = rows[0]
            boxes = len(rows)
            coils = sum(int(r.get("num_reels") or 0) for r in rows)
            pieces = sum(int(r.get("pieces") or 0) for r in rows)
            kg = sum(float(r.get("total_net") or 0) for r in rows)
            big = f"{boxes} box{'es' if boxes != 1 else ''}"
            sub = f"{kg:.3f} kg · {coils} coil{'s' if coils != 1 else ''}"
            if pieces:
                sub += f" · {pieces} piece{'s' if pieces != 1 else ''}"
            card = ProductCard(jid, job_label(first), str(first["job_name"]),
                               big, sub)
            card.clicked.connect(self.show_product)
            self.grid.addWidget(card, i // 3, i % 3)
        for c in range(3):
            self.grid.setColumnStretch(c, 1)

    def _render_table(self, job_id: int):
        rows = self._jobs().get(job_id, [])
        self._view_rows = rows
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(bare_carton_no(r["carton_no"])))
            self.table.setItem(i, 1, QTableWidgetItem(job_label(r)))
            self.table.setItem(i, 2, QTableWidgetItem(str(r["bundle_no"])))
            self.table.setItem(i, 3, QTableWidgetItem(
                str(r["num_reels"]) if r["num_reels"] else "—"))
            self.table.setItem(i, 4, QTableWidgetItem(
                ",".join(map(str, r["rolls"])) if r["rolls"] else "—"))
            self.table.setItem(i, 5, QTableWidgetItem(f"{r['total_net']:.3f}"))
            self.table.setItem(i, 6, QTableWidgetItem(r["packed_at"][:16]))
        if rows:
            self.detail_title.setText(job_label(rows[0]))
        else:
            self.detail_title.setText("")
            self.show_cards()

    def show_product(self, job_id: int):
        self._job_id = int(job_id)
        self._render_table(self._job_id)
        if self._job_id is not None and self._view_rows:
            self.stack.setCurrentIndex(1)
        self._update_count()

    def show_cards(self):
        self._job_id = None
        self.stack.setCurrentIndex(0)
        self._update_count()

    def _update_count(self):
        if self._job_id is not None and self._view_rows:
            rows = self._view_rows
            tot = sum(r["total_net"] for r in rows)
            text = f"{len(rows)} boxes  |  {tot:.3f} Kg"
            empty = False
        elif self._rows:
            tot = sum(r["total_net"] for r in self._rows)
            text = (f"In stock: {len(self._rows)} boxes across "
                    f"{len(self._jobs())} products  |  {tot:.3f} Kg")
            empty = False
        else:
            text = "No boxes in stock — pack boxes in Packaging."
            empty = True
        self.count.setText(text)
        self.count.setProperty("empty", empty)
        try:
            s = self.count.style()
            s.unpolish(self.count)
            s.polish(self.count)
        except Exception:
            pass

    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._view_rows[i] if 0 <= i < len(self._view_rows) else None

    def reprint(self):
        r = self.selected()
        if not r:
            return QMessageBox.warning(self, "Stock", "Select a box row first.")
        if self._offline:
            return QMessageBox.warning(
                self, "Connection issue",
                "Not connected to the packager. Reconnect, then refresh before retrying.")
        try:
            detail = DA.carton_detail(r["id"])
        except Exception as e:
            return _show_backend_error(self, e)
        CartonLabelDialog(self, detail, HW.printer).exec()

    def slip(self):
        r = self.selected()
        if not r:
            return QMessageBox.warning(
                self, "Slip",
                "Open a product, select any of its boxes, then print its packing slip.\n"
                "Reel-only jobs have no boxes — open their packing slip "
                "from the Jobs page.")
        if self._offline:
            return QMessageBox.warning(
                self, "Connection issue",
                "Not connected to the packager. Reconnect, then refresh before retrying.")
        PackingSlipDialog(self, r["job_id"]).exec()

    def verify(self):
        """Scanner re-verification: scan any box QR (Ready or dispatched),
        see its batch + box details and dispatch status."""
        code = self.verify_scan.text()
        self.verify_scan.clear()
        if not code.strip():
            return
        try:
            found = DA.carton_verify(code)
        except Exception as e:
            self.verify_scan.setFocus()
            return _show_backend_error(self, e)
        if not found:
            self.verify_scan.setFocus()
            return QMessageBox.warning(
                self, "Verify", f"Unknown box QR: {code.strip().upper()}")
        d = found["detail"]
        for r in self._rows:
            if r["carton_no"] == d["carton_no"]:
                self.show_product(int(r["job_id"]))
                for i, vr in enumerate(self._view_rows):
                    if vr["carton_no"] == d["carton_no"]:
                        self.table.selectRow(i)
                        break
                break
        self.verify_scan.setFocus()
        VerifyDialog(self, found).exec()
