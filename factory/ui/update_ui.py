"""Update UI — About-dialog check, startup notice, download/install flow.

All network and disk work happens off the GUI thread (factory/updater.py);
this module only renders decisions. The failure policy mirrors the rest of
the app: an update problem is never fatal and never blocks the floor — worst
case the operator keeps running the current version and simpld.log says why.

Flow (manual, from About):
    Check for updates -> update found -> "Update now" -> download with
    progress -> SHA-256 verified -> installer launched silently -> this app
    quits; Inno replaces the files and relaunches Simpl'd.

Flow (automatic, frozen fleet builds): one silent check a few seconds after
the window comes up; only a toast appears when something is available.
"""
from __future__ import annotations

import logging
import os
import sys

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import QApplication, QMessageBox, QProgressDialog

from factory import updater
from factory.version import VERSION

log = logging.getLogger("simpld.updater")

# QThread objects must stay referenced until run() returns, even when the
# dialog that started them is already gone (login/logout cycles, About
# closed mid-check). Process-lifetime list, pruned on finish.
_workers: list[QThread] = []
_auto_checked = False


def _keep(worker: QThread) -> None:
    _workers.append(worker)
    worker.finished.connect(lambda: _discard(worker))


def _discard(worker: QThread) -> None:
    try:
        _workers.remove(worker)
    except ValueError:
        pass


class CheckWorker(QThread):
    """One update check off the GUI thread; emits UpdateInfo | None."""

    done = Signal(object)

    def run(self) -> None:
        info = None
        try:
            info = updater.check_for_update()
        except Exception:
            # check_for_update is already defensive; this is belt-and-braces
            # so a thread crash can never take the app down.
            log.exception("update check failed")
        self.done.emit(info)


class DownloadWorker(QThread):
    """Download + SHA-256 verification off the GUI thread."""

    progress = Signal(int, int)   # bytes done, bytes total (-1 unknown)
    done = Signal(str, str)       # installer path, error text ("" on success)

    def __init__(self, info: updater.UpdateInfo, parent=None):
        super().__init__(parent)
        self._info = info

    def run(self) -> None:
        try:
            path = updater.download_and_verify(self._info,
                                               progress=self.progress.emit)
            self.done.emit(path, "")
        except updater.UpdateError as exc:
            self.done.emit("", str(exc))
        except Exception:
            log.exception("update download crashed")
            self.done.emit("", "Unexpected error while downloading.")


def check_async(parent, on_result) -> None:
    """Run a check off-thread; call on_result(UpdateInfo | None) on the GUI."""
    worker = CheckWorker()
    worker.done.connect(on_result)
    _keep(worker)
    worker.start()


def auto_check(parent) -> None:
    """One silent startup check (frozen fleet builds only) -> toast if newer."""
    global _auto_checked
    if _auto_checked or os.environ.get("SIMPLD_UPDATE_DISABLE") == "1":
        return
    if (not getattr(sys, "frozen", False)
            and os.environ.get("SIMPLD_UPDATE_FORCE") != "1"):
        return  # source/dev runs have no installed app to replace
    _auto_checked = True

    def done(info) -> None:
        if info is None:
            return  # up to date or offline — the floor never notices
        try:
            from factory.ui.toast import toast
            toast(parent,
                  f"Update available: v{info.version} — open About to install",
                  kind="info", ms=7000)
        except Exception:
            log.exception("update toast failed")

    check_async(parent, done)


def check_now(parent, on_done=None) -> None:
    """Manual check from About; always reports the outcome and calls on_done."""
    def result(info) -> None:
        if on_done is not None:
            try:
                on_done()
            except Exception:
                pass
        if info is None:
            QMessageBox.information(
                parent, "Simpl'd update",
                f"No newer version was found — you have v{VERSION}.\n\n"
                "If this PC is offline, updates can still be published on "
                "the packager's LAN channel (see UPDATES.md).")
        else:
            offer_update(parent, info)

    check_async(parent, result)


def offer_update(parent, info: updater.UpdateInfo) -> None:
    """Ask the operator; on yes, download, verify and hand off to Inno."""
    label = updater.SOURCE_LABELS.get(info.source, info.source)
    notes = (info.notes or "").strip()
    if len(notes) > 500:
        notes = notes[:500] + "…"
    text = (f"Simpl'd {info.version} is available — this PC runs {VERSION}.\n\n"
            f"Channel: {label}")
    if notes:
        text += f"\n\nWhat's new:\n{notes}"
    text += ("\n\nInstall now? Simpl'd will close, Windows may ask for "
             "administrator confirmation, and the app reopens by itself "
             "when the update finishes.")
    box = QMessageBox(parent)
    box.setWindowTitle("Simpl'd update")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(text)
    install = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
    box.exec()
    if box.clickedButton() is install:
        _download_and_install(parent, info)


def _download_and_install(parent, info: updater.UpdateInfo) -> None:
    progress = QProgressDialog(f"Downloading Simpl'd {info.version}…",
                               "", 0, 0, parent)
    progress.setWindowTitle("Simpl'd update")
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.setAutoClose(False)
    progress.setAutoReset(False)
    progress.setMinimumWidth(380)
    progress.show()

    worker = DownloadWorker(info)

    def on_progress(done: int, total: int) -> None:
        if total and total > 0:
            progress.setRange(0, total)
            progress.setValue(min(done, total))
            progress.setLabelText(
                f"Downloading Simpl'd {info.version}… {done * 100 // total}%")

    def on_done(path: str, err: str) -> None:
        progress.close()
        if err:
            QMessageBox.critical(
                parent, "Simpl'd update",
                f"The update could not be prepared:\n\n{err}\n\n"
                "Simpl'd keeps running the current version.")
            return
        try:
            updater.install_and_relaunch(path)
        except updater.UpdateError as exc:
            QMessageBox.critical(parent, "Simpl'd update", str(exc))
            return
        # Inno Setup takes over from here: it closes this app, replaces the
        # files and relaunches Simpl'd (see simpld.iss [Run]). Quitting is
        # the last thing this process does on purpose.
        log.info("update %s: installer started; quitting for install",
                 info.version)
        QApplication.quit()

    worker.progress.connect(on_progress)
    worker.done.connect(on_done)
    _keep(worker)
    worker.start()
