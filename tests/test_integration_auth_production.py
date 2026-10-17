"""Integration: real auth + production + dispatch + migrations. Temp DB only.

Covers the round's load-bearing claims with real temp-DB accounts and real
authenticated sessions (never forged role dicts, never no-session
production bypass):
- mutations fail closed without a session; forged actor kwargs are ignored
- production vs dispatch authorization stays distinct
- legacy linked cartons + dispatch_items migrate with exact IDs/weights/
  timestamps/links preserved, FK integrity holds, reruns are idempotent,
  and the same job may issue Roll 1 again next month
- deleted job/order numbers are never reused (monotonic counters)
- partial completion, overloading, unsafe cancel/remove are blocked
- finite weights, valid statuses, delete guards, reweigh/print rules
- production seed creates no sample data
"""
import math
import os
import sqlite3
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory.db import init_db, get_conn
from factory import services as S
from factory import auth as A
from tests._auth_helper import ensure_default_users


@pytest.fixture()
def adb():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-int-"), "factory.db")
    init_db(tmp)
    ensure_default_users(tmp)
    A._SESSIONS.clear()
    A.set_session(None)
    yield tmp
    A._SESSIONS.clear()
    A.set_session(None)


def _login(role, path):
    creds = {"admin": ("admin", "admin123"), "packager": ("packager", "pack123"),
             "dispatcher": ("dispatcher", "dispatch123")}
    u, pw = creds[role]
    out = A.authenticate(u, pw, path)
    assert out is not None
    return out


def _mk_job(path, name="INT JOB", core=0.280):
    return S.create_job(name, size_mm=160, core_wt=core, path=path)


# ---------- auth enforcement ----------
def test_mutations_fail_closed_without_session(adb):
    with pytest.raises(PermissionError):
        S.create_job("NOSESS", core_wt=0.280, path=adb)
    with pytest.raises(PermissionError):
        S.create_product("NOSESS PROD", 16500, path=adb)
    with pytest.raises(PermissionError):
        S.create_dispatch_order("X", 1, 1, path=adb)


def test_forged_actor_dict_is_ignored(adb):
    # A caller-supplied admin dict with NO session must still be denied.
    with pytest.raises(PermissionError):
        S.create_job("FORGED", core_wt=0.280, path=adb,
                     actor={"username": "admin", "role": "admin"})
    with pytest.raises(PermissionError):
        S.create_reel(1, gross=11.0, core=0.280, path=adb,
                      actor={"username": "admin", "role": "admin"})
    with pytest.raises(PermissionError):
        S.scan_into_order(1, "C0001", path=adb,
                          actor={"username": "dispatcher", "role": "dispatcher"})


def test_production_dispatch_authorization_distinct(adb):
    _login("packager", adb)
    j = _mk_job(adb)
    r = S.create_reel(j["id"], gross=11.380, core=0.280, path=adb)
    ct = S.pack_weight_carton(j["id"], 10.5, path=adb)
    S.reweigh_reel(r["id"], 11.500, path=adb)
    S.mark_reel_printed(r["id"], path=adb)
    S.mark_carton_printed(ct["id"], path=adb)
    with pytest.raises(PermissionError):
        S.create_dispatch_order("C", j["id"], 1, path=adb)
    with pytest.raises(PermissionError):
        S.delete_reel(r["id"], path=adb)  # admin-only even for packager
    _login("dispatcher", adb)
    o = S.create_dispatch_order("DISP CO", j["id"], 1, path=adb)
    S.scan_into_order(o["id"], ct["carton_no"], path=adb)
    with pytest.raises(PermissionError):
        S.create_reel(j["id"], gross=11.0, core=0.280, path=adb)
    with pytest.raises(PermissionError):
        S.pack_weight_carton(j["id"], 9.0, path=adb)
    with pytest.raises(PermissionError):
        S.mark_reel_printed(r["id"], path=adb)


