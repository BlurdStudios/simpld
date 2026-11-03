"""Device registry + live status for the three physical devices.

Simpl'd runs on two factory stations and three pieces of hardware:

  Packaging station   weigh machine (Essae DS-215, RS232)
                      label printer (Citizen CL-S621II, installed driver)
  Dispatch station    QR scanner (USB keyboard wedge)

Only the first two are *configurable*: the scanner is a keyboard wedge, so the
OS already types into whatever box has focus and there is no port/driver to
pick. It still earns a place in this registry — the dispatcher needs to know
at a glance whether the wedge is actually reaching the app, and the only honest
evidence of that is a scan that landed.

States
------
  ready    auto-detected and healthy
  setup    not found yet — click the pill (or Refresh) to detect again
  error    configured but the driver failed to build (DEV.init_error)
  detecting a probe is running right now
  idle     needs no config and has not been used this session
  active   needs no config and was used this session
  demo     the explicit demo switch is on — simulated, not real
"""
from __future__ import annotations

import os
import sys
import time

from . import config as _config
from . import devices as _devices

SCALE = "scale"
PRINTER = "printer"
SCANNER = "scanner"

DEVICE_SPECS: dict[str, dict] = {
    SCALE: {
        "label": "Weigh machine",
        "model": "Essae DS-215",
        "station": "packaging",
        "icon": "scale",
        "configurable": True,
    },
    PRINTER: {
        "label": "Label printer",
        "model": "Citizen CL-S621II",
        "station": "packaging",
        "icon": "printer",
        "configurable": True,
    },
    SCANNER: {
        "label": "QR scanner",
        "model": "USB keyboard wedge",
        "station": "dispatch",
        "icon": "scan",
        "configurable": False,
    },
}

STATIONS: dict[str, tuple[str, list[str]]] = {
    "packaging": ("Packaging station", [SCALE, PRINTER]),
    "dispatch": ("Dispatch station", [SCANNER]),
}

STATION_OF = {dev: st for st, (_, devs) in STATIONS.items() for dev in devs}

# ---- scanner session activity --------------------------------------------
# When the last barcode/QR landed in the app. Session-scoped on purpose: it
# answers "is the wedge reaching me right now?", not "has it ever worked".
# Attempts (Enter pressed in the scan box with text in it) are tracked
# separately from landed scans: input reaching the box proves keystrokes
# arrive, while zero landed with attempts > 0 means the scans are failing
# validation (wrong box / order not loading) — a different problem from a
# dead wedge, and the pill tells them apart.
_last_scan: float | None = None
_scan_count: int = 0
_scan_attempts: int = 0


def note_scan(when: float | None = None) -> None:
    """Record a successful scan (call from the Dispatch page on a landed scan)."""
    global _last_scan, _scan_count
    _last_scan = time.time() if when is None else float(when)
    _scan_count += 1


def note_scan_attempt() -> None:
    """Record scan-box input submitted (Enter with text), landed or not."""
    global _scan_attempts
    _scan_attempts += 1


def reset_scanner_session() -> None:
    """Forget this session's scan activity (call on logout).

    The pill answers "is the wedge reaching me right now" — a new login
    is a new session, so stale counts must not leak across it.
    """
    global _last_scan, _scan_count, _scan_attempts
    _last_scan = None
    _scan_count = 0
    _scan_attempts = 0


def scanner_activity() -> tuple[int, float | None]:
    """(scans this session, last scan epoch or None)."""
    return _scan_count, _last_scan


def scanner_attempts() -> int:
    """Scan-box submissions this session, landed or not."""
    return _scan_attempts


def scanner_input_without_landing() -> bool:
    """True when input reaches the app but no scan has landed yet."""
    return _scan_attempts > 0 and _scan_count <= 0


def _hhmmss(epoch: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(epoch))


