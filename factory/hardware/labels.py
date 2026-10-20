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
import base64
import io
import os
from datetime import datetime

from factory.db import COMPANY, fmt_date_ddmmyy, bare_carton_no

# Fixed Citizen media: 4in x 3in. Width fixed by hardware; height fixed to the
# sticker stock (matches the old label proportions, wider than tall).
LABEL_WIDTH_MM = 101.6
LABEL_HEIGHT_MM = 76.2
LABEL_DPI = 203

# Type-3 client box sticker: A4-width media (210 mm), height follows the
# number of coils in the box. Printed on the dispatch station's wide label
# printer, not the 4x3 Citizen used for reel/box stickers.
WIDE_WIDTH_MM = 210.0
WIDE_DPI = 203
WIDE_MIN_H_MM = 78.0


def wide_label_height_mm(n_rows: int) -> float:
    """Content height: fixed header/table chrome + one 8 mm band per coil."""
    return max(WIDE_MIN_H_MM, 67.0 + 8.0 * max(0, int(n_rows or 0)))


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


def _serif(size: int, bold: bool = False):
    """Serif face for the company title (the sample uses a serif wordmark)."""
    try:
        from PIL import ImageFont
        if bold:
            names = ("timesbd.ttf", "georgiab.ttf",
                     "/usr/share/fonts/liberation-serif-fonts/LiberationSerif-Bold.ttf",
                     "/usr/share/fonts/dejavu-serif-fonts/DejaVuSerif-Bold.ttf",
                     "DejaVuSerif-Bold.ttf")
        else:
            names = ("times.ttf", "georgia.ttf",
                     "/usr/share/fonts/liberation-serif-fonts/LiberationSerif-Regular.ttf",
                     "/usr/share/fonts/dejavu-serif-fonts/DejaVuSerif.ttf",
                     "DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf")
        for name in names:
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return _font(size, bold=bold)
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


def _dmystr(ts) -> str:
    s = str(ts or "")
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%d-%m-%Y")
    except (TypeError, ValueError):
        return s[:10] or "—"


def _kgstr(v) -> str:
    try:
        s = f"{float(v):.2f}"
    except (TypeError, ValueError):
        return "—"
    s = s.rstrip("0").rstrip(".")
    return s or "0"


def _pcsstr(v) -> str:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return "—"
    return str(n) if n > 0 else "—"


def _text_height(font) -> int:
    try:
        b = font.getbbox("Ag")
        return max(1, b[3] - b[1])
    except Exception:
        return int(getattr(font, "size", 20))


def _fit_text_ellipsis(d, text: str, max_w: int, start: int,
                       bold: bool = True, floor: int = 16):
    f = _fit_font(d, str(text), max_w, start, bold=bold, floor=floor)
    t = str(text)
    if _text_w(d, t, f) <= max_w:
        return t, f
    while len(t) > 1 and _text_w(d, t + "…", f) > max_w:
        t = t[:-1]
    return (t + "…") if t else "…", f


def _spaced_w(d, text: str, font, spacing: int) -> int:
    return sum(_text_w(d, ch, font) for ch in text) + spacing * max(0, len(text) - 1)


def _draw_spaced_center(d, cx: int, cy: int, text: str, font, spacing: int) -> None:
    total = _spaced_w(d, text, font, spacing)
    x = cx - total / 2
    for ch in text:
        d.text((x, cy), ch, font=font, fill="black", anchor="lm")
        x += _text_w(d, ch, font) + spacing


def _logo_image(data):
    if not data:
        return None
    try:
        from PIL import Image
        raw = base64.b64decode(str(data), validate=True)
        if not raw:
            return None
        im = Image.open(io.BytesIO(raw))
        im.load()
        return im.convert("RGBA")
    except Exception:
        return None


# Wide client sticker columns, left -> right. The sample's table has no
# vertical padding tricks: a plain six-column grid with a total/box-no row.
_WIDE_COLS = (0.09, 0.20, 0.16, 0.14, 0.16, 0.25)
_WIDE_HEAD = ("S.no", "No of pcs", "Gross Wt (kgs)",
              "Tare Wt (kgs)", "Net Wt (kgs)", "Mfg Date")


