"""Settings: one dialog, a sidebar of areas, a Back button.

Areas: General, Printer, Sync, Licence, Updates, User, About. Each area is
its own page so a setting only lives in one place.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                               QListWidget, QListWidgetItem, QStackedWidget, QLineEdit,
                               QFormLayout, QSpinBox, QMessageBox, QWidget, QFrame)

from factory.ui.styles import style_button

AREAS = ("General", "Printer", "Sync", "Licence", "Updates", "User", "About")


def _page(title: str) -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(10)
    h = QLabel(title)
    h.setProperty("page", "title")
    lay.addWidget(h)
    return w, lay


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("hint", True)
    lbl.setWordWrap(True)
    return lbl


class SettingsDialog(QDialog):
    def __init__(self, parent=None, user: dict | None = None, role: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumSize(760, 480)
        self._user = user or {}
        self._role = role or ""
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(14)
        self.nav = QListWidget()
        self.nav.setFixedWidth(180)
        self.stack = QStackedWidget()
        builders = {
            "General": self._build_general,
            "Printer": self._build_printer,
            "Sync": self._build_sync,
            "Licence": self._build_licence,
            "Updates": self._build_updates,
            "User": self._build_user,
            "About": self._build_about,
        }
        for area in AREAS:
            self.nav.addItem(QListWidgetItem(area))
            self.stack.addWidget(builders[area]())
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.nav.setCurrentRow(0)
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        foot = QHBoxLayout()
        self.btn_back = QPushButton("← Back")
        self.btn_back.setMinimumHeight(40)
        style_button(self.btn_back, "secondary")
        self.btn_back.clicked.connect(self.accept)
        foot.addWidget(self.btn_back)
        foot.addStretch(1)
        root.addLayout(foot)

    # ----- General ------------------------------------------------------
    def _build_general(self) -> QWidget:
        w, lay = _page("General")
        try:
            from factory.hardware import devices as DEV
            mode = DEV.mode_text()
        except Exception:
            mode = "unknown"
        try:
            from factory import data_access as DA
            backend = "LAN dispatcher" if DA.is_lan() else "This PC (local database)"
        except Exception:
            backend = "unknown"
        form = QFormLayout()
        form.addRow("Data source", QLabel(backend))
        form.addRow("Devices", QLabel(mode))
        lay.addLayout(form)
        lay.addWidget(_hint("Devices are detected automatically from the station strips. "
                            "Printer port is set under Printer."))
        lay.addStretch(1)
        return w

    # ----- Printer ------------------------------------------------------
    def _build_printer(self) -> QWidget:
        from factory.hardware import config as HWC
        w, lay = _page("Printer")
        cfg = HWC.load()
        self.p_port = QLineEdit(str(cfg.get("printer_port") or ""))
        self.p_port.setPlaceholderText("Leave blank to find the Citizen by USB ID (e.g. COM5)")
        self.p_baud = QSpinBox()
        self.p_baud.setRange(1200, 115200)
        self.p_baud.setValue(int(cfg.get("printer_baud") or 9600))
        form = QFormLayout()
        form.addRow("Serial port (VCOM)", self.p_port)
        form.addRow("Baud rate", self.p_baud)
        lay.addLayout(form)
        lay.addWidget(_hint("Citizen CL-S621 over its VCOM serial port. Printing is "
                            "only enabled once the printer language (DPL/ZPL) is set."))
        btn = QPushButton("Save printer settings")
        btn.setMinimumHeight(40)
        style_button(btn, "primary")
        btn.clicked.connect(self._save_printer)
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return w

    def _save_printer(self):
        from factory.hardware import config as HWC
        from factory.hardware import devices as DEV
        cfg = HWC.load()
        cfg["printer_port"] = self.p_port.text().strip()
        cfg["printer_baud"] = int(self.p_baud.value())
        HWC.save(cfg)
        DEV.reload_hardware(cfg)
        QMessageBox.information(self, "Printer", "Printer settings saved.")

    # ----- Sync ---------------------------------------------------------
    def _build_sync(self) -> QWidget:
        from factory import services as SVC
        w, lay = _page("Sync (vendor dashboard)")
        self.s_url = QLineEdit(SVC.get_setting("sync.url", "") or "")
        self.s_url.setPlaceholderText("https://simpld-dashboard.workers.dev")
        self.s_site = QLineEdit(SVC.get_setting("sync.site_id", "") or "")
        self.s_site.setPlaceholderText("e.g. vasavi-f1")
        self.s_key = QLineEdit(SVC.get_setting("sync.key", "") or "")
        self.s_key.setEchoMode(QLineEdit.Password)
        form = QFormLayout()
        form.addRow("Dashboard address", self.s_url)
        form.addRow("Site ID", self.s_site)
        form.addRow("Site key", self.s_key)
        lay.addLayout(form)
        self.s_status = _hint("")
        lay.addWidget(self.s_status)
        row = QHBoxLayout()
        btn_save = QPushButton("Save sync settings")
        btn_save.setMinimumHeight(40)
        style_button(btn_save, "primary")
        btn_save.clicked.connect(self._save_sync)
        btn_test = QPushButton("Sync now")
        btn_test.setMinimumHeight(40)
        style_button(btn_test, "secondary")
        btn_test.clicked.connect(self._sync_now)
        row.addWidget(btn_save)
        row.addWidget(btn_test)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(_hint("The site key is stored on this PC only. The factory works "
                            "fully offline; sync is outbound only."))
        lay.addStretch(1)
        self._refresh_sync_status()
        return w

    def _refresh_sync_status(self):
        try:
            from factory import sync as SYNC
            last = SYNC.last_sync()
            if not SYNC.configured():
                self.s_status.setText("Not set up yet — enter the address, site ID and key, then save.")
            elif last.get("at"):
                ok = "ok" if last.get("ok") else "failed"
                self.s_status.setText(f"Last sync: {last['at']} ({ok})")
            else:
                self.s_status.setText("Set up. Not yet pushed.")
        except Exception as e:
            self.s_status.setText(f"Sync status unavailable: {e}")

    def _save_sync(self):
        from factory import services as SVC
        SVC.set_setting("sync.url", self.s_url.text().strip().rstrip("/"))
        SVC.set_setting("sync.site_id", self.s_site.text().strip())
        SVC.set_setting("sync.key", self.s_key.text().strip())
        self._refresh_sync_status()
        QMessageBox.information(self, "Sync", "Sync settings saved.")

    def _sync_now(self):
        from factory import sync as SYNC
        self._save_sync()
        if not SYNC.configured():
            return
        try:
            SYNC.push_now()
        except Exception as e:
            QMessageBox.warning(self, "Sync", f"Sync failed: {e}")
        self._refresh_sync_status()

    # ----- Licence ------------------------------------------------------
    def _build_licence(self) -> QWidget:
        w, lay = _page("Licence")
        self.l_text = _hint("")
        self.l_text.setProperty("hint", False)
        lay.addWidget(self.l_text)
        btn = QPushButton("Enter licence key")
        btn.setMinimumHeight(40)
        style_button(btn, "primary")
        btn.clicked.connect(self._enter_key)
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addWidget(_hint("Paste the key or import the licence file from the vendor. "
                            "A licence sent by the vendor is installed automatically."))
        lay.addSpacing(8)
        req = QHBoxLayout()
        self.t_days = QSpinBox()
        self.t_days.setRange(1, 90)
        self.t_days.setValue(14)
        self.t_days.setSuffix(" days")
        self.t_reason = QLineEdit()
        self.t_reason.setPlaceholderText("Reason (optional)")
        btn_req = QPushButton("Request trial extension")
        btn_req.setMinimumHeight(40)
        style_button(btn_req, "secondary")
        btn_req.clicked.connect(self._request_trial)
        req.addWidget(QLabel("Extra trial:"))
        req.addWidget(self.t_days)
        req.addWidget(self.t_reason, 1)
        req.addWidget(btn_req)
        lay.addLayout(req)
        self.t_status = _hint("")
        lay.addWidget(self.t_status)
        lay.addStretch(1)
        self._refresh_licence()
        return w

    def _refresh_licence(self):
        try:
            from factory import license as LIC
            st = LIC.license_status()
            self.l_text.setText(f"{LIC.license_display(st)}\n{st.get('reason') or ''}")
        except Exception as e:
            self.l_text.setText(f"Licence status unavailable: {e}")

    def _request_trial(self):
        from factory import sync as SYNC
        res = SYNC.request_trial(int(self.t_days.value()), self.t_reason.text().strip())
        if res.get("ok"):
            self.t_status.setText("Request sent to the vendor. It appears on your licence "
                                  "once approved — no key to copy.")
        else:
            self.t_status.setText(f"Request not sent: {res.get('detail')}")

    def _enter_key(self):
        from factory.ui.activate import ActivateDialog
        if ActivateDialog(self).exec():
            self._refresh_licence()
            if self.parent() is not None and hasattr(self.parent(), "_refresh_license_banner"):
                self.parent()._refresh_license_banner()

    # ----- Updates ------------------------------------------------------
    def _build_updates(self) -> QWidget:
        from factory.version import VERSION
        w, lay = _page("Updates")
        lay.addWidget(QLabel(f"Installed version: {VERSION}"))
        btn = QPushButton("Check for updates")
        btn.setMinimumHeight(40)
        style_button(btn, "primary")
        btn.clicked.connect(self._check_updates)
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return w

    def _check_updates(self):
        from factory.ui import update_ui
        update_ui.check_now(self)

    # ----- User ---------------------------------------------------------
    def _build_user(self) -> QWidget:
        w, lay = _page("User")
        name = str(self._user.get("username") or "—")
        lay.addWidget(QLabel(f"Signed in as: {name}  ·  role: {self._role or '—'}"))
        btn = QPushButton("Sign out")
        btn.setMinimumHeight(40)
        style_button(btn, "secondary")
        btn.clicked.connect(self._sign_out)
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return w

    def _sign_out(self):
        self.accept()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_do_logout"):
            parent._do_logout()

    # ----- About --------------------------------------------------------
    def _build_about(self) -> QWidget:
        from factory.version import VERSION
        from factory.ui.splash import AboutDialog
        w, lay = _page("About")
        lay.addWidget(QLabel(f"Simpl'd · version {VERSION}"))
        btn = QPushButton("Show details")
        btn.setMinimumHeight(40)
        style_button(btn, "secondary")
        btn.clicked.connect(lambda: AboutDialog(self).exec())
        lay.addWidget(btn, 0, Qt.AlignLeft)
        lay.addStretch(1)
        return w
