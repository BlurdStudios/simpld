"""Job batch codes + mixed-batch dispatch + new sticker layouts.

Covers: job_code normalization/storage, additive migration on legacy DBs,
_job_head formatting, the full-width job-name bands (C-divider gap) with
the date beside Gross Wt (reel) / Date|No-of-Reels row (carton), the
smaller QR box, and mixed-batch dispatch scans with per-batch slip
subtotals. Temp DB only.
"""
import os
import sqlite3

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import auth as A
from factory import db as DB
from factory import services as S
from factory.db import init_db, job_label
from tests._auth_helper import ensure_default_users, reset_auth


def _db(tmp_path, name="factory.db"):
    p = str(tmp_path / name)
    DB.DB_PATH = p
    init_db(p)
    ensure_default_users(p)
    assert A.authenticate("admin", "admin123", p) is not None
    return p


def _dark(px, x, y):
    return sum(px[x, y][:3]) < 150


def _vfrac(img, x, y0, y1):
    """Fraction of dark pixels along vertical line x in [y0, y1)."""
    px = img.load()
    n = max(1, y1 - y0)
    return sum(1 for y in range(y0, y1) if _dark(px, x, y)) / n


def _bands(weights, H, m=6):
    ys = [float(m)]
    for w in weights:
        ys.append(ys[-1] + (H - 2 * m) * w / sum(weights))
    ys[-1] = float(H - m)
    return [(ys[i], ys[i + 1]) for i in range(len(weights))]


def test_job_code_normalized_and_legacy_blank(tmp_path):
    p = _db(tmp_path)
    try:
        prod = S.create_product("CODE PROD", 100, path=p)
        j = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                     job_code="  12dec  ")
        assert j["job_code"] == "12DEC", j
        j2 = S.create_job("CODE LEGACY", core_wt=0.28, path=p)
        assert j2.get("job_code", "") == "", j2
        long_code = S.create_job_for_product(
            prod["id"], core_wt=0.28, path=p, job_code="X" * 99)
        assert len(long_code["job_code"]) == 32
    finally:
        reset_auth()


def test_job_code_migration_keeps_legacy_rows(tmp_path):
    p = str(tmp_path / "legacy.db")
    c = sqlite3.connect(p)
    try:
        c.executescript(DB.SCHEMA.replace(
            "  job_code TEXT NOT NULL DEFAULT '',\n", ""))
        c.execute("INSERT INTO jobs(job_no, job_name, core_wt, status, created_at)"
                  " VALUES(4169, 'OLD JOB', 0.5, 'open', '2026-01-01 00:00:00')")
        c.commit()
    finally:
        c.close()
    DB.DB_PATH = p
    init_db(p)
    ensure_default_users(p)
    assert A.authenticate("admin", "admin123", p) is not None
    try:
        c = DB.get_conn(p)
        try:
            cols = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
            assert "job_code" in cols
            row = c.execute("SELECT job_no, job_name, job_code FROM jobs WHERE job_no=4169").fetchone()
            assert row["job_no"] == 4169 and row["job_name"] == "OLD JOB"
            assert row["job_code"] == ""
        finally:
            c.close()
    finally:
        reset_auth()


def test_job_head_and_label():
    from factory.hardware.labels import _job_head
    assert _job_head(4167, "12dec") == "J:4167 12DEC"
    assert _job_head(4167, "") == "J:4167"
    assert _job_head(4167, None) == "J:4167"
    assert job_label({"job_no": 1, "job_name": "N", "job_code": "a1"}) == "J:1 [A1] — N"
    assert job_label({"job_no": 1, "job_name": "N", "job_code": ""}) == "J:1 — N"


