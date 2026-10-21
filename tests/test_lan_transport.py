"""Isolated LAN transport tests: temp DB + in-test HTTPS server only.

Never touches the project dev server or production data/factory.db. Uses
real TLS (locally generated cert via the openssl CLI), real SQLite temp DBs,
and real auth/services/slip code paths on the server side. Fixture-only use
of the auth test helper's ensure_default_users bootstraps the temp DB; the
server itself must
still refuse uninitialized DBs (covered below).
"""
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import urllib.request

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import auth as A
from factory import lan_client as LC
from factory import lan_server as LS
from factory.db import init_db
from tests._auth_helper import ensure_default_users


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tls_pair():
    """Local cert+key for 127.0.0.1, generated with the real openssl CLI."""
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-lan-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-lan-test",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    assert os.path.getsize(cert) > 0 and os.path.getsize(key) > 0
    yield cert, key
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def lan(tls_pair):
    """Temp DB + HTTPS LAN server on 127.0.0.1:0 in a background thread."""
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-lan-db-")
    dbp = os.path.join(d, "factory.db")
    init_db(dbp)
    ensure_default_users(dbp)  # fixture-only bootstrap of the temp DB
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
        shutil.rmtree(d, ignore_errors=True)


def _client(lan, user="packager", pw="pack123"):
    c = LC.LanClient("127.0.0.1", lan["port"], lan["ca"], timeout=10.0)
    c.login(user, pw)
    return c


def _raw(lan, payload, token=None):
    """Raw HTTPS POST helper (for forging/malformed cases the client hides)."""
    ctx = ssl.create_default_context(cafile=lan["ca"])
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    req = urllib.request.Request(
        f"https://127.0.0.1:{lan['port']}/api", data=data, method="POST",
        headers={"Content-Type": "application/json"})
    if token:
        req.add_header("X-Session-Token", token)
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode())
        except Exception:  # noqa: BLE001 - surface unexpected transport faults
            return exc.code, {}


# --------------------------------------------------------------------------
# auth against the real DB
# --------------------------------------------------------------------------

def test_login_real_db_and_bad_password_denied(lan):
    c = LC.LanClient("127.0.0.1", lan["port"], lan["ca"])
    who = c.login("packager", "pack123")
    assert who == {"username": "packager", "role": "packager"}
    assert c.logged_in and c.token
    c.logout()
    assert not c.logged_in
    with pytest.raises(LC.LanAuthError):
        c.login("packager", "wrong-password")
    with pytest.raises(LC.LanAuthError):
        c.login("nosuchuser", "whatever")
    with pytest.raises(LC.LanAuthError):
        LC.LanClient("127.0.0.1", lan["port"], lan["ca"]).list_jobs()


def test_plaintext_http_is_not_served(lan):
    """Credentials must never travel plaintext: plain HTTP gets no API."""
    req = urllib.request.Request(
        f"http://127.0.0.1:{lan['port']}/api",
        data=json.dumps({"op": "login",
                         "args": {"username": "x", "password": "y"}}).encode(),
        method="POST", headers={"Content-Type": "application/json"})
    with pytest.raises(Exception):
        urllib.request.urlopen(req, timeout=10)


def test_logout_invalidates_token(lan):
    c = _client(lan)
    assert c.list_products() == []
    tok = c.token
    c.logout()
    status, _ = _raw(lan, {"op": "list_products", "args": {}}, token=tok)
    assert status == 401
    with pytest.raises(LC.LanAuthError):
        c.list_products()


# --------------------------------------------------------------------------
# shared packager/dispatcher flow on one host DB
# --------------------------------------------------------------------------

def test_two_clients_share_job_and_box(lan):
    pack = _client(lan, "packager", "pack123")
    disp = _client(lan, "dispatcher", "dispatch123")

    prod = pack.create_product("LAN WIDGET-160 BODY", 16500)
    job = pack.create_job_for_product(prod["id"], core_wt=0.28)
    assert job["job_no"] >= 4169 and job["product_name"] == "LAN WIDGET-160 BODY"
    reel = pack.create_reel(job["id"], gross=11.38, core=0.28, shift="DAY",
                            supervisor="RAMESH", request_id="reel-0001")
    assert reel["net"] == pytest.approx(11.38 - 0.28)
    box = pack.pack_weight_carton(job["id"], gross_weight=22.5, shift="DAY",
                                  request_id="box-0001")
    assert box["status"] == "Ready"

    # Dispatcher sees the same host DB state and can read it.
    stock = disp.ready_stock()
    assert any(s["carton_no"] == box["carton_no"] for s in stock)
    assert disp.get_job(job["job_no"])["job_name"] == job["job_name"]
    assert any(h["box"] == box["carton_no"]
               for h in disp.production_history(limit=50))

    # Dispatcher runs the dispatch side on the packager's data.
    order = disp.create_dispatch_order("PARLE AGRO HYD", job["id"],
                                       required_bundles=1,
                                       request_id="order-0001")
    assert order["order_no"].startswith("DO-")
    got = disp.scan_into_order(order["id"], box["carton_no"])
    assert got["carton_no"] == box["carton_no"]
    done = disp.complete_dispatch(order["id"])
    assert done["status"] == "complete"
    assert not any(s["carton_no"] == box["carton_no"]
                   for s in disp.ready_stock())

    # Packager cannot see dispatch reports (report.read is dispatcher/admin).
    with pytest.raises(LC.LanPermissionError):
        pack.order_progress(order["id"])
    # Slip evidence is readable by both floor roles.
    slip = disp.get_slip(job["id"])
    assert slip["job_no"] == job["job_no"]
    assert slip["totals"]["boxes"] >= 1
    slip2 = pack.get_slip(job["id"])
    assert slip2["totals"] == slip["totals"]


