"""Label layout: stock + content-box geometry, probed driver data, persistence.

Three nested rectangles, all in millimetres, origin top-left (see the
Label Layout Editor brief):

  stock     = (0, 0, stock_w_mm, stock_h_mm)          # physical label
  printable = (origin_x, origin_y, print_w, print_h)   # what driver can ink
  content   = (cx, cy, cw, ch)                        # where simpld draws

margins: left=cx, top=cy, right=stock_w-(cx+cw), bottom=stock_h-(cy+ch)

Conversions: dots = mm * dpi / 25.4, mm = dots * 25.4 / dpi.

Qt-free: geometry + settings I/O only, so tests never need Qt. Driver
probing lives in factory.hardware.qtprint (probe/ensure_paper) and
writes its results through the printer.* keys below.
"""
from __future__ import annotations

import json
import math

SCHEMA_VERSION = 1
SCHEMA_KEY = "label.schema_version"

# Template ids with their factory defaults (mm). Reel/carton share the
# 100x50 Citizen stock; client is the wide dispatch sticker.
TEMPLATES = ("reel", "carton", "client")

DEFAULTS = {
    "reel": {"stock_w_mm": 100.0, "stock_h_mm": 50.0, "dpi": 203,
             "content_x_mm": 3.0, "content_y_mm": 3.0,
             "content_w_mm": 94.0, "content_h_mm": 44.0,
             "margin_locked": True, "units": "mm", "fit_mode": "reflow"},
    "carton": {"stock_w_mm": 100.0, "stock_h_mm": 50.0, "dpi": 203,
               "content_x_mm": 3.0, "content_y_mm": 3.0,
               "content_w_mm": 94.0, "content_h_mm": 44.0,
               "margin_locked": True, "units": "mm", "fit_mode": "reflow"},
    "client": {"stock_w_mm": 210.0, "stock_h_mm": 92.0, "dpi": 203,
               "content_x_mm": 3.0, "content_y_mm": 3.0,
               "content_w_mm": 204.0, "content_h_mm": 86.0,
               "margin_locked": True, "units": "mm", "fit_mode": "reflow"},
}

PRESETS = (
    ("100x50", 100.0, 50.0),
    ("100x150", 100.0, 150.0),
    ("4x6 in", 101.6, 152.4),
    ("custom", None, None),
)

MIN_CONTENT_MM = 10.0


def mm_to_dots(mm: float, dpi: int) -> int:
    return int(round(float(mm) / 25.4 * int(dpi)))


def dots_to_mm(dots: float, dpi: int) -> float:
    return float(dots) * 25.4 / int(dpi)


def _prefix(template_id: str) -> str:
    return f"label.{template_id}."


def _get(settings: dict, key: str, default: str = "") -> str:
    try:
        v = settings.get(key, default)
    except Exception:
        return default
    return str(v) if v is not None else default


def _f(settings: dict, key: str, default: float) -> float:
    try:
        return float(settings.get(key, default))
    except (TypeError, ValueError):
        return float(default)


def _i(settings: dict, key: str, default: int) -> int:
    try:
        return int(float(settings.get(key, default)))
    except (TypeError, ValueError):
        return int(default)


def _b(settings: dict, key: str, default: bool) -> bool:
    v = settings.get(key, default)
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in ("1", "true", "yes", "on", "locked"):
        return True
    if s in ("0", "false", "no", "off"):
        return False
    return bool(default)


