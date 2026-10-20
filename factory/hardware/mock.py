"""Mock hardware for prototype. Real drivers replace these 1:1 later.

- MockEssaeDS215: manual entry + small jitter, mimics RS232 'read'/'stop'
- MockCitizenCLS621: logs print jobs to data/print_log.txt (preview in UI)
- MockScanner: keyboard wedge -> nothing to do, UI LineEdit handles Enter
"""
from __future__ import annotations
import os
import random
from datetime import datetime
from .base import ScaleInterface, PrinterInterface, ScannerInterface

_BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _default_log_path() -> str:
    """Stable user data root (frozen-safe): <root>/print_log.txt.

    Honors SIMPLD_DATA_DIR / frozen %LOCALAPPDATA%/Simpld via
    factory.db.app_data_dir(). Per-call so env changes (tests) apply.
    """
    try:
        from factory.db import app_data_dir
        return os.path.join(app_data_dir(), "print_log.txt")
    except Exception:
        return os.path.join(_BASE, "data", "print_log.txt")


class MockEssaeDS215(ScaleInterface):
    """Pretends to be the Essae DS-215. Real version opens serial.Serial('/dev/ttyS0', 9600).

    demo=True (set only by devices.build_scale when the demo switch is on)
    simulates plausible reel/carton weights so the app can be shown with no
    hardware. Default instances keep the old manual-entry behavior tests use.
    """

    def __init__(self, demo: bool = False):
        self._manual = 0.0
        self._frozen: float | None = None
        self._demo = bool(demo)
        self._demo_base: float | None = None

    def set_manual(self, kg: float):
        self._manual = float(kg or 0)
        self._frozen = None
        self._demo_base = None

    def _demo_read_base(self) -> float:
        if self._demo_base is None:
            self._demo_base = random.uniform(4.0, 24.0)
        return self._demo_base

    def read_weight(self) -> float:
        if self._manual:
            base = self._frozen if self._frozen is not None else self._manual
            jitter = random.uniform(-0.005, 0.005)
            return round(max(0.0, base + jitter), 3)
        if self._demo:
            if self._frozen is not None:
                return round(self._frozen, 3)
            return round(self._demo_read_base() + random.uniform(-0.02, 0.02), 3)
        return 0.0

    def freeze(self) -> float:
        if self._manual:
            self._frozen = round(self._manual, 3)
            return self._frozen
        if self._demo:
            self._frozen = round(self._demo_read_base(), 3)
            return self._frozen
        self._frozen = round(self._manual, 3)   # old behavior: 0.0
        return self._frozen

    def unfreeze(self):
        self._frozen = None
        if self._demo and not self._manual:
            self._demo_base = None   # next read = a new simulated reel


class MockCitizenCLS621(PrinterInterface):
    """Demo/mock label printer: file-logs jobs to data/print_log.txt.

    Accepts any dict (tests/demo). The real driver (qtprint.QtCitizenCLS621)
    needs the complete job/reel/detail payload and prints through the
    installed Citizen driver via Qt — no EPL/ZPL bytes anywhere."""

    def __init__(self, log_path: str | None = None):
        self.log_path = log_path or _default_log_path()

    def _log(self, kind: str, data: dict) -> bool:
        os.makedirs(os.path.dirname(self.log_path), exist_ok=True)
        with open(self.log_path, "a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{kind}] {data}\n")
        return True

    def print_reel_label(self, data: dict) -> bool:
        return self._log("REEL", data)

    def print_carton_label(self, data: dict) -> bool:
        return self._log("CARTON-QR", data)

    def print_client_label(self, data: dict) -> bool:
        return self._log("CLIENT-WIDE", data)


class MockScanner(ScannerInterface):
    def __init__(self):
        self._last = ""

    def feed(self, text: str) -> str:
        self._last = text.strip()
        return self._last

    def last_scan(self) -> str:
        return self._last


# Shared instances used by the UI (swap with real drivers later in one place)
scale = MockEssaeDS215()
printer = MockCitizenCLS621()
scanner = MockScanner()
