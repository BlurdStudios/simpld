"""Packing slip PDF + evening backup. Temp DB/files only — never production."""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from factory import auth as A
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-slip-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    _auth(tmp)
    return tmp


def _auth(p):
    """Real temp-user session on the temp DB (services mutations require auth)."""
    ensure_default_users(p)
    sess = A.authenticate("admin", "admin123", p)
    assert sess and sess.get("session_token"), "temp admin login failed"
    A.set_session(sess["session_token"])


def _app():
    # QPrinter/QImage painting is fatal without a QApplication instance.
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _pdf_text(path):
    out = subprocess.run(["pdftotext", "-layout", path, "-"],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, f"pdftotext failed: {out.stderr[:200]}"
    return out.stdout


def _assert_pages_fit(slip, pages):
    from factory import slip_pdf as SP
    assert abs(sum(SP.COL_FR) - 1.0) < 1e-9, SP.COL_FR
    for k, page in enumerate(pages):
        assert SP.page_fits_mm(slip, page, k == len(pages) - 1), \
            f"page {k + 1} body+totals exceeds usable printable height"


def _job_with_cartons(n, customer="SLIP CO"):
    p = _fresh_db()  # always a temp DB — never production
    job = S.create_job("SLIP TEST JOB", size_mm=160, core_wt=0.280, path=p)
    for i in range(n):
        r = S.create_reel(job["id"], gross=round(11.0 + (i % 5) * 0.1, 3),
                           core=0.280, customer=customer, path=p)
        S.pack_carton(job["id"], [r["id"]], path=p)
    return p, job


def test_slip_data_totals_and_customer():
    from factory import slip_pdf as SP
    p, job = _job_with_cartons(3)
    slip = SP.get_slip(job["id"], p)
    assert slip["job_no"] == job["job_no"]
    assert slip["customer"] == "SLIP CO", slip["customer"]
    # pack_carton stores legacy reel-linked cartons: every reel appears
    # once AND every carton appears as a (linked) box row.
    assert [r["roll_no"] for r in slip["reel_rows"]] == [1, 2, 3]
    assert len(slip["box_rows"]) == 3
    assert all(b["linked"] for b in slip["box_rows"])
    assert len(slip["rows"]) == 6
    # Qualified identity: code when stored, else period accompanies the number.
    for r in slip["reel_rows"]:
        assert r["roll_label"], r
        assert r["roll_code"] or r["roll_period"], r
        assert str(r["roll_no"]) in r["roll_label"], r["roll_label"]
    t = slip["totals"]
    assert t["bundles"] == 3 and t["reels"] == 3
    assert t["boxes"] == 0 and t["box_wt"] == 0.0
    assert t["legacy_cartons"] == 3
    reel_gross = sum(r["gross"] for r in slip["reel_rows"])
    assert abs(reel_gross - t["gross"]) < 0.01
    # Legacy aggregate is reported separately — never merged into reel totals.
    assert abs(sum(b["gross"] for b in slip["box_rows"]) - t["legacy_box_wt"]) < 0.01
    assert t["gross"] == reel_gross  # box weights add nothing to reels


def test_slip_mixed_customers_blank():
    p = _fresh_db()
    job = S.create_job("MIXED JOB", core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.0, core=0.280, customer="A CO", path=p)
    r2 = S.create_reel(job["id"], gross=11.1, core=0.280, customer="B CO", path=p)
    S.pack_carton(job["id"], [r1["id"]], path=p)
    S.pack_carton(job["id"], [r2["id"]], path=p)
    from factory import slip_pdf as SP
    assert SP.get_slip(job["id"], p)["customer"] == ""


def test_pagination_fits_printable_height():
    from factory import slip_pdf as SP
    p, job = _job_with_cartons(45)
    slip = SP.get_slip(job["id"], DB.DB_PATH)
    # 45 reels + 45 legacy carton rows = 90 rows. Height-fit pages: the
    # section change costs a header, so pages split 40/38/12.
    assert len(slip["rows"]) == 90, len(slip["rows"])
    pages = SP.paginate(slip)
    assert len(pages) == 3, [len(x) for x in pages]
    assert [len(x) for x in pages] == [40, 38, 12], [len(x) for x in pages]
    assert sum(len(x) for x in pages) == 90
    _assert_pages_fit(slip, pages)


def test_save_pdf_full_text_and_layout():
    from factory import slip_pdf as SP
    _app()
    p, job = _job_with_cartons(45)
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-pdf-"), "slip.pdf")
    SP.save_pdf(job["id"], out, DB.DB_PATH)
    with open(out, "rb") as f:
        head = f.read(5)
    assert head == b"%PDF-", head
    assert os.path.getsize(out) > 5000, "90-row 3-page PDF should have substance"
    slip = SP.get_slip(job["id"], DB.DB_PATH)
    pages = SP.paginate(slip)
    _assert_pages_fit(slip, pages)
    # Full records in the PDF text — every reel label, carton no and the
    # separate totals — not a header-only check.
    txt = _pdf_text(out)
    for r in slip["reel_rows"]:
        assert r["roll_label"] in txt, r["roll_label"]
    for b in slip["box_rows"]:
        assert DB.bare_carton_no(b["carton_no"]) in txt, b["carton_no"]
    assert "Reel totals" in txt and "Box totals" in txt
    assert "Legacy box totals" in txt and "Total boxes" in txt
    assert txt.count("Page Nos.:") == len(pages), txt.count("Page Nos.:")
    for banned in ("qualified identity", "NOT added",
                   "independent boxes + legacy linked"):
        assert banned not in txt, banned


def test_preview_matches_print_pages():
    from factory import slip_pdf as SP
    _app()
    p, job = _job_with_cartons(45)
    slip, imgs = SP.preview_images(job["id"], DB.DB_PATH)
    pages = SP.paginate(slip)
    # Same renderer, same pagination: preview page count == PDF page count.
    assert len(imgs) == len(pages) == 3, (len(imgs), len(pages))
    assert slip["totals"]["bundles"] == 45
    assert slip["totals"]["reels"] == 45
    assert slip["totals"]["legacy_cartons"] == 45
    for img in imgs:
        assert not img.isNull() and img.width() > 700
    _assert_pages_fit(slip, pages)


def test_section_transition_paginates_before_footer():
    """Boundary: 39 reels + 1 box must paginate 39/1 so the box section
    header + row + totals never overlap the reserved footer."""
    from factory import slip_pdf as SP
    _app()
    p = _fresh_db()
    job = S.create_job("BOUNDARY JOB", core_wt=0.280, path=p)
    c = DB.get_conn(p)
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(reels)").fetchall()}
        for i in range(1, 40):
            row = {"job_id": job["id"], "roll_no": i, "gross": 11.0,
                   "core": 0.280, "net": 10.72, "shift": "DAY",
                   "supervisor": "", "customer": "ACME", "status": "loose",
                   "created_at": DB.now_iso()}
            if "roll_period" in cols:
                row["roll_period"] = "2026-10"
            if "roll_code" in cols:
                row["roll_code"] = f"R2026-10-{job['job_no']}-{i:04d}"
            keys = [k for k in row if k in cols]
            c.execute(f"INSERT INTO reels({','.join(keys)})"
                      f" VALUES({','.join('?' for _ in keys)})",
                      tuple(row[k] for k in keys))
        ccols = {r["name"] for r in c.execute("PRAGMA table_info(cartons)").fetchall()}
        row = {"carton_no": "C0001", "job_id": job["id"], "bundle_no": 1,
               "num_reels": 0, "total_gross": 5.5, "total_core": 0.0,
               "total_net": 5.5, "shift": "DAY", "customer": "ACME",
               "status": "Ready", "packed_at": DB.now_iso()}
        if "box_period" in ccols:
            row["box_period"] = "2026-10"
        keys = [k for k in row if k in ccols]
        c.execute(f"INSERT INTO cartons({','.join(keys)})"
                  f" VALUES({','.join('?' for _ in keys)})",
                  tuple(row[k] for k in keys))
        c.commit()
    finally:
        c.close()
    slip = SP.get_slip(job["id"], p)
    assert len(slip["rows"]) == 40
    pages = SP.paginate(slip)
    assert [len(x) for x in pages] == [39, 1], [len(x) for x in pages]
    _assert_pages_fit(slip, pages)
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-pdf-"), "boundary.pdf")
    SP.save_pdf(job["id"], out, p)
    txt = _pdf_text(out)
    assert "C0001" in txt
    assert "Reel totals" in txt and "Total boxes" in txt
    assert txt.count("Page Nos.:") == 2


def test_slip_dialog_table_and_preview():
    from factory.ui.styles import APP_QSS
    from factory import slip_pdf as SP
    p, job = _job_with_cartons(2)
    app = _app()
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    from factory.ui.label_preview import PackingSlipDialog
    d = PackingSlipDialog(None, job["id"])
    slip = SP.get_slip(job["id"], p)
    # Dialog lists the same full-evidence row set: 2 reels + 2 legacy boxes.
    assert d.table.rowCount() == len(slip["rows"]) == 4, d.table.rowCount()
    kinds = [d.table.item(i, 0).text() for i in range(d.table.rowCount())]
    assert kinds.count("Reel") == 2 and kinds.count("Box") == 2, kinds
    assert f"J:{job['job_no']}" in d.windowTitle()
    # Teardown hygiene: a live top-level dialog at interpreter shutdown
    # crashes native Windows Qt (access violation after a green summary).
    d.close()
    d.deleteLater()
    app.processEvents()


def test_evening_backup_copies_db():
    from factory import backup as B
    p = _fresh_db()
    S.create_job("BACKUP JOB", core_wt=0.280, path=p)
    dest = tempfile.mkdtemp(prefix="simpld-bak-")
    out = B.backup_db(dest, src=p)
    assert os.path.exists(out) and out.startswith(dest)
    assert os.path.getsize(out) == os.path.getsize(p)
    assert os.path.basename(out).startswith("simpld_") and out.endswith(".db")


if __name__ == "__main__":
    test_slip_data_totals_and_customer()
    test_slip_mixed_customers_blank()
    test_pagination_fits_printable_height()
    test_save_pdf_full_text_and_layout()
    test_preview_matches_print_pages()
    test_section_transition_paginates_before_footer()
    test_slip_dialog_table_and_preview()
    test_evening_backup_copies_db()
    print("test_slip_pdf PASS")
