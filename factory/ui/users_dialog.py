"""Admin-only user manager: add logins, reset passwords, change roles."""
from __future__ import annotations
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
                               QMessageBox, QComboBox, QAbstractItemView, QInputDialog)
from factory import data_access as DA
from factory.ui.styles import style_button

ROLES = ("packager", "dispatcher", "admin")


class UsersDialog(QDialog):
    def __init__(self, parent, me: str):
        super().__init__(parent)
        self.me = me
        self.setWindowTitle("Users — logins & roles (admin)")
        self.setMinimumSize(560, 480)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(10)
        ht = QLabel("Logins & roles")
        ht.setProperty("dialog", "title")
        lay.addWidget(ht)
        hd = QLabel("Packager sees packing · dispatcher sees dispatch · admin sees all.")
        hd.setProperty("dialog", "desc")
        hd.setWordWrap(True)
        lay.addWidget(hd)

        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Username", "Role", "Created"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(44)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(True)
        self.table.setFocusPolicy(Qt.NoFocus)
        lay.addWidget(self.table, 1)

        add = QHBoxLayout()
        add.setSpacing(8)
        self.u = QLineEdit()
        self.u.setPlaceholderText("new username")
        self.u.setMinimumHeight(48)
        self.p = QLineEdit()
        self.p.setPlaceholderText("password (4+ chars)")
        self.p.setEchoMode(QLineEdit.Password)
        self.p.setMinimumHeight(48)
        self.r = QComboBox()
        self.r.addItems(list(ROLES))
        self.r.setMinimumHeight(48)
        self.btn_add = QPushButton("+ ADD")
        self.btn_add.setMinimumHeight(48)
        style_button(self.btn_add, "primary")
        add.addWidget(self.u, 2)
        add.addWidget(self.p, 2)
        add.addWidget(self.r, 1)
        add.addWidget(self.btn_add, 1)
        lay.addLayout(add)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_pw = QPushButton("RESET PASSWORD")
        self.btn_role = QPushButton("CHANGE ROLE")
        self.btn_del = QPushButton("DELETE")
        self.btn_close = QPushButton("Close")
        for b in (self.btn_pw, self.btn_role, self.btn_close):
            b.setMinimumHeight(52)
            style_button(b, "secondary")
            row.addWidget(b, 1)
        self.btn_del.setMinimumHeight(52)
        style_button(self.btn_del, "danger")
        row.addWidget(self.btn_del, 1)
        lay.addLayout(row)

        self.btn_add.clicked.connect(self.add)
        self.btn_pw.clicked.connect(self.reset_pw)
        self.btn_role.clicked.connect(self.change_role)
        self.btn_del.clicked.connect(self.delete)
        self.btn_close.clicked.connect(self.accept)
        self._rows: list[dict] = []
        self.refresh()

    def refresh(self):
        try:
            self._rows = DA.list_users()
        except Exception as e:
            # LAN: user management is local-admin only (no server op).
            self._rows = []
            self.table.setRowCount(0)
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            QMessageBox.warning(self, "Users", msg)
            return
        self.table.setRowCount(0)
        for r in self._rows:
            i = self.table.rowCount()
            self.table.insertRow(i)
            self.table.setItem(i, 0, QTableWidgetItem(r["username"]))
            self.table.setItem(i, 1, QTableWidgetItem(r["role"]))
            self.table.setItem(i, 2, QTableWidgetItem((r["created_at"] or "")[:16]))

    def selected(self):
        i = self.table.currentRow()
        return self._rows[i] if 0 <= i < len(self._rows) else None

    def add(self):
        try:
            DA.create_user(self.u.text(), self.p.text(), self.r.currentText())
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Users", str(e))
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            return QMessageBox.warning(self, "Users", msg)
        self.u.clear()
        self.p.clear()
        self.refresh()

    def reset_pw(self):
        r = self.selected()
        if not r:
            return QMessageBox.warning(self, "Users", "Select a user row first.")
        pw, ok = QInputDialog.getText(self, "Reset password", f"New password for {r['username']}:",
                                      QLineEdit.Password)
        if not ok or not pw:
            return
        try:
            DA.set_password(r["username"], pw)
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Users", str(e))
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            return QMessageBox.warning(self, "Users", msg)
        from factory.ui.toast import toast
        toast(self, f"Password reset for {r['username']}.")

    def change_role(self):
        r = self.selected()
        if not r:
            return QMessageBox.warning(self, "Users", "Select a user row first.")
        if r["username"] == self.me:
            return QMessageBox.warning(self, "Users", "Can't change your own role while logged in.")
        role, ok = QInputDialog.getItem(self, "Change role", f"Role for {r['username']}:",
                                        list(ROLES), editable=False)
        if not ok:
            return
        try:
            DA.set_role(r["username"], role)
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Users", str(e))
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            return QMessageBox.warning(self, "Users", msg)
        self.refresh()

    def delete(self):
        r = self.selected()
        if not r:
            return QMessageBox.warning(self, "Users", "Select a user row first.")
        if r["username"] == self.me:
            return QMessageBox.warning(self, "Users", "Can't delete yourself while logged in.")
        if QMessageBox.question(self, "Delete", f"Delete login '{r['username']}'?") != QMessageBox.Yes:
            return
        try:
            DA.delete_user(r["username"])
        except (ValueError, PermissionError) as e:
            return QMessageBox.warning(self, "Users", str(e))
        except Exception as e:
            try:
                msg, _kind = DA.describe_error(e)
            except Exception:
                msg = str(e)
            return QMessageBox.warning(self, "Users", msg)
        self.refresh()
