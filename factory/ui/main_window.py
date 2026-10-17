"""Native desktop shell: role-gated sidebar nav, offline-first, scanner-friendly."""
from __future__ import annotations
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QPushButton, QStackedWidget, QLabel, QButtonGroup)
from PySide6.QtCore import Signal, QTimer, QSize
from factory import data_access as DA
from factory.hardware import devices as DEV  # noqa: F401  (applies env/file config)
from factory.ui.page_packaging import PackagingPage
from factory.ui.page_stock import StockPage
from factory.ui.page_dispatch import DispatchPage
from factory.ui.pages_misc import JobsPage, HistoryPage, SummaryPage
from factory.ui.styles import style_button
from factory.ui import brand as BRAND
from factory.ui.device_strip import DeviceStrip

# key -> (nav label, page factory). Factories run lazily per window.
PAGES = {
    "packaging": ("Packaging", PackagingPage),
    "stock": ("Ready Stock", StockPage),
    "dispatch": ("Dispatch", DispatchPage),
    "jobs": ("Jobs", JobsPage),
    "history": ("History", HistoryPage),
    "summary": ("Summary", SummaryPage),
}

# nav/sidebar glyph per page key (see factory/ui/brand.py + brand/svg/)
NAV_ICON = {
    "packaging": "packaging",
    "stock": "stock",
    "dispatch": "dispatch",
    "jobs": "jobs",
    "history": "history",
    "summary": "summary",
}


