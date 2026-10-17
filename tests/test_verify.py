"""Scanner re-verification: QR -> batch + box details. Temp DB only."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as DB
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-verify-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


def _seed(p):
    job = S.create_job("VERIFY JOB", size_mm=160, core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    ct = S.pack_carton(job["id"], [r1["id"], r2["id"]], path=p)
    return job, ct


def test_verify_unknown_returns_none():
    p = _fresh_db()
    assert S.carton_verify("C9999", path=p) is None
    assert S.carton_verify("   ", path=p) is None


def test_verify_ready_carton_has_no_order():
    p = _fresh_db()
    job, ct = _seed(p)
    for scan in (ct["carton_no"], f"VASAVI:{ct['carton_no']}", f"  vasavi:{ct['carton_no'].lower()} "):
        found = S.carton_verify(scan, path=p)
        assert found is not None, scan
        d = found["detail"]
        assert d["carton_no"] == ct["carton_no"]
        assert d["job_no"] == job["job_no"] and d["num_reels"] == 2
        assert [r["roll_no"] for r in d["reels"]] == [1, 2]
        assert d["qr"] == f"VASAVI:{ct['carton_no']}"
        assert found["order"] is None


def test_verify_dispatched_carton_links_order():
    p = _fresh_db()
    job, ct = _seed(p)
    o = S.create_dispatch_order("VERIFY CUSTOMER", job["id"], 1, path=p)
    S.scan_into_order(o["id"], ct["carton_no"], path=p)
    S.complete_dispatch(o["id"], path=p)
    found = S.carton_verify(ct["carton_no"], path=p)
    assert found["detail"]["status"] == "Dispatched"
    assert found["order"]["order_no"] == o["order_no"]
    assert found["order"]["customer"] == "VERIFY CUSTOMER"


def test_stock_verify_scan_opens_details():
    from unittest import mock as _mock
    from PySide6.QtWidgets import QApplication, QMessageBox
    from factory.ui.styles import APP_QSS
    _fresh_db()
    job, ct = _seed(DB.DB_PATH)
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    from factory.ui.page_stock import StockPage
    pg = StockPage()
    # unknown code -> warning, focus kept for the next scan
    with _mock.patch("factory.ui.page_stock.QMessageBox.warning") as w:
        pg.verify_scan.setText("C9999")
        pg.verify()
        assert w.call_count == 1
    # known code -> detail dialog with batch + box + status
    with _mock.patch("factory.ui.page_stock.VerifyDialog") as dlg:
        pg.verify_scan.setText(ct["carton_no"])
        pg.verify()
        assert dlg.call_count == 1, "scan must open the verify dialog"
        found = dlg.call_args[0][1]
        assert found["detail"]["carton_no"] == ct["carton_no"]
        assert found["detail"]["job_no"] == job["job_no"]
    assert QMessageBox is not None  # keep import used (warning path above)


def test_verify_dialog_shows_order_when_dispatched():
    from PySide6.QtWidgets import QApplication, QTextEdit
    p = _fresh_db()
    job, ct = _seed(p)
    o = S.create_dispatch_order("DLG CUSTOMER", job["id"], 1, path=p)
    S.scan_into_order(o["id"], ct["carton_no"], path=p)
    S.complete_dispatch(o["id"], path=p)
    app = QApplication.instance() or QApplication([])
    from factory.ui.label_preview import VerifyDialog
    from factory.ui.styles import APP_QSS
    app.setStyleSheet(APP_QSS)
    found = S.carton_verify(ct["carton_no"], path=p)
    d = VerifyDialog(None, found)
    assert ct["carton_no"] in d.windowTitle()
    assert o["order_no"] in d.findChildren(QTextEdit)[0].toPlainText()


if __name__ == "__main__":
    test_verify_unknown_returns_none()
    test_verify_ready_carton_has_no_order()
    test_verify_dispatched_carton_links_order()
    test_stock_verify_scan_opens_details()
    test_verify_dialog_shows_order_when_dispatched()
    print("test_verify PASS")
