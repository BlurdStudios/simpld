"""A4 packing-slip printing. Matches the old paper slip (see floor photos).

Layout (portrait A4):
  PACKING SLIP (centered)
  Jobno: X   C.Name.: Y   DATE:        (DATE stays blank — handwritten on paper)
  Job Name: ...   Wrappers/Reel: N | unknown (job snapshot only, never invented)
  Reels section: one row per recorded reel.
  Boxes section: one row per EVERY carton.
  Separate reel totals, box totals and legacy box totals on the last page.
  Page Nos.: 1 \\2  (bottom right, old format with backslash)

One paint routine draws to any QPainter: the same code prints to a normal
office printer (QPrintDialog), saves a PDF (QPrinter PdfFormat — used for
local testing without a printer), and renders the on-screen preview images.
~40 rows per page like the old slips. Stdlib + PySide6 only.
"""
from __future__ import annotations
import os

from factory.db import get_conn, fmt_date_ddmmyy  # noqa: F401  (date fmt kept for parity)

ROWS_PER_PAGE = 40  # historic approx. capacity; paginate() now fits by height.

# ---------- printable geometry (millimetres, dpi-independent) ----------
# A4 210x297 with 14mm margins -> printable 182 x 269.
PRINTABLE_W_MM = 210.0 - 2 * 14.0
PRINTABLE_H_MM = 297.0 - 2 * 14.0
TOP_H_MM = 12.0 + 6.0 + 6.0 + 8.0  # title + job/customer + job name + wrappers
RH_MM = 5.3  # one body row
SECTION_H_MM = 2 * RH_MM  # section bar + column header
FOOTER_RESERVE_MM = 12.0  # footer line lives in the bottom 8mm + gap
TOTALS_GAP_MM = 3.0
TOTALS_LINE_MM = 6.0
TOTALS_LINES = 4  # reel totals, box totals, legacy totals, bundles
_BODY_USABLE_MM = PRINTABLE_H_MM - TOP_H_MM - FOOTER_RESERVE_MM

# Table column fractions of the printable width. Must sum to 1.0.
COL_FR = [0.30, 0.15, 0.13, 0.12, 0.30]


def _row_kind(r: dict) -> str:
    return r.get("kind", "Box" if r.get("weight_only") else "Reel")


def totals_height_mm(slip: dict) -> float:
    """Vertical space the last-page totals block needs (mm)."""
    h = TOTALS_GAP_MM + TOTALS_LINES * TOTALS_LINE_MM
    if (slip.get("totals") or {}).get("legacy_cartons"):
        h += TOTALS_LINE_MM  # one-line note under the totals
    return h


def _body_height_mm(page_rows: list[dict], start_sec: str | None = None) -> float:
    """Body height (section headers + rows) for the given rows in order."""
    used = 0.0
    cur_sec = start_sec
    for r in page_rows:
        kind = _row_kind(r)
        if kind != cur_sec:
            used += SECTION_H_MM
            cur_sec = kind
        used += RH_MM
    return used


def page_fits_mm(slip: dict, page_rows: list[dict], is_last: bool) -> bool:
    """Numeric fit check: body (+ totals on the last page) stays above the
    reserved footer area. Tests assert this for every paginated page."""
    used = _body_height_mm(page_rows)
    if is_last:
        used += totals_height_mm(slip)
    return used <= _BODY_USABLE_MM + 1e-9


def _roll_label(reel: dict, job_no) -> str:
    """Qualified reel identity for display. Read-only, never renumbers.

    roll_code when stored, else period + roll number (e.g. "2026-10 / Roll
    1"), else job + roll number (e.g. "J1001 / Roll 1"). Bare roll_no is
    never shown alone (it repeats every month).
    """
    code = (reel.get("roll_code") or "").strip()
    if code:
        return code
    try:
        n = int(reel.get("roll_no"))
    except (TypeError, ValueError):
        n = reel.get("roll_no")
    per = (reel.get("roll_period") or "").strip()
    if per:
        return f"{per} / Roll {n}"
    return f"J{job_no} / Roll {n}"