class MainWindow(QMainWindow):
    logout_requested = Signal()

    def __init__(self, user: dict | None = None):
        super().__init__()
        # Real session only: re-resolve username/role on the configured
        # backend (local DB session or LAN whoami). Forged caller dicts,
        # unknown users, stale or cross-DB tokens fail closed (no window).
        # LAN mode never creates a local DB: no init_db/seed here (run.py
        # owns backend selection + local bootstrap before login).
        if not DA.is_lan():
            from factory.db import init_db
            from factory.seed import ensure_production_db
            init_db()
            ensure_production_db()
        actor = DA.current_user()
        if actor is None:
            raise PermissionError("No authenticated session (denied)")
        role = actor.get("role", "")
        if role not in ("packager", "dispatcher", "admin"):
            raise PermissionError(f"Unknown role '{role}' (denied)")
        self.user = actor
        user = actor
        # Theme lives on the QApplication (see run.py). Do NOT set it here:
        # a second copy on the window fights the app one for subcontrols
        # (combo arrows, indicators) and dialogs stop inheriting it.
        self.setWindowTitle(f"Simpl'd — {user.get('username','')} ({role})")
        self.resize(1320, 840)
        self.setMinimumSize(1080, 680)

        root = QWidget()
        self.setCentralWidget(root)
        lay = QHBoxLayout(root)
        lay.setContentsMargins(0, 0, 20, 0)
        lay.setSpacing(20)

        # ---- sidebar ----
        side = QVBoxLayout()
        side.setContentsMargins(12, 14, 12, 12)
        side.setSpacing(6)
        # Brand lockup: mark + wordmark with the Blur'd parent line under the word.
        head = BRAND.lockup(size=21, mark_px=26, parent=True)
        side.addWidget(head)
        who = QLabel(f"{user.get('username','')} · {role.upper()}")
        who.setObjectName("sideSub")
        side.addWidget(who)
        side.addSpacing(10)
        self.nav = QButtonGroup(self)
        self.nav.setExclusive(True)
        self.btns: list[QPushButton] = []

        # Build only the pages this role may see. Pages needing cross-refresh
        # callbacks get them only if both sides exist for this role.
        keys = [k for k in DA.pages_for(role) if k in PAGES]
        if not keys:  # unknown role -> safe minimum
            keys = ["stock"]
        self.stack = QStackedWidget()
        self.pages: dict[str, QWidget] = {}

        # Create shared pages first so callbacks can wire regardless of order.
        for key in keys:
            label, factory = PAGES[key]
            if key == "packaging":
                page = factory(on_stock_changed=lambda: self._refresh_stock_views())
            elif key == "dispatch":
                page = factory(on_stock_changed=lambda: self._refresh_stock_views())
            elif key == "jobs":
                page = factory(on_changed=lambda: self._refresh_after_jobs())
            elif key == "history":
                try:
                    page = factory()
                except TypeError:
                    page = factory()
            else:
                page = factory()
            self.pages[key] = page

        for i, key in enumerate(keys):
            label, _ = PAGES[key]
            b = QPushButton("   " + label)
            b.setCheckable(True)
            b.setMinimumHeight(40)
            style_button(b, "nav")
            self._set_nav_icon(b, NAV_ICON.get(key))
            b.setShortcut(f"F{i + 1}")
            self.nav.addButton(b, i)
            side.addWidget(b)
            self.btns.append(b)
            self.stack.addWidget(self.pages[key])

        # Every station strip self-refreshes on its own detection result;
        # this hook only re-arms the Packaging page's hardware-gated buttons.
        for _page in self.pages.values():
            for _strip in _page.findChildren(DeviceStrip):
                _strip.detected.connect(self._on_devices_detected)

        self._keys = keys
        side.addStretch(1)
        # No "Devices" nav item and no config dialog: the station device strips
        # auto-detect the hardware and are the only hardware surface. The
        # admin-only Users manager stays (local only — no LAN server op
        # exists for user management, so it is hidden over LAN).
        if role == "admin" and not DA.is_lan():
            self.btn_users = QPushButton("   Users")
            self.btn_users.setMinimumHeight(40)
            style_button(self.btn_users, "nav")
            self._set_nav_icon(self.btn_users, "users")
            side.addWidget(self.btn_users)
            self.btn_users.clicked.connect(self.manage_users)
        self.btn_about = QPushButton("   About")
        self.btn_about.setMinimumHeight(40)
        style_button(self.btn_about, "nav")
        self._set_nav_icon(self.btn_about, "info")
        side.addWidget(self.btn_about)
        self.btn_about.clicked.connect(self.show_about)
        self.btn_logout = QPushButton("   Sign out")
        self.btn_logout.setMinimumHeight(40)
        style_button(self.btn_logout, "nav")
        self._set_nav_icon(self.btn_logout, "signout")
        side.addWidget(self.btn_logout)
        self.btn_logout.clicked.connect(self._do_logout)
        side.addSpacing(4)
        self.db_label = QLabel(self._backend_text())
        self.db_label.setObjectName("dbPath")
        self.db_label.setWordWrap(True)
        self.db_label.hide()
        side.addWidget(self.db_label)
        wrap = QWidget()
        wrap.setObjectName("sidebar")
        wrap.setLayout(side)
        wrap.setFixedWidth(200)
        lay.addWidget(wrap)

        # ---- pages ----
        lay.addWidget(self.stack, 1)
        self.nav.buttonClicked.connect(lambda b: self.stack.setCurrentIndex(self.nav.id(b)))
        self.btns[0].setChecked(True)
        self.stack.setCurrentIndex(0)
        if DA.is_lan():
            self.statusBar().showMessage(
                "LAN mode: shared packager DB. F1–F6: switch pages. Auto-refresh every 15s.")
            self._lan_timer = QTimer(self)
            self._lan_timer.timeout.connect(self._lan_refresh)
            self._lan_timer.start(15000)
        else:
            self.statusBar().showMessage("F1–F6: switch pages.")

    def _set_nav_icon(self, btn, name: str | None) -> None:
        """Attach a brand glyph and keep it in sync with the checked state.

        The accent nav pill is dark, so the white `-on` variant is swapped in
        whenever the row is the active page (or a hovered row is pressed).
        """
        if not name:
            return
        btn.setProperty("navIcon", name)
        btn.setIconSize(QSize(18, 18))
        btn.setIcon(BRAND.icon(name, on=btn.isChecked()))
        btn.toggled.connect(
            lambda on, b=btn, n=name: b.setIcon(BRAND.icon(n, on=on)))

    def show_about(self):
        from factory.ui.splash import AboutDialog
        AboutDialog(self).exec()

    @staticmethod
    def _backend_text() -> str:
        if DA.is_lan():
            try:
                import os
                host = (os.environ.get("SIMPLD_SERVER_HOST") or "").strip()
            except Exception:
                host = ""
            return f"LAN: {host or 'packager host'} (shared DB)"
        try:
            from factory.db import DB_PATH
            return DB_PATH
        except Exception:
            return "local DB"

    def _lan_refresh(self):
        """Remote auto-refresh: dispatcher sees packager changes without
        restarting. Failures surface in the status bar; no local fallback,
        no retry of writes (reads only)."""
        try:
            self._refresh_stock_views()
            pack = self.pages.get("packaging")
            if pack is not None and hasattr(pack, "refresh_jobs"):
                try:
                    pack.refresh_jobs()
                except Exception:
                    pass
            disp = self.pages.get("dispatch")
            if disp is not None and hasattr(disp, "refresh"):
                try:
                    disp.refresh()
                except Exception:
                    pass
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
                self.statusBar().showMessage(f"Connection issue: {msg}")
            except Exception:
                pass

    def _do_logout(self):
        """Real logout: invalidate the session, then ask run.py for login."""
        try:
            DA.logout()
        finally:
            self.logout_requested.emit()

    # ----- cross-page refresh (only what this role has) -----
    def _refresh_stock_views(self):
        for k in ("stock", "history", "summary"):
            p = self.pages.get(k)
            if p is not None and hasattr(p, "refresh"):
                try:
                    p.refresh()
                except Exception:
                    pass

    def _refresh_after_jobs(self):
        pack = self.pages.get("packaging")
        if pack is not None and hasattr(pack, "refresh_jobs"):
            try:
                pack.refresh_jobs()
            except Exception:
                pass
        self._refresh_stock_views()

    def detect_devices(self):
        """Kick off hardware auto-detection once the window is up.

        Runs on a worker thread (ports are opened and probed, which can take a
        second or two), so the window paints immediately. Every station strip
        updates itself when the probe finishes.
        """
        for page in self.pages.values():
            for strip in page.findChildren(DeviceStrip):
                strip.autodetect_now()

    def _on_devices_detected(self, _result=None):
        """Detection finished — refresh the pages that gate on hardware."""
        pack = self.pages.get("packaging")
        if pack is not None and hasattr(pack, "refresh_hardware"):
            try:
                pack.refresh_hardware()
            except Exception:
                pass

    def manage_users(self):
        from factory.ui.users_dialog import UsersDialog
        UsersDialog(self, self.user.get("username", "")).exec()

    def closeEvent(self, ev):
        super().closeEvent(ev)