def get_layout(template_id: str, settings: dict) -> dict:
    """Resolved layout for one template (defaults fill missing keys)."""
    tid = str(template_id or "").strip().lower()
    d = dict(DEFAULTS.get(tid, DEFAULTS["reel"]))
    p = _prefix(tid if tid in DEFAULTS else "reel")
    out = {
        "template_id": tid if tid in DEFAULTS else "reel",
        "stock_w_mm": _f(settings, p + "stock_w_mm", d["stock_w_mm"]),
        "stock_h_mm": _f(settings, p + "stock_h_mm", d["stock_h_mm"]),
        "dpi": _i(settings, p + "dpi", d["dpi"]),
        "content_x_mm": _f(settings, p + "content_x_mm", d["content_x_mm"]),
        "content_y_mm": _f(settings, p + "content_y_mm", d["content_y_mm"]),
        "content_w_mm": _f(settings, p + "content_w_mm", d["content_w_mm"]),
        "content_h_mm": _f(settings, p + "content_h_mm", d["content_h_mm"]),
        "margin_locked": _b(settings, p + "margin_locked", d["margin_locked"]),
        "units": _get(settings, p + "units", d["units"]).strip().lower() or "mm",
        "fit_mode": _get(settings, p + "fit_mode", d["fit_mode"]).strip().lower() or "reflow",
    }
    if out["units"] not in ("mm", "inch"):
        out["units"] = "mm"
    if out["fit_mode"] not in ("reflow", "stretch"):
        out["fit_mode"] = "reflow"
    if out["dpi"] <= 0:
        out["dpi"] = d["dpi"]
    # Clamp content inside stock (never block editing — clamp only).
    out = clamp_to_stock(out)
    return out


def clamp_to_stock(layout: dict) -> dict:
    """Keep the content box inside the stock rect, proportionally."""
    try:
        sw, sh = float(layout["stock_w_mm"]), float(layout["stock_h_mm"])
    except (TypeError, ValueError):
        return layout
    if sw <= 0 or sh <= 0:
        return layout
    out = dict(layout)
    try:
        cx = max(0.0, float(out.get("content_x_mm") or 0))
        cy = max(0.0, float(out.get("content_y_mm") or 0))
        cw = max(1.0, float(out.get("content_w_mm") or 1))
        ch = max(1.0, float(out.get("content_h_mm") or 1))
    except (TypeError, ValueError):
        return layout
    if cx + cw > sw:
        over = (cx + cw) - sw
        if cw - over >= 1.0:
            cw -= over
        else:
            scale = sw / (cx + cw)
            cw *= scale
            ch *= scale
            cx *= scale
    if cy + ch > sh:
        over = (cy + ch) - sh
        if ch - over >= 1.0:
            ch -= over
        else:
            scale = sh / (cy + ch)
            cw *= scale
            ch *= scale
            cy *= scale
    out["content_x_mm"] = round(max(0.0, cx), 2)
    out["content_y_mm"] = round(max(0.0, cy), 2)
    out["content_w_mm"] = round(max(1.0, min(cw, sw)), 2)
    out["content_h_mm"] = round(max(1.0, min(ch, sh)), 2)
    return out


def margins_of(layout: dict) -> dict:
    """left/top/right/bottom margins in mm derived from the content box."""
    try:
        sw = float(layout["stock_w_mm"])
        sh = float(layout["stock_h_mm"])
        cx = float(layout["content_x_mm"])
        cy = float(layout["content_y_mm"])
        cw = float(layout["content_w_mm"])
        ch = float(layout["content_h_mm"])
    except (TypeError, ValueError):
        return {"left": 0.0, "top": 0.0, "right": 0.0, "bottom": 0.0}
    return {
        "left": round(cx, 2),
        "top": round(cy, 2),
        "right": round(max(0.0, sw - (cx + cw)), 2),
        "bottom": round(max(0.0, sh - (cy + ch)), 2),
    }


def set_margins(layout: dict, left=None, top=None, right=None, bottom=None,
                locked: bool | None = None) -> dict:
    """Return a new layout with margins applied (content derived)."""
    out = dict(layout)
    try:
        sw = float(out["stock_w_mm"])
        sh = float(out["stock_h_mm"])
    except (TypeError, ValueError):
        return out
    m = margins_of(out)
    if left is not None:
        m["left"] = max(0.0, float(left))
    if top is not None:
        m["top"] = max(0.0, float(top))
    if right is not None:
        m["right"] = max(0.0, float(right))
    if bottom is not None:
        m["bottom"] = max(0.0, float(bottom))
    if locked is None:
        try:
            locked = bool(out.get("margin_locked", True))
        except Exception:
            locked = True
    out["margin_locked"] = bool(locked)
    out["content_x_mm"] = round(m["left"], 2)
    out["content_y_mm"] = round(m["top"], 2)
    out["content_w_mm"] = round(max(1.0, sw - m["left"] - m["right"]), 2)
    out["content_h_mm"] = round(max(1.0, sh - m["top"] - m["bottom"]), 2)
    return clamp_to_stock(out)