def _detail_scale(cfg: dict) -> tuple[str, str]:
    """Short pill text. Kept tight so pills never crowd each other."""
    if _devices.demo_mode(cfg):
        return "demo", "simulated"
    port = str(cfg.get("scale_port") or "").strip()
    if not port:
        return "setup", "not found"
    if _devices.is_mock_scale():
        return "setup", f"{port} (inactive)"
    return "ready", f"{port} · {cfg.get('scale_baud', 9600)}"


def _detail_printer(cfg: dict) -> tuple[str, str]:
    if _devices.demo_mode(cfg):
        return "demo", "simulated"
    name = str(cfg.get("printer_name") or "").strip()
    if not name:
        return "setup", "not found"
    if _devices.is_mock_printer():
        return "setup", f"{name} (inactive)"
    return "ready", name


def _detail_scanner() -> tuple[str, str]:
    count, last = scanner_activity()
    if count > 0:
        return "active", f"{count} scan{'' if count == 1 else 's'} · {_hhmmss(last)[3:]}"
    if _scan_attempts > 0:
        # Keystrokes reach the box but nothing validated: the wedge (or the
        # operator) is typing, yet no scan landed — usually wrong box, order
        # not loading, or a wedge missing its Enter suffix (which routes to
        # the same failure: text submitted but rejected).
        return ("idle", f"input seen · {_scan_attempts} attempt"
                f"{'' if _scan_attempts == 1 else 's'} · none landed")
    return "idle", "wedge · no scan yet"


# ---- detection-in-progress -----------------------------------------------
# The UI runs autodetect on a worker thread; the pills for the devices being
# probed show "detecting" until it finishes. Tracks WHICH keys, so clicking
# the weigh-machine pill doesn't flash the printer into 'detecting' too.
_detecting: set[str] = set()


def set_detecting(keys) -> None:
    """keys: None for everything, or an iterable of device keys."""
    global _detecting
    if keys is None:
        _detecting = {SCALE, PRINTER}
    else:
        _detecting = {str(k) for k in keys}


def clear_detecting() -> None:
    set_detecting(())


def is_detecting() -> bool:
    return bool(_detecting)


def _detected_override(key: str) -> dict | None:
    """While a probe is running, show that rather than a stale verdict."""
    if key not in _detecting:
        return None
    return {"state": "detecting",
            "detail": "checking ports…" if key == SCALE else "checking printers…"}


def device_status(key: str, cfg: dict | None = None) -> dict:
    """Full status for one device: spec + resolved state + human detail."""
    spec = DEVICE_SPECS.get(key)
    if spec is None:
        raise KeyError(f"unknown device '{key}'")
    cfg = cfg if cfg is not None else _config.load()

    override = _detected_override(key)
    if override is not None:
        return {"key": key, **override, **spec}

    if key == SCALE:
        state, detail = _detail_scale(cfg)
    elif key == PRINTER:
        state, detail = _detail_printer(cfg)
    else:
        state, detail = _detail_scanner()

    # A configured-but-broken driver is never reported as merely unconfigured.
    err = _devices.init_error or ""
    if state != "ready" and key in err:
        state = "error"

    out = {"key": key, "state": state, "detail": detail, **spec}
    if state == "setup":
        # The one thing an operator can do next, spelled out in the tooltip
        # rather than crowding the pill. For the printer, add what the OS
        # actually reports so "not found" is diagnosable on the floor.
        hint = "Click to detect again."
        if key == PRINTER:
            seen = _printer_seen_hint()
            if seen:
                hint += f" {seen}"
        out["hint"] = hint
    elif state == "demo":
        out["hint"] = "Demo mode is on — this device is simulated."
    elif key == SCANNER and scanner_input_without_landing():
        out["hint"] = ("Input reaches the app but no scan landed — read the "
                       "error line (wrong box? order not loading? wedge "
                       "missing its Enter suffix?).")
    return out


def station_status(station: str, cfg: dict | None = None) -> list[dict]:
    """Status of every device at one station, in registry order."""
    if station not in STATIONS:
        raise KeyError(f"unknown station '{station}'")
    return [device_status(d, cfg) for d in STATIONS[station][1]]