def get_slip(job_id: int, path: str | None = None) -> dict:
    """Assemble full-evidence slip data. Read-only. Raises ValueError for unknown job.

    Every recorded reel appears once (from the reels table) with a
    month-qualified identity (roll_code when stored, else period + roll
    number). Every carton appears as a box row: independent weighed boxes
    and legacy reel-linked cartons are flagged via the factual
    carton_items membership — nothing inferred, nothing renumbered, no
    data modified. Box weights never merge into reel weights: reel totals
    come from the reels table once; the independent-box total and the
    legacy aggregate (stored carton weights) are reported separately and
    clearly distinct.
    Snapshot records only: identities/weights/roll/carton numbers come
    straight from the DB; nothing is invented. Wrappers show only the
    job's snapshot value, else "unknown".
    """
    c = get_conn(path)
    try:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Job not found")
        job = dict(job)
        reels = [dict(r) for r in c.execute(
            "SELECT * FROM reels WHERE job_id=? ORDER BY roll_no", (job_id,)).fetchall()]
        cartons = [dict(r) for r in c.execute(
            "SELECT * FROM cartons WHERE job_id=? ORDER BY bundle_no", (job_id,)).fetchall()]
        # Factual legacy links only (what carton_items actually stores).
        # Labels are month-qualified so a bare roll_no is never displayed
        # alone. No membership is inferred: only stored rows are read.
        reel_to_carton: dict[int, str] = {}
        carton_to_rolls: dict[int, list[int]] = {}
        carton_to_labels: dict[int, list[str]] = {}
        for ci in c.execute(
                "SELECT ci.reel_id, ci.carton_id, ct.carton_no, r.roll_no,"
                " r.roll_period, r.roll_code"
                " FROM carton_items ci JOIN cartons ct ON ct.id=ci.carton_id"
                " JOIN reels r ON r.id=ci.reel_id WHERE ct.job_id=?",
                (job_id,)).fetchall():
            reel_to_carton[int(ci["reel_id"])] = ci["carton_no"]
            carton_to_rolls.setdefault(int(ci["carton_id"]), []).append(int(ci["roll_no"]))
            carton_to_labels.setdefault(int(ci["carton_id"]), []).append(
                _roll_label(dict(ci), job["job_no"]))
        for v in carton_to_rolls.values():
            v.sort()
    finally:
        c.close()

    reel_rows: list[dict] = []
    custs = set()
    for r in reels:
        cust = (r.get("customer", "") or "").strip().upper()
        if cust:
            custs.add(cust)
        reel_rows.append({
            "kind": "Reel",
            "roll_no": int(r["roll_no"]),
            "roll_period": (r.get("roll_period") or ""),
            "roll_code": (r.get("roll_code") or ""),
            "roll_label": _roll_label(r, job["job_no"]),
            "job_no": job["job_no"],
            "num_reels": 1,
            "weight_only": False,
            "gross": round(float(r["gross"]), 3),
            "core": round(float(r["core"]), 3),
            "net": round(float(r["net"]), 3),
            "status": r.get("status", ""),
            "carton_no": reel_to_carton.get(int(r["id"]), ""),
        })

    # Every carton appears as a box row. Independent weighed boxes link no
    # reels; legacy reel-linked cartons keep their factual carton_items
    # membership (rolls + stored weight shown, never re-added to reels).
    box_rows: list[dict] = []
    legacy_linked: list[dict] = []
    for ct in cartons:
        cust = (ct.get("customer", "") or "").strip().upper()
        if cust:
            custs.add(cust)
        linked_roll_nos = carton_to_rolls.get(int(ct["id"]), [])
        linked = bool(linked_roll_nos) or int(ct.get("num_reels") or 0) != 0
        stored_gross = round(float(ct["total_gross"]), 3)
        row = {
            "kind": "Box",
            "carton_no": ct["carton_no"],
            "bundle_no": int(ct["bundle_no"]),
            "box_period": (ct.get("box_period") or ""),
            "num_reels": int(ct.get("num_reels") or 0),
            "weight_only": not linked,
            "linked": linked,
            "rolls": ",".join(str(n) for n in linked_roll_nos),
            "roll_refs": ", ".join(carton_to_labels.get(int(ct["id"]), [])),
            "gross": stored_gross,
            "core": 0.0,
            "net": 0.0,
            "status": ct.get("status", ""),
            "note": ("legacy reel-linked carton" if linked
                     else "independent weighed box"),
        }
        box_rows.append(row)
        if linked:
            legacy_linked.append({
                "carton_no": ct["carton_no"],
                "bundle_no": int(ct["bundle_no"]),
                "rolls": ",".join(str(n) for n in linked_roll_nos),
                "roll_refs": ", ".join(carton_to_labels.get(int(ct["id"]), [])),
                "gross": stored_gross,
            })

    # Totals stay separate and are never mixed: reels counted once from the
    # reels table; independent boxes summed from weight-only cartons;
    # legacy aggregate is the stored carton weights, display-only.
    rg = round(sum(r["gross"] for r in reel_rows), 3)
    rc = round(sum(r["core"] for r in reel_rows), 3)
    rn = round(sum(r["net"] for r in reel_rows), 3)
    indep = [b for b in box_rows if not b["linked"]]
    leg = [b for b in box_rows if b["linked"]]
    bw = round(sum(b["gross"] for b in indep), 3)
    lw = round(sum(b["gross"] for b in leg), 3)
    totals = {"bundles": len(cartons), "reels": len(reel_rows),
              "gross": rg, "core": rc, "net": rn,
              "boxes": len(indep), "box_wt": bw,
              "legacy_cartons": len(leg), "legacy_box_wt": lw}
    out_rows = reel_rows + box_rows
    return {
        "job_no": job["job_no"],
        "job_name": job["job_name"],
        "customer": next(iter(custs)) if len(custs) == 1 else "",
        "wrappers_per_reel": job.get("wrappers_per_reel"),
        "product_name": job.get("product_name", "") or "",
        "reel_rows": reel_rows,
        "box_rows": box_rows,
        "legacy_linked": legacy_linked,
        "rows": out_rows,
        "totals": totals,
    }


