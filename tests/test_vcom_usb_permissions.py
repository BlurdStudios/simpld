"""Raw-USB print failures must name the real cause.

On Linux the Citizen presents a vendor bulk interface and the app writes
straight to it, so a usbfs permission problem (node is root:lp, user is
not in that group) surfaces as libusb's `[Errno 13] Access denied`. That
is NOT a broken print queue — CUPS keeps printing because its backend
runs as root. These tests pin the operator-facing messages, the writable-
node pre-check, and the release/re-attach cleanup. Fake libusb only — no
bus scan, no print, no hardware.
"""
import os
import sys
import types
import pytest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory.hardware import vcom as V


class USBError(Exception):
    """PyUSB's shape: errno=13/16 and backend_error_code=-3."""

    def __init__(self, msg, errno=None, backend_error_code=None):
        super().__init__(msg)
        self.errno = errno
        self.backend_error_code = backend_error_code


class FakeDev:
    bus = 1
    address = 4

    def __init__(self):
        self.calls = []

    def is_kernel_driver_active(self, iface):
        self.calls.append(("is_kernel_driver_active", iface))
        return False

    def detach_kernel_driver(self, iface):
        self.calls.append(("detach_kernel_driver", iface))

    def attach_kernel_driver(self, iface):
        self.calls.append(("attach_kernel_driver", iface))

    def set_configuration(self):
        self.calls.append(("set_configuration",))

    def write(self, ep, data, timeout):
        self.calls.append(("write", ep, len(data), timeout))
        return len(data)

    def dispose(self):
        pass


def _fake_usb(dev, claim_error=None):
    """Patch sys.modules with a minimal official-PyUSB-shaped 'usb'."""
    usb = types.ModuleType("usb")
    core = types.ModuleType("usb.core")
    util = types.ModuleType("usb.util")
    calls = []

    def find(**kw):
        assert kw == {"idVendor": V.CITIZEN_VID, "idProduct": V.CITIZEN_PID}
        return dev

    def claim_interface(d, iface):
        calls.append(("claim_interface", iface))
        if claim_error is not None:
            raise claim_error

    def release_interface(d, iface):
        calls.append(("release_interface", iface))

    def dispose_resources(d):
        calls.append(("dispose_resources",))

    core.find = find
    core.USBError = USBError
    util.claim_interface = claim_interface
    util.release_interface = release_interface
    util.dispose_resources = dispose_resources
    usb.core, usb.util = core, util
    return mock.patch.dict(sys.modules, {"usb": usb, "usb.core": core,
                                         "usb.util": util}), calls


def _no_node_check():
    """Skip the node pre-check so libusb's own error is exercised."""
    return mock.patch.object(V, "_usb_node_path", return_value="")


def test_access_denied_names_lp_group_not_the_queue():
    dev = FakeDev()
    err = USBError("Access denied (insufficient permissions)",
                   errno=13, backend_error_code=-3)
    patcher, _ = _fake_usb(dev, claim_error=err)
    with patcher, _no_node_check():
        try:
            V.citizen_usb_send(b"ZPL")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("a denied claim must raise")
    assert "lp" in msg and "usermod -aG lp" in msg, msg
    assert "print queue" not in msg.casefold(), (
        f"permissions must not be blamed on a queue: {msg!r}")


def test_libusb_access_code_without_errno_is_still_permission():
    """PyUSB can expose LIBUSB_ERROR_ACCESS (-3) without an errno."""
    dev = FakeDev()
    err = USBError("Access denied", backend_error_code=-3)
    patcher, _ = _fake_usb(dev, claim_error=err)
    with patcher, _no_node_check():
        try:
            V.citizen_usb_send(b"ZPL")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("a denied claim must raise")
    assert "lp" in msg and "usermod -aG lp" in msg, msg


def test_busy_interface_names_usblp_and_draining():
    dev = FakeDev()
    err = USBError("Resource busy", errno=16)
    patcher, _ = _fake_usb(dev, claim_error=err)
    with patcher, _no_node_check():
        try:
            V.citizen_usb_send(b"ZPL")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("a busy interface must raise")
    assert "busy" in msg and "usblp" in msg and "lpstat -o" in msg, msg


@pytest.mark.skipif(sys.platform == "win32",
                   reason="usbfs node pre-check is Linux-only (no geteuid/os node on Windows)")
def test_unwritable_node_fails_before_claim():
    """The pre-check gives the lp-group fix without touching libusb."""
    dev = FakeDev()
    patcher, calls = _fake_usb(dev)
    with patcher, \
            mock.patch.object(V, "_usb_node_path",
                              return_value="/dev/bus/usb/001/004"), \
            mock.patch.object(V.os, "geteuid", return_value=1000), \
            mock.patch.object(V.os.path, "exists", return_value=True), \
            mock.patch.object(V.os, "access", return_value=False):
        try:
            V.citizen_usb_send(b"ZPL")
        except RuntimeError as e:
            msg = str(e)
        else:
            raise AssertionError("an unwritable node must raise")
    assert "/dev/bus/usb/001/004" in msg and "usermod -aG lp" in msg, msg
    assert not [c for c in calls if c[0] == "claim_interface"], calls


def test_claim_success_releases_interface_and_reattaches_driver():
    dev = FakeDev()

    def _active(iface):
        dev.calls.append(("is_kernel_driver_active", iface))
        return True

    dev.is_kernel_driver_active = _active
    patcher, calls = _fake_usb(dev)
    with patcher, _no_node_check():
        V.citizen_usb_send(b"ZPL" * 10)
    assert dev.calls == [
        ("is_kernel_driver_active", 0),
        ("detach_kernel_driver", 0),
        ("set_configuration",),
        ("write", V.USB_EP_OUT, 30, V.USB_WRITE_TIMEOUT_MS),
        ("attach_kernel_driver", 0),
    ], dev.calls
    assert [c[0] for c in calls] == ["claim_interface", "release_interface",
                                     "dispose_resources"], calls


def test_large_payload_is_chunked():
    dev = FakeDev()
    patcher, _ = _fake_usb(dev)
    payload = b"x" * (V.USB_CHUNK * 2 + 1)
    with patcher, _no_node_check():
        V.citizen_usb_send(payload)
    writes = [c for c in dev.calls if c[0] == "write"]
    assert [w[2] for w in writes] == [V.USB_CHUNK, V.USB_CHUNK, 1], writes


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"{name} PASS")
