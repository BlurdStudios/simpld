"""Jobs + Dispatch history + Daily summary pages."""
from __future__ import annotations
import csv
import os
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QTableWidget, QTableWidgetItem, QHeaderView, QMessageBox,
                               QFrame, QGridLayout, QAbstractItemView, QFileDialog, QLineEdit, QTabBar)
from PySide6.QtCore import Qt
from factory import data_access as DA
from factory.ui.styles import style_button, status_color
from factory.ui.label_preview import PackingSlipDialog


def _err_text(e) -> str:
    try:
        msg, _kind = DA.describe_error(e)
    except Exception:
        msg = str(e) or "Request failed."
    return msg


def _role() -> str:
    try:
        me = DA.current_user()
    except Exception:
        return ""
    return str((me or {}).get("role") or "") if me else ""


class JobsPage(QWidget):
    def __init__(self, on_changed=None, actor: dict | None = None):
        super().__init__()
        self.on_changed = on_changed
        # Caller-supplied actor dicts are NEVER trusted; auth resolves live.
        self._ignored_actor = actor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        ht = QLabel("Jobs")
        ht.setProperty("page", "title")
        lay.addWidget(ht)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_close = QPushButton("Close / reopen")
        self.btn_slip = QPushButton("Packing slip")
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.setToolTip("Admin only; empty jobs only — otherwise close/archive.")
        for b in (self.btn_refresh, self.btn_close, self.btn_slip, self.btn_delete):
            b.setMinimumHeight(40)
            style_button(b, "secondary")
            bar.addWidget(b)
        bar.addStretch(1)
        lay.addLayout(bar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ["Job No", "Job Name", "Wrappers/reel", "Status", "Reels", "Ready"])
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
        self.empty = QLabel("No jobs yet.")
        self.empty.setProperty("empty", True)
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        lay.addWidget(self.empty)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_close.clicked.connect(self.toggle)
        self.btn_slip.clicked.connect(self.slip)
        self.btn_delete.clicked.connect(self.delete)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self._rows: list[dict] = []
        self.refresh()

    def _is_admin(self) -> bool:
        return _role() == "admin"

    def _can_manage_jobs(self) -> bool:
        return _role() in ("packager", "admin")

    def _update_buttons(self):
        has = self.selected() is not None
        is_admin = self._is_admin()
        can_manage = self._can_manage_jobs()
        self.btn_close.setEnabled(bool(has and can_manage))
        self.btn_slip.setEnabled(has)
        self.btn_delete.setEnabled(bool(has and is_admin))
        if has and not is_admin:
            self.btn_delete.setToolTip("Admin only.")

    def refresh(self):
        try:
            rows = DA.list_jobs()
        except Exception as e:
            return QMessageBox.warning(self, "Jobs", f"Connection issue: {_err_text(e)}")
        self._rows = rows
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        self.table.setRowCount(0)
        for j in rows:
            try:
                st = DA.job_stats(j["id"])
            except Exception:
                st = {"reels": "?", "ready_cartons": "?"}
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(str(j["job_no"])))
            self.table.setItem(i, 1, QTableWidgetItem(j["job_name"]))
            w = j.get("wrappers_per_reel")
            self.table.setItem(i, 2, QTableWidgetItem(str(w) if w else "unknown"))
            from PySide6.QtGui import QColor as _QColor
            _st = QTableWidgetItem(j["status"])
            try:
                _st.setForeground(_QColor(status_color(str(j.get("status") or ""))))
            except Exception:
                pass
            self.table.setItem(i, 3, _st)
            self.table.setItem(i, 4, QTableWidgetItem(str(st["reels"])))
            self.table.setItem(i, 5, QTableWidgetItem(str(st["ready_cartons"])))
        self.empty.setVisible(len(rows) == 0)
        self._update_buttons()

    def selected(self):
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        i = rows[0].row()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def toggle(self):
        j = self.selected()
        if not j:
            return QMessageBox.warning(self, "Jobs", "Select a job row first.")
        try:
            DA.set_job_status(j["id"], "closed" if j["status"] == "open" else "open")
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Jobs", str(e))
        except Exception as e:
            return QMessageBox.warning(
                self, "Jobs",
                f"Connection issue: {_err_text(e)} Refresh before retrying.")
        self.refresh()
        if self.on_changed:
            self.on_changed()

    def slip(self):
        j = self.selected()
        if not j:
            return QMessageBox.warning(self, "Slip", "Select a job first.")
        PackingSlipDialog(self, j["id"]).exec()

    def delete(self):
        j = self.selected()
        if not j:
            return QMessageBox.warning(self, "Jobs", "Select a job row first.")
        if not self._is_admin():
            return QMessageBox.warning(self, "Jobs", "Only admin may delete jobs.")
        ans = QMessageBox.question(
            self, "Delete job",
            f"Delete J:{j['job_no']} {j['job_name']}?\n"
            "Allowed only with zero reels, cartons and orders — otherwise"
            " close/archive it. Production is never cascade-deleted.")
        if ans != QMessageBox.StandardButton.Yes:
            return
        try:
            DA.delete_job(j["id"])
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Cannot delete job", str(e))
        except Exception as e:
            return QMessageBox.warning(
                self, "Cannot delete job",
                f"Connection issue: {_err_text(e)} Refresh before retrying.")
        self.refresh()
        if self.on_changed:
            self.on_changed()


