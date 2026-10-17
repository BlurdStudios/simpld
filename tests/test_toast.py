"""Toast notes replace stacked success popups. Temp objects only."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication, QWidget
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    return app, QWidget()


def test_toast_shows_and_replaces():
    from factory.ui import toast as T
    app, host = _app()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    t1 = T.toast(host, "First note.")
    app.processEvents()
    assert t1 is not None and t1.isVisible() and "First note." in t1.text()
    assert "#166534" in t1.styleSheet(), "ok toast is green"
    t2 = T.toast(host, "Second note.", ok=False)
    app.processEvents()
    assert "#991B1B" in t2.styleSheet(), "err toast is red"
    assert not t1.isVisible(), "new toast replaces the old one"
    T.dismiss()
    host.close()


def test_print_closes_dialog_with_toast():
    from unittest import mock as _mock
    from PySide6.QtWidgets import QDialog
    from factory.ui import toast as T
    from factory.ui.label_preview import ReelLabelDialog
    app, host = _app()
    host.resize(800, 600)
    host.show()
    app.processEvents()
    job = {"job_no": 4169, "job_name": "TOAST JOB"}
    reel = {"roll_no": 1, "gross": 11.0, "core": 0.28, "net": 10.72,
            "created_at": "2026-10-10 10:00:00"}
    printer = _mock.Mock()
    printer.print_reel_label.return_value = True
    d = ReelLabelDialog(host, job, reel, printer)
    d.do_print()
    app.processEvents()
    assert d.result() == QDialog.DialogCode.Accepted, "good print closes the dialog itself"
    assert T._active is not None and T._active.isVisible()
    assert "Roll #1" in T._active.text()
    T.dismiss()
    host.close()


def test_print_failure_stays_open_with_warning():
    from unittest import mock as _mock
    from PySide6.QtWidgets import QMessageBox, QDialog
    from factory.ui.label_preview import ReelLabelDialog
    _, host = _app()
    job = {"job_no": 4169, "job_name": "TOAST JOB"}
    reel = {"roll_no": 2, "gross": 11.0, "core": 0.28, "net": 10.72,
            "created_at": "2026-10-10 10:00:00"}
    printer = _mock.Mock()
    printer.print_reel_label.side_effect = RuntimeError("printer offline")
    d = ReelLabelDialog(host, job, reel, printer)
    with _mock.patch.object(QMessageBox, "warning") as w:
        d.do_print()
        assert w.call_count == 1, "failed print still warns"
    assert d.result() != QDialog.DialogCode.Accepted, "failed print keeps the dialog open"
    host.close()


if __name__ == "__main__":
    test_toast_shows_and_replaces()
    test_print_closes_dialog_with_toast()
    test_print_failure_stays_open_with_warning()
    print("test_toast PASS")
