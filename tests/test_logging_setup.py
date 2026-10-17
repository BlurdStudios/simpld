"""Crash logging: rotating file log + global excepthook. Temp dirs only."""
import logging
import logging.handlers
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest


@pytest.fixture(autouse=True)
def _restore_root_logger():
    """Keep handlers added in one test from leaking into the others."""
    root = logging.getLogger()
    before = list(root.handlers)
    old_level = root.level
    yield
    for h in list(root.handlers):
        if h not in before:
            root.removeHandler(h)
            h.close()
    root.setLevel(old_level)


def _file_handlers(log_dir: str) -> list:
    """RotatingFileHandler instances on the root logger for log_dir/simpld.log."""
    target = os.path.join(os.path.abspath(log_dir), "simpld.log")
    return [h for h in logging.getLogger().handlers
            if (isinstance(h, logging.handlers.RotatingFileHandler)
                and getattr(h, "baseFilename", None) == target)]


def test_setup_logging_writes_file(tmp_path):
    from factory.logging_setup import setup_logging
    log_path = setup_logging(str(tmp_path))
    assert log_path == os.path.join(str(tmp_path), "simpld.log")
    logging.getLogger("simpld").info("hello")
    with open(log_path, encoding="utf-8") as f:
        content = f.read()
    assert "hello" in content


def test_setup_logging_twice_no_duplicate_handlers(tmp_path):
    from factory.logging_setup import setup_logging
    setup_logging(str(tmp_path))
    setup_logging(str(tmp_path))
    assert len(_file_handlers(str(tmp_path))) == 1


def test_excepthook_logs_unhandled_exception(tmp_path):
    from unittest import mock
    from factory.logging_setup import setup_logging, install_excepthook
    log_path = setup_logging(str(tmp_path))
    install_excepthook(log_path)
    original = sys.excepthook
    try:
        with mock.patch("PySide6.QtWidgets.QMessageBox.critical") as crit:
            sys.excepthook(ValueError, ValueError("boom"), None)
        crit.assert_called_once()  # operator is told where the log lives
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert "Unhandled exception" in content
        assert "boom" in content
    finally:
        sys.excepthook = original


def test_excepthook_does_not_log_secret_values(tmp_path):
    """The hook records only the traceback — local variable values never
    reach the log. The fake token is built from two literals so the test
    source line (which the traceback prints) cannot contain it either."""
    from unittest import mock
    from factory.logging_setup import setup_logging, install_excepthook
    log_path = setup_logging(str(tmp_path))
    install_excepthook(log_path)
    original = sys.excepthook
    try:
        def _crash():
            tok = "super-" + "secret-token-123"  # noqa: F841 (fake local)
            raise ValueError("boom")

        _, _, tb = sys.exc_info()
        try:
            _crash()
        except ValueError:
            _, _, tb = sys.exc_info()
        with mock.patch("PySide6.QtWidgets.QMessageBox.critical"):
            sys.excepthook(ValueError, ValueError("boom"), tb)
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert "secret-token-123" not in content
        assert "session_token" not in content and "pass_hash" not in content
        assert "boom" in content  # the exception message itself is allowed
    finally:
        sys.excepthook = original
