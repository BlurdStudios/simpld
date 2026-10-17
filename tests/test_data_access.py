"""data_access facade tests: real temp DBs + real temporary HTTPS servers only.

Never touches the project dev DB or production data. Local mode runs against
a temp SQLite file (factory.db.DB_PATH monkeypatched); LAN mode runs against
a second temp DB served by a real TLS server in a background thread. Fixture
bootstrap via the auth test helper's ensure_default_users is test-only. No fake production
backend anywhere: every record comes from real services/server code.
"""
import os
import inspect
import shutil
import socket
import subprocess
import sys
import tempfile
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import auth as A
from factory import data_access as DA
from factory import lan_client as LC
from factory import lan_server as LS
from factory import db as DB
from factory.db import init_db
from tests._auth_helper import ensure_default_users


REPO_DEFAULT_DB = os.path.abspath(DB.DEFAULT_DB)


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tls_pair():
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-da-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-da-test",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    yield cert, key
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def local_db(monkeypatch):
    """Temp local DB as the facade's default DB. Production guard included."""
    d = tempfile.mkdtemp(prefix="simpld-da-local-")
    dbp = os.path.join(d, "factory.db")
    assert os.path.abspath(dbp) != REPO_DEFAULT_DB
    monkeypatch.setattr(DB, "DB_PATH", dbp)
    init_db(dbp)
    ensure_default_users(dbp)  # fixture-only bootstrap of the temp DB
    A._SESSIONS.clear()
    A.set_session(None)
    DA.configure_local()
    try:
        yield dbp
    finally:
        DA.logout()
        DA.configure_local()
        A._SESSIONS.clear()
        A.set_session(None)
        shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def lan_srv(tls_pair):
    """Temp host DB + real HTTPS server. Fixture bootstrap is test-only."""
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-da-lan-")
    dbp = os.path.join(d, "factory.db")
    assert os.path.abspath(dbp) != REPO_DEFAULT_DB
    init_db(dbp)
    ensure_default_users(dbp)  # fixture-only bootstrap of the temp host DB
    A._SESSIONS.clear()
    A.set_session(None)
    httpd = LS.create_server(dbp, "127.0.0.1", 0, cert, key)
    port = httpd.server_address[1]
    th = threading.Thread(target=httpd.serve_forever, daemon=True)
    th.start()
    DA.configure_lan("127.0.0.1", port, cert, timeout=10.0)
    try:
        yield {"db": dbp, "port": port, "ca": cert, "dir": d}
    finally:
        httpd.shutdown()
        httpd.server_close()
        th.join(timeout=10)
        DA.logout()
        DA.configure_local()
        A._SESSIONS.clear()
        A.set_session(None)
        shutil.rmtree(d, ignore_errors=True)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _row_count(dbp, table) -> int:
    import sqlite3
    c = sqlite3.connect(dbp)
    try:
        return int(c.execute(f"SELECT COUNT(*) n FROM {table}").fetchone()[0])
    finally:
        c.close()


# --------------------------------------------------------------------------
# local mode: real services + real auth on the temp DB
# --------------------------------------------------------------------------

def test_local_auth_and_crud(local_db):
    assert DA.backend() == "local"
    assert DA.authenticate("packager", "pack123") == {
        "username": "packager", "role": "packager"}
    assert DA.current_user() == {"username": "packager", "role": "packager"}

    prod = DA.create_product("DA WIDGET-160 BODY", "16500")  # UI passes text
    assert prod["wrappers_per_reel"] == 16500
    assert DA.get_product(prod["id"])["name"] == "DA WIDGET-160 BODY"
    job = DA.create_job_for_product(prod["id"], core_wt=0.28)
    assert job["job_no"] >= 4169
    assert DA.get_job_by_no(job["job_no"])["id"] == job["id"]
    assert DA.get_job_by_id(job["id"])["job_no"] == job["job_no"]
    assert DA.get_job_by_id(999999) is None

    reel = DA.create_reel(job["id"], gross=11.38, core=0.28)
    assert reel["net"] == pytest.approx(11.38 - 0.28)
    assert any(r["id"] == reel["id"] for r in DA.loose_reels(job["id"]))
    assert any(r["id"] == reel["id"] for r in DA.job_reels(job["id"]))
    box = DA.pack_weight_carton(job["id"], gross_weight=22.5)
    assert box["status"] == "Ready"
    assert any(s["carton_no"] == box["carton_no"] for s in DA.ready_stock())
    detail = DA.carton_detail(box["id"])
    assert detail["qr"] == DA.qr_payload(box["carton_no"])

    slip = DA.slip_data(job["id"])
    assert slip["job_no"] == job["job_no"]
    assert slip["totals"]["boxes"] >= 1
    assert isinstance(DA.predict_next_reel_seq(), int)
    assert isinstance(DA.predict_next_box_seq(), int)
    assert DA.job_stats(job["id"])["ready_cartons"] >= 1

    DA.logout()
    assert DA.current_user() is None


