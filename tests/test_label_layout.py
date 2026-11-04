"""Label Layout Editor: geometry, validation, profiles, 1:1 print.

Acceptance tests for the build brief (sections 6-8, 10-12).
"""
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory.hardware import layout as L


def _base(tid="reel"):
    return dict(L.DEFAULTS[tid], template_id=tid)


# 1. Reset margins -> 94x44 content -------------------------------------------
def test_reset_margins_gives_94x44_and_dot_readout():
    lay = L.reset_margins(_base("reel"), 3.0)
    assert (lay["content_x_mm"], lay["content_y_mm"]) == (3.0, 3.0)
    assert (lay["content_w_mm"], lay["content_h_mm"]) == (94.0, 44.0)
    cw, ch = L.content_px(lay)
    assert (cw, ch) == (L.mm_to_dots(94.0, 203), L.mm_to_dots(44.0, 203))
    m = L.margins_of(lay)
    assert (m["left"], m["top"], m["right"], m["bottom"]) == (3.0, 3.0, 3.0, 3.0)


# 2. Linked margins ------------------------------------------------------------
def test_linked_edit_sets_all_four_and_stays_ok():
    lay = L.reset_margins(_base("reel"), 3.0)
    lay["margin_locked"] = True
    lay = L.set_margins(lay, 5.0, 5.0, 5.0, 5.0, True)
    m = L.margins_of(lay)
    assert (m["left"], m["top"], m["right"], m["bottom"]) == (5.0, 5.0, 5.0, 5.0)
    printable = {"origin_x_mm": 2.5, "origin_y_mm": 0.0,
                 "print_w_mm": 96.1, "print_h_mm": 50.0}
    assert L.status_token(lay, printable) == "OK"


# 3. Short-by deficit blocks Save ---------------------------------------------
def test_short_margin_reports_deficit_and_wont_fit():
    lay = L.set_margins(_base("reel"), 2.0, 3.0, 3.0, 3.0, False)
    printable = {"origin_x_mm": 2.5, "origin_y_mm": 0.0,
                 "print_w_mm": 96.1, "print_h_mm": 50.0}
    issues = L.fit_report(lay, printable)
    assert issues == ["left short by 0.50 mm"], issues
    assert L.status_token(lay, printable) == "WON'T FIT"
    assert any(v["severity"] == "error" for v in L.validate(lay, printable))


# 4. Test pattern matches the status readout -----------------------------------
def test_test_pattern_matches_content_box():
    lay = L.reset_margins(_base("reel"), 3.0)
    art = L.render_test_pattern(lay)
    cw, ch = L.content_px(lay)
    assert art.size == (max(40, cw), max(40, ch)), art.size
    line = L.status_line(lay, None)
    assert "94.0x44.0mm" in line and f"{cw}x{ch}px" in line


# 5. DPI change keeps mm, changes dots ------------------------------------------
def test_dpi_change_keeps_mm_proportions():
    lay = L.reset_margins(_base("reel"), 3.0)
    before = (lay["content_w_mm"], lay["content_h_mm"])
    lay300 = dict(lay, dpi=300)
    assert (lay300["content_w_mm"], lay300["content_h_mm"]) == before
    assert L.content_px(lay300) != L.content_px(lay)
    assert L.content_px(lay300) == (L.mm_to_dots(94.0, 300),
                                    L.mm_to_dots(44.0, 300))


# 6. Export / wipe / import round-trip ------------------------------------------
def test_profile_export_import_round_trip():
    lay = L.set_margins(_base("carton"), 4.0, 2.5, 4.0, 2.5, False)
    printable = {"origin_x_mm": 2.5, "origin_y_mm": 0.0,
                 "print_w_mm": 96.1, "print_h_mm": 50.0, "dpi": 203,
                 "ticket_delta_mm": 0.0, "probed_at": ""}
    data = L.export_profile("carton", lay, printable)
    text = json.dumps(data)
    back = L.parse_profile_text(text)
    tid, lay2, prob2 = L.import_profile(back)
    assert tid == "carton"
    for k in ("stock_w_mm", "stock_h_mm", "dpi", "content_x_mm",
              "content_y_mm", "content_w_mm", "content_h_mm"):
        assert lay2[k] == lay[k], k
    assert prob2 == printable


def test_profile_rejects_garbage():
    for bad in ("not json", json.dumps({"kind": "nope"}),
                json.dumps({"kind": "simpld-label-profile",
                            "template_id": "reel", "layout": {}})):
        try:
            d = L.parse_profile_text(bad) if bad.startswith("{") else \
                L.parse_profile_text(bad)
            L.import_profile(d)
        except ValueError:
            continue
        raise SystemExit(f"FAIL: bad profile accepted: {bad[:40]}")


def test_shipped_bundle_validates():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    p = os.path.join(base, "factory", "hardware", "profiles",
                     "citizen-cl-s621.json")
    with open(p, encoding="utf-8") as f:
        data = L.parse_profile_text(f.read())
    assert len(L.bundle_profiles(data)) == 3
    assert "reel" in L.bundle_summary(data)


