"""Label image rendering. 203dpi PNGs for the Citizen CL-S621.

Type-1 (reel, NO QR) and Type-2 (carton, WITH QR) share the bordered table
layout of the old system's sticker (see floor photo: company header, C.Name,
Job Name, Date/Shift/No of Reels, No of Pieces, Gross/Core/Net with big
Roll No#, Supervisor; carton swaps the roll-number box for a QR). The PNG is
what gets sent to the Windows spooler (Citizen Windows driver) — no EPL/ZPL
emulation games needed, works with the stock Citizen USB driver.

Fixed media: 4in wide (101.6mm) x 3in tall (76.2mm). No height option.

Also used for the on-screen preview.
"""
from __future__ import annotations
import io
import os

from factory.db import COMPANY, fmt_date_ddmmyy, bare_carton_no

# Fixed Citizen media: 4in x 3in. Width fixed by hardware; height fixed to the
# sticker stock (matches the old label proportions, wider than tall).
LABEL_WIDTH_MM = 101.6
LABEL_HEIGHT_MM = 76.2
LABEL_DPI = 203


def label_px(width_mm: float = LABEL_WIDTH_MM, height_mm: float = LABEL_HEIGHT_MM,
             dpi: int = LABEL_DPI) -> tuple[int, int]:
    w = max(200, int(width_mm / 25.4 * dpi))
    h = max(200, int(height_mm / 25.4 * dpi))
    return w, h


def _font(size: int, bold: bool = True):
    try:
        from PIL import ImageFont
        # Windows factory PC: arial/arialbd. Linux: Liberation Sans (Arial metric
        # clone) or DejaVu. Bare names only resolve next to PIL, so absolute
        # system paths are tried too; else bitmap default (wrong sizes!).
        if bold:
            names = ("arialbd.ttf",
                     "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
                     "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
                     "DejaVuSans-Bold.ttf")
        else:
            names = ("arial.ttf",
                     "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Regular.ttf",
                     "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans.ttf",
                     "DejaVuSans.ttf", "DejaVuSans-Bold.ttf")
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()
    except Exception:
        return None


def _text_w(d, text: str, font) -> int:
    try:
        return int(d.textlength(text, font=font))
    except Exception:
        try:
            b = d.textbbox((0, 0), text, font=font)
            return b[2] - b[0]
        except Exception:
            return len(text) * (getattr(font, "size", 20) // 2)


def _fit_font(d, text: str, max_w: int, start: int, bold: bool = True, floor: int = 16):
    size = start
    while size > floor:
        f = _font(size, bold=bold)
        if _text_w(d, text, f) <= max_w:
            return f
        size -= 2
    return _font(floor, bold=bold)


def _cell_text(d, box, text: str, size: int, bold: bool = True,
               center: bool = False, right: bool = False):
    """Draw text vertically centered in box (x0, y0, x1, y1), shrunk to fit."""
    x0, y0, x1, y1 = box
    pad = 6
    f = _fit_font(d, text, (x1 - x0) - pad * 2, size, bold=bold)
    tw = _text_w(d, text, f)
    th = getattr(f, "size", size)
    if center:
        x = x0 + (x1 - x0 - tw) // 2
    elif right:
        x = x1 - tw - pad
    else:
        x = x0 + pad
    y = y0 + max(0, (y1 - y0 - th) // 2) - 2
    d.text((x, y), text, font=f, fill="black")
    return f


def _qr_image(text: str, px: int = 340):
    try:
        import qrcode
        q = qrcode.make(text, box_size=8, border=2)
        q = q.resize((px, px))
        return q.convert("RGB")
    except Exception:
        return None


def _table_base(wt, ab_rows: set[int], weight_first: int,
                width_mm: float = LABEL_WIDTH_MM,
                height_mm: float = LABEL_HEIGHT_MM, dpi: int = LABEL_DPI):
    """Blank bordered canvas + grid geometry shared by both stickers.

    Columns: A (row labels) | B (values) | C (right box: Roll No# or QR).
    Bands (header/Job/Date) run full width with only the C divider; the A|B
    split exists solely in the Gross/Core/Net rows (ab_rows); the C box is
    one merged cell with no horizontal lines through it.
    """
    from PIL import Image, ImageDraw
    W, H = label_px(width_mm, height_mm, dpi)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W - 1, H - 1], outline="black", width=3)
    n = len(wt)
    tot = sum(wt)
    ys = [2]
    for w_ in wt:
        ys.append(ys[-1] + (H - 4) * w_ / tot)
    ys[-1] = H - 2
    # columns A | B | C
    xA, xB, xC = 2, 2 + (W - 4) * 0.27, 2 + (W - 4) * 0.70
    xE = W - 2
    lw = 2
    # full-width horizontals; weight-row dividers stop at C (merged right box)
    for i in range(1, n):
        y = ys[i]
        if i in ab_rows:
            d.line([(xA, y), (xC, y)], fill="black", width=lw)
        else:
            d.line([(2, y), (xE, y)], fill="black", width=lw)
    # C divider runs the full height; B divider only inside the weight rows
    # (clamped so single-weight layouts like the box-only sticker work too).
    d.line([(xC, ys[0]), (xC, ys[n])], fill="black", width=lw)
    d.line([(xB, ys[weight_first]), (xB, ys[min(n, weight_first + 3)])],
           fill="black", width=lw)
    rows = [(ys[i], ys[i + 1]) for i in range(n)]
    cols = {"A": xA, "B": xB, "C": xC, "E": xE}
    return img, d, rows, cols


