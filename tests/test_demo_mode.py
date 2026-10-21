"""The explicit demo switch: simulated scale + printer, on purpose.

Demo mode is the ONE path that reaches the mock drivers in a frozen build:
a site with no weigh machine / label printer can still be shown end to end.
Default OFF keeps production fail-closed (plan 004's rule is untouched);
SIMPLD_ALLOW_MOCK stays dead when frozen. Temp DB / temp hardware.json only.
"""
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory.hardware import config as HWC


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-demo-"), "factory.db")
    from factory import db as DB
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from tests._auth_helper import ensure_default_users
    ensure_default_users(tmp)
    from factory import auth as _A
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


def _temp_hw_json():
    return os.path.join(tempfile.mkdtemp(prefix="simpld-demo-"), "hardware.json")


def test_config_demo_mode_defaults_off_and_round_trips(tmp_path):
    assert HWC.DEFAULTS["demo_mode"] is False
    tmp = str(tmp_path / "hardware.json")
    HWC.save({"demo_mode": True}, tmp)
    assert HWC.load(tmp)["demo_mode"] is True
    assert HWC.DEFAULTS["demo_mode"] is False, "save() must not mutate DEFAULTS"


def test_simpld_demo_env_forces_on(tmp_path, monkeypatch):
    tmp = str(tmp_path / "hardware.json")
    monkeypatch.setenv("SIMPLD_DEMO", "1")
    assert HWC.load(tmp)["demo_mode"] is True


def test_demo_mode_honored_even_when_frozen(monkeypatch):
    """The demo switch is the one override that IS honored when frozen —
    but only in DEMO builds; SIMPLD_ALLOW_MOCK stays dead (plan 004 must
    not regress)."""
    from factory import db as _db
    from factory import version as _ver
    from factory.hardware import devices, mock
    monkeypatch.setattr(_ver, "DEMO_ENABLED", True)   # simulate a demo build
    monkeypatch.setattr(_db, "_is_frozen", lambda: True)
    cfg = {"demo_mode": True}
    assert isinstance(devices.build_scale(cfg), mock.MockEssaeDS215)
    assert isinstance(devices.build_printer(cfg), mock.MockCitizenCLS621)
    devices.require_scale(cfg)      # must not raise
    devices.require_printer(cfg)    # must not raise
    monkeypatch.setenv("SIMPLD_ALLOW_MOCK", "1")
    assert devices.mock_allowed() is False, \
        "the test override stays dead when frozen"


def test_demo_disabled_in_prod_frozen_build(monkeypatch, tmp_path):
    """Prod installers (DEMO_ENABLED = False, frozen): the switch, the env
    force and any stored demo_mode are all ignored — fail-closed."""
    from factory import db as _db
    from factory import version as _ver
    from factory.hardware import devices
    monkeypatch.setattr(_ver, "DEMO_ENABLED", False)
    monkeypatch.setattr(_db, "_is_frozen", lambda: True)
    assert HWC.demo_build_enabled() is False
    assert devices.demo_mode({"demo_mode": True}) is False
    with pytest.raises(RuntimeError):
        devices.require_scale({"demo_mode": True})
    with pytest.raises(RuntimeError):
        devices.require_printer({"demo_mode": True})
    monkeypatch.setenv("SIMPLD_DEMO", "1")
    assert HWC.load(str(tmp_path / "hardware.json"))["demo_mode"] is False


def test_demo_button_hidden_in_prod_build(monkeypatch):
    """Prod builds don't even create the Demo switch in the strip."""
    from PySide6.QtWidgets import QApplication
    from factory import db as _db
    from factory import version as _ver
    from factory.ui.device_strip import DeviceStrip
    monkeypatch.setattr(_ver, "DEMO_ENABLED", False)
    monkeypatch.setattr(_db, "_is_frozen", lambda: True)
    app = QApplication.instance() or QApplication([])
    strip = DeviceStrip("packaging", demo_switch=True)
    assert getattr(strip, "btn_demo", None) is None
    strip.close()


def test_demo_button_present_in_demo_build(monkeypatch):
    """Demo builds (frozen) still get the switch."""
    from PySide6.QtWidgets import QApplication
    from factory import db as _db
    from factory import version as _ver
    from factory.ui.device_strip import DeviceStrip
    monkeypatch.setattr(_ver, "DEMO_ENABLED", True)
    monkeypatch.setattr(_db, "_is_frozen", lambda: True)
    app = QApplication.instance() or QApplication([])
    strip = DeviceStrip("packaging", demo_switch=True)
    assert getattr(strip, "btn_demo", None) is not None
    assert strip.btn_demo.text() == "Demo"
    strip.close()