def test_unknown_user_fails_closed(adb):
    _login("admin", adb)
    A.create_user("temp1", "temp1pass", "packager", adb)
    vic = A.authenticate("temp1", "temp1pass", adb)
    assert vic is not None
    admin = A.authenticate("admin", "admin123", adb)
    A.set_session(admin["session_token"])
    A.delete_user("temp1", adb)
    A.set_session(vic["session_token"])
    with pytest.raises(PermissionError):
        S.create_reel(1, gross=11.0, core=0.280, path=adb)
    assert A.current_user(adb) is None


# ---------- e2e production + dispatch ----------
def test_e2e_packager_dispatch_exact_count(adb):
    _login("packager", adb)
    prod = S.create_product("E2E PROD", 16500, path=adb)
    j = S.create_job_for_product(prod["id"], path=adb)
    assert j["wrappers_per_reel"] == 16500
    r1 = S.create_reel(j["id"], gross=11.380, core=0.280, path=adb)
    r2 = S.create_reel(j["id"], gross=11.120, core=0.280, path=adb)
    assert r1["roll_code"] != r2["roll_code"]
    box = S.pack_weight_carton(j["id"], 10.5, path=adb)
    assert box["num_reels"] == 0 and box["total_net"] == 10.5
    assert len(S.loose_reels(j["id"], path=adb)) == 2  # boxes independent
    _login("dispatcher", adb)
    o = S.create_dispatch_order("E2E CO", j["id"], 1, path=adb)
    assert o["order_no"] == "DO-0001"
    S.scan_into_order(o["id"], box["carton_no"], path=adb)
    done = S.complete_dispatch(o["id"], path=adb)
    assert done["status"] == "complete" and done["loaded"] == 1
    assert S.ready_stock(j["id"], path=adb) == []
    # completed order is immutable
    with pytest.raises(ValueError):
        S.cancel_order(o["id"], path=adb)
    with pytest.raises(ValueError):
        S.remove_from_order(o["id"], box["id"], path=adb)
    with pytest.raises(ValueError):
        S.complete_dispatch(o["id"], path=adb)


def test_partial_complete_overload_duplicate_mismatch_blocked(adb):
    _login("packager", adb)
    j = _mk_job(adb)
    boxes = [S.pack_weight_carton(j["id"], 10.0 + i * 0.1, path=adb) for i in range(3)]
    _login("dispatcher", adb)
    o = S.create_dispatch_order("PART CO", j["id"], 2, path=adb)
    S.scan_into_order(o["id"], boxes[0]["carton_no"], path=adb)
    with pytest.raises(ValueError, match="Partial load"):
        S.complete_dispatch(o["id"], path=adb)
    with pytest.raises(ValueError, match="duplicate"):
        S.scan_into_order(o["id"], boxes[0]["carton_no"], path=adb)
    S.scan_into_order(o["id"], boxes[1]["carton_no"], path=adb)
    with pytest.raises(ValueError, match="full"):
        S.scan_into_order(o["id"], boxes[2]["carton_no"], path=adb)
    _login("packager", adb)
    j2 = S.create_job("OTHER PROD", core_wt=0.5, path=adb)
    other = S.pack_weight_carton(j2["id"], 9.0, path=adb)
    _login("dispatcher", adb)
    with pytest.raises(ValueError, match="mismatch"):
        S.scan_into_order(o["id"], other["carton_no"], path=adb)
    done = S.complete_dispatch(o["id"], path=adb)
    assert done["loaded"] == 2
    # safe cancel of a loading order returns cartons to Ready
    o2 = S.create_dispatch_order("CANCEL CO", j["id"], 1, path=adb)
    S.scan_into_order(o2["id"], boxes[2]["carton_no"], path=adb)
    S.cancel_order(o2["id"], path=adb)
    assert S.find_carton(boxes[2]["carton_no"], path=adb)["status"] == "Ready"
    with pytest.raises(ValueError):
        S.cancel_order(o2["id"], path=adb)  # already cancelled


