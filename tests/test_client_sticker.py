"""Wide client sticker: wrapper pieces, client box numbers, PO/batch, logo."""
import base64
import io
import os
import sqlite3

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import auth as A
from factory import db as DB
from factory import services as S
from factory.db import init_db
from factory.hardware import labels as L
from factory.ui.page_dispatch import _piece_alert
from tests._auth_helper import ensure_default_users


def _db(tmp_path, name="factory.db"):
    p = str(tmp_path / name)
    DB.DB_PATH = p
    init_db(p)
    ensure_default_users(p)
    assert A.authenticate("admin", "admin123", p) is not None
    return p


def _product_job(p, wrappers=1000, name="CLIENT STICKER PRODUCT"):
    prod = S.create_product(name, wrappers, path=p)
    return S.create_job_for_product(prod["id"], core_wt=0.280, path=p)


def _box(p, job, reels):
    out = []
    for gross in reels:
        out.append(S.create_reel(job["id"], gross=gross, core=0.280, path=p))
    return S.pack_carton(job["id"], [r["id"] for r in out], path=p)


def test_wrapper_pieces_and_client_box_numbering(tmp_path):
    p = _db(tmp_path)
    job = _product_job(p, wrappers=1000)
    b1 = _box(p, job, [11.38, 11.12])          # 2 coils -> 2,000 pcs
    b2 = _box(p, job, [11.20, 11.10, 11.00])   # 3 coils -> 3,000 pcs
    order = S.create_dispatch_order(
        "PARLE AGRO", job["id"], required_pieces=4000,
        customer_po="4500048181", path=p)

    S.scan_into_order(order["id"], b1["carton_no"], path=p)
    S.scan_into_order(order["id"], b2["carton_no"], path=p)
    prog = S.order_progress(order["id"], path=p)
    assert prog["piece_unit"] == "wrapper"
    assert prog["loaded"] == 2
    assert prog["loaded_pieces"] == 5000 >= prog["required_pieces"] == 4000
    assert [it["client_box_no"] for it in prog["items"]] == [1, 2]

    # Removing a box renumbers the client-facing sequence 1..N.
    S.remove_from_order(order["id"], b1["id"], path=p)
    prog = S.order_progress(order["id"], path=p)
    assert prog["loaded_pieces"] == 3000
    assert [it["client_box_no"] for it in prog["items"]] == [1]
    assert prog["items"][0]["carton_no"] == b2["carton_no"]

    # Rescanning appends in scan order: b2 is client box 1, b1 becomes 2.
    S.scan_into_order(order["id"], b1["carton_no"], path=p)
    prog = S.order_progress(order["id"], path=p)
    assert [it["client_box_no"] for it in prog["items"]] == [1, 2]
    assert prog["items"][0]["carton_no"] == b2["carton_no"]
    assert prog["items"][1]["carton_no"] == b1["carton_no"]

    done = S.complete_dispatch(order["id"], path=p)
    assert done["status"] == "complete"
    assert done["loaded_pieces"] == 5000


def test_wrapper_order_requires_wrappers_per_reel(tmp_path):
    p = _db(tmp_path)
    job = S.create_job("LEGACY NO WRAPPERS", core_wt=0.280, path=p)
    with pytest.raises(ValueError, match="wrappers-per-reel"):
        S.create_dispatch_order("NO COUNT CO", job["id"],
                                required_pieces=100, path=p)


def test_legacy_reel_piece_order_keeps_old_semantics(tmp_path):
    p = _db(tmp_path)
    job = S.create_job("LEGACY PIECE JOB", core_wt=0.280, path=p)
    box = _box(p, job, [11.38, 11.12])
    order = S.create_dispatch_order("LEGACY CO", job["id"],
                                    required_pieces=2, piece_unit="reel",
                                    path=p)
    S.scan_into_order(order["id"], box["carton_no"], path=p)
    prog = S.order_progress(order["id"], path=p)
    assert prog["piece_unit"] == "reel"
    assert prog["loaded_pieces"] == 2
    assert S.complete_dispatch(order["id"], path=p)["status"] == "complete"


