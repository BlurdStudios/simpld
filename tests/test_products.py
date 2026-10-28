"""Products master + job snapshot tests. Temp SIMPLD_DB only, never production."""
import os
import sys
import sqlite3
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._auth_helper import ensure_default_users


def _fresh_db():
    from factory.db import init_db
    p = os.path.join(tempfile.mkdtemp(prefix="simpld-prod-"), "factory.db")
    init_db(p)
    from factory import auth as _A
    ensure_default_users(p)
    assert _A.authenticate("admin", "admin123", p) is not None
    return p


def test_migration_preserves_ids_and_values():
    """Legacy DB (pre-products schema) migrates with IDs/weights intact,
    products backfilled with wrappers NULL — never an invented 16500."""
    from factory.db import init_db, get_conn
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
    INSERT INTO jobs(job_no, job_name, size_mm, core_wt, status, created_at)
      VALUES(4169, 'APPY FIZZ-160ML (SEP.28) BODY', 160, 0.280, 'open', '2026-01-01 10:00:00');
    INSERT INTO reels(job_id, roll_no, gross, core, net, shift, status, created_at)
      VALUES(1, 1, 11.380, 0.280, 11.100, 'DAY', 'loose', '2026-01-01 10:01:00');
    INSERT INTO cartons(carton_no, job_id, bundle_no, num_reels, total_gross, total_core,
      total_net, shift, status, packed_at) VALUES('C0001', 1, 1, 0, 10.0, 0, 10.0, 'DAY', 'Ready',
      '2026-01-01 11:00:00');
    INSERT INTO dispatch_orders(order_no, customer, job_id, required_bundles, status, order_date,
      created_at) VALUES('DO-0001', 'PARLE', 1, 2, 'loading', '2026-01-01', '2026-01-01 12:00:00');
    """)
    c.commit()
    c.close()
    init_db(p)  # migrate
    c = get_conn(p)
    try:
        j = c.execute("SELECT * FROM jobs WHERE id=1").fetchone()
        assert j["job_no"] == 4169 and j["job_name"] == "APPY FIZZ-160ML (SEP.28) BODY"
        assert j["size_mm"] == 160 and j["core_wt"] == 0.280, "legacy size/core kept"
        assert j["id"] == 1 and j["product_name"] == j["job_name"]
        assert j["wrappers_per_reel"] is None, "legacy wrappers stay unknown"
        r = c.execute("SELECT * FROM reels WHERE id=1").fetchone()
        assert (r["gross"], r["core"], r["net"]) == (11.380, 0.280, 11.100)
        ct = c.execute("SELECT * FROM cartons WHERE id=1").fetchone()
        assert ct["carton_no"] == "C0001" and ct["total_gross"] == 10.0
        o = c.execute("SELECT * FROM dispatch_orders WHERE id=1").fetchone()
        assert o["order_no"] == "DO-0001"
        prods = c.execute("SELECT * FROM products").fetchall()
        assert len(prods) == 1 and prods[0]["wrappers_per_reel"] is None, \
            "backfill must be NULL, never an invented count"
        assert prods[0]["name"] == "APPY FIZZ-160ML (SEP.28) BODY"
    finally:
        c.close()


def test_product_validation_and_reuse():
    from factory import services as S
    p = _fresh_db()
    for bad in (0, -5, "abc", "16.5", 16.5, True):
        try:
            S.create_product("W JOB", bad, path=p)
            raise SystemExit(f"FAIL: wrappers {bad!r} accepted")
        except ValueError:
            pass
    # Blank pieces = kg product (NULL, never invented).
    kg = S.create_product("KG PRODUCT", None, path=p)
    assert kg["wrappers_per_reel"] is None
    kg2 = S.create_product("KG PRODUCT 2", "", path=p)
    assert kg2["wrappers_per_reel"] is None
    assert kg["unit"] == "kg" and kg2["unit"] == "kg"
    # Explicit unit: pieces needs a count, kg refuses one.
    for bad_args in ({"wrappers_per_reel": None, "unit": "pieces"},
                     {"wrappers_per_reel": 100, "unit": "kg"},
                     {"wrappers_per_reel": 100, "unit": "litres"}):
        try:
            S.create_product("UNIT JOB", path=p, **bad_args)
            raise SystemExit(f"FAIL: unit args {bad_args} accepted")
        except ValueError:
            pass
    exp = S.create_product("KG EXPLICIT", None, path=p, unit="kg")
    assert exp["unit"] == "kg" and exp["wrappers_per_reel"] is None
    # Kg products refuse Set pieces; snapshot carries the unit.
    try:
        S.set_product_wrappers(kg["id"], 16500, path=p)
        raise SystemExit("FAIL: Set pieces accepted on kg product")
    except ValueError:
        pass
    jk = S.create_job_for_product(kg["id"], path=p)
    assert jk["unit"] == "kg" and jk["wrappers_per_reel"] is None
    prod = S.create_product("appy fizz-160ml (sep.28) body", 16500, path=p)
    assert prod["name"] == "APPY FIZZ-160ML (SEP.28) BODY" and prod["wrappers_per_reel"] == 16500
    try:
        S.create_product("APPY FIZZ-160ML (SEP.28) BODY", 16500, path=p)
        raise SystemExit("FAIL: duplicate product accepted")
    except ValueError:
        pass
    # one product, many jobs across sessions
    j1 = S.create_job_for_product(prod["id"], path=p)
    j2 = S.create_job_for_product(prod["id"], path=p)
    assert j1["product_id"] == j2["product_id"] == prod["id"]
    assert j1["job_no"] != j2["job_no"] and j1["wrappers_per_reel"] == 16500
    # snapshot survives catalog rename
    from factory.db import get_conn
    c = get_conn(p)
    try:
        c.execute("UPDATE products SET name='RENAMED', wrappers_per_reel=999 WHERE id=?", (prod["id"],))
        c.commit()
    finally:
        c.close()
    j1r = S.get_job_by_no(j1["job_no"], path=p)
    assert j1r["product_name"] == "APPY FIZZ-160ML (SEP.28) BODY" and j1r["wrappers_per_reel"] == 16500, \
        "job history must not rewrite on catalog change"


def test_legacy_create_job_compat():
    from factory import services as S
    p = _fresh_db()
    j = S.create_job("LEGACY JOB", size_mm=160, core_wt=0.280, path=p)
    assert j["job_no"] >= 4169 and j["size_mm"] == 160 and j["core_wt"] == 0.280
    assert j["wrappers_per_reel"] is None, "legacy path never invents a count"
    r = S.create_reel(j["id"], gross=11.380, core=0.280, path=p)
    assert r["net"] == round(11.380 - 0.280, 3), "core behavior unchanged"


def test_jobs_page_shows_snapshot_offscreen():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from factory import db as DB, services as S
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-uip-"), "factory.db")
    DB.DB_PATH = tmp
    DB.init_db(tmp)
    from factory import auth as _A
    ensure_default_users(tmp)
    assert _A.authenticate("admin", "admin123", tmp) is not None
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    prod = S.create_product("UI PROD JOB", "16500")
    j = S.create_job_for_product(prod["id"])
    from factory.ui.pages_misc import JobsPage
    pg = JobsPage()
    pg.refresh()
    assert pg.table.columnCount() == 6
    assert "Pieces" in pg.table.horizontalHeaderItem(2).text()
    assert pg.table.item(0, 2).text() == "16500", "snapshot visible in Jobs UI"
    assert j["job_name"] == "UI PROD JOB"
    pg.close()
    app.closeAllWindows()


if __name__ == "__main__":
    test_migration_preserves_ids_and_values()
    test_product_validation_and_reuse()
    test_legacy_create_job_compat()
    test_jobs_page_shows_snapshot_offscreen()
    print("test_products PASS")
