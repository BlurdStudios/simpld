"""Monthly global reel/box invariants (unit, temp SQLite only).

Covers (parent GPT6.1 requirements, real APIs only):
- monthly GLOBAL reel + box numbering across jobs
- month/year reset within the same job (period override = test-only clock)
- number non-reuse after deletion (counters only move forward)
- bounded concurrent writes (6 threads, counters + inserts unique)
- rollback: failed writes leave no partial record and do not consume numbers
- audit rows + delete downstream guards (packed/dispatched/loaded blocked)
- print -> reweigh invalidation (keeps number/created_at, clears print state)
- legacy migration: carton_items + dispatched-order fixture survives init_db
  with exact ID/weight/link identity; repeated init_db is idempotent.
  Parent previously reproduced FK failure on DROP TABLE reels when a legacy
  link exists: this test FAILS (reports defect) if init_db raises there.
- RBAC: admin-only delete (forged actor dict ignored), packager cannot
  delete, packed/dispatched blocked, no-session mutations rejected.
- LAN disallows client period (server owns the month).
- Reweigh validation: non-finite / core>=gross / gross<=0 rejected.

Rules: real temp SQLite, real authenticate sessions (ContextVar per thread,
each concurrent thread authenticates its own account). ensure_default_users
only for bootstrap. No forged-actor bypass, no production edits, no real
DB/hardware/server/app. Only service period= override (supported) for clocks.
"""
import os
import re
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory import auth as A
from factory import services as S
from factory.db import get_conn, init_db


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_auth():
    A._SESSIONS.clear()
    A.set_session(None)
    yield
    A._SESSIONS.clear()
    A.set_session(None)


def _fresh_db(role="admin"):
    import tempfile
    from factory.db import init_db as _init
    from factory import auth as _A
    from tests._auth_helper import ensure_default_users
    p = os.path.join(tempfile.mkdtemp(prefix="simpld-monthly-"), "factory.db")
    _init(p)
    ensure_default_users(p)
    u, pw = {"admin": ("admin", "admin123"),
             "packager": ("packager", "pack123"),
             "dispatcher": ("dispatcher", "dispatch123")}[role]
    assert _A.authenticate(u, pw, p) is not None
    return p


def _mk_job(path, name="MONTH JOB", core=0.5):
    return S.create_job(name, size_mm=100, core_wt=core, path=path)


_CREDS = {"admin": ("admin", "admin123"),
            "packager": ("packager", "pack123"),
            "dispatcher": ("dispatcher", "dispatch123")}


def _login(path, role):
    u, pw = _CREDS[role]
    out = A.authenticate(u, pw, path)
    assert out is not None, f"login failed for {role}"
    return out


# --------------------------------------------------------------------------
# 1. monthly GLOBAL reel numbering across jobs + month/year reset same job
# --------------------------------------------------------------------------

def test_reel_global_across_jobs_and_month_reset_same_job():
    p = _fresh_db("admin")
    ja = _mk_job(p, "GLOBAL A")
    jb = _mk_job(p, "GLOBAL B")
    per = "2026-03"
    # global: seq shared across jobs in one month
    r1 = S.create_reel(ja["id"], gross=10.0, core=0.5, path=p, period=per)
    r2 = S.create_reel(jb["id"], gross=10.0, core=0.5, path=p, period=per)
    r3 = S.create_reel(ja["id"], gross=10.0, core=0.5, path=p, period=per)
    assert (r1["roll_no"], r2["roll_no"], r3["roll_no"]) == (1, 2, 3), (r1, r2, r3)
    assert r1["roll_period"] == r2["roll_period"] == r3["roll_period"] == per
    assert r1["roll_code"] == "R202603-0001"
    assert r2["roll_code"] == "R202603-0002"
    # month + year reset within the SAME job (job stays continuous)
    r_apr = S.create_reel(ja["id"], gross=10.0, core=0.5, path=p, period="2026-04")
    r_jan27 = S.create_reel(ja["id"], gross=10.0, core=0.5, path=p, period="2027-01")
    assert r_apr["roll_no"] == 1 and r_apr["roll_period"] == "2026-04"
    assert r_apr["roll_code"] == "R202604-0001"
    assert r_jan27["roll_no"] == 1 and r_jan27["roll_code"] == "R202701-0001"
    # predictor agrees with next global seq of that month
    assert S.predict_next_reel_seq(per, path=p) == 4


