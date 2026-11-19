"""Label printing over VCOM — serial on Windows, raw USB on Linux.

The Citizen CL-S621 in VCOM mode appears as a COM port on Windows (via the
Citizen VCOM driver). On Linux it presents a vendor-specific bulk interface
and NEVER creates a tty node, so the bytes go straight over USB bulk OUT
(the same sequence the proven tools/cups/citizenusb CUPS backend uses).
This module only moves bytes. The label command language is NOT defined
here: it must match the printer's emulation (Datamax/DPL or Zebra/ZPL),
which is read from the printer's self-test page. Until that is configured,
every print raises, so nothing is ever sent as a guessed command.
"""
from __future__ import annotations

import logging as _logging
import os

from .base import PrinterInterface

_log = _logging.getLogger("simpld.hardware")

VCOM_BAUD = 9600
WRITE_TIMEOUT_S = 5.0

# Confirmed on this CL-S621 by a ZPL test label printing: ZPL.
EMULATION: str | None = "ZPL"


CITIZEN_VID = 0x2730
CITIZEN_PID = 0x0FFF

# Raw-USB transport (Linux): this printer presents a vendor-specific bulk
# interface (ff/ff/ff) and NEVER creates a tty node, so serial VCOM is
# Windows-only. On Linux the bytes go straight over bulk OUT 0x02 — the
# exact sequence tools/cups/citizenusb (CUPS backend) uses in production.
USB_IFACE = 0
USB_EP_OUT = 0x02
USB_WRITE_TIMEOUT_MS = 10000
USB_CHUNK = 16384


def match_citizen_port(ports) -> str | None:
    """Pick the Citizen's COM port from an already-enumerated port list.

    Matches VID/PID first, then the Citizen description. Returns None when
    no Citizen COM port is in the list, and never guesses when more than
    one is. Pure filter — no enumeration, so callers can use a cached list.
    """
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


def find_vcom_port() -> str | None:
    """The Citizen's COM port by USB ID, wherever it is plugged in."""
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception as e:
        _log.warning("vcom: port enumeration failed (%s)", e)
        return None
    return match_citizen_port(ports)


# ---- raw-USB transport (Linux) ---------------------------------------------
# The Citizen never exposes a serial node on Linux (interface 0 is
# ff/ff/ff vendor bulk), so "VCOM mode" there means: write the bytes
# straight to bulk OUT 0x02 over libusb. Sequence mirrors the proven
# tools/cups/citizenusb CUPS backend. Windows keeps the real COM port
# from the Citizen VCOM driver — serial path, unchanged.
_USB_CACHE: dict = {"at": 0.0, "present": False}
_USB_TTL_S = 10.0


def citizen_usb_present() -> bool:
    """A Citizen attached over raw USB right now? Cached 10s so the 5s
    pill poll never scans the bus each tick."""
    import time
    now = time.monotonic()
    if now - float(_USB_CACHE.get("at", 0.0)) < _USB_TTL_S:
        return bool(_USB_CACHE.get("present"))
    present = False
    try:
        import usb.core
        dev = usb.core.find(idVendor=CITIZEN_VID, idProduct=CITIZEN_PID)
        present = dev is not None
    except Exception as e:
        _log.warning("vcom: USB probe failed (%s)", e)
        present = False
    _USB_CACHE["at"] = now
    _USB_CACHE["present"] = present
    return present


def _usb_node_path(dev) -> str:
    """usbfs node for a PyUSB device ('/dev/bus/usb/BBB/DDD'), or ''."""
    try:
        return "/dev/bus/usb/%03d/%03d" % (int(dev.bus), int(dev.address))
    except Exception:
        return ""


def _usb_permission_message(node: str = "") -> str:
    """The real fix for usbfs EACCES: the node is root:lp, the user is not.

    CUPS keeps working in this state because its backend runs as root, so
    "print via the queue works but the app's own print does not" is the
    signature of this cause — never a broken queue.
    """
    where = f" ({node})" if node else ""
    return (
        f"No permission to open the label printer over USB{where} — the "
        "device belongs to root:lp and your user is not in the 'lp' group.\n"
        "Fix it once:\n"
        "    sudo usermod -aG lp $USER\n"
        "Then log out / back in, or relaunch the app from a `newgrp lp` "
        "shell — a running app started before the change keeps the old "
        "groups.")