def test_client_label_detail_po_batch_and_rows(tmp_path):
    p = _db(tmp_path)
    job = _product_job(p, wrappers=38740, name="APPY FIZZ 160ML NECK 10 RS")
    box = _box(p, job, [8.68, 8.64, 8.78])
    # Pin the coil mfg date so the derived batch is deterministic.
    c = sqlite3.connect(p)
    c.execute("UPDATE reels SET created_at='2026-10-16 08:00:00' WHERE job_id=?",
              (job["id"],))
    c.commit()
    c.close()
    order = S.create_dispatch_order(
        "PARLE AGRO PVT LTD", job["id"], required_pieces=116220,
        customer_po="4500048181", path=p)
    S.scan_into_order(order["id"], box["carton_no"], path=p)
    detail = S.client_label_detail(order["id"], box["id"], path=p)
    assert detail["customer_po"] == "4500048181"
    assert detail["batch_no"] == "SVSF/OCT-16"
    assert detail["client_box_no"] == 1
    assert detail["num_coils"] == 3
    assert detail["total_pieces"] == 116220
    assert detail["coils"][0]["pieces"] == 38740
    assert round(detail["total_net"], 2) == round(8.40 + 8.36 + 8.50, 2)

    # Explicit batch override wins over the derived code.
    box2 = _box(p, job, [8.60])
    order2 = S.create_dispatch_order(
        "PARLE AGRO PVT LTD", job["id"], required_pieces=38740,
        customer_po="PO-2", batch_no="custom-b1", path=p)
    S.scan_into_order(order2["id"], box2["carton_no"], path=p)
    d2 = S.client_label_detail(order2["id"], box2["id"], path=p)
    assert d2["batch_no"] == "CUSTOM-B1"
    assert d2["client_box_no"] == 1


def test_company_logo_roundtrip_and_permissions(tmp_path):
    p = _db(tmp_path)
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGBA", (80, 30), (20, 40, 120, 255)).save(buf, "PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    S.set_company_logo(b64, path=p)
    stored = S.get_company_logo(path=p)
    assert stored.startswith("iVBOR")  # PNG magic in base64
    Image.open(io.BytesIO(base64.b64decode(stored))).verify()

    job = _product_job(p, wrappers=1000)
    box = _box(p, job, [11.38])
    order = S.create_dispatch_order("LOGO CO", job["id"],
                                    required_pieces=1000,
                                    customer_po="PO-1", path=p)
    S.scan_into_order(order["id"], box["carton_no"], path=p)
    detail = S.client_label_detail(order["id"], box["id"], path=p)
    assert detail["company_logo"] == stored

    # Packagers can read the logo (labels) but not replace it.
    assert A.authenticate("packager", "pack123", p) is not None
    with pytest.raises(PermissionError):
        S.set_company_logo(b64, path=p)
    assert A.authenticate("admin", "admin123", p) is not None
    S.set_company_logo("", path=p)
    assert S.get_company_logo(path=p) == ""


def test_wide_renderer_size_rows_and_min_height(tmp_path):
    p = _db(tmp_path)
    job = _product_job(p, wrappers=38740, name="APPY FIZZ 160ML NECK 10 RS")
    box = _box(p, job, [8.68, 8.64, 8.78])
    order = S.create_dispatch_order("RENDER CO", job["id"],
                                    required_pieces=116220,
                                    customer_po="4500048181", path=p)
    S.scan_into_order(order["id"], box["carton_no"], path=p)
    detail = S.client_label_detail(order["id"], box["id"], path=p)
    img = L.render_client_box(detail)
    assert L.wide_label_height_mm(3) == 91.0
    assert img.size == (1678, 727)
    assert img.convert("L").getextrema()[0] < 128  # has ink, not a blank page

    # The Product row must be closed by a full-width rule before the table
    # header; otherwise PRODUCT and S.no visually merge into one row.
    rule_y = round(45 / 25.4 * L.WIDE_DPI)
    rule = [img.getpixel((x, rule_y))
            for x in range(60, img.width - 60, 7)]
    assert all(sum(p[:3]) < 200 for p in rule)

    one = dict(detail, coils=detail["coils"][:1], num_coils=1,
               client_box_no=2)
    img1 = L.render_client_box(one)
    assert L.wide_label_height_mm(1) == L.WIDE_MIN_H_MM
    assert img1.size == (1678, 623)
    assert img1.height < img.height

    empty = dict(detail, coils=[], num_coils=0)
    assert L.render_client_box(empty).size == (1678, 623)


