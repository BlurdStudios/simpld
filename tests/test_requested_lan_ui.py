"""Requested LAN-UI checks over an isolated HTTPS fixture (no prod server).

Spins a temp-DB packager host on 127.0.0.1:0 with a locally generated TLS
cert (openssl CLI), then drives the REAL Qt packaging/history reads through
factory.data_access in LAN mode. Never touches production data/factory.db
or the project dev server. Physical hardware NOT verified (no devices).
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("SIMPLD_ALLOW_MOCK", "1")

from tests._auth_helper import ensure_default_users

import pytest


@pytest.fixture(scope="module")
def tls_pair():
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-req-lanui-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-lanui-test",
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
    d = tempfile.mkdtemp(prefix="simpld-req-lanui-db-")
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


def test_qt_remote_startup_lists_server_jobs_no_local_db(lan):
    """Qt packaging page starts against the remote host; no local DB file."""
    from factory import data_access as DA
    from factory.db import DB_PATH as _prod_default
    probe_dir = tempfile.mkdtemp(prefix="simpld-req-lanui-local-")
    sentinel = os.path.join(probe_dir, "factory.db")
    assert not os.path.exists(sentinel)
    from factory import db as DB
    old_path = DB.DB_PATH
    DB.DB_PATH = sentinel
    try:
        DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
        # packager seeds one product+job+box on the host via the facade
        DA.authenticate("packager", "pack123")
        prod = DA.create_product("LANUI WIDGET-160 BODY", 16500)
        job = DA.create_job_for_product(prod["id"], core_wt=0.28)
        DA.pack_weight_carton(job["id"], 21.5)
        DA.logout()
        # dispatcher Qt startup reads the SAME host state, not a local file
        DA.authenticate("dispatcher", "dispatch123")
        assert DA.current_user() == {"username": "dispatcher", "role": "dispatcher"}
        _qt_app()
        from factory.ui.page_packaging import PackagingPage
        pg = PackagingPage()
        try:
            assert pg.job_combo.count() >= 1, "remote jobs must populate the dropdown"
            found = [pg.job_combo.itemText(i) for i in range(pg.job_combo.count())]
            assert any(str(job["job_no"]) in t for t in found), found
            pg.refresh_history()
            assert not os.path.exists(sentinel), "LAN mode must not create a local DB"
        finally:
            pg.close()
            DA.logout()
    finally:
        DB.DB_PATH = old_path
        DA.configure_local()
        shutil.rmtree(probe_dir, ignore_errors=True)


def test_dispatcher_refresh_over_lan_reads_shared_box(lan):
    from factory import data_access as DA
    DA.configure_lan("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
    try:
        DA.authenticate("packager", "pack123")
        prod = DA.create_product("LANUI SHARED", 15000)
        job = DA.create_job_for_product(prod["id"])
        box = DA.pack_weight_carton(job["id"], 12.0)
        DA.logout()
        DA.authenticate("dispatcher", "dispatch123")
        stock = DA.ready_stock()
        assert any(s["carton_no"] == box["carton_no"] for s in stock)
        hist = DA.production_history()
        assert any(h.get("box") == box["carton_no"] for h in hist)
        _qt_app()
        from factory.ui.pages_misc import HistoryPage
        hp = HistoryPage()
        try:
            hp.tabs.setCurrentIndex(0)
            hp.search.setText("")
            hp.refresh()
            assert hp.table.rowCount() >= 1
            kinds = {hp.table.item(rw, 0).text() for rw in range(hp.table.rowCount())}
            assert "Box" in kinds, kinds
        finally:
            hp.close()
    finally:
        try:
            DA.logout()
        except Exception:
            pass
        DA.configure_local()


def test_lan_disconnect_surfaces_connection_no_local_fallback(lan):
    from factory import data_access as DA
    from factory.lan_client import LanConnectionError
    shut = socket.socket()
    shut.bind(("127.0.0.1", 0))
    free_port = shut.getsockname()[1]
    shut.close()
    DA.configure_lan("127.0.0.1", free_port, lan["ca"], timeout=2.0)
    try:
        with pytest.raises(LanConnectionError):
            DA.authenticate("packager", "pack123")
    finally:
        DA.configure_local()