def test_box_global_across_jobs_and_month_reset_pack_weight_only():
    p = _fresh_db("admin")
    ja = _mk_job(p, "BOX A")
    jb = _mk_job(p, "BOX B")
    per = "2026-03"
    # real API only: pack_weight_carton (weight-only, no reel guessing)
    b1 = S.pack_weight_carton(ja["id"], 20.0, path=p, period=per)
    b2 = S.pack_weight_carton(jb["id"], 21.0, path=p, period=per)
    b3 = S.pack_weight_carton(ja["id"], 22.0, path=p, period=per)
    assert b1["carton_no"] == "C202603-0001"
    assert b2["carton_no"] == "C202603-0002"
    assert b3["carton_no"] == "C202603-0003"
    assert b1["box_period"] == b2["box_period"] == per
    assert b1["total_gross"] == b1["total_net"] == 20.0
    assert S.predict_next_box_seq(per, path=p) == 4
    # next month resets to 1 on the same job
    b_apr = S.pack_weight_carton(ja["id"], 20.0, path=p, period="2026-04")
    assert b_apr["carton_no"] == "C202604-0001"


# --------------------------------------------------------------------------
# 2. number non-reuse after deletion (reels + boxes)
# --------------------------------------------------------------------------

def test_numbers_never_reused_after_delete():
    p = _fresh_db("admin")
    j = _mk_job(p, "NOREUSE")
    per = "2026-05"
    r1 = S.create_reel(j["id"], gross=10.0, core=0.5, path=p, period=per)
    assert r1["roll_no"] == 1
    S.delete_reel(r1["id"], path=p)
    r2 = S.create_reel(j["id"], gross=10.0, core=0.5, path=p, period=per)
    assert r2["roll_no"] == 2, f"deleted #1 must not recycle, got {r2}"
    assert r2["roll_code"] == "R202605-0002"

    b1 = S.pack_weight_carton(j["id"], 20.0, path=p, period=per)
    assert b1["carton_no"] == "C202605-0001"
    S.delete_carton(b1["id"], path=p)
    b2 = S.pack_weight_carton(j["id"], 20.0, path=p, period=per)
    assert b2["carton_no"] == "C202605-0002", f"box number recycled: {b2}"


# --------------------------------------------------------------------------
# 3. bounded concurrent writes: counters + inserts unique (6 threads)
# --------------------------------------------------------------------------

def test_concurrent_writes_counters_and_inserts_unique():
    p = _fresh_db("admin")
    # per-thread accounts (admin creates; each thread authenticates its own)
    users = []
    for i in range(6):
        u = f"conc{i}"
        A.create_user(u, "pw1234", "packager", path=p)
        users.append((u, "pw1234"))
    job = _mk_job(p, "CONC JOB")
    jid = job["id"]
    per = "2026-06"
    errors = []
    made_reels = []
    made_boxes = []
    lock = threading.Lock()

    def worker(idx):
        try:
            A.authenticate(users[idx][0], users[idx][1], p)
            rs = [S.create_reel(jid, gross=10.0 + idx * 0.01, core=0.5,
                                path=p, period=per) for _ in range(2)]
            b = S.pack_weight_carton(jid, 20.0 + idx * 0.01, path=p, period=per)
            with lock:
                made_reels.extend(rs)
                made_boxes.append(b)
        except Exception as e:  # record, fail below with detail
            with lock:
                errors.append(f"thread{idx}: {type(e).__name__}: {e}")
        finally:
            A.set_session(None)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors, f"concurrent write errors: {errors}"
    _login(p, "admin")
    assert len(made_reels) == 12 and len(made_boxes) == 6
    roll_nos = sorted(r["roll_no"] for r in made_reels)
    assert roll_nos == list(range(1, 13)), f"reel seqs not 1..12 unique: {roll_nos}"
    codes = [r["roll_code"] for r in made_reels]
    assert len(set(codes)) == 12
    box_nos = sorted(b["carton_no"] for b in made_boxes)
    assert box_nos == [f"C202606-{i:04d}" for i in range(1, 7)], box_nos
    c = get_conn(p)
    try:
        assert c.execute("SELECT COUNT(*) n FROM reels WHERE roll_period=?",
                         (per,)).fetchone()["n"] == 12
        assert c.execute("SELECT COUNT(*) n FROM cartons WHERE box_period=?",
                         (per,)).fetchone()["n"] == 6
    finally:
        c.close()