# ---- live presence (realtime pills) --------------------------------------
# Why this exists: every other layer looks at NAMES, not cables. An
# unplugged USB printer leaves its CUPS/Windows queue behind, and a pulled
# serial adapter is just a stale string in hardware.json — so pills built
# from stored config alone report "ready" for missing hardware. These
# helpers answer "is it attached right now?" with cheap read-only checks
# (no port opens, no probing, no config writes). The strip polls them;
# only explicit detection ever persists anything.
_SYSFS_USB_ROOT = "/sys/bus/usb/devices"


def _sysfs_entries_have_printer(names, read_class) -> bool:
    """Pure scan core: interface nodes (name contains ':') whose class is 07.

    Split out so tests never touch the filesystem — a ':' cannot even be
    created as a directory name on Windows.
    """
    for name in names:
        if ":" not in name:
            continue  # device nodes, not interfaces
        try:
            if (read_class(name) or "").strip() == "07":
                return True
        except OSError:
            continue
    return False


def _sysfs_usb_printer_present(root: str = _SYSFS_USB_ROOT) -> bool:
    """True when any USB interface of printer class (07) is enumerated."""
    try:
        names = os.listdir(root)
    except OSError:
        return False

    def _read(name: str) -> str:
        with open(os.path.join(root, name, "bInterfaceClass"),
                  "r", errors="replace") as f:
            return f.read()

    return _sysfs_entries_have_printer(names, _read)