def test_finite_weights_valid_statuses(adb):
    _login("packager", adb)
    j = _mk_job(adb)
    for bad in (float("inf"), float("-inf"), float("nan")):
        with pytest.raises(ValueError):
            S.create_reel(j["id"], gross=bad, core=0.280, path=adb)
        with pytest.raises(ValueError):
            S.pack_weight_carton(j["id"], bad, path=adb)
    r = S.create_reel(j["id"], gross=11.0, core=0.280, path=adb)
    for bad in (float("inf"), float("nan")):
        with pytest.raises(ValueError):
            S.reweigh_reel(r["id"], bad, path=adb)
    with pytest.raises(ValueError):
        S.set_job_status(j["id"], "archived", path=adb)
    _login("dispatcher", adb)
    with pytest.raises(ValueError):
        S.create_dispatch_order("C", j["id"], 0, path=adb)


def test_delete_guards_and_id_nonreuse(adb):
    _login("admin", adb)
    j = _mk_job(adb, "DEL JOB")
    n0 = j["job_no"]
    r = S.create_reel(j["id"], gross=11.0, core=0.280, path=adb)
    box = S.pack_weight_carton(j["id"], 9.0, path=adb)
    with pytest.raises(ValueError, match="close/archive"):
        S.delete_job(j["id"], path=adb)
    o = S.create_dispatch_order("DEL CO", j["id"], 1, path=adb)
    seq0 = o["order_no"]
    S.scan_into_order(o["id"], box["carton_no"], path=adb)
    with pytest.raises(ValueError, match="loaded"):
        S.delete_carton(box["id"], path=adb)
    S.cancel_order(o["id"], path=adb)
    S.delete_carton(box["id"], path=adb)  # Ready + unloaded now
    S.delete_reel(r["id"], path=adb)
    # order row still references the job -> job still not deletable
    with pytest.raises(ValueError, match="close/archive"):
        S.delete_job(j["id"], path=adb)
    # empty job deletes, but its number is never reused
    je = S.create_job("EMPTY DEL", core_wt=0.280, path=adb)
    ne = je["job_no"]
    assert ne > n0
    S.delete_job(je["id"], path=adb)
    jn = S.create_job("AFTER DEL", core_wt=0.280, path=adb)
    assert jn["job_no"] > ne, "deleted job numbers must not be reused"
    # audit recorded the mutations
    assert S.list_audit(path=adb), "audit must persist"


def test_reweigh_loose_only_print_invalidated(adb):
    _login("packager", adb)
    j = _mk_job(adb)
    r = S.create_reel(j["id"], gross=11.380, core=0.280, path=adb)
    S.mark_reel_printed(r["id"], path=adb)
    w = S.reweigh_reel(r["id"], 11.900, path=adb)
    assert w["gross"] == 11.900 and w["print_status"] == "pending"
    assert w["roll_no"] == r["roll_no"] and w["roll_code"] == r["roll_code"]
    linked = S.pack_carton(j["id"], [r["id"]], path=adb)
    with pytest.raises(ValueError):
        S.reweigh_reel(r["id"], 12.0, path=adb)
    _login("admin", adb)
    with pytest.raises(ValueError):
        S.delete_reel(r["id"], path=adb)  # packed, not loose
    assert linked["num_reels"] == 1


def test_monthly_numbers_never_reused_after_delete(adb):
    _login("admin", adb)
    j = _mk_job(adb)
    r1 = S.create_reel(j["id"], gross=11.0, core=0.280, path=adb)
    code1, no1 = r1["roll_code"], r1["roll_no"]
    S.delete_reel(r1["id"], path=adb)
    r2 = S.create_reel(j["id"], gross=11.1, core=0.280, path=adb)
    assert (r2["roll_no"], r2["roll_code"]) != (no1, code1)
    b1 = S.pack_weight_carton(j["id"], 9.0, path=adb)
    cn1 = b1["carton_no"]
    S.delete_carton(b1["id"], path=adb)
    b2 = S.pack_weight_carton(j["id"], 9.1, path=adb)
    assert b2["carton_no"] != cn1