def reset_margins(layout: dict, value: float = 3.0) -> dict:
    return set_margins(layout, value, value, value, value)


def content_px(layout: dict) -> tuple[int, int]:
    dpi = int(layout.get("dpi") or 203)
    return (mm_to_dots(float(layout["content_w_mm"]), dpi),
            mm_to_dots(float(layout["content_h_mm"]), dpi))


def content_origin_px(layout: dict) -> tuple[int, int]:
    dpi = int(layout.get("dpi") or 203)
    return (mm_to_dots(float(layout["content_x_mm"]), dpi),
            mm_to_dots(float(layout["content_y_mm"]), dpi))


def fit_report(layout: dict, printable: dict | None) -> list[str]:
    """Deficit of content vs printable area, in mm (actionable numbers).

    Empty list = fits. Never raises; missing printable = no printable
    overlay (editor still works offline).
    """
    if not printable:
        return []
    try:
        cx = float(layout["content_x_mm"])
        cy = float(layout["content_y_mm"])
        cw = float(layout["content_w_mm"])
        ch = float(layout["content_h_mm"])
        px = float(printable.get("origin_x_mm", 0) or 0)
        py = float(printable.get("origin_y_mm", 0) or 0)
        pw = float(printable.get("print_w_mm", 0) or 0)
        ph = float(printable.get("print_h_mm", 0) or 0)
    except (TypeError, ValueError):
        return []
    if pw <= 0 or ph <= 0:
        return []
    issues = []
    for side, short in (
            ("left", px - cx),
            ("right", (cx + cw) - (px + pw)),
            ("top", py - cy),
            ("bottom", (cy + ch) - (py + ph))):
        if short > 0:
            issues.append(f"{side} short by {short:.2f} mm")
    return issues


def validate(layout: dict, printable: dict | None) -> list[dict]:
    """Warn-only validation (never blocks editing)."""
    out = []
    for msg in fit_report(layout, printable):
        out.append({"severity": "error", "message": f"Content {msg}"})
    try:
        cw = float(layout.get("content_w_mm") or 0)
        ch = float(layout.get("content_h_mm") or 0)
    except (TypeError, ValueError):
        cw = ch = 0.0
    if 0 < cw < MIN_CONTENT_MM or 0 < ch < MIN_CONTENT_MM:
        out.append({"severity": "warning",
                    "message": "Content area is very small"})
    try:
        dpi = int(layout.get("dpi") or 0)
        probed = int((printable or {}).get("dpi") or 0)
    except (TypeError, ValueError):
        dpi = probed = 0
    if dpi and probed and dpi != probed:
        out.append({"severity": "error",
                    "message": f"Label DPI {dpi} does not match printer DPI {probed}"})
    return out


def status_token(layout: dict, printable: dict | None) -> str:
    """OK | TIGHT | WON'T FIT | not probed."""
    if not printable or not printable.get("print_w_mm"):
        return "not probed"
    issues = fit_report(layout, printable)
    if issues:
        return "WON'T FIT"
    try:
        m = margins_of(layout)
        px = float(printable.get("origin_x_mm", 0) or 0)
        py = float(printable.get("origin_y_mm", 0) or 0)
        pw = float(printable.get("print_w_mm", 0) or 0)
        ph = float(printable.get("print_h_mm", 0) or 0)
        slack = min(m["left"] - px, m["top"] - py,
                    (px + pw) - (m["left"] + float(layout["content_w_mm"])),
                    (py + ph) - (m["top"] + float(layout["content_h_mm"])))
    except (TypeError, ValueError):
        return "OK"
    if slack < 1.0:
        return "TIGHT"
    return "OK"


