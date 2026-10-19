"""Explicit reel/box packing, with no weight-based mode detection.

Box packing consumes loose reels in production order and queues stickers
for later printing. Tests use temporary databases only.
"""
import math
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-pkg-"), "factory.db")
    DB.DB_PATH = tmp  # UI service calls without path read this global
    DB.init_db(tmp)
    os.environ["SIMPLD_ALLOW_MOCK"] = "1"  # test-only: exercise mock HW gates
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


def _page():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import Qt
    from factory.ui.page_packaging import PackagingPage
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    pg = PackagingPage()
    pg.setAttribute(Qt.WA_ShowWithoutActivating, False)
    return app, pg


def _select_job(pg, job_id):
    pg.refresh_jobs()
    for i in range(pg.job_combo.count()):
        if pg.job_combo.itemData(i) == job_id:
            pg.job_combo.setCurrentIndex(i)
            return
    raise AssertionError("job not in combo")


def test_loose_list_is_display_only():
    _fresh_db()
    from PySide6.QtWidgets import QAbstractItemView
    _, pg = _page()
    assert pg.table.columnCount() == 4, pg.table.columnCount()  # Roll/Net/Status/Label(print state)
    assert pg.table.editTriggers() == QAbstractItemView.NoEditTriggers
    assert pg.table.selectionBehavior() == QAbstractItemView.SelectRows
    assert not hasattr(pg, "btn_select_all") and not hasattr(pg, "btn_clear")


def test_no_weight_detection_in_ui():
    """Active packing UI must not guess object type from weight."""
    _fresh_db()
    _, pg = _page()
    for attr in ("btn_detect", "auto_detect", "_detect_and_tick", "_match_box",
                 "_tick_reels", "selected_reel_ids", "selected_gross",
                 "_detected_ids", "_suggest_ids"):
        assert not hasattr(pg, attr), f"UI must not keep {attr}"
    assert not hasattr(pg, "mode_tab")
    assert pg.pack_mode == "reel"
    pg.set_mode("carton")
    assert pg.pack_mode == "carton"
    pg.set_mode("reel")
    assert pg.pack_mode == "reel"