def _claim_error_message(e: BaseException, node: str = "") -> str:
    """Operator-facing reason for a failed interface claim.

    libusb raises USBError with a usbfs errno, NOT PermissionError: 13 is
    permission denied (root:lp node), 16 is a busy interface (usblp or a
    running CUPS backend). Naming the actual cause matters — telling an
    operator about a "queue" when the node is unwritable sends them to the
    wrong dialog.
    """
    errno = getattr(e, "errno", None)
    if errno is None and getattr(e, "backend_error_code", None) == -3:
        errno = 13  # libusb LIBUSB_ERROR_ACCESS
    if errno == 13:
        return _usb_permission_message(node)
    if errno == 16:
        return ("The label printer's USB interface is busy — a CUPS job or "
                "the usblp kernel driver is holding it. Let the queue drain "
                f"(lpstat -o) and print again. ({e})")
    return ("Could not claim the printer's USB interface — another print "
            f"queue or driver may be holding it. ({e})")


def citizen_usb_send(data: bytes) -> None:
    """Write label bytes to the Citizen over raw USB (Linux path).

    Raises RuntimeError with an operator-actionable message — the caller
    surfaces it and the label stays pending, never half-sent as success.
    """
    try:
        import usb.core
        import usb.util
    except Exception as e:
        raise RuntimeError(
            f"PyUSB missing — without a serial port the label printer is "
            f"driven over raw USB: install pyusb ({e})") from e
    try:
        dev = usb.core.find(idVendor=CITIZEN_VID, idProduct=CITIZEN_PID)
    except Exception as e:
        raise RuntimeError(f"USB bus probe failed: {e}") from e
    if dev is None:
        raise RuntimeError(
            "Label printer not found on USB — connect the Citizen and try again.")
    node = _usb_node_path(dev)
    detached = False
    try:
        # Root bypasses the node mode, and os.access uses the REAL uid, so
        # an effective-root process must not be told it lacks permission.
        if (node and hasattr(os, "geteuid") and os.geteuid() != 0
                and os.path.exists(node) and not os.access(node, os.W_OK)):
            raise RuntimeError(_usb_permission_message(node))
        # A kernel driver (usblp/hid) on interface 0 must step aside for
        # the print; it is re-attached in the finally block below.
        try:
            if dev.is_kernel_driver_active(USB_IFACE):
                dev.detach_kernel_driver(USB_IFACE)
                detached = True
        except Exception:
            pass
        try:
            dev.set_configuration()
        except Exception:
            pass
        try:
            usb.util.claim_interface(dev, USB_IFACE)
        except Exception as e:
            raise RuntimeError(_claim_error_message(e, node)) from e
        view = memoryview(data)
        for off in range(0, len(view), USB_CHUNK):
            dev.write(USB_EP_OUT, view[off:off + USB_CHUNK].tobytes(),
                      timeout=USB_WRITE_TIMEOUT_MS)
    except RuntimeError:
        raise
    except PermissionError as e:
        raise RuntimeError(_usb_permission_message(node)) from e
    except Exception as e:
        raise RuntimeError(f"USB write to the Citizen failed: {e}") from e
    finally:
        try:
            usb.util.release_interface(dev, USB_IFACE)
        except Exception:
            pass
        try:
            usb.util.dispose_resources(dev)
        except Exception:
            pass
        if detached:
            try:
                dev.attach_kernel_driver(USB_IFACE)
            except Exception:
                pass


def send_bytes(port: str, data: bytes, baud: int = VCOM_BAUD) -> None:
    """Write raw label bytes to the printer and wait for them to leave.

    Transport: an explicit port means serial VCOM (Windows COM port from
    the Citizen driver, or a pinned node). An EMPTY port means the
    zero-config path — on Linux there is no serial node at all, so the
    bytes go over raw USB bulk (same as the CUPS backend).
    """
    if not port:
        citizen_usb_send(data)
        return
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


def _load_layout(template_id: str) -> dict:
    """Layout dict for one template from the settings table, defaults when unavailable."""
    try:
        from . import layout as _layout
        from factory import services as _svc
        keys = ["stock_w_mm", "stock_h_mm", "dpi", "content_x_mm",
                "content_y_mm", "content_w_mm", "content_h_mm",
                "margin_locked", "units", "fit_mode"]
        tid = str(template_id or "").strip().lower() or "reel"
        settings = {}
        for k in keys:
            try:
                settings[f"label.{tid}.{k}"] = _svc.get_setting(
                    f"label.{tid}.{k}", "")
            except Exception:
                settings[f"label.{tid}.{k}"] = ""
        return _layout.get_layout(tid, settings)
    except Exception:
        from . import layout as _layout
        tid = str(template_id or "").strip().lower()
        if tid not in _layout.DEFAULTS:
            tid = "reel"
        d = dict(_layout.DEFAULTS[tid])
        d["template_id"] = tid
        return d


