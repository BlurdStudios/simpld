"""day_report: day-scoped line items (reels + boxes) across all jobs.

Temp SIMPLD_DB only. Seeds two jobs with reels/boxes on two distinct days,
asserts the day filter isolates, totals match, an empty day returns empty
rows + zero totals, and the LAN round-trip returns the same payload.
"""
import json
import os
import shutil
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
from factory.db import init_db, get_conn, today_iso
from factory import services as S
from tests._auth_helper import ensure_default_users, fresh_db, login_as

REPO_DEFAULT_DB = os.path.abspath(DB.DEFAULT_DB)

DAY1 = "2025-01-10"
DAY2 = "2025-01-11"
EMPTY = "2025-01-12"


def _seed(p: str) -> dict:
    """Two jobs + reels/boxes on two distinct days. Returns row ids."""
    assert os.path.abspath(p) != REPO_DEFAULT_DB
    job1 = S.create_job("DAYRPT JOB A", core_wt=0.280, path=p)
    job2 = S.create_job("DAYRPT JOB B", core_wt=0.500, path=p)
    r1 = S.create_reel(job1["id"], gross=11.380, core=0.280, path=p)  # net 11.100
    r2 = S.create_reel(job1["id"], gross=10.500, core=0.280, path=p)  # net 10.220
    ct1 = S.pack_carton(job1["id"], [r1["id"], r2["id"]], path=p)
    r3 = S.create_reel(job2["id"], gross=20.000, core=0.500, path=p)  # net 19.500
    r4 = S.create_reel(job2["id"], gross=21.000, core=0.500, path=p)  # net 20.500
    ct2 = S.pack_weight_carton(job2["id"], gross_weight=30.000, path=p)
    c = get_conn(p)
    try:
        c.execute("UPDATE reels SET created_at='2025-01-10 10:00:00' WHERE id IN (?,?)",
                  (r1["id"], r2["id"]))
        c.execute("UPDATE cartons SET packed_at='2025-01-10 10:00:00' WHERE id=?",
                  (ct1["id"],))
        c.execute("UPDATE reels SET created_at='2025-01-11 10:00:00' WHERE id IN (?,?)",
                  (r3["id"], r4["id"]))
        c.execute("UPDATE cartons SET packed_at='2025-01-11 10:00:00' WHERE id=?",
                  (ct2["id"],))
        c.commit()
    finally:
        c.close()
    return {"job1": job1, "job2": job2, "r1": r1, "r2": r2,
            "r3": r3, "r4": r4, "ct1": ct1, "ct2": ct2}


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True)


# --------------------------------------------------------------------------
# local services (real temp DB + real auth)
# --------------------------------------------------------------------------

def test_day_filter_isolates():
    p = fresh_db("admin", prefix="simpld-dayrpt-")
    ids = _seed(p)
    d1 = S.day_report(DAY1, p)
    d2 = S.day_report(DAY2, p)
    assert d1["day"] == DAY1 and d2["day"] == DAY2
    assert len(d1["reel_rows"]) == 2 and len(d1["box_rows"]) == 1
    assert len(d2["reel_rows"]) == 2 and len(d2["box_rows"]) == 1
    # day1 box is reel-linked (legacy pack) with the two day1 rolls
    b1 = d1["box_rows"][0]
    assert b1["linked"] is True and b1["weight_only"] is False
    assert set(b1["rolls"].split(",")) == {str(ids["r1"]["roll_no"]),
                                           str(ids["r2"]["roll_no"])}
    # day2 box is independent (weight-only), never merges reel weights
    b2 = d2["box_rows"][0]
    assert b2["weight_only"] is True and b2["num_reels"] == 0
    # reel rows carry month-qualified identity (roll_label set, never bare)
    assert all(r["roll_label"] for r in d1["reel_rows"])
    assert all(r["roll_label"] for r in d2["reel_rows"])


