"""Device strip — the clickable, self-detecting device status row.

Every device is a pill: icon, name, state dot, detail. Configurable devices
(weigh machine, label printer) are **clickable** — clicking re-runs detection
for that device. The Refresh button re-runs detection for everything.

There is no Devices dialog and no port dropdown. `autodetect.detect_all()`
probes the serial ports and reads the OS printer list in a worker thread, so
the UI never blocks while ports are opened.

Detection is deliberately honest: a pill says "not found" when nothing answers
and never invents a port. The fail-closed behaviour in `devices.py` is
untouched, so an undetected scale still blocks weighing rather than silently
using the mock.

The Packaging strip can also carry a Demo switch (`demo_switch=True`, a
checkable ghost button): it toggles the explicit `demo_mode` config setting
that simulates the weigh machine and label printer for trainings/audience
demos. It asks for confirmation once and off-loads nothing silently; see
`factory/hardware/devices.py` (demo_mode()).

Demo builds only: prod installers ship with DEMO_ENABLED = False, and the
switch is then not created at all (even with `demo_switch=True`), so
operators can never turn it on. Demo installers (*-demo.exe) are USB-only.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QSize, QObject, QThread, Signal, QTimer
from PySide6.QtWidgets import (QWidget, QHBoxLayout, QLabel, QFrame, QSizePolicy,
                               QPushButton, QMessageBox)

from factory.hardware import status as HWStatus
from factory.hardware import autodetect as Auto
from factory.hardware import config as HWConfig
from factory.hardware import devices as HWDevices
from factory.ui import brand as _brand
from factory.ui.styles import style_button

# state -> dot colour
_STATE_STYLE = {
    "ready": "#166534",
    "active": "#166534",
    "idle": "#71717A",
    "setup": "#B45309",
    "error": "#991B1B",
    "detecting": "#7C3AED",
    "demo": "#BE185D",
}


class _DetectWorker(QObject):
    """Runs detection off the UI thread; probes open serial ports (slow)."""

    done = Signal(dict)

    def __init__(self, keys: tuple[str, ...] | None = None):
        super().__init__()
        self._keys = keys

    def run(self) -> None:
        import logging as _logging
        import time as _time
        _t0 = _time.monotonic()
        try:
            if self._keys is None:
                result = Auto.detect_all(save=True)
            else:
                # One pill was clicked: re-detect just that device.
                result = Auto.detect_one(self._keys[0], save=True)
            if not isinstance(result, dict):
                result = {"scale": None, "printer": None,
                          "error": "detection returned nothing"}
        except Exception as e:            # never let a worker die silently
            _logging.getLogger("simpld.hardware").warning(
                "detection worker failed: %s", e, exc_info=True)
            result = {"scale": None, "printer": None, "error": str(e)}
        try:
            result.setdefault("elapsed", round(_time.monotonic() - _t0, 2))
        except Exception:
            pass
        try:
            self.done.emit(result)
        except Exception:
            pass


class DetectionController(QObject):
    """Owns the worker thread so detection can't be launched twice at once.

    Prod guarantees:
    - busy() is always accurate: a failsafe timer clears a stuck run even
      if the worker thread dies without emitting done.
    - Threads are fully cleaned up (quit + wait + deleteLater) so repeated
      Refresh clicks cannot leak QThreads.
    - A second start() while busy is ignored (returns False); every strip
      refreshes from the single `finished` signal.
    """

    finished = Signal(dict)

    # Failsafe: even a hung serial driver must not wedge the pills in
    # "detecting" forever. Autodetect budgets ~12s; 30s here is the outer
    # "something is very wrong" net (worker killed, thread joined).
    FAILSAFE_MS = 30000

    def __init__(self, parent=None):
        super().__init__(parent)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._busy = False
        self._keys: tuple[str, ...] | None = None
        self._failsafe: QTimer | None = None
        self.last_result: dict | None = None
        self.last_error: str = ""

    def busy(self) -> bool:
        return self._busy

    def start(self, keys: tuple[str, ...] | None = None) -> bool:
        if self._busy:
            return False
        self._busy = True
        self._keys = keys
        self.last_error = ""
        HWStatus.set_detecting(keys)

        thread = QThread()
        worker = _DetectWorker(keys)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.done.connect(self._on_done)
        # If the thread itself fails to start, do not wedge the UI.
        try:
            thread.start()
        except Exception as e:
            self.last_error = str(e)
            HWStatus.clear_detecting()
            self._busy = False
            try:
                worker.deleteLater()
                thread.deleteLater()
            except Exception:
                pass
            return False
        self._thread = thread
        self._worker = worker
        try:
            self._failsafe = QTimer(self)
            self._failsafe.setSingleShot(True)
            self._failsafe.timeout.connect(self._on_failsafe)
            self._failsafe.start(self.FAILSAFE_MS)
        except Exception:
            self._failsafe = None
        return True

    def cancel(self) -> None:
        """Best-effort stop (used by tests / shutdown paths)."""
        thread = self._thread
        if thread is not None:
            try:
                thread.quit()
                thread.wait(1500)
            except Exception:
                pass

    def _on_failsafe(self) -> None:
        if not self._busy:
            return
        import logging as _logging
        _logging.getLogger("simpld.hardware").warning(
            "detection failsafe fired after %dms with keys=%r — "
            "clearing stuck 'detecting' state", self.FAILSAFE_MS, self._keys)
        self._finish({"scale": None, "printer": None,
                      "error": "detection timed out — check cables and click Refresh"})

    def _on_done(self, result: dict) -> None:
        self._finish(result if isinstance(result, dict) else {})

    def _finish(self, result: dict) -> None:
        try:
            if self._failsafe is not None:
                self._failsafe.stop()
        except Exception:
            pass
        self._failsafe = None
        HWStatus.clear_detecting()
        self._busy = False
        thread, worker = self._thread, self._worker
        self._thread = None
        self._worker = None
        if thread is not None:
            try:
                thread.quit()
                # Wait off the worker thread (this slot runs on the GUI
                # thread via queued connection), bounded so a hung
                # driver cannot freeze the UI at Refresh time.
                if thread.isRunning():
                    thread.wait(3000)
            except Exception:
                pass
            try:
                thread.deleteLater()
            except Exception:
                pass
        if worker is not None:
            try:
                worker.deleteLater()
            except Exception:
                pass
        try:
            self.last_result = dict(result)
            err = result.get("error") or ""
            errs = result.get("errors") or []
            if errs and not err:
                err = "; ".join(str(e) for e in errs[:3])
            self.last_error = str(err)
        except Exception:
            pass
        try:
            self.finished.emit(result)
        except Exception:
            pass


_shared_ctrl: "DetectionController | None" = None


def shared_controller() -> "DetectionController":
    """One controller for the whole app.

    Every station strip shares it, so two pages asking to detect at once cannot
    probe the same COM ports concurrently (and can't race on hardware.json).
    A second request while busy is simply ignored — every strip is already
    connected to `finished` and will refresh.
    """
    global _shared_ctrl
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance()
    except Exception:
        app = None
    # Recreate when the QApplication died (tests create/destroy apps) or
    # when the controller lost its parent.
    if _shared_ctrl is None:
        _shared_ctrl = DetectionController(app)
    else:
        try:
            if app is not None and _shared_ctrl.parent() is None:
                _shared_ctrl.setParent(app)
        except Exception:
            pass
    return _shared_ctrl


def reset_shared_controller() -> None:
    """Test aid: drop the shared controller so a fresh QApplication binds."""
    global _shared_ctrl
    try:
        if _shared_ctrl is not None:
            _shared_ctrl.cancel()
    except Exception:
        pass
    _shared_ctrl = None


class DevicePill(QFrame):
    """One device. Configurable devices are clickable to re-detect."""

    clicked = Signal(str)

    def __init__(self, spec: dict, clickable: bool, parent=None):
        super().__init__(parent)
        self.setProperty("card", True)
        self._spec = spec
        self._clickable = clickable
        self.setCursor(Qt.CursorShape.PointingHandCursor if clickable
                       else Qt.CursorShape.ArrowCursor)

        # Flat row: icon, name, dot, stretch, detail. A nested layout here let
        # Qt squeeze the name column until it overlapped the detail text.
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 7, 12, 7)
        row.setSpacing(9)

        ic = QLabel()
        ic.setPixmap(_brand.icon(spec["icon"]).pixmap(QSize(18, 18)))
        ic.setFixedSize(18, 18)
        ic.setStyleSheet("background: transparent;")
        row.addWidget(ic, 0, Qt.AlignmentFlag.AlignVCenter)

        name = QLabel(spec["label"])
        name.setStyleSheet("background: transparent;font-weight:600;font-size:12px;")
        # Device names must never elide — "Weigh mac…" on a shop floor is worse
        # than a strip that overflows. Pin the width to the full text.
        name.setMinimumWidth(name.fontMetrics().horizontalAdvance(spec["label"]))
        row.addWidget(name, 0, Qt.AlignmentFlag.AlignVCenter)

        dot = QLabel()
        dot.setFixedSize(8, 8)
        dot.setStyleSheet(f"background:{self._dot(spec['state'])};border-radius:4px;")
        row.addWidget(dot, 0, Qt.AlignmentFlag.AlignVCenter)

        row.addStretch(1)

        detail = QLabel(spec["detail"])
        detail.setObjectName("deviceDetail")
        detail.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        detail.setMinimumWidth(
            detail.fontMetrics().horizontalAdvance(spec["detail"]))
        row.addWidget(detail, 0, Qt.AlignmentFlag.AlignVCenter)

        # Every child refuses to shrink below its text, and so does the frame.
        # A strip in a too-narrow pane then overflows instead of overlapping.
        # QLayout exposes children as items (there is no .widgets()).
        for i in range(row.count()):
            item = row.itemAt(i)
            w = item.widget() if item is not None else None
            if w is None:
                continue
            p = w.sizePolicy()
            p.setHorizontalPolicy(QSizePolicy.Policy.Minimum)
            w.setSizePolicy(p)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Fixed)

        self._dot_label = dot
        self._name = name
        self._detail = detail
        self._apply_hover(False)
        self.refresh(spec)

    @staticmethod
    def _dot(state: str) -> str:
        return _STATE_STYLE.get(state, _STATE_STYLE["idle"])

    def _apply_hover(self, on: bool) -> None:
        if not self._clickable:
            return
        bg = "#F4F4F5" if on else "transparent"
        self.setStyleSheet(f"QFrame {{ background:{bg}; border-radius:8px; }}")

    def mouseReleaseEvent(self, ev):
        if self._clickable and ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._spec["key"])
            ev.accept()
            return
        super().mouseReleaseEvent(ev)

    def refresh(self, spec: dict) -> None:
        """Re-apply from a fresh status dict (icon is static per device)."""
        self._spec = spec
        state = spec["state"]
        self._dot_label.setStyleSheet(
            f"background:{self._dot(state)};border-radius:4px;")
        self._detail.setText(spec["detail"])
        tip = f"{spec['label']} — {spec['model']}\n{spec['detail']}"
        hint = spec.get("hint")
        if hint:
            tip += f"\n{hint}"
        if self._clickable:
            tip += "\n\nClick to detect this device again."
        self.setToolTip(tip)

    def spec(self) -> dict:
        return self._spec


class DeviceStrip(QWidget):
    """All devices for one station, plus a Refresh that re-detects."""

    device_clicked = Signal(str)      # device key
    detected = Signal(dict)           # detection result

    def __init__(self, station: str, parent=None, show_station_label: bool = False,
                 demo_switch: bool = False):
        super().__init__(parent)
        self._station = station
        self._pills: list[DevicePill] = {}
        self.btn_demo = None    # created below only in demo builds

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)

        if show_station_label:
            cap = QLabel(HWStatus.STATIONS.get(station, (station, []))[0])
            cap.setProperty("field", "caption")
            cap.setStyleSheet("background: transparent;")
            outer.addWidget(cap, 0)

        for spec in HWStatus.station_status(station):
            pill = DevicePill(spec, clickable=bool(spec["configurable"]))
            pill.installEventFilter(self)
            pill.clicked.connect(self._on_pill)
            self._pills[spec["key"]] = pill
            outer.addWidget(pill, 0)

        if demo_switch and HWConfig.demo_build_enabled():
            self.btn_demo = QPushButton("Demo")
            self.btn_demo.setCheckable(True)
            self.btn_demo.setMinimumHeight(28)
            # "outline", not "ghost": the demo switch must be findable at a
            # glance — a borderless transparent button disappears into the
            # strip and operators/owners reported not seeing it at all.
            style_button(self.btn_demo, "outline")
            try:
                self.btn_demo.setChecked(HWDevices.demo_mode())
            except Exception:
                self.btn_demo.setChecked(False)
            self._demo_label(self.btn_demo.isChecked())
            self.btn_demo.toggled.connect(self._toggle_demo)   # connect AFTER setChecked
            outer.addWidget(self.btn_demo, 0, Qt.AlignmentFlag.AlignVCenter)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.setMinimumHeight(28)
        self.btn_refresh.setIcon(_brand.icon("refresh"))
        self.btn_refresh.setIconSize(QSize(14, 14))
        style_button(self.btn_refresh, "ghost")
        self.btn_refresh.setToolTip("Detect the factory hardware again.")
        self.btn_refresh.clicked.connect(lambda: self.detect(None))
        outer.addWidget(self.btn_refresh, 0, Qt.AlignmentFlag.AlignVCenter)

        outer.addStretch(1)

        self._ctrl = shared_controller()
        self._ctrl.finished.connect(self._on_detected)
        self.refresh()
        # Realtime pills: cheap presence re-read (port enumeration, printer
        # list, USB class scan — no probing, no config writes) so pulled
        # cables flip the pill within seconds. Skipped while a detection
        # run owns the states; only explicit detection persists anything.
        self._live = QTimer(self)
        self._live.setInterval(5000)
        self._live.timeout.connect(self._live_refresh)
        self._live.start()

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if isinstance(obj, DevicePill) and self._pills.get(obj.spec()["key"]) is obj:
            if ev.type() == QEvent.Type.Enter:
                obj._apply_hover(True)      # noqa: SLF001 - own widget
            elif ev.type() == QEvent.Type.Leave:
                obj._apply_hover(False)     # noqa: SLF001 - own widget
        return super().eventFilter(obj, ev)

    def detect(self, key: str | None) -> None:
        """Start detection for one device (key) or all of them (None).

        No-op when a detection is already running — the shared controller
        serialises it, and every strip refreshes on the single `finished`.
        """
        keys = None if key is None else (key,)
        if not self._ctrl.start(keys):
            self.refresh()      # keep this strip's button state honest
            return
        self.refresh()

    def _on_pill(self, key: str) -> None:
        self.detect(key)

    def _demo_label(self, on: bool) -> None:
        self.btn_demo.setText("Demo ON" if on else "Demo")
        if on:
            # Solid pink so "demo is running" is unmistakable in the strip.
            self.btn_demo.setStyleSheet(
                "QPushButton { background:#BE185D; color:white; "
                "border:1px solid #BE185D; border-radius:8px; padding:0 14px; "
                "font-weight:600; }"
                "QPushButton:hover { background:#9D174D; border-color:#9D174D; }")
        else:
            self.btn_demo.setStyleSheet("")     # back to the outline QSS
        self.btn_demo.setToolTip(
            "Demo mode is ON — the weigh machine and label printer are simulated "
            "(labels go to the print log, not paper). Click to turn it off."
            if on else
            "Turn on demo mode: simulate the weigh machine and label printer "
            "so the app can run with no hardware.")

    def _toggle_demo(self, on: bool) -> None:
        on = bool(on)
        if on:
            ans = QMessageBox.question(
                self, "Turn on demo mode?",
                "Demo mode simulates the weigh machine and label printer.\n\n"
                "Weights are made up and labels are written to the print log "
                "instead of being printed. Turn it off before real production.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            if ans != QMessageBox.StandardButton.Yes:
                self.btn_demo.blockSignals(True)
                self.btn_demo.setChecked(False)
                self.btn_demo.blockSignals(False)
                self._demo_label(False)
                return
        try:
            cfg = HWConfig.load()
            cfg["demo_mode"] = on
            HWConfig.save(cfg)
            HWDevices.reload_hardware(cfg)
        except Exception as e:
            self.btn_demo.blockSignals(True)
            self.btn_demo.setChecked(not on)
            self.btn_demo.blockSignals(False)
            self._demo_label(not on)
            try:
                QMessageBox.warning(self, "Demo mode",
                                    f"Could not change demo mode: {e}")
            except Exception:
                pass
            return
        self._demo_label(on)
        self.refresh()
        self.detected.emit({"demo": on})

    def _on_detected(self, result: dict) -> None:
        self.refresh()
        try:
            result = result or {}
            err = str(result.get("error") or "")
            errs = result.get("errors") or []
            if errs and not err:
                err = "; ".join(str(e) for e in errs[:3])
            if err:
                # Detection finished but something failed: surface it once
                # per strip so "detecting" resolving into silence is never
                # mistaken for success. Pills already show not-found; this
                # explains WHY (cable? spooler? timeout?).
                self.btn_refresh.setToolTip(
                    f"Last detection issue: {err[:220]} — click to retry.")
                try:
                    from factory.ui.toast import toast
                    toast(self, f"Detection issue: {err[:220]}", ok=False)
                except Exception:
                    pass
            else:
                self.btn_refresh.setToolTip(
                    "Detect the factory hardware again.")
        except Exception:
            pass
        self.detected.emit(result)

    def refresh(self) -> None:
        """Re-read device status (after detection, or a scan landed)."""
        for pill, spec in zip(self._pills.values(),
                              HWStatus.station_status(self._station)):
            pill.refresh(spec)
        self.btn_refresh.setEnabled(not self._ctrl.busy())

    def _live_refresh(self) -> None:
        """Poll tick: re-render pills with live presence applied.

        Display only — never probes serial ports, never writes config.
        A skipped tick (detection running, or any error) simply keeps the
        current pills until the next one.
        """
        if self._ctrl.busy():
            return
        try:
            specs = HWStatus.station_live_status(self._station)
        except Exception:
            return
        for pill, spec in zip(self._pills.values(), specs):
            try:
                pill.refresh(spec)
            except Exception:
                pass

    # convenience used by pages that just want to kick off a first detection
    def autodetect_now(self) -> None:
        """Ask for a full detect unless one is already running.

        Deliberately silent when busy: this is called once per strip at
        startup, and the in-flight run will refresh every strip anyway.
        """
        self.detect(None)