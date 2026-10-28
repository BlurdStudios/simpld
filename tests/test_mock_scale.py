"""Hardware gating on the packaging page: no fakes in production.

Blank scale/printer config blocks weighing/printing (with Devices guidance)
instead of fabricating records. The mock drivers stay only for test
compatibility (SIMPLD_ALLOW_MOCK=1). Temp DB only — never production.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-mock-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    os.environ["SIMPLD_ALLOW_MOCK"] = "1"  # test-only HW gate override
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


def _select_job(pg, job_id):
    pg.refresh_jobs()
    for i in range(pg.job_combo.count()):
        if pg.job_combo.itemData(i) == job_id:
            pg.job_combo.setCurrentIndex(i)
            return
    raise AssertionError("job not in combo")


def _blank_hw_config():
    """Point SIMPLD_HW_JSON at a blank temp config.

    Hardware gating tests must not depend on this machine's real
    data/hardware.json (a dev PC may have demo_mode/mock_qr on, CI has no
    file at all). Returns the previous env value for restoration.
    """
    import json as _json
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-hw-"), "hardware.json")
    with open(tmp, "w") as f:
        _json.dump({"scale_port": "", "printer_name": "",
                    "demo_mode": False, "mock_qr": False}, f)
    old = os.environ.get("SIMPLD_HW_JSON")
    os.environ["SIMPLD_HW_JSON"] = tmp
    return old


def _restore_hw_config(old):
    if old is None:
        os.environ.pop("SIMPLD_HW_JSON", None)
    else:
        os.environ["SIMPLD_HW_JSON"] = old


def test_no_mock_helpers_in_production_ui():
    _fresh_db()
    _, pg = _page()
    for attr in ("btn_mock_reel", "btn_mock_box", "btn_dbg_reels",
                 "mock_weigh", "debug_add_reels", "debug_pack_carton"):
        assert not hasattr(pg, attr), f"production UI must not keep {attr}"
    pg.close()


def test_blank_config_blocks_weighing_not_mock_records():
    from factory.hardware import mock as HW
    old_hw = _blank_hw_config()
    try:
        p = _fresh_db()
        job = S.create_job("GATE JOB", core_wt=0.280, path=p)
        _, pg = _page()
        _select_job(pg, job["id"])
        old = os.environ.pop("SIMPLD_ALLOW_MOCK", None)
        try:
            pg.gross.setValue(5.0)
            pg.read_scale()
            assert "not configured" in pg.msg.casefold(), pg.msg
            assert pg.gross.value() == 0
            assert S.loose_reels(job["id"], path=p) == [], "blocked read must write nothing"
            assert S.ready_stock(job["id"], path=p) == []
        finally:
            if old is not None:
                os.environ["SIMPLD_ALLOW_MOCK"] = old
        pg.close()
    finally:
        _restore_hw_config(old_hw)


def test_mock_allowed_read_writes_nothing_by_itself():
    from factory.hardware import mock as HW
    p = _fresh_db()
    job = S.create_job("MOCK READ JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    HW.scale.set_manual(11.380)
    pg.read_scale()
    assert pg.gross.value() > 0, "test-enabled mock read fills the card"
    assert S.loose_reels(job["id"], path=p) == [], "read must not save by itself"
    pg.close()


def test_read_needs_job():
    _fresh_db()
    _, pg = _page()
    pg.job_id = None
    pg.read_scale()
    assert "job" in pg.msg.casefold(), pg.msg
    pg.close()


def test_mock_override_ignored_when_frozen(monkeypatch):
    """SIMPLD_ALLOW_MOCK=1 must not unlock mock drivers in the frozen build."""
    from factory.hardware import devices

    # Frozen: the override is ignored even when the env var is set.
    monkeypatch.setenv("SIMPLD_ALLOW_MOCK", "1")
    monkeypatch.setattr("factory.db._is_frozen", lambda: True)
    assert devices.mock_allowed() is False
    with pytest.raises(RuntimeError):
        devices.require_scale({})

    # Source checkout: the override still works for tests.
    monkeypatch.setattr("factory.db._is_frozen", lambda: False)
    assert devices.mock_allowed() is True


if __name__ == "__main__":
    test_no_mock_helpers_in_production_ui()
    test_blank_config_blocks_weighing_not_mock_records()
    test_mock_allowed_read_writes_nothing_by_itself()
    test_read_needs_job()
    print("test_mock_scale PASS")