def paginate(slip: dict) -> list[list[dict]]:
    """Split rows into pages that physically fit the printable area.

    Capacity is computed in millimetres from the same geometry paint_page
    uses (top block + per-section headers + rows + footer reserve, plus the
    totals block on the last page) — not a fixed row count. A section that
    starts or continues on a page costs one section-bar row plus one column
    header row; every body row costs one row height.
    """
    rows = slip["rows"]
    if not rows:
        return [[]]
    totals_h = totals_height_mm(slip)
    pages: list[list[dict]] = []
    i, n = 0, len(rows)
    while i < n:
        # If everything left fits on one last page (with totals), take it.
        rest_h = _body_height_mm(rows[i:], None)
        if rest_h + totals_h <= _BODY_USABLE_MM:
            pages.append(rows[i:])
            break
        # Otherwise fill one non-last page greedily up to the usable height.
        cur: list[dict] = []
        used = 0.0
        cur_sec: str | None = None
        while i < n:
            kind = _row_kind(rows[i])
            need = RH_MM + (SECTION_H_MM if kind != cur_sec else 0.0)
            if cur and used + need > _BODY_USABLE_MM + 1e-9:
                break
            cur.append(rows[i])
            used += need
            cur_sec = kind
            i += 1
        if not cur:  # single row never overflows (guard against no progress)
            cur.append(rows[i])
            i += 1
        pages.append(cur)
    return pages


# ---------- QPainter rendering (device pixels; pixel fonts stay exact) ----------
#
# NOTE: QPainter scales point-size fonts by the world transform, so drawing
# in millimetres with a scaled painter blows text up. Everything here is
# therefore computed in device pixels from the device dpi, with pixel-size
# fonts — identical output on a 600dpi printer and a 100dpi preview image.

def _font(dpi: int, pt: float, bold: bool = False):
    from PySide6.QtGui import QFont
    px = max(6, round(pt / 72 * dpi))
    for family in ("Arial", "Liberation Sans", "DejaVu Sans", "Noto Sans"):
        f = QFont(family)
        f.setPixelSize(px)
        f.setBold(bold)
        return f
    f = QFont()
    f.setPixelSize(px)
    f.setBold(bold)
    return f