def test_dispatch_slip_saved_printed_and_reprinted_over_lan(lan):
    pack = _client(lan, "packager", "pack123")
    disp = _client(lan, "dispatcher", "dispatch123")

    prod = pack.create_product("LAN SLIP WIDGET BODY", 16500)
    job = pack.create_job_for_product(prod["id"], core_wt=0.28)
    reel = pack.create_reel(job["id"], gross=11.38, core=0.28, shift="DAY",
                            supervisor="RAMESH")
    box = pack.pack_carton(job["id"], [reel["id"]], shift="DAY")
    order = disp.create_dispatch_order("PARLE AGRO HYD", job["id"],
                                       required_bundles=1,
                                       customer_po="PO-LAN")
    disp.scan_into_order(order["id"], box["carton_no"])

    # Completion freezes the slip server-side and returns it in-band.
    done = disp.complete_dispatch(order["id"])
    assert done["status"] == "complete"
    assert done["slip"]["order_no"] == order["order_no"]
    assert done["slip"]["customer_po"] == "PO-LAN"
    assert len(done["slip"]["rows"]) == 1

    # Reprint reads the frozen copy — identical bytes.
    again = disp.dispatch_slip(order["id"])
    assert again["frozen"] is True
    assert again["rows"] == done["slip"]["rows"]

    # Printing marks the saved record (dispatcher role, dispatch.mutate).
    assert disp.mark_dispatch_slip_printed(order["id"])["print_count"] == 1

    # Packager has no report.read: the saved slip stays dispatcher/admin.
    with pytest.raises(LC.LanPermissionError):
        pack.dispatch_slip(order["id"])


def test_role_boundaries_enforced_server_side(lan):
    pack = _client(lan, "packager", "pack123")
    disp = _client(lan, "dispatcher", "dispatch123")
    prod = pack.create_product("BOUNDARY WIDGET", 16500)
    job = pack.create_job_for_product(prod["id"])

    # Dispatcher is read-only on production.
    with pytest.raises(LC.LanPermissionError):
        disp.create_reel(job["id"], gross=10.0, core=0.28)
    with pytest.raises(LC.LanPermissionError):
        disp.pack_weight_carton(job["id"], gross_weight=20.0)
    with pytest.raises(LC.LanPermissionError):
        disp.create_product("NOPE", 100)
    # Packager may not mutate dispatch.
    with pytest.raises(LC.LanPermissionError):
        pack.create_dispatch_order("X", job["id"], 1)
    # Summary is admin-only.
    with pytest.raises(LC.LanPermissionError):
        disp.daily_summary()
    with pytest.raises(LC.LanPermissionError):
        pack.daily_summary()
    admin = _client(lan, "admin", "admin123")
    assert admin.daily_summary()["day"]


def test_no_role_forging(lan):
    """Client-supplied actor/role/path/period args are rejected, never honored."""
    pack = _client(lan, "packager", "pack123")
    for forged in ({"role": "admin"}, {"actor": {"role": "admin"}},
                   {"path": "/tmp/evil.db"}, {"db": "x"}, {"period": "1999-01"},
                   {"token": "x"}, {"session_token": "x"}):
        args = {"job_id": 1, "gross": 10.0, "core": 0.28}
        args.update(forged)
        status, msg = _raw(lan, {"op": "create_reel", "args": args},
                           token=pack.token)
        assert status == 422, (forged, msg)
        assert msg.get("ok") is False
    # Forged session token is denied.
    status, _ = _raw(lan, {"op": "list_products", "args": {}},
                     token="forged-token-never-issued")
    assert status == 401
    # Unknown op is not dispatched (no getattr-style RPC).
    status, _ = _raw(lan, {"op": "__class__", "args": {}}, token=pack.token)
    assert status == 422
    status, _ = _raw(lan, {"op": "drop_table", "args": {}}, token=pack.token)
    assert status == 422


