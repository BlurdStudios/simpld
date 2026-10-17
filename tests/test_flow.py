"""End-to-end test of the 8-step MVP flow. Runs offline against a temp DB."""
import os
import sys
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from factory.db import init_db
from factory import services as S
from tests._auth_helper import ensure_default_users

tmp = os.path.join(tempfile.gettempdir(), "simpld_test.db")
if os.path.exists(tmp):
    os.remove(tmp)
init_db(tmp)
from factory import auth as _A
ensure_default_users(tmp)
assert _A.authenticate("admin", "admin123", tmp) is not None
P = tmp

print("1. create product job")
job = S.create_job("APPY FIZZ-160ML (SEP.28) BODY", size_mm=160, core_wt=0.280, path=P)
assert job["job_no"] >= 4169, job
print("   J:", job["job_no"], job["job_name"])

print("2. create multiple reels (Type-1, no QR)")
reels = []
for g in (11.380, 11.120, 10.960):
    r = S.create_reel(job["id"], gross=g, core=0.280, shift="DAY", supervisor="RAMESH", path=P)
    reels.append(r)
    assert abs(r["net"] - (r["gross"] - r["core"])) < 0.001
    assert r["status"] == "loose"
print("   rolls:", [r["roll_no"] for r in reels], "nets:", [r["net"] for r in reels])

print("3. pack reels into carton (Type-2, QR)")
ct = S.pack_carton(job["id"], [reels[0]["id"], reels[1]["id"]], shift="DAY", path=P)
assert ct["num_reels"] == 2
d = S.carton_detail(ct["id"], path=P)
assert d["qr"].startswith("VASAVI:C"), d
assert len(d["reels"]) == 2
print("   carton:", ct["carton_no"], "bundle:", ct["bundle_no"], "QR:", d["qr"],
      "net:", ct["total_net"])

print("4. see cartons in ready stock")
stock = S.ready_stock(path=P)
assert any(s["carton_no"] == ct["carton_no"] for s in stock), stock
print("   stock count:", len(stock))

print("5. create dispatch order")
order = S.create_dispatch_order("PARLE AGRO HYD", job["id"], required_bundles=1, path=P)
assert order["order_no"].startswith("DO-")
print("   order:", order["order_no"], order["customer"])

print("6. scan carton (incl. duplicate + mismatch guards)")
got = S.scan_into_order(order["id"], d["qr"], path=P)  # full 'VASAVI:C0001' form
assert got["carton_no"] == ct["carton_no"]
try:
    S.scan_into_order(order["id"], ct["carton_no"], path=P)
    raise SystemExit("FAIL: duplicate scan not blocked")
except ValueError as e:
    print("   duplicate blocked:", e)
# mismatch guard
job2 = S.create_job("BAILLEY-1LTR (SEP.36C)", size_mm=200, core_wt=0.720, path=P)
r2 = S.create_reel(job2["id"], gross=19.06, core=0.720, path=P)
ct2 = S.pack_carton(job2["id"], [r2["id"]], path=P)
try:
    S.scan_into_order(order["id"], ct2["carton_no"], path=P)
    raise SystemExit("FAIL: mismatch not blocked")
except ValueError as e:
    print("   mismatch blocked:", e)
prog = S.order_progress(order["id"], path=P)
assert prog["loaded"] == 1 and prog["remaining"] == 0, prog
print(f"   progress: required {prog['required_bundles']}, loaded {prog['loaded']}, remaining {prog['remaining']}")

print("7. complete dispatch")
done = S.complete_dispatch(order["id"], path=P)
assert done["status"] == "complete"
stock2 = S.ready_stock(path=P)
assert not any(s["carton_no"] == ct["carton_no"] for s in stock2)
print("   dispatched, removed from stock")

print("8. stock and daily totals update")
s = S.daily_summary(path=P)
print("   summary:", s)
assert s["reels_packed"] >= 4 and s["cartons_packed"] >= 2 and s["dispatched"] >= 1
# packing slip totals
_, rows, totals = S.packing_slip_rows(job["id"], path=P)
assert totals["bundles"] >= 1 and totals["reels"] >= 2
print("   slip totals:", totals)

print("\nALL 8 STEPS PASS ✓")