# Row weights: header, job, date, pieces, gross, core, net. No C.Name / shift /
# supervisor on the sticker — customer lives on the dispatch order only.
REEL_ROWS = [0.115, 0.135, 0.115, 0.115, 0.155, 0.15, 0.23]


def render_reel(job: dict, reel: dict, width_mm=LABEL_WIDTH_MM,
                height_mm=LABEL_HEIGHT_MM, dpi=LABEL_DPI):
    """Type-1 single-reel sticker (NO QR). Bordered table per the old label."""
    img, d, rows, cols = _table_base(REEL_ROWS, {5, 6}, 4, width_mm, height_mm, dpi)
    A, B, C, E = cols["A"], cols["B"], cols["C"], cols["E"]
    (r0a, r0b), (r1a, r1b), (r2a, r2b) = rows[0], rows[1], rows[2]
    (r3a, r3b), (r4a, r4b), (r5a, r5b), (r6a, r6b) = rows[3], rows[4], rows[5], rows[6]
    _cell_text(d, (A, r0a, C, r0b), COMPANY, 34)
    _cell_text(d, (C, r0a, E, r0b), f"J:{job['job_no']}", 34, right=True)
    _cell_text(d, (A, r1a, E, r1b), f"Job Name:{job['job_name']}", 30)
    _per = str(reel.get("roll_period") or "")
    _date_txt = f"Date:{fmt_date_ddmmyy(reel['created_at'])}"
    if _per:
        _date_txt += f" {_per}"
    _cell_text(d, (A, r2a, C, r2b), _date_txt, 28)
    _cell_text(d, (C, r2a, E, r2b), "No of Reels:1", 26)
    wpr = job.get("wrappers_per_reel")
    try:
        pieces_txt = f"No of Pieces:{int(wpr)}" if wpr is not None else "No of Pieces:unknown"
    except (TypeError, ValueError):
        pieces_txt = "No of Pieces:unknown"
    _cell_text(d, (A, r3a, E, r3b), pieces_txt, 30)
    _cell_text(d, (A, r4a, B, r4b), "Gross Wt:", 30)
    _cell_text(d, (B, r4a, C, r4b), f"{reel['gross']:.3f} Kg", 40)
    _cell_text(d, (A, r5a, B, r5b), "Core Wt:", 30)
    _cell_text(d, (B, r5a, C, r5b), f"{reel['core']:.3f} Kg", 36)
    _cell_text(d, (A, r6a, B, r6b), "Net Wt:", 30)
    _cell_text(d, (B, r6a, C, r6b), f"{reel['net']:.3f} Kg", 40)
    # right box: small caption on top, huge roll identity below.
    # The floor reads the bare reel number; the month-qualified roll_code
    # stays in the DB and only surfaces on evidence documents (the slip),
    # never on this sticker.
    mid = r4a + (r6b - r4a) * 0.28
    _cell_text(d, (C, r4a, E, mid), "Roll No#", 26)
    _cell_text(d, (C, mid, E, r6b), str(reel["roll_no"]), 120)
    return img


def is_weight_only(detail: dict) -> bool:
    """True when the carton links no reels: box weight only, unknowns omitted.

    Reliable marker is num_reels == 0 (what pack_weight_carton stores).
    Callers that omit num_reels fall back to the reels list; callers that
    pass num_reels > 0 keep the reel-linked sticker even without a reels key.
    """
    try:
        if "num_reels" in detail:
            return int(detail.get("num_reels") or 0) == 0
        return not detail.get("reels")
    except Exception:
        return not detail.get("reels")


