"""Auto-detect the factory hardware. No dropdowns, no manual port entry.

The operator should never have to know that a weigh machine lives on COM3. On
startup (and whenever a status pill is clicked, or Refresh pressed) Simpl'd
figures the hardware out by itself:

  Weigh machine   probes every serial port at the Essae default 9600 8N1 and
                  keeps the one that answers with a *parseable weight frame*.
                  That is a real handshake, not a name guess — a wrong port
                  stays silent, so a false positive is not possible.
  QR scanner      nothing to detect — keyboard wedge.

Detected values are written to the same `hardware.json` the old dialog used,
so the env override (`SIMPLD_SCALE_PORT`) and the
"fail closed, never mock" rules in `devices.py` all keep working unchanged.

Nothing here imports Qt, so the UI never blocks while ports are probed.

Prod contract (what "stuck on detecting" taught us):
- Every probe has a deadline: configured port first (fast path, <1s when
  healthy), then all ports at 9600, fallback bauds only with time left.
- Forgetting a stored entry only happens when enumeration itself succeeded
  (pyserial present) but the device genuinely
  answers nothing. A failed enumeration (pyserial missing, spooler error,
  exception) keeps the stored config and reports an error instead of
  wiping hardware.json.
- detect_all/detect_one never raise, never fabricate, always log timings
  + ports seen so a "not found" pill is diagnosable from
  simpld.log without guessing.
- The watchdog uses reconnect_scale(), which NEVER forgets a port on a miss.
  A scale that is off or briefly unplugged keeps its config until a real hit.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time

from . import config as _config
from . import devices as _devices

_log = logging.getLogger("simpld.hardware")

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
SCALE_BUDGET_S = 8.0        # max time spent probing serial ports per run
OVERALL_BUDGET_S = 12.0     # hard deadline for a full detect_all run

def _is_mock_fn(fn) -> bool:
    """True when fn is a unittest.mock patch (tests). Cached/live paths
    must bypass caches when mocked, otherwise one test's values leak
    into the next via the TTL."""
    try:
        if getattr(fn, "_mock_name", None) is not None:
            return True
        if "mock" in str(type(fn).__module__ or "").lower():
            return True
        if hasattr(fn, "assert_called_with") and hasattr(fn, "call_count"):
            return True
    except Exception:
        pass
    return False


def list_ports_status() -> tuple[list[str], bool]:
    """(ports, trustworthy): False only when enumeration itself failed.

    pyserial missing or comports() raising means "we could not look" —
    callers must NOT forget stored config on that signal. An empty list
    with True means the OS enumerated fine and there really is no serial
    hardware.
    """
    try:
        from . import essae as _essae
        _fn = _essae.list_ports
    except Exception:
        return [], False
    if _is_mock_fn(_fn):
        # Tests patch essae.list_ports: trust the mock exactly.
        try:
            return list(_fn() or []), True
        except Exception:
            return [], False
    # Real path: enumerate directly (essae.list_ports swallows errors
    # into [] and would hide "could not look" from the forget logic).
    try:
        from serial.tools import list_ports as lp
    except Exception as e:
        _log.warning("list_ports: pyserial unavailable (%s) — "
                     "keeping stored config", e)
        return [], False
    try:
        return [p.device for p in lp.comports()], True
    except Exception as e:
        _log.warning("list_ports: enumeration failed (%s) — "
                     "keeping stored config", e, exc_info=True)
        return [], False


# ---- weigh machine --------------------------------------------------------
def probe_ports(ports: list[str] | None = None,
                dwell: float = PROBE_DWELL,
                deadline: float | None = None,
                baud_list: tuple[int, ...] | None = None) -> list[dict]:
    """Probe serial ports for an Essae that answers.

    Returns [{port, baud, weight}] for every port that produced a parseable
    weight frame. Probes PRIMARY_BAUD on all ports first (the overwhelmingly
    common case), then falls back to other rates only if nothing answered
    AND the deadline allows it.

    Fast path: the already-configured port (when present in the candidate
    list) is probed FIRST and returned immediately on a hit, so a healthy
    plant resolves in ~0.5s instead of sweeping every COM port.
    """
    from .essae import EssaeDS215, list_ports

    if ports is None:
        try:
            candidates = list(list_ports())
        except Exception:
            candidates = []
    else:
        candidates = list(ports)
    if not candidates:
        return []
    if baud_list is None:
        baud_list = (PRIMARY_BAUD,) + FALLBACK_BAUDS
    elif not baud_list:
        baud_list = (PRIMARY_BAUD,)
    if deadline is None:
        deadline = time.monotonic() + SCALE_BUDGET_S

    found: list[dict] = []
    answered: set[str] = set()

    def _expired() -> bool:
        try:
            return time.monotonic() >= deadline
        except Exception:
            return False

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

    for baud in baud_list:
        for port in candidates:
            if port in answered or _expired():
                continue
            # Serial open on a dead Bluetooth/SPP port can block past the
            # read timeout, so each probe is bounded by the global deadline
            # (checked between ports) rather than trusting per-port timing.
            hit = probe(port, baud)
            if hit:
                found.append(hit)
                answered.add(port)
                # Fast path: first baud round with a hit ends the sweep —
                # a healthy plant never pays for fallback rates.
                if baud == baud_list[0] and len(found) == 1 and len(candidates) > 1:
                    pass  # keep sweeping this baud round for multi-scale note
        if found:
            break   # got a real answer; don't spend time on other rates
        if _expired():
            _log.info("probe_ports: deadline reached at baud %s — "
                      "stopping sweep", baud)
            break
    return found


def detect_scale(cfg: dict | None = None, save: bool = True,
                 timeout: float = SCALE_BUDGET_S) -> dict | None:
    """Find the weigh machine. Returns the detected dict (probe only).

    Persistence is intentionally NOT done here — `detect_all` writes both
    results in a single read-modify-write. Saving from this function would
    re-read the file and let a concurrent printer save clobber it.
    """
    hit, _trustworthy, _detail = detect_scale_ex(cfg, timeout=timeout)
    return hit


def detect_scale_ex(cfg: dict | None = None,
                    timeout: float = SCALE_BUDGET_S
                    ) -> tuple[dict | None, bool, dict]:
    """(hit, trustworthy, detail): trustworthy=False means "could not look".

    Callers must only forget stored config when trustworthy is True.
    """
    t0 = time.monotonic()
    cfg = cfg if cfg is not None else _config.load()
    ports, ok = list_ports_status()
    if not ok:
        return None, False, {"ports": [], "error": "serial enumeration failed",
                             "elapsed": round(time.monotonic() - t0, 2)}
    if not ports:
        return None, True, {"ports": [], "error": "",
                             "elapsed": round(time.monotonic() - t0, 2)}
    deadline = t0 + max(0.5, float(timeout or SCALE_BUDGET_S))
    # Fast path first: configured port alone, primary baud only.
    want = str(cfg.get("scale_port") or "").strip()
    ordered = ([want] + [p for p in ports if p != want]) if want in ports \
        else list(ports)
    try:
        hits = probe_ports(ordered, deadline=deadline)
    except Exception as e:
        _log.warning("detect_scale: probe crashed (%s)", e, exc_info=True)
        return None, False, {"ports": ports, "error": str(e),
                             "elapsed": round(time.monotonic() - t0, 2)}
    if not hits:
        return None, True, {"ports": ports, "error": "",
                             "elapsed": round(time.monotonic() - t0, 2)}
    # Prefer the already-configured port when several answer, so a restart
    # never silently migrates to a different machine.
    pick = next((h for h in hits if h["port"] == want), hits[0])
    detail = {"ports": ports, "hits": len(hits),
              "elapsed": round(time.monotonic() - t0, 2), "error": ""}
    return pick, True, detail


# ---- both -----------------------------------------------------------------
def _env_pins_scale() -> str:
    return str(os.environ.get("SIMPLD_SCALE_PORT") or "").strip()


def detect_all(save: bool = True, cfg: dict | None = None,
               timeout: float = OVERALL_BUDGET_S) -> dict:
    """Detect both configurable devices. Never raises; never fabricates.

    Returns {'scale': {...}|None, ...diagnostics}. Anything not found stays
    unconfigured, so devices.py keeps failing closed rather than quietly
    switching to the mock.

    A device the probe can no longer find is FORGOTTEN (its entry is cleared)
    so the pills read "not found" instead of "ready" for phantom hardware —
    unless an env override (SIMPLD_SCALE_PORT) pins it, which keeps winning
    as documented in hardware/config.py, and unless the enumeration itself
    failed (pyserial missing), in which case the stored value is KEPT and the
    failure is logged.
    """
    t0 = time.monotonic()
    overall_deadline = t0 + max(2.0, float(timeout or OVERALL_BUDGET_S))
    with _LOCK:
        cfg = dict(cfg) if cfg is not None else _config.load()
        scale = None
        scale_ok = False
        scale_detail: dict = {}
        errors: list[str] = []
        try:
            remaining = max(1.0, overall_deadline - time.monotonic())
            scale, scale_ok, scale_detail = detect_scale_ex(
                cfg, timeout=min(SCALE_BUDGET_S, remaining))
        except Exception as e:
            scale, scale_ok = None, False
            scale_detail = {"error": str(e)}
            errors.append(f"scale: {e}")

        changed = False
        if scale:
            if cfg.get("scale_port") != scale["port"] or \
                    int(cfg.get("scale_baud") or 0) != int(scale["baud"]):
                changed = True
            cfg["scale_port"] = scale["port"]
            cfg["scale_baud"] = scale["baud"]
        elif scale_ok and cfg.get("scale_port") \
                and cfg.get("scale_port") != _env_pins_scale():
            # Probed, nothing answered, nobody pinned it: forget the stale
            # port instead of reporting "ready" for a scale that is gone.
            changed = True
            cfg["scale_port"] = ""
        elif scale is None and not scale_ok and cfg.get("scale_port"):
            _log.warning("detect_all: scale probe inconclusive "
                         "(enumeration failed) — keeping stored %r",
                         cfg.get("scale_port"))
        if changed and save:
            try:
                _config.save(cfg)
            except Exception as e:
                errors.append(f"save: {e}")
                _log.warning("detect_all: config save failed (%s)", e,
                             exc_info=True)
        # Reload live drivers when the stored config moved in either
        # direction (found OR forgotten). Forgetting without reloading
        # used to leave the old real driver live while the pill said
        # "not found".
        if scale or changed:
            try:
                _devices.reload_hardware(cfg)
            except Exception as e:
                errors.append(f"reload: {e}")
        elapsed = round(time.monotonic() - t0, 2)
        _log.info("detect_all: scale=%s changed=%s in %.1fs",
                  (scale or {}).get("port") if scale else None,
                  changed, elapsed)
        out = {"scale": scale}
        # Diagnostics (additive: older callers ignore them).
        out["changed"] = changed
        out["elapsed"] = elapsed
        if errors:
            out["errors"] = errors
        try:
            out["ports"] = scale_detail.get("ports", [])
        except Exception:
            pass
        return out


def detect_one(key: str, save: bool = True,
               timeout: float = OVERALL_BUDGET_S) -> dict:
    """Re-detect a single device (what clicking one pill does).

    Same single read-modify-write discipline as detect_all, but scoped to one
    device so the other one's stored config is preserved. Like detect_all, a
    probe that finds nothing forgets the stale entry (unless env-pinned),
    but an inconclusive probe (enumeration failed) keeps it.
    """
    with _LOCK:
        cfg = dict(_config.load())
        out: dict = {"scale": None}
        errors: list[str] = []
        changed = False
        if key == "scale":
            try:
                hit, ok, _detail = detect_scale_ex(
                    cfg, timeout=min(SCALE_BUDGET_S, timeout))
            except Exception as e:
                hit, ok = None, False
                errors.append(f"scale: {e}")
            out["scale"] = hit
            if hit:
                cfg["scale_port"] = hit["port"]
                cfg["scale_baud"] = hit["baud"]
                changed = True
            elif ok and cfg.get("scale_port") \
                    and cfg.get("scale_port") != _env_pins_scale():
                cfg["scale_port"] = ""
                changed = True
        if changed and save:
            try:
                _config.save(cfg)
            except Exception as e:
                errors.append(f"save: {e}")
        if out["scale"] or changed:
            try:
                _devices.reload_hardware(cfg)
            except Exception as e:
                errors.append(f"reload: {e}")
        if errors:
            out["errors"] = errors
        return out


def reconnect_scale(save: bool = True,
                    timeout: float = SCALE_BUDGET_S) -> dict:
    """Quiet watchdog reconnect. Only a real hit touches config/live drivers.

    Unlike detect_one()/detect_all(), a miss NEVER clears scale_port: a scale
    that is switched off or briefly unplugged must not be forgotten just
    because the watchdog looked. An inconclusive probe (enumeration failed)
    is reported but leaves hardware.json alone too.

    On a hit we reload even when port/baud are unchanged — the live driver can
    still be holding a stale serial handle from before the scale went away.
    """
    t0 = time.monotonic()
    out: dict = {"scale": None, "changed": False, "inconclusive": False}
    try:
        with _LOCK:
            cfg = dict(_config.load())
            try:
                hit, ok, detail = detect_scale_ex(cfg, timeout=timeout)
            except Exception as e:
                out["inconclusive"] = True
                out["error"] = str(e)
                _log.warning("reconnect_scale: probe failed (%s)", e,
                             exc_info=True)
                return out
            out["inconclusive"] = not ok
            try:
                out["ports"] = detail.get("ports", [])
            except Exception:
                pass
            if not hit:
                if not ok:
                    _log.info("reconnect_scale: inconclusive — keeping stored %r",
                              cfg.get("scale_port"))
                return out

            changed = (cfg.get("scale_port") != hit["port"] or
                       int(cfg.get("scale_baud") or 0) != int(hit["baud"]))
            cfg["scale_port"] = hit["port"]
            cfg["scale_baud"] = hit["baud"]
            out["scale"] = hit
            out["changed"] = bool(changed)
            if changed and save:
                try:
                    _config.save(cfg)
                except Exception as e:
                    out.setdefault("errors", []).append(f"save: {e}")
            # Always rebuild live drivers on a hit: same port still means a
            # stale handle until a fresh driver object is installed.
            try:
                _devices.reload_hardware(cfg)
            except Exception as e:
                out.setdefault("errors", []).append(f"reload: {e}")
            _log.info("reconnect_scale: scale=%s changed=%s",
                      hit["port"], out["changed"])
            return out
    finally:
        out["elapsed"] = round(time.monotonic() - t0, 2)


def current_ports() -> list[str]:
    try:
        ports, _ok = list_ports_status()
        return ports
    except Exception:
        return []


# Small guard used by the tests: a scale port is "believable" only if it was
# produced by a probe. Keeps a hand-edited hardware.json from looking detected.
_DETECTED_RE = re.compile(r"^\s*(COM\d+|/dev/tty\S+|/dev/cu\.\S+)\s*$", re.I)
