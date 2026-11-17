"""Real-hardware integration: selection, strict reads, Qt printing, no fakes.

Covers the devices registry (mock default vs real selection from existing
config), the Essae driver's fresh-read-or-error behavior plus its line
parser on known-supported frames, the Qt Citizen driver's complete-data
rendering/painter flow with fakes (no real printer jobs in tests), the
Packaging mock-controls toggle + Read/Stop error paths, and Devices dialog
persistence. Temp DB + offscreen — never production, never real hardware.
"""
import os
import sys
import pytest
import json
import tempfile
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from factory.hardware import mock as HW
from factory.hardware import devices as DEV
from factory.hardware import config as HWC
from factory.hardware import status as HWStatus
from factory.hardware.essae import EssaeDS215, parse_weight_line
from factory.hardware.vcom import VcomCitizen
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-hw-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    os.environ["SIMPLD_ALLOW_MOCK"] = "1"  # test-only: exercise mock HW gates
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


class _patch_hw:
    """Temporarily swap shared HW.scale/HW.printer, then restore + reload."""

    def __init__(self, scale=None, printer=None, keep_scale=False, keep_printer=False):
        self.scale, self.printer = scale, printer
        self.keep_scale, self.keep_printer = keep_scale, keep_printer

    def __enter__(self):
        self._s, self._p = HW.scale, HW.printer
        if not self.keep_scale:
            HW.scale = self.scale
        if not self.keep_printer:
            HW.printer = self.printer
        return self

    def __exit__(self, *a):
        HW.scale, HW.printer = self._s, self._p
        return False


# ----- parser: known-supported Essae frames only (nothing invented) -----
def test_parser_supported_frames():
    assert parse_weight_line(" 12.340 kg") == 12.34
    assert parse_weight_line("+12.340") == 12.34
    assert parse_weight_line("ST,GS,+ 12.340kg") == 12.34
    assert parse_weight_line("  5.000  ") == 5.0


def test_parser_rejects_unstable_and_nonsense():
    assert parse_weight_line("") is None
    assert parse_weight_line("UN 12.340") is None
    assert parse_weight_line("MOTION +12.340kg") is None
    assert parse_weight_line("OL") is None
    assert parse_weight_line("ERR 00") is None
    assert parse_weight_line("no weight here") is None
    assert parse_weight_line("-1.500 kg") is None
    assert parse_weight_line("999.999 kg") is None  # >500kg sanity cap


# ----- fresh reading: strict, never stale/zero as success -----
def _drv_with_lines(lines):
    d = EssaeDS215("COM9")
    d._read_lines = lambda dwell=0.8: list(lines)
    return d


def test_real_read_returns_fresh_value():
    d = _drv_with_lines(["ST,GS,+ 12.340kg"])
    assert d.read_weight() == 12.34


def test_real_read_timeout_errors_not_zero():
    d = _drv_with_lines([])
    try:
        d.read_weight()
    except RuntimeError as e:
        assert "COM9" in str(e)
    else:
        raise SystemExit("FAIL: timeout must raise, not return 0.0")


def test_real_read_unstable_errors_not_stale():
    d = _drv_with_lines(["ST,GS,+ 12.340kg"])
    assert d.read_weight() == 12.34
    d._read_lines = lambda dwell=0.8: ["UNSTABLE +12.999kg", "MOTION"]
    try:
        d.read_weight()
    except RuntimeError as e:
        assert "stable" in str(e).casefold()
    else:
        raise SystemExit("FAIL: unstable-only lines must raise, not go stale")


def test_real_freeze_propagates_error():
    d = _drv_with_lines([])
    try:
        d.freeze()
    except RuntimeError:
        pass
    else:
        raise SystemExit("FAIL: freeze with no data must raise")


def test_real_open_failure_errors():
    d = EssaeDS215("COM9")
    with _mock.patch("serial.Serial", side_effect=Exception("denied")):
        try:
            d.read_weight()
        except Exception:
            pass
        else:
            raise SystemExit("FAIL: disconnect must raise")


# ----- selection: explicit mock default, real when configured -----
def test_blank_config_selects_mock():
    assert isinstance(DEV.build_scale({"scale_port": ""}), HW.MockEssaeDS215)
    assert isinstance(DEV.build_printer({"printer_port": ""}), VcomCitizen)
    assert DEV.mode_text({"scale_port": "", "printer_port": ""}).startswith(
        "Real:")