def render_weight_carton(detail: dict, width_mm=LABEL_WIDTH_MM,
                         height_mm=LABEL_HEIGHT_MM, dpi=LABEL_DPI):
    """Type-2 weight-only carton sticker (WITH QR): box weight only.

    Cartons are BOX WEIGHT ONLY — core, reel count and roll IDs are unknown
    and must not be invented, so the sticker shows the measured box weight
    and omits Gross/Core/Net breakdown, reel count and roll lists. Same
    bordered table, company header and QR + carton no as the reel-linked
    carton; old cartons keep render_carton unchanged.
    """
    img, d, rows, cols = _table_base(
        [0.13, 0.15, 0.13, 0.13, 0.46],
        {4}, 4, width_mm, height_mm, dpi)
    A, B, C, E = cols["A"], cols["B"], cols["C"], cols["E"]
    (r0a, r0b), (r1a, r1b), (r2a, r2b) = rows[0], rows[1], rows[2]
    (r3a, r3b), (r4a, r4b) = rows[3], rows[4]
    _cell_text(d, (A, r0a, C, r0b), COMPANY, 34)
    _cell_text(d, (C, r0a, E, r0b), f"J:{detail['job_no']}", 34, right=True)
    _cell_text(d, (A, r1a, E, r1b), f"Job Name:{detail['job_name']}", 30)
    _cell_text(d, (A, r2a, E, r2b),
               f"Bundle:{detail['bundle_no']}", 28)
    _cell_text(d, (A, r3a, C, r3b),
               f"Date:{fmt_date_ddmmyy(detail['packed_at'])}", 28)
    _cell_text(d, (C, r3a, E, r3b), "BOX", 26)
    _cell_text(d, (A, r4a, B, r4b), "Box Wt:", 30)
    _cell_text(d, (B, r4a, C, r4b), f"{detail['total_gross']:.3f} Kg", 40)
    # right box: QR on top, carton no below (spans the weight rows)
    qr_px = max(80, min(300, int(min(E - C - 12, (r4b - r4a) * 0.62))))
    qr = _qr_image(detail["qr"], px=qr_px)
    if qr is not None:
        qx = int(C + (E - C - qr.size[0]) / 2)
        img.paste(qr, (qx, int(r4a + 6)))
        _cell_text(d, (C, r4a + 6 + qr.size[1], E, r4b),
                   bare_carton_no(detail["carton_no"]), 34, center=True)
    else:
        mid = r4a + (r4b - r4a) * 0.28
        _cell_text(d, (C, r4a, E, mid), "Box No#", 26)
        _cell_text(d, (C, mid, E, r4b), bare_carton_no(detail["carton_no"]), 80)
    return img


def render_carton(detail: dict, width_mm=LABEL_WIDTH_MM,
                  height_mm=LABEL_HEIGHT_MM, dpi=LABEL_DPI):
    """Type-2 carton sticker (WITH QR). Same table; right box holds the QR.

    Carton layout is inferred from the reel photo (no carton-sticker photo on
    file yet): totals replace Gross/Core/Net, one band names the carton, and
    the Roll-No# box carries the QR + carton no. Send a carton-sticker photo
    to correct it.

    Weight-only cartons (no linked reels) render the box-only sticker:
    measured box weight, no invented core/count/rolls.
    """
    if is_weight_only(detail):
        return render_weight_carton(detail, width_mm, height_mm, dpi)
    img, d, rows, cols = _table_base(
        [0.115, 0.13, 0.11, 0.115, 0.15, 0.145, 0.235],
        {5, 6}, 4, width_mm, height_mm, dpi)
    A, B, C, E = cols["A"], cols["B"], cols["C"], cols["E"]
    (r0a, r0b), (r1a, r1b), (r2a, r2b) = rows[0], rows[1], rows[2]
    (r3a, r3b), (r4a, r4b), (r5a, r5b), (r6a, r6b) = rows[3], rows[4], rows[5], rows[6]
    rolls = ",".join(str(r["roll_no"]) for r in detail.get("reels", []))
    _cell_text(d, (A, r0a, C, r0b), COMPANY, 34)
    _cell_text(d, (C, r0a, E, r0b), f"J:{detail['job_no']}", 34, right=True)
    _cell_text(d, (A, r1a, E, r1b), f"Job Name:{detail['job_name']}", 30)
    _cell_text(d, (A, r2a, E, r2b),
               f"Bundle:{detail['bundle_no']}  Rolls:{rolls}", 28)
    _cell_text(d, (A, r3a, C, r3b),
               f"Date:{fmt_date_ddmmyy(detail['packed_at'])}", 28)
    _cell_text(d, (C, r3a, E, r3b), f"No of Reels:{detail['num_reels']}", 26)
    _cell_text(d, (A, r4a, B, r4b), "Gross Wt:", 30)
    _cell_text(d, (B, r4a, C, r4b), f"{detail['total_gross']:.3f} Kg", 40)
    _cell_text(d, (A, r5a, B, r5b), "Core Wt:", 30)
    _cell_text(d, (B, r5a, C, r5b), f"{detail['total_core']:.3f} Kg", 36)
    _cell_text(d, (A, r6a, B, r6b), "Net Wt:", 30)
    _cell_text(d, (B, r6a, C, r6b), f"{detail['total_net']:.3f} Kg", 40)
    # right box: QR on top, carton no below (spans the weight rows)
    qr_px = max(80, min(300, int(min(E - C - 12, (r6b - r4a) * 0.62))))
    qr = _qr_image(detail["qr"], px=qr_px)
    if qr is not None:
        qx = int(C + (E - C - qr.size[0]) / 2)
        img.paste(qr, (qx, int(r4a + 6)))
        _cell_text(d, (C, r4a + 6 + qr.size[1], E, r6b),
                   bare_carton_no(detail["carton_no"]), 34, center=True)
    else:
        mid = r4a + (r6b - r4a) * 0.28
        _cell_text(d, (C, r4a, E, mid), "Box No#", 26)
        _cell_text(d, (C, mid, E, r6b), bare_carton_no(detail["carton_no"]), 80)
    return img


def save_png(img, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    return path
