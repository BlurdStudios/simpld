"""Settings: one dialog, a sidebar of areas, a Back button.

Areas: General, Sync, Licence, Updates, User, About. Each area is its own
page so a setting only lives in one place, all built from the shared theme
(nav rows, white cards, semantic buttons — same visual language as the
main window).

Deliberately ABSENT by owner request:
  Printer — the label printer has NO setup UI and NO detection loop. It is
  a direct USB-to-serial (VCOM) device: a hardware.json port overrides,
  otherwise it is found quietly by USB ID. Settings would only invite
  fiddling.
  Sync inputs — the licence IS the verification: the site ID comes from
  the installed licence and the dashboard address is fixed. One button
  runs sync now.
About is a page here, not a separate dialog.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QHBoxLayout, QVBoxLayout, QLabel, QPushButton,
                               QStackedWidget, QLineEdit, QSpinBox,
                               QMessageBox, QWidget, QFrame, QScrollArea)

from factory.ui import brand as _brand
from factory.ui.styles import style_button, set_banner

AREAS = ("General", "Sync", "Licence", "Updates", "User", "About")


def _page(title: str, desc: str = "") -> tuple[QWidget, QVBoxLayout]:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(18, 14, 18, 14)
    lay.setSpacing(10)
    h = QLabel(title)
    h.setProperty("page", "title")
    lay.addWidget(h)
    if desc:
        d = QLabel(desc)
        d.setProperty("page", "desc")
        d.setWordWrap(True)
        lay.addWidget(d)
    return w, lay


def _hint(text: str) -> QLabel:
    lbl = QLabel(text)
    lbl.setProperty("hint", True)
    lbl.setWordWrap(True)
    return lbl


def _card() -> tuple[QFrame, QVBoxLayout]:
    """A themed white card (frame.root) — the standard container everywhere
    else in the app; settings pages are built from these."""
    card = QFrame()
    card.setProperty("card", True)
    cl = QVBoxLayout(card)
    cl.setContentsMargins(16, 14, 16, 14)
    cl.setSpacing(8)
    return card, cl


def _kv_row(key: str, value: str, wrap: bool = False) -> QHBoxLayout:
    """One label/value row inside a card (caption + value)."""
    row = QHBoxLayout()
    row.setSpacing(10)
    a = QLabel(key)
    a.setProperty("field", "caption")
    a.setFixedWidth(120)
    a.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
    b = QLabel(value)
    b.setProperty("hint", True)
    b.setWordWrap(wrap)
    row.addWidget(a)
    row.addWidget(b, 1)
    return row


def _button(text: str, kind: str, tip: str = "") -> QPushButton:
    btn = QPushButton(text)
    btn.setMinimumHeight(40)
    style_button(btn, kind)
    if tip:
        btn.setToolTip(tip)
    return btn


class SettingsDialog(QDialog):
    def __init__(self, parent=None, user: dict | None = None, role: str = ""):
        super().__init__(parent)
        self.setWindowTitle(f"Settings — {_brand.PRODUCT_NAME}")
        self.setMinimumSize(760, 500)
        self._user = user or {}
        self._role = role or ""
        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(12)
        body = QHBoxLayout()
        body.setSpacing(14)

        # Sidebar — same quiet nav rows as the main window (white active
        # card, ink text), not a default list widget.
        side = QFrame()
        side.setObjectName("sidebar")
        side.setFixedWidth(180)
        side_lay = QVBoxLayout(side)
        side_lay.setContentsMargins(10, 10, 10, 10)
        side_lay.setSpacing(4)
        self._nav_buttons: list[QPushButton] = []
        for i, area in enumerate(AREAS):
            b = QPushButton(area)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            style_button(b, "nav")
            b.clicked.connect(lambda _=False, idx=i: self._select(idx))
            side_lay.addWidget(b)
            self._nav_buttons.append(b)
        side_lay.addStretch(1)
        body.addWidget(side)

        # Pages scroll when a small window clips them.
        self.stack = QStackedWidget()
        builders = {
            "General": self._build_general,
            "Sync": self._build_sync,
            "Licence": self._build_licence,
            "Updates": self._build_updates,
            "User": self._build_user,
            "About": self._build_about,
        }
        for area in AREAS:
            page = builders[area]()
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)

        foot = QHBoxLayout()
        self.btn_back = _button("← Back", "secondary", "Close Settings.")
        self.btn_back.clicked.connect(self.accept)
        foot.addWidget(self.btn_back)
        foot.addStretch(1)
        root.addLayout(foot)

        self._select(0)

    def _select(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        for j, b in enumerate(self._nav_buttons):
            b.setChecked(j == idx)

    # ----- General ------------------------------------------------------
    def _build_general(self) -> QWidget:
        w, lay = _page("General", "How this PC runs Simpl'd.")
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
        card, cl = _card()
        cl.addLayout(_kv_row("Data source", backend))
        cl.addLayout(_kv_row("Devices", mode, wrap=True))
        lay.addWidget(card)
        lay.addWidget(_hint(
            "The weigh machine is auto-detected from the station strips. "
            "The label printer needs no setup — it is found automatically "
            "on its USB port."))
        lay.addStretch(1)
        return w

    # ----- Sync ---------------------------------------------------------
    def _build_sync(self) -> QWidget:
        from factory import sync as SYNC
        w, lay = _page("Sync", "Push production to the vendor dashboard.")
        card, cl = _card()
        url, site, _key = SYNC._cfg()
        cl.addLayout(_kv_row("Dashboard", url, wrap=True))
        cl.addLayout(_kv_row("Site", site or "— (from your licence)"))
        row = QHBoxLayout()
        row.setSpacing(10)
        cap = QLabel("Last sync")
        cap.setProperty("field", "caption")
        self.s_status = QLabel("")
        self.s_status.setProperty("hint", True)
        self.s_status.setWordWrap(True)
        row.addWidget(cap)
        row.addWidget(self.s_status, 1)
        cl.addLayout(row)
        lay.addWidget(card)

        btn = _button("Sync now", "primary", "Push today's data to the vendor dashboard now.")
        btn.clicked.connect(self._sync_now)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(_hint(
            "Verified by your licence — the site ID comes with it, nothing to "
            "enter. The factory works fully offline; sync is outbound only."))
        lay.addStretch(1)
        self._refresh_sync_status()
        return w

    def _refresh_sync_status(self):
        try:
            from factory import sync as SYNC
            last = SYNC.last_sync()
            if not SYNC.configured():
                self.s_status.setText("Waiting for the vendor to finish setting "
                                      "up sync for this PC.")
            elif last.get("at"):
                ok = "ok" if last.get("ok") else "failed"
                self.s_status.setText(f"{last['at']} ({ok})")
            else:
                self.s_status.setText("Set up. Not yet pushed.")
        except Exception as e:
            self.s_status.setText(f"Sync status unavailable: {e}")

    def _sync_now(self):
        from factory import sync as SYNC
        if not SYNC.configured():
            self._refresh_sync_status()
            return
        try:
            SYNC.push_now()
        except Exception as e:
            QMessageBox.warning(self, "Sync", f"Sync failed: {e}")
        self._refresh_sync_status()

    # ----- Licence ------------------------------------------------------
    def _build_licence(self) -> QWidget:
        w, lay = _page("Licence", "This copy's licence and trial.")
        card, cl = _card()
        self.l_text = QLabel("")
        self.l_text.setProperty("hint", True)
        self.l_text.setWordWrap(True)
        cl.addWidget(self.l_text)
        btn = _button("Enter licence key", "primary",
                      "Paste the key or import the licence file from the vendor.")
        btn.clicked.connect(self._enter_key)
        cl.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        cl.addWidget(_hint("A licence sent by the vendor is installed "
                           "automatically at the next heartbeat."))
        lay.addWidget(card)

        tcard, tl = _card()
        tl.addWidget(QLabel("Need more trial time?"))
        req = QHBoxLayout()
        req.setSpacing(8)
        self.t_days = QSpinBox()
        self.t_days.setRange(1, 90)
        self.t_days.setValue(14)
        self.t_days.setSuffix(" days")
        self.t_reason = QLineEdit()
        self.t_reason.setPlaceholderText("Reason (optional)")
        btn_req = _button("Request trial extension", "secondary",
                          "Asks the vendor — appears on your licence once approved.")
        btn_req.clicked.connect(self._request_trial)
        req.addWidget(self.t_days)
        req.addWidget(self.t_reason, 1)
        req.addWidget(btn_req)
        tl.addLayout(req)
        self.t_status = _hint("")
        tl.addWidget(self.t_status)
        lay.addWidget(tcard)
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
        w, lay = _page("Updates", "Keep this PC on the current release.")
        card, cl = _card()
        cl.addLayout(_kv_row("Installed version", VERSION))
        btn = _button("Check for updates", "primary")
        btn.clicked.connect(self._check_updates)
        cl.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addWidget(card)
        lay.addStretch(1)
        return w

    def _check_updates(self):
        from factory.ui import update_ui
        update_ui.check_now(self)

    # ----- User ---------------------------------------------------------
    def _build_user(self) -> QWidget:
        w, lay = _page("User", "Who is signed in on this PC.")
        card, cl = _card()
        name = str(self._user.get("username") or "—")
        cl.addLayout(_kv_row("Signed in as", name))
        cl.addLayout(_kv_row("Role", self._role or "—"))
        lay.addWidget(card)
        btn = _button("Sign out", "danger")
        btn.clicked.connect(self._sign_out)
        lay.addWidget(btn, 0, Qt.AlignmentFlag.AlignLeft)
        lay.addStretch(1)
        return w

    def _sign_out(self):
        self.accept()
        parent = self.parent()
        if parent is not None and hasattr(parent, "_do_logout"):
            parent._do_logout()

    # ----- About --------------------------------------------------------
    def _build_about(self) -> QWidget:
        """About inline (no separate dialog): identity lockup, the facts,
        and the licence card with the trial countdown."""
        w, lay = _page("About")
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(_brand.lockup(size=28, mark_px=44, parent=True))
        holder.addStretch(1)
        lay.addLayout(holder)

        card, cl = _card()
        cl.addLayout(_kv_row("Version", _brand.VERSION))
        cl.addLayout(_kv_row("Product of", _brand.PARENT_NAME))
        cl.addLayout(_kv_row("Works offline", "Yes — no data leaves this computer"))
        cl.addLayout(_kv_row("Storage", "Local SQLite database on this PC"))
        lay.addWidget(card)

        # Licence card (same content the old About dialog carried).
        try:
            from factory import license as _LIC
            _st = _LIC.license_status()
            _disp = _LIC.license_display(_st)
            _mid = _LIC.machine_fingerprint()
        except Exception as _e:
            _LIC, _st, _disp, _mid = None, {}, f"Licence check failed: {_e}", "?"
        lic_card, lc = _card()
        cap = QLabel("LICENCE")
        cap.setProperty("field", "caption")
        lc.addWidget(cap)
        lic_lbl = QLabel(_disp)
        lic_lbl.setProperty("hint", True)
        lic_lbl.setWordWrap(True)
        lc.addWidget(lic_lbl)
        # Trial countdown (vivid), same wording as the old dialog.
        try:
            _lic_d = (_st or {}).get("lic") or {}
            if _LIC is not None and str(_lic_d.get("edition") or "") == "trial":
                _left = _LIC.license_days_left(_lic_d)
                _trial_lbl = QLabel("")
                _trial_lbl.setWordWrap(True)
                if _left is not None:
                    if _left < 0:
                        _trial_lbl.setText(f"Trial expired {-_left}d ago — contact your vendor for a full key.")
                        set_banner(_trial_lbl, "err")
                        lc.addWidget(_trial_lbl)
                    elif _left == 0:
                        _trial_lbl.setText("Trial expires TODAY — back up your data and contact your vendor.")
                        set_banner(_trial_lbl, "warn")
                        lc.addWidget(_trial_lbl)
                    else:
                        _trial_lbl.setText(f"Trial: {_left} day{'s' if _left != 1 else ''} left"
                                           f" (expires {_lic_d.get('expires')}).")
                        set_banner(_trial_lbl, "warn" if _left <= 3 else "info")
                        lc.addWidget(_trial_lbl)
        except Exception:
            pass
        mid_lbl = QLabel(f"Machine ID: {_mid}")
        mid_lbl.setProperty("hint", True)
        mid_lbl.setWordWrap(True)
        mid_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lc.addWidget(mid_lbl)
        lay.addWidget(lic_card)
        lay.addStretch(1)
        return w