def test_configured_port_selects_real_scale():
    d = DEV.build_scale({"scale_port": "COM3", "scale_baud": 9600})
    assert isinstance(d, EssaeDS215) and d.port == "COM3" and d.baud == 9600


def test_configured_printer_selects_vcom_port():
    p = DEV.build_printer({"printer_port": "COM5", "printer_baud": 9600})
    assert isinstance(p, VcomCitizen)
    assert p.port == "COM5" and p.baud == 9600


def test_reload_swaps_shared_instances_without_restart():
    old_s, old_p = HW.scale, HW.printer
    try:
        DEV.reload_hardware({"scale_port": "COM3", "scale_baud": 9600,
                             "printer_port": "COM5"})
        assert isinstance(HW.scale, EssaeDS215)
        assert isinstance(HW.printer, VcomCitizen)
        assert "COM3" in DEV.mode_text(
            {"scale_port": "COM3", "printer_port": "COM5"})
        DEV.reload_hardware({"scale_port": "", "scale_baud": 9600,
                             "printer_port": ""})
        assert isinstance(HW.scale, HW.MockEssaeDS215)
        assert isinstance(HW.printer, VcomCitizen)
    finally:
        HW.scale, HW.printer = old_s, old_p


# ----- Qt printing with fakes: render + painter verified, no real jobs -----
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


def test_no_mock_helpers_blank_config_blocks_weighing():
    """Production has no fake-weight/test-data helpers; blank hardware
    config blocks weighing/printing instead of fabricating records."""
    old_hw = _blank_hw_config()
    try:
        p = _fresh_db()
        job = S.create_job("NOHELP JOB", core_wt=0.280, path=p)
        _, pg = _page()
        _select_job(pg, job["id"])
        for attr in ("btn_mock_reel", "btn_mock_box", "btn_dbg_reels",
                     "mock_weigh", "debug_add_reels", "debug_pack_carton"):
            assert not hasattr(pg, attr), f"production UI must not keep {attr}"
        # Device strip is the live hardware surface now (no hw_mode label).
        from factory.ui.device_strip import DeviceStrip
        from factory.hardware import status as HWStatus
        strip = pg.findChild(DeviceStrip)
        assert strip is not None, "packaging page must show a device strip"
        # Hermetic: blank config must read setup even on a bench where a real
        # Citizen is attached over raw USB (this test pins NO hardware).
        with _mock.patch("factory.hardware.vcom.citizen_usb_present",
                         return_value=False):
            states = {d["key"]: d["state"] for d in HWStatus.station_status("packaging")}
        assert set(states) == {"scale", "printer"}, states
        assert all(s == "setup" for s in states.values()), states
        # Blank config (mock not test-enabled) blocks weighing with guidance.
        old = os.environ.pop("SIMPLD_ALLOW_MOCK", None)
        try:
            pg.job_id = pg.job_id  # keep selection state
            pg.gross.setValue(5.0)
            pg.read_scale()
            assert "not configured" in pg.msg.casefold(), pg.msg
            assert pg.gross.value() == 0, "blocked read must invalidate the card"
        finally:
            if old is not None:
                os.environ["SIMPLD_ALLOW_MOCK"] = old
        pg.close()
    finally:
        _restore_hw_config(old_hw)


