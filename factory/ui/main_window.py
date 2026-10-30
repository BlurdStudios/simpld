"""Native desktop shell: role-gated sidebar nav, offline-first, scanner-friendly."""
from __future__ import annotations
from PySide6.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                               QPushButton, QStackedWidget, QLabel, QButtonGroup)
from PySide6.QtCore import Signal, QTimer, QSize, Qt, QPointF, QEvent
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
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


class NavGroupButton(QPushButton):
    """Collapsible nav-group header (glyph + label + chevron).

    The chevron is painted rather than parented: hover and click then cover the
    whole row (the `navGroup` QSS reserves the right-hand room via padding).
    """

    def __init__(self, text: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self._expanded = True

    def is_expanded(self) -> bool:
        return self._expanded

    def set_expanded(self, on: bool) -> None:
        on = bool(on)
        if on != self._expanded:
            self._expanded = on
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor("#52525B"), 1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        cx = self.width() - 12 - 8          # 8 = half the 16px chevron slot
        cy = self.height() / 2
        path = QPainterPath()
        if self._expanded:                  # ^ — the open state (click closes)
            path.moveTo(cx - 4.5, cy + 2.5)
            path.lineTo(cx, cy - 2.5)
            path.lineTo(cx + 4.5, cy + 2.5)
        else:                               # v — collapsed (click opens)
            path.moveTo(cx - 4.5, cy - 2.5)
            path.lineTo(cx, cy + 2.5)
            path.lineTo(cx + 4.5, cy - 2.5)
        p.drawPath(path)


class NavTree(QWidget):
    """Indented child rows of a nav group, tied to it by the guide rail.

    One quiet vertical connector runs the height of the block and every child
    gets an elbow stub curving into its glyph. Accent marks the rail (and the
    active child's elbow); everything else stays hairline grey — matching the
    sidebar reference the group was drawn from.
    """

    RAIL_X = 18.0        # left inset of the vertical connector
    ELBOW_R = 12.0       # corner radius of the stubs
    STUB_END = 40.0      # px; the stub stops just short of the child glyph
    TOP_OVER = 20.0      # how far the rail reaches above the first child

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(34, 2, 0, 6)
        lay.setSpacing(2)
        self._rows: list[QPushButton] = []

    def add_row(self, btn: QPushButton) -> None:
        self._rows.append(btn)
        self.layout().addWidget(btn)

    def _stub(self, y: float) -> QPainterPath:
        """The elbow that leaves the rail into a child row centred on y."""
        r = self.ELBOW_R
        path = QPainterPath(QPointF(self.RAIL_X, y - r))
        path.quadTo(QPointF(self.RAIL_X, y), QPointF(self.RAIL_X + r, y))
        path.lineTo(self.STUB_END, y)
        return path

    def paintEvent(self, event):
        if not self._rows:
            return
        ys = [w.geometry().center().y() for w in self._rows]
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen_accent = QPen(QColor(BRAND.ACCENT), 2.0)
        pen_grey = QPen(QColor("#D4D4D8"), 1.5)
        for pen in (pen_accent, pen_grey):
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        # Quiet elbows for every child but the last one...
        p.setPen(pen_grey)
        for y in ys[:-1]:
            p.drawPath(self._stub(y))
        # ...the rail itself, which turns into the last child's elbow.
        r = self.ELBOW_R
        rail = QPainterPath(QPointF(self.RAIL_X, ys[0] - self.TOP_OVER))
        if len(ys) > 1:
            rail.lineTo(self.RAIL_X, ys[-1] - r)
        rail.quadTo(QPointF(self.RAIL_X, ys[-1]), QPointF(self.RAIL_X + r, ys[-1]))
        rail.lineTo(self.STUB_END, ys[-1])
        p.setPen(pen_accent)
        p.drawPath(rail)
        # The active child keeps an accent elbow of its own; the rail's last
        # corner already points at the bottom row.
        for w in self._rows[:-1]:
            if w.isChecked():
                p.drawPath(self._stub(w.geometry().center().y()))


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

        self.pack_nav: dict[str, QPushButton] = {}
        shortcut_n = 0

        for i, key in enumerate(keys):
            label, _ = PAGES[key]
            if key == "packaging":
                # Collapsible group: an ink header row (glyph + label +
                # chevron) over an indented children block. Both children map
                # to the same stack page; they only differ by the page mode.
                hdr = NavGroupButton("   " + label)
                hdr.setMinimumHeight(40)
                hdr.setToolTip("Packaging workspaces — click to collapse or expand")
                style_button(hdr, "navGroup")
                self._set_nav_icon(hdr, NAV_ICON.get("packaging"))
                hdr.clicked.connect(self._toggle_pack_group)
                side.addWidget(hdr)
                self.pack_group = hdr
                tree = NavTree()
                for clabel, cmode, cicon in (("Reel packing", "reel", "reel"),
                                             ("Box packing", "carton", "box")):
                    cb = QPushButton("   " + clabel)
                    cb.setCheckable(True)
                    cb.setMinimumHeight(36)
                    style_button(cb, "navChild")
                    self._set_nav_icon(cb, cicon, px=16)
                    cb.setToolTip("Packaging workspace: " + clabel.lower())
                    shortcut_n += 1
                    cb.setShortcut(f"F{shortcut_n}")
                    self.nav.addButton(cb, i)
                    cb.clicked.connect(
                        lambda _=False, m=cmode: self._activate_packaging(m))
                    tree.add_row(cb)
                    self.btns.append(cb)
                    self.pack_nav[cmode] = cb
                side.addWidget(tree)
                self.pack_tree = tree
                self.stack.addWidget(self.pages[key])
                continue
            b = QPushButton("   " + label)
            b.setCheckable(True)
            b.setMinimumHeight(40)
            style_button(b, "nav")
            self._set_nav_icon(b, NAV_ICON.get(key))
            shortcut_n += 1
            b.setShortcut(f"F{shortcut_n}")
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
        # Licence / trial timer (always visible so the floor sees expiry).
        self.lic_label = QLabel("")
        self.lic_label.setWordWrap(True)
        self.lic_label.hide()
        side.addWidget(self.lic_label)
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
        if self.pack_nav:
            # The first nav row is the packaging workspace; its default mode
            # is Reel packing (the page applies it in its constructor).
            self.pack_nav["reel"].setChecked(True)
            self._activate_packaging("reel")
        else:
            self.btns[0].setChecked(True)
        self.stack.setCurrentIndex(0)
        self.statusBar().showMessage(
            f"F1–F{shortcut_n}: switch pages."
            + (" Shared packager DB — auto-refresh every 15s." if DA.is_lan() else ""))
        self._refresh_license_banner()
        if DA.is_lan():
            self._lan_timer = QTimer(self)
            self._lan_timer.timeout.connect(self._lan_refresh)
            self._lan_timer.start(15000)

        # One silent update check per process, a few seconds after the window
        # is up (frozen fleet builds only; see factory/ui/update_ui.py). The
        # only visible effect is a toast when a newer version exists.
        try:
            from factory.ui import update_ui
            QTimer.singleShot(4000, lambda: update_ui.auto_check(self))
        except Exception:
            pass

    def _toggle_pack_group(self) -> None:
        self._set_pack_expanded(not self.pack_group.is_expanded())

    def _set_pack_expanded(self, on: bool) -> None:
        """Show/hide the packaging children block (the header row stays)."""
        if not hasattr(self, "pack_group"):
            return
        self.pack_group.set_expanded(on)
        self.pack_tree.setVisible(on)

    def _activate_packaging(self, mode: str) -> None:
        """Switch the packaging page to a nested workspace (reel/carton)."""
        self._set_pack_expanded(True)
        pack = self.pages.get("packaging")
        if pack is None or not hasattr(pack, "set_mode"):
            return
        try:
            pack.set_mode(mode)
        except Exception:
            pass

    def _set_nav_icon(self, btn, name: str | None, px: int = 18) -> None:
        """Attach a brand glyph and keep it in sync with the checked state.

        The active nav row is a white card carrying an accent glyph, so the
        `-accent` variant replaces the default one while the row is the
        active page. This is also where nav rows start watching focus, so the
        accent ring is reserved for keyboard navigation (see eventFilter).
        """
        if not name:
            return
        btn.setProperty("navIcon", name)
        btn.installEventFilter(self)
        btn.setIconSize(QSize(px, px))
        btn.setIcon(BRAND.icon(
            name, variant="accent" if btn.isChecked() else "default"))
        btn.toggled.connect(
            lambda on, b=btn, n=name: b.setIcon(BRAND.icon(
                n, variant="accent" if on else "default")))

    # Focus reasons that count as keyboard navigation. Mouse/tab-bar clicks
    # focus the row too, but must not paint the accent ring.
    _KB_FOCUS = (Qt.FocusReason.TabFocusReason,
                 Qt.FocusReason.BacktabFocusReason,
                 Qt.FocusReason.ShortcutFocusReason)

    def eventFilter(self, obj, event):
        """Keyboard-only focus ring on nav rows (QSS keys off `kbFocus`)."""
        if event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            on = (event.type() == QEvent.Type.FocusIn
                  and event.reason() in self._KB_FOCUS)
            if on != bool(obj.property("kbFocus")):
                obj.setProperty("kbFocus", on)
                style_button(obj, obj.property("kind") or "nav")
        return super().eventFilter(obj, event)

    def show_about(self):
        from factory.ui.splash import AboutDialog
        AboutDialog(self).exec()

    def _refresh_license_banner(self):
        """Persistent licence/trial timer in the sidebar (vivid when expiring).

        Trial licences also stamp the window title (TRIAL · Nd left) so the
        mode is visible even with the sidebar collapsed.
        """
        try:
            from factory import license as _LIC
            from factory.ui.styles import set_banner
            st = _LIC.license_status()
            lic = st.get("lic") or {}
            ed = str(lic.get("edition") or "")
            state = str(st.get("state") or "")
            left = _LIC.license_days_left(lic if lic else None)
            try:
                _user = getattr(self, "user", {}) or {}
                _base = f"Simpl'd — {_user.get('username', '')} ({_user.get('role', '')})"
            except Exception:
                _base = str(self.windowTitle() or "Simpl'd")
            if ed == "trial" and left is not None and left >= 0:
                self.setWindowTitle(f"{_base} — TRIAL · {left}d left")
            if state in ("none", "invalid", "blocked"):
                self.lic_label.setText(f"⚠ {st.get('reason') or state}")
                set_banner(self.lic_label, "err")
                self.lic_label.show()
            elif ed == "trial" and left is not None:
                if left < 0:
                    self.lic_label.setText(f"Trial expired {-left}d ago — contact vendor")
                    set_banner(self.lic_label, "err")
                elif left == 0:
                    self.lic_label.setText("Trial expires TODAY — contact vendor")
                    set_banner(self.lic_label, "warn")
                elif left <= 3:
                    self.lic_label.setText(f"Trial: {left}d left — contact vendor soon")
                    set_banner(self.lic_label, "warn")
                else:
                    self.lic_label.setText(f"Trial: {left}d left")
                    set_banner(self.lic_label, "info")
                self.lic_label.show()
            elif state in ("expiring", "grace"):
                self.lic_label.setText(f"{_LIC.license_display(st)}")
                set_banner(self.lic_label, "warn")
                self.lic_label.show()
            else:
                self.lic_label.hide()
        except Exception:
            pass

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
