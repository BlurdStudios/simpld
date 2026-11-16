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

# Confirmed on this CL-S621 by a ZPL test label printing: ZPL.
EMULATION: str | None = "ZPL"


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


def zpl_bitmap(pil_img, x: int, y: int, stock_w_dots: int,
               stock_h_dots: int) -> bytes:
    """One ZPL job that draws a 1-bit bitmap at (x, y) on a stock_w x stock_h page."""
    from PIL import ImageOps
    mono = ImageOps.invert(pil_img.convert("L")).convert("1")
    row_bytes = (mono.width + 7) // 8
    raw = mono.tobytes()
    total = row_bytes * mono.height
    hexdata = raw.hex().upper()
    job = (
        "^XA"
        f"^PW{stock_w_dots}^LL{stock_h_dots}^LH0,0"
        f"^FO{x},{y}"
        f"^GFA,{total},{total},{row_bytes},{hexdata}"
        "^FS"
        "^XZ"
    )
    return job.encode("ascii")


class VcomCitizen(PrinterInterface):
    """Label printer on a VCOM port, sending ZPL bitmaps (Citizen CL-S621)."""

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

    def _print(self, template_id: str, image, data_label: str) -> bool:
        from .qtprint import _load_layout
        from . import layout as _layout
        self._require_language()
        port = self._target()
        if not port:
            raise RuntimeError("VCOM port not found. Set the printer port in Settings.")
        lay = _load_layout(template_id)
        dpi = int(lay.get("dpi") or 203)
        stock_w = round(float(lay["stock_w_mm"]) / 25.4 * dpi)
        stock_h = round(float(lay["stock_h_mm"]) / 25.4 * dpi)
        ox, oy = _layout.content_origin_px(lay)
        send_bytes(port, zpl_bitmap(image, ox, oy, stock_w, stock_h), self.baud)
        _log.info("vcom: printed %s (%s) on %s", template_id, data_label, port)
        return True

    def print_reel_label(self, data: dict) -> bool:
        from .qtprint import QtCitizenCLS621, _load_layout
        lay = _load_layout("reel")
        return self._print("reel", QtCitizenCLS621._reel_image(data, lay), "reel")

    def print_carton_label(self, data: dict) -> bool:
        from .qtprint import QtCitizenCLS621, _load_layout
        lay = _load_layout("carton")
        return self._print("carton", QtCitizenCLS621._carton_image(data, lay), "carton")

    def print_client_label(self, data: dict) -> bool:
        from .qtprint import QtCitizenCLS621, _load_layout
        lay = _load_layout("client")
        return self._print("client", QtCitizenCLS621._client_image(data, lay), "client")
