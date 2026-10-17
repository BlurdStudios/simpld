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
import tempfile
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from factory.hardware import mock as HW
from factory.hardware import devices as DEV
from factory.hardware import config as HWC
from factory.hardware.essae import EssaeDS215, parse_weight_line
from factory.hardware.qtprint import QtCitizenCLS621
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
    assert isinstance(
        DEV.build_printer({"printer_name": ""}), HW.MockCitizenCLS621)
    assert DEV.mode_text({"scale_port": "", "printer_name": ""}).startswith(
        "Demo/mock")


def test_configured_port_selects_real_scale():
    d = DEV.build_scale({"scale_port": "COM3", "scale_baud": 9600})
    assert isinstance(d, EssaeDS215) and d.port == "COM3" and d.baud == 9600


def test_configured_printer_selects_real_driver():
    p = DEV.build_printer({"printer_name": "Citizen CL-S621"})
    assert isinstance(p, QtCitizenCLS621)
    assert p.printer_name == "Citizen CL-S621"


def test_reload_swaps_shared_instances_without_restart():
    old_s, old_p = HW.scale, HW.printer
    try:
        DEV.reload_hardware({"scale_port": "COM3", "scale_baud": 9600,
                             "printer_name": "Citizen CL-S621"})
        assert isinstance(HW.scale, EssaeDS215)
        assert isinstance(HW.printer, QtCitizenCLS621)
        assert "COM3" in DEV.mode_text(
            {"scale_port": "COM3", "printer_name": "Citizen CL-S621"})
        DEV.reload_hardware({"scale_port": "", "scale_baud": 9600,
                             "printer_name": ""})
        assert isinstance(HW.scale, HW.MockEssaeDS215)
        assert isinstance(HW.printer, HW.MockCitizenCLS621)
    finally:
        HW.scale, HW.printer = old_s, old_p


# ----- Qt printing with fakes: render + painter verified, no real jobs -----
def _fake_qt(monkey_state, begin_ok=True):
    from PySide6 import QtPrintSupport as PS
    from PySide6 import QtGui as G
    from PySide6.QtCore import QRect

    class FakePrinter:
        HighResolution = 2
        DevicePixel = 0
        instances = []

        def __init__(self, mode):
            self.calls = []
            FakePrinter.instances.append(self)

        def setPrinterName(self, n):
            self.calls.append(("name", n))

        def setDocName(self, n):
            self.calls.append(("doc", n))

        def setFullPage(self, on):
            self.calls.append(("fullpage", on))

        def setResolution(self, dpi):
            self.calls.append(("resolution", dpi))

        def setPageLayout(self, layout):
            self.calls.append(("layout", layout))
            monkey_state["layout"] = layout

        def pageLayout(self):
            return monkey_state["layout"]

        def pageRect(self, unit):
            return QRect(0, 0, 812, 609)  # 4x3in @203dpi full-bleed

        def resolution(self):
            return 203

    class FakePainter:
        def __init__(self):
            monkey_state["painter"] = self
            self.drawn = []

        def begin(self, printer):
            monkey_state["printer"] = printer
            return begin_ok

        def drawImage(self, rect, img):
            self.drawn.append((rect, img))

        def end(self):
            monkey_state["ended"] = True
            return True

    monkey_state["FakePrinter"] = FakePrinter
    p1 = _mock.patch.object(PS, "QPrinter", FakePrinter)
    p2 = _mock.patch.object(G, "QPainter", FakePainter)
    p3 = _mock.patch("factory.hardware.qtprint.printer_exists",
                     lambda name: True)
    return p1, p2, p3


def _job_reel_detail():
    job = {"job_no": 4169, "job_name": "QT JOB"}
    reel = {"gross": 11.38, "core": 0.28, "net": 11.10,
            "created_at": "2026-10-10 10:00:00", "roll_no": 1}
    detail = {"job_no": 4169, "job_name": "QT JOB", "carton_no": "C0007",
              "bundle_no": 1, "packed_at": "2026-10-10 10:00:00",
              "num_reels": 0, "total_gross": 10.5, "total_core": 0,
              "total_net": 10.5, "reels": [], "qr": "VASAVI:C0007"}
    return job, reel, detail


def test_real_print_renders_complete_data_and_paints_stock():
    from PySide6 import QtGui as G
    from factory.hardware import labels as L
    st = {}
    job, reel, detail = _job_reel_detail()
    rendered = {}
    real_reel, real_carton = L.render_reel, L.render_carton
    def spy_reel(j, r, **k):
        rendered["reel"] = (j, r)
        return real_reel(j, r, **k)

    def spy_carton(d, **k):
        rendered["carton"] = d
        return real_carton(d, **k)

    p1, p2, p3 = _fake_qt(st)
    with p1, p2, p3, \
            _mock.patch.object(L, "render_reel", spy_reel), \
            _mock.patch.object(L, "render_carton", spy_carton):
        assert QtCitizenCLS621("Citizen").print_reel_label(
            {"job": job, "reel": reel}) is True
        assert rendered["reel"] == (job, reel), "must render complete job+reel"
        assert QtCitizenCLS621("Citizen").print_carton_label(
            {"detail": detail}) is True
        assert rendered["carton"] == detail, "must render complete detail"
    # fixed 101.6x76.2mm @203dpi, zero margins, painter begin/end honored
    lay = st["layout"]
    from PySide6.QtGui import QPageLayout as _PL
    assert lay.orientation() == _PL.Portrait, \
        "Qt rotates custom sizes: Portrait keeps 101.6-wide stock as-is"
    try:
        full = lay.fullRect(_PL.Millimeter)
        assert abs(full.width() - 101.6) < 1.0, full
        assert abs(full.height() - 76.2) < 1.0, full
    except AssertionError:
        raise
    except Exception:
        pass
    ps = lay.pageSize()
    try:
        size_mm = ps.size(G.QPageSize.Millimeter)
        assert abs(size_mm.width() - 101.6) < 0.5, size_mm
        assert abs(size_mm.height() - 76.2) < 0.5, size_mm
    except Exception:
        pts = ps.sizePoints()
        assert abs(pts.width() - 288.0) < 1.0, pts
        assert abs(pts.height() - 216.0) < 1.0, pts
    try:
        margins = lay.margins()
    except TypeError:
        margins = lay.margins(G.QPageLayout.Millimeter)
    assert margins.left() == 0 and margins.top() == 0, margins
    assert margins.right() == 0 and margins.bottom() == 0, margins
    assert st.get("ended") is True
    assert st["painter"].drawn, "label image must be painted"
    rect = st["painter"].drawn[-1][0]
    assert (rect.x(), rect.y()) == (0, 0)
    assert abs(rect.width() - 812) < 3 and abs(rect.height() - 609) < 3
    calls = st["FakePrinter"].instances[-1].calls
    assert ("fullpage", True) in calls, "must request full-page printing"
    assert ("resolution", 203) in calls, "must request 203dpi"


