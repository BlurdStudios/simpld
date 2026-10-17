"""Weight-only carton packing: explicit Reel/Carton mode, no weight detection.

Cartons are BOX WEIGHT ONLY: the scale cannot infer object type, so the
operator picks the mode and the measured box weight is stored as-is — no
reel guessing, no count input, no fabricated roll IDs. Temp DB only.
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
    assert pg.mode_tab.count() == 2
    assert "Reel" in pg.mode_tab.tabText(0) and "Box" in pg.mode_tab.tabText(1)
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


def test_carton_mode_packs_box_without_touching_reels():
    from unittest import mock
    p = _fresh_db()
    job = S.create_job("UI BOX JOB", core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.mode_tab.setCurrentIndex(1)
    assert pg.pack_mode == "carton"
    pg.gross.setValue(10.5)
    assert pg.btn_action.isEnabled(), "valid box weight must arm PACK"
    assert "Pack" in pg.btn_action.text(), pg.btn_action.text()
    with mock.patch("factory.ui.page_packaging.CartonLabelDialog") as dlg:
        pg.do_action()
        assert dlg.call_count == 1, "carton label should print on success"
    stock = S.ready_stock(job["id"], path=p)
    assert len(stock) == 1 and stock[0]["num_reels"] == 0, stock
    assert stock[0]["rolls"] == [], stock
    assert abs(stock[0]["total_gross"] - 10.5) < 0.001
    assert len(S.loose_reels(job["id"], path=p)) == 2, "reels must stay loose"
    assert "packed" in pg.msg.casefold(), pg.msg


def test_reel_mode_saves_single_reel_no_carton():
    from unittest import mock
    p = _fresh_db()
    job = S.create_job("UI REEL JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
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
    for tab in (0, 1):
        pg.mode_tab.setCurrentIndex(tab)
        pg.gross.setValue(0)
        assert not pg.btn_action.isEnabled(), f"tab {tab}: zero must not arm action"
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
    assert pg._preview_card.maximumHeight() <= 300
    # reel mode, no weight -> hint, action off
    pg.mode_tab.setCurrentIndex(0)
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
    # carton mode, same weight -> carton art, pack armed, still no writes
    before_reels = S.loose_reels(job["id"], path=p)
    before_stock = S.ready_stock(path=p)
    pg.mode_tab.setCurrentIndex(1)
    app.processEvents()
    assert pg.btn_action.isEnabled() and "Pack" in pg.btn_action.text()
    assert "carton" in pg.sticker_cap.text().casefold(), pg.sticker_cap.text()
    assert pg.sticker_preview.pixmap() is not None and not pg.sticker_preview.pixmap().isNull()
    assert S.loose_reels(job["id"], path=p) == before_reels
    assert S.ready_stock(path=p) == before_stock


if __name__ == "__main__":
    test_loose_list_is_display_only()
    test_no_weight_detection_in_ui()
    test_weight_only_carton_service()
    test_carton_mode_packs_box_without_touching_reels()
    test_reel_mode_saves_single_reel_no_carton()
    test_zero_weight_arms_nothing_and_packs_nothing()
    test_row_action_preview_behavior()
    print("test_packaging_detect PASS")
