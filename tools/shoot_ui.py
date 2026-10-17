#!/usr/bin/env python3
"""Offscreen screenshot harness — render real widgets to PNG for visual review.

Uses a throwaway SIMPLD_DB so nothing touches data/factory.db.

    QT_QPA_PLATFORM=offscreen python3 tools/shoot_ui.py
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_tmp = tempfile.mkdtemp(prefix="simpld-shot-")
os.environ["SIMPLD_DB"] = os.path.join(_tmp, "shot.db")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

OUT = os.path.join(ROOT, "brand", "shots")
os.makedirs(OUT, exist_ok=True)

app = QApplication(sys.argv[:1])
app.setStyle("Fusion")
from factory.ui.styles import APP_QSS  # noqa: E402
app.setStyleSheet(APP_QSS)
from factory.ui import brand  # noqa: E402
brand.apply_app_icon(app)


def shot(w, name):
    path = os.path.join(OUT, f"{name}.png")
    w.grab().save(path)
    print("wrote", path)


# ---- splash pixmap
from factory.ui.splash import _splash_pixmap  # noqa: E402
from PySide6.QtGui import QPixmap  # noqa: E402
pm = _splash_pixmap()
pm.save(os.path.join(OUT, "splash.png"))
print("wrote splash.png")

# ---- login (setup mode = fresh empty DB -> first-run admin)
from factory.ui.login import LoginDialog  # noqa: E402
dlg = LoginDialog()
dlg.show()
app.processEvents()
shot(dlg, "login")

# ---- main window: log in as admin on the throwaway DB
from factory import data_access as DA  # noqa: E402
from factory.db import init_db  # noqa: E402
init_db()
try:
    DA.configure_from_env()
except Exception:
    pass
from factory.seed import ensure_production_db  # noqa: E402
ensure_production_db()
from factory.ui.main_window import MainWindow  # noqa: E402
try:
    import factory.auth as A
    c = None
    from factory.db import get_conn, now_iso
    conn = get_conn()
    n = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
    if not n:
        conn.execute(
            "INSERT INTO users(username, pass_hash, role, created_at) VALUES(?,?,?,?)",
            ("admin", A.hash_password("admin123"), "admin", now_iso()))
    conn.commit()
    conn.close()
except Exception as e:
    print("user seed skipped:", e)

session = A.authenticate("admin", "admin123")
DA.set_session(session) if hasattr(DA, "set_session") else None
w = MainWindow()
w.resize(1320, 840)
w.show()
app.processEvents()
app.processEvents()
shot(w, "main")

# ---- about dialog
from factory.ui.splash import AboutDialog  # noqa: E402
ab = AboutDialog(w)
ab.show()
app.processEvents()
shot(ab, "about")
print("done")