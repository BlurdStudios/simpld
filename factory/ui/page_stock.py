"""Ready Stock: all packed boxes available for dispatch. No customer shown (decoupled)."""
from __future__ import annotations
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QLineEdit,
                               QMessageBox, QAbstractItemView)
from factory import data_access as DA
from factory.db import bare_carton_no
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
        lay.addWidget(self.table, 1)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_reprint.clicked.connect(self.reprint)
        self.btn_slip.clicked.connect(self.slip)
        self._rows: list[dict] = []
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
                    or q in str(r["job_no"]) or q in r["job_name"].upper()]
        self._rows = rows
        self.table.setRowCount(0)
        for r in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(bare_carton_no(r["carton_no"])))
            self.table.setItem(i, 1, QTableWidgetItem(f"J:{r['job_no']} {r['job_name']}"))
            self.table.setItem(i, 2, QTableWidgetItem(str(r["bundle_no"])))
            self.table.setItem(i, 3, QTableWidgetItem(
                str(r["num_reels"]) if r["num_reels"] else "—"))
            self.table.setItem(i, 4, QTableWidgetItem(
                ",".join(map(str, r["rolls"])) if r["rolls"] else "—"))
            self.table.setItem(i, 5, QTableWidgetItem(f"{r['total_net']:.3f}"))
            self.table.setItem(i, 6, QTableWidgetItem(r["packed_at"][:16]))
        tot = sum(r["total_net"] for r in rows)
        if rows:
            self.count.setText(f"In stock: {len(rows)} boxes  |  {tot:.3f} Kg")
            self.count.setProperty("empty", False)
        else:
            self.count.setText("No boxes in stock — pack boxes in Packaging.")
            self.count.setProperty("empty", True)
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
        return self._rows[i] if 0 <= i < len(self._rows) else None

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
                "Select any box of the job, then print its packing slip.\n"
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
        for i, r in enumerate(self._rows):
            if r["carton_no"] == d["carton_no"]:
                self.table.selectRow(i)
                break
        self.verify_scan.setFocus()
        VerifyDialog(self, found).exec()