def test_malformed_and_injection_args_safe(lan):
    pack = _client(lan, "packager", "pack123")
    # Non-object / missing shapes rejected.
    for bad in ({"op": "list_products", "args": [1, 2]},
                {"args": {}},
                "not-json-at-all",
                {"op": "create_product",
                 "args": {"name": "X", "wrappers_per_reel": "lots"}}):
        status, _ = _raw(lan, bad, token=pack.token)
        assert status == 422, bad
    # Missing args defaults to {} for arg-less ops.
    status, msg = _raw(lan, {"op": "list_products"}, token=pack.token)
    assert status == 200 and msg.get("ok") is True
    # SQL metacharacters are stored literally via parameters, tables intact.
    evil = "'; DROP TABLE jobs;--"
    job = pack.create_job(evil)
    assert job["job_name"] == evil.upper()
    assert isinstance(pack.list_jobs(), list)
    # Path-looking names are inert data (never touch the filesystem).
    probe = pack.create_job("../../etc/pwned")
    assert probe["job_name"] == "../../ETC/PWNED".upper()
    # Bool is not accepted where an id is required (no True==1 confusion).
    status, _ = _raw(lan, {"op": "get_product", "args": {"product_id": True}},
                     token=pack.token)
    assert status == 422
    # request_id is validated; a valid one is echoed, never dedup-claimed.
    status, msg = _raw(
        lan, {"op": "list_products", "args": {}, "request_id": "trace-01"},
        token=pack.token)
    assert status == 200 and msg.get("request_id") == "trace-01"
    status, _ = _raw(
        lan, {"op": "list_products", "args": {}, "request_id": "bad id!"},
        token=pack.token)
    assert status == 422


# --------------------------------------------------------------------------
# offline behaviour: clear connection error, never a local DB fallback
# --------------------------------------------------------------------------

def test_disconnect_raises_connection_error_no_local_writes(lan):
    c = _client(lan)
    assert c.health()["ok"] is True
    dead = LC.LanClient("127.0.0.1", lan["port"], lan["ca"], timeout=2.0)
    # Point at a closed port on loopback: must be a clear connection error.
    shut = socket.socket()
    shut.bind(("127.0.0.1", 0))
    free_port = shut.getsockname()[1]
    shut.close()
    offline = LC.LanClient("127.0.0.1", free_port, lan["ca"], timeout=2.0)
    with pytest.raises(LC.LanConnectionError):
        offline.health()
    with pytest.raises(LC.LanConnectionError):
        offline.login("packager", "pack123")
    # The client module has no local-DB fallback surface at all.
    assert "factory.db" not in dir(dead)
    assert not hasattr(dead, "db_path")
    import factory.lan_client as LCM
    src = open(LCM.__file__).read()
    assert "get_conn" not in src and "sqlite3" not in src
    assert "SIMPLD_DB" not in src and "REELPACK_DB" not in src
    assert not any(hasattr(dead, n) for n in
                   ("local_fallback", "fallback", "get_conn", "db_path"))


# --------------------------------------------------------------------------
# concurrency: two roles on one host must not leak sessions into each other
# --------------------------------------------------------------------------

def test_concurrent_roles_stay_isolated(lan):
    pack = _client(lan, "packager", "pack123")
    disp = _client(lan, "dispatcher", "dispatch123")
    prod = pack.create_product("RACE WIDGET", 16500)
    job = pack.create_job_for_product(prod["id"])
    box = pack.pack_weight_carton(job["id"], gross_weight=10.0)
    errors: list = []
    barrier = threading.Barrier(3)

    def packager_loop():
        try:
            barrier.wait(timeout=10)
            for _ in range(15):
                assert pack.job_stats(job["id"])["ready_cartons"] >= 1
                assert pack.whoami() == {"username": "packager",
                                         "role": "packager"}
        except Exception as exc:  # noqa: BLE001
            errors.append(("packager", repr(exc)))

    def dispatcher_loop():
        try:
            barrier.wait(timeout=10)
            for _ in range(15):
                assert disp.whoami() == {"username": "dispatcher",
                                         "role": "dispatcher"}
                assert any(s["carton_no"] == box["carton_no"]
                           for s in disp.ready_stock())
                with pytest.raises(LC.LanPermissionError):
                    disp.create_reel(job["id"], gross=9.0, core=0.28)
        except Exception as exc:  # noqa: BLE001
            errors.append(("dispatcher", repr(exc)))

    t1 = threading.Thread(target=packager_loop)
    t2 = threading.Thread(target=dispatcher_loop)
    t1.start()
    t2.start()
    barrier.wait(timeout=10)
    t1.join(timeout=60)
    t2.join(timeout=60)
    assert not t1.is_alive() and not t2.is_alive()
    assert errors == [], errors


# --------------------------------------------------------------------------
# server refuses uninitialized DBs (main app must set up admin first)
# --------------------------------------------------------------------------

def test_server_refuses_uninitialized_db(tls_pair):
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-lan-empty-")
    try:
        missing = os.path.join(d, "nope.db")
        with pytest.raises(RuntimeError):
            LS.create_server(missing, "127.0.0.1", 0, cert, key)
        empty = os.path.join(d, "empty.db")
        init_db(empty)
        with pytest.raises(RuntimeError):
            LS.create_server(empty, "127.0.0.1", 0, cert, key)
        with pytest.raises(RuntimeError):
            LS.create_server(empty, "127.0.0.1", 0, "/none/cert.pem", key)
    finally:
        shutil.rmtree(d, ignore_errors=True)