def test_local_exact_failures(local_db):
    assert DA.authenticate("packager", "wrong-password") is None
    assert DA.current_user() is None
    DA.authenticate("dispatcher", "dispatch123")
    with pytest.raises(PermissionError):
        DA.create_reel(1, gross=10.0, core=0.28)
    with pytest.raises(ValueError):
        DA.slip_data(999999)
    with pytest.raises(ValueError):
        DA.create_product("BAD W", "lots")
    DA.logout()


def test_local_user_management_admin_only(local_db):
    DA.authenticate("admin", "admin123")
    users = DA.list_users()
    assert {u["username"] for u in users} >= {"admin", "packager", "dispatcher"}
    DA.create_user("daworker", "w1234", "packager")
    DA.set_password("daworker", "n5678")
    DA.set_role("daworker", "dispatcher")
    DA.delete_user("daworker")
    DA.logout()
    DA.authenticate("packager", "pack123")
    with pytest.raises(PermissionError):
        DA.list_users()


# --------------------------------------------------------------------------
# LAN mode: two real clients sharing one host DB through the facade
# --------------------------------------------------------------------------

def test_lan_two_clients_share_host_db(lan_srv):
    assert DA.backend() == "lan"
    # Second real client alongside the facade (packager vs dispatcher).
    other = LC.LanClient("127.0.0.1", lan_srv["port"], lan_srv["ca"])
    other.login("dispatcher", "dispatch123")

    DA.authenticate("packager", "pack123")
    assert DA.current_user() == {"username": "packager", "role": "packager"}
    prod = DA.create_product("DA LAN WIDGET", 16500)
    job = DA.create_job_for_product(prod["id"], core_wt=0.28)
    reel = DA.create_reel(job["id"], gross=11.38, core=0.28)
    assert reel["net"] == pytest.approx(11.10)
    box = DA.pack_weight_carton(job["id"], gross_weight=22.5)
    assert DA.get_job_by_id(job["id"])["job_no"] == job["job_no"]

    # Dispatcher client sees the same host DB state.
    assert any(s["carton_no"] == box["carton_no"] for s in other.ready_stock())
    order = other.create_dispatch_order("PARLE AGRO HYD", job["id"],
                                        required_bundles=1)
    other.scan_into_order(order["id"], box["carton_no"])
    done = other.complete_dispatch(order["id"])
    assert done["status"] == "complete"

    # Facade re-signed as dispatcher reads dispatch state on the host DB.
    DA.authenticate("dispatcher", "dispatch123")
    prog = DA.order_progress(order["id"])
    assert prog["loaded"] == 1 and prog["remaining"] == 0
    slip = DA.slip_data(job["id"])
    assert slip["totals"]["boxes"] >= 1
    other.logout()
    DA.logout()
    assert DA.current_user() is None


def test_lan_exact_failures(lan_srv):
    with pytest.raises(LC.LanAuthError):
        DA.authenticate("packager", "wrong-password")
    DA.authenticate("dispatcher", "dispatch123")
    prod_done = LC.LanClient("127.0.0.1", lan_srv["port"], lan_srv["ca"])
    prod_done.login("packager", "pack123")
    prod = prod_done.create_product("DA FAIL W", 100)
    job = prod_done.create_job_for_product(prod["id"])
    prod_done.logout()
    with pytest.raises(LC.LanPermissionError):
        DA.create_reel(job["id"], gross=10.0, core=0.28)
    with pytest.raises(LC.LanValidationError):
        DA.slip_data(999999)
    with pytest.raises(LC.LanPermissionError):
        DA.daily_summary()  # dispatcher may not read admin summary
    DA.logout()
    with pytest.raises(DA.NotConfiguredError):
        DA.list_jobs()  # signed out: no silent fallback, controlled error