def test_reel_layout_job_full_width_date_beside_gross():
    from factory.hardware import labels as L
    job = {"job_no": 4167, "job_name": "LAYOUT JOB", "job_code": "12DEC"}
    reel = {"gross": 11.38, "core": 0.28, "net": 11.10,
            "created_at": "2026-10-03 10:00:00", "roll_no": 23}
    img = L.render_reel(job, reel)
    W, H = img.size
    assert (W, H) == (799, 399)
    m = 6
    xC = int(m + (W - 2 * m) * 0.70)
    bands = _bands(L.REEL_ROWS, H)
    job_top, job_bot = bands[1]
    # C divider skips the full-width job band (only text crosses it).
    mid = int((job_top + job_bot) / 2)
    assert _vfrac(img, xC, mid - 8, mid + 8) < 0.5, "no divider through job name"
    # ... but splits Gross Wt | value | Date one row down.
    g_top, g_bot = (int(bands[2][0]) + 2, int(bands[2][1]) - 2)
    assert _vfrac(img, xC, g_top, g_bot) > 0.9, "divider beside gross row"
    # Header code adds glyphs to the J cell.
    px = img.load()
    x0 = int(xC + 4)
    hdr = bands[0]
    dark_with = sum(1 for x in range(x0, W - 8, 2)
                    for y in range(int(hdr[0]) + 2, int(hdr[1]) - 2)
                    if _dark(px, x, y))
    img2 = L.render_reel({"job_no": 4167, "job_name": "LAYOUT JOB"}, reel)
    px2 = img2.load()
    dark_without = sum(1 for x in range(x0, W - 8, 2)
                       for y in range(int(hdr[0]) + 2, int(hdr[1]) - 2)
                       if _dark(px2, x, y))
    assert dark_with > dark_without, "batch code prints in the header"


def test_carton_layout_date_row_down_qr_starts_at_tare():
    from factory.hardware import labels as L
    detail = {"job_no": 4167, "job_name": "LAYOUT JOB", "job_code": "14DEC",
              "carton_no": "C0007", "bundle_no": 1,
              "packed_at": "2026-10-10 10:00:00", "num_reels": 2,
              "total_gross": 20.5, "total_core": 0.56, "total_net": 19.94,
              "box_wt": 1.4, "reels": [{"roll_no": 1}, {"roll_no": 2}],
              "qr": "VASAVI:C0007"}
    img = L.render_carton(detail)
    W, H = img.size
    assert (W, H) == (799, 399)
    m = 6
    xC = int(m + (W - 2 * m) * 0.70)
    weights = [0.10, 0.11, 0.105, 0.10, 0.095, 0.13, 0.125, 0.235]
    bands = _bands(weights, H)
    # Job band has no divider; tare band (QR box top) does.
    jm = int(sum(bands[1]) / 2)
    assert _vfrac(img, xC, jm - 6, jm + 6) < 0.5, "no divider through job name"
    tm = int(sum(bands[3]) / 2)
    assert _vfrac(img, xC, int(bands[3][0]) + 2, int(bands[3][1]) - 2) > 0.9, \
        "QR box starts at the tare row"
    assert tm > 0


def test_mixed_batch_dispatch_and_slip_batches(tmp_path):
    p = _db(tmp_path)
    try:
        prod = S.create_product("BATCH PROD", 100, path=p)
        j1 = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                      job_code="12DEC")
        j2 = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                      job_code="14DEC")
        for j in (j1, j2):
            r1 = S.create_reel(j["id"], gross=5.0, core=0.28, path=p)
            r2 = S.create_reel(j["id"], gross=5.1, core=0.28, path=p)
            S.pack_carton(j["id"], [r1["id"], r2["id"]], path=p)
        o = S.create_dispatch_order("BATCH CUST", j1["id"],
                                    required_pieces=100_000, path=p,
                                    customer_po="PO-1", piece_unit="wrapper")
        boxes = S.ready_stock(path=p)
        assert len(boxes) == 2
        for b in boxes:
            S.scan_into_order(o["id"], b["carton_no"], path=p)
        # Same product, other batch: accepted. Other product: rejected.
        prod2 = S.create_product("OTHER BATCH PROD", 50, path=p)
        jo = S.create_job_for_product(prod2["id"], core_wt=0.28, path=p,
                                      job_code="XX")
        ro = S.create_reel(jo["id"], gross=5.0, core=0.28, path=p)
        ro2 = S.create_reel(jo["id"], gross=5.0, core=0.28, path=p)
        S.pack_carton(jo["id"], [ro["id"], ro2["id"]], path=p)
        ob = S.ready_stock(jo["id"], path=p)[0]
        try:
            S.scan_into_order(o["id"], ob["carton_no"], path=p)
        except ValueError as e:
            assert "Product mismatch" in str(e)
        else:
            raise AssertionError("cross-product scan must be rejected")
        slip = S.dispatch_slip_data(o["id"], path=p)
        assert slip["loaded_pieces"] == 400, slip
        assert {b["job_code"] for b in slip["batches"]} == {"12DEC", "14DEC"}
        assert all(b["boxes"] == 1 and b["pieces"] == 200 for b in slip["batches"])
        assert {r["job_code"] for r in slip["rows"]} == {"12DEC", "14DEC"}
        from factory.dispatch_slip_render import render_html
        html = render_html(slip)
        assert "Batch (job code)" in html
        assert "12DEC" in html and "14DEC" in html
    finally:
        reset_auth()


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        import pathlib
        test_job_code_normalized_and_legacy_blank(pathlib.Path(t))
        test_job_code_migration_keeps_legacy_rows(pathlib.Path(t))
    test_job_head_and_label()
    test_reel_layout_job_full_width_date_beside_gross()
    test_carton_layout_date_row_down_qr_starts_at_tare()
    import tempfile as _t
    with _t.TemporaryDirectory() as t:
        import pathlib as _p
        test_mixed_batch_dispatch_and_slip_batches(_p.Path(t))
        test_job_slip_has_code_column_and_batch_line(_p.Path(t))
        test_order_packing_summary_mixed_batches(_p.Path(t))
    print("test_job_code_batches PASS")