def status_line(layout: dict, printable: dict | None) -> str:
    m = margins_of(layout)
    cw, ch = content_px(layout)
    try:
        sw = float(layout["stock_w_mm"])
        sh = float(layout["stock_h_mm"])
    except (TypeError, ValueError):
        sw = sh = 0.0
    base = (f"stock {sw:.1f}x{sh:.1f}mm  "
            f"content {float(layout['content_w_mm']):.1f}x"
            f"{float(layout['content_h_mm']):.1f}mm ({cw}x{ch}px)  "
            f"margins {m['left']:.1f}/{m['top']:.1f}/"
            f"{m['right']:.1f}/{m['bottom']:.1f}")
    if not printable or not printable.get("print_w_mm"):
        return base + "  printable not probed  ->  not probed"
    try:
        pw = float(printable.get("print_w_mm"))
        ph = float(printable.get("print_h_mm"))
        ox = float(printable.get("origin_x_mm", 0) or 0)
        oy = float(printable.get("origin_y_mm", 0) or 0)
    except (TypeError, ValueError):
        return base + "  ->  not probed"
    return (base + f"  printable {pw:.1f}x{ph:.1f}mm from {ox:.1f},{oy:.1f}"
            + f"  ->  {status_token(layout, printable)}")


# ----- persistence (settings-table keys) -----

def to_settings(template_id: str, layout: dict) -> dict:
    p = _prefix(str(template_id or "").strip().lower())
    return {
        p + "stock_w_mm": str(layout.get("stock_w_mm", "")),
        p + "stock_h_mm": str(layout.get("stock_h_mm", "")),
        p + "dpi": str(layout.get("dpi", "")),
        p + "content_x_mm": str(layout.get("content_x_mm", "")),
        p + "content_y_mm": str(layout.get("content_y_mm", "")),
        p + "content_w_mm": str(layout.get("content_w_mm", "")),
        p + "content_h_mm": str(layout.get("content_h_mm", "")),
        p + "margin_locked": "true" if layout.get("margin_locked") else "false",
        p + "units": str(layout.get("units", "mm")),
        p + "fit_mode": str(layout.get("fit_mode", "reflow")),
        SCHEMA_KEY: str(SCHEMA_VERSION),
    }


def get_printable(printer_name: str, settings: dict) -> dict | None:
    """Cached probed driver geometry for one printer (None when never probed)."""
    p = f"printer.{printer_name}."
    w = settings.get(p + "printable_w_mm")
    h = settings.get(p + "printable_h_mm")
    if w is None or h is None:
        return None
    try:
        out = {
            "origin_x_mm": float(settings.get(p + "printable_origin_x_mm", 0) or 0),
            "origin_y_mm": float(settings.get(p + "printable_origin_y_mm", 0) or 0),
            "print_w_mm": float(w),
            "print_h_mm": float(h),
            "ticket_delta_mm": float(settings.get(p + "ticket_delta_mm", 0) or 0),
            "dpi": int(float(settings.get(p + "dpi", 203) or 203)),
            "probed_at": str(settings.get(p + "probed_at", "") or ""),
        }
    except (TypeError, ValueError):
        return None
    if out["print_w_mm"] <= 0 or out["print_h_mm"] <= 0:
        return None
    return out


def printable_to_settings(printer_name: str, probed: dict) -> dict:
    p = f"printer.{printer_name}."
    return {
        p + "printable_origin_x_mm": str(probed.get("origin_x_mm", 0)),
        p + "printable_origin_y_mm": str(probed.get("origin_y_mm", 0)),
        p + "printable_w_mm": str(probed.get("print_w_mm", 0)),
        p + "printable_h_mm": str(probed.get("print_h_mm", 0)),
        p + "ticket_delta_mm": str(probed.get("ticket_delta_mm", 0)),
        p + "dpi": str(probed.get("dpi", 203)),
        p + "probed_at": str(probed.get("probed_at", "")),
    }


# ----- profiles (export/import) -----

def export_profile(template_id: str, layout: dict,
                   printable: dict | None) -> dict:
    return {
        "kind": "simpld-label-profile",
        "version": 1,
        "template_id": template_id,
        "layout": {k: layout.get(k) for k in (
            "stock_w_mm", "stock_h_mm", "dpi", "content_x_mm",
            "content_y_mm", "content_w_mm", "content_h_mm",
            "margin_locked", "units", "fit_mode")},
        "printable": dict(printable or {}),
    }