def render_client_box(detail: dict, width_mm: float = WIDE_WIDTH_MM,
                      dpi: int = WIDE_DPI):
    """Type-3 wide client sticker (one per box, one row per coil).

    Layout (from the Vasavi photo): logo + company title, header grid with
    Pack. Date / Customer / Product and Batch No. / Customer Po / No of
    Coils, then S.no / pieces / gross / tare / net / mfg-date rows, a Total
    row and BOX NO (the client-facing sequence, never the scanned carton
    code). Height grows with the coil count; width is A4 (210 mm).
    """
    from PIL import Image, ImageDraw

    rows = list(detail.get("coils") or [])
    n = len(rows)
    h_mm = wide_label_height_mm(n)
    W, H = label_px(width_mm, h_mm, dpi)
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    def px(v: float) -> int:
        return int(round(v / 25.4 * dpi))

    left, right = px(5), W - px(5)
    top, bottom = px(5), H - px(5)
    line = max(2, px(0.22))
    d.rectangle([left, top, right, bottom], outline="black", width=max(3, line))

    # ---- top band: logo + company title ----
    band_bot = px(25)
    d.line([(left, band_bot), (right, band_bot)], fill="black", width=line)
    logo = _logo_image(detail.get("company_logo"))
    if logo is not None:
        box_w, box_h = px(52), px(16)
        logo.thumbnail((box_w, box_h), Image.LANCZOS)
        lx = px(8)
        ly = px(15) - logo.height // 2
        img.paste(logo, (lx, ly), logo)
        tx0, tx1 = px(63), right - px(3)
    else:
        tx0, tx1 = left + px(3), right - px(3)

    title = str(detail.get("company_name") or COMPANY).strip().upper()
    spacing = px(0.35)
    size = px(7.0)
    floor = px(4.0)
    tf = _serif(size, bold=False)
    while size > floor and _spaced_w(d, title, tf, spacing) > (tx1 - tx0):
        size -= 2
        tf = _serif(size, bold=False)
    _draw_spaced_center(d, (tx0 + tx1) // 2, (top + band_bot) // 2,
                        title, tf, spacing)

    # ---- header grid: 3 rows, left + right groups ----
    head_bot = px(45)
    row_h = (head_bot - band_bot) / 3
    for i in (1, 2):
        y = int(band_bot + row_h * i)
        d.line([(left, y), (right, y)], fill="black", width=line)
    div_x = px(122)
    d.line([(div_x, band_bot), (div_x, head_bot)], fill="black", width=line)
    # Close the third header row (Product / No of Coils) before the table
    # header starts. Without this line the product row and the S.no header
    # row run together on the printed sticker.
    d.line([(left, head_bot), (right, head_bot)], fill="black", width=line)

    label_font = _font(px(3.0), bold=True)
    l_label_x, l_value_x = px(8), px(42)
    r_label_x, r_value_x = px(126), px(160)
    l_max = div_x - px(3) - l_value_x
    r_max = right - px(3) - r_value_x
    left_fields = (
        ("Pack. Date", _dmystr(detail.get("packed_at"))),
        ("CUSTOMER", str(detail.get("customer") or "—")),
        ("PRODUCT", str(detail.get("product_name") or detail.get("job_name") or "—")),
    )
    right_fields = (
        ("BATCH NO.", str(detail.get("batch_no") or "—")),
        ("Customer Po:", str(detail.get("customer_po") or "—")),
        ("NO OF Coils:", str(detail.get("num_coils", n) or 0)),
    )
    for i, (lab, val) in enumerate(left_fields):
        yc = int(band_bot + row_h * (i + 0.5))
        d.text((l_label_x, yc), lab, font=label_font, fill="black", anchor="lm")
        text, f = _fit_text_ellipsis(d, val, l_max, px(4.4),
                                     bold=False, floor=px(2.6))
        d.text((l_value_x, yc), text, font=f, fill="black", anchor="lm")
    for i, (lab, val) in enumerate(right_fields):
        yc = int(band_bot + row_h * (i + 0.5))
        d.text((r_label_x, yc), lab, font=label_font, fill="black", anchor="lm")
        text, f = _fit_text_ellipsis(d, val, r_max, px(4.4),
                                     bold=False, floor=px(2.6))
        d.text((r_value_x, yc), text, font=f, fill="black", anchor="lm")

    # ---- table ----
    t_top = head_bot
    t_bottom = bottom
    head_h = px(8)
    total_h = px(9)
    body_top = t_top + head_h
    body_bot = t_bottom - total_h
    n_draw = max(1, n)
    body_row_h = (body_bot - body_top) / n_draw

    xs = [left + (right - left) * sum(_WIDE_COLS[:i])
          for i in range(len(_WIDE_COLS) + 1)]
    xs[-1] = right
    d.line([(left, t_top + head_h), (right, t_top + head_h)],
           fill="black", width=line)
    d.line([(left, body_bot), (right, body_bot)], fill="black", width=max(3, line))
    for x in xs[1:-1]:
        d.line([(int(x), t_top), (int(x), t_bottom)], fill="black", width=line)
    for i in range(1, n_draw):
        y = int(body_top + body_row_h * i)
        d.line([(left, y), (right, y)], fill="black", width=line)

    head_font = _font(px(3.1), bold=True)
    for i, label in enumerate(_WIDE_HEAD):
        cw = xs[i + 1] - xs[i] - px(1.2)
        text, f = _fit_text_ellipsis(d, label, int(cw), px(3.1),
                                     bold=True, floor=px(1.9))
        d.text(((xs[i] + xs[i + 1]) / 2, t_top + head_h / 2), text,
               font=f, fill="black", anchor="mm")

    row_font = _font(px(4.0), bold=False)
    if n:
        for i, r in enumerate(rows):
            yc = body_top + body_row_h * (i + 0.5)
            vals = (
                str(r.get("roll_no", "—")),
                _pcsstr(r.get("pieces")),
                _kgstr(r.get("gross")),
                _kgstr(r.get("core")),
                _kgstr(r.get("net")),
                _dmystr(r.get("mfg_date")),
            )
            for j, val in enumerate(vals):
                cw = xs[j + 1] - xs[j] - px(1.2)
                text, f = _fit_text_ellipsis(d, val, int(cw), px(4.0),
                                             bold=False, floor=px(2.2))
                d.text(((xs[j] + xs[j + 1]) / 2, yc), text,
                       font=f, fill="black", anchor="mm")
    else:
        yc = body_top + body_row_h / 2
        for j in range(len(_WIDE_HEAD)):
            d.text(((xs[j] + xs[j + 1]) / 2, yc), "—",
                   font=row_font, fill="black", anchor="mm")

    total_pieces = sum(int(r.get("pieces") or 0) for r in rows)
    total_gross = sum(float(r.get("gross") or 0) for r in rows)
    total_tare = sum(float(r.get("core") or 0) for r in rows)
    total_net = sum(float(r.get("net") or 0) for r in rows)
    box_no = int(detail.get("client_box_no") or 1)
    totals = (
        "Total",
        _pcsstr(total_pieces),
        _kgstr(total_gross),
        _kgstr(total_tare),
        _kgstr(total_net),
    )
    yc = body_bot + total_h / 2
    for j, val in enumerate(totals):
        cw = xs[j + 1] - xs[j] - px(1.2)
        text, f = _fit_text_ellipsis(d, val, int(cw), px(4.0),
                                     bold=True, floor=px(2.2))
        d.text(((xs[j] + xs[j + 1]) / 2, yc), text,
               font=f, fill="black", anchor="mm")

    # Client box number: a dedicated field in the final total cell, with the
    # label on top and the bare 1..N number below it (never the scanned carton
    # code, and no 01-style padding on the printed sticker).
    bcx = (xs[5] + xs[6]) / 2
    box_label_font = _font(px(2.2), bold=True)
    box_num_font = _font(px(4.8), bold=True)
    d.text((bcx, body_bot + total_h * 0.27), "BOX NO",
           font=box_label_font, fill="black", anchor="mm")
    d.text((bcx, body_bot + total_h * 0.68), str(box_no),
           font=box_num_font, fill="black", anchor="mm")
    return img


def save_png(img, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    img.save(path, "PNG")
    return path