def test_weight_only_carton_service():
    p = _fresh_db()
    job = S.create_job("BOX JOB", core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    ct = S.pack_weight_carton(job["id"], 10.5, path=p)
    assert ct["num_reels"] == 0, ct
    assert ct["total_gross"] == ct["total_net"] == 10.5, ct
    assert ct["total_core"] == 0, ct
    assert ct["carton_no"].startswith("C") and ct["bundle_no"] == 1
    # no carton_items links, no loose reels falsely marked packed
    assert S.loose_reels(job["id"], path=p) and len(S.loose_reels(job["id"], path=p)) == 2
    d = S.carton_detail(ct["id"], path=p)
    assert d["reels"] == [] and d["qr"] == f"VASAVI:{ct['carton_no']}"
    # old reel-linked path untouched
    ct2 = S.pack_carton(job["id"], [r1["id"], r2["id"]], path=p)
    assert ct2["num_reels"] == 2 and len(S.carton_detail(ct2["id"], path=p)["reels"]) == 2
    for bad in (0, -1.5, float("inf"), float("nan")):
        try:
            S.pack_weight_carton(job["id"], bad, path=p)
            raise SystemExit(f"FAIL: weight {bad} not rejected")
        except ValueError:
            pass


def test_box_mode_consumes_loose_reels_and_queues_sticker():
    from unittest import mock
    p = _fresh_db()
    job = S.create_job("UI BOX JOB", core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.set_mode("carton")
    assert pg.pack_mode == "carton"
    pg.tare.setValue(0.300)
    pg.reels_per_box.setValue(2)
    pg.gross.setValue(22.0)
    assert pg.btn_action.isEnabled(), "valid box weight must arm PACK"
    assert "Pack" in pg.btn_action.text(), pg.btn_action.text()
    with mock.patch("factory.ui.page_packaging.CartonLabelDialog") as dlg:
        pg.do_action()
        dlg.assert_not_called()
    stock = S.ready_stock(job["id"], path=p)
    assert len(stock) == 1 and stock[0]["num_reels"] == 2, stock
    assert sorted(stock[0]["rolls"]) == [1, 2], stock
    assert math.isclose(stock[0]["box_wt"], 0.300)
    assert math.isclose(stock[0]["total_gross"], 22.0)
    assert math.isclose(stock[0]["total_core"], 0.560)
    assert math.isclose(stock[0]["total_net"], 22.0 - 0.300 - 0.560)
    assert S.loose_reels(job["id"], path=p) == []
    assert "queued" in pg.msg.casefold(), pg.msg
    assert len(S.pending_box_stickers(job["id"], path=p)) == 1
    assert pg.box_table.rowCount() == 1
    assert "1 waiting" in pg.box_queue.text()


def test_box_packing_consumes_reels_in_production_order():
    """Oldest loose reels go in first; a partial final box is allowed; the
    sticker queue keeps packing order (oldest box first)."""
    p = _fresh_db()
    job = S.create_job("FIFO JOB", core_wt=0.280, path=p)
    for g in (11.380, 11.120, 10.900):
        S.create_reel(job["id"], gross=g, core=0.280, path=p)
    b1 = S.pack_next_box(job["id"], 22.0, 0.300, 2, path=p)
    b2 = S.pack_next_box(job["id"], 11.5, 0.300, 2, path=p)  # partial: 1 reel left
    boxes = S.job_boxes(job["id"], path=p)
    assert [b["carton_no"] for b in boxes] == [b1["carton_no"], b2["carton_no"]], boxes
    assert boxes[0]["rolls"] == [1, 2], boxes[0]["rolls"]  # production order, not sorted
    assert boxes[0]["num_reels"] == 2
    assert boxes[1]["rolls"] == [3] and boxes[1]["num_reels"] == 1, boxes[1]
    # Net identity holds for every box (the stored tare is the rounded one).
    for b in boxes:
        assert math.isclose(
            b["total_net"],
            round(b["total_gross"] - b["box_wt"] - b["total_core"], 3)), b
    queue = S.pending_box_stickers(job["id"], path=p)
    assert [x["carton_no"] for x in queue] == [b1["carton_no"], b2["carton_no"]], queue
    assert S.loose_reels(job["id"], path=p) == []


def test_box_queue_buttons_print_oldest_and_delete_returns_reels():
    from unittest import mock
    from PySide6.QtWidgets import QMessageBox
    from factory import auth as A
    p = _fresh_db()
    job = S.create_job("QUEUE JOB", core_wt=0.280, path=p)
    for g in (11.380, 11.120, 10.900):
        S.create_reel(job["id"], gross=g, core=0.280, path=p)
    b1 = S.pack_next_box(job["id"], 22.0, 0.300, 2, path=p)
    b2 = S.pack_next_box(job["id"], 11.5, 0.300, 2, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.set_mode("carton")
    assert pg.box_table.rowCount() == 2
    assert "2 waiting" in pg.box_queue.text(), pg.box_queue.text()
    # "Print next" opens the OLDEST waiting sticker (packing order).
    with mock.patch("factory.ui.page_packaging.CartonLabelDialog") as dlg:
        pg.box_print_next()
        assert dlg.call_count == 1, pg.msg
        assert dlg.call_args.args[1]["carton_no"] == b1["carton_no"], dlg.call_args
    # Read-only role: no printing, no deleting.
    A.authenticate("dispatcher", "dispatch123", p)
    try:
        pg.box_print_next()
        assert "read-only" in pg.msg.casefold(), pg.msg
        pg.box_table.selectRow(0)
        pg.box_delete()
        assert "Only admin" in pg.msg, pg.msg
        assert len(S.job_boxes(job["id"], path=p)) == 2
    finally:
        A.authenticate("admin", "admin123", p)
    # Admin delete: the box goes away and its reels return to loose.
    pg.refresh_history()
    pg.box_table.selectRow(0)
    with mock.patch.object(QMessageBox, "question",
                           return_value=QMessageBox.StandardButton.Yes):
        pg.box_delete()
    assert "back to loose" in pg.msg.casefold(), pg.msg
    boxes = S.job_boxes(job["id"], path=p)
    assert [b["carton_no"] for b in boxes] == [b2["carton_no"]], boxes
    assert [x["roll_no"] for x in S.loose_reels(job["id"], path=p)] == [1, 2]


def test_reel_mode_saves_single_reel_no_carton():
    from unittest import mock
    p = _fresh_db()
    job = S.create_job("UI REEL JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.set_mode("reel")
    assert pg.pack_mode == "reel"
    pg.gross.setValue(11.380)
    assert pg.btn_action.isEnabled()
    assert "reel" in pg.btn_action.text().casefold(), pg.btn_action.text()
    with mock.patch("factory.ui.page_packaging.ReelLabelDialog") as dlg:
        pg.do_action()
        assert dlg.call_count == 1
    assert len(S.loose_reels(job["id"], path=p)) == 1
    assert S.ready_stock(job["id"], path=p) == [], "reel save must not create cartons"


def test_zero_weight_arms_nothing_and_packs_nothing():
    p = _fresh_db()
    job = S.create_job("ZERO JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    for mode in ("reel", "carton"):
        pg.set_mode(mode)
        pg.gross.setValue(0)
        assert not pg.btn_action.isEnabled(), f"{mode}: zero must not arm action"
        if mode == "carton":
            assert "loose" in pg.btn_action.text().casefold()
        pg.do_action()
        assert "scale" in pg.msg.casefold(), pg.msg
    assert S.ready_stock(job["id"], path=p) == []
    assert S.loose_reels(job["id"], path=p) == []


def test_row_action_preview_behavior():
    """Action gating follows mode+weight; preview renders without DB writes."""
    p = _fresh_db()
    job = S.create_job("PREVIEW STATE JOB", core_wt=0.280, path=p)
    app, pg = _page()
    _select_job(pg, job["id"])
    assert pg.sticker_preview.minimumHeight() <= 80
    # reel mode, no weight -> hint, action off
    pg.set_mode("reel")
    pg.gross.setValue(0)
    app.processEvents()
    assert not pg.btn_action.isEnabled()
    assert "Weigh" in pg.sticker_preview.text(), pg.sticker_preview.text()
    # reel mode, weight -> reel art only, action on
    pg.gross.setValue(11.380)
    app.processEvents()
    assert pg.btn_action.isEnabled() and "reel" in pg.btn_action.text().casefold()
    assert "reel" in pg.sticker_cap.text().casefold(), pg.sticker_cap.text()
    assert pg.sticker_preview.pixmap() is not None and not pg.sticker_preview.pixmap().isNull()
    # Box mode needs loose reels before it can arm or show art.
    pg.set_mode("carton")
    app.processEvents()
    assert not pg.btn_action.isEnabled()
    assert "loose" in pg.btn_action.text().casefold()
    assert "No loose reels" in pg.sticker_preview.text()
    px = pg.sticker_preview.pixmap()
    assert px is None or px.isNull()
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    pg.refresh_history()
    before_reels = S.loose_reels(job["id"], path=p)
    before_stock = S.ready_stock(path=p)
    pg.set_mode("carton")
    pg.gross.setValue(22.0)
    app.processEvents()
    assert pg.btn_action.isEnabled() and "Pack" in pg.btn_action.text()
    assert "QR" in pg.sticker_cap.text(), pg.sticker_cap.text()
    assert pg.sticker_preview.pixmap() is not None and not pg.sticker_preview.pixmap().isNull()
    assert S.loose_reels(job["id"], path=p) == before_reels
    assert S.ready_stock(path=p) == before_stock


if __name__ == "__main__":
    test_loose_list_is_display_only()
    test_no_weight_detection_in_ui()
    test_weight_only_carton_service()
    test_box_mode_consumes_loose_reels_and_queues_sticker()
    test_box_packing_consumes_reels_in_production_order()
    test_box_queue_buttons_print_oldest_and_delete_returns_reels()
    test_reel_mode_saves_single_reel_no_carton()
    test_zero_weight_arms_nothing_and_packs_nothing()
    test_row_action_preview_behavior()
    print("test_packaging_detect PASS")
