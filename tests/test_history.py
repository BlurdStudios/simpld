"""production_history must list reels+boxes without the UNION ORDER BY crash."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from factory.db import init_db
from factory import services as S
from tests._auth_helper import ensure_default_users


def _fresh_db():
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-hist-"), "factory.db")
    init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    return tmp


def test_production_history_has_reels_and_boxes():
    p = _fresh_db()
    job = S.create_job("HIST TEST JOB", size_mm=160, core_wt=0.280, path=p)
    r1 = S.create_reel(job["id"], gross=11.380, core=0.280, path=p)
    r2 = S.create_reel(job["id"], gross=11.120, core=0.280, path=p)
    ct = S.pack_carton(job["id"], [r1["id"], r2["id"]], path=p)
    rows = S.production_history(path=p)  # used to raise OperationalError
    kinds = {r["kind"] for r in rows}
    assert "Reel" in kinds and "Box" in kinds, rows
    box_rows = [r for r in rows if r["kind"] == "Box"]
    assert any(b["reference"] == ct["carton_no"] for b in box_rows)
    reel_rows = [r for r in rows if r["kind"] == "Reel"]
    assert any(r["box"] == ct["carton_no"] for r in reel_rows), rows
    # newest first
    assert rows[0]["recorded_at"] >= rows[-1]["recorded_at"], rows


def test_history_dispatch_tab_fields_match_schema():
    p = _fresh_db()
    job = S.create_job("HIST DISPATCH JOB", core_wt=0.280, path=p)
    r = S.create_reel(job["id"], gross=10.280, core=0.280, path=p)
    ct = S.pack_carton(job["id"], [r["id"]], path=p)
    o = S.create_dispatch_order("HIST CUSTOMER", job["id"], 1, path=p)
    S.scan_into_order(o["id"], ct["carton_no"], path=p)
    orders = S.list_orders(path=p)
    assert orders, "dispatch tab would be empty"
    for key in ("order_no", "customer", "job_no", "job_name",
                "required_bundles", "loaded", "status", "order_date"):
        assert key in orders[0], (key, orders[0].keys())


if __name__ == "__main__":
    test_production_history_has_reels_and_boxes()
    test_history_dispatch_tab_fields_match_schema()
    print("test_history PASS")
