"""OS print-queue viewer (CUPS): pending jobs + printer state + cancel.

Operators could spool a sticker, get a success toast, and see nothing come
out — with the job stuck in CUPS ("Waiting for printer to become
available", "Filter failed", stopped queue) and no surface in the app
showing it. This module is that surface: a read-only view of the queue
for one printer plus cancel, built on `lpstat`/`cancel` so it needs no
new dependencies.

Offline-safe: when CUPS is absent (frozen Windows floor builds, demo
mode with the mock file-log printer) every helper degrades to
"unavailable" and the dialog says so instead of failing.
"""
from __future__ import annotations

import shutil
import subprocess

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QTableWidget, QTableWidgetItem,
                               QHeaderView, QAbstractItemView)


def cups_available() -> bool:
    """True when `lpstat` exists (CUPS/Unix only)."""
    try:
        return shutil.which("lpstat") is not None
    except Exception:
        return False


def _run(args: list[str], timeout: int = 8) -> tuple[bool, str]:
    """Run a CUPS CLI. ok=False on missing binary, timeout, or nonzero exit
    (e.g. `lpstat -p` for an unknown queue) — callers treat that as
    'queue unknown', never as a printer problem."""
    try:
        p = subprocess.run(args, capture_output=True, text=True,
                           timeout=timeout)
        if p.returncode != 0:
            return False, ((p.stdout or "") + (p.stderr or "")).strip()
        return True, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return False, ""
    except Exception as e:
        return False, str(e)


def queue_state(printer_name: str) -> dict:
    """Printer state for one queue: enabled/disabled, idle/printing, reason.

    Returns {'available', 'enabled', 'state', 'reason', 'raw'}. `reason`
    is the indented CUPS status line (e.g. 'Filter failed',
    'Waiting for printer to become available.') or '' when healthy.
    Never raises — unknown output degrades to available/unknown.
    """
    out: dict = {"available": False, "enabled": True, "state": "unknown",
                 "reason": "", "raw": ""}
    name = (printer_name or "").strip()
    if not name or not cups_available():
        return out
    ok, text = _run(["lpstat", "-p", name, "-l"])
    if not ok:
        return out
    out["available"] = True
    out["raw"] = text.strip()
    lines = [ln for ln in text.splitlines()]
    if not lines:
        return out
    first = lines[0].strip()
    low = first.casefold()
    if "disabled" in low or "not accepting" in low or "stopped" in low:
        out["enabled"] = False
    if "now printing" in low or "printing" in low:
        out["state"] = "printing"
    elif "idle" in low:
        out["state"] = "idle"
    elif "disabled" in low:
        out["state"] = "disabled"
    # Any further non-empty lines are status detail (reason). Skip the
    # boilerplate capability lines; keep real reasons.
    skip = ("form mounted", "content types", "printer types", "description:",
            "alerts: none", "location:", "connection:", "interface:",
            "on fault", "after fault", "users allowed", "forms allowed",
            "banner", "charset", "default pitch", "default page",
            "default port", "status:")
    reasons = []
    for ln in lines[1:]:
        s = ln.strip()
        if not s:
            continue
        if s.casefold().startswith(skip):
            continue
        reasons.append(s)
    out["reason"] = " ".join(reasons).strip()
    return out


def queue_jobs(printer_name: str) -> list[dict]:
    """Pending (not-completed) jobs for one queue, oldest first.

    Each item: {'id', 'user', 'size', 'submitted'}. [] when the queue is
    empty, unknown, or CUPS is absent. Never raises.
    """
    name = (printer_name or "").strip()
    if not name or not cups_available():
        return []
    ok, text = _run(["lpstat", "-o", name])
    if not ok:
        return []
    jobs: list[dict] = []
    for ln in text.splitlines():
        parts = ln.split()
        # `lpstat -o` rows: job-id user size submitted-date...
        # e.g. `Citizen-CLS621-7  onlybond  1024  Wed 21 Oct ...`
        if len(parts) < 4 or "-" not in parts[0]:
            continue
        jobs.append({"id": parts[0].strip(),
                     "user": parts[1].strip(),
                     "size": parts[2].strip(),
                     "submitted": " ".join(parts[3:]).strip()})
    return jobs


def cancel_job(job_id: str) -> tuple[bool, str]:
    """Cancel one job by id (`Citizen-CLS621-7` or bare `7`)."""
    jid = (job_id or "").strip()
    if not jid:
        return False, "No job selected."
    if not cups_available():
        return False, "No print system found on this PC."
    ok, text = _run(["cancel", jid])
    if not ok:
        return False, text.strip() or "Could not cancel the job."
    return True, ""