def test_read_scale_real_error_invalidates_weight_and_action():
    """A failed real read must clear the card to 0 and disarm the action —
    a prior positive gross must never stay armed as a fresh reading."""
    p = _fresh_db()
    job = S.create_job("REAL READ JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.gross.setValue(5.0)

    class FailingScale:
        def read_weight(self):
            raise RuntimeError("No data from scale on COM9")

        def freeze(self):
            raise RuntimeError("No data from scale on COM9")

    with _patch_hw(scale=FailingScale()):
        pg.read_scale()
        assert "scale error" in pg.msg.casefold(), pg.msg
        assert pg.gross.value() == 0, "failed read must invalidate stale weight"
        assert not pg.btn_action.isEnabled(), "failed read must disarm action"
        pg.gross.setValue(5.0)  # operator re-weighs / types manually
        pg.stop_wt()
        assert "scale error" in pg.msg.casefold(), pg.msg
        assert pg.gross.value() == 0, "failed stop must invalidate too"
        assert not pg.btn_action.isEnabled()
    pg.close()


def test_genuine_zero_stop_sets_zero_not_stale():
    """stop_wt with a real 0.000 kg freeze must land on 0, not keep old."""
    p = _fresh_db()
    job = S.create_job("ZERO STOP JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])
    pg.gross.setValue(5.0)

    class ZeroScale:
        def read_weight(self):
            return 0.0

        def freeze(self):
            return 0.0

    with _patch_hw(scale=ZeroScale()):
        pg.stop_wt()
        assert pg.gross.value() == 0, pg.gross.value()
        assert not pg.btn_action.isEnabled(), "zero must not arm action"
    pg.close()


def test_hardware_mode_switch_invalidates_stale_weight():
    """Switching real<->mock via refresh_hardware() clears the card: a
    weight read under one scale mode must not stay armed under the other."""
    _fresh_db()
    _, pg = _page()
    assert pg._hw_was_mock is True
    pg.gross.setValue(5.0)
    with _patch_hw(scale=EssaeDS215("COM9")):
        pg.refresh_hardware()
        assert pg.gross.value() == 0, "mode switch must invalidate stale weight"
        assert not pg.btn_action.isEnabled()
    pg.refresh_hardware()
    pg.close()


def test_configured_startup_invalid_reports_no_hidden_mock():
    """A configured device that fails to build must raise at point of use
    and show in mode_text() — never silently become demo/mock."""
    old_s, old_p = HW.scale, HW.printer
    old_err = DEV.init_error
    try:
        with _mock.patch.object(
                DEV, "build_scale", side_effect=Exception("denied")):
            DEV.reload_hardware({"scale_port": "COM9", "scale_baud": 9600,
                                 "printer_name": ""})
            assert not isinstance(HW.scale, HW.MockEssaeDS215), \
                "broken configured scale must not masquerade as mock"
            try:
                HW.scale.read_weight()
            except RuntimeError as e:
                assert "denied" in str(e), e
            else:
                raise SystemExit("FAIL: broken scale must raise, not read")
            assert "CONFIG ERROR" in DEV.mode_text(
                {"scale_port": "COM9", "printer_name": ""}), DEV.mode_text()
    finally:
        HW.scale, HW.printer = old_s, old_p
        DEV.init_error = old_err


def test_stored_configs_use_stable_data_root():
    """hardware.json + print log default into SIMPLD_DATA_DIR (frozen-safe
    root), so the installed .exe persists outside the install dir."""
    tmp = tempfile.mkdtemp(prefix="simpld-data-root-")
    old = os.environ.get("SIMPLD_DATA_DIR")
    os.environ["SIMPLD_DATA_DIR"] = tmp
    try:
        from factory.db import app_data_dir
        assert app_data_dir() == tmp
        assert HWC._default_path() == os.path.join(tmp, "hardware.json")
        HWC.save({"scale_port": "COM3", "scale_baud": 9600,
                  "printer_name": "", "label_width_mm": 100,
                  "label_height_mm": 50, "label_dpi": 203})
        assert os.path.exists(os.path.join(tmp, "hardware.json"))
        assert HWC.load()["scale_port"] == "COM3"
        assert HW.MockCitizenCLS621().log_path == os.path.join(
            tmp, "print_log.txt")
    finally:
        if old is None:
            os.environ.pop("SIMPLD_DATA_DIR", None)
        else:
            os.environ["SIMPLD_DATA_DIR"] = old


def test_autodetect_finds_scale_by_probing_ports():
    """No dropdown: detection opens each port and keeps the one that answers
    with a parseable weight frame."""
    from factory.hardware import autodetect as Auto

    class Answering(EssaeDS215):
        """Behaves like a real Essae on the 'good' port only."""

        def _read_lines(self, dwell=0.8):
            if self.port == "COM9":
                return ["ST,GS,+   4.250kg", "ST,GS,+   4.250kg"]
            return []          # wrong port stays silent -> no false positive

    with _mock.patch("factory.hardware.essae.list_ports",
                     return_value=["COM1", "COM9", "COM3"]), \
         _mock.patch.object(EssaeDS215, "_read_lines", Answering._read_lines):
        hits = Auto.probe_ports()
    assert [h["port"] for h in hits] == ["COM9"], hits
    assert hits[0]["baud"] == Auto.PRIMARY_BAUD, hits
    assert hits[0]["weight"] == 4.25, hits


def test_autodetect_persists_scale():
    """A successful probe writes hardware.json and goes live without restart."""
    from factory.hardware import autodetect as Auto
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auto-"), "hardware.json")
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    os.environ["SIMPLD_HW_JSON"] = tmp

    class Answering(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return ["ST,GS,+   1.500kg"] if self.port == "COM4" else []

    try:
        with _mock.patch("factory.hardware.essae.list_ports",
                         return_value=["COM4"]), \
             _mock.patch.object(EssaeDS215, "_read_lines",
                               Answering._read_lines):
            result = Auto.detect_all(save=True)
        assert result["scale"] and result["scale"]["port"] == "COM4", result
        assert HWC.load(tmp)["scale_port"] == "COM4"
        assert isinstance(HW.scale, EssaeDS215), "detection must apply live"
        assert getattr(HW.scale, "port", None) == "COM4", \
            "wrong scale must never be selected"
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        HW.scale, HW.printer = old_s, old_p


def test_autodetect_no_answer_stays_unconfigured():
    """Nothing found -> stays unconfigured, so the app fails closed (never
    silently falls back to the mock) and the pill reads 'not found'."""
    from factory.hardware import autodetect as Auto
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auto-"), "hardware.json")
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    os.environ["SIMPLD_HW_JSON"] = tmp

    class Silent(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return []

    # mock_allowed() would otherwise short-circuit require_scale()
    old_mock = os.environ.pop("SIMPLD_ALLOW_MOCK", None)
    try:
        with _mock.patch("factory.hardware.essae.list_ports",
                         return_value=["COM1", "COM2"]), \
             _mock.patch.object(EssaeDS215, "_read_lines", Silent._read_lines):
            result = Auto.detect_all(save=True)
        assert result["scale"] is None, result
        assert HWC.load(tmp)["scale_port"] == ""
        assert isinstance(HW.scale, HW.MockEssaeDS215), "no silent real fallback"
        try:
            DEV.require_scale()
            raise SystemExit("FAIL: unconfigured scale must fail closed")
        except RuntimeError as e:
            assert "Scale not configured" in str(e), e
    finally:
        if old_mock is not None:
            os.environ["SIMPLD_ALLOW_MOCK"] = old_mock
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        HW.scale, HW.printer = old_s, old_p


def test_autodetect_forgets_devices_that_stopped_answering():
    """Stale hardware.json must not report phantom 'ready' pills.

    Regression: detect_all only wrote on success, so a scale/printer that
    vanished kept its old port/name and the pills read 'ready' for hardware
    that was no longer there. A probe that finds nothing now clears the
    entry (unless an env override pins it).
    """
    from factory.hardware import autodetect as Auto
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auto-"), "hardware.json")
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    old_port = os.environ.pop("SIMPLD_SCALE_PORT", None)
    os.environ["SIMPLD_HW_JSON"] = tmp
    with open(tmp, "w") as f:
        json.dump({"scale_port": "COM7", "scale_baud": 9600}, f)

    class Silent(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return []

    try:
        with _mock.patch("factory.hardware.essae.list_ports",
                          return_value=["COM7"]), \
              _mock.patch.object(EssaeDS215, "_read_lines", Silent._read_lines):
            result = Auto.detect_all(save=True)
        assert result["scale"] is None, result
        cfg = HWC.load(tmp)
        assert cfg["scale_port"] == "", cfg
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        if old_port is not None:
            os.environ["SIMPLD_SCALE_PORT"] = old_port
        HW.scale, HW.printer = old_s, old_p


def test_autodetect_keeps_env_pinned_config_when_nothing_answers():
    """Env overrides still win: a pinned entry is not forgotten."""
    from factory.hardware import autodetect as Auto
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auto-"), "hardware.json")
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    old_port = os.environ.get("SIMPLD_SCALE_PORT")
    os.environ["SIMPLD_HW_JSON"] = tmp
    os.environ["SIMPLD_SCALE_PORT"] = "COM9"
    with open(tmp, "w") as f:
        json.dump({"scale_port": "COM7", "scale_baud": 9600}, f)

    class Silent(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return []

    try:
        with _mock.patch("factory.hardware.essae.list_ports",
                          return_value=["COM9"]), \
              _mock.patch.object(EssaeDS215, "_read_lines", Silent._read_lines):
            result = Auto.detect_all(save=True)
        assert result["scale"] is None, result
        cfg = HWC.load(tmp)
        assert cfg["scale_port"] == "COM9", cfg
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        if old_port is None:
            os.environ.pop("SIMPLD_SCALE_PORT", None)
        else:
            os.environ["SIMPLD_SCALE_PORT"] = old_port
        HW.scale, HW.printer = old_s, old_p


def test_scanner_attempts_without_landing():
    """Input with no landed scan reads differently from a dead wedge."""
    HWStatus.reset_scanner_session()
    try:
        cfg = _live_cfg()
        d = HWStatus.device_status("scanner", cfg)
        assert d["state"] == "idle" and d["detail"] == "wedge · no scan yet", d
        assert "hint" not in d, d
        HWStatus.note_scan_attempt()
        HWStatus.note_scan_attempt()
        d = HWStatus.device_status("scanner", cfg)
        assert d["state"] == "idle", d
        assert "none landed" in d["detail"], d
        assert "hint" in d, d  # points at the error line, not the cable
        HWStatus.note_scan()
        d = HWStatus.device_status("scanner", cfg)
        assert d["state"] == "active", d
    finally:
        HWStatus.reset_scanner_session()


def test_scanner_session_resets_on_logout():
    HWStatus.note_scan()
    HWStatus.note_scan_attempt()
    HWStatus.reset_scanner_session()
    assert HWStatus.scanner_activity() == (0, None)
    assert HWStatus.scanner_attempts() == 0
    assert not HWStatus.scanner_input_without_landing()


def _live_cfg(**kw):
    cfg = {"scale_port": "", "scale_baud": 9600, "printer_port": "",
           "demo_mode": False, "mock_qr": False}
    cfg.update(kw)
    return cfg


def _live_mocks(ports):
    """Stacked patches for live-status tests (fully offline).

    One serial port list drives both the scale and the printer (VCOM).
    """
    return (
        _mock.patch.object(DEV, "init_error", None),
        _mock.patch("factory.hardware.devices.is_mock_scale",
                    return_value=False),
        _mock.patch("factory.hardware.devices.is_mock_printer",
                    return_value=False),
        _mock.patch("factory.hardware.essae.list_ports",
                    return_value=ports),
    )


def test_live_status_ready_when_everything_attached():
    cfg = _live_cfg(scale_port="COM4", printer_port="COM5")
    a, b, c, d = _live_mocks(["COM4", "COM5"])
    with a, b, c, d:
        states = [s["state"] for s in HWStatus.station_live_status("packaging", cfg)]
    assert states == ["ready", "ready"], states


def test_live_status_unplugged_scale_reads_not_found():
    """Pulled serial adapter: port gone from enumeration -> setup."""
    cfg = _live_cfg(scale_port="COM4", printer_port="COM5")
    a, b, c, d = _live_mocks(["COM9", "COM5"])
    with a, b, c, d:
        specs = HWStatus.station_live_status("packaging", cfg)
    assert specs[0]["state"] == "setup", specs
    assert specs[0]["detail"] == "not found", specs
    assert specs[1]["state"] == "ready", specs


def test_live_status_printer_config_trusted_no_flip():
    """The printer has no detection logic: a configured port stays ready
    even when it vanishes from enumeration. A pulled cable fails loudly
    at print time (the error names the port) instead of the pill flipping.
    """
    cfg = _live_cfg(scale_port="COM4", printer_port="COM5")
    a, b, c, _d = _live_mocks(["COM4"])  # COM5 gone
    with a, b, c, _d:
        specs = HWStatus.station_live_status("packaging", cfg)
    assert specs[0]["state"] == "ready", specs
    assert specs[1]["state"] == "ready", specs
    assert specs[1]["detail"] == "COM5 · VCOM", specs


def test_live_status_unknowable_keeps_last_verdict():
    """Port list unreadable: never flap a working pill."""
    cfg = _live_cfg(scale_port="COM4", printer_port="COM5")
    a, b, c, _d = _live_mocks(None)
    with a, b, c, _mock.patch("factory.hardware.essae.list_ports",
                              side_effect=RuntimeError("enum failed")):
        states = [s["state"] for s in HWStatus.station_live_status("packaging", cfg)]
    assert states == ["ready", "ready"], states


def test_sysfs_usb_printer_scan():
    """REMOVED: the printer has no OS USB detection (VCOM device, owner
    request). Kept as a placeholder so the runner list stays stable."""
    assert not hasattr(HWStatus, "usb_printer_present")
    assert not hasattr(HWStatus, "_sysfs_usb_printer_present")
    assert not hasattr(HWStatus, "_windows_usbprint_present")


# ---- Linux raw-USB transport (the Citizen makes no tty node there) ------

def test_match_citizen_port_pure_matching():
    """The VID/PID -> description -> never-guess filter, no enumeration.

    Pure filter over a caller-supplied port list, so the pill cache and
    tests can feed it anything with .device/.vid/.pid/.description.
    """
    from factory.hardware import vcom as V

    class P:
        def __init__(self, device, vid=None, pid=None, description=""):
            self.device, self.vid, self.pid = device, vid, pid
            self.description = description

    hit = P("COM7", vid=V.CITIZEN_VID, pid=V.CITIZEN_PID)
    assert V.match_citizen_port([P("COM3", vid=0x1234, pid=0x5678), hit]) == "COM7"
    # description fallback when the IDs are hidden (some hubs report 0/None)
    desc = P("COM8", vid=0, pid=0, description="Citizen CL-S621")
    assert V.match_citizen_port([desc]) == "COM8"
    assert V.match_citizen_port([P("COM3", vid=0x1234, pid=0x5678,
                                   description=None)]) is None
    # two Citizens on the bus: never guess
    two = [P("COM7", vid=V.CITIZEN_VID, pid=V.CITIZEN_PID),
           P("COM9", vid=V.CITIZEN_VID, pid=V.CITIZEN_PID)]
    assert V.match_citizen_port(two) is None
    # no Citizen at all
    assert V.match_citizen_port([P("COM3", vid=0x1234, pid=0x5678)]) is None


def test_send_bytes_empty_port_routes_to_raw_usb():
    """No configured port (the Linux reality — the Citizen makes no tty
    node): the bytes go over raw USB, the same write the CUPS backend
    does. Nothing touches serial."""
    from factory.hardware import vcom as V
    calls = []
    with _mock.patch.object(V, "citizen_usb_send",
                            side_effect=calls.append):
        V.send_bytes("", b"ZPL-job")
    assert calls == [b"ZPL-job"]


def test_send_bytes_explicit_port_uses_serial():
    """A pinned or USB-ID-found port stays serial VCOM (Windows path):
    opened at the given baud, written, flushed, closed."""
    from factory.hardware import vcom as V
    made = {}

    class FakeSer:
        def __init__(self, port, baudrate=None, timeout=None, write_timeout=None):
            made["port"], made["baud"] = port, baudrate
            made["written"] = []

        def write(self, data):
            made["written"].append(data)

        def flush(self):
            made["flushed"] = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    import serial as serial_mod
    with _mock.patch.object(serial_mod, "Serial", FakeSer):
        V.send_bytes("COM5", b"ZPL-job", baud=19200)
    assert made["port"] == "COM5" and made["baud"] == 19200, made
    assert made["written"] == [b"ZPL-job"], made
    assert made["flushed"] is True, made


def test_printer_configured_usb_fallback():
    """The configured-gate passes on raw-USB presence alone (Linux, no
    serial node to find), and a pinned port wins without any probing."""
    cfg = {"printer_port": ""}
    with _mock.patch("factory.hardware.vcom.find_vcom_port", return_value=None), \
         _mock.patch("factory.hardware.vcom.citizen_usb_present",
                     return_value=True):
        assert DEV.printer_configured(cfg) is True
    with _mock.patch("factory.hardware.vcom.find_vcom_port", return_value=None), \
         _mock.patch("factory.hardware.vcom.citizen_usb_present",
                     return_value=False):
        assert DEV.printer_configured(cfg) is False
    # pinned port: short-circuits before any bus work
    boom = AssertionError("pinned port must not probe")
    with _mock.patch("factory.hardware.vcom.find_vcom_port",
                     side_effect=boom), \
         _mock.patch("factory.hardware.vcom.citizen_usb_present",
                     side_effect=boom):
        assert DEV.printer_configured({"printer_port": "COM5"}) is True


def test_detail_printer_usb_pill():
    """Linux, no serial node anywhere: the pill shows the calm
    'USB · VCOM' ready state from the cached USB probe, and setup only
    when the printer is truly absent. No nagging either way."""
    cfg = _live_cfg()  # blank printer_port
    with _mock.patch.object(HWStatus, "_cached_port_objects", return_value=[]), \
         _mock.patch("factory.hardware.vcom.citizen_usb_present",
                     return_value=True):
        assert HWStatus._detail_printer(cfg) == ("ready", "USB · VCOM")
    with _mock.patch.object(HWStatus, "_cached_port_objects", return_value=[]), \
         _mock.patch("factory.hardware.vcom.citizen_usb_present",
                     return_value=False):
        assert HWStatus._detail_printer(cfg) == ("setup", "printer not connected")


def test_read_scale_real_success_writes_measured():
    p = _fresh_db()
    job = S.create_job("REAL OK JOB", core_wt=0.280, path=p)
    _, pg = _page()
    _select_job(pg, job["id"])

    class GoodScale:
        def unfreeze(self):
            pass

        def read_weight(self):
            return 12.5

        def freeze(self):
            return 12.5

    with _patch_hw(scale=GoodScale()):
        pg.read_scale()
        assert pg.gross.value() == 12.5
    pg.close()


def test_autodetect_concurrent_runs_do_not_clobber():
    """Two strips detecting at once must not lose either device.

    Regression: detect_printer used to re-read and rewrite the whole config, so
    a concurrent scale save could be overwritten back to '' (one device would
    silently forget itself). Both results now land in one read-modify-write.
    """
    import threading
    from factory.hardware import autodetect as Auto
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-race-"), "hardware.json")
    old_json = os.environ.get("SIMPLD_HW_JSON")
    old_s, old_p = HW.scale, HW.printer
    os.environ["SIMPLD_HW_JSON"] = tmp

    class Answering(EssaeDS215):
        def _read_lines(self, dwell=0.8):
            return ["ST,GS,+   2.000kg"] if self.port == "COM6" else []

    errs: list = []

    def run():
        try:
            Auto.detect_all(save=True)
        except Exception as e:          # pragma: no cover
            errs.append(e)

    try:
        # Patches are applied once, here, not per thread: concurrent
        # patch/unpatch from several threads restores the real functions
        # under the others and makes the test flaky.
        with _mock.patch("factory.hardware.essae.list_ports",
                         return_value=["COM6"]), \
             _mock.patch.object(EssaeDS215, "_read_lines",
                                Answering._read_lines):
            threads = []
            for _ in range(4):
                t = threading.Thread(target=run)
                threads.append(t)
                t.start()
            for t in threads:
                t.join(30)
        assert not errs, errs
        cfg = HWC.load(tmp)
        assert cfg["scale_port"] == "COM6", cfg
    finally:
        if old_json is None:
            os.environ.pop("SIMPLD_HW_JSON", None)
        else:
            os.environ["SIMPLD_HW_JSON"] = old_json
        HW.scale, HW.printer = old_s, old_p


if __name__ == "__main__":
    test_parser_supported_frames()
    test_parser_rejects_unstable_and_nonsense()
    test_real_read_returns_fresh_value()
    test_real_read_timeout_errors_not_zero()
    test_real_read_unstable_errors_not_stale()
    test_real_freeze_propagates_error()
    test_real_open_failure_errors()
    test_blank_config_selects_mock()
    test_configured_port_selects_real_scale()
    test_configured_printer_selects_vcom_port()
    test_reload_swaps_shared_instances_without_restart()
    test_no_mock_helpers_blank_config_blocks_weighing()
    test_read_scale_real_error_invalidates_weight_and_action()
    test_genuine_zero_stop_sets_zero_not_stale()
    test_hardware_mode_switch_invalidates_stale_weight()
    test_configured_startup_invalid_reports_no_hidden_mock()
    test_stored_configs_use_stable_data_root()
    test_read_scale_real_success_writes_measured()
    test_autodetect_finds_scale_by_probing_ports()
    test_autodetect_persists_scale()
    test_autodetect_no_answer_stays_unconfigured()
    test_autodetect_forgets_devices_that_stopped_answering()
    test_autodetect_keeps_env_pinned_config_when_nothing_answers()
    test_live_status_ready_when_everything_attached()
    test_live_status_unplugged_scale_reads_not_found()
    test_live_status_printer_config_trusted_no_flip()
    test_live_status_unknowable_keeps_last_verdict()
    test_sysfs_usb_printer_scan()
    test_match_citizen_port_pure_matching()
    test_send_bytes_empty_port_routes_to_raw_usb()
    test_send_bytes_explicit_port_uses_serial()
    test_printer_configured_usb_fallback()
    test_detail_printer_usb_pill()
    test_scanner_attempts_without_landing()
    test_scanner_session_resets_on_logout()
    test_autodetect_concurrent_runs_do_not_clobber()
    print("test_hardware_real PASS")
