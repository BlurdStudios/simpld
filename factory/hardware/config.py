"""Hardware config. Offline JSON in data/hardware.json + env overrides.

Windows factory PC example:
  SIMPLD_SCALE_PORT=COM3 python3 run.py
  SIMPLD_PRINTER_NAME="Citizen CL-S621" python3 run.py

File wins over defaults, env wins over file (so a shortcut can pin COM3
without editing JSON on the floor).
"""
from __future__ import annotations
import json
import os

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_LEGACY_DEFAULT = os.path.join(_BASE, "data", "hardware.json")


def _default_path() -> str:
    """Stable user data root (frozen-safe): <root>/hardware.json.

    Honors SIMPLD_DATA_DIR (tests, portable installs) and the frozen
    Windows %LOCALAPPDATA%/Simpld root via factory.db.app_data_dir().
    Falls back to the legacy <repo>/data path if that helper is
    unavailable. Evaluated per call so env changes (tests) take effect.
    """
    try:
        from factory.db import app_data_dir
        return os.path.join(app_data_dir(), "hardware.json")
    except Exception:
        return _LEGACY_DEFAULT


# Import-time value for back-compat; load()/save() use _default_path().
DEFAULT_PATH = _default_path()

DEFAULTS = {
    # Essae DS-215: 9600 8N1. COM port set on factory PC via dialog/env.
    "scale_port": "",          # e.g. "COM3" on Windows, "/dev/ttyS0" on Linux. "" = manual/mock
    "scale_baud": 9600,
    "scale_bytesize": 8,
    "scale_parity": "N",
    "scale_stopbits": 1,
    # Citizen CL-S621 (USB): Windows printer share name from driver install.
    "printer_name": "",        # e.g. "Citizen CL-S621". "" = file-log/mock
    "demo_mode": False,        # explicit demo switch: simulate scale+printer (no hardware)
    "mock_qr": False,          # test aid: show a Dispatch button that loads the
                               # next Ready box as if its QR was scanned. Off on
                               # the floor; enable from About (admin) or
                               # SIMPLD_MOCK_QR=1. Does not change printer mode.
    "label_width_mm": 100,     # 4-inch media default; dialog-adjustable
    "label_height_mm": 100,
    "label_dpi": 203,          # CL-S621 = 203dpi
}


def load(path: str | None = None) -> dict:
    p = path or os.environ.get("SIMPLD_HW_JSON") or _default_path()
    cfg = dict(DEFAULTS)
    try:
        if os.path.exists(p):
            with open(p) as f:
                cfg.update(json.load(f))
    except Exception:
        pass
    # env overrides (deploy without editing files)
    if os.environ.get("SIMPLD_SCALE_PORT"):
        cfg["scale_port"] = os.environ["SIMPLD_SCALE_PORT"]
    if os.environ.get("SIMPLD_PRINTER_NAME"):
        cfg["printer_name"] = os.environ["SIMPLD_PRINTER_NAME"]
    if os.environ.get("SIMPLD_DEMO") == "1":
        cfg["demo_mode"] = True
    if "SIMPLD_MOCK_QR" in os.environ:
        cfg["mock_qr"] = os.environ.get("SIMPLD_MOCK_QR") == "1"
    return cfg


def save(cfg: dict, path: str | None = None) -> str:
    p = path or os.environ.get("SIMPLD_HW_JSON") or _default_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    # only persist known keys
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    with open(p, "w") as f:
        json.dump(out, f, indent=2)
    return p