def _windows_usbprint_present() -> bool | None:
    """True when a USBPRINT device is enumerated (ctypes, stdlib only).

    Returns None when the query itself cannot run (non-Windows, missing
    setupapi, call failure) — callers must keep the last verdict instead
    of flapping. Only a successful enumeration that finds zero USBPRINT
    devices returns False, and even then callers treat it as advisory:
    network/shared queues have no USBPRINT device on this PC.
    """
    try:
        import ctypes
        setupapi = ctypes.windll.setupapi
        DIGCF_PRESENT = 0x00000002
        DIGCF_ALLCLASSES = 0x00000004
        setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p
        setupapi.SetupDiGetClassDevsW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_ulong]
        setupapi.SetupDiEnumDeviceInfo.restype = ctypes.c_int
        setupapi.SetupDiEnumDeviceInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
        setupapi.SetupDiDestroyDeviceInfoList.restype = ctypes.c_int
        setupapi.SetupDiDestroyDeviceInfoList.argtypes = [ctypes.c_void_p]

        class _DevInfo(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_ulong),
                        ("ClassGuid", ctypes.c_byte * 16),
                        ("DevInst", ctypes.c_ulong),
                        ("Reserved", ctypes.c_size_t)]

        handle = setupapi.SetupDiGetClassDevsW(
            None, "USBPRINT", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
        if handle is None or handle == ctypes.c_void_p(-1).value:
            return None
        try:
            info = _DevInfo()
            info.cbSize = ctypes.sizeof(_DevInfo)
            return bool(setupapi.SetupDiEnumDeviceInfo(
                handle, 0, ctypes.byref(info)))
        finally:
            setupapi.SetupDiDestroyDeviceInfoList(handle)
    except Exception:
        return None


def usb_printer_present() -> bool | None:
    """Any USB printer attached right now? None when unknowable.

    Linux reads sysfs (no root, no deps); Windows enumerates USBPRINT.
    Anything else — or any failure — returns None and callers keep the
    last verdict instead of flapping the pill.
    """
    try:
        if sys.platform.startswith("linux"):
            return _sysfs_usb_printer_present()
        if os.name == "nt":
            return _windows_usbprint_present()
    except Exception:
        return None
    return None


def scale_port_present(port: str) -> bool | None:
    """Configured scale port still enumerated? None when unknowable.

    pyserial enumeration only — the port is never opened. An empty but
    successful enumeration means the port is gone (False), not unknown.
    """
    try:
        from .essae import list_ports
        return str(port or "").strip() in (list_ports() or [])
    except Exception:
        return None


def _printer_seen_hint() -> str | None:
    """Best-effort 'Seen N: a, b' summary for printer tooltips. None when
    the printer list cannot be read (no QApplication yet, Qt missing, …).

    Display-only: never raises, never blocks. Keeps the operator from
    guessing why detection says "not found".
    """
    try:
        from PySide6.QtWidgets import QApplication
        if QApplication.instance() is None:
            return None
        from .qtprint import available_printers
        names = available_printers()
    except Exception:
        return None
    if not names:
        return "Seen 0 printers — check the driver install and spooler."
    shown = ", ".join(names[:4])
    if len(names) > 4:
        shown += f" +{len(names) - 4} more"
    if len(shown) > 90:
        shown = shown[:87] + "…"
    return f"Seen {len(names)}: {shown}."


def live_device_status(key: str, cfg: dict | None = None) -> dict:
    """device_status() with cheap live presence applied (display only).

    A configured-but-vanished device reads setup/"not found" so the pills
    track cables within one poll tick. Never probes, never writes config.

    Printer rule: the installed queue list is authoritative. A configured
    queue that is still enumerated stays `ready` — the USB class scan is
    advisory only (network/shared queues have no USBPRINT device on this
    PC, and must not flip a working printer to "not found"). An actually
    unplugged USB printer fails explicitly at print time with the queue
    name in the error. A missing queue still reads setup, with the seen
    queue names in the hint.
    """
    if cfg is None:
        cfg = _config.load()
    spec = device_status(key, cfg)
    if spec.get("state") != "ready":
        return spec
    try:
        if key == SCALE:
            if scale_port_present(str(cfg.get("scale_port") or "")) is False:
                return {**spec, "state": "setup", "detail": "not found",
                        "hint": "Click to detect again."}
        elif key == PRINTER:
            from .qtprint import available_printers
            name = str(cfg.get("printer_name") or "")
            seen = available_printers()
            if name not in seen:
                hint = "Click to detect again."
                shown = ", ".join(seen[:4])
                if len(seen) > 4:
                    shown += f" +{len(seen) - 4} more"
                if shown:
                    if len(shown) > 90:
                        shown = shown[:87] + "…"
                    hint += f" Seen {len(seen)}: {shown}."
                else:
                    hint += " Seen 0 printers."
                return {**spec, "state": "setup", "detail": "not found",
                        "hint": hint}
            if usb_printer_present() is False:
                # Queue present, no USB printer class on this PC. Most
                # likely a network/shared queue — stay ready, note it.
                return {**spec,
                        "hint": "Queue seen · no USB printer class on this PC "
                                "(normal for network/shared queues). "
                                "Check the cable if printing fails."}
    except Exception:
        pass
    return spec


def station_live_status(station: str, cfg: dict | None = None) -> list[dict]:
    """station_status() with live presence applied (display only)."""
    if station not in STATIONS:
        raise KeyError(f"unknown station '{station}'")
    if cfg is None:
        cfg = _config.load()
    return [live_device_status(d, cfg) for d in STATIONS[station][1]]


def all_status(cfg: dict | None = None) -> list[dict]:
    """All three devices, station by station."""
    out: list[dict] = []
    for station in STATIONS:
        out.extend(station_status(station, cfg))
    return out


def needs_setup(station: str, cfg: dict | None = None) -> bool:
    """True when any configurable device at this station is not usable.

    Drives the "open Devices" affordance on the page strip.
    """
    return any(d["state"] in ("setup", "error")
               for d in station_status(station, cfg)
               if d["configurable"])


def summary_line(station: str, cfg: dict | None = None) -> str:
    """One-line mode text, e.g. 'Weigh machine: setup · Label printer: ready'."""
    return " · ".join(f"{d['label']}: {d['state']}"
                      for d in station_status(station, cfg))