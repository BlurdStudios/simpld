"""Citizen CL-S621II label printing via the installed Qt printer driver.

Cross-platform by design: the Citizen printer is used through its normal
OS-installed driver (the same printer name shown in any Print dialog), via
Qt PrintSupport (QPrinter/QPainter). No EPL/ZPL command bytes are invented
or sent — the existing full-resolution label art from
factory.hardware.labels (render_reel / render_carton, 101.6 x 76.2 mm fixed
stock at 203 dpi, zero margins) is painted 1:1 onto the label page.

Data contract: pass the COMPLETE rendering inputs, not a truncated summary:
- print_reel_label({"job": job_row, "reel": reel_row})
- print_carton_label({"detail": carton_detail})  (or the detail dict itself)

The mock printer (MockCitizenCLS621) still accepts any dict and only
file-logs it, so existing demo/tests paths keep working. This real driver
rejects incomplete payloads instead of printing a fabricated label.
"""
from __future__ import annotations

from .base import PrinterInterface
from . import labels as LabelArt

LABEL_WIDTH_MM = LabelArt.LABEL_WIDTH_MM    # 101.6 (4 in fixed stock)
LABEL_HEIGHT_MM = LabelArt.LABEL_HEIGHT_MM  # 76.2  (3 in fixed stock)


def available_printers() -> list[str]:
    """Installed system printer names on this PC. [] when none / unsupported."""
    try:
        from PySide6.QtPrintSupport import QPrinterInfo
        return [p.printerName() for p in QPrinterInfo.availablePrinters()]
    except Exception:
        return []


def printer_exists(name: str) -> bool:
    return bool(name) and name in available_printers()


