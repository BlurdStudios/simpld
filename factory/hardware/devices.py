"""Shared hardware instances, initialized from existing config (no new knobs).

Blank scale_port / printer_name means NOT CONFIGURED: weighing and printing
are blocked by the UI gates (require_scale/require_printer) instead of
creating mock records. The mock drivers in factory/hardware/mock.py are
retained ONLY for test compatibility (SIMPLD_ALLOW_MOCK=1 in tests) and for
the shared-instance plumbing below — never as production data sources.

One exception, explicit and user-visible: the demo switch (config key
`demo_mode`, toggled from the Packaging device strip, or SIMPLD_DEMO=1).
Unlike SIMPLD_ALLOW_MOCK it IS honored in the frozen build, on purpose —
the demo runs on a machine with no scale or printer. It is never silent:
the user turns it on, a confirmation is shown, and the pills read "demo".
Without the switch, blank config stays fail-closed exactly as below.

Setting a port/name in the Devices dialog (or SIMPLD_SCALE_PORT /
SIMPLD_PRINTER_NAME env, or data/hardware.json) selects the real driver
after reload_hardware() — applied immediately, no restart. A real device
that later fails raises at the point of use; nothing silently falls back
to fake reads/prints.

A configured device that fails to *build* (bad baud, broken import)
installs an explicitly-raising placeholder and records init_error —
surfaced in mode_text() — instead of silently keeping the mock.

UI code keeps using `from factory.hardware import mock as HW` /
HW.scale / HW.printer: reload_hardware() swaps those same attributes, so
existing callers and test patches keep working. devices.* is the same
object graph (no second copy).
"""
from __future__ import annotations

import os

from . import config as _config
from . import mock as _mock

# Last build problem with a *configured* device. None when healthy.
# Never silent: mode_text() surfaces it until the config is fixed.
init_error: str | None = None


class _BrokenScale:
    """Placeholder: a configured real scale failed to build. Raises on use."""

    def __init__(self, error: Exception | str):
        self._error = str(error)

    def read_weight(self) -> float:
        raise RuntimeError(f"Scale unavailable: {self._error}")

    def freeze(self) -> float:
        raise RuntimeError(f"Scale unavailable: {self._error}")

    def close(self):
        pass


class _BrokenPrinter:
    """Placeholder: a configured real printer failed to build. Raises on use."""

    def __init__(self, error: Exception | str):
        self._error = str(error)

    def print_reel_label(self, data: dict) -> bool:
        raise RuntimeError(f"Printer unavailable: {self._error}")

    def print_carton_label(self, data: dict) -> bool:
        raise RuntimeError(f"Printer unavailable: {self._error}")

    def print_client_label(self, data: dict) -> bool:
        raise RuntimeError(f"Printer unavailable: {self._error}")


def build_scale(cfg: dict):
    """Real Essae when a port is configured, else the demo/mock scale."""
    if demo_mode(cfg):
        return _mock.MockEssaeDS215(demo=True)
    port = str(cfg.get("scale_port") or "").strip()
    if not port:
        return _mock.MockEssaeDS215()
    from .essae import EssaeDS215
    return EssaeDS215(port, baud=int(cfg.get("scale_baud") or 9600))


def build_printer(cfg: dict):
    """Real Citizen (installed Qt printer) when named, else the log mock."""
    if demo_mode(cfg):
        return _mock.MockCitizenCLS621()
    name = str(cfg.get("printer_name") or "").strip()
    if not name:
        return _mock.MockCitizenCLS621()
    from .qtprint import QtCitizenCLS621
    return QtCitizenCLS621(name)


def reload_hardware(cfg: dict | None = None) -> dict:
    """(Re)build shared instances from config. Returns the cfg used.

    Never silently substitutes demo/mock for a configured device: a
    real build that raises installs an explicitly-raising placeholder
    and records init_error (surfaced in mode_text()) instead.
    """
    global init_error
    cfg = dict(cfg) if cfg is not None else _config.load()
    errors: list[str] = []
    try:
        new_scale = build_scale(cfg)
    except Exception as e:
        errors.append(f"scale: {e}")
        new_scale = _BrokenScale(e)
    try:
        new_printer = build_printer(cfg)
    except Exception as e:
        errors.append(f"printer: {e}")
        new_printer = _BrokenPrinter(e)
    try:
        old = _mock.scale
        if old is not new_scale and hasattr(old, "close"):
            old.close()
    except Exception:
        pass
    _mock.scale = new_scale
    _mock.printer = new_printer
    init_error = "; ".join(errors) if errors else None
    return cfg