def import_profile(data: dict) -> tuple[str, dict, dict | None]:
    """Validate an exported profile; returns (template_id, layout, printable).

    Raises ValueError on bad files. Callers persist via to_settings().
    """
    if not isinstance(data, dict):
        raise ValueError("Not a label profile (expected a JSON object)")
    if str(data.get("kind") or "") != "simpld-label-profile":
        raise ValueError("Not a label profile (bad kind)")
    tid = str(data.get("template_id") or "").strip().lower() or "reel"
    if tid not in DEFAULTS:
        raise ValueError(f"Unknown template {tid!r}")
    raw = data.get("layout") or {}
    if not isinstance(raw, dict):
        raise ValueError("Profile layout is missing")
    if "stock_w_mm" not in raw or "stock_h_mm" not in raw:
        raise ValueError("Profile layout has no stock size")
    base = dict(DEFAULTS[tid])
    base.update({k: raw[k] for k in base if k in raw})
    # Numeric sanity (no invented defaults beyond the template's own).
    for k in ("stock_w_mm", "stock_h_mm", "content_w_mm", "content_h_mm"):
        try:
            v = float(base[k])
        except (TypeError, ValueError):
            raise ValueError(f"Profile {k} is not a number")
        if not math.isfinite(v) or v <= 0 or v > 1000:
            raise ValueError(f"Profile {k} out of range: {v!r}")
    for k in ("content_x_mm", "content_y_mm"):
        try:
            v = float(base[k])
        except (TypeError, ValueError):
            raise ValueError(f"Profile {k} is not a number")
        if not math.isfinite(v) or v < 0 or v > 1000:
            raise ValueError(f"Profile {k} out of range: {v!r}")
    try:
        dpi = int(float(base.get("dpi") or 0))
    except (TypeError, ValueError):
        raise ValueError("Profile dpi is not a number")
    if dpi not in (152, 200, 203, 300, 400, 600):
        raise ValueError(f"Unusual profile dpi: {dpi}")
    base["dpi"] = dpi
    printable = data.get("printable")
    if printable is not None and not isinstance(printable, dict):
        raise ValueError("Profile printable block is not an object")
    layout = clamp_to_stock(base)
    layout["template_id"] = tid
    return tid, layout, (dict(printable) if printable else None)


def profile_summary(data: dict) -> str:
    try:
        tid, layout, printable = import_profile(data)
    except ValueError as e:
        return f"Invalid profile: {e}"
    m = margins_of(layout)
    s = (f"{tid}: stock {layout['stock_w_mm']}x{layout['stock_h_mm']}mm "
         f"@{layout['dpi']}dpi, content {layout['content_w_mm']}x"
         f"{layout['content_h_mm']}mm @ {layout['content_x_mm']},"
         f"{layout['content_y_mm']}, margins {m['left']}/{m['top']}/"
         f"{m['right']}/{m['bottom']}")
    if printable:
        s += (f" (probed printable {printable.get('print_w_mm')}x"
              f"{printable.get('print_h_mm')} from "
              f"{printable.get('origin_x_mm')},{printable.get('origin_y_mm')})")
    return s


def parse_profile_text(text: str) -> dict:
    try:
        data = json.loads(text or "")
    except Exception:
        raise ValueError("Profile is not valid JSON")
    if isinstance(data, dict) and str(data.get("kind") or "") == \
            "simpld-label-profile-bundle":
        profiles = data.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            raise ValueError("Profile bundle has no profiles")
        for p in profiles:
            import_profile(p)  # validate every entry now
        return data
    import_profile(data)  # validate now, so bad files fail before apply
    return data


def bundle_profiles(data: dict) -> list[dict]:
    """Single profiles and bundles -> flat list of profile dicts."""
    if isinstance(data, dict) and str(data.get("kind") or "") == \
            "simpld-label-profile-bundle":
        return [p for p in data.get("profiles") or [] if isinstance(p, dict)]
    return [data]


def bundle_summary(data: dict) -> str:
    lines = []
    for p in bundle_profiles(data):
        try:
            lines.append(profile_summary(p))
        except ValueError as e:
            lines.append(f"Invalid profile: {e}")
    return "\n".join(lines)


# ----- shared rendering (preview and print call the same function) -----

