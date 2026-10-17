"""Hardware interfaces. Real devices plug in here later without UI changes.

Current factory hardware (from photos):
- Scale: Essae DS-215 platform scale (RS232 serial, kg)
- Printer: Citizen CL-S621II thermal label printer
- Scanner: USB QR/barcode scanner (keyboard wedge + Enter)
"""
from __future__ import annotations
from abc import ABC, abstractmethod


class ScaleInterface(ABC):
    @abstractmethod
    def read_weight(self) -> float:
        """Return live gross weight in kg."""

    @abstractmethod
    def freeze(self) -> float:
        """STOP key: freeze current reading (old software 'Stop Wt')."""


class PrinterInterface(ABC):
    @abstractmethod
    def print_reel_label(self, data: dict) -> bool:
        """Type-1 sticker: single reel, NO QR.

        Pass COMPLETE rendering inputs: {"job": job, "reel": reel}."""

    @abstractmethod
    def print_carton_label(self, data: dict) -> bool:
        """Type-2 sticker: carton/bundle WITH QR.

        Pass COMPLETE rendering inputs: {"detail": carton_detail}."""


class ScannerInterface(ABC):
    """USB scanners type the code + Enter. UI just needs a focused QLineEdit.
    This interface exists so a serial-scanner variant can be added later."""
    @abstractmethod
    def last_scan(self) -> str:
        ...
