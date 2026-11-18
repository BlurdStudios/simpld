"""Scale watchdog: self-healing serial reads + quiet reconnect.

The scale can die without the app restarting (indicator power-cycle, USB-serial
adapter reseat). These tests pin the recovery path: the driver drops dead
handles, the watchdog probes only when warranted, and a missed probe never
forgets a configured port.
"""
import json
import os
import sys

import pytest
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory.hardware import autodetect as Auto
from factory.hardware import config as HWC
from factory.hardware import devices as DEV
from factory.hardware import mock as HW
from factory.hardware import status as HWStatus
from factory.hardware.essae import EssaeDS215


@pytest.fixture
def keep_hw():
    """Shared drivers/config are process-wide; restore them per test."""
    old = (HW.scale, HW.printer, DEV.init_error)
    yield
    HW.scale, HW.printer, DEV.init_error = old
    DEV.reset_scale_reconnect_state()


def _isolated_config(tmp_path, monkeypatch, port="COM7", baud=9600):
    path = tmp_path / "hardware.json"
    path.write_text(json.dumps({"scale_port": port, "scale_baud": baud,
                                "printer_port": ""}))
    monkeypatch.setenv("SIMPLD_HW_JSON", str(path))
    return path


class _FakeSerial:
    def __init__(self, payload=b"", fail=False):
        self.in_waiting = 1
        self._payload = payload
        self._fail = fail
        self._sent = False
        self.closed = False

    def reset_input_buffer(self):
        pass

    def read(self, _n=1):
        if self._fail:
            raise OSError("device disconnected")
        if self._sent:
            return b""
        self._sent = True
        return self._payload

    def close(self):
        self.closed = True


def test_dead_serial_handle_is_replaced_without_restart():
    dead = _FakeSerial(fail=True)
    good = _FakeSerial(payload=b"ST,GS,+ 12.340kg\r\n")
    d = EssaeDS215("COM7")

    with _mock.patch("serial.Serial", side_effect=[dead, good]):
        assert d.read_weight() == 12.34

    assert dead.closed, "dead handle must be closed, not cached"
    assert d._ser is good, "retry must rebuild the serial object"
    assert not good.closed
    assert d.fail_streak == 0
    assert d.last_ok > 0


def test_read_failures_are_counted_then_reset_on_success():
    d = EssaeDS215("COM9")

    def boom(dwell=0.8):
        raise RuntimeError("scale gone")

    d._read_lines = boom
    for _ in range(3):
        with pytest.raises(RuntimeError):
            d.read_weight()
    assert d.fail_streak == 3

    d._read_lines = lambda dwell=0.8: ["  5.000 kg"]
    assert d.read_weight() == 5.0
    assert d.fail_streak == 0


def test_reconnect_miss_keeps_saved_port_and_live_driver(
        tmp_path, monkeypatch, keep_hw):
    path = _isolated_config(tmp_path, monkeypatch)

    class Silent(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return []

    with _mock.patch("factory.hardware.essae.list_ports",
                     return_value=["COM7"]), \
         _mock.patch.object(EssaeDS215, "_read_lines", Silent._read_lines), \
         _mock.patch.object(Auto._devices, "reload_hardware") as reload:
        result = Auto.reconnect_scale(save=True)

    assert result["scale"] is None
    assert result["changed"] is False
    assert result["inconclusive"] is False
    assert json.loads(path.read_text())["scale_port"] == "COM7"
    reload.assert_not_called(), "a miss must not rebuild or forget the scale"


def test_reconnect_hit_reloads_even_when_port_is_unchanged(
        tmp_path, monkeypatch, keep_hw):
    path = _isolated_config(tmp_path, monkeypatch)

    class Answering(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return ["ST,GS,+   2.500kg"] if self.port == "COM7" else []

    with _mock.patch("factory.hardware.essae.list_ports",
                     return_value=["COM7"]), \
         _mock.patch.object(EssaeDS215, "_read_lines", Answering._read_lines), \
         _mock.patch.object(Auto._devices, "reload_hardware",
                            wraps=DEV.reload_hardware) as reload:
        result = Auto.reconnect_scale(save=True)

    assert result["scale"]["port"] == "COM7"
    assert result["changed"] is False
    reload.assert_called_once(), "same-port hit still needs a fresh driver"
    assert isinstance(HW.scale, EssaeDS215)
    assert json.loads(path.read_text())["scale_port"] == "COM7"


def test_watchdog_gate_is_cooldowned_and_only_for_sick_scale():
    cfg = {"scale_port": "COM7", "demo_mode": False}
    common = (
        _mock.patch.object(DEV, "init_error", None),
        _mock.patch.object(DEV, "demo_mode", return_value=False),
        _mock.patch.object(DEV, "is_mock_scale", return_value=False),
        _mock.patch.object(DEV, "scale_fail_streak", return_value=0),
        _mock.patch("factory.hardware.status.is_detecting",
                    return_value=False),
    )

    DEV.reset_scale_reconnect_state()
    with _exit_stack(common) as stack:
        stack.enter_context(_mock.patch(
            "factory.hardware.status.live_device_status",
            return_value={"state": "setup"}))
        assert DEV.scale_reconnect_due(cfg)
        DEV.mark_scale_reconnect_attempt()
        assert not DEV.scale_reconnect_due(cfg)

    DEV.reset_scale_reconnect_state()
    with _exit_stack(common) as stack:
        stack.enter_context(_mock.patch.object(
            DEV, "scale_fail_streak", return_value=DEV.FAIL_STREAK_TRIGGER))
        stack.enter_context(_mock.patch(
            "factory.hardware.status.live_device_status",
            return_value={"state": "ready"}))
        assert DEV.scale_reconnect_due(cfg)

    DEV.reset_scale_reconnect_state()
    with _exit_stack(common) as stack:
        stack.enter_context(_mock.patch.object(
            DEV, "demo_mode", return_value=True))
        assert not DEV.scale_reconnect_due(cfg)


def _exit_stack(patches):
    from contextlib import ExitStack
    stack = ExitStack()
    for p in patches:
        stack.enter_context(p)
    return stack


def test_strip_live_tick_queues_quiet_reconnect():
    from PySide6.QtWidgets import QApplication
    from factory.ui import device_strip as DS

    QApplication.instance() or QApplication([])
    DS.reset_shared_controller()
    strip = DS.DeviceStrip("packaging")
    try:
        with _mock.patch.object(DS.HWDevices, "scale_reconnect_due",
                                return_value=True), \
             _mock.patch.object(DS.HWDevices,
                                "mark_scale_reconnect_attempt") as mark, \
             _mock.patch.object(DS.HWStatus, "station_live_status",
                                return_value=[]), \
             _mock.patch.object(strip._ctrl, "start", return_value=True) as start:
            strip._live_refresh()
        mark.assert_called_once_with()
        start.assert_called_once_with(("scale_reconnect",))
    finally:
        strip.close()
        DS.reset_shared_controller()


def test_quiet_worker_has_no_detecting_theater():
    from PySide6.QtWidgets import QApplication
    from factory.ui.device_strip import _DetectWorker

    QApplication.instance() or QApplication([])
    HWStatus.set_detecting(("scale_reconnect",))
    assert not HWStatus.is_detecting(), "watchdog must not show 'checking ports'"
    HWStatus.clear_detecting()

    seen = []
    worker = _DetectWorker(("scale_reconnect",))
    worker.done.connect(seen.append)
    with _mock.patch.object(Auto, "reconnect_scale",
                            return_value={"scale": None}) as reconnect:
        worker.run()
    reconnect.assert_called_once_with(save=True)
    assert seen and seen[0]["scale"] is None