def test_lan_user_management_unsupported(lan_srv):
    DA.authenticate("admin", "admin123")
    with pytest.raises(DA.RemoteUnsupportedError, match="local-admin only"):
        DA.list_users()
    with pytest.raises(DA.RemoteUnsupportedError):
        DA.create_user("x", "yyyy", "packager")
    with pytest.raises(DA.RemoteUnsupportedError):
        DA.set_password("packager", "yyyy")
    with pytest.raises(DA.RemoteUnsupportedError):
        DA.set_role("packager", "dispatcher")
    with pytest.raises(DA.RemoteUnsupportedError):
        DA.delete_user("packager")
    DA.logout()


def test_lan_disconnect_never_writes_local(local_db, tls_pair):
    """Offline LAN ops raise LanConnectionError; the local temp DB is
    untouched and the backend stays lan (no fallback)."""
    cert, _key = tls_pair
    before = {t: _row_count(local_db, t)
              for t in ("products", "jobs", "reels", "cartons")}
    DA.configure_lan("127.0.0.1", _free_port(), cert, timeout=2.0)
    with pytest.raises(LC.LanConnectionError):
        DA.authenticate("packager", "pack123")
    with pytest.raises(DA.NotConfiguredError):
        DA.list_jobs()
    # A stale-client path also surfaces LanConnectionError, never local data.
    DA.configure_lan("127.0.0.1", _free_port(), cert, timeout=2.0)
    cli = LC.LanClient("127.0.0.1", _free_port(), cert, timeout=2.0)
    import factory.data_access as _DA
    with _DA._LOCK:
        _DA._CLIENT = cli  # signed-in shape without a server is impossible;
        # an unsigned client raises LanAuthError (still never a local write)
    with pytest.raises(LC.LanError):
        DA.list_jobs()
    assert DA.backend() == "lan"
    after = {t: _row_count(local_db, t)
             for t in ("products", "jobs", "reels", "cartons")}
    assert after == before
    DA.configure_local()


def test_no_plaintext_token_or_local_bootstrap_in_remote_path(lan_srv):
    import ast as _ast
    import factory.data_access as _DA
    src = inspect.getsource(_DA)
    assert "ensure_default_users" not in src
    assert "init_db" not in src
    assert "sqlite3" not in src
    # No module-level local-DB imports: db/services are lazily imported
    # inside local branches only, so the remote path cannot touch them.
    tree = _ast.parse(src)
    top_imports = []
    for node in tree.body:
        if isinstance(node, (_ast.Import, _ast.ImportFrom)):
            top_imports.append(_ast.unparse(node))
    assert not any("factory.db" in s or "factory.services" in s
                   or "sqlite3" in s for s in top_imports), top_imports
    # Token lives only in the client instance memory, never on disk/env.
    DA.authenticate("packager", "pack123")
    assert "SIMPLD_TOKEN" not in os.environ
    assert _DA._CLIENT.token and len(_DA._CLIENT.token) >= 32
    DA.logout()


def test_pure_helpers_and_env_config(monkeypatch):
    assert DA.parse_scan("vasavi:C0007") == "C0007"
    assert DA.parse_scan("C0007") == "C0007"
    assert DA.qr_payload("C0007") == "VASAVI:C0007"
    with pytest.raises(ValueError):
        DA.configure_lan("", 8443, "ca")
    with pytest.raises(ValueError):
        DA.configure_lan("h", 0, "ca")
    with pytest.raises(ValueError):
        DA.configure_lan("h", 8443, "")
    monkeypatch.delenv("SIMPLD_SERVER_HOST", raising=False)
    assert DA.configure_from_env() == "local"
    monkeypatch.setenv("SIMPLD_SERVER_HOST", "192.168.1.10")
    monkeypatch.setenv("SIMPLD_SERVER_PORT", "8443")
    monkeypatch.setenv("SIMPLD_SERVER_CA_CERT", "/tmp/ca.pem")
    assert DA.configure_from_env() == "lan"
    assert DA.backend() == "lan"
    DA.configure_local()
