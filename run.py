"""Entry point. Fully offline: python3 run.py

Dev hot-reload (no more stop/start while iterating):
    python3 run.py --dev            # auto-restart on any .py change
    SIMPLD_DEV=1 python3 run.py     # same via env

Production / factory floor: plain `python3 run.py` (login, no watcher).
No sample jobs are ever seeded; the DB starts empty and the first-run
admin is created in the login dialog (administrator chooses the password).
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

DEV = "--dev" in sys.argv or os.environ.get("SIMPLD_DEV") == "1"


def _py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in ("__pycache__", ".venv", ".git", "data")]
        for f in filenames:
            if f.endswith(".py"):
                out.append(os.path.join(dirpath, f))
    out.append(os.path.join(root, "run.py"))
    return out


def _snapshot(files: list[str]) -> dict[str, float]:
    snap = {}
    for f in files:
        try:
            snap[f] = os.path.getmtime(f)
        except OSError:
            pass
    return snap


def _install_dev_reloader(app, files: list[str]):
    """Restart the whole process when source changes. Stdlib only (offline-safe).

    QSS lives in styles.py, so a restart picks up theme edits too. DB/hardware
    state persists (SQLite); only unsaved UI state resets.
    """
    from PySide6.QtCore import QTimer
    snap = _snapshot(files)

    def check():
        nonlocal snap
        now = _snapshot(files)
        if now != snap:
            changed = sorted(set(now) - set(snap) | {f for f in now if now.get(f) != snap.get(f)})
            print(f"[dev] changed: {', '.join(os.path.basename(f) for f in changed[:5])} — restarting…",
                  flush=True)
            sys.stdout.flush()
            os.execv(sys.executable, [sys.executable] + sys.argv)

    t = QTimer(app)
    t.timeout.connect(check)
    t.start(1000)
    print("[dev] hot-reload ON (watching *.py, 1s). Factory: run without --dev.", flush=True)


def main():
    from PySide6.QtWidgets import QApplication
    app = QApplication(sys.argv)
    app.setApplicationName("Simpl'd")
    app.setOrganizationName("Simpl'd")
    # Fusion gives identical QSS rendering on every factory PC / distro.
    # Without it the native style draws its own combo arrows, checkboxes and
    # headers on top of the theme, which is half the "broken UI" look.
    try:
        app.setStyle("Fusion")
    except Exception:
        pass
    from factory.ui.styles import APP_QSS
    app.setStyleSheet(APP_QSS)

    # Brand: taskbar / window / Alt-Tab icon. Set on the QApplication so every
    # window and dialog inherits it.
    try:
        from factory.ui import brand
        brand.apply_app_icon(app)
    except Exception:
        pass

    # Crash logging first: from here on any uncaught exception is recorded
    # in <data dir>/logs/simpld.log and shown to the operator (the Windows
    # build is windowed, so stderr is invisible).
    from factory.logging_setup import setup_logging, install_excepthook
    log_path = setup_logging()
    install_excepthook(log_path)

    # Backend first: SIMPLD_SERVER_HOST set -> dispatcher-style LAN client
    # (no local DB touched); unset -> local packager DB. Must run before
    # any local-DB access so a remote login never creates local users.
    from factory import data_access as DA
    try:
        DA.configure_from_env()
    except ValueError as e:
        from PySide6.QtWidgets import QMessageBox
        QMessageBox.critical(None, "Simpl'd", f"Bad LAN settings:\n{e}")
        return

    # Licensing: locked States (none/invalid/blocked) open the Activate
    # dialog before login (local mode only; LAN dispatchers are enforced
    # server-side per-login). Demo builds bypass. Grace/expiring proceed
    # with a banner. Backup/Export of the DB file itself never needs login.
    try:
        from factory import license as _LIC
        _st = _LIC.license_status()
        os.environ["SIMPLD_LICENSE_STATE"] = str(_st.get("state") or "")
        print(f"[license] {_LIC.license_display(_st)}", flush=True)
        if not DA.is_lan() and _st.get("state") in ("ok", "expiring", "grace"):
            try:
                _LIC.record_station()
            except Exception as _e:
                print(f"[license] station heartbeat failed: {_e}", flush=True)
        if _st.get("state") in ("grace", "expiring"):
            try:
                from PySide6.QtWidgets import QMessageBox as _MB
                _MB.warning(
                    None, "Simpl'd licence",
                    f"{_LIC.license_display(_st)}\n\nContact admin.")
            except Exception:
                pass
    except Exception as _e:
        print(f"[license] check skipped: {_e}", flush=True)

    if not DA.is_lan():
        from factory.seed import ensure_production_db
        ensure_production_db()
        from factory.ui.activate import ensure_activated
        if not ensure_activated():
            return

    if DEV:
        _install_dev_reloader(app, _py_files(os.path.dirname(os.path.abspath(__file__))))

    from factory.ui.main_window import MainWindow
    from factory.ui.login import LoginDialog

    # Branded splash while the DB/login UI comes up. Closes as soon as the real
    # window appears — it never gates startup.
    splash = None
    try:
        from factory.ui.splash import show_splash, close_splash
        splash = show_splash()
    except Exception:
        splash = None

    try:
        while True:
            dlg = LoginDialog()
            if splash is not None:
                close_splash(splash)
                splash = None
            if dlg.exec() != LoginDialog.Accepted or not dlg.user:
                break  # Quit pressed / window closed -> exit
            try:
                w = MainWindow()
            except PermissionError:
                DA.logout()
                continue  # unknown/stale session fails closed -> back to login
            logged_out = []
            w.logout_requested.connect(lambda: (logged_out.append(True), w.close()))
            w.show()
            w.detect_devices()   # non-blocking auto-detect; pills self-update
            app.exec()
            w.deleteLater()
            # Real logout: invalidate the session so the next login starts clean.
            try:
                DA.logout()
            except Exception:
                pass
            if not logged_out:
                break  # window closed (X) -> exit for real
    finally:
        if splash is not None:
            close_splash(splash)


if __name__ == "__main__":
    main()