def test_progressive_piece_alert_thresholds():
    assert _piece_alert(700, 1000) is None
    assert _piece_alert(750, 1000) == (
        "250 pcs left (75% loaded).", "info")
    assert _piece_alert(900, 1000) == (
        "Almost done — 100 pcs left (900/1,000).", "warning")
    assert _piece_alert(1000, 1000) == (
        "Target reached — 1,000/1,000 pcs.", "success")
    assert _piece_alert(1180, 1000) == (
        "Target reached — over by 180 pcs.", "warning")


def test_ready_stock_summary_single_aggregate(tmp_path):
    """Picker stock brackets: one GROUP BY, boxes/coils/pieces per job."""
    p = _db(tmp_path)
    job = _product_job(p, wrappers=1000)
    _box(p, job, [11.38, 11.12])          # 2 coils
    _box(p, job, [11.20, 11.10, 11.00])   # 3 coils
    legacy = S.create_job("LEGACY NO WPR", core_wt=0.280, path=p)
    _box(p, legacy, [11.38])
    summ = S.ready_stock_summary(path=p)
    assert summ[job["id"]] == {"boxes": 2, "coils": 5, "pieces": 5000}
    assert summ[legacy["id"]] == {"boxes": 1, "coils": 1, "pieces": None}
    # A loaded (dispatched) box leaves Ready, so it leaves the summary.
    order = S.create_dispatch_order("SUMMARY CO", job["id"],
                                    required_pieces=2000,
                                    customer_po="PO-S", path=p)
    first = S.ready_stock(job["id"], path=p)[0]
    S.scan_into_order(order["id"], first["carton_no"], path=p)
    S.complete_dispatch(order["id"], path=p)
    summ = S.ready_stock_summary(path=p)
    assert summ[job["id"]]["boxes"] == 1

    from factory.ui.page_dispatch import _compact_pcs
    assert _compact_pcs(116220) == "1.2L"
    assert _compact_pcs(9500) == "9.5k"
    assert _compact_pcs(999) == "999"


def test_migration_adds_columns_and_backfills_box_numbers(tmp_path):
    p = _db(tmp_path)
    job = _product_job(p, wrappers=1000)
    b1 = _box(p, job, [11.38, 11.12])
    b2 = _box(p, job, [11.20, 11.10])
    order = S.create_dispatch_order("MIGRATE CO", job["id"],
                                    required_pieces=4000,
                                    customer_po="PO-9", path=p)
    S.scan_into_order(order["id"], b1["carton_no"], path=p)
    S.scan_into_order(order["id"], b2["carton_no"], path=p)

    # Simulate the pre-wide-label schema.
    c = sqlite3.connect(p)
    c.execute("ALTER TABLE dispatch_items DROP COLUMN client_box_no")
    c.execute("ALTER TABLE dispatch_orders DROP COLUMN piece_unit")
    c.execute("ALTER TABLE dispatch_orders DROP COLUMN customer_po")
    c.execute("ALTER TABLE dispatch_orders DROP COLUMN batch_no")
    c.commit()
    c.close()

    init_db(p)
    c = sqlite3.connect(p)
    ocols = {r[1] for r in c.execute("PRAGMA table_info(dispatch_orders)")}
    icols = {r[1] for r in c.execute("PRAGMA table_info(dispatch_items)")}
    assert {"customer_po", "batch_no", "piece_unit"} <= ocols
    assert "client_box_no" in icols
    boxes = [r[0] for r in c.execute(
        "SELECT client_box_no FROM dispatch_items WHERE order_id=? ORDER BY id",
        (order["id"],))]
    assert boxes == [1, 2]
    row = c.execute("SELECT piece_unit, customer_po, batch_no FROM dispatch_orders"
                    " WHERE id=?", (order["id"],)).fetchone()
    c.close()
    assert row[0] == "reel"  # existing targets are never reinterpreted
    # A pre-wide-label order had no PO/batch columns; the additive migration
    # leaves them blank rather than inventing values.
    assert row[1] == "" and row[2] == ""