def test_job_slip_has_code_column_and_batch_line(tmp_path):
    from factory import slip_pdf as SP
    p = _db(tmp_path)
    try:
        prod = S.create_product("SLIP CODE PROD", 100, path=p)
        j = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                     job_code="12DEC")
        for g in (5.0, 5.1):
            S.create_reel(j["id"], gross=g, core=0.28, path=p)
        reels = S.loose_reels(j["id"], path=p)
        S.pack_carton(j["id"], [r["id"] for r in reels], path=p)
        S.pack_weight_carton(j["id"], 7.5, path=p)
        slip = SP.get_slip(j["id"], p)
        assert slip["job_code"] == "12DEC"
        assert all(r.get("job_code") == "12DEC" for r in slip["rows"])
        box = next(b for b in slip["box_rows"] if b["linked"])
        assert box["rolls"] == "1,2", box  # stored bare, paint spaces it
        assert slip["batches"][0]["reels"] == 2
        assert slip["batches"][0]["boxes"] == 2
        assert "12DEC" in SP.job_batch_line(slip)
        assert abs(sum(SP.SLIP_COL_FR) - 1.0) < 1e-9
        assert abs(sum(SP.ORDER_COL_FR) - 1.0) < 1e-9
        pages = SP.paginate(slip)
        for k, pg in enumerate(pages):
            assert SP.page_fits_mm(slip, pg, k == len(pages) - 1)
    finally:
        reset_auth()


def test_order_packing_summary_mixed_batches(tmp_path):
    import os as _os
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from factory import slip_pdf as SP
    p = _db(tmp_path)
    try:
        prod = S.create_product("ORD SUM PROD", 100, path=p)
        j1 = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                      job_code="12DEC")
        j2 = S.create_job_for_product(prod["id"], core_wt=0.28, path=p,
                                      job_code="14DEC")
        for j in (j1, j2):
            r1 = S.create_reel(j["id"], gross=5.0, core=0.28, path=p)
            r2 = S.create_reel(j["id"], gross=5.1, core=0.28, path=p)
            S.pack_carton(j["id"], [r1["id"], r2["id"]], path=p)
        o = S.create_dispatch_order("SUM CUST", j1["id"],
                                    required_pieces=100_000, path=p,
                                    customer_po="PO-1", piece_unit="wrapper")
        for jj in (j1, j2):
            for b in S.ready_stock(jj["id"], path=p):
                S.scan_into_order(o["id"], b["carton_no"], path=p)
        summ = S.order_packing_summary(o["id"], path=p)
        assert len(summ["rows"]) == 2
        assert {r["job_code"] for r in summ["rows"]} == {"12DEC", "14DEC"}
        assert all(r["rolls"] for r in summ["rows"])  # bare rolls listed
        assert summ["totals"]["rolls"] == 4
        assert summ["totals"]["boxes"] == 2
        assert {b["label"] for b in summ["batches"]} == {"12DEC", "14DEC"}
        assert all(b["rolls"] == 2 for b in summ["batches"])
        assert "12DEC" in SP.order_batch_line(summ)
        pages = SP.order_paginate(summ)
        assert pages and all(SP.order_page_fits_mm(summ, pg) for pg in pages)
        assert len(SP.preview_order_images(summ)) == len(pages)
        pdf = str(tmp_path / "packing_summary.pdf")
        SP.save_order_pdf(summ, pdf)
        assert _os.path.getsize(pdf) > 0
    finally:
        reset_auth()