def _reel_image(data: dict, layout: dict | None = None):
    from . import labels as LabelArt
    try:
        job, reel = data["job"], data["reel"]
    except (KeyError, TypeError):
        raise ValueError("Incomplete reel label data: need {'job': job, 'reel': reel}")
    for key in ("job_no", "job_name"):
        if key not in job:
            raise ValueError(f"Incomplete reel label data: job lacks {key!r}")
    for key in ("gross", "core", "net", "created_at", "roll_no"):
        if key not in reel:
            raise ValueError(f"Incomplete reel label data: reel lacks {key!r}")
    lay = layout or _load_layout("reel")
    return LabelArt.render_reel(
        job, reel, width_mm=float(lay["content_w_mm"]),
        height_mm=float(lay["content_h_mm"]), dpi=int(lay["dpi"]))


def _carton_image(data: dict, layout: dict | None = None):
    from . import labels as LabelArt
    detail = data.get("detail", data) if isinstance(data, dict) else None
    if not isinstance(detail, dict):
        raise ValueError("Incomplete box label data: need {'detail': carton_detail}")
    for key in ("job_no", "job_name", "carton_no", "bundle_no",
                "packed_at", "total_gross", "qr"):
        if key not in detail:
            raise ValueError(f"Incomplete box label data: detail lacks {key!r}")
    lay = layout or _load_layout("carton")
    return LabelArt.render_carton(
        detail, width_mm=float(lay["content_w_mm"]),
        height_mm=float(lay["content_h_mm"]), dpi=int(lay["dpi"]))


def _client_image(data: dict, layout: dict | None = None):
    from . import labels as LabelArt
    detail = data.get("detail", data) if isinstance(data, dict) else None
    if not isinstance(detail, dict):
        raise ValueError("Incomplete client label data: need {'detail': client_label_detail}")
    for key in ("customer", "packed_at", "client_box_no", "coils"):
        if key not in detail:
            raise ValueError(f"Incomplete client label data: detail lacks {key!r}")
    lay = layout or _load_layout("client")
    return LabelArt.render_client_box(
        detail, width_mm=float(lay["content_w_mm"]), dpi=int(lay["dpi"]))


class VcomCitizen(PrinterInterface):
    """Label printer on a VCOM port, sending ZPL bitmaps (Citizen CL-S621)."""

    def __init__(self, port: str = "", baud: int = VCOM_BAUD):
        self.port = str(port or "").strip()
        self.baud = int(baud or VCOM_BAUD)

    def _target(self) -> str:
        """Serial port if pinned or found by USB ID, else "" — an empty
        target is valid: send_bytes routes it to the raw-USB transport
        (Linux never shows a serial node for this printer)."""
        return self.port or find_vcom_port() or ""

    def _require_language(self) -> None:
        if EMULATION not in ("DPL", "ZPL"):
            raise RuntimeError(
                "VCOM printing needs the printer language confirmed first "
                "(self-test page: DPL or ZPL). Nothing was sent.")

    def _print(self, template_id: str, image, data_label: str) -> bool:
        from . import layout as _layout
        self._require_language()
        port = self._target()
        lay = _load_layout(template_id)
        dpi = int(lay.get("dpi") or 203)
        stock_w = round(float(lay["stock_w_mm"]) / 25.4 * dpi)
        stock_h = round(float(lay["stock_h_mm"]) / 25.4 * dpi)
        ox, oy = _layout.content_origin_px(lay)
        # port "" → raw-USB bulk write; a missing printer raises there with
        # an actionable message (never "set the port in Settings" — there
        # is no printer settings UI).
        send_bytes(port, zpl_bitmap(image, ox, oy, stock_w, stock_h), self.baud)
        _log.info("vcom: printed %s (%s) on %s", template_id, data_label,
                  port or "USB")
        return True

    def print_reel_label(self, data: dict) -> bool:
        lay = _load_layout("reel")
        return self._print("reel", _reel_image(data, lay), "reel")

    def print_carton_label(self, data: dict) -> bool:
        lay = _load_layout("carton")
        return self._print("carton", _carton_image(data, lay), "carton")

    def print_client_label(self, data: dict) -> bool:
        lay = _load_layout("client")
        return self._print("client", _client_image(data, lay), "client")
