"""Label printing over a VCOM (serial) port.

The Citizen CL-S621 in VCOM mode appears as a COM port (Windows: via the
Citizen VCOM driver; Linux: no COM node, see tools/cups/citizenusb). This
module only moves bytes. The label command language is NOT defined here:
it must match the printer's emulation (Datamax/DPL or Zebra/ZPL), which is
read from the printer's self-test page. Until that is configured, every
print raises, so nothing is ever sent as a guessed command.
"""
from __future__ import annotations

import logging as _logging

from .base import PrinterInterface

_log = _logging.getLogger("simpld.hardware")

VCOM_BAUD = 9600
WRITE_TIMEOUT_S = 5.0

# Set to "DPL" or "ZPL" after the self-test page confirms the emulation.
EMULATION: str | None = None


CITIZEN_VID = 0x2730
CITIZEN_PID = 0x0FFF


def find_vcom_port() -> str | None:
    """The Citizen's COM port by USB ID, wherever it is plugged in.

    Matches VID/PID first, then the Citizen description. Returns None when
    no Citizen COM port exists, and never guesses when more than one does.
    """
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception as e:
        _log.warning("vcom: port enumeration failed (%s)", e)
        return None
    hits = [p.device for p in ports
            if (p.vid, p.pid) == (CITIZEN_VID, CITIZEN_PID)]
    if not hits:
        hits = [p.device for p in ports
                if "citizen" in (p.description or "").lower()]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        _log.warning("vcom: %d Citizen COM ports %r — not guessing", len(hits), hits)
    return None


def send_bytes(port: str, data: bytes, baud: int = VCOM_BAUD) -> None:
    """Write raw bytes to a COM port and wait for them to leave the PC."""
    if not port:
        raise RuntimeError("VCOM port not configured")
    try:
        import serial
    except Exception as e:
        raise RuntimeError(f"pyserial missing: {e}")
    try:
        with serial.Serial(port, baudrate=int(baud), timeout=WRITE_TIMEOUT_S,
                           write_timeout=WRITE_TIMEOUT_S) as ser:
            ser.write(data)
            ser.flush()
    except Exception as e:
        raise RuntimeError(f"VCOM write to {port} failed: {e}")


class VcomCitizen(PrinterInterface):
    """Label printer on a VCOM port. Fails closed until the language is set."""

    def __init__(self, port: str = "", baud: int = VCOM_BAUD):
        self.port = str(port or "").strip()
        self.baud = int(baud or VCOM_BAUD)

    def _target(self) -> str:
        """Configured port if given, else whatever port the USB ID finds now."""
        return self.port or find_vcom_port() or ""

    def _require_language(self) -> None:
        if EMULATION not in ("DPL", "ZPL"):
            raise RuntimeError(
                "VCOM printing needs the printer language confirmed first "
                "(self-test page: DPL or ZPL). Nothing was sent.")

    def print_reel_label(self, data: dict) -> bool:
        self._require_language()
        raise NotImplementedError("reel label renderer not written yet")

    def print_carton_label(self, data: dict) -> bool:
        self._require_language()
        raise NotImplementedError("box label renderer not written yet")

    def print_client_label(self, data: dict) -> bool:
        self._require_language()
        raise NotImplementedError("client label renderer not written yet")