# --------------------------------------------------------------------------
# 4. rollback: failed writes leave no partial record, consume no numbers
# --------------------------------------------------------------------------

def test_rollback_no_partial_record_no_number_consumed():
    p = _fresh_db("admin")
    j = _mk_job(p, "ROLLBACK")
    per = "2026-07"
    ok = S.create_reel(j["id"], gross=10.0, core=0.5, path=p, period=per)
    assert ok["roll_no"] == 1
    nxt_before = S.predict_next_reel_seq(per, path=p)
    assert nxt_before == 2
    c = get_conn(p)
    try:
        n0 = c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"]
    finally:
        c.close()
    # each failure must raise and leave DB unchanged
    for bad in ({"gross": 0}, {"gross": -3}, {"gross": 600},
                {"gross": float("inf")}, {"gross": float("nan")},
                {"gross": 0.4, "core": 0.5}):  # core >= gross
        try:
            S.create_reel(j["id"], path=p, period=per, **bad)
            raise AssertionError(f"expected failure for {bad}")
        except (ValueError, TypeError):
            pass
    # closed job + unknown job also roll back
    S.set_job_status(j["id"], "closed", path=p)
    with pytest.raises(ValueError):
        S.create_reel(j["id"], gross=10.0, core=0.5, path=p, period=per)
    with pytest.raises(ValueError):
        S.create_reel(999999, gross=10.0, core=0.5, path=p, period=per)
    with pytest.raises(ValueError):
        S.pack_weight_carton(j["id"], 20.0, path=p, period=per)
    c = get_conn(p)
    try:
        n1 = c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"]
        nbox = c.execute("SELECT COUNT(*) n FROM cartons").fetchone()["n"]
    finally:
        c.close()
    assert n1 == n0 == 1, (n0, n1)
    assert nbox == 0
    assert S.predict_next_reel_seq(per, path=p) == nxt_before == 2
    assert S.predict_next_box_seq(per, path=p) == 1


# --------------------------------------------------------------------------
# 5. audit rows + delete downstream guards
# --------------------------------------------------------------------------

def test_audit_and_delete_downstream_guards():
    p = _fresh_db("admin")
    j = _mk_job(p, "GUARD")
    r1 = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    r2 = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    # legacy reel-linked pack (kept for link-guard coverage)
    ct = S.pack_carton(j["id"], [r1["id"], r2["id"]], path=p)
    # audit persisted for create + pack
    audits = S.list_audit(path=p)
    actions = {a["action"] for a in audits}
    assert "create_reel" in actions and "pack_carton_legacy" in actions
    # packed reel delete blocked
    with pytest.raises(ValueError):
        S.delete_reel(r1["id"], path=p)
    # Ready-but-linked carton delete releases reels (allowed) — use weight box for blocked path
    b = S.pack_weight_carton(j["id"], 20.0, path=p)
    o = S.create_dispatch_order("GUARD CO", j["id"], 1, path=p)
    _login(p, "dispatcher")
    S.scan_into_order(o["id"], b["carton_no"], path=p)
    _login(p, "admin")
    with pytest.raises(ValueError):
        S.delete_carton(b["id"], path=p)  # loaded in order -> blocked
    # dispatched carton delete blocked
    _login(p, "dispatcher")
    S.complete_dispatch(o["id"], path=p)
    _login(p, "admin")
    with pytest.raises(ValueError):
        S.delete_carton(b["id"], path=p)  # now Dispatched -> blocked
    r_loose = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    _login(p, "dispatcher")
    S2 = S  # dispatcher session active
    with pytest.raises(PermissionError):
        S2.delete_reel(r_loose["id"], path=p)  # dispatcher may not delete
    # job with production/orders cannot be deleted
    _login(p, "admin")
    with pytest.raises(ValueError):
        S.delete_job(j["id"], path=p)


# --------------------------------------------------------------------------
# 6. RBAC: admin-only delete, forged actor ignored, no-session rejected
# --------------------------------------------------------------------------