class QtCitizenCLS621(PrinterInterface):
    """Real Citizen CL-S621II through its installed OS printer driver.

    printer_name must exactly match an installed printer
    (see available_printers() / the Devices dialog).
    """

    def __init__(self, printer_name: str):
        name = (printer_name or "").strip()
        if not name:
            raise ValueError("Printer not configured (pick the Citizen printer in Devices)")
        self.printer_name = name

    # ----- payload handling (complete data only) -----
    @staticmethod
    def _reel_image(data: dict):
        try:
            job, reel = data["job"], data["reel"]
        except (KeyError, TypeError):
            raise ValueError(
                "Incomplete reel label data: need {'job': job, 'reel': reel}")
        for key in ("job_no", "job_name"):
            if key not in job:
                raise ValueError(f"Incomplete reel label data: job lacks {key!r}")
        for key in ("gross", "core", "net", "created_at", "roll_no"):
            if key not in reel:
                raise ValueError(f"Incomplete reel label data: reel lacks {key!r}")
        return LabelArt.render_reel(job, reel)

    @staticmethod
    def _carton_image(data: dict):
        detail = data.get("detail", data) if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            raise ValueError(
                "Incomplete box label data: need {'detail': carton_detail}")
        for key in ("job_no", "job_name", "carton_no", "bundle_no",
                    "packed_at", "total_gross", "qr"):
            if key not in detail:
                raise ValueError(
                    f"Incomplete box label data: detail lacks {key!r}")
        return LabelArt.render_carton(detail)

    # ----- Qt submission -----
    @staticmethod
    def _effective_mm(eff_layout) -> tuple[float, float]:
        """Effective page size in mm (orientation-aware)."""
        try:
            from PySide6.QtGui import QPageLayout
            full = eff_layout.fullRect(QPageLayout.Millimeter)
            return float(full.width()), float(full.height())
        except Exception:
            pass
        from PySide6.QtGui import QPageSize
        size_mm = eff_layout.pageSize().size(QPageSize.Millimeter)
        return float(size_mm.width()), float(size_mm.height())

    @staticmethod
    def _margins_mm(eff_layout) -> float:
        """Largest absolute margin in mm (0 for the required borderless layout)."""
        try:
            from PySide6.QtGui import QPageLayout
            m = eff_layout.margins(QPageLayout.Millimeter)
        except TypeError:
            m = eff_layout.margins()
        except Exception:
            return 0.0
        try:
            return max(abs(m.left()), abs(m.top()),
                       abs(m.right()), abs(m.bottom()))
        except Exception:
            return 0.0

    def _submit(self, pil_img, doc_name: str) -> bool:
        if not printer_exists(self.printer_name):
            raise RuntimeError(
                f"Printer {self.printer_name!r} not found — install the "
                f"Citizen driver and pick it in Devices")
        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QPainter, QImage
            from PySide6.QtCore import QSizeF, QRectF
            import io as _io
        except Exception as e:
            raise RuntimeError(f"Qt printing unavailable: {e}")
        try:
            buf = _io.BytesIO()
            pil_img.save(buf, format="PNG")
            qimg = QImage.fromData(buf.getvalue())
        except Exception as e:
            raise RuntimeError(f"Could not render label image: {e}")
        if qimg.isNull():
            raise RuntimeError("Could not render label image (empty)")
        try:
            from PySide6.QtGui import QPageSize, QPageLayout
            from PySide6.QtCore import QMarginsF
            printer = QPrinter(QPrinter.HighResolution)
            printer.setPrinterName(self.printer_name)
            printer.setDocName(doc_name)
            try:
                printer.setFullPage(True)
            except Exception:
                pass
            try:
                printer.setResolution(203)  # CL-S621II native resolution
            except Exception:
                pass
            # Qt rotates the custom size by the orientation (verified:
            # Landscape swaps fullRect to 76.2x101.6), so Portrait keeps the
            # defined 101.6-wide x 76.2-tall stock as-is. The effective-size
            # check below guards this against Qt/driver changes instead of
            # trusting the request.
            orient = (QPageLayout.Portrait if LABEL_WIDTH_MM >= LABEL_HEIGHT_MM
                      else QPageLayout.Landscape)
            size = QPageSize(QSizeF(LABEL_WIDTH_MM, LABEL_HEIGHT_MM),
                             QPageSize.Millimeter)
            layout = QPageLayout(size, orient, QMarginsF(0, 0, 0, 0))
            printer.setPageLayout(layout)
            eff = printer.pageLayout()
            ew, eh = self._effective_mm(eff)
            if abs(ew - LABEL_WIDTH_MM) > 1.0 or abs(eh - LABEL_HEIGHT_MM) > 1.0:
                raise RuntimeError(
                    f"Printer {self.printer_name!r} reports a "
                    f"{ew:.1f}x{eh:.1f}mm page, not the 101.6x76.2mm stock — "
                    f"set a 4x3in custom paper size in the Citizen driver")
            if self._margins_mm(eff) > 0.25:
                raise RuntimeError(
                    f"Printer {self.printer_name!r} enforces non-zero margins — "
                    f"enable borderless/full-bleed (zero margins) in the driver")
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Could not open printer {self.printer_name!r}: {e}")
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError(
                f"Printer {self.printer_name!r} refused the job — check it is "
                f"online with 4x3in stock loaded")
        try:
            # Paint 1:1 onto the fixed 101.6x76.2mm stock at the printer's
            # own resolution, origin (0,0), zero margins by construction.
            res = int(printer.resolution() or 203)
            w_px = int(LABEL_WIDTH_MM / 25.4 * res)
            h_px = int(LABEL_HEIGHT_MM / 25.4 * res)
            try:
                pr = printer.pageRect(QPrinter.DevicePixel)
                if pr.width() < w_px or pr.height() < h_px:
                    raise RuntimeError(
                        f"Printer {self.printer_name!r} printable area is "
                        f"{pr.width()}x{pr.height()}px < label {w_px}x{h_px}px — "
                        f"check the 4x3in stock and borderless setting "
                        f"(refusing to clip)")
            except RuntimeError:
                raise
            except Exception:
                pass
            painter.drawImage(QRectF(0, 0, w_px, h_px), qimg)
        except Exception as e:
            try:
                painter.end()
            except Exception:
                pass
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Label print failed on {self.printer_name!r}: {e}")
        if not painter.end():
            raise RuntimeError(
                f"Label print failed on {self.printer_name!r} (spooler error)")
        return True

    def print_reel_label(self, data: dict) -> bool:
        return self._submit(self._reel_image(data), "Simpld reel sticker")

    def print_carton_label(self, data: dict) -> bool:
        return self._submit(self._carton_image(data), "Simpld box sticker")
