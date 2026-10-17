"""Offline login. Same software, three doors: packager / dispatcher / admin.

First run (empty users table): administrator setup — the administrator
chooses their own username + password; no known production credentials exist.
Later runs: normal username/password login. Sessions are DB-bound opaque
tokens via factory.auth (fail closed on forged/stale/unknown).
"""
from __future__ import annotations
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QFrame)
from PySide6.QtCore import Qt, QSettings
from factory import auth as A
from factory.db import get_conn, init_db, now_iso
from factory.ui import brand


def _is_lan() -> bool:
    try:
        from factory import data_access as _DA
        return _DA.is_lan()
    except Exception:
        return False


def _user_count() -> int:
    if _is_lan():
        # Remote mode: never touch/create a local DB or users here.
        # Setup happens once on the packager computer itself.
        return 1  # fail closed: assume users exist -> normal login
    try:
        init_db()
        c = get_conn()
        try:
            return int(c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"])
        finally:
            c.close()
    except Exception:
        return 1  # fail closed: assume users exist -> normal login


def _create_first_admin(username: str, password: str) -> dict | None:
    """Direct bootstrap insert for the empty-DB first run only.

    Refuses when users already exist (never an overwrite/bypass). Returns the
    authenticated session dict, or None with (ok, message) style errors
    raised as ValueError.

    Concurrent-setup safe: check + insert run inside one BEGIN IMMEDIATE
    transaction, so two first-run dialogs cannot create two admins.
    """
    if _is_lan():
        raise ValueError(
            "User setup runs on the packager computer itself — "
            "ask the administrator to create your login there.")
    u = (username or "").strip().lower()
    if not u:
        raise ValueError("Choose an administrator username.")
    if len(password or "") < 4:
        raise ValueError("Password must be at least 4 characters.")
    init_db()
    c = get_conn()
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
            if n:
                raise ValueError("Users already exist — sign in instead.")
            c.execute("INSERT INTO users(username, pass_hash, role, created_at) VALUES(?,?,?,?)",
                      (u, A.hash_password(password), "admin", now_iso()))
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
    finally:
        c.close()
    out = A.authenticate(u, password)
    return out


class LoginDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Simpl'd — login")
        self.setMinimumSize(440, 560)
        self.user: dict | None = None
        self._setup_mode = (_user_count() == 0)
        # Centered narrow card: the window may open maximized, the form stays
        # a compact 400px card instead of stretching wall to wall.
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 24, 24, 24)
        outer.addStretch(1)
        mid = QHBoxLayout()
        mid.addStretch(1)
        card = QFrame()
        card.setProperty("card", True)
        card.setFixedWidth(400)
        lay = QVBoxLayout(card)
        lay.setContentsMargins(24, 24, 24, 24)
        lay.setSpacing(10)
        mid.addWidget(card)
        mid.addStretch(1)
        outer.addLayout(mid)
        outer.addStretch(1)

        # Brand lockup: mark + wordmark, Blur'd parent line under the word.
        holder = QHBoxLayout()
        holder.addStretch(1)
        holder.addWidget(brand.lockup(size=30, mark_px=46, parent=True))
        holder.addStretch(1)
        lay.addLayout(holder)

        if self._setup_mode:
            sub = QLabel("First run — create the administrator login")
            sub.setProperty("field", "caption")
            sub.setAlignment(Qt.AlignCenter)
            sub.setWordWrap(True)
            lay.addWidget(sub)

        user_cap = QLabel("Administrator username" if self._setup_mode else "Username")
        user_cap.setProperty("field", "caption")
        lay.addWidget(user_cap)
        self.name = QLineEdit()
        self.name.setMinimumHeight(40)
        self.name.setPlaceholderText("e.g. admin")
        lay.addWidget(self.name)

        pw_cap = QLabel("Choose a password" if self._setup_mode else "Password")
        pw_cap.setProperty("field", "caption")
        lay.addWidget(pw_cap)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.Password)
        self.pw.setMinimumHeight(40)
        self.pw.setPlaceholderText("••••••")
        lay.addWidget(self.pw)

        self.err = QLabel("")
        self.err.setProperty("banner", "err")
        self.err.setWordWrap(True)
        self.err.setVisible(False)
        lay.addWidget(self.err)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_login = QPushButton("Create administrator" if self._setup_mode else "Sign in")
        self.btn_login.setMinimumHeight(40)
        from factory.ui.styles import style_button
        style_button(self.btn_login, "primary")
        self.btn_quit = QPushButton("Quit")
        self.btn_quit.setMinimumHeight(40)
        style_button(self.btn_quit, "secondary")
        row.addWidget(self.btn_login, 2)
        row.addWidget(self.btn_quit, 1)
        lay.addLayout(row)

        if self._setup_mode:
            hint = QLabel("This login is created once on this computer. "
                          "Keep the password with the plant head — there is no default.")
        else:
            hint = QLabel("Sign in with your factory login. Contact the administrator for access.")
        hint.setProperty("hint", True)
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignCenter)
        lay.addWidget(hint)

        self.btn_login.clicked.connect(self.try_login)
        self.btn_quit.clicked.connect(self.reject)
        self.pw.returnPressed.connect(self.try_login)
        self.name.returnPressed.connect(self.try_login)

        if not self._setup_mode:
            try:
                last = QSettings("Sri Vasavi Shrink Film", "simpld").value("last_user", "")
                if last:
                    self.name.setText(str(last))
                    self.pw.setFocus()
            except Exception:
                pass

    def _fail(self, text: str):
        self.err.setText(text)
        self.err.setVisible(True)
        try:
            s = self.err.style()
            s.unpolish(self.err)
            s.polish(self.err)
        except Exception:
            pass
        self.pw.clear()
        self.pw.setFocus()

    def try_login(self):
        if self._setup_mode:
            try:
                out = _create_first_admin(self.name.text(), self.pw.text())
            except ValueError as e:
                return self._fail(str(e))
            if not out:
                return self._fail("Could not create the administrator. Try again.")
            try:
                QSettings("Sri Vasavi Shrink Film", "simpld").setValue("last_user", out["username"])
            except Exception:
                pass
            self.user = out
            self.accept()
            return
        if _is_lan():
            from factory import data_access as _DA
            try:
                u = _DA.authenticate(self.name.text(), self.pw.text())
            except Exception as e:
                return self._fail(f"Connection issue: {e} Check the network and try again.")
            if not u:
                # data_access LAN raises LanAuthError on bad creds; None is
                # unexpected — treat as a failed login without local fallback.
                return self._fail("Wrong username or password. Caps lock? Try again.")
        else:
            u = A.authenticate(self.name.text(), self.pw.text())
        if not u:
            self.err.setText("Wrong username or password. Caps lock? Try again.")
            self.err.setVisible(True)
            try:
                s = self.err.style()
                s.unpolish(self.err)
                s.polish(self.err)
            except Exception:
                pass
            self.pw.clear()
            self.pw.setFocus()
            return
        try:
            QSettings("Sri Vasavi Shrink Film", "simpld").setValue("last_user", u["username"])
        except Exception:
            pass
        self.user = u
        self.accept()