def is_mock_scale() -> bool:
    return isinstance(_mock.scale, _mock.MockEssaeDS215)


def is_mock_printer() -> bool:
    return isinstance(_mock.printer, _mock.MockCitizenCLS621)


def scale_configured(cfg: dict | None = None) -> bool:
    """True when a real scale port is configured (blank blocks weighing)."""
    cfg = cfg if cfg is not None else _config.load()
    return bool(str(cfg.get("scale_port") or "").strip())


def printer_configured(cfg: dict | None = None) -> bool:
    """True when a real printer name is configured (blank blocks printing)."""
    cfg = cfg if cfg is not None else _config.load()
    return bool(str(cfg.get("printer_name") or "").strip())


def mock_allowed() -> bool:
    """Test-compat override: SIMPLD_ALLOW_MOCK=1 lets tests exercise the
    mock drivers. Never honored in the frozen production build."""
    from factory.db import _is_frozen
    if _is_frozen():
        return False
    return os.environ.get("SIMPLD_ALLOW_MOCK") == "1"


def demo_mode(cfg: dict | None = None) -> bool:
    """True when the explicit demo switch is on (simulated hardware).

    Unlike SIMPLD_ALLOW_MOCK this IS honored in the frozen build on
    purpose: the switched-on demo runs on a machine with no scale or
    printer. It is never silent — the user turns it on from the device
    strip, a confirmation is shown, and the pills read "demo".
    """
    cfg = cfg if cfg is not None else _config.load()
    return bool(cfg.get("demo_mode"))


def require_scale(cfg: dict | None = None) -> None:
    """Fail closed when no scale is configured (and mock not test-enabled).

    Raises RuntimeError with a Devices-pointer message; callers surface it
    and leave the weigh card disarmed (0) instead of writing mock records.
    The explicit demo switch also passes — see demo_mode().
    """
    if scale_configured(cfg) or mock_allowed() or demo_mode(cfg):
        return
    raise RuntimeError("Scale not configured — open Devices to configure the scale.")


def require_printer(cfg: dict | None = None) -> None:
    """Fail closed when no printer is configured (and mock not test-enabled).

    Records stay pending/reprintable; nothing is marked printed.
    The explicit demo switch also passes — see demo_mode().
    """
    if printer_configured(cfg) or mock_allowed() or demo_mode(cfg):
        return
    raise RuntimeError(
        "Printer not configured — set the label printer for this PC.")


def _override_suffix() -> str:
    """Short note when env vars override the stored file (env wins)."""
    active = [k for k in ("SIMPLD_SCALE_PORT", "SIMPLD_PRINTER_NAME",
                          "SIMPLD_DEMO",
                          "SIMPLD_HW_JSON", "SIMPLD_DATA_DIR")
              if os.environ.get(k)]
    return f" · env override: {', '.join(active)}" if active else ""


def mode_text(cfg: dict | None = None) -> str:
    """Short explicit mode indication for the UI (never silent)."""
    cfg = cfg if cfg is not None else _config.load()
    if demo_mode(cfg):
        base = "DEMO — simulated scale + printer (no hardware)"
    elif is_mock_scale() and is_mock_printer():
        base = "Demo/mock — no device selected"
    else:
        scale = ("mock" if is_mock_scale()
                 else f"scale {cfg.get('scale_port')}:{cfg.get('scale_baud', 9600)}")
        printer = ("log only" if is_mock_printer()
                   else f"printer {cfg.get('printer_name')}")
        base = f"Real: {scale} · {printer}"
    if init_error:
        base += f" · CONFIG ERROR: {init_error}"
    return base + _override_suffix()


# First import applies env/file config immediately (demo/mock by default).
# reload_hardware() never raises (per-device placeholders above), so a
# configured-but-broken device is explicit, never a silent mock.
reload_hardware()
