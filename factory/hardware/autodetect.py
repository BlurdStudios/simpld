"""Auto-detect the factory hardware. No dropdowns, no manual port entry.

The operator should never have to know that a weigh machine lives on COM3. On
startup (and whenever a status pill is clicked, or Refresh pressed) Simpl'd
figures the hardware out by itself:

  Weigh machine   probes every serial port at the Essae default 9600 8N1 and
                  keeps the one that answers with a *parseable weight frame*.
                  That is a real handshake, not a name guess — a wrong port
                  stays silent, so a false positive is not possible.
  Label printer   reads the installed Windows/Linux printer list and picks the
                  one whose name matches the Citizen CL-S621 family.
  QR scanner      nothing to detect — keyboard wedge.

Detected values are written to the same `hardware.json` the old dialog used,
so the env overrides (`SIMPLD_SCALE_PORT` / `SIMPLD_PRINTER_NAME`) and the
"fail closed, never mock" rules in `devices.py` all keep working unchanged.

Nothing here imports Qt: detection is called from a worker thread so the UI
never blocks while ports are probed.
"""
from __future__ import annotations

import re
import threading

from . import config as _config
from . import devices as _devices

# One detection at a time, process-wide. Every station strip can ask to detect;
# without this two worker threads would probe the same COM ports at once and
# race on the config file.
_LOCK = threading.Lock()

# Essae DS-215 ships at 9600 8N1; that is probed first on every port so a
# normal plant resolves in one pass. Other rates are only tried if nothing
# answers, which keeps startup detection to a couple of seconds.
PRIMARY_BAUD = 9600
FALLBACK_BAUDS = (19200, 38400, 57600, 115200)

PROBE_DWELL = 0.45          # seconds per port; the indicator streams ~10/s
PROBE_TIMEOUT = 0.5         # serial read timeout

# Substrings that identify the label printer in the OS printer list.
PRINTER_HINTS = ("citizen", "cl-s621", "cl-s6", "cls-621")


# ---- weigh machine --------------------------------------------------------
def probe_ports(ports: list[str] | None = None,
                dwell: float = PROBE_DWELL) -> list[dict]:
    """Probe serial ports for an Essae that answers.

    Returns [{port, baud, weight}] for every port that produced a parseable
    weight frame. Probes PRIMARY_BAUD on all ports first (the overwhelmingly
    common case), then falls back to other rates only if nothing answered.
    """
    from .essae import EssaeDS215, list_ports

    candidates = list(ports if ports is not None else list_ports())
    if not candidates:
        return []
    found: list[dict] = []
    answered: set[str] = set()

    def probe(port: str, baud: int) -> dict | None:
        drv = EssaeDS215(port, baud=baud, timeout=PROBE_TIMEOUT)
        # test() dwells for a fixed 1.2s; go through _read_lines directly so
        # detection stays quick, then decide with the same parser the app uses.
        try:
            lines = drv._read_lines(dwell=dwell)
        except Exception:
            return None
        finally:
            try:
                drv.close()
            except Exception:
                pass
        from .essae import parse_weight_line
        for line in reversed(lines[-8:]):
            val = parse_weight_line(line)
            if val is not None:
                return {"port": port, "baud": baud, "weight": val}
        return None

    for baud in (PRIMARY_BAUD,) + FALLBACK_BAUDS:
        for port in candidates:
            if port in answered:
                continue
            hit = probe(port, baud)
            if hit:
                found.append(hit)
                answered.add(port)
        if found:
            break   # got a real answer; don't spend time on other rates
    return found


def detect_scale(cfg: dict | None = None, save: bool = True) -> dict | None:
    """Find the weigh machine. Returns the detected dict (probe only).

    Persistence is intentionally NOT done here — `detect_all` writes both
    results in a single read-modify-write. Saving from this function would
    re-read the file and let a concurrent printer save clobber it.
    """
    cfg = cfg if cfg is not None else _config.load()
    hits = probe_ports()
    if not hits:
        return None
    # Prefer the already-configured port when several answer, so a restart
    # never silently migrates to a different machine.
    want = str(cfg.get("scale_port") or "").strip()
    return next((h for h in hits if h["port"] == want), hits[0])


# ---- label printer --------------------------------------------------------
def detect_printer(save: bool = True) -> str | None:
    """Pick the Citizen label printer out of the OS printer list.

    Probe only (see detect_scale): persistence is done by `detect_all`.
    """
    from .qtprint import available_printers

    names = available_printers()
    if not names:
        return None
    for needle in PRINTER_HINTS:
        for name in names:
            if needle in name.casefold():
                return name
    return None


# ---- both -----------------------------------------------------------------
def detect_all(save: bool = True, cfg: dict | None = None) -> dict:
    """Detect both configurable devices. Never raises; never fabricates.

    Returns {'scale': {...}|None, 'printer': str|None}. Both results are merged
    into ONE config read-modify-write, so the two devices can never overwrite
    each other's detection. Anything not found stays unconfigured, so
    devices.py keeps failing closed rather than quietly switching to the mock.
    """
    with _LOCK:
        cfg = dict(cfg) if cfg is not None else _config.load()
        scale = None
        printer = None
        try:
            scale = detect_scale(cfg)
        except Exception:
            scale = None
        try:
            printer = detect_printer()
        except Exception:
            printer = None

        changed = False
        if scale:
            if cfg.get("scale_port") != scale["port"] or \
                    int(cfg.get("scale_baud") or 0) != int(scale["baud"]):
                changed = True
            cfg["scale_port"] = scale["port"]
            cfg["scale_baud"] = scale["baud"]
        if printer and cfg.get("printer_name") != printer:
            changed = True
            cfg["printer_name"] = printer

        if changed and save:
            try:
                _config.save(cfg)
            except Exception:
                pass
        if scale or printer:
            try:
                _devices.reload_hardware(cfg)
            except Exception:
                pass
        return {"scale": scale, "printer": printer}


def detect_one(key: str, save: bool = True) -> dict:
    """Re-detect a single device (what clicking one pill does).

    Same single read-modify-write discipline as detect_all, but scoped to one
    device so the other one's stored config is preserved.
    """
    with _LOCK:
        cfg = dict(_config.load())
        out: dict = {"scale": None, "printer": None}
        if key == "scale":
            out["scale"] = detect_scale(cfg)
            if out["scale"]:
                cfg["scale_port"] = out["scale"]["port"]
                cfg["scale_baud"] = out["scale"]["baud"]
        elif key == "printer":
            out["printer"] = detect_printer()
            if out["printer"]:
                cfg["printer_name"] = out["printer"]
        if save:
            try:
                _config.save(cfg)
            except Exception:
                pass
        if out["scale"] or out["printer"]:
            try:
                _devices.reload_hardware(cfg)
            except Exception:
                pass
        return out


def current_ports() -> list[str]:
    from .essae import list_ports
    try:
        return list_ports()
    except Exception:
        return []


# Small guard used by the tests: a scale port is "believable" only if it was
# produced by a probe. Keeps a hand-edited hardware.json from looking detected.
_DETECTED_RE = re.compile(r"^\s*(COM\d+|/dev/tty\S+|/dev/cu\.\S+)\s*$", re.I)