def paint_page(p, area, dpi: int, slip: dict, page_rows: list[dict],
               page_no: int, page_count: int):
    """Draw one A4 page.

    area = printable rectangle in the painter's OWN device pixels: for a
    QPrinter (fullPage False) the painter origin is already the printable
    top-left, so callers pass QRectF(0, 0, w, h); for a QImage preview the
    origin is the paper top-left, so callers pass the inset rect. Either
    way paint_page draws strictly inside area — the right border is drawn
    1px inside the edge so the pen is never clipped.
    """
    from PySide6.QtCore import Qt, QRectF
    from PySide6.QtGui import QFontMetrics
    mm = dpi / 25.4
    x, y, W, H = area.x(), area.y(), area.width(), area.height()
    right = x + W - 1
    footer_top = y + H - FOOTER_RESERVE_MM * mm

    def text(r, s, font, align=Qt.AlignLeft | Qt.AlignVCenter):
        p.setFont(font)
        s = "" if s is None else str(s)
        try:
            fm = QFontMetrics(font)
            avail = int(r.width() - 2)
            if avail > 10 and fm.horizontalAdvance(s) > avail:
                s = fm.elidedText(s, Qt.ElideRight, avail)
        except Exception:
            pass
        p.save()
        try:
            p.setClipRect(r)
            p.drawText(r, align | Qt.TextSingleLine, s)
        finally:
            p.restore()

    # title
    text(QRectF(x, y, W, 10 * mm), "PACKING SLIP", _font(dpi, 15, True),
         Qt.AlignHCenter | Qt.AlignVCenter)
    y += 12 * mm
    f_head, f_row, f_bold = _font(dpi, 10), _font(dpi, 9), _font(dpi, 9, True)
    # header bands (match old captions; DATE blank for handwriting).
    # Long customer / job names are ellipsized, never overflow the border.
    text(QRectF(x, y, W, 6 * mm),
         f"Jobno: {slip['job_no']}     C.Name.: {slip['customer']}     DATE:", f_head)
    y += 6 * mm
    text(QRectF(x, y, W, 6 * mm), f"Job Name: {slip['job_name']}", f_head)
    y += 6 * mm
    wpr = slip.get("wrappers_per_reel")
    wline = f"Wrappers/Reel: {int(wpr)}" if wpr is not None else "Wrappers/Reel: unknown"
    text(QRectF(x, y, W, 6 * mm), wline, f_head)
    y += 8 * mm

    # table columns (fractions of printable width; sum == 1.0, last column
    # is wide because member-roll identities are long)
    fr = list(COL_FR)
    xs = [x]
    for w_ in fr[:-1]:
        xs.append(xs[-1] + W * w_)
    xs.append(x + W)
    rh = RH_MM * mm
    f_sec = _font(dpi, 9, True)

    def hline(y0, x0=None, x1=None):
        p.drawLine(int(x0 if x0 is not None else x), int(y0),
                   int(x1 if x1 is not None else right), int(y0))

    def vline(x0, y0, y1):
        p.drawLine(int(x0), int(y0), int(x0), int(y1))

    def section_bar(title: str):
        nonlocal y
        text(QRectF(x, y, W, rh), title, f_sec,
             Qt.AlignLeft | Qt.AlignVCenter)
        y += rh
        hline(y)

    def col_header(cells):
        nonlocal y
        top = y
        for j, h in enumerate(cells):
            text(QRectF(xs[j] + 1, y, xs[j + 1] - xs[j] - 2, rh), h, f_bold,
                 Qt.AlignHCenter | Qt.AlignVCenter)
        y += rh
        hline(top)
        hline(y)
        return top

    REEL_HEAD = ("Roll identity", "GROSS WT", "CORE WT", "NET WT", "In Box")
    BOX_HEAD = ("Carton No.", "Bundle", "BOX WT", "Type", "Rolls / Note")
    top = y
    cur_sec: str | None = None
    # Grid segments: vertical rules run through each section's column
    # header + body only (never through section-bar titles), and repeat
    # with the section header on every page.
    segments: list[tuple[float, float]] = []
    seg_top: float | None = None
    # Section continuity across pages: "cont'd" only when the SAME section
    # appeared on an earlier page (a brand-new section gets no suffix).
    all_rows: list[dict] = slip.get("rows", [])
    if page_rows:
        try:
            first_idx = all_rows.index(page_rows[0])
        except ValueError:
            first_idx = 0
    else:
        first_idx = 0
    prior_kinds = {_row_kind(r) for r in all_rows[:first_idx]}

    def open_section(kind: str):
        nonlocal y, top, cur_sec, seg_top
        if cur_sec == kind:
            return
        if cur_sec is not None and seg_top is not None:
            segments.append((seg_top, y))
        cur_sec = kind
        suffix = "  (cont'd)" if kind in prior_kinds else ""
        if kind == "Reel":
            section_bar("Reels" + suffix)
            top = col_header(list(REEL_HEAD))
        else:
            section_bar("Boxes" + suffix)
            top = col_header(list(BOX_HEAD))
        seg_top = top

    if not page_rows:
        text(QRectF(x, y, W, 8 * mm),
             "No reels or boxes recorded for this job yet.", f_row)
        y += 8 * mm
        hline(y)

    for r in page_rows:
        kind = _row_kind(r)
        open_section(kind)
        if kind == "Reel":
            in_box = r.get("carton_no") or "loose"
            cells = [str(r.get("roll_label") or r.get("roll_no")),
                     f"{r['gross']:.3f}",
                     f"{r['core']:.2f}", f"{r['net']:.3f}", str(in_box)]
        else:
            refs = r.get("roll_refs") or r.get("rolls") or ""
            cells = [str(r.get("carton_no", "")), str(r.get("bundle_no", "")),
                     f"{r['gross']:.3f}",
                     "legacy" if r.get("linked") else "indep.",
                     refs if r.get("linked") else "weighed box"]
        for j, val in enumerate(cells):
            a = Qt.AlignHCenter | Qt.AlignVCenter if j in (0, 1, 3, 4) else Qt.AlignRight | Qt.AlignVCenter
            text(QRectF(xs[j] + 1, y, xs[j + 1] - xs[j] - 2, rh), val, f_row, a)
        y += rh
        hline(y)
    if page_rows:
        if seg_top is not None:
            segments.append((seg_top, y))
        for (y0, y1) in segments:
            for j in range(1, 5):
                vline(xs[j], y0, y1)
            vline(x, y0, y1)
            vline(right, y0, y1)

    # totals live on the last page only (like the old print). Reel, box
    # and legacy weights stay separate; box weights are never added to
    # reel totals.
    if page_no == page_count:
        t = slip["totals"]
        y += TOTALS_GAP_MM * mm
        lines = [
            f"Reel totals: {t['reels']} reels  ·  Gross {t['gross']:.3f}Kgs"
            f"  ·  Core {t['core']:.3f}Kgs  ·  Net {t['net']:.3f}Kgs",
            f"Box totals: {t.get('boxes', 0)} boxes"
            f"  ·  Box Wt {t.get('box_wt', 0.0):.3f}Kgs",
            f"Legacy box totals: {t.get('legacy_cartons', 0)} cartons"
            f"  ·  Stored Wt {t.get('legacy_box_wt', 0.0):.3f}Kgs"
            "  (listed separately, not added)",
            f"Total bundles/cartons:  {t['bundles']}",
        ]
        if t.get("legacy_cartons"):
            lines.append(
                "Note: boxes are weighed independently;"
                " reel weights count each reel once.")
        for line in lines:
            text(QRectF(x, y, W, TOTALS_LINE_MM * mm), line, f_bold)
            y += TOTALS_LINE_MM * mm

    # footer, old format with backslash; the body (+ totals) always ends
    # above footer_top because paginate() reserves FOOTER_RESERVE_MM.
    text(QRectF(x, area.y() + H - 8 * mm, W, 6 * mm),
         f"Page Nos.: {page_no} \\{page_count}", _font(dpi, 9, True),
         Qt.AlignRight | Qt.AlignVCenter)