def test_real_print_failures_never_report_success():
    st = {}
    job, reel, _detail = _job_reel_detail()
    # painter refused
    p1, p2, p3 = _fake_qt(st, begin_ok=False)
    with p1, p2, p3:
        try:
            QtCitizenCLS621("Citizen").print_reel_label({"job": job, "reel": reel})
        except RuntimeError:
            pass
        else:
            raise SystemExit("FAIL: refused painter must raise")
    # unknown installed printer
    with _mock.patch("factory.hardware.qtprint.printer_exists", lambda n: False):
        try:
            QtCitizenCLS621("Ghost").print_carton_label({"detail": _detail})
        except RuntimeError as e:
            assert "not found" in str(e)
        else:
            raise SystemExit("FAIL: missing printer must raise")
    # truncated payloads rejected, not printed as fabricated labels
    q1, q2, q3 = _fake_qt({})
    with q1, q2, q3:
        for bad in ({"job_no": 1}, {"carton_no": "C1"}, {}, {"detail": {"qr": "x"}}):
            try:
                QtCitizenCLS621("Citizen").print_reel_label(bad)
                QtCitizenCLS621("Citizen").print_carton_label(bad)
            except (ValueError, RuntimeError):
                pass
            else:
                raise SystemExit(f"FAIL: truncated payload accepted: {bad}")


# ----- UI: mock toggle, Read/Stop errors, mode indication -----
def test_no_mock_helpers_blank_config_blocks_weighing():
    """Production has no fake-weight/test-data helpers; blank hardware
    config blocks weighing/printing instead of fabricating records."""
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
                  "label_height_mm": 100, "label_dpi": 203})
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


def test_autodetect_persists_scale_and_printer():
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
                               Answering._read_lines), \
             _mock.patch("factory.hardware.qtprint.available_printers",
                         return_value=["Citizen CL-S621II",
                                       "Microsoft Print to PDF"]):
            result = Auto.detect_all(save=True)
        assert result["scale"] and result["scale"]["port"] == "COM4", result
        assert result["printer"] == "Citizen CL-S621II", result
        assert HWC.load(tmp)["scale_port"] == "COM4"
        assert HWC.load(tmp)["printer_name"] == "Citizen CL-S621II"
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
             _mock.patch.object(EssaeDS215, "_read_lines", Silent._read_lines), \
             _mock.patch("factory.hardware.qtprint.available_printers",
                         return_value=["Microsoft Print to PDF"]):
            result = Auto.detect_all(save=True)
        assert result["scale"] is None, result
        assert result["printer"] is None, "non-Citizen printer must not be picked"
        assert HWC.load(tmp)["scale_port"] == ""
        assert HWC.load(tmp)["printer_name"] == ""
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
            with _mock.patch("factory.hardware.essae.list_ports",
                             return_value=["COM6"]), \
                 _mock.patch.object(EssaeDS215, "_read_lines",
                                   Answering._read_lines), \
                 _mock.patch("factory.hardware.qtprint.available_printers",
                             return_value=["Citizen CL-S621II"]):
                Auto.detect_all(save=True)
        except Exception as e:          # pragma: no cover
            errs.append(e)

    try:
        with _mock.patch("factory.hardware.essae.list_ports",
                         return_value=["COM6"]), \
             _mock.patch("factory.hardware.qtprint.available_printers",
                         return_value=["Citizen CL-S621II"]):
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
        assert cfg["printer_name"] == "Citizen CL-S621II", cfg
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
    test_configured_printer_selects_real_driver()
    test_reload_swaps_shared_instances_without_restart()
    test_real_print_renders_complete_data_and_paints_stock()
    test_real_print_failures_never_report_success()
    test_no_mock_helpers_blank_config_blocks_weighing()
    test_read_scale_real_error_invalidates_weight_and_action()
    test_genuine_zero_stop_sets_zero_not_stale()
    test_hardware_mode_switch_invalidates_stale_weight()
    test_configured_startup_invalid_reports_no_hidden_mock()
    test_stored_configs_use_stable_data_root()
    test_read_scale_real_success_writes_measured()
    test_autodetect_finds_scale_by_probing_ports()
    test_autodetect_persists_scale_and_printer()
    test_autodetect_no_answer_stays_unconfigured()
    test_autodetect_concurrent_runs_do_not_clobber()
    print("test_hardware_real PASS")