class HistoryPage(QWidget):
    """Read-only reel, box and dispatch records."""

    def __init__(self, actor: dict | None = None):
        super().__init__()
        # Read-only page: no actor needed; session gating happens at nav.
        self._ignored_actor = actor
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        ht = QLabel("History")
        ht.setProperty("page", "title")
        lay.addWidget(ht)
        bar = QHBoxLayout()
        self.tabs = QTabBar()
        for title in ("All production", "Reels", "Boxes", "Dispatch"):
            self.tabs.addTab(title)
        self.tabs.setExpanding(False)
        bar.addWidget(self.tabs)
        bar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search job, roll or box…")
        self.search.setClearButtonEnabled(True)
        bar.addWidget(self.search)
        self.btn = QPushButton("Refresh")
        style_button(self.btn, "secondary")
        bar.addWidget(self.btn)
        lay.addLayout(bar)
        self.table = QTableWidget()
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.table, 1)
        self.count = QLabel()
        self.count.setProperty("hint", True)
        lay.addWidget(self.count)
        self.btn.clicked.connect(self.refresh)
        self.tabs.currentChanged.connect(self.refresh)
        self.search.textChanged.connect(self.refresh)
        self.refresh()

    def refresh(self):
        tab = self.tabs.currentIndex()
        query = self.search.text().strip().casefold()
        try:
            if tab == 3:
                headers = ["Order", "Customer", "Job", "Required", "Loaded", "Status", "Date"]
                rows = [[o["order_no"], o["customer"], f"J:{o['job_no']} {o['job_name']}",
                         o["required_bundles"], o["loaded"], o["status"], o["order_date"]]
                        for o in DA.list_orders()]
            else:
                headers = ["Type", "Reference", "Full ID", "Job", "Wt · kg", "Shift",
                           "Status", "Label", "Box", "Recorded"]
                records = DA.production_history()
                if tab in (1, 2):
                    records = [r for r in records if r["kind"] == ("Reel" if tab == 1 else "Box")]
                rows = []
                for r in records:
                    ref = f"Roll {r['reference']}" if r["kind"] == "Reel" else r["reference"]
                    full = str(r.get("full_ref") or "")
                    n = int(r.get("print_count") or 0)
                    pst = str(r.get("print_status") or "pending")
                    label = f"Printed ×{n}" if pst == "printed" and n > 0 else "Pending"
                    rows.append([r["kind"], ref, full,
                                 f"J:{r['job_no']} {r['job_name']}", f"{r['net_weight']:.3f}", r["shift"],
                                 r["status"], label, r["box"] or "—", r["recorded_at"][:16]])
        except Exception as e:
            self.count.setText(f"Connection issue — {_err_text(e)}")
            return
        if query:
            rows = [row for row in rows if query in " ".join(map(str, row)).casefold()]
        self.table.setRowCount(0)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        from PySide6.QtGui import QColor as _QColor
        _status_col = 5 if tab == 3 else 6
        _label_col = 7 if tab != 3 else -1
        for row in rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            for col, value in enumerate(row):
                _it = QTableWidgetItem(str(value))
                try:
                    if col == _status_col:
                        _it.setForeground(_QColor(status_color(str(value))))
                    elif col == _label_col:
                        _is_printed = str(value).startswith("Printed")
                        _it.setForeground(_QColor(
                            status_color("printed" if _is_printed else "pending")))
                except Exception:
                    pass
                self.table.setItem(i, col, _it)
        if rows:
            self.count.setText(f"{len(rows)} records")
            self.count.setProperty("empty", False)
        else:
            self.count.setText("No records.")
            self.count.setProperty("empty", True)
        try:
            s = self.count.style()
            s.unpolish(self.count)
            s.polish(self.count)
        except Exception:
            pass