def test_delete_rbac_forged_actor_ignored_and_nosession_rejected():
    p = _fresh_db("admin")
    j = _mk_job(p, "RBAC")
    r = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    forged = {"username": "intruder", "role": "admin", "user_id": 999}
    # dispatcher logged in: even a forged actor=dict(role=admin) must not authorize
    _login(p, "dispatcher")
    with pytest.raises(PermissionError):
        S.delete_reel(r["id"], path=p, actor=forged)
    with pytest.raises(PermissionError):
        S.delete_carton(1, path=p, actor=forged)
    # packager cannot delete either
    _login(p, "packager")
    with pytest.raises(PermissionError):
        S.delete_reel(r["id"], path=p)
    # no session at all: every mutation rejected
    A.set_session(None)
    for fn in (lambda: S.create_reel(j["id"], gross=10.0, core=0.5, path=p),
               lambda: S.pack_weight_carton(j["id"], 20.0, path=p),
               lambda: S.delete_reel(r["id"], path=p),
               lambda: S.reweigh_reel(r["id"], 11.0, path=p),
               lambda: S.mark_reel_printed(r["id"], path=p),
               lambda: S.delete_job(j["id"], path=p)):
        with pytest.raises(PermissionError):
            fn()
    # admin can still delete the loose reel (proves the reel survived denials)
    _login(p, "admin")
    S.delete_reel(r["id"], path=p)
    assert S.list_audit("reel", r["id"], path=p)


# --------------------------------------------------------------------------
# 7. print -> reweigh invalidation (keeps number/time, clears print, audits)
# --------------------------------------------------------------------------

def test_print_reweigh_invalidation_keeps_identity():
    p = _fresh_db("packager")  # packager holds production.create/reweigh/print
    j = S.create_job("PRINT JOB", size_mm=100, core_wt=0.5, path=p)
    r = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    orig_created = r["created_at"]
    orig_no, orig_per, orig_code = r["roll_no"], r["roll_period"], r["roll_code"]
    # printer-accepted state API (no fake printer: service mark = accepted path)
    marked = S.mark_reel_printed(r["id"], path=p)
    assert marked["print_status"] == "printed" and marked["print_count"] == 1
    # invalid reweigh values rejected
    for bad_gross, bad_core in [(float("inf"), None), (float("nan"), None),
                                (0, None), (-5, None), (600, None),
                                (10.0, 10.0), (10.0, 50.0)]:
        with pytest.raises(ValueError):
            S.reweigh_reel(r["id"], bad_gross, path=p, new_core=bad_core)
    # packed reel reweigh blocked (downstream guard)
    r2 = S.create_reel(j["id"], gross=10.0, core=0.5, path=p)
    S.pack_carton(j["id"], [r2["id"]], path=p)
    with pytest.raises(ValueError):
        S.reweigh_reel(r2["id"], 11.0, path=p)
    # valid reweigh: keeps number/period/code/created_at, clears print state
    rw = S.reweigh_reel(r["id"], 12.0, path=p)
    assert rw["roll_no"] == orig_no and rw["roll_period"] == orig_per
    assert rw["roll_code"] == orig_code and rw["created_at"] == orig_created
    assert rw["gross"] == 12.0 and rw["net"] == round(12.0 - 0.5, 3)
    assert rw["print_status"] == "pending"  # old print invalidated
    assert rw["print_count"] == 1  # count kept, not reset
    # audit preserves former values
    audits = S.list_audit("reel", r["id"], path=p)
    assert any(a["action"] == "reweigh" and "gross=10.0" in a["detail"]
               for a in audits), audits


# --------------------------------------------------------------------------
# 8. legacy migration: carton_items + dispatched order survive with identity
# --------------------------------------------------------------------------

LEGACY_TS = "2025-12-15 10:00:00"
LEGACY_PACKED = "2025-12-16 10:00:00"