def test_totals_match():
    p = fresh_db("admin", prefix="simpld-dayrpt-")
    ids = _seed(p)
    d1 = S.day_report(DAY1, p)
    t1 = d1["totals"]
    assert t1["reels"] == 2
    assert t1["reel_net"] == pytest.approx(21.320, abs=1e-9)
    assert t1["reel_gross"] == pytest.approx(21.880, abs=1e-9)
    assert t1["reel_core"] == pytest.approx(0.560, abs=1e-9)
    assert t1["boxes"] == 1
    # box weight reported separately (stored carton weight), never added to reels
    assert t1["box_gross"] == pytest.approx(21.880, abs=1e-9)

    d2 = S.day_report(DAY2, p)
    t2 = d2["totals"]
    assert t2["reels"] == 2
    assert t2["reel_net"] == pytest.approx(40.000, abs=1e-9)
    assert t2["boxes"] == 1
    assert t2["box_gross"] == pytest.approx(30.000, abs=1e-9)

    # per-job subtotals
    by = {j["job_no"]: j for j in d1["by_job"]}
    j1 = by[ids["job1"]["job_no"]]
    assert j1["reels"] == 2
    assert j1["reel_net"] == pytest.approx(21.320, abs=1e-9)
    assert j1["boxes"] == 1
    assert j1["box_gross"] == pytest.approx(21.880, abs=1e-9)


def test_empty_day_returns_empty_rows_and_zero_totals():
    p = fresh_db("admin", prefix="simpld-dayrpt-")
    _seed(p)
    d = S.day_report(EMPTY, p)
    assert d["day"] == EMPTY
    assert d["reel_rows"] == []
    assert d["box_rows"] == []
    assert d["rows"] == []
    assert d["by_job"] == []
    assert d["totals"] == {
        "reels": 0, "reel_gross": 0.0, "reel_core": 0.0, "reel_net": 0.0,
        "boxes": 0, "box_gross": 0.0,
    }


def test_day_validation_and_default():
    p = fresh_db("admin", prefix="simpld-dayrpt-")
    with pytest.raises(ValueError):
        S.day_report("not-a-date", p)
    with pytest.raises(ValueError):
        S.day_report("2025/01/10", p)
    # default is today, read-only, never writes
    d = S.day_report(None, p)
    assert d["day"] == today_iso()
    assert d["reel_rows"] == [] and d["box_rows"] == []


def test_no_db_writes():
    p = fresh_db("admin", prefix="simpld-dayrpt-")
    _seed(p)
    c = get_conn(p)
    before = (c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"],
              c.execute("SELECT COUNT(*) n FROM cartons").fetchone()["n"],
              c.execute("SELECT COUNT(*) n FROM production_audit").fetchone()["n"])
    c.close()
    S.day_report(DAY1, p)
    S.day_report(DAY2, p)
    S.day_report(EMPTY, p)
    c = get_conn(p)
    after = (c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"],
             c.execute("SELECT COUNT(*) n FROM cartons").fetchone()["n"],
             c.execute("SELECT COUNT(*) n FROM production_audit").fetchone()["n"])
    c.close()
    assert after == before


# --------------------------------------------------------------------------
# LAN round-trip (real TLS server; same payload as local services)
# --------------------------------------------------------------------------

@pytest.fixture(scope="module")
def tls_pair():
    if shutil.which("openssl") is None:
        pytest.skip("openssl CLI not available")
    d = tempfile.mkdtemp(prefix="simpld-dayrpt-cert-")
    cert = os.path.join(d, "cert.pem")
    key = os.path.join(d, "key.pem")
    r = subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048",
         "-keyout", key, "-out", cert, "-days", "2", "-nodes",
         "-subj", "/CN=simpld-dayrpt-test",
         "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-2000:]
    yield cert, key
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def lan_srv(tls_pair):
    cert, key = tls_pair
    d = tempfile.mkdtemp(prefix="simpld-dayrpt-lan-")
    dbp = os.path.join(d, "factory.db")
    assert os.path.abspath(dbp) != REPO_DEFAULT_DB
    init_db(dbp)
    ensure_default_users(dbp)
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


def test_lan_round_trip_same_payload(lan_srv):
    # Seed the host DB with a real local session bound to that file.
    login_as("admin", lan_srv["db"])
    _seed(lan_srv["db"])

    DA.authenticate("dispatcher", "dispatch123")  # history.read role
    lan_report = DA.day_report(DAY1)
    local_report = S.day_report(DAY1, lan_srv["db"])
    assert _canon(lan_report) == _canon(local_report)

    # empty day over LAN too
    assert _canon(DA.day_report(EMPTY)) == _canon(S.day_report(EMPTY, lan_srv["db"]))
    DA.logout()


def test_lan_day_report_rejects_bad_day(lan_srv):
    DA.authenticate("dispatcher", "dispatch123")
    with pytest.raises(LC.LanValidationError):
        DA.day_report("not-a-date")
    DA.logout()


if __name__ == "__main__":
    test_day_filter_isolates()
    test_totals_match()
    test_empty_day_returns_empty_rows_and_zero_totals()
    test_day_validation_and_default()
    test_no_db_writes()
    print("test_day_report PASS")
