"""Packing-slip full evidence: independent reels + boxes. Temp DB/files only.

Row setup uses direct SQL inserts (test-only helpers) so these tests do not
depend on the sibling task's in-progress services.py/db.py edits. The slip
code under test (factory/slip_pdf.py) reads via db.get_conn SELECTs only.
"""
import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from tests._auth_helper import ensure_default_users

_PERIOD = "2026-10"


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-slipev-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


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


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _cols(p, table):
    c = DB.get_conn(p)
    try:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
    finally:
        c.close()


def _add_reel(p, job_id, roll_no, gross, core=0.280, customer="", status="loose"):
    """Direct snapshot row: real roll_no + measured weights, nothing invented."""
    c = DB.get_conn(p)
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(reels)").fetchall()}
        row = {"job_id": job_id, "roll_no": roll_no,
               "gross": round(gross, 3), "core": round(core, 3),
               "net": round(gross - core, 3),
               "shift": "DAY", "supervisor": "", "customer": customer.strip().upper(),
               "status": status, "created_at": DB.now_iso()}
        if "roll_period" in cols:
            row["roll_period"] = _PERIOD
        if "roll_code" in cols:
            row["roll_code"] = f"R{_PERIOD}-{int(job_id)}-{int(roll_no):04d}"
        keys = [k for k in row if k in cols]
        cur = c.execute(f"INSERT INTO reels({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                        tuple(row[k] for k in keys))
        c.commit()
        return dict(c.execute("SELECT * FROM reels WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        c.close()


def _next_carton_no(c):
    r = c.execute("SELECT MAX(CAST(SUBSTR(carton_no,2) AS INTEGER)) m FROM cartons"
                  " WHERE carton_no LIKE 'C%'").fetchone()
    return f"C{((r['m'] or 0) + 1):04d}"


def _add_box(p, job_id, bundle_no, weight, customer=""):
    """Independent weighed box: measured weight only, links NO reels."""
    c = DB.get_conn(p)
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(cartons)").fetchall()}
        row = {"carton_no": _next_carton_no(c), "job_id": job_id, "bundle_no": bundle_no,
               "num_reels": 0, "total_gross": round(weight, 3), "total_core": 0.0,
               "total_net": round(weight, 3), "shift": "DAY",
               "customer": customer.strip().upper(), "status": "Ready",
               "packed_at": DB.now_iso()}
        if "box_period" in cols:
            row["box_period"] = _PERIOD
        keys = [k for k in row if k in cols]
        cur = c.execute(f"INSERT INTO cartons({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                        tuple(row[k] for k in keys))
        c.commit()
        return dict(c.execute("SELECT * FROM cartons WHERE id=?", (cur.lastrowid,)).fetchone())
    finally:
        c.close()


def _add_linked_carton(p, job_id, bundle_no, reel_ids):
    """Legacy reel-linked carton: factual carton_items membership (kept as-is)."""
    c = DB.get_conn(p)
    try:
        cols = {r["name"] for r in c.execute("PRAGMA table_info(cartons)").fetchall()}
        ph = ",".join("?" for _ in reel_ids)
        reels = c.execute(f"SELECT * FROM reels WHERE id IN ({ph})", tuple(reel_ids)).fetchall()
        tg = round(sum(float(r["gross"]) for r in reels), 3)
        tc = round(sum(float(r["core"]) for r in reels), 3)
        tn = round(sum(float(r["net"]) for r in reels), 3)
        row = {"carton_no": _next_carton_no(c), "job_id": job_id, "bundle_no": bundle_no,
               "num_reels": len(reels), "total_gross": tg, "total_core": tc, "total_net": tn,
               "shift": "DAY", "customer": "", "status": "Ready", "packed_at": DB.now_iso()}
        if "box_period" in cols:
            row["box_period"] = _PERIOD
        keys = [k for k in row if k in cols]
        cur = c.execute(f"INSERT INTO cartons({','.join(keys)}) VALUES({','.join('?' for _ in keys)})",
                        tuple(row[k] for k in keys))
        cid = cur.lastrowid
        for r in reels:
            c.execute("INSERT INTO carton_items(carton_id, reel_id) VALUES(?,?)", (cid, r["id"]))
            c.execute("UPDATE reels SET status='packed' WHERE id=?", (r["id"],))
        c.commit()
        return dict(c.execute("SELECT * FROM cartons WHERE id=?", (cid,)).fetchone())
    finally:
        c.close()


def test_reels_no_boxes_produces_slip():
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("REELS ONLY JOB", core_wt=0.280, path=p)
    r1 = _add_reel(p, job["id"], 1, 11.0, customer="ACME")
    r2 = _add_reel(p, job["id"], 2, 12.0, customer="ACME")
    slip = SP.get_slip(job["id"], p)
    assert len(slip["reel_rows"]) == 2
    assert slip["box_rows"] == []
    assert [r["roll_no"] for r in slip["reel_rows"]] == [r1["roll_no"], r2["roll_no"]]
    # Qualified identity: stored code wins, else period accompanies the number.
    for r in slip["reel_rows"]:
        assert r["roll_label"] == r["roll_code"], r["roll_label"]
        assert str(r["roll_no"]) in r["roll_label"] or r["roll_code"] in r["roll_label"]
    assert all(r["kind"] == "Reel" for r in slip["rows"])
    t = slip["totals"]
    assert t["reels"] == 2 and t["boxes"] == 0
    assert abs(t["gross"] - 23.0) < 0.001
    assert abs(t["net"] - (23.0 - 0.56)) < 0.01
    assert slip["customer"] == "ACME"
    assert len(SP.paginate(slip)) == 1


def test_no_reels_no_boxes_empty_slip():
    from factory import slip_pdf as SP
    _app()
    p = _fresh_db()
    job = S.create_job("EMPTY JOB", core_wt=0.280, path=p)
    slip = SP.get_slip(job["id"], p)
    assert slip["rows"] == [] and slip["reel_rows"] == [] and slip["box_rows"] == []
    assert SP.paginate(slip) == [[]]
    t = slip["totals"]
    assert t["reels"] == 0 and t["boxes"] == 0 and t["gross"] == 0
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-slipev-"), "empty.pdf")
    SP.save_pdf(job["id"], out, p)
    assert open(out, "rb").read(5) == b"%PDF-"
    _, imgs = SP.preview_images(job["id"], p)
    assert len(imgs) == 1 and not imgs[0].isNull()


def test_mixed_independent_no_double_count():
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("MIXED INDEP JOB", core_wt=0.280, path=p)
    r1 = _add_reel(p, job["id"], 1, 11.0)
    r2 = _add_reel(p, job["id"], 2, 12.0)
    b1 = _add_box(p, job["id"], 1, 5.5)
    b2 = _add_box(p, job["id"], 2, 6.25)
    slip = SP.get_slip(job["id"], p)
    assert [r["roll_no"] for r in slip["reel_rows"]] == [r1["roll_no"], r2["roll_no"]]
    assert [b["carton_no"] for b in slip["box_rows"]] == [b1["carton_no"], b2["carton_no"]]
    for b in slip["box_rows"]:
        assert b["kind"] == "Box" and b["core"] == 0.0
    # independence: weight-only cartons link no reels
    c = DB.get_conn(p)
    try:
        n = c.execute("SELECT COUNT(*) n FROM carton_items WHERE carton_id IN (?,?)",
                      (b1["id"], b2["id"])).fetchone()["n"]
    finally:
        c.close()
    assert n == 0
    assert all(r["carton_no"] == "" for r in slip["reel_rows"])
    # Every carton appears as a box row, flagged independent.
    assert all(not b["linked"] and b["weight_only"] for b in slip["box_rows"])
    assert all(b["note"] == "independent weighed box" for b in slip["box_rows"])
    t = slip["totals"]
    assert t["reels"] == 2 and t["boxes"] == 2
    assert t["legacy_cartons"] == 0 and t["legacy_box_wt"] == 0.0
    assert abs(t["gross"] - 23.0) < 0.001          # reels only
    assert abs(t["box_wt"] - 11.75) < 0.001        # boxes only, not merged
    assert abs(sum(r["gross"] for r in slip["reel_rows"]) - t["gross"]) < 0.001


def test_legacy_linked_counts_each_reel_once():
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("LEGACY JOB", core_wt=0.280, path=p)
    r1 = _add_reel(p, job["id"], 1, 11.0)
    r2 = _add_reel(p, job["id"], 2, 12.0)
    r3 = _add_reel(p, job["id"], 3, 13.0)  # stays loose
    _add_linked_carton(p, job["id"], 1, [r1["id"], r2["id"]])
    slip = SP.get_slip(job["id"], p)
    assert len(slip["reel_rows"]) == 3  # every recorded reel appears
    linked = [r for r in slip["reel_rows"] if r["carton_no"]]
    loose = [r for r in slip["reel_rows"] if not r["carton_no"]]
    assert len(linked) == 2 and len(loose) == 1 and loose[0]["roll_no"] == r3["roll_no"]
    assert linked[0]["carton_no"] == linked[1]["carton_no"]  # factual membership kept
    # The legacy carton itself appears as a box row (not metadata-only):
    # stored gross shown, member rolls factual, flagged linked.
    assert len(slip["box_rows"]) == 1
    lb = slip["box_rows"][0]
    assert lb["linked"] and not lb["weight_only"]
    assert lb["rolls"] == "1,2", lb["rolls"]
    assert abs(lb["gross"] - 23.0) < 0.001  # factual stored carton weight
    assert slip["totals"]["legacy_cartons"] == 1
    assert len(slip["rows"]) == 4  # 3 reels + 1 legacy carton row
    t = slip["totals"]
    assert t["reels"] == 3 and abs(t["gross"] - 36.0) < 0.001  # counted once
    assert t["boxes"] == 0 and t["box_wt"] == 0.0
    # Legacy aggregate is distinct from reel totals — never added to them.
    assert abs(t["legacy_box_wt"] - 23.0) < 0.001
    assert abs(sum(r["gross"] for r in slip["reel_rows"]) - t["gross"]) < 0.001


def test_multipage_pdf_and_preview():
    from factory import slip_pdf as SP
    _app()
    p = _fresh_db()
    job = S.create_job("BIG JOB", core_wt=0.280, path=p)
    for i in range(45):
        _add_reel(p, job["id"], i + 1, round(11.0 + (i % 5) * 0.1, 3))
    _add_box(p, job["id"], 1, 5.0)
    _add_box(p, job["id"], 2, 6.0)
    slip = SP.get_slip(job["id"], p)
    assert len(slip["rows"]) == 47
    assert slip["totals"]["boxes"] == 2
    assert slip["totals"]["legacy_cartons"] == 0
    assert abs(slip["totals"]["legacy_box_wt"] - 0.0) < 0.001
    assert abs(slip["totals"]["box_wt"] - 11.0) < 0.001
    pages = SP.paginate(slip)
    # Height-fit: 45 reels fill page 1 (40) then 5 reels + 2 boxes + totals
    # share page 2 — the box section header is counted, not crammed.
    assert len(pages) == 2 and len(pages[0]) == 40 and len(pages[1]) == 7
    _assert_pages_fit(slip, pages)
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-slipev-"), "big.pdf")
    SP.save_pdf(job["id"], out, p)
    assert open(out, "rb").read(5) == b"%PDF-"
    assert os.path.getsize(out) > 5000
    # Full records in the PDF text — every reel + box + separate totals.
    txt = _pdf_text(out)
    for r in slip["reel_rows"]:
        assert r["roll_label"] in txt, r["roll_label"]
    for b in slip["box_rows"]:
        assert DB.bare_carton_no(b["carton_no"]) in txt, b["carton_no"]
    assert "Reel totals" in txt and "Box totals" in txt
    assert "Legacy box totals" in txt and "Total boxes" in txt
    assert txt.count("Page Nos.:") == 2
    for banned in ("qualified identity", "NOT added",
                   "independent boxes + legacy linked"):
        assert banned not in txt, banned
    _, imgs = SP.preview_images(job["id"], p)
    assert len(imgs) == 2 and all(not im.isNull() for im in imgs)


def test_wrappers_snapshot_unknown_and_known():
    from factory import slip_pdf as SP
    p = _fresh_db()
    j1 = S.create_job("UNKNOWN WRAP JOB", core_wt=0.280, path=p)
    assert SP.get_slip(j1["id"], p)["wrappers_per_reel"] is None
    prod = S.create_product("WRAP PROD", 16500, path=p)
    j2 = S.create_job_for_product(prod["id"], path=p)
    slip2 = SP.get_slip(j2["id"], p)
    assert slip2["wrappers_per_reel"] == 16500


def test_dialog_identifies_reel_box_rows():
    from factory.ui.styles import APP_QSS
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("DIALOG JOB", core_wt=0.280, path=p)
    r = _add_reel(p, job["id"], 1, 11.0)
    b = _add_box(p, job["id"], 1, 5.5)
    app = _app()
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    from factory.ui.label_preview import PackingSlipDialog
    d = PackingSlipDialog(None, job["id"])
    slip = SP.get_slip(job["id"], p)
    assert d.table.rowCount() == len(slip["rows"]) == 2
    assert d.table.item(0, 0).text() == "Reel"
    assert slip["rows"][0]["roll_label"] == d.table.item(0, 1).text()
    assert d.table.item(1, 0).text() == "Box"
    assert DB.bare_carton_no(b["carton_no"]) == d.table.item(1, 1).text()
    assert f"J:{job['job_no']}" in d.windowTitle()
    # Teardown hygiene: a live top-level dialog at interpreter shutdown
    # crashes native Windows Qt (access violation after a green summary).
    d.close()
    d.deleteLater()
    app.processEvents()


def test_roll_identity_code_then_period_then_job_fallback():
    """Bare roll_no is never the identity; fallback chain never renumbers."""
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("IDENTITY JOB", core_wt=0.280, path=p)
    r = _add_reel(p, job["id"], 1, 11.0)
    slip = SP.get_slip(job["id"], p)
    assert slip["reel_rows"][0]["roll_label"] == r["roll_code"], slip["reel_rows"][0]
    # Lose the code: period accompanies the number (e.g. 2026-10 / Roll 1).
    c = DB.get_conn(p)
    try:
        c.execute("UPDATE reels SET roll_code=NULL WHERE id=?", (r["id"],))
        c.commit()
    finally:
        c.close()
    slip = SP.get_slip(job["id"], p)
    row = slip["reel_rows"][0]
    assert row["roll_no"] == 1  # stored number untouched
    assert row["roll_label"] == f"{_PERIOD} / Roll 1", row["roll_label"]
    # Lose the period too: job + roll identity, still no bare number.
    c = DB.get_conn(p)
    try:
        c.execute("UPDATE reels SET roll_period=NULL WHERE id=?", (r["id"],))
        c.commit()
    finally:
        c.close()
    slip = SP.get_slip(job["id"], p)
    row = slip["reel_rows"][0]
    assert row["roll_no"] == 1
    assert row["roll_label"] == f"J{job['job_no']} / Roll 1", row["roll_label"]
    c = DB.get_conn(p)
    try:
        kept = c.execute("SELECT roll_no FROM reels WHERE id=?", (r["id"],)).fetchone()["roll_no"]
    finally:
        c.close()
    assert kept == 1  # never renumbered


def test_all_cartons_appear_with_separate_totals():
    """Full evidence: every carton is a box row; totals never mix."""
    from factory import slip_pdf as SP
    p = _fresh_db()
    job = S.create_job("FULL EVIDENCE JOB", core_wt=0.280, path=p)
    r1 = _add_reel(p, job["id"], 1, 11.0)
    r2 = _add_reel(p, job["id"], 2, 12.0)
    r3 = _add_reel(p, job["id"], 3, 13.0)
    b1 = _add_box(p, job["id"], 1, 5.5)
    lc = _add_linked_carton(p, job["id"], 2, [r1["id"], r2["id"]])
    slip = SP.get_slip(job["id"], p)
    assert {b["carton_no"] for b in slip["box_rows"]} == {b1["carton_no"], lc["carton_no"]}
    indep = [b for b in slip["box_rows"] if not b["linked"]]
    leg = [b for b in slip["box_rows"] if b["linked"]]
    assert len(indep) == 1 and len(leg) == 1
    assert abs(indep[0]["gross"] - 5.5) < 0.001
    assert abs(leg[0]["gross"] - 23.0) < 0.001  # factual stored weight
    t = slip["totals"]
    assert t["reels"] == 3 and abs(t["gross"] - 36.0) < 0.001
    assert t["boxes"] == 1 and abs(t["box_wt"] - 5.5) < 0.001
    assert t["legacy_cartons"] == 1 and abs(t["legacy_box_wt"] - 23.0) < 0.001
    assert t["bundles"] == 2
    assert len(slip["rows"]) == 5  # 3 reels + 2 carton rows, none dropped
    # Reel identities stay qualified even beside legacy links.
    assert all(rr["roll_label"] for rr in slip["reel_rows"])


def test_legacy_long_ids_last_page_totals_in_pdf():
    """Legacy membership with long roll identities: the last page keeps the
    full totals block above the footer, every reel/box record is in the PDF
    text in full, and the overflowing member-roll list is clipped with an
    ellipsis inside its cell (never past the border)."""
    from factory import slip_pdf as SP
    _app()
    p = _fresh_db()
    job = S.create_job(
        "LEGACY LONG IDS JOB WITH A VERY LONG JOB NAME THAT MUST NOT"
        " OVERFLOW THE PRINTABLE WIDTH OF THE SLIP",
        core_wt=0.280, path=p)
    for i in range(1, 40):
        _add_reel(p, job["id"], i, 11.0, customer="ACME")
    c = DB.get_conn(p)
    try:
        reel_ids = [r["id"] for r in c.execute(
            "SELECT id FROM reels WHERE job_id=? ORDER BY roll_no",
            (job["id"],)).fetchall()]
    finally:
        c.close()
    # One legacy carton linking the first 5 reels + two independent boxes.
    _add_linked_carton(p, job["id"], 1, reel_ids[:5])
    _add_box(p, job["id"], 2, 5.5, customer="ACME")
    _add_box(p, job["id"], 3, 6.25, customer="ACME")
    slip = SP.get_slip(job["id"], p)
    assert len(slip["reel_rows"]) == 39
    assert len(slip["box_rows"]) == 3
    assert slip["totals"]["legacy_cartons"] == 1
    assert slip["totals"]["boxes"] == 2
    pages = SP.paginate(slip)
    assert sum(len(x) for x in pages) == 42
    _assert_pages_fit(slip, pages)
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-slipev-"), "legacy-long.pdf")
    SP.save_pdf(job["id"], out, p)
    assert open(out, "rb").read(5) == b"%PDF-"
    txt = _pdf_text(out)
    # Every reel identity in full (real-format codes fit their cells).
    for r in slip["reel_rows"]:
        assert r["roll_label"] in txt, r["roll_label"]
    # Every carton record in full.
    for b in slip["box_rows"]:
        assert DB.bare_carton_no(b["carton_no"]) in txt, b["carton_no"]
    # Full separate totals on the last page.
    assert "Reel totals" in txt and "Box totals" in txt
    assert "Legacy box totals" in txt and "Total boxes" in txt
    assert txt.count("Page Nos.:") == len(pages)
    # Overflowing text is clipped with an ellipsis inside its cell (long
    # job name, 5-member roll list) — never past the border.
    assert "…" in txt or "..." in txt, "expected intentional ellipsis clipping"
    for banned in ("qualified identity", "NOT added",
                   "independent boxes + legacy linked"):
        assert banned not in txt, banned
    _, imgs = SP.preview_images(job["id"], p)
    assert len(imgs) == len(pages) and all(not im.isNull() for im in imgs)


def test_prefetched_slip_dict_renders_without_local_db():
    """Remote path: save_pdf/preview_images accept a pre-fetched slip dict
    (e.g. data_access.slip_data(job_id) over LAN) and never open SQLite."""
    from factory import slip_pdf as SP
    _app()
    p = _fresh_db()
    job = S.create_job("REMOTE SLIP JOB", core_wt=0.280, path=p)
    r1 = _add_reel(p, job["id"], 1, 11.0)
    b1 = _add_box(p, job["id"], 1, 5.5)
    slip = SP.get_slip(job["id"], p)
    assert len(slip["rows"]) == 2
    pages = SP.paginate(slip)
    _assert_pages_fit(slip, pages)
    # Bogus DB path proves no local read when the dict is supplied.
    bogus = "/nonexistent-remote-slip/factory.db"
    out = os.path.join(tempfile.mkdtemp(prefix="simpld-slipev-"), "remote.pdf")
    SP.save_pdf(job["id"], out, bogus, slip=slip)
    assert open(out, "rb").read(5) == b"%PDF-"
    txt = _pdf_text(out)
    assert slip["reel_rows"][0]["roll_label"] in txt
    assert DB.bare_carton_no(b1["carton_no"]) in txt
    assert "Reel totals" in txt and "Box totals" in txt
    assert "Legacy box totals" in txt and "Total boxes" in txt
    assert txt.count("Page Nos.:") == len(pages)
    slip2, imgs = SP.preview_images(job["id"], bogus, slip=slip)
    assert len(imgs) == len(pages) and all(not im.isNull() for im in imgs)
    assert slip2["totals"] == slip["totals"]
    # Same shape data_access.slip_data() returns locally (LAN returns the
    # server's get_slip dict over the wire).
    from factory import data_access as DA
    DA.configure_local()
    assert DA.slip_data(job["id"])["totals"] == slip["totals"]


if __name__ == "__main__":
    test_reels_no_boxes_produces_slip()
    test_no_reels_no_boxes_empty_slip()
    test_mixed_independent_no_double_count()
    test_legacy_linked_counts_each_reel_once()
    test_multipage_pdf_and_preview()
    test_wrappers_snapshot_unknown_and_known()
    test_dialog_identifies_reel_box_rows()
    test_roll_identity_code_then_period_then_job_fallback()
    test_all_cartons_appear_with_separate_totals()
    test_legacy_long_ids_last_page_totals_in_pdf()
    test_prefetched_slip_dict_renders_without_local_db()
    print("test_slip_evidence PASS")
