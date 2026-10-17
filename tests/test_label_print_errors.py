"""Label dialogs surface the real printer error (no swallowing).

ReelLabelDialog.do_print / CartonLabelDialog.do_print must show the actual
exception text from the Qt driver (e.g. missing Citizen driver on Windows)
under a short 'Could not print' heading, and must not close/toast on failure.
Offline Qt + temp DB — no physical jobs, no production writes.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _fresh_db():
    from factory import db as DB
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-print-err-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    return tmp


def _app_host():
    from PySide6.QtWidgets import QApplication, QWidget
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    host = QWidget()
    return app, host


class _RaisingPrinter:
    def __init__(self, msg):
        self._msg = msg
        self.calls = []

    def print_reel_label(self, payload):
        self.calls.append(("reel", payload))
        raise RuntimeError(self._msg)

    def print_carton_label(self, payload):
        self.calls.append(("carton", payload))
        raise RuntimeError(self._msg)


def _assert_warning_shows_real_error(dialog, method_name, printer_msg):
    from unittest import mock as _mock
    from PySide6.QtWidgets import QDialog, QMessageBox
    from factory.ui import toast as T
    with _mock.patch.object(QMessageBox, "warning") as w, \
            _mock.patch.object(T, "toast") as toast_mock:
        getattr(dialog, method_name)()
        assert w.call_count == 1, "exception must warn once"
        args = w.call_args[0]
        assert args[1] == "Could not print", args
        assert printer_msg in args[2], f"warning must include real error, got: {args[2]!r}"
        toast_mock.assert_not_called()
    assert dialog.result() != QDialog.DialogCode.Accepted, \
        "exception must not close the dialog as success"


def test_reel_dialog_shows_actual_exception():
    _fresh_db()
    from factory.ui.label_preview import ReelLabelDialog
    _, host = _app_host()
    msg = "Printer 'Citizen CLS621II' not found — install test"
    job = {"job_no": 4169, "job_name": "PRINT ERR JOB"}
    reel = {"roll_no": 1, "gross": 11.0, "core": 0.28, "net": 10.72,
            "created_at": "2026-10-10 10:00:00"}
    d = ReelLabelDialog(host, job, reel, _RaisingPrinter(msg))
    _assert_warning_shows_real_error(d, "do_print", msg)
    host.close()


def test_carton_dialog_shows_actual_exception():
    _fresh_db()
    from factory.ui.label_preview import CartonLabelDialog
    _, host = _app_host()
    msg = "Printer 'Citizen CLS621II' refused the job — test offline"
    detail = {"job_no": 4169, "job_name": "PRINT ERR JOB", "carton_no": "C0001",
              "bundle_no": 1, "packed_at": "2026-10-10 10:00:00",
              "num_reels": 0, "total_gross": 10.5, "total_core": 0.0,
              "total_net": 10.5, "reels": [], "qr": "VASAVI:C0001"}
    d = CartonLabelDialog(host, detail, _RaisingPrinter(msg))
    _assert_warning_shows_real_error(d, "do_print", msg)
    host.close()


if __name__ == "__main__":
    test_reel_dialog_shows_actual_exception()
    test_carton_dialog_shows_actual_exception()
    print("test_label_print_errors PASS")
