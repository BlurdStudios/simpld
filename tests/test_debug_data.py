"""Fixture seeding: production starts empty; fixtures are explicit test-only.

Production startup (factory.seed.seed / ensure_production_db) creates schema
only — never sample jobs/reels/cartons/orders. Demo data lives ONLY in
seed_sample_data(), which requires bootstrap via tests/_auth_helper
(ensure_default_users) and a real admin session on the temp DB.
Temp DB only — never production.
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-dbg-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    return tmp


def test_production_seed_creates_no_sample_data():
    from factory import seed as SEED
    p = _fresh_db()
    SEED.seed(p)
    assert S.list_jobs(path=p) == [], "production seed must not create jobs"
    assert S.ready_stock(path=p) == []
    assert S.list_orders(path=p) == []
    assert S.production_history(path=p) == []
    # second run stays empty (idempotent, never duplicates)
    SEED.seed(p)
    assert S.list_jobs(path=p) == []


def test_sample_data_helper_is_explicit_and_complete():
    from factory import seed as SEED
    from tests._auth_helper import ensure_default_users
    p = _fresh_db()
    ensure_default_users(p)
    out = SEED.seed_sample_data(p, "admin", "admin123")
    jobs = S.list_jobs(path=p)
    assert len(jobs) >= 2, jobs
    stock = S.ready_stock(path=p)
    assert len(stock) >= 2, "fixture Ready cartons"
    orders = S.list_orders(path=p)
    assert any(o["status"] == "loading" for o in orders), "fixture loading order"
    hist = S.production_history(path=p)
    assert any(r["kind"] == "Box" for r in hist), "fixture history"
    assert out["jobs"], "helper returns what it built"


def test_production_ui_has_no_debug_data_helpers():
    _fresh_db()
    from PySide6.QtWidgets import QApplication
    from factory.ui.page_packaging import PackagingPage
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    pg = PackagingPage()
    for attr in ("btn_dbg_reels", "debug_add_reels", "debug_pack_carton",
                 "mock_weigh", "btn_mock_reel", "btn_mock_box"):
        assert not hasattr(pg, attr), f"production UI must not keep {attr}"
    pg.close()


if __name__ == "__main__":
    test_production_seed_creates_no_sample_data()
    test_sample_data_helper_is_explicit_and_complete()
    test_production_ui_has_no_debug_data_helpers()
    print("test_debug_data PASS")
