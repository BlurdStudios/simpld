"""LAN packing-slip preview/print/PDF from the server snapshot (no local DB).

Offscreen Qt + isolated HTTPS packager host (temp DB, local TLS cert).
The PackingSlipDialog fetches the authoritative slip once via
data_access.slip_data(job_id) (LAN get_slip op) and passes slip= to ALL
preview/save/print renderers in local AND LAN mode — no server image op,
no local SQLite read. Printer hardware is never touched: print tests fake
the office print call only.

Never touches production data/factory.db or the project dev server.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from unittest import mock as _mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SIMPLD_ALLOW_MOCK", "1")

from tests._auth_helper import ensure_default_users

import pytest


@pytest.fixture(scope="module")
def tls_pair():
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-lanslip-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-lanslip-test",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    yield cert, key
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def lan(tls_pair):
    from factory.db import init_db
    from factory import auth as A
    from factory import lan_server as LS
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-lanslip-db-")
    dbp = os.path.join(d, "factory.db")
    init_db(dbp)
    ensure_default_users(dbp)
    A._SESSIONS.clear()
    A.set_session(None)
    httpd = LS.create_server(dbp, "127.0.0.1", 0, cert, key)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    try:
        yield {"db": dbp, "port": port, "ca": cert, "dir": d}
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=10)
        A._SESSIONS.clear()
        A.set_session(None)
        from factory import data_access as DA
        try:
            DA.configure_local()
        except Exception:
            pass
        try:
            DA.logout()
        except Exception:
            pass
        shutil.rmtree(d, ignore_errors=True)


def _qt_app():
    from PySide6.QtWidgets import QApplication
    from factory.ui.styles import APP_QSS
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(APP_QSS)
    return app


def _seed_job_via_lan():
    """Packager seeds one job: 2 reels + 2 independent weighed boxes."""
    from factory import data_access as DA
    prod = DA.create_product("LANSLIP WIDGET-160 BODY", 16500)
    job = DA.create_job_for_product(prod["id"], core_wt=0.28)
    r1 = DA.create_reel(job["id"], 11.0, core=0.28)
    r2 = DA.create_reel(job["id"], 12.5, core=0.28)
    b1 = DA.pack_weight_carton(job["id"], 21.5)
    b2 = DA.pack_weight_carton(job["id"], 9.25)
    return job, [r1, r2], [b1, b2]


def _dialog_preview_pixmaps(dlg):
    from PySide6.QtWidgets import QLabel
    out = []
    for ql in dlg.findChildren(QLabel):
        try:
            px = ql.pixmap()
        except Exception:
            px = None
        if px is not None and not px.isNull():
            out.append(px)
    return out


def _pdf_text(path):
    """Text of a generated PDF for the evidence assertions.

    Prefers pdftotext (an independent tool), with Qt's own PDF engine as
    a second reader when it extracts nothing. Returns "" when neither can
    read the file; the caller then fails loudly instead of skipping the
    check.
    """
    if shutil.which("pdftotext") is not None:
        out = subprocess.run(["pdftotext", "-layout", path, "-"],
                             capture_output=True, text=True, timeout=60)
        assert out.returncode == 0, f"pdftotext failed: {out.stderr[:200]}"
        if out.stdout.strip():
            return out.stdout
    from PySide6.QtPdf import QPdfDocument
    doc = QPdfDocument()
    if doc.load(path) != QPdfDocument.Error.None_:
        return ""
    parts = []
    for i in range(doc.pageCount()):
        sel = doc.getAllText(i)          # QPdfSelection (Qt >= 6.4)
        parts.append(sel.text() if hasattr(sel, "text") else str(sel))
    return "\n".join(parts)


def test_lan_slip_preview_uses_server_snapshot_no_local_db(lan):
    """Offscreen LAN dialog: snapshot from server, preview+PDF, no local file."""
    from factory import data_access as DA
    from factory import db as DB
    from factory import slip_pdf as SP
    DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
    probe_dir = tempfile.mkdtemp(prefix="simpld-lanslip-local-")
    sentinel = os.path.join(probe_dir, "factory.db")
    old_path = DB.DB_PATH
    DB.DB_PATH = sentinel
    try:
        DA.authenticate("packager", "pack123")
        job, reels, boxes = _seed_job_via_lan()
        job_id = job["id"]
        server_slip = DA.slip_data(job_id)
        assert len(server_slip["reel_rows"]) == 2, server_slip["totals"]
        assert len(server_slip["box_rows"]) == 2, server_slip["totals"]
        DA.logout()
        DA.authenticate("dispatcher", "dispatch123")
        _qt_app()
        with _mock.patch("PySide6.QtWidgets.QMessageBox.warning") as _w:
            from factory.ui.label_preview import PackingSlipDialog
            dlg = PackingSlipDialog(None, job_id)
            assert _w.call_count == 0, "no error popup on healthy LAN slip"
        try:
            assert dlg._slip is not None
            assert dlg._slip["totals"] == server_slip["totals"]
            # Full evidence unchanged: separate reel rows + independent
            # boxes, month-qualified roll identities, distinct totals.
            assert len(dlg._slip["reel_rows"]) == 2
            assert len(dlg._slip["box_rows"]) == 2
            for r in dlg._slip["reel_rows"]:
                assert r.get("roll_label"), r
            # Same renderer both modes: prefetched slip -> images, with a
            # bogus local path proving no SQLite read.
            bogus = "/nonexistent-lanslip/factory.db"
            pages = SP.paginate(dlg._slip)
            _slip2, imgs = SP.preview_images(job_id, bogus, slip=dlg._slip)
            assert len(imgs) == len(pages) >= 1
            assert all(not im.isNull() for im in imgs)
            # Dialog's own A4 preview shows the same rendered pages.
            got = _dialog_preview_pixmaps(dlg)
            assert len(got) == len(pages), (len(got), len(pages))
            # Actual PDF save from the prefetched slip: every reel identity
            # and every box number present, totals separate.
            out = os.path.join(tempfile.mkdtemp(prefix="simpld-lanslip-"),
                               "lan_slip.pdf")
            SP.save_pdf(job_id, out, bogus, slip=dlg._slip)
            assert os.path.getsize(out) > 0
            txt = _pdf_text(out)
            assert txt.strip(), \
                "no PDF text extractor (pdftotext/QtPdf) could read the file"
            for r in dlg._slip["reel_rows"]:
                assert r["roll_label"] in txt, r["roll_label"]
            for b in boxes:
                assert b["carton_no"] in txt, b["carton_no"]
            assert "Reel totals" in txt and "Box totals" in txt
            assert "Legacy box totals" in txt
            assert "Total bundles/cartons" in txt
            assert txt.count("Page Nos.:") == len(pages)
            assert not os.path.exists(sentinel), \
                "LAN mode must not create a local DB file"
        finally:
            dlg.close()
            DA.logout()
    finally:
        DB.DB_PATH = old_path
        DA.configure_local()
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_lan_slip_disconnect_no_local_fallback_and_relogin(lan):
    """Dead host -> connection error shown, no local data; relogin recovers."""
    from factory import data_access as DA
    from factory import db as DB
    from factory.lan_client import LanConnectionError
    DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
    old_path = DB.DB_PATH
    probe_dir = tempfile.mkdtemp(prefix="simpld-lanslip-disc-")
    sentinel = os.path.join(probe_dir, "factory.db")
    DB.DB_PATH = sentinel
    try:
        DA.authenticate("packager", "pack123")
        job, _, _ = _seed_job_via_lan()
        job_id = job["id"]
        DA.logout()
        # Point at a dead port: authoritative fetch must fail loudly.
        shut = socket.socket()
        shut.bind(("127.0.0.1", 0))
        dead = shut.getsockname()[1]
        shut.close()
        DA.configure_lan("127.0.0.1", dead, lan["ca"], timeout=2.0)
        # Login itself hits the dead host: real connection failure, and any
        # slip fetch without a session refuses (both surface, no fallback).
        with pytest.raises(LanConnectionError):
            DA.authenticate("dispatcher", "dispatch123")
        _qt_app()
        with _mock.patch("PySide6.QtWidgets.QMessageBox.warning") as w:
            from factory.ui.label_preview import PackingSlipDialog
            dlg = PackingSlipDialog(None, job_id)
            assert w.call_count >= 1, "disconnect must surface a warning"
            assert dlg._slip is None, "no local fallback slip on disconnect"
        try:
            dlg.close()
        except Exception:
            pass
        assert not os.path.exists(sentinel)
        # Relogin on the live host recovers with no restart.
        DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
        DA.authenticate("dispatcher", "dispatch123")
        with _mock.patch("PySide6.QtWidgets.QMessageBox.warning") as w2:
            dlg2 = PackingSlipDialog(None, job_id)
            assert w2.call_count == 0
            assert dlg2._slip is not None
            assert len(dlg2._slip["rows"]) == 4  # 2 reels + 2 boxes
        dlg2.close()
        DA.logout()
    finally:
        DB.DB_PATH = old_path
        DA.configure_local()
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_slip_print_uses_prefetched_slip_and_surfaces_errors(lan):
    """Fake office print only: dialog passes slip=; errors shown, never
    reported printed. No real printer/hardware touched."""
    from factory import data_access as DA
    DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
    try:
        DA.authenticate("packager", "pack123")
        job, _, _ = _seed_job_via_lan()
        DA.logout()
        DA.authenticate("dispatcher", "dispatch123")
        _qt_app()
        from factory import slip_pdf as SP
        from factory.ui.label_preview import PackingSlipDialog
        with _mock.patch("PySide6.QtWidgets.QMessageBox.warning"):
            dlg = PackingSlipDialog(None, job["id"])
        try:
            assert dlg._slip is not None
            # Ensure a selectable printer row in the test (no hardware).
            dlg.printer_combo.setEnabled(True)
            if dlg.printer_combo.count() == 0:
                dlg.printer_combo.addItem("TEST-OFFICE-PRINTER")
            else:
                dlg.printer_combo.clear()
                dlg.printer_combo.addItem("TEST-OFFICE-PRINTER")
            seen = {}

            def fake_print(parent, job_id, printer_name=None, db_path=None,
                           slip=None):
                seen["job_id"] = job_id
                seen["printer"] = printer_name
                seen["slip"] = slip
                seen["db_path"] = db_path
                assert slip is not None, "dialog must pass prefetched slip="
                assert slip["totals"] == dlg._slip["totals"]
                return True

            with _mock.patch.object(SP, "print_slip", side_effect=fake_print), \
                 _mock.patch("PySide6.QtWidgets.QMessageBox.warning") as w, \
                 _mock.patch("factory.ui.toast.toast", lambda *a, **k: None):
                dlg.do_print()
                assert w.call_count == 0, "accepted print shows no error"
            assert seen.get("job_id") == job["id"]
            assert seen.get("slip") is dlg._slip

            def failing_print(*a, **k):
                raise RuntimeError("printer jam on TEST-OFFICE-PRINTER")

            with _mock.patch.object(SP, "print_slip",
                                    side_effect=failing_print), \
                 _mock.patch("PySide6.QtWidgets.QMessageBox.warning") as w2:
                dlg.do_print()
                assert w2.call_count >= 1, "printer error must be displayed"
                assert "Could not print" in str(w2.call_args), w2.call_args
        finally:
            dlg.close()
            DA.logout()
    finally:
        DA.configure_local()
