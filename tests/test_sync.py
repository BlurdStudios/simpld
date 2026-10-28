"""Plan 009 Phase 3: factory sync (outbound-only). Temp DB, mocked HTTP."""
import json
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from factory import db as _db
from factory import data_access as DA
from factory import auth as _auth
from factory import sync as S


def _seed(path):
    _db.DB_PATH = path
    _db.init_db(path)
    _auth.set_session(None)
    c = _db.get_conn(path)
    c.execute("INSERT INTO users(username, pass_hash, role, created_at)"
              " VALUES('admin','x','admin','2026-10-26 10:00:00')")
    c.commit()
    c.close()
    # fake an admin session for services RBAC
    import secrets
    tok = secrets.token_urlsafe(16)
    _auth._SESSIONS[tok] = {"db": _auth._canonical_db_path(path),
                            "user_id": 1, "username": "admin",
                            "pass_hash": "x", "created": 1e9}
    _auth.set_session(tok)
    DA.configure_local()
    from factory import services as svc
    p = svc.create_product("SYNC-PROD", 100)
    import factory.services as _s
    job = {"id": 1}
    # create job via services directly (bypasses DA product flow quirks)
    j = svc.create_job_for_product(p["id"], core_wt=0.5)
    r = svc.create_reel(j["id"], gross=10.0, core=0.5)
    b = svc.pack_weight_carton(j["id"], gross_weight=20.0)
    o = svc.create_dispatch_order("Buyer1", j["id"], required_pieces=50)
    return j


def test_adapt_shapes(tmp_path, monkeypatch):
    p = str(tmp_path / "s.db")
    monkeypatch.setenv("SIMPLD_DB", p)
    _seed(p)
    push = S.build_push()
    assert push["reporter"]["packager_fp"]
    assert push["reporter"]["station_id"]
    dr = push["day_report"]
    assert dr["day"] and isinstance(dr["reel_rows"], list)
    assert set(dr["totals"]) == {"reel_count", "reel_net_kg",
                                 "box_count", "box_gross_kg"}
    assert isinstance(dr["by_job"], dict)
    assert push["orders"] and push["orders"][0]["buyer"] == "BUYER1"
    sc = push["stock_counts"]
    assert set(sc) == {"raw_rolls", "packed_cartons", "dispatched_cartons"}
    assert sc["packed_cartons"] >= 1


def test_push_disabled(tmp_path, monkeypatch):
    monkeypatch.delenv("SIMPLD_SYNC_URL", raising=False)
    monkeypatch.delenv("SIMPLD_SYNC_SITE_ID", raising=False)
    monkeypatch.delenv("SIMPLD_SYNC_KEY", raising=False)
    assert S.configured() is False
    res = S.push_now()
    assert res["ok"] is False and res["queued"] is False


def test_push_queues_offline(tmp_path, monkeypatch):
    p = str(tmp_path / "s2.db")
    monkeypatch.setenv("SIMPLD_DB", p)
    _seed(p)
    monkeypatch.setenv("SIMPLD_SYNC_URL", "https://127.0.0.1:9")
    monkeypatch.setenv("SIMPLD_SYNC_SITE_ID", "test-site")
    monkeypatch.setenv("SIMPLD_SYNC_KEY", "k")
    monkeypatch.setenv("SIMPLD_DATA_DIR", str(tmp_path / "appdata"))
    res = S.push_now()
    assert res["ok"] is False and res["queued"] is True
