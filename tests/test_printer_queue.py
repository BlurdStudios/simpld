"""Print queue viewer + print-button states (offline, no hardware).

PrinterQueueDialog helpers parse `lpstat` output (monkeypatched here, so
no CUPS needed); the label dialogs must show a loading state while
spooling, warn (not toast) on pre-flight problems, and leave the mock
path untouched.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from unittest import mock as _mock


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


class _FakeCompleted:
    def __init__(self, out):
        self.stdout = out
        self.stderr = ""
        self.returncode = 0


def _lpstat_state(*args, **kwargs):
    cmd = args[0]
    if cmd[:2] == ["lpstat", "-p"]:
        return _FakeCompleted(
            "printer Citizen-CLS621 is idle.  enabled since Wed 21 Oct 2026 06:13:05 PM IST\n")
    if cmd[:2] == ["lpstat", "-o"]:
        return _FakeCompleted(
            "Citizen-CLS621-7  onlybond  1024  Wed 21 Oct 2026 06:13:05 PM IST\n")
    raise AssertionError(cmd)


def test_queue_state_and_jobs_parse():
    from factory.ui import printer_queue as PQ
    with _mock.patch.object(PQ.subprocess, "run", side_effect=_lpstat_state):
        with _mock.patch.object(PQ, "cups_available", return_value=True):
            st = PQ.queue_state("Citizen-CLS621")
            assert st["available"] and st["enabled"] and st["state"] == "idle"
            assert st["reason"] == ""
            jobs = PQ.queue_jobs("Citizen-CLS621")
            assert jobs == [{"id": "Citizen-CLS621-7", "user": "onlybond",
                             "size": "1024",
                             "submitted": "Wed 21 Oct 2026 06:13:05 PM IST"}]
            assert PQ.describe_problem("Citizen-CLS621") is None


def test_queue_state_reason_blocks_print():
    from factory.ui import printer_queue as PQ

    def stuck(cmd, **kw):
        if cmd[:2] == ["lpstat", "-p"]:
            return _FakeCompleted(
                "printer Citizen-CLS621 now printing Citizen-CLS621-7.  enabled since X\n"
                "\tWaiting for printer to become available.\n")
        return _FakeCompleted("")

    with _mock.patch.object(PQ.subprocess, "run", side_effect=stuck):
        with _mock.patch.object(PQ, "cups_available", return_value=True):
            problem = PQ.describe_problem("Citizen-CLS621")
            assert problem and "Waiting for printer" in problem


def test_describe_problem_unknown_queue_is_none():
    from factory.ui import printer_queue as PQ

    def unknown(cmd, **kw):
        raise FileNotFoundError("no lpstat")

    with _mock.patch.object(PQ.subprocess, "run", side_effect=unknown):
        assert PQ.describe_problem("NOPE") is None
        assert PQ.queue_jobs("NOPE") == []


def test_printing_state_restores_button():
    _app()
    from PySide6.QtWidgets import QWidget, QPushButton
    from factory.ui.label_preview import _printing_state
    host = QWidget()
    btn = QPushButton("PRINT REEL STICKER", host)
    with _printing_state(btn):
        assert not btn.isEnabled()
        assert "Printing" in btn.text()
    assert btn.isEnabled()
    assert btn.text() == "PRINT REEL STICKER"
    host.close()


def test_preflight_skipped_for_mock_printer():
    _app()
    import tempfile
    from factory import db as DB
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-preflight-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from PySide6.QtWidgets import QWidget
    from factory.ui.label_preview import ReelLabelDialog

    class FakeOK:
        def __init__(self):
            self.calls = 0

        def print_reel_label(self, payload):
            self.calls += 1
            return True

    host = QWidget()
    job = {"job_no": 4169, "job_name": "PREFLIGHT JOB"}
    reel = {"id": None, "roll_no": 3, "gross": 11.0, "core": 0.28,
            "net": 10.72, "created_at": "2026-10-10 10:00:00"}
    printer = FakeOK()
    d = ReelLabelDialog(host, job, reel, printer)
    with _mock.patch("factory.ui.toast.toast") as t:
        d.do_print()
        assert printer.calls == 1, "mock print must proceed without CUPS"
        assert t.call_count == 1
    host.close()


def test_preflight_blocks_and_warns_on_stuck_queue():
    _app()
    from PySide6.QtWidgets import QWidget
    from factory.ui.label_preview import ReelLabelDialog

    class FakeOK:
        def __init__(self):
            self.calls = 0

        def print_reel_label(self, payload):
            self.calls += 1
            return True

        @property
        def printer_name(self):
            return "Citizen-CLS621"

    host = QWidget()
    job = {"job_no": 4169, "job_name": "PREFLIGHT JOB"}
    reel = {"id": None, "roll_no": 3, "gross": 11.0, "core": 0.28,
            "net": 10.72, "created_at": "2026-10-10 10:00:00"}
    printer = FakeOK()
    d = ReelLabelDialog(host, job, reel, printer)
    with _mock.patch("factory.ui.printer_queue.describe_problem",
                     return_value="Printer Citizen-CLS621 reports: stuck"), \
         _mock.patch("factory.ui.label_preview.QMessageBox.warning") as w, \
         _mock.patch("factory.ui.toast.toast") as t:
        d.do_print()
        assert w.call_count == 1, "stuck queue must warn"
        assert "stuck" in str(w.call_args)
        assert printer.calls == 0, "must not spool into a stuck queue"
        t.assert_not_called()
        assert d.btn_print.isEnabled(), "button must be usable again"
    host.close()


if __name__ == "__main__":
    test_queue_state_and_jobs_parse()
    test_queue_state_reason_blocks_print()
    test_describe_problem_unknown_queue_is_none()
    test_printing_state_restores_button()
    test_preflight_skipped_for_mock_printer()
    test_preflight_blocks_and_warns_on_stuck_queue()
    print("test_printer_queue PASS")
