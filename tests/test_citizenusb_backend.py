"""Fake-libusb tests for the citizenusb CUPS backend + safe live discovery.

No physical print happens here. Live test only runs backend discovery
(argv==1, sysfs read-only). All print-path tests use a fake usb module.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import io
import os
import subprocess
import sys
import types

import pytest

BACKEND_PATH = os.path.join(os.path.dirname(__file__), "..", "tools", "cups", "citizenusb")


def load_backend():
    loader = importlib.machinery.SourceFileLoader("citizenusb_backend", BACKEND_PATH)
    spec = importlib.util.spec_from_loader("citizenusb_backend", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def make_fake_usb(serials, writes=None, fail_write=False):
    """Build a fake 'usb' package with core/util using official PyUSB call shape."""
    usb = types.ModuleType("usb")
    core = types.ModuleType("usb.core")
    util = types.ModuleType("usb.util")

    class USBError(Exception):
        pass

    core.USBError = USBError

    class FakeDev:
        def __init__(self, serial):
            self.serial_number = serial
            self.claimed = False
            self.configured = False

        def is_kernel_driver_active(self, _iface):
            return False

        def set_configuration(self):
            self.configured = True

        def write(self, ep, data, timeout):
            assert ep == 0x02
            assert timeout == 10000
            if fail_write:
                raise USBError("inject fail")
            writes.append(bytes(data))
            # partial-write simulation: caller loops on return value;
            # report full length (loop correctness covered by chunk test).
            return len(bytes(data))

        def dispose(self):
            pass

    devices = [FakeDev(s) for s in serials]

    def find(find_all=False, idVendor=None, idProduct=None):
        assert find_all is True
        assert idVendor == 0x2730 and idProduct == 0x0FFF
        return list(devices)

    core.find = find

    util.claim_interface = lambda dev, iface: setattr(dev, "claimed", True)
    util.release_interface = lambda dev, iface: setattr(dev, "claimed", False)

    usb.core = core
    usb.util = util
    return usb, FakeDev


def test_uri_validation():
    mod = load_backend()
    ok, serial = mod.validate_uri("citizenusb://2730/0fff?serial=00000000")
    assert ok and serial == "00000000"
    ok, _ = mod.validate_uri("usb://2730/0fff?serial=00000000")
    assert not ok
    ok, _ = mod.validate_uri("citizenusb://9999/0001?serial=x")
    assert not ok


def test_discovery_lines_format():
    mod = load_backend()
    lines = mod.discovery_lines(
        [{"manufacturer": "CITIZEN", "product": "Barcode Printer", "serial": "00000000"}]
    )
    assert len(lines) == 1
    assert lines[0].startswith("direct citizenusb://2730/0fff?serial=00000000 ")
    assert '"CITIZEN Barcode Printer"' in lines[0]


def test_enumerate_fake_sysfs(tmp_path):
    mod = load_backend()
    dev = tmp_path / "3-2"
    dev.mkdir()
    (dev / "idVendor").write_text("2730\n")
    (dev / "idProduct").write_text("0fff\n")
    (dev / "manufacturer").write_text("CITIZEN\n")
    (dev / "product").write_text("Barcode Printer\n")
    (dev / "serial").write_text("00000000\n")
    try:
        # Interface nodes like "3-2:1.0" must be skipped. ':' is not a legal
        # file name on Windows, so this half of the case only exists on POSIX
        # (Windows can never present such an entry to enumerate_sysfs anyway).
        (tmp_path / "3-2:1.0").mkdir()
    except OSError:
        pass
    found = mod.enumerate_sysfs(str(tmp_path))
    assert len(found) == 1 and found[0]["serial"] == "00000000"


def test_print_sends_bytes_unchanged_single_write(monkeypatch, tmp_path):
    mod = load_backend()
    writes: list[bytes] = []
    usb, FakeDev = make_fake_usb(["00000000"], writes)
    monkeypatch.setitem(sys.modules, "usb", usb)
    monkeypatch.setitem(sys.modules, "usb.core", usb.core)
    monkeypatch.setitem(sys.modules, "usb.util", usb.util)
    payload = bytes(range(256)) * 4  # 1024 bytes, one chunk
    assert mod.send_to_printer(payload, "00000000") is True
    assert b"".join(writes) == payload  # unchanged bytes


def test_print_chunks_and_handles_partial(monkeypatch):
    mod = load_backend()
    chunks: list[bytes] = []

    usb = types.ModuleType("usb")
    core = types.ModuleType("usb.core")
    util = types.ModuleType("usb.util")

    class USBError(Exception):
        pass

    core.USBError = USBError

    class PartialDev:
        serial_number = "00000000"

        def is_kernel_driver_active(self, _i):
            return False

        def set_configuration(self):
            pass

        def write(self, ep, data, timeout):
            assert ep == 0x02
            b = bytes(data)
            # simulate partial write: accept half, caller must loop
            half = max(1, len(b) // 2)
            chunks.append(b[:half])
            return half

        def dispose(self):
            pass

    core.find = lambda find_all=False, **kw: [PartialDev()]
    util.claim_interface = lambda d, i: None
    util.release_interface = lambda d, i: None
    usb.core, usb.util = core, util
    monkeypatch.setitem(sys.modules, "usb", usb)
    monkeypatch.setitem(sys.modules, "usb.core", core)
    monkeypatch.setitem(sys.modules, "usb.util", util)
    payload = b"A" * 100
    assert mod.send_to_printer(payload, "00000000") is True
    assert b"".join(chunks) == payload


def test_print_failure_exit_no_retry(monkeypatch, tmp_path):
    mod = load_backend()
    usb, FakeDev = make_fake_usb(["00000000"], [], fail_write=True)
    monkeypatch.setitem(sys.modules, "usb", usb)
    monkeypatch.setitem(sys.modules, "usb.core", usb.core)
    monkeypatch.setitem(sys.modules, "usb.util", usb.util)
    f = tmp_path / "job.bin"
    f.write_bytes(b"hello")
    argv = ["citizenusb", "1", "user", "t", "1", "", str(f)]
    rc = mod.run_job(argv, {"DEVICE_URI": "citizenusb://2730/0fff?serial=00000000"})
    assert rc == 1  # CUPS_BACKEND_FAILED, never 6/7 retry (no duplicate labels)


def test_print_refuses_wrong_uri(tmp_path):
    mod = load_backend()
    f = tmp_path / "job.bin"
    f.write_bytes(b"hello")
    argv = ["citizenusb", "1", "user", "t", "1", "", str(f)]
    assert mod.run_job(argv, {"DEVICE_URI": "usb://2730/0fff"}) == 1


def test_live_discovery_safe_readonly():
    """Runs the real backend with no args: sysfs read-only, no print.

    Environmental-only skip: when no Citizen USB unit is present in sysfs
    there is nothing to discover, so the test skips instead of failing.
    Driver behavior and fake-output tests above are never relaxed."""
    mod = load_backend()
    try:
        present = mod.enumerate_sysfs()
    except Exception:
        present = []
    if not present:
        pytest.skip("no Citizen USB device in sysfs (environmental, no hardware)")
    r = subprocess.run(
        ["python3", BACKEND_PATH], capture_output=True, text=True, timeout=15
    )
    assert r.returncode == 0
    assert "citizenusb://2730/0fff?serial=" in r.stdout
    assert "CITIZEN Barcode Printer" in r.stdout