class SummaryPage(QWidget):
    def __init__(self):
        super().__init__()
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 12, 0, 12)
        lay.setSpacing(8)
        ht = QLabel("Daily summary")
        ht.setProperty("page", "title")
        lay.addWidget(ht)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.btn_refresh = QPushButton("Refresh today")
        self.btn_refresh.setMinimumHeight(40)
        style_button(self.btn_refresh, "secondary")
        self.btn_export = QPushButton("Export CSV")
        self.btn_export.setMinimumHeight(40)
        style_button(self.btn_export, "secondary")
        self.btn_backup = QPushButton("Evening backup")
        self.btn_backup.setMinimumHeight(40)
        style_button(self.btn_backup, "secondary")
        self.btn_backup.setToolTip("Copy today's database to a timestamped file — "
                                   "do this before closing the computer.")
        bar.addWidget(self.btn_refresh)
        bar.addWidget(self.btn_export)
        bar.addWidget(self.btn_backup)
        bar.addStretch(1)
        lay.addLayout(bar)
        grid = QGridLayout()
        grid.setSpacing(10)
        grid.setContentsMargins(0, 4, 0, 4)
        self.cards: dict[str, QLabel] = {}
        for i, key in enumerate(["reels_packed", "cartons_packed", "in_stock",
                                 "dispatched", "packed_wt", "dispatched_wt"]):
            f = QFrame()
            f.setProperty("card", True)
            f.setMinimumHeight(110)
            f.setMinimumWidth(200)
            v = QVBoxLayout(f)
            v.setContentsMargins(12, 12, 12, 12)
            v.setSpacing(6)
            title = {"reels_packed": "Reels packed today", "cartons_packed": "Cartons packed today",
                     "in_stock": "Cartons in stock", "dispatched": "Cartons dispatched today",
                     "packed_wt": "Packed weight (Kg)", "dispatched_wt": "Dispatched weight (Kg)"}[key]
            t = QLabel(title)
            t.setProperty("field", "caption")
            t.setWordWrap(True)
            v.addWidget(t)
            big = QLabel("–")
            big.setProperty("stat", "value")
            big.setAlignment(Qt.AlignCenter)
            v.addWidget(big, 1)
            self.cards[key] = big
            grid.addWidget(f, i // 3, i % 3)
            grid.setColumnStretch(i % 3, 1)
        lay.addLayout(grid)
        self.detail = QLabel("")
        self.detail.setProperty("hint", True)
        self.detail.setWordWrap(True)
        lay.addWidget(self.detail)
        lay.addStretch(1)
        self.btn_refresh.clicked.connect(self.refresh)
        self.btn_export.clicked.connect(self.export)
        self.btn_backup.clicked.connect(self.backup)
        self.refresh()

    def refresh(self):
        try:
            s = DA.daily_summary()
        except Exception as e:
            self.detail.setText(f"Connection issue — {_err_text(e)}")
            return
        self.cards["reels_packed"].setText(str(s["reels_packed"]))
        self.cards["cartons_packed"].setText(str(s["cartons_packed"]))
        self.cards["in_stock"].setText(str(s["in_stock"]))
        self.cards["dispatched"].setText(str(s["dispatched"]))
        self.cards["packed_wt"].setText(f"{s['packed_wt']:.3f}")
        self.cards["dispatched_wt"].setText(f"{s['dispatched_wt']:.3f}")
        self.detail.setText(
            f"Day: {s['day']}  |  Reel net today: {s['reel_net']:.3f} Kg")

    def export(self):
        try:
            s = DA.daily_summary()
        except Exception as e:
            return QMessageBox.warning(self, "Export", f"Connection issue: {_err_text(e)}")
        try:
            from factory.db import app_data_dir
            default = os.path.join(app_data_dir(), f"summary_{s['day']}.csv")
        except Exception:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            default = os.path.join(base, "data", f"summary_{s['day']}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "Export summary CSV", default, "CSV (*.csv)")
        if not path:
            return
        with open(path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["metric", "value"])
            for k, v in s.items():
                w.writerow([k, v])
        from factory.ui.toast import toast
        toast(self, f"Summary saved ({os.path.basename(path)}).")

    def backup(self):
        """Evening backup: timestamped copy of the database before closing."""
        from factory import backup as B
        try:
            default_dir = B._default_dest_dir()
        except Exception:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            default_dir = os.path.join(base, "data", "backups")
        dest = QFileDialog.getExistingDirectory(
            self, "Evening backup — pick a folder (USB stick works too)", default_dir)
        if not dest:
            return
        try:
            out = B.backup_db(dest)
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Backup", str(e))
        except OSError as e:
            return QMessageBox.warning(self, "Backup", f"Could not write backup:\n{e}")
        from factory.ui.toast import toast
        toast(self, "Backup done — safe to close the computer.")