def render_content(template_id: str, detail: dict, layout: dict):
    """Render the content-box bitmap at content_w x content_h px.

    Preview fidelity: this single function feeds both the on-screen
    preview and the print path, so geometry is pixel-identical.
    """
    from factory.hardware import labels as _art
    tid = str(template_id or "").strip().lower()
    dpi = int(layout.get("dpi") or 203)
    cw = float(layout.get("content_w_mm") or 0)
    ch = float(layout.get("content_h_mm") or 0)
    if tid == "reel":
        job = detail.get("job", detail)
        reel = detail.get("reel", {})
        if not isinstance(reel, dict) or not reel:
            reel = detail
        return _art.render_reel(job, reel, width_mm=cw, height_mm=ch, dpi=dpi)
    if tid == "carton":
        box = detail.get("detail", detail)
        return _art.render_carton(box, width_mm=cw, height_mm=ch, dpi=dpi)
    if tid == "client":
        box = detail.get("detail", detail)
        return _art.render_client_box(box, width_mm=cw, dpi=dpi)
    raise ValueError(f"Unknown template {tid!r}")


def sample_detail(template_id: str, n_coils: int = 3) -> dict:
    """Placeholder data so the editor preview renders without production rows."""
    tid = str(template_id or "").strip().lower()
    if tid == "reel":
        return {"job": {"job_no": 4169, "job_name": "SAMPLE JOB"},
                "reel": {"gross": 10.0, "core": 0.28, "net": 9.72,
                         "created_at": "2026-01-01 10:00:00", "roll_no": 1}}
    if tid == "carton":
        return {"detail": {"job_no": 4169, "job_name": "SAMPLE JOB",
                           "carton_no": "C202601-0001", "bundle_no": 1,
                           "packed_at": "2026-01-01 10:00:00",
                           "num_reels": 0, "total_gross": 10.5,
                           "total_core": 0, "total_net": 10.5,
                           "reels": [], "qr": "VASAVI:C202601-0001"}}
    coils = [{"roll_no": i + 1, "pieces": 1000, "gross": 8.0, "core": 0.28,
              "net": 7.72, "mfg_date": "2026-01-01 08:00:00"}
             for i in range(max(1, int(n_coils or 3)))]
    return {"detail": {
        "customer": "SAMPLE CUSTOMER", "customer_po": "PO-001",
        "batch_no": "SVSF/JAN-01", "product_name": "SAMPLE PRODUCT",
        "packed_at": "2026-01-01 10:00:00", "client_box_no": 1,
        "num_coils": len(coils), "coils": coils,
        "total_pieces": 1000 * len(coils), "total_gross": 8.0 * len(coils),
        "total_tare": 0.28 * len(coils), "total_net": 7.72 * len(coils)}}


def render_test_pattern(layout: dict):
    """Calibration frame inside the content box (test-print button).

    1-dot frame at the content border, ruler ticks every 10mm with numbers
    on all four sides, and the L/T/R/B margin values. The operator measures
    the four paper margins and confirms they match the status bar.
    """
    from PIL import Image, ImageDraw
    dpi = int(layout.get("dpi") or 203)
    cw_px = mm_to_dots(float(layout.get("content_w_mm") or 0), dpi)
    ch_px = mm_to_dots(float(layout.get("content_h_mm") or 0), dpi)
    cw_px = max(40, int(cw_px))
    ch_px = max(40, int(ch_px))
    img = Image.new("RGB", (cw_px, ch_px), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, cw_px - 1, ch_px - 1], outline="black", width=1)
    try:
        m = margins_of(layout)
    except Exception:
        m = {"left": 0, "top": 0, "right": 0, "bottom": 0}
    # ruler ticks every 10mm along each edge
    step = max(1, mm_to_dots(10, dpi))
    tick = max(2, mm_to_dots(2, dpi))
    for x in range(0, cw_px, step):
        d.line([(x, 0), (x, tick)], fill="black", width=1)
        d.line([(x, ch_px - 1 - tick), (x, ch_px - 1)], fill="black", width=1)
        d.text((x + 2, tick + 1), str(int(round(x / dpi * 25.4))), fill="black")
    for y in range(0, ch_px, step):
        d.line([(0, y), (tick, y)], fill="black", width=1)
        d.line([(cw_px - 1 - tick, y), (cw_px - 1, y)], fill="black", width=1)
        d.text((tick + 1, y + 1), str(int(round(y / dpi * 25.4))), fill="black")
    # margin values in the middle
    txt = (f"L{m['left']:.1f} T{m['top']:.1f} "
           f"R{m['right']:.1f} B{m['bottom']:.1f}")
    d.text((tick + 2, ch_px // 2), txt, fill="black")
    return img
