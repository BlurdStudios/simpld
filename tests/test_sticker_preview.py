"""Packaging sticker previews: fixed 4x3in stock, table art, read-only.

Covers the live Type-1 (reel, NO QR) + Type-2 (box, WITH QR) previews on the
packaging page. No customer/shift/supervisor on stickers (customer lives on
the dispatch order). Runs offscreen against a temp DB — never production.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-pv-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


def _page():
    from PySide6.QtWidgets import QApplication
    from factory.ui.page_packaging import PackagingPage
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    return app, PackagingPage()


def _has_px(label) -> bool:
    p = label.pixmap()
    return p is not None and not p.isNull()


def _select_job(pg, job_id):
    pg.refresh_jobs()
    for i in range(pg.job_combo.count()):
        if pg.job_combo.itemData(i) == job_id:
            pg.job_combo.setCurrentIndex(i)
            return
    raise AssertionError("job not in combo")


def test_stock_fixed_4x3in_no_height_option():
    from factory.ui.page_packaging import PRINTER_WIDTH_MM, PRINTER_HEIGHT_MM
    assert abs(PRINTER_WIDTH_MM - 101.6) < 0.01
    assert abs(PRINTER_HEIGHT_MM - 76.2) < 0.5
    _, pg = _page()
    assert not hasattr(pg, "label_height"), "no height option on the page"


def test_idle_shows_hint_not_art():
    p = _fresh_db()
    job = S.create_job("PREVIEW JOB", size_mm=160, core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    assert not _has_px(pg.sticker_preview), "idle scale must not show art"
    assert "Weigh" in pg.sticker_preview.text(), pg.sticker_preview.text()


def test_reel_weigh_shows_only_reel_art():
    p = _fresh_db()
    job = S.create_job("PREVIEW REEL JOB", core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    app, pg = _page()
    _select_job(pg, job["id"])
    pg.mode_tab.setCurrentIndex(0)  # explicit reel mode
    pg.gross.setValue(11.380)
    app.processEvents()
    assert pg.pack_mode == "reel"
    assert _has_px(pg.sticker_preview), "reel art must render"
    assert "reel" in pg.sticker_cap.text().casefold(), pg.sticker_cap.text()


def test_box_preview_in_carton_mode_needs_no_reels():
    """Weight-only carton art from the scale weight alone; nothing guessed."""
    from factory.hardware import labels as L
    p = _fresh_db()
    job = S.create_job("PREVIEW BOX JOB", core_wt=0.280, path=p)
    app, pg = _page()
    _select_job(pg, job["id"])
    assert not hasattr(pg, "cust") and not hasattr(pg, "sup"), \
        "no customer/shift/supervisor inputs on the page"
    pg.mode_tab.setCurrentIndex(1)  # explicit carton mode
    pg.gross.setValue(10.5)
    app.processEvents()
    assert pg.pack_mode == "carton"
    assert pg.btn_action.isEnabled(), "valid box weight must arm PACK"
    assert _has_px(pg.sticker_preview), "carton art must render"
    assert "carton" in pg.sticker_cap.text().casefold(), pg.sticker_cap.text()
    # bordered grid prints dark (top border pixel row); box-only, no rolls
    detail = S.carton_detail(S.pack_weight_carton(job["id"], 10.5, path=p)["id"], path=p)
    assert detail["reels"] == [] and detail["num_reels"] == 0
    from factory.ui.label_preview import carton_label_text as _clt
    assert "Roll" not in _clt(detail), _clt(detail)
    img = L.render_carton(detail)
    assert img.size[0] == 812, img.size  # 4in @203dpi
    dark = sum(1 for x in range(0, img.size[0], 7) if sum(img.getpixel((x, 1))[:3]) < 120)
    assert dark > img.size[0] // 7 * 0.9, "top border row must print dark"


def test_mode_switch_changes_preview_only_without_writes():
    """Same scale weight, Reel<->Carton tabs: preview swaps, PACK follows mode."""
    p = _fresh_db()
    job = S.create_job("SUGGEST JOB 1", core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    app, pg = _page()
    _select_job(pg, job["id"])
    before_reels = S.loose_reels(job["id"], path=p)
    before_stock = S.ready_stock(path=p)
    pg.mode_tab.setCurrentIndex(0)
    pg.gross.setValue(11.380)
    app.processEvents()
    assert "reel" in pg.sticker_cap.text().casefold()
    assert _has_px(pg.sticker_preview)
    pg.mode_tab.setCurrentIndex(1)
    app.processEvents()
    assert pg.pack_mode == "carton"
    assert pg.btn_action.isEnabled() and "Pack" in pg.btn_action.text()
    assert "carton" in pg.sticker_cap.text().casefold()
    assert _has_px(pg.sticker_preview), "box-only carton art must render"
    assert S.loose_reels(job["id"], path=p) == before_reels
    assert S.ready_stock(path=p) == before_stock


def test_reel_table_art_matches_photo_layout():
    from factory.hardware import labels as L
    job = {"job_no": 4167, "job_name": "APPY FIZZ-160ML (SEP.28) BODY QR CODE"}
    reel = {"gross": 11.380, "core": 0.280, "net": 11.100,
            "created_at": "2026-10-03 10:00:00", "roll_no": 23}
    img = L.render_reel(job, reel)
    W, H = img.size
    assert (W, H) == (812, 609), (W, H)  # 4x3in @203dpi
    px = img.load()
    # outer border dark on all four sides; inner grid lines dark
    assert sum(px[W // 2, 1][:3]) < 150, "top border"
    assert sum(px[W // 2, H - 2][:3]) < 150, "bottom border"
    assert sum(px[1, H // 2][:3]) < 150, "left border"
    # merged right box: no horizontal grid through the Roll No# cell middle
    # (blank band between caption band ~0.52-0.58H and ident glyphs ~0.68-0.94H)
    mid_y = int(H * 0.63)
    assert sum(px[int(W * 0.85), mid_y][:3]) > 600, "no line through roll box"


def test_reel_sticker_shows_no_of_pieces():
    from factory.hardware import labels as L
    from factory.ui.label_preview import reel_label_text
    reel = {"gross": 11.380, "core": 0.280, "net": 11.100,
            "created_at": "2026-10-03 10:00:00", "roll_no": 23}
    job = {"job_no": 4167, "job_name": "APPY FIZZ-160ML BODY", "wrappers_per_reel": 16500}
    assert "No of Pieces:16500" in reel_label_text(job, reel)
    img = L.render_reel(job, reel)
    assert img.size == (812, 609), img.size
    job_none = {"job_no": 4167, "job_name": "APPY FIZZ-160ML BODY", "wrappers_per_reel": None}
    img_none = L.render_reel(job_none, reel)
    assert img_none.size == (812, 609), img_none.size
    assert img.tobytes() != img_none.tobytes(), \
        "the count must be drawn into the art, not only stated"
    # unknown job (no key at all — same shape as test_hardware_real._job_reel_detail)
    job_unk = {"job_no": 4167, "job_name": "APPY FIZZ-160ML BODY"}
    assert "No of Pieces:unknown" in reel_label_text(job_unk, reel)
    img_unk = L.render_reel(job_unk, reel)
    assert img_unk.size == (812, 609), img_unk.size


def test_preview_writes_nothing():
    p = _fresh_db()
    job = S.create_job("PREVIEW CLEAN JOB", core_wt=0.280, path=p)
    S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    before_reels = S.loose_reels(job["id"], path=p)
    before_stock = S.ready_stock(path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.mode_tab.setCurrentIndex(0)
    pg.gross.setValue(11.380)
    pg._render_previews()
    pg.mode_tab.setCurrentIndex(1)
    pg._render_previews()
    assert S.loose_reels(job["id"], path=p) == before_reels
    assert S.ready_stock(path=p) == before_stock


def test_customer_columns_migrated_and_inherited():
    p = _fresh_db()
    job = S.create_job("CUST JOB", core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, customer="PARLE AGRO HYD", path=p)
    assert r1["customer"] == "PARLE AGRO HYD"
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    assert r2["customer"] == ""
    ct = S.pack_carton(job["id"], [r1["id"]], path=p)  # uniform single -> inherits
    assert ct["customer"] == "PARLE AGRO HYD", ct
    ct2 = S.pack_carton(job["id"], [r2["id"]], customer="OTHER CO", path=p)
    assert ct2["customer"] == "OTHER CO"


def test_preview_fits_padded_bounds_on_resize():
    """Large->small resize: full pixmap stays inside padded bounds, ratio kept.

    Usable area = label contentsRect (Qt subtracts the actual stylesheet
    padding/border). The displayed pixmap must fit within it, keep
    the 4:3 sticker aspect, fill one dimension (no excessive blank area),
    and the card must stay compact (<=260px tall).
    """
    p = _fresh_db()
    job = S.create_job("PREVIEW RESIZE JOB", core_wt=0.280, path=p)
    app, pg = _page()
    _select_job(pg, job["id"])
    pg.mode_tab.setCurrentIndex(0)
    pg.gross.setValue(11.380)
    pg.show()
    pg.resize(1100, 750)
    app.processEvents()

    lbl = pg.sticker_preview
    assert lbl.pixmap() is not None and not lbl.pixmap().isNull()
    orig = getattr(lbl, "_orig_pixmap", None)
    assert orig is not None and not orig.isNull(), "original must be stored"
    orig_ratio = orig.width() / orig.height()

    def _check_fits(tag: str):
        app.processEvents()
        px = lbl.pixmap()
        assert px is not None and not px.isNull(), tag
        # Actual padded content bounds straight from Qt.
        cr = lbl.contentsRect()
        avail_w, avail_h = int(cr.width()), int(cr.height())
        assert avail_w > 0 and avail_h > 0, (tag, lbl.size().toTuple())
        assert px.width() <= avail_w and px.height() <= avail_h, \
            (tag, px.width(), px.height(), avail_w, avail_h)
        ratio = px.width() / px.height()
        assert abs(ratio - orig_ratio) / orig_ratio < 0.03, \
            (tag, ratio, orig_ratio)
        # No excessive blank area: one dimension fills the padded bounds.
        assert (px.width() >= avail_w - 2) or (px.height() >= avail_h - 2), \
            (tag, px.width(), px.height(), avail_w, avail_h)
        from PySide6.QtWidgets import QSizePolicy as _SP
        assert lbl.sizePolicy().horizontalPolicy() == _SP.Ignored, tag
        assert lbl.minimumHeight() == 0, (tag, lbl.minimumHeight())
        assert pg._preview_card.height() <= 260, \
            (tag, pg._preview_card.height())

    _check_fits("large")
    # Shrink the window: layout compresses the label, the filter rescales
    # the sticker down inside it — never overflow/clip.
    pg.resize(700, 500)
    _check_fits("small")

    pg.close()


if __name__ == "__main__":
    test_stock_fixed_4x3in_no_height_option()
    test_idle_shows_hint_not_art()
    test_reel_weigh_shows_only_reel_art()
    test_box_preview_in_carton_mode_needs_no_reels()
    test_mode_switch_changes_preview_only_without_writes()
    test_reel_table_art_matches_photo_layout()
    test_reel_sticker_shows_no_of_pieces()
    test_preview_writes_nothing()
    test_customer_columns_migrated_and_inherited()
    test_preview_fits_padded_bounds_on_resize()
    print("test_sticker_preview PASS")
