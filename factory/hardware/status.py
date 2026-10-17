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
_last_scan: float | None = None
_scan_count: int = 0


def note_scan(when: float | None = None) -> None:
    """Record a successful scan (call from the Dispatch page on a landed scan)."""
    global _last_scan, _scan_count
    _last_scan = time.time() if when is None else float(when)
    _scan_count += 1


def scanner_activity() -> tuple[int, float | None]:
    """(scans this session, last scan epoch or None)."""
    return _scan_count, _last_scan


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
    if count <= 0:
        return "idle", "wedge · no scan yet"
    return "active", f"{count} scan{'' if count == 1 else 's'} · {_hhmmss(last)[3:]}"


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
        # rather than crowding the pill.
        out["hint"] = "Click to detect again."
    elif state == "demo":
        out["hint"] = "Demo mode is on — this device is simulated."
    return out


def station_status(station: str, cfg: dict | None = None) -> list[dict]:
    """Status of every device at one station, in registry order."""
    if station not in STATIONS:
        raise KeyError(f"unknown station '{station}'")
    return [device_status(d, cfg) for d in STATIONS[station][1]]


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