def _build_legacy_db(path):
    """Pre-monthly DB: legacy reels UNIQUE(job_id, roll_no), legacy C0001
    carton, carton_items link + dispatched order. No monthly columns."""
    if os.path.exists(path):
        os.remove(path)
    c = sqlite3.connect(path)
    try:
        c.execute("PRAGMA foreign_keys=ON")
        c.executescript("""
        CREATE TABLE jobs (id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_no INTEGER UNIQUE NOT NULL, job_name TEXT NOT NULL,
          size_mm REAL DEFAULT 0, core_wt REAL NOT NULL DEFAULT 0.5,
          status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL,
          closed_at TEXT);
        CREATE TABLE reels (id INTEGER PRIMARY KEY AUTOINCREMENT,
          job_id INTEGER NOT NULL REFERENCES jobs(id),
          roll_no INTEGER NOT NULL, gross REAL NOT NULL, core REAL NOT NULL,
          net REAL NOT NULL, shift TEXT NOT NULL DEFAULT 'DAY',
          supervisor TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'loose',
          created_at TEXT NOT NULL, UNIQUE(job_id, roll_no));
        CREATE TABLE cartons (id INTEGER PRIMARY KEY AUTOINCREMENT,
          carton_no TEXT UNIQUE NOT NULL, job_id INTEGER NOT NULL REFERENCES jobs(id),
          bundle_no INTEGER NOT NULL, num_reels INTEGER NOT NULL,
          total_gross REAL NOT NULL, total_core REAL NOT NULL, total_net REAL NOT NULL,
          shift TEXT NOT NULL DEFAULT 'DAY', customer TEXT DEFAULT '',
          status TEXT NOT NULL DEFAULT 'Ready', packed_at TEXT NOT NULL,
          dispatched_at TEXT, UNIQUE(job_id, bundle_no));
        CREATE TABLE carton_items (carton_id INTEGER NOT NULL REFERENCES cartons(id) ON DELETE CASCADE,
          reel_id INTEGER UNIQUE NOT NULL REFERENCES reels(id),
          PRIMARY KEY (carton_id, reel_id));
        CREATE TABLE dispatch_orders (id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_no TEXT UNIQUE NOT NULL, customer TEXT NOT NULL,
          job_id INTEGER NOT NULL REFERENCES jobs(id), required_bundles INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'loading', order_date TEXT NOT NULL,
          created_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE dispatch_items (id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id INTEGER NOT NULL REFERENCES dispatch_orders(id) ON DELETE CASCADE,
          carton_id INTEGER UNIQUE NOT NULL REFERENCES cartons(id),
          scanned_at TEXT NOT NULL, UNIQUE(order_id, carton_id));
        CREATE TABLE users (id INTEGER PRIMARY KEY AUTOINCREMENT,
          username TEXT UNIQUE NOT NULL, pass_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'packager', created_at TEXT NOT NULL);
        """)
        c.execute("INSERT INTO jobs(job_no, job_name, size_mm, core_wt, status, created_at)"
                  " VALUES(4169,'LEGACY JOB',160,0.5,'open',?)", (LEGACY_TS,))
        jid = c.execute("SELECT id FROM jobs WHERE job_no=4169").fetchone()[0]
        c.execute("INSERT INTO reels(job_id, roll_no, gross, core, net, shift, supervisor, status, created_at)"
                  " VALUES(?,1,11.38,0.5,10.88,'DAY','RAMESH','packed',?)", (jid, LEGACY_TS))
        c.execute("INSERT INTO reels(job_id, roll_no, gross, core, net, shift, supervisor, status, created_at)"
                  " VALUES(?,2,11.12,0.5,10.62,'DAY','RAMESH','packed',?)", (jid, LEGACY_TS))
        r1 = c.execute("SELECT id FROM reels WHERE roll_no=1").fetchone()[0]
        r2 = c.execute("SELECT id FROM reels WHERE roll_no=2").fetchone()[0]
        c.execute("INSERT INTO cartons(carton_no, job_id, bundle_no, num_reels, total_gross, total_core, total_net,"
                  " shift, status, packed_at, dispatched_at)"
                  " VALUES('C0001',?,1,2,22.5,1.0,21.5,'DAY','Dispatched',?,?)",
                  (jid, LEGACY_PACKED, LEGACY_PACKED))
        cid = c.execute("SELECT id FROM cartons WHERE carton_no='C0001'").fetchone()[0]
        c.execute("INSERT INTO carton_items(carton_id, reel_id) VALUES(?,?)", (cid, r1))
        c.execute("INSERT INTO carton_items(carton_id, reel_id) VALUES(?,?)", (cid, r2))
        c.execute("INSERT INTO dispatch_orders(order_no, customer, job_id, required_bundles, status,"
                  " order_date, created_at, completed_at)"
                  " VALUES('DO-0001','LEGACY CO',?,1,'complete','2025-12-16',?,?)",
                  (jid, LEGACY_PACKED, LEGACY_PACKED))
        oid = c.execute("SELECT id FROM dispatch_orders WHERE order_no='DO-0001'").fetchone()[0]
        c.execute("INSERT INTO dispatch_items(order_id, carton_id, scanned_at) VALUES(?,?,?)",
                  (oid, cid, LEGACY_PACKED))
        c.commit()
    finally:
        c.close()
    return {"job_id": jid, "reel_ids": (r1, r2), "carton_id": cid, "order_id": oid}