# ---------- outputs ----------

def _printer(a4_margins_mm: float = 14):
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtCore import QMarginsF
    from PySide6.QtGui import QPageSize, QPageLayout
    pr = QPrinter(QPrinter.PrinterMode.HighResolution)
    pr.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    pr.setPageOrientation(QPageLayout.Orientation.Portrait)
    m = QMarginsF(a4_margins_mm, a4_margins_mm, a4_margins_mm, a4_margins_mm)
    pr.setPageMargins(m, QPageLayout.Unit.Millimeter)
    return pr


def save_pdf(job_id: int, path: str, db_path: str | None = None,
             slip: dict | None = None) -> str:
    """Render the slip to an A4 PDF file. Returns the path.

    Pass a pre-fetched slip dict (e.g. data_access.slip_data(job_id) over
    LAN) to render without touching local SQLite; when omitted the slip is
    assembled from the local DB via get_slip(job_id, db_path).

    Raises RuntimeError when the painter/printer fails, so callers never
    report a successful save that did not happen.
    """
    from PySide6.QtPrintSupport import QPrinter
    from PySide6.QtGui import QPainter
    from PySide6.QtCore import QRectF
    if slip is None:
        slip = get_slip(job_id, db_path)
    pages = paginate(slip)
    pr = _printer()
    pr.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    pr.setOutputFileName(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    p = QPainter(pr)
    try:
        if not p.isActive():
            raise RuntimeError("Could not start PDF painter (QPainter not active).")
        dpi = pr.resolution()
        # QPrinter with fullPage False paints in printable-local coords:
        # the painter origin is already the printable top-left, so the
        # drawable rect starts at (0, 0) — NOT at pageRect().x/y (using
        # those would shift everything right/down by the margin and clip
        # the right border). paint_page draws strictly inside `area`, so a
        # zero-origin rect keeps PDF output and preview aligned.
        raw = pr.pageRect(QPrinter.Unit.DevicePixel)
        area = QRectF(0, 0, raw.width(), raw.height())
        for i, rows in enumerate(pages):
            if i and not pr.newPage():
                raise RuntimeError("Printer rejected a new PDF page.")
            paint_page(p, area, dpi, slip, rows, i + 1, len(pages))
        if getattr(p, "hasError", None) is not None:
            try:
                if p.hasError():
                    raise RuntimeError("PDF paint device reported an error.")
            except RuntimeError:
                raise
            except Exception:
                pass
    finally:
        p.end()
    if (not os.path.exists(path)) or os.path.getsize(path) == 0:
        raise RuntimeError("PDF was not written (empty or missing file).")
    return path


def print_slip(parent, job_id: int, printer_name: str | None = None,
               db_path: str | None = None, slip: dict | None = None) -> bool:
    """Print on a normal office printer. Returns True if a job was sent.

    Pass a pre-fetched slip dict (e.g. data_access.slip_data(job_id) over
    LAN) to print without touching local SQLite; when omitted the slip is
    assembled from the local DB via get_slip(job_id, db_path).

    Raises RuntimeError on painter/printer failure instead of claiming a
    successful print.
    """
    from PySide6.QtPrintSupport import QPrinterInfo
    from PySide6.QtGui import QPainter
    from PySide6.QtCore import QRectF
    if slip is None:
        slip = get_slip(job_id, db_path)
    pages = paginate(slip)
    pr = _printer()
    if printer_name:
        for info in QPrinterInfo.availablePrinters():
            if info.printerName() == printer_name:
                pr.setPrinterName(printer_name)
                break
    p = QPainter(pr)
    try:
        if not p.isActive():
            raise RuntimeError("Could not start the print job (QPainter not active).")
        dpi = pr.resolution()
        # Same printable-local origin as save_pdf (see above).
        raw = pr.pageRect(QPrinter.Unit.DevicePixel)
        area = QRectF(0, 0, raw.width(), raw.height())
        for i, rows in enumerate(pages):
            if i and not pr.newPage():
                raise RuntimeError("Printer rejected a new page.")
            paint_page(p, area, dpi, slip, rows, i + 1, len(pages))
        if getattr(p, "hasError", None) is not None:
            try:
                if p.hasError():
                    raise RuntimeError("Print device reported an error.")
            except RuntimeError:
                raise
            except Exception:
                pass
    finally:
        p.end()
    try:
        from PySide6.QtPrintSupport import QPrinter as _QP
        if pr.printerState() == _QP.PrinterState.Error:
            raise RuntimeError("Printer reports an error state.")
    except RuntimeError:
        raise
    except Exception:
        pass
    return True


def preview_images(job_id: int, db_path: str | None = None, dpi: int = 100,
                   slip: dict | None = None):
    """Render A4 pages to QImages for the on-screen preview (same paint code).

    Pass a pre-fetched slip dict (e.g. data_access.slip_data(job_id) over
    LAN) to render without touching local SQLite; when omitted the slip is
    assembled from the local DB via get_slip(job_id, db_path).
    """
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtCore import QRectF, Qt
    if slip is None:
        slip = get_slip(job_id, db_path)
    pages = paginate(slip)
    out = []
    w = int(210 / 25.4 * dpi)
    h = int(297 / 25.4 * dpi)
    m = 14 / 25.4 * dpi
    for i, rows in enumerate(pages):
        img = QImage(w, h, QImage.Format.Format_RGB32)
        img.fill(Qt.white)
        p = QPainter(img)
        try:
            paint_page(p, QRectF(m, m, w - 2 * m, h - 2 * m), dpi,
                       slip, rows, i + 1, len(pages))
        finally:
            p.end()
        out.append(img)
    return slip, out