# ---------- migration integrity ----------
def _legacy_db_with_links():
    p = os.path.join(tempfile.mkdtemp(prefix="simpld-leg-"), "factory.db")
    c = sqlite3.connect(p)
    c.executescript("""
    CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT, job_no INTEGER UNIQUE NOT NULL,
      job_name TEXT NOT NULL, size_mm REAL DEFAULT 0, core_wt REAL NOT NULL DEFAULT 0.5,
      status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL, closed_at TEXT);
    CREATE TABLE reels (id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
      roll_no INTEGER NOT NULL, gross REAL NOT NULL, core REAL NOT NULL, net REAL NOT NULL,
      shift TEXT NOT NULL DEFAULT 'DAY', supervisor TEXT DEFAULT '', customer TEXT DEFAULT '',
      status TEXT NOT NULL DEFAULT 'loose', created_at TEXT NOT NULL, UNIQUE(job_id, roll_no));
    CREATE TABLE cartons (id INTEGER PRIMARY KEY AUTOINCREMENT, carton_no TEXT UNIQUE NOT NULL,
      job_id INTEGER NOT NULL, bundle_no INTEGER NOT NULL, num_reels INTEGER NOT NULL,
      total_gross REAL NOT NULL, total_core REAL NOT NULL, total_net REAL NOT NULL,
      shift TEXT NOT NULL DEFAULT 'DAY', customer TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'Ready',
      packed_at TEXT NOT NULL, dispatched_at TEXT, UNIQUE(job_id, bundle_no));
    CREATE TABLE carton_items (carton_id INTEGER NOT NULL, reel_id INTEGER UNIQUE NOT NULL,
      PRIMARY KEY (carton_id, reel_id));
    CREATE TABLE dispatch_orders (id INTEGER PRIMARY KEY AUTOINCREMENT, order_no TEXT UNIQUE NOT NULL,
      customer TEXT NOT NULL, job_id INTEGER NOT NULL, required_bundles INTEGER NOT NULL,
      status TEXT NOT NULL DEFAULT 'loading', order_date TEXT NOT NULL, created_at TEXT NOT NULL,
      completed_at TEXT);
    CREATE TABLE dispatch_items (id INTEGER PRIMARY KEY AUTOINCREMENT, order_id INTEGER NOT NULL,
      carton_id INTEGER UNIQUE NOT NULL, scanned_at TEXT NOT NULL, UNIQUE(order_id, carton_id));
    INSERT INTO jobs(id, job_no, job_name, size_mm, core_wt, status, created_at)
      VALUES(7, 4169, 'LEGACY LINK JOB', 160, 0.280, 'open', '2026-03-04 10:00:00');
    INSERT INTO reels(id, job_id, roll_no, gross, core, net, shift, status, created_at)
      VALUES(11, 7, 1, 11.380, 0.280, 11.100, 'DAY', 'packed', '2026-03-04 10:01:00');
    INSERT INTO reels(id, job_id, roll_no, gross, core, net, shift, status, created_at)
      VALUES(12, 7, 2, 11.120, 0.280, 10.840, 'DAY', 'packed', '2026-03-04 10:02:00');
    INSERT INTO cartons(id, carton_no, job_id, bundle_no, num_reels, total_gross, total_core,
      total_net, shift, status, packed_at) VALUES(21, 'C0001', 7, 1, 2, 22.500, 0.560,
      21.940, 'DAY', 'Ready', '2026-03-04 11:00:00');
    INSERT INTO carton_items(carton_id, reel_id) VALUES(21, 11);
    INSERT INTO carton_items(carton_id, reel_id) VALUES(21, 12);
    INSERT INTO dispatch_orders(id, order_no, customer, job_id, required_bundles, status,
      order_date, created_at) VALUES(31, 'DO-0001', 'PARLE', 7, 2, 'loading',
      '2026-03-04', '2026-03-04 12:00:00');
    INSERT INTO dispatch_items(id, order_id, carton_id, scanned_at)
      VALUES(41, 31, 21, '2026-03-04 12:05:00');
    """)
    c.commit()
    c.close()
    return p