# 7. Print logs scale=1.0, never the scaling branch ------------------------------
def test_print_log_line_is_11_and_unscaled(caplog):
    import logging
    from unittest import mock as _mock
    from PySide6 import QtPrintSupport as PS
    from PySide6 import QtGui as G
    from PySide6.QtCore import QRect
    from factory.hardware.qtprint import QtCitizenCLS621

    st: dict = {}

    class FakePrinter:
        HighResolution = 2
        DevicePixel = 0
        instances: list = []

        def __init__(self, mode):
            FakePrinter.instances.append(self)

        def setPrinterName(self, n): pass

        def setDocName(self, n): pass

        def setFullPage(self, on): pass

        def setResolution(self, dpi): pass

        def setPageLayout(self, layout):
            st["layout"] = layout

        def pageLayout(self):
            return st["layout"]

        def pageRect(self, unit):
            return QRect(0, 0, 799, 399)

        def resolution(self):
            return 203

    class FakePainter:
        def __init__(self):
            st["painter"] = self
            self.drawn = []

        def begin(self, printer):
            return True

        def drawImage(self, rect, img):
            self.drawn.append((rect, img))

        def end(self):
            return True

    job = {"job_no": 1, "job_name": "J"}
    reel = {"gross": 1.0, "core": 0.1, "net": 0.9,
            "created_at": "2026-01-01 10:00:00", "roll_no": 1}
    with _mock.patch.object(PS, "QPrinter", FakePrinter), \
         _mock.patch.object(G, "QPainter", FakePainter), \
         _mock.patch("factory.hardware.qtprint.printer_exists",
                      lambda n: True):
        with caplog.at_level(logging.INFO, logger="simpld.hardware"):
            assert QtCitizenCLS621("P").print_reel_label(
                {"job": job, "reel": reel}) is True
    geom = [r for r in caplog.records
            if "print geom:" in (r.getMessage() or "")]
    assert geom, "print must log the geometry line"
    msg = geom[-1].getMessage()
    assert "scale=1.0" in msg and "off=24,24" in msg, msg
    assert "scaling art to printable" not in caplog.text


# 8. Wrong driver page still prints (repaired/proceed) -----------------------------
def test_killed_driver_page_still_prints(caplog):
    import logging
    from unittest import mock as _mock
    from PySide6 import QtPrintSupport as PS
    from PySide6 import QtGui as G
    from PySide6.QtCore import QRect
    from factory.hardware.qtprint import QtCitizenCLS621

    st: dict = {}

    class WrongFormPrinter:
        HighResolution = 2
        DevicePixel = 0

        def __init__(self, mode): pass

        def setPrinterName(self, n): pass

        def setDocName(self, n): pass

        def setFullPage(self, on): pass

        def setResolution(self, dpi): pass

        def setPageLayout(self, layout):
            st["layout"] = layout

        def pageLayout(self):
            # operator set the driver to 4x6in: ticket never matches stock
            from PySide6.QtGui import QPageLayout as _PL, QPageSize as _PS
            from PySide6.QtCore import QSizeF, QMarginsF
            return _PL(_PS(QSizeF(101.6, 152.4), _PS.Millimeter),
                       _PL.Portrait, QMarginsF(0, 0, 0, 0))

        def pageRect(self, unit):
            return QRect(0, 0, 799, 399)

        def resolution(self):
            return 203

    class FakePainter:
        def __init__(self):
            st["painter"] = self
            self.drawn = []

        def begin(self, printer):
            return True

        def drawImage(self, rect, img):
            self.drawn.append((rect, img))

        def end(self):
            st["ended"] = True
            return True

    job = {"job_no": 1, "job_name": "J"}
    reel = {"gross": 1.0, "core": 0.1, "net": 0.9,
            "created_at": "2026-01-01 10:00:00", "roll_no": 1}
    with _mock.patch.object(PS, "QPrinter", WrongFormPrinter), \
         _mock.patch.object(G, "QPainter", FakePainter), \
         _mock.patch("factory.hardware.qtprint.printer_exists",
                      lambda n: True):
        with caplog.at_level(logging.INFO, logger="simpld.hardware"):
            assert QtCitizenCLS621("P").print_reel_label(
                {"job": job, "reel": reel}) is True
    assert st.get("ended") is True
    assert any(s in caplog.text for s in ("state=repaired", "state=proceed")), \
        caplog.text


if __name__ == "__main__":
    test_reset_margins_gives_94x44_and_dot_readout()
    test_linked_edit_sets_all_four_and_stays_ok()
    test_short_margin_reports_deficit_and_wont_fit()
    test_test_pattern_matches_content_box()
    test_dpi_change_keeps_mm_proportions()
    test_profile_export_import_round_trip()
    test_profile_rejects_garbage()
    test_shipped_bundle_validates()
    print("layout model tests OK")