def test_legacy_migration_preserves_links_and_repeated_init_idempotent(tmp_path):
    p = str(tmp_path / "legacy.db")
    ids = _build_legacy_db(p)
    # Must NOT raise (parent reproduced FK failure on DROP TABLE reels here).
    try:
        init_db(p)
    except Exception as e:
        pytest.fail(f"PRODUCTION DEFECT: legacy migration with carton_items + "
                    f"dispatched order failed: {type(e).__name__}: {e}")
    c = get_conn(p)
    try:
        # exact ID + weight identity preserved
        r1 = c.execute("SELECT * FROM reels WHERE id=?", (ids["reel_ids"][0],)).fetchone()
        r2 = c.execute("SELECT * FROM reels WHERE id=?", (ids["reel_ids"][1],)).fetchone()
        assert dict(r1)["gross"] == 11.38 and dict(r1)["net"] == 10.88
        assert dict(r2)["gross"] == 11.12 and dict(r2)["net"] == 10.62
        assert dict(r1)["roll_no"] == 1 and dict(r2)["roll_no"] == 2
        # periods backfilled from created_at month, numbers untouched
        assert dict(r1)["roll_period"] == "2025-12"
        ct = c.execute("SELECT * FROM cartons WHERE id=?", (ids["carton_id"],)).fetchone()
        assert dict(ct)["carton_no"] == "C0001"  # legacy code untouched
        assert dict(ct)["box_period"] == "2025-12"
        assert dict(ct)["total_gross"] == 22.5 and dict(ct)["bundle_no"] == 1
        # links preserved
        links = c.execute("SELECT carton_id, reel_id FROM carton_items ORDER BY reel_id").fetchall()
        assert [(x["carton_id"], x["reel_id"]) for x in links] == \
            [(ids["carton_id"], ids["reel_ids"][0]), (ids["carton_id"], ids["reel_ids"][1])]
        di = c.execute("SELECT * FROM dispatch_items WHERE order_id=?",
                       (ids["order_id"],)).fetchone()
        assert dict(di)["carton_id"] == ids["carton_id"]
        o = c.execute("SELECT * FROM dispatch_orders WHERE id=?",
                      (ids["order_id"],)).fetchone()
        assert dict(o)["status"] == "complete" and dict(o)["order_no"] == "DO-0001"
        # counters seeded at legacy MAX so no collision/reuse
        rc = c.execute("SELECT last_no FROM monthly_counters WHERE period='2025-12' AND kind='reel'").fetchone()
        assert rc is not None and int(rc["last_no"]) >= 2
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
        n_reels = c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"]
        n_links = c.execute("SELECT COUNT(*) n FROM carton_items").fetchone()["n"]
    finally:
        c.close()
    # repeated init: idempotent, no renumber, no loss
    init_db(p)
    c = get_conn(p)
    try:
        assert c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"] == n_reels == 2
        assert c.execute("SELECT COUNT(*) n FROM carton_items").fetchone()["n"] == n_links == 2
        assert dict(c.execute("SELECT * FROM cartons WHERE id=?",
                              (ids["carton_id"],)).fetchone())["carton_no"] == "C0001"
        assert not c.execute("PRAGMA foreign_key_check").fetchall()
    finally:
        c.close()


# --------------------------------------------------------------------------
# 9. LAN disallows client period (server owns the month)
# --------------------------------------------------------------------------

def test_lan_disallows_client_period_and_actor():
    from factory import lan_server as LV
    assert "period" in LV.FORBIDDEN_ARGS
    assert "actor" in LV.FORBIDDEN_ARGS and "role" in LV.FORBIDDEN_ARGS
    with pytest.raises(Exception):
        LV._reject_forbidden({"period": "2026-03"})
    with pytest.raises(Exception):
        LV._reject_forbidden({"actor": {"role": "admin"}})
    # allowlisted reel/box ops take no period from the wire
    for op in ("create_reel", "pack_weight_carton", "pack_carton"):
        allowed = LV.OPS[op][1]
        assert "period" not in allowed and "actor" not in allowed, (op, allowed)
    # service-level period override itself is genuinely supported
    import inspect
    assert "period" in inspect.signature(S.create_reel).parameters
    assert "period" in inspect.signature(S.pack_weight_carton).parameters
