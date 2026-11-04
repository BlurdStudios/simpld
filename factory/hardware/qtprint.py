"""Label printing through the installed OS driver via Qt (QPrinter/QPainter).

No EPL/ZPL command bytes are invented or sent — the label art from
factory.hardware.labels is drawn 1:1 at the layout content-box origin.
Geometry is never hardcoded in the draw path: the stock/content sizes
come from the layout store (factory.hardware.layout, settings table,
user-editable) and the printable origin/size/DPI are probed from the
driver on every job.

Rules (Label Layout Editor brief):
- No scaling, ever. The page width the driver reports must never feed
  the draw transform; the content bitmap is drawn at its own pixel size
  at the content origin (the driver's own origin is handled by GDI).
- No hard page-size exception. The driver's reported page is the paper
  plus its unprintable origin (100.0 + 2.5 = 102.5), so the ticket
  subtracts the probed origin (ticket = label − origin) and the report
  comes back at the stock size; if the repair does not stick, the job
  still prints 1:1 and the state is logged as repaired/proceed.
- The only print-time failure is a genuine fit failure (content outside
  the printable area), reported as a millimetre deficit.
- Preview and print share the same render: callers render the content
  box at content_w*content_h px and pass the bitmap in.

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

LABEL_WIDTH_MM = LabelArt.LABEL_WIDTH_MM    # legacy fallback only
LABEL_HEIGHT_MM = LabelArt.LABEL_HEIGHT_MM  # (geometry now comes from layout)

# Agreement threshold: a driver ticket within this of the wanted stock size
# counts as OK. Anything further is repaired once (ticket = label − origin,
# the probed unprintable offset) and, if it still does not stick, printed
# 1:1 anyway with state=repaired/proceed (never a hard fail).
PAPER_TOL_MM = 0.3

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


def _now_iso() -> str:
    try:
        from factory.db import now_iso
        return now_iso()
    except Exception:
        import datetime as _dt
        return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ----- layout loading (settings table, defaults when unavailable) -----

def _load_layout(template_id: str) -> dict:
    """Layout dict for one template; defaults when the DB is unreachable.

    Tests and offline paths must still print, so any settings failure
    falls back to the template defaults (user-editable starting values,
    not draw-path constants).
    """
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
        try:
            from . import layout as _layout
            tid = str(template_id or "").strip().lower()
            if tid not in _layout.DEFAULTS:
                tid = "reel"
            d = dict(_layout.DEFAULTS[tid])
            d["template_id"] = tid
            return d
        except Exception:
            return {"template_id": "reel", "stock_w_mm": 100.0,
                    "stock_h_mm": 50.0, "dpi": 203, "content_x_mm": 3.0,
                    "content_y_mm": 3.0, "content_w_mm": 94.0,
                    "content_h_mm": 44.0, "margin_locked": True,
                    "units": "mm", "fit_mode": "reflow"}


def _save_probe_cache(printer_name: str, probed: dict) -> None:
    try:
        from . import layout as _layout
        from factory import services as _svc
        for k, v in _layout.printable_to_settings(
                str(printer_name or ""), probed).items():
            try:
                _svc.set_setting(k, str(v))
            except Exception:
                pass
    except Exception:
        pass


def _load_probe_cache(printer_name: str) -> dict | None:
    try:
        from . import layout as _layout
        from factory import services as _svc
        settings = {}
        for k in ("printable_origin_x_mm", "printable_origin_y_mm",
                  "printable_w_mm", "printable_h_mm", "ticket_delta_mm",
                  "dpi", "probed_at"):
            try:
                settings[f"printer.{printer_name}.{k}"] = _svc.get_setting(
                    f"printer.{printer_name}.{k}", "")
            except Exception:
                settings[f"printer.{printer_name}.{k}"] = ""
        return _layout.get_printable(str(printer_name or ""), settings)
    except Exception:
        return None


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
    def _reel_image(data: dict, layout: dict | None = None):
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
        lay = layout or _load_layout("reel")
        return LabelArt.render_reel(
            job, reel, width_mm=float(lay["content_w_mm"]),
            height_mm=float(lay["content_h_mm"]), dpi=int(lay["dpi"]))

    @staticmethod
    def _carton_image(data: dict, layout: dict | None = None):
        detail = data.get("detail", data) if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            raise ValueError(
                "Incomplete box label data: need {'detail': carton_detail}")
        for key in ("job_no", "job_name", "carton_no", "bundle_no",
                    "packed_at", "total_gross", "qr"):
            if key not in detail:
                raise ValueError(
                    f"Incomplete box label data: detail lacks {key!r}")
        lay = layout or _load_layout("carton")
        return LabelArt.render_carton(
            detail, width_mm=float(lay["content_w_mm"]),
            height_mm=float(lay["content_h_mm"]), dpi=int(lay["dpi"]))

    @staticmethod
    def _client_image(data: dict, layout: dict | None = None):
        detail = data.get("detail", data) if isinstance(data, dict) else None
        if not isinstance(detail, dict):
            raise ValueError(
                "Incomplete client label data: need {'detail': client_label_detail}")
        for key in ("customer", "packed_at", "client_box_no", "coils"):
            if key not in detail:
                raise ValueError(
                    f"Incomplete client label data: detail lacks {key!r}")
        lay = layout or _load_layout("client")
        # Content width from the layout; height follows the coil count
        # (row-driven art, drawn 1:1 at the content origin).
        return LabelArt.render_client_box(
            detail, width_mm=float(lay["content_w_mm"]),
            dpi=int(lay["dpi"]))

    # ----- driver probing (no hardcoded geometry) -----
    @staticmethod
    def _effective_mm(eff_layout) -> tuple[float, float]:
        """Effective ticket size in mm (orientation-aware)."""
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
    def _margins_each(eff_layout) -> tuple[float, float, float, float]:
        """(left, top, right, bottom) unprintable margins in mm."""
        try:
            from PySide6.QtGui import QPageLayout
            m = eff_layout.margins(QPageLayout.Millimeter)
        except TypeError:
            m = eff_layout.margins()
        except Exception:
            return 0.0, 0.0, 0.0, 0.0
        try:
            return (float(m.left()), float(m.top()),
                    float(m.right()), float(m.bottom()))
        except Exception:
            return 0.0, 0.0, 0.0, 0.0

    @staticmethod
    def _quantized_mm(width_mm: float, height_mm: float,
                      dpi: int) -> tuple[float, float]:
        """Dot-quantized request size (driver rounds UP to whole dots)."""
        import math
        try:
            wd = math.ceil(float(width_mm) / 25.4 * int(dpi) - 1e-9)
            hd = math.ceil(float(height_mm) / 25.4 * int(dpi) - 1e-9)
            return wd / int(dpi) * 25.4, hd / int(dpi) * 25.4
        except Exception:
            return float(width_mm), float(height_mm)

    @classmethod
    def _set_ticket(cls, printer, stock_w_mm: float, stock_h_mm: float,
                    dpi: int, off_x_mm: float = 0.0,
                    off_y_mm: float = 0.0) -> tuple[float, float]:
        """Ticket = label − origin, quantized up to whole dots.

        The driver's reported page is the paper plus its unprintable
        origin (floor case: 100.0 + 2.5 = 102.5), so the ticket subtracts
        the probed origin and the report comes back at the stock size.
        Returns the ticketed (quantized) size in mm.
        """
        from PySide6.QtGui import QPageSize, QPageLayout
        from PySide6.QtCore import QSizeF, QMarginsF
        tw = max(1.0, float(stock_w_mm) - float(off_x_mm or 0.0))
        th = max(1.0, float(stock_h_mm) - float(off_y_mm or 0.0))
        q_w, q_h = cls._quantized_mm(tw, th, dpi)
        orient = (QPageLayout.Portrait if float(stock_w_mm)
                  >= float(stock_h_mm) else QPageLayout.Landscape)
        printer.setPageLayout(QPageLayout(
            QPageSize(QSizeF(q_w, q_h), QPageSize.Millimeter),
            orient, QMarginsF(0, 0, 0, 0)))
        return q_w, q_h

    @staticmethod
    def _ticket_ok(g: dict, want_w_mm: float, want_h_mm: float) -> bool:
        return (abs(float(g.get("ticket_w_mm") or 0) - float(want_w_mm))
                <= PAPER_TOL_MM
                and abs(float(g.get("ticket_h_mm") or 0) - float(want_h_mm))
                <= PAPER_TOL_MM)

    @classmethod
    def probe(cls, printer_name: str, stock_w_mm: float | None = None,
              stock_h_mm: float | None = None,
              dpi: int = 203, off_x_mm: float = 0.0,
              off_y_mm: float = 0.0) -> dict:
        """Read the driver's ticket/printable geometry (nothing hardcoded).

        When stock sizes are given, the ticket (label − origin) is set
        first so the printable rect is measured for that stock; otherwise
        the driver's current page is measured as-is.
        """
        from PySide6.QtPrintSupport import QPrinter
        from PySide6.QtGui import QPageSize, QPageLayout
        from PySide6.QtCore import QSizeF, QMarginsF
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPrinterName(printer_name)
        try:
            printer.setFullPage(False)
        except Exception:
            pass
        try:
            printer.setResolution(int(dpi))
        except Exception:
            pass
        if stock_w_mm and stock_h_mm:
            cls._set_ticket(printer, stock_w_mm, stock_h_mm, dpi,
                            off_x_mm, off_y_mm)
        res = int(printer.resolution() or dpi)
        eff = printer.pageLayout()
        ew, eh = cls._effective_mm(eff)
        ml, mt, _mr, _mb = cls._margins_each(eff)
        try:
            from PySide6.QtPrintSupport import QPrinter as _QP
            pr = printer.pageRect(_QP.DevicePixel)
            pw_mm = float(pr.width()) / res * 25.4
            ph_mm = float(pr.height()) / res * 25.4
        except Exception:
            pw_mm = max(0.0, ew - ml)
            ph_mm = max(0.0, eh - mt)
        out = {
            "origin_x_mm": round(ml, 2),
            "origin_y_mm": round(mt, 2),
            "print_w_mm": round(pw_mm, 2),
            "print_h_mm": round(ph_mm, 2),
            "ticket_w_mm": round(ew, 2),
            "ticket_h_mm": round(eh, 2),
            "ticket_delta_mm": round(
                (ew - float(stock_w_mm)) if stock_w_mm else 0.0, 2),
            "dpi": res,
            "probed_at": _now_iso(),
        }
        return out

    @classmethod
    def ensure_paper(cls, printer_name: str, stock_w_mm: float,
                     stock_h_mm: float, dpi: int = 203) -> tuple[dict, str]:
        """Repair the driver ticket, then fit-test; never raises for size.

        Returns (geometry, state): "ok" when the ticket reads back within
        PAPER_TOL_MM, "repaired" after the single origin correction,
        "proceed" when the driver ignores the ticket (the job still prints
        1:1 and the state is logged). At most two ticket writes per call;
        the probed origin is cached per printer so the next job tickets
        the corrected size first try.
        """
        want_w, want_h = float(stock_w_mm), float(stock_h_mm)
        # Fast path: the cached origin corrects the ticket first try.
        ox = oy = 0.0
        try:
            cached = _load_probe_cache(printer_name)
            if cached and int(cached.get("dpi") or 0) == int(dpi):
                ox = float(cached.get("origin_x_mm") or 0.0)
                oy = float(cached.get("origin_y_mm") or 0.0)
        except (TypeError, ValueError):
            ox = oy = 0.0
        first = cls.probe(printer_name, want_w, want_h, dpi, ox, oy)
        if cls._ticket_ok(first, want_w, want_h):
            first["ticket_delta_mm"] = round(
                first["ticket_w_mm"] - want_w, 2)
            _save_probe_cache(printer_name, first)
            return first, "ok"
        # Single repair attempt with the freshly measured origin.
        second = cls.probe(printer_name, want_w, want_h, dpi,
                           float(first.get("origin_x_mm") or 0.0),
                           float(first.get("origin_y_mm") or 0.0))
        if cls._ticket_ok(second, want_w, want_h):
            _log.info(
                "paper repaired: ticket=%.2fx%.2f -> reported=%.2fx%.2f",
                want_w - float(first.get("origin_x_mm") or 0.0),
                want_h - float(first.get("origin_y_mm") or 0.0),
                second["ticket_w_mm"], second["ticket_h_mm"])
            _save_probe_cache(printer_name, second)
            return second, "repaired"
        _log.warning(
            "driver page still %.2fx%.2fmm (wanted %.2fx%.2fmm); "
            "continuing at 1:1",
            second["ticket_w_mm"], second["ticket_h_mm"], want_w, want_h)
        _save_probe_cache(printer_name, second)
        return second, "proceed"

    # ----- 1:1 submission (never scaled, never fail on size) -----
    def _submit_11(self, pil_img, doc_name: str, layout: dict,
                   geometry: dict, state: str) -> bool:
        if not printer_exists(self.printer_name):
            raise RuntimeError(
                f"Printer {self.printer_name!r} not found — install its "
                f"driver and pick it in Devices")
        try:
            from PySide6.QtPrintSupport import QPrinter
            from PySide6.QtGui import QPainter, QImage
            from PySide6.QtCore import QRectF
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
        dpi = int(layout.get("dpi") or 203)
        try:
            from . import layout as _layout
            ox_px, oy_px = _layout.content_origin_px(layout)
        except Exception:
            ox_px = oy_px = 0
        printer = QPrinter(QPrinter.HighResolution)
        printer.setPrinterName(self.printer_name)
        printer.setDocName(doc_name)
        try:
            printer.setFullPage(False)
        except Exception:
            pass
        try:
            printer.setResolution(dpi)
        except Exception:
            pass
        painter = QPainter()
        if not painter.begin(printer):
            raise RuntimeError(
                f"Printer {self.printer_name!r} refused the job — check it is "
                f"online with the right label stock loaded")
        try:
            # 1:1 — the bitmap is drawn at its own pixel size at the content
            # origin, never resized: the target rect equals the image size,
            # so no scaling can occur. Reel/carton art is rendered at
            # exactly the content size; client art carries its row-driven
            # height. The page width the driver reports never feeds this
            # transform; the driver's own origin is handled by GDI
            # (printable-local origin, fullPage False).
            painter.drawImage(
                QRectF(ox_px, oy_px, qimg.width(), qimg.height()), qimg)
        except Exception as e:
            try:
                painter.end()
            except Exception:
                pass
            raise RuntimeError(f"Label print failed on {self.printer_name!r}: {e}")
        if not painter.end():
            raise RuntimeError(
                f"Label print failed on {self.printer_name!r} (spooler error)")
        res = int(geometry.get("dpi") or dpi)
        _log.info(
            "print geom: dpi=%d phys=%dx%d off=%d,%d print=%dx%d "
            "content=%.1fx%.1f@%.1f,%.1f scale=1.0 state=%s",
            res,
            int(round(float(layout["stock_w_mm"]) / 25.4 * res)),
            int(round(float(layout["stock_h_mm"]) / 25.4 * res)),
            ox_px, oy_px, qimg.width(), qimg.height(),
            float(layout["content_w_mm"]), float(layout["content_h_mm"]),
            float(layout["content_x_mm"]), float(layout["content_y_mm"]),
            state)
        return True

    def print_label(self, template_id: str, pil_img, doc_name: str,
                    layout: dict | None = None) -> bool:
        """Print one content-box bitmap 1:1 (shared by all label types)."""
        if not printer_exists(self.printer_name):
            raise RuntimeError(
                f"Printer {self.printer_name!r} not found — install its "
                f"driver and pick it in Devices")
        lay = dict(layout) if layout else _load_layout(template_id)
        dpi = int(lay.get("dpi") or 203)
        geometry, state = self.ensure_paper(
            self.printer_name, float(lay["stock_w_mm"]),
            float(lay["stock_h_mm"]), dpi)
        # The only print-time failure left is a genuine fit failure:
        # the size check is gone, but content outside the printable area
        # would clip — report the deficit in mm so it is fixable.
        try:
            from . import layout as _layout
            printable = {
                "origin_x_mm": geometry.get("origin_x_mm", 0),
                "origin_y_mm": geometry.get("origin_y_mm", 0),
                "print_w_mm": geometry.get("print_w_mm", 0),
                "print_h_mm": geometry.get("print_h_mm", 0),
            }
            issues = _layout.fit_report(lay, printable)
        except Exception:
            issues = []
        if issues:
            raise RuntimeError(
                f"Printer {self.printer_name!r} content "
                f"{float(lay['content_w_mm']):.1f}x"
                f"{float(lay['content_h_mm']):.1f}mm does not fit the "
                f"printable area: {'; '.join(issues)}. "
                f"Open Label layout and move the content inside.")
        return self._submit_11(pil_img, doc_name, lay, geometry, state)

    def print_reel_label(self, data: dict) -> bool:
        lay = _load_layout("reel")
        return self.print_label("reel", self._reel_image(data, lay),
                                "Simpld reel sticker", lay)

    def print_carton_label(self, data: dict) -> bool:
        lay = _load_layout("carton")
        return self.print_label("carton", self._carton_image(data, lay),
                                "Simpld box sticker", lay)

    def print_client_label(self, data: dict) -> bool:
        lay = _load_layout("client")
        return self.print_label("client", self._client_image(data, lay),
                                "Simpld client box sticker", lay)
