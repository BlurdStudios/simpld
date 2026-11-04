"""Citizen CL-S621II label printing via the installed Qt printer driver.

Cross-platform by design: the Citizen printer is used through its normal
OS-installed driver (the same printer name shown in any Print dialog), via
Qt PrintSupport (QPrinter/QPainter). No EPL/ZPL command bytes are invented
or sent — the existing full-resolution label art from
factory.hardware.labels (render_reel / render_carton, 100 x 50 mm fixed
stock at 203 dpi) is fitted to the driver's printable rect. The CL-S621
hardware enforces ~2.50mm left / ~1.38mm right unprintable area with no
borderless option, so the art scales to the printable width (800 dots fit
into 769) instead of being refused or clipped; wrong stock (tens of mm
off) still fails via the ±1.0mm page-size check.

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

import logging as _logging

LABEL_WIDTH_MM = LabelArt.LABEL_WIDTH_MM    # 100 (fixed 100x50 stock)
LABEL_HEIGHT_MM = LabelArt.LABEL_HEIGHT_MM  # 50  (fixed 100x50 stock)

# Citizen CL-S621 @203dpi cannot hit 100.0x50.0mm exactly: 100mm =
# 799.21 dots, 50mm = 399.61 dots, and the driver rounds UP to 800x400
# dots = 100.10x50.05mm (20-dot/2.50mm left origin + 11-dot/1.38mm right
# margin are hardware unprintable, not a user setting — there is no
# borderless option). Size checks therefore use a ±1.0mm tolerance and
# the page is fit to the printable rect instead of refused.
SIZE_TOL_MM = 1.0
# A stale Seagull USER form (101.6x152.4mm + margins) surfaces as a
# 104.1x152.4mm ticket while GDI already reports 100.10x50.05. Detect it
# specifically so the error points at the registry fix instead of stock.
_STALE_W_MM = 104.1
_STALE_H_MM = 152.4

_log = _logging.getLogger("simpld.hardware")


def available_printers() -> list[str]:
    """Installed system printer names on this PC. [] when none / unsupported."""
    try:
        from PySide6.QtPrintSupport import QPrinterInfo
        return [p.printerName() for p in QPrinterInfo.availablePrinters()]
    except Exception:
        # Frozen Windows builds have no visible stderr (console=False), so a
        # silent [] here used to mean "printer not detected" with no trace.
        # Log it — detection outcome logging in autodetect adds the names.
        _log.warning("available_printers() failed — reporting no printers",
                     exc_info=True)
        return []


def printer_exists(name: str) -> bool:
    return bool(name) and name in available_printers()


class QtCitizenCLS621(PrinterInterface):
    """Real label printer through its installed OS driver.

    Used for the 100x50 Citizen on packaging and the wide client-sticker
    printer on dispatch. printer_name must exactly match an installed
    printer (see available_printers() / the Devices dialog).
    """

    def __init__(self, printer_name: str):
        name = (printer_name or "").strip()
        if not name:
            raise ValueError("Printer not configured (pick the label printer in Devices)")
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

    @staticmethod
    def _client_image(data: dict):
        detail = data.get("detail", data) if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            raise ValueError(
                "Incomplete client label data: need {'detail': client_label_detail}")
        for key in ("customer", "packed_at", "client_box_no", "coils"):
            if key not in detail:
                raise ValueError(
                    f"Incomplete client label data: detail lacks {key!r}")
        return LabelArt.render_client_box(detail)

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
        """Largest absolute margin in mm (informational only).

        The CL-S621 enforces ~2.50mm left / ~1.38mm right hardware
        unprintable area with no borderless option, so this is logged and
        fitted — never a fatal error.
        """
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

    @staticmethod
    def _quantized_mm(width_mm: float, height_mm: float,
                      dpi: int) -> tuple[float, float]:
        """Dot-quantized request size (driver rounds UP to whole dots).

        100.0mm @203dpi = 799.21 dots -> driver reports 800 dots =
        100.10mm; 50.0mm -> 400 dots = 50.05mm. Requesting the quantized
        size makes the printed page match bit-for-bit instead of relying
        on the driver to round our fractional request.
        """
        import math
        try:
            wd = math.ceil(float(width_mm) / 25.4 * int(dpi) - 1e-9)
            hd = math.ceil(float(height_mm) / 25.4 * int(dpi) - 1e-9)
            return wd / int(dpi) * 25.4, hd / int(dpi) * 25.4
        except Exception:
            return float(width_mm), float(height_mm)

    def _submit(self, pil_img, doc_name: str,
                width_mm: float = LABEL_WIDTH_MM,
                height_mm: float = LABEL_HEIGHT_MM,
                dpi: int = 203) -> bool:
        if not printer_exists(self.printer_name):
            raise RuntimeError(
                f"Printer {self.printer_name!r} not found — install its "
                f"driver and pick it in Devices")
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
                # Printable-local origin: (0,0) is the printable top-left,
                # so the 2.50mm hardware left origin is handled by Qt and
                # the label is never clipped by a paper-edge offset.
                printer.setFullPage(False)
            except Exception:
                pass
            try:
                printer.setResolution(int(dpi))
            except Exception:
                pass
            # Qt rotates the custom size by the orientation (verified:
            # Landscape swaps fullRect). A wide stock (width >= height) stays
            # Portrait as defined; the effective-size check guards this
            # against Qt/driver changes instead of trusting the request.
            orient = (QPageLayout.Portrait if width_mm >= height_mm
                      else QPageLayout.Landscape)
            q_w, q_h = self._quantized_mm(width_mm, height_mm, dpi)
            size = QPageSize(QSizeF(q_w, q_h),
                             QPageSize.Millimeter)
            layout = QPageLayout(size, orient, QMarginsF(0, 0, 0, 0))
            printer.setPageLayout(layout)
            eff = printer.pageLayout()
            ew, eh = self._effective_mm(eff)
            if abs(ew - width_mm) > SIZE_TOL_MM or abs(eh - height_mm) > SIZE_TOL_MM:
                if (abs(ew - _STALE_W_MM) < 2.0 and abs(eh - _STALE_H_MM) < 2.0):
                    raise RuntimeError(
                        f"Printer {self.printer_name!r} reports a "
                        f"{ew:.1f}x{eh:.1f}mm page, not the "
                        f"{width_mm:.1f}x{height_mm:.1f}mm stock — the "
                        f"driver's USER form is stale (still 101.6x152.4mm "
                        f"with margins, surfacing as 104.1x152.4mm). Update "
                        f"'User Form: Data' to 100000x50000um with 0 "
                        f"margins, then re-check")
                raise RuntimeError(
                    f"Printer {self.printer_name!r} reports a "
                    f"{ew:.1f}x{eh:.1f}mm page, not the "
                    f"{width_mm:.1f}x{height_mm:.1f}mm stock — set that exact "
                    f"custom paper size in the printer driver")
            # Hardware unprintable area (CL-S621: ~2.50mm left origin,
            # ~1.38mm right) is normal — there is no borderless option.
            # Warn at most; the paint step below fits to printable.
            try:
                mg = self._margins_mm(eff)
            except Exception:
                mg = 0.0
            if mg > 0.25:
                _log.warning(
                    "Printer %r reports %.2fmm margins "
                    "(hardware unprintable, e.g. CL-S621 2.50mm left origin) "
                    "— fitting the label to the printable area",
                    self.printer_name, mg)
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Could not open printer {self.printer_name!r}: {e}")
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError(
                f"Printer {self.printer_name!r} refused the job — check it is "
                f"online with the right label stock loaded")
        try:
            # Fit the full label art to the printable rect (printable-local
            # origin, so the hardware left offset needs no manual shift).
            # Zero-margin drivers stay 1:1; the CL-S621 squeezes 800 dots
            # into its 769-dot printable width (~3.8%) instead of clipping
            # the border — the size check above still rejects wrong stock
            # (tens of mm off), this only absorbs hardware unprintable.
            res = int(printer.resolution() or dpi)
            try:
                pr = printer.pageRect(QPrinter.DevicePixel)
                pw, ph = int(pr.width()), int(pr.height())
            except Exception:
                pw = ph = 0
            if pw <= 0 or ph <= 0:
                w_px = int(round(q_w / 25.4 * res))
                h_px = int(round(q_h / 25.4 * res))
                painter.drawImage(QRectF(0, 0, w_px, h_px), qimg)
            else:
                w_px = int(round(q_w / 25.4 * res))
                h_px = int(round(q_h / 25.4 * res))
                if abs(pw - w_px) > max(4, int(round(0.5 / 25.4 * res))) or \
                        abs(ph - h_px) > max(4, int(round(0.5 / 25.4 * res))):
                    _log.info(
                        "Printer %r printable %dx%dpx vs label %dx%dpx — "
                        "scaling art to printable (hardware margins)",
                        self.printer_name, pw, ph, w_px, h_px)
                painter.drawImage(QRectF(0, 0, pw, ph), qimg)
        except Exception as e:
            try:
                painter.end()
            except Exception:
                pass
            if isinstance(e, RuntimeError):
                raise
            raise RuntimeError(f"Label print failed on {self.printer_name!r}: {e}")
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
        return self._submit(self._reel_image(data), "Simpld reel sticker",
                            width_mm=LABEL_WIDTH_MM,
                            height_mm=LABEL_HEIGHT_MM, dpi=203)

    def print_carton_label(self, data: dict) -> bool:
        return self._submit(self._carton_image(data), "Simpld box sticker",
                            width_mm=LABEL_WIDTH_MM,
                            height_mm=LABEL_HEIGHT_MM, dpi=203)

    def print_client_label(self, data: dict) -> bool:
        img = self._client_image(data)
        detail = data.get("detail", data) if isinstance(data, dict) else {}
        width_mm = float(detail.get("width_mm") or LabelArt.WIDE_WIDTH_MM)
        dpi = int(detail.get("dpi") or LabelArt.WIDE_DPI)
        height_mm = float(detail.get("height_mm")
                          or (img.height / dpi * 25.4))
        return self._submit(img, "Simpld client box sticker",
                            width_mm=width_mm, height_mm=height_mm, dpi=dpi)
