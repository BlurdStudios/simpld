"""Device registry + live status for the three physical devices.

Simpl'd runs on two factory stations and three pieces of hardware:

  Packaging station   weigh machine (Essae DS-215, RS232)
                      label printer (Citizen CL-S621II, VCOM serial —
                      direct USB-to-serial, no OS printer involved)
  Dispatch station    QR scanner (USB keyboard wedge)

Only the weigh machine is auto-detected (serial probe). The printer is
config-driven: its COM port is set once in Settings → Devices and trusted —
the owner removed the detection logic because a VCOM printer is never
"online" in any OS-printer sense. The scanner is a keyboard wedge: no
port/driver to pick. It still earns a place in this registry — the
dispatcher needs to know at a glance whether the wedge is actually reaching
the app, and the only honest evidence of that is a scan that landed.

States
------
  ready    configured and trusted (printer) / auto-detected (scale)
  setup    not configured — click the pill (or Refresh); printer points
           at Settings, scale re-probes
  error    configured but the driver failed to build (DEV.init_error)
  detecting a probe is running right now (scale only)
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


def _cached_port_objects() -> list | None:
    """Enumerated serial port objects (pyserial), shared 10s cache with the
    scale's port check (the 5s strip poll must never block the UI).
    None when enumeration is unavailable."""
    if _cache_fresh(_PORTS_CACHE.get("at", 0.0), _PORTS_TTL_S) \
            and "objs" in _PORTS_CACHE:
        return list(_PORTS_CACHE["objs"])
    try:
        from serial.tools import list_ports as _lp
        objs = list(_lp.comports())
    except Exception:
        return None
    _PORTS_CACHE["objs"] = objs
    _PORTS_CACHE["ports"] = [p.device for p in objs]
    _PORTS_CACHE["ok"] = True
    _PORTS_CACHE["at"] = time.monotonic()
    return list(objs)


def _detail_printer(cfg: dict) -> tuple[str, str]:
    """Calm, config-first pill. No detection UI exists for the printer
    (owner request): a set hardware.json port is trusted; otherwise the
    Citizen is found quietly by USB ID from the shared port cache, and
    failing that, by raw-USB presence (Linux — the printer never creates
    a serial node there). When nothing resolves, the pill says so without
    nagging — it never flips ready→setup on a poll tick, and a real
    failure surfaces at print time with the transport named in the error."""
    if _devices.demo_mode(cfg):
        return "demo", "simulated"
    port = str(cfg.get("printer_port") or "").strip()
    if not port:
        objs = _cached_port_objects()
        if objs:
            try:
                from .vcom import match_citizen_port
                port = match_citizen_port(objs) or ""
            except Exception:
                port = ""
    if not port:
        try:
            from .vcom import citizen_usb_present
            if citizen_usb_present():  # own 10s cache — no bus scan per tick
                return "ready", "USB · VCOM"
        except Exception:
            pass
        return "setup", "printer not connected"
    if _devices.is_mock_printer():
        return "setup", f"{port} (inactive)"
    return "ready", f"{port} · VCOM"


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
# The UI runs autodetect on a worker thread; the pill for the device being
# probed shows "detecting" until it finishes. Only the weigh machine is
# probed — the printer is config-driven (a VCOM COM port), so it never
# enters the detecting state.
_detecting: set[str] = set()


def set_detecting(keys) -> None:
    """keys: None for everything, or an iterable of device keys.

    None means "everything detectable", which is the weigh machine only.
    """
    global _detecting
    if keys is None:
        _detecting = {SCALE}
    else:
        _detecting = {str(k) for k in keys if k == SCALE}


def clear_detecting() -> None:
    set_detecting(())


def is_detecting() -> bool:
    return bool(_detecting)


def _detected_override(key: str) -> dict | None:
    """While a probe is running, show that rather than a stale verdict."""
    if key not in _detecting:
        return None
    return {"state": "detecting",
            "detail": "checking ports…"}


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
        # rather than crowding the pill. The printer has no setup UI (owner
        # request) — it is found automatically once its USB cable is in.
        out["hint"] = ("Connect the label printer — it is found automatically."
                       if key == PRINTER else "Click to detect again.")
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
# Why this exists: the configured scale port is just a string in
# hardware.json — a pulled serial adapter leaves it behind, and a pill built
# from stored config alone would report "ready" for missing hardware. This
# helper answers "is that COM port still there right now?" with a cheap
# read-only check (no port opens, no probing, no config writes). The strip
# polls it; only explicit detection ever persists anything.
#
# The printer is deliberately NOT live-checked: it is a direct USB-to-serial
# (VCOM) device, never an OS printer, and the owner asked for its detection
# logic removed. A configured port stays "ready" until a print fails on it
# with the port name in the error.
_PORTS_CACHE: dict = {"at": 0.0, "ports": None, "ok": False}
_PORTS_TTL_S = 10.0


def _cache_fresh(entry_at: float, ttl: float) -> bool:
    try:
        return (time.monotonic() - float(entry_at)) < float(ttl)
    except Exception:
        return False


def cached_scale_port_present(port: str) -> bool | None:
    """Cached scale_port_present(). None when unknowable."""
    want = str(port or "").strip()
    if not want:
        return None
    # Tests patch essae.list_ports per-case: bypass the cache when mocked.
    try:
        from . import essae as _essae
        from . import autodetect as _auto
        if _auto._is_mock_fn(_essae.list_ports):
            try:
                return want in (list(_essae.list_ports() or []))
            except Exception:
                return None
    except Exception:
        pass
    if _cache_fresh(_PORTS_CACHE.get("at", 0.0), _PORTS_TTL_S):
        ports = _PORTS_CACHE.get("ports")
        if ports is None and not _PORTS_CACHE.get("ok"):
            return None
        try:
            return want in (ports or [])
        except Exception:
            return None
    try:
        from . import autodetect as _auto
        ports, ok = _auto.list_ports_status()
    except Exception:
        return None
    if not ok:
        return None  # do not cache failures; retry next tick
    _PORTS_CACHE["ports"] = list(ports)
    _PORTS_CACHE["ok"] = True
    _PORTS_CACHE["at"] = time.monotonic()
    try:
        return want in ports
    except Exception:
        return None


def clear_live_caches() -> None:
    """Test aid: force the next live tick to re-enumerate."""
    try:
        _PORTS_CACHE["at"] = 0.0
    except Exception:
        pass





def live_device_status(key: str, cfg: dict | None = None) -> dict:
    """device_status() with cheap live presence applied (display only).

    A configured-but-vanished scale reads setup/"not found" so the pill
    tracks the cable within one poll tick. Never probes, never writes
    config.

    The printer is NOT live-checked (no detection logic, by owner request):
    it is a direct USB-to-serial (VCOM) device that will never show up as
    an OS printer, so its pill is purely config-driven. A pulled cable
    fails explicitly at print time with the port name in the error.

    Live checks are cached (port list 10s) so the 5s strip poll never
    blocks the UI. A cache miss that fails keeps the last verdict instead
    of flapping the pill.
    """
    if cfg is None:
        cfg = _config.load()
    spec = device_status(key, cfg)
    if spec.get("state") != "ready":
        return spec
    try:
        if key == SCALE:
            present = cached_scale_port_present(str(cfg.get("scale_port") or ""))
            if present is False:
                return {**spec, "state": "setup", "detail": "not found",
                        "hint": "Click to detect again."}
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