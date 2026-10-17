"""Crash logging for Simpl'd. Stdlib only (offline-safe).

- setup_logging(): attach a RotatingFileHandler to the root logger writing
  simpld.log under <app data dir>/logs (overridable for tests).
- install_excepthook(): record uncaught exceptions to that log and show a
  dialog instead of dying silently in the windowed Windows build
  (build/windows/simpld.spec sets console=False, so stderr is invisible).

Log content rule: never log passwords, session tokens or the pass_hash
column. The excepthook records only the exception type, message and
traceback — never argument/local values.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import sys


def setup_logging(log_dir: str | None = None) -> str:
    """Attach a rotating file handler to the root logger.

    Returns the path of the log file. Idempotent: calling it twice for the
    same directory does not add a second handler for that file.
    """
    from factory.db import app_data_dir

    if log_dir is None:
        log_dir = os.path.join(app_data_dir(), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, "simpld.log")

    target = os.path.abspath(log_path)
    for h in logging.getLogger().handlers:
        if (isinstance(h, logging.handlers.RotatingFileHandler)
                and getattr(h, "baseFilename", None) == target):
            return log_path  # already set up for this file

    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=1_000_000, backupCount=5, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)
    return log_path


def install_excepthook(log_path: str) -> None:
    """Record uncaught exceptions and tell the operator where the log is."""
    original = sys.excepthook

    def hook(exc_type, exc, tb):
        # Only the traceback is logged — never argument values.
        logging.getLogger("simpld").critical(
            "Unhandled exception", exc_info=(exc_type, exc, tb))
        if exc_type is KeyboardInterrupt:
            original(exc_type, exc, tb)
            return
        # A dialog failure must never mask the log write above.
        try:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(
                None, "Simpl'd",
                "Simpl'd hit an unexpected error and needs to close.\n\n"
                f"Details were saved to:\n{log_path}")
        except Exception:
            pass

    sys.excepthook = hook
