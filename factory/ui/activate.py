"""Activation gate: no valid licence -> activate before anything else.

Locked states (none/invalid/blocked) open ActivateDialog instead of the
login window: Machine ID + Paste key / Import file, offline verification.
Grace/expiring/demo proceed with a banner only. LAN (dispatcher) mode never
gates locally — the packager server enforces per-login.
"""
from __future__ import annotations

import json
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QFrame, QFileDialog,
                               QMessageBox, QTabWidget, QWidget)


class ActivateDialog(QDialog):
    """Machine ID + key paste / file import. Accept = licence stored + valid."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Activate Simpl'd")
        self.setMinimumWidth(520)
        try:
            from factory import license as _LIC
        except Exception:
            _LIC = None
        self._LIC = _LIC
        try:
            mid = _LIC.machine_fingerprint() if _LIC else "?"
        except Exception:
            mid = "?"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 20)
        outer.setSpacing(12)

        title = QLabel("Software locked — activation required")
        title.setProperty("page", "title")
        outer.addWidget(title)
        hint = QLabel(
            "Paste the licence key or import the licence file for this "
            "computer. No internet needed — the key is verified offline.\n"
            "Send the Machine ID below to your vendor to get a key.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        outer.addWidget(hint)

        card = QFrame()
        card.setProperty("card", True)
        cl = QVBoxLayout(card)
        cl.setContentsMargins(16, 14, 16, 14)
        cl.setSpacing(8)
        ml = QLabel("MACHINE ID (this computer)")
        ml.setProperty("field", "caption")
        cl.addWidget(ml)
        row = QHBoxLayout()
        self.mid_edit = QLineEdit(mid)
        self.mid_edit.setReadOnly(True)
        row.addWidget(self.mid_edit, 1)
        copy_btn = QPushButton("Copy")
        copy_btn.setMinimumHeight(36)
        from factory.ui.styles import style_button
        style_button(copy_btn, "outline")
        copy_btn.clicked.connect(self._copy_mid)
        row.addWidget(copy_btn)
        cl.addLayout(row)
        outer.addWidget(card)

        self.tabs = QTabWidget()
        kp = QWidget()
        kl = QVBoxLayout(kp)
        kl.setSpacing(8)
        self.key_edit = QLineEdit()
        self.key_edit.setPlaceholderText("Paste SIMPLD1.… key here")
        self.key_edit.setMinimumHeight(40)
        kl.addWidget(self.key_edit)
        self.tabs.addTab(kp, "Paste key")
        fp = QWidget()
        fl = QVBoxLayout(fp)
        fl.setSpacing(8)
        frow = QHBoxLayout()
        self.file_edit = QLineEdit()
        self.file_edit.setPlaceholderText("No file chosen")
        self.file_edit.setReadOnly(True)
        frow.addWidget(self.file_edit, 1)
        browse = QPushButton("Browse…")
        browse.setMinimumHeight(36)
        style_button(browse, "outline")
        browse.clicked.connect(self._browse)
        frow.addWidget(browse)
        fl.addLayout(frow)
        self.tabs.addTab(fp, "Import file")
        outer.addWidget(self.tabs)

        brow = QHBoxLayout()
        brow.addStretch(1)
        quit_btn = QPushButton("Quit")
        quit_btn.setMinimumHeight(40)
        quit_btn.setMinimumWidth(110)
        style_button(quit_btn, "secondary")
        quit_btn.clicked.connect(self.reject)
        brow.addWidget(quit_btn)
        go = QPushButton("Activate")
        go.setMinimumHeight(40)
        go.setMinimumWidth(140)
        style_button(go, "accent")
        go.clicked.connect(self._activate)
        brow.addWidget(go)
        outer.addLayout(brow)

    def _copy_mid(self):
        try:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(self.mid_edit.text())
            from factory.ui.toast import toast
            toast(self, "Machine ID copied.")
        except Exception:
            pass

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose licence file", "",
            "Licence (*.lic);;All (*)")
        if path:
            self.file_edit.setText(path)

    def _candidate(self) -> dict:
        LIC = self._LIC
        if LIC is None:
            raise ValueError("Licensing module unavailable.")
        if self.tabs.currentIndex() == 1:
            p = self.file_edit.text().strip()
            if not p:
                raise ValueError("Choose the licence file first.")
            with open(p, "r", encoding="utf-8") as f:
                d = json.load(f)
            if not isinstance(d, dict):
                raise ValueError("That file is not a licence.")
            return d
        return LIC.decode_key_string(self.key_edit.text())

    def _activate(self):
        LIC = self._LIC
        try:
            lic = self._candidate()
        except (ValueError, OSError) as e:
            return QMessageBox.warning(self, "Activate", str(e))
        ok, reason = LIC.verify_license(lic)
        if not ok:
            return QMessageBox.warning(
                self, "Activate",
                f"This key is not valid for this computer:\n{reason}\n\n"
                "Check the Machine ID matches the one sent to your vendor.")
        try:
            dest = LIC.license_path()
            os.makedirs(os.path.dirname(dest), exist_ok=True)
            with open(dest, "w", encoding="utf-8") as f:
                json.dump(lic, f, indent=2, sort_keys=True)
                f.write("\n")
        except OSError as e:
            return QMessageBox.warning(
                self, "Activate", f"Key is valid but could not be saved:\n{e}")
        try:
            LIC.record_station()
        except Exception:
            pass
        QMessageBox.information(
            self, "Activate", f"Activated.\n{LIC.license_display()}")
        self.accept()


def ensure_activated() -> bool:
    """Gate for local mode. True = proceed to login; False = quit.

    Demo bypass, LAN mode, and ok/expiring/grace states proceed without a
    dialog. Locked states (none/invalid/blocked) loop the Activate dialog
    until valid or the operator quits.

    Dev escape hatch: SIMPLD_LICENSE_DEV=1 skips the gate, but ONLY for
    source runs — frozen (.exe) builds ignore it, so shipped software is
    always gated.
    """
    if os.environ.get("SIMPLD_LICENSE_DEV") == "1":
        try:
            from factory.db import _is_frozen
            frozen = _is_frozen()
        except Exception:
            frozen = False
        if not frozen:
            print("[license] DEV bypass — source run only, never ships", flush=True)
            return True
    try:
        from factory import license as LIC
        from factory import data_access as DA
    except Exception:
        return True  # fail open only when licensing itself is broken
    try:
        if DA.is_lan() or LIC.is_demo_bypass():
            return True
        st = LIC.license_status()
        if st.get("state") not in ("none", "invalid", "blocked"):
            return True
        while True:
            dlg = ActivateDialog()
            if dlg.exec() == QDialog.DialogCode.Accepted:
                st2 = LIC.license_status()
                if st2.get("state") not in ("none", "invalid", "blocked"):
                    return True
            else:
                return False
    except Exception:
        return True