def _snapshot_links(p):
    c = get_conn(p)
    try:
        return {
            "jobs": [dict(r) for r in c.execute("SELECT * FROM jobs").fetchall()],
            "reels": [dict(r) for r in c.execute("SELECT * FROM reels").fetchall()],
            "cartons": [dict(r) for r in c.execute("SELECT * FROM cartons").fetchall()],
            "items": [dict(r) for r in c.execute("SELECT * FROM carton_items").fetchall()],
            "orders": [dict(r) for r in c.execute("SELECT * FROM dispatch_orders").fetchall()],
            "ditems": [dict(r) for r in c.execute("SELECT * FROM dispatch_items").fetchall()],
            "fk": c.execute("PRAGMA foreign_key_check").fetchall(),
        }
    finally:
        c.close()


def test_legacy_linked_migration_preserves_everything():
    p = _legacy_db_with_links()
    before = _snapshot_links(p)
    assert before["fk"] == []
    init_db(p)  # migrate
    after = _snapshot_links(p)
    assert after["fk"] == [], "migration must leave FK integrity clean"
    # exact IDs preserved
    assert [r["id"] for r in after["reels"]] == [11, 12]
    assert [r["id"] for r in after["cartons"]] == [21]
    assert [r["id"] for r in after["orders"]] == [31]
    assert [r["id"] for r in after["ditems"]] == [41]
    assert after["jobs"][0]["id"] == 7 and after["jobs"][0]["job_no"] == 4169
    # exact weights + timestamps + links preserved
    by_id = {r["id"]: r for r in after["reels"]}
    assert (by_id[11]["gross"], by_id[11]["core"], by_id[11]["net"]) == (11.380, 0.280, 11.100)
    assert by_id[11]["created_at"] == "2026-03-04 10:01:00"
    assert by_id[11]["roll_no"] == 1 and by_id[11]["roll_period"] == "2026-03"
    assert after["cartons"][0]["carton_no"] == "C0001"
    assert after["cartons"][0]["total_gross"] == 22.500
    assert after["cartons"][0]["packed_at"] == "2026-03-04 11:00:00"
    assert sorted((x["carton_id"], x["reel_id"]) for x in after["items"]) == [(21, 11), (21, 12)]
    assert (after["ditems"][0]["order_id"], after["ditems"][0]["carton_id"]) == (31, 21)
    assert after["ditems"][0]["scanned_at"] == "2026-03-04 12:05:00"
    # repeated migration is a no-op (idempotent, same rows)
    init_db(p)
    again = _snapshot_links(p)
    assert [r["id"] for r in again["reels"]] == [11, 12]
    assert again["fk"] == []
    # legacy monthly detector: same job CANNOT reuse roll_no 1 in the same
    # month (UNIQUE job/period/no), but CAN issue Roll 1 next month.
    ensure_default_users(p)
    A.authenticate("admin", "admin123", p)
    with pytest.raises(Exception):
        c = get_conn(p)
        try:
            c.execute("INSERT INTO reels(job_id, roll_no, roll_period, gross, core, net,"
                      " shift, status, created_at) VALUES(7, 1, '2026-03', 11.0, 0.28, 10.72,"
                      " 'DAY', 'loose', '2026-03-05 10:00:00')")
            c.commit()
        finally:
            c.close()
    nxt = S.create_reel(7, gross=11.200, core=0.280, path=p, period="2026-04")
    assert nxt["roll_no"] == 1 and nxt["roll_period"] == "2026-04"
    # counters adopted legacy MAX: same-month allocation continues past 2
    same = S.create_reel(7, gross=11.300, core=0.280, path=p, period="2026-03")
    assert same["roll_no"] > 2


def test_production_seed_creates_no_sample_data(adb):
    from factory.seed import seed
    seed(adb)
    assert S.list_jobs(path=adb) == []
    assert S.ready_stock(path=adb) == []
    assert S.list_orders(path=adb) == []