def describe_problem(printer_name: str) -> str | None:
    """Actionable problem line for the print pre-flight, or None when clear.

    Returns a short operator-facing message when the queue is disabled or
    carries a CUPS reason (stuck jobs, filter failure, printer offline).
    None means printing may proceed. Never raises.
    """
    name = (printer_name or "").strip()
    if not name or not cups_available():
        return None
    st = queue_state(name)
    if not st.get("available"):
        return None
    if not st.get("enabled") or st.get("state") == "disabled":
        reason = st.get("reason") or "the queue is stopped"
        return (f"Printer {name} is stopped ({reason}).\n"
                "Open Queue to inspect, then print again.")
    if st.get("reason"):
        return (f"Printer {name} reports: {st['reason']}\n"
                "Jobs are not flowing — open Queue to inspect or cancel "
                "stuck jobs, then print again.")
    return None


class PrinterQueueDialog(QDialog):
    """Pending-jobs view for one OS print queue, with cancel + refresh."""

    def __init__(self, parent, printer_name: str):
        super().__init__(parent)
        self._queue = (printer_name or "").strip()
        self.setWindowTitle(f"Print queue — {self._queue or 'no printer'}")
        self.setMinimumSize(560, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)

        ht = QLabel(f"Queue: {self._queue or '—'}")
        ht.setProperty("dialog", "title")
        ht.setWordWrap(True)
        lay.addWidget(ht)
        self._state_line = QLabel("")
        self._state_line.setProperty("field", "caption")
        self._state_line.setWordWrap(True)
        lay.addWidget(self._state_line)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Job", "User", "Size", "Submitted"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(32)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        lay.addWidget(self.table, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_refresh = QPushButton("Refresh")
        self.btn_cancel = QPushButton("Cancel job")
        self.btn_close = QPushButton("Close")
        for b in (self.btn_refresh, self.btn_cancel, self.btn_close):
            b.setMinimumHeight(44)
            row.addWidget(b)
        try:
            from factory.ui.styles import style_button
            style_button(self.btn_refresh, "secondary")
            style_button(self.btn_cancel, "secondary")
            style_button(self.btn_close, "primary")
        except Exception:
            pass
        lay.addLayout(row)
        self.btn_refresh.clicked.connect(self.refresh_queue)
        self.btn_cancel.clicked.connect(self.cancel_selected)
        self.btn_close.clicked.connect(self.accept)

        self._timer = QTimer(self)
        self._timer.setInterval(3000)
        self._timer.timeout.connect(self.refresh_queue)
        self._timer.start()
        self.refresh_queue()

    def closeEvent(self, ev):
        try:
            self._timer.stop()
        except Exception:
            pass
        super().closeEvent(ev)

    def refresh_queue(self) -> None:
        """Re-read printer state + jobs. Never raises; shows reasons."""
        if not self._queue:
            self._state_line.setText(
                "No OS print queue here — demo/mock mode logs jobs to "
                "print_log.txt instead of paper.")
            self.table.setRowCount(0)
            self.btn_cancel.setEnabled(False)
            return
        if not cups_available():
            self._state_line.setText(
                "No print system found on this PC — install the printer "
                "driver first.")
            self.table.setRowCount(0)
            self.btn_cancel.setEnabled(False)
            return
        try:
            st = queue_state(self._queue)
        except Exception:
            st = {}
        state = str((st or {}).get("state") or "unknown")
        reason = str((st or {}).get("reason") or "")
        enabled = bool((st or {}).get("enabled", True))
        head = f"Printer {self._queue}: {state}" + ("" if enabled else " (stopped)")
        self._state_line.setText(head + (f" — {reason}" if reason else ""))
        try:
            jobs = queue_jobs(self._queue)
        except Exception:
            jobs = []
        self.table.setRowCount(0)
        for j in jobs:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(str(j.get("id", ""))))
            self.table.setItem(i, 1, QTableWidgetItem(str(j.get("user", ""))))
            self.table.setItem(i, 2, QTableWidgetItem(str(j.get("size", ""))))
            self.table.setItem(i, 3, QTableWidgetItem(str(j.get("submitted", ""))))
        self.btn_cancel.setEnabled(bool(jobs))

    def cancel_selected(self) -> None:
        from PySide6.QtWidgets import QMessageBox
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        jid = item.text().strip() if item is not None else ""
        if not jid:
            return
        ok, msg = cancel_job(jid)
        if not ok:
            QMessageBox.warning(self, "Cancel job",
                                msg or "Could not cancel the job.")
        self.refresh_queue()