def test_demo_mode_off_keeps_fail_closed(monkeypatch):
    """Switch off (frozen run): blank config still blocks weighing."""
    from factory import db as _db
    from factory.hardware import devices
    monkeypatch.setattr(_db, "_is_frozen", lambda: True)
    with pytest.raises(RuntimeError):
        devices.require_scale({"demo_mode": False})
    with pytest.raises(RuntimeError):
        devices.require_printer({"demo_mode": False})


def test_mock_scale_demo_simulation():
    from factory.hardware import mock
    m = mock.MockEssaeDS215(demo=True)
    first = m.read_weight()
    assert 3.9 <= first <= 24.1, first
    second = m.read_weight()
    assert abs(first - second) < 0.05, (first, second)
    frozen = m.freeze()
    assert abs(frozen - second) < 0.05, (frozen, second)
    for _ in range(3):
        assert m.read_weight() == frozen, "frozen demo reads must repeat"
    m.unfreeze()
    again = m.read_weight()
    assert 3.9 <= again <= 24.1, again
    # regression: default instance keeps the old manual-entry behavior
    plain = mock.MockEssaeDS215()
    assert plain.read_weight() == 0.0
    assert plain.freeze() == 0.0


def test_status_pills_read_demo():
    from factory.hardware import devices as DEV
    from factory.hardware import status as Status
    from factory.hardware import mock as HW
    tmp = _temp_hw_json()
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    os.environ["SIMPLD_HW_JSON"] = tmp
    try:
        cfg = {"demo_mode": True, "scale_port": "", "printer_name": ""}
        DEV.reload_hardware(cfg)
        s = Status.device_status("scale", cfg)
        p = Status.device_status("printer", cfg)
        assert s["state"] == "demo" and s["detail"] == "simulated", s
        assert p["state"] == "demo" and p["detail"] == "simulated", p
        assert Status.needs_setup("packaging", cfg) is False
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        DEV.reload_hardware({"scale_port": "", "printer_name": ""})


def test_packaging_demo_switch_toggles_and_weighs(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from factory import services as S
    from factory.hardware import devices as DEV
    from factory.hardware import mock as HW
    from factory.hardware import mock

    def _no_confirm(*a, **k):
        raise AssertionError("no confirmation must be shown when disabling")

    tmp = _temp_hw_json()
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    os.environ["SIMPLD_HW_JSON"] = tmp
    monkeypatch.delenv("SIMPLD_ALLOW_MOCK", raising=False)
    try:
        p = _fresh_db()
        _app, pg = _page()
        assert not pg.devices.btn_demo.isChecked()
        assert pg.devices.btn_demo.text() == "Demo"

        # ... confirm -> ON
        monkeypatch.setattr(
            "factory.ui.device_strip.QMessageBox.question",
            staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
        pg.devices.btn_demo.click()
        assert pg.devices.btn_demo.isChecked()
        assert pg.devices.btn_demo.text() == "Demo ON"
        assert HWC.load()["demo_mode"] is True
        assert isinstance(mock.scale, mock.MockEssaeDS215)
        assert DEV.demo_mode() is True

        # weigh in demo: no SIMPLD_ALLOW_MOCK, the switch alone passes the gate
        job = S.create_job("DEMO JOB", core_wt=0.280, path=p)
        _select_job(pg, job["id"])
        pg.read_scale()
        assert pg.gross.value() > 0, pg.msg
        assert "not configured" not in pg.msg.casefold(), pg.msg
        assert S.loose_reels(job["id"], path=p) == [], "read must not save by itself"

        # ... click again -> OFF, and no confirmation is asked when disabling
        monkeypatch.setattr("factory.ui.device_strip.QMessageBox.question",
                            staticmethod(_no_confirm))
        pg.devices.btn_demo.click()
        assert not pg.devices.btn_demo.isChecked()
        assert pg.devices.btn_demo.text() == "Demo"
        assert HWC.load()["demo_mode"] is False
        assert DEV.demo_mode() is False
        pg.close()
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        DEV.reload_hardware({"scale_port": "", "printer_name": ""})


if __name__ == "__main__":
    print("run via pytest")
