"""One-shot hardware visibility report — run ONCE per machine, paste output back.

Usage (repo root):
    python3 tools/diagnose_hardware.py        # Linux
    C:\\Python312\\python.exe tools\\diagnose_hardware.py   # Windows

Reports what Simpl'd can see, in priority order:
  1. OS printer queues (the ONLY place the label-printer detector looks)
  2. USB printer-class presence (advisory live-pill signal)
  3. Serial/COM ports (weigh-machine candidates)
  4. Stored hardware.json + env overrides (what the app will actually use)
  5. simpld.log location (detection outcomes are logged there now)

Needs PySide6 + pyserial for sections 1/3 (present in dev + Windows build
envs); degrades gracefully without them. Stdlib otherwise. Read-only:
never opens ports, never writes config.
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def main() -> int:
    import platform
    section("platform")
    print(f"system={platform.system()} release={platform.release()} "
          f"machine={platform.machine()} python={sys.version.split()[0]}")
    try:
        from factory.version import VERSION
        print(f"simpld VERSION={VERSION}")
    except Exception as e:
        print(f"simpld VERSION: unreadable ({e})")

    section("1. OS printer queues (label-printer detection reads ONLY this)")
    try:
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from factory.hardware.qtprint import available_printers
        names = available_printers()
        print(f"count={len(names)}")
        for n in names:
            print(f"  - {n!r}")
        try:
            from factory.hardware import autodetect as Auto
            print(f"hint match -> {Auto.detect_printer()!r}")
        except Exception as e:
            print(f"detect_printer: raised {e!r}")
    except Exception as e:
        print(f"Qt printer enumeration unavailable ({e!r})")

    section("2. USB printer-class presence (advisory only)")
    try:
        from factory.hardware import status as S
        print(f"usb_printer_present() -> {S.usb_printer_present()!r}")
    except Exception as e:
        print(f"unreadable ({e!r})")

    section("3. serial/COM ports (weigh-machine candidates)")
    try:
        from factory.hardware.essae import list_ports
        ports = list_ports()
        print(f"count={len(ports)}")
        for p in ports:
            print(f"  - {p}")
    except Exception as e:
        print(f"pyserial enumeration unavailable ({e!r})")

    section("4. stored hardware.json + env overrides (what the app uses)")
    for var in ("SIMPLD_HW_JSON", "SIMPLD_DATA_DIR", "SIMPLD_SCALE_PORT",
                "SIMPLD_PRINTER_NAME", "SIMPLD_DEMO", "SIMPLD_MOCK_QR"):
        if os.environ.get(var):
            print(f"env {var}={os.environ[var]!r}")
    try:
        from factory.hardware import config as C
        cfg = C.load()
        print(f"path={C._default_path()}")
        print(json.dumps({k: cfg.get(k) for k in
                          ("scale_port", "scale_baud", "printer_name",
                           "demo_mode", "mock_qr")}, indent=2))
    except Exception as e:
        print(f"config unreadable ({e!r})")

    section("5. logs")
    try:
        from factory.db import app_data_dir
        log = os.path.join(app_data_dir(), "logs", "simpld.log")
        print(f"simpld.log: {log}")
        print(f"exists={os.path.exists(log)}")
    except Exception as e:
        print(f"log dir unreadable ({e!r})")

    print("\nPaste everything above back, plus: does the Citizen show in "
          "Windows Settings > Printers (queue name?), or only in Device "
          "Manager (as what?).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
