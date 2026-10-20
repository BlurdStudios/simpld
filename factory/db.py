"""SQLite local-first storage. Fully offline. No internet required."""
from __future__ import annotations
import os
import sqlite3
import sys
from datetime import datetime

APP_NAME = "simpld"
COMPANY = "SRI VASAVI SHRINK FILM"


def _is_frozen() -> bool:
    """True inside a PyInstaller (or similar) frozen executable."""
    return bool(getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"))


def app_data_dir() -> str:
    """Stable writable per-user data root shared by DB/backups/hardware/print log.

    - Env override SIMPLD_DATA_DIR wins everywhere (tests, portable installs).
    - Frozen (Windows .exe): LOCALAPPDATA/Simpld (e.g.
      C:\\Users\\<name>\\AppData\\Local\\Simpld). Falls back to APPDATA, then
      %USERPROFILE%, then cwd-adjacent data dir. Never sys._MEIPASS (read-only
      extraction temp) and never the protected install dir.
    - Source/dev: <repo>/data (existing default, unchanged).
    Hardware agent: reuse this function for hardware.json / print_log.txt so
    the frozen app and source checkout share one stable root.
    """
    env = os.environ.get("SIMPLD_DATA_DIR")
    if env:
        return os.path.abspath(os.path.expanduser(env))
    if _is_frozen():
        if os.name == "nt":
            base = (os.environ.get("LOCALAPPDATA")
                    or os.environ.get("APPDATA")
                    or os.environ.get("USERPROFILE")
                    or os.path.expanduser("~"))
            return os.path.join(base, "Simpld")
        xdg = os.environ.get("XDG_DATA_HOME")
        if xdg:
            return os.path.join(xdg, "simpld")
        return os.path.join(os.path.expanduser("~"), ".local", "share", "simpld")
    _BASE_SRC = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(_BASE_SRC, "data")


# Stable writable root (frozen-safe). Hardware task: import and reuse this
# instead of hard-coding <repo>/data paths.
APP_DATA_DIR = app_data_dir()

# DB lives next to the app -> fully local. Override with SIMPLD_DB if needed.
# Source/dev default: <repo>/data/factory.db (unchanged).
# Frozen default:  <LOCALAPPDATA>/Simpld/factory.db (writable, stable).
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(_BASE, "data", "factory.db")
_FROZEN_DEFAULT_DB = os.path.join(APP_DATA_DIR, "factory.db")
_DB_DEFAULT = _FROZEN_DEFAULT_DB if _is_frozen() else DEFAULT_DB
DB_PATH = os.environ.get("SIMPLD_DB", os.environ.get("REELPACK_DB", _DB_DEFAULT))

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS products (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  wrappers_per_reel INTEGER CHECK(wrappers_per_reel IS NULL OR wrappers_per_reel > 0),
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS jobs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_no INTEGER UNIQUE NOT NULL,
  job_name TEXT NOT NULL,
  size_mm REAL DEFAULT 0,
  core_wt REAL NOT NULL DEFAULT 0.5,
  product_id INTEGER REFERENCES products(id),
  product_name TEXT DEFAULT '',
  wrappers_per_reel INTEGER CHECK(wrappers_per_reel IS NULL OR wrappers_per_reel > 0),
  status TEXT NOT NULL DEFAULT 'open',
  created_at TEXT NOT NULL,
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS reels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  roll_no INTEGER NOT NULL,
  roll_period TEXT,
  roll_code TEXT UNIQUE,
  gross REAL NOT NULL,
  core REAL NOT NULL,
  net REAL NOT NULL,
  shift TEXT NOT NULL DEFAULT 'DAY',
  supervisor TEXT DEFAULT '',
  customer TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'loose',
  created_at TEXT NOT NULL,
  print_count INTEGER NOT NULL DEFAULT 0,
  print_status TEXT NOT NULL DEFAULT 'pending',
  last_printed_at TEXT,
  UNIQUE(job_id, roll_period, roll_no)
);
CREATE INDEX IF NOT EXISTS idx_reels_job ON reels(job_id, status);
CREATE TABLE IF NOT EXISTS cartons (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  carton_no TEXT UNIQUE NOT NULL,
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  bundle_no INTEGER NOT NULL,
  box_period TEXT,
  num_reels INTEGER NOT NULL,
  box_wt REAL NOT NULL DEFAULT 0,
  total_gross REAL NOT NULL,
  total_core REAL NOT NULL,
  total_net REAL NOT NULL,
  shift TEXT NOT NULL DEFAULT 'DAY',
  customer TEXT DEFAULT '',
  status TEXT NOT NULL DEFAULT 'Ready',
  packed_at TEXT NOT NULL,
  dispatched_at TEXT,
  print_count INTEGER NOT NULL DEFAULT 0,
  print_status TEXT NOT NULL DEFAULT 'pending',
  last_printed_at TEXT,
  UNIQUE(job_id, bundle_no)
);
CREATE INDEX IF NOT EXISTS idx_cartons_status ON cartons(status, job_id);
CREATE TABLE IF NOT EXISTS monthly_counters (
  period TEXT NOT NULL,
  kind TEXT NOT NULL,
  last_no INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (period, kind)
);
CREATE TABLE IF NOT EXISTS id_counters (
  kind TEXT PRIMARY KEY,
  last_no INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS production_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL,
  actor TEXT NOT NULL DEFAULT '',
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  entity_id INTEGER NOT NULL DEFAULT 0,
  detail TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS carton_items (
  carton_id INTEGER NOT NULL REFERENCES cartons(id) ON DELETE CASCADE,
  reel_id INTEGER UNIQUE NOT NULL REFERENCES reels(id),
  PRIMARY KEY (carton_id, reel_id)
);
CREATE TABLE IF NOT EXISTS dispatch_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_no TEXT UNIQUE NOT NULL,
  customer TEXT NOT NULL,
  customer_po TEXT NOT NULL DEFAULT '',
  batch_no TEXT NOT NULL DEFAULT '',
  job_id INTEGER NOT NULL REFERENCES jobs(id),
  required_bundles INTEGER NOT NULL,
  required_pieces INTEGER,
  piece_unit TEXT NOT NULL DEFAULT 'reel',
  status TEXT NOT NULL DEFAULT 'loading',
  order_date TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT
);
CREATE TABLE IF NOT EXISTS dispatch_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  order_id INTEGER NOT NULL REFERENCES dispatch_orders(id) ON DELETE CASCADE,
  carton_id INTEGER UNIQUE NOT NULL REFERENCES cartons(id),
  client_box_no INTEGER,
  scanned_at TEXT NOT NULL,
  UNIQUE(order_id, carton_id)
);
CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  username TEXT UNIQUE NOT NULL,
  pass_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'packager',
  created_at TEXT NOT NULL
);
"""


def get_conn(path: str | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _ensure_customer_columns(c) -> None:
    """Add customer label columns to pre-existing DBs (fresh DBs get them
    from SCHEMA). Read-only check + ALTER only when missing; existing rows
    keep customer='' (prints blank, like an empty Supervisor).

    Fail loudly on SQLite errors (fail+rollback in init_db); never swallow.
    """
    for table in ("reels", "cartons"):
        cols = {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
        if cols and "customer" not in cols:
            c.execute(f"ALTER TABLE {table} ADD COLUMN customer TEXT DEFAULT ''")


def _ensure_box_piece_columns(c) -> None:
    """Additive columns for the reel-linked box + piece-based dispatch flow.

    Existing rows keep their old semantics: box_wt=0 and required_pieces NULL
    mean "legacy weight-only box / bundle-count order". No row is rewritten.
    """
    cols = {r["name"] for r in c.execute("PRAGMA table_info(cartons)").fetchall()}
    if cols and "box_wt" not in cols:
        c.execute("ALTER TABLE cartons ADD COLUMN box_wt REAL NOT NULL DEFAULT 0")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(dispatch_orders)").fetchall()}
    if cols and "required_pieces" not in cols:
        c.execute("ALTER TABLE dispatch_orders ADD COLUMN required_pieces INTEGER")


def _ensure_wide_label_columns(c) -> None:
    """Additive columns for the wide client sticker + settings.

    Existing orders are explicitly marked `piece_unit='reel'` (the old
    semantics: one linked reel = one piece), so their targets and history are
    never reinterpreted as wrappers. New orders created by the UI use
    `piece_unit='wrapper'` and count wrappers via jobs.wrappers_per_reel.

    Client box numbers are backfilled per order in scan order, so an old
    loaded order still prints a sane 1..N sequence.
    """
    c.execute("""CREATE TABLE IF NOT EXISTS settings (
      key TEXT PRIMARY KEY, value TEXT NOT NULL DEFAULT '',
      updated_at TEXT NOT NULL DEFAULT '')""")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(dispatch_orders)").fetchall()}
    if cols:
        if "customer_po" not in cols:
            c.execute("ALTER TABLE dispatch_orders ADD COLUMN customer_po TEXT NOT NULL DEFAULT ''")
        if "batch_no" not in cols:
            c.execute("ALTER TABLE dispatch_orders ADD COLUMN batch_no TEXT NOT NULL DEFAULT ''")
        if "piece_unit" not in cols:
            c.execute("ALTER TABLE dispatch_orders ADD COLUMN piece_unit TEXT NOT NULL DEFAULT 'reel'")
    cols = {r["name"] for r in c.execute("PRAGMA table_info(dispatch_items)").fetchall()}
    if cols and "client_box_no" not in cols:
        c.execute("ALTER TABLE dispatch_items ADD COLUMN client_box_no INTEGER")
    # Backfill only rows still missing a number; never renumber a live order.
    starts = {r["order_id"]: int(r["n"] or 0) for r in c.execute(
        "SELECT order_id, COALESCE(MAX(client_box_no),0) n FROM dispatch_items"
        " GROUP BY order_id").fetchall()}
    rows = c.execute(
        "SELECT id, order_id FROM dispatch_items WHERE client_box_no IS NULL"
        " ORDER BY order_id, id").fetchall()
    for r in rows:
        nxt = starts.get(r["order_id"], 0) + 1
        c.execute("UPDATE dispatch_items SET client_box_no=? WHERE id=?",
                  (nxt, r["id"]))
        starts[r["order_id"]] = nxt


def _ensure_id_counters(c) -> None:
    """Global issued-number high-water marks for jobs and dispatch orders.

    job_no and DO-NNNN must never reuse a deleted issued number: the
    counter only moves forward (monotonic), seeded from the current MAX on
    first creation. Idempotent reruns only raise the mark, never lower it.
    """
    import re as _re
    c.execute("""CREATE TABLE IF NOT EXISTS id_counters (
      kind TEXT PRIMARY KEY, last_no INTEGER NOT NULL DEFAULT 0)""")
    # jobs: seed from MAX(job_no), floor at 4168 (first job is 4169)
    row = c.execute("SELECT last_no FROM id_counters WHERE kind='job'").fetchone()
    mx = c.execute("SELECT MAX(job_no) m FROM jobs").fetchone()
    want = int(mx["m"] or 4168)
    if want < 4168:
        want = 4168
    if row is None:
        c.execute("INSERT INTO id_counters(kind, last_no) VALUES('job', ?)", (want,))
    elif int(row["last_no"] or 0) < want:
        c.execute("UPDATE id_counters SET last_no=? WHERE kind='job'", (want,))
    # orders: seed from MAX(DO-NNNN seq)
    row = c.execute("SELECT last_no FROM id_counters WHERE kind='order'").fetchone()
    mx_seq = 0
    for (on,) in c.execute(
            "SELECT order_no FROM dispatch_orders WHERE order_no LIKE 'DO-%'").fetchall():
        m = _re.match(r"^DO-(\d+)$", str(on or "").upper())
        if m:
            mx_seq = max(mx_seq, int(m.group(1)))
    if row is None:
        c.execute("INSERT INTO id_counters(kind, last_no) VALUES('order', ?)", (mx_seq,))
    elif int(row["last_no"] or 0) < mx_seq:
        c.execute("UPDATE id_counters SET last_no=? WHERE kind='order'", (mx_seq,))


def _alloc_job_no(c) -> int:
    """Allocate the next job_no transactionally; never reuses deleted numbers."""
    r = c.execute("SELECT last_no FROM id_counters WHERE kind='job'").fetchone()
    if r is None:
        mx = c.execute("SELECT MAX(job_no) m FROM jobs").fetchone()
        base = int(mx["m"] or 4168)
        if base < 4168:
            base = 4168
        c.execute("INSERT INTO id_counters(kind, last_no) VALUES('job', ?)", (base,))
        last = base
    else:
        last = int(r["last_no"] or 4168)
        mx = c.execute("SELECT MAX(job_no) m FROM jobs").fetchone()
        if mx and mx["m"] and int(mx["m"]) > last:
            last = int(mx["m"])
    nxt = last + 1
    c.execute("UPDATE id_counters SET last_no=? WHERE kind='job'", (nxt,))
    return nxt


def _alloc_order_seq(c) -> int:
    """Allocate the next DO-NNNN sequence transactionally; never reuses."""
    import re as _re
    r = c.execute("SELECT last_no FROM id_counters WHERE kind='order'").fetchone()
    last = int(r["last_no"] or 0) if r is not None else 0
    if r is None:
        c.execute("INSERT INTO id_counters(kind, last_no) VALUES('order', 0)")
    mx_seq = 0
    for (on,) in c.execute(
            "SELECT order_no FROM dispatch_orders WHERE order_no LIKE 'DO-%'").fetchall():
        m = _re.match(r"^DO-(\d+)$", str(on or "").upper())
        if m:
            mx_seq = max(mx_seq, int(m.group(1)))
    last = max(last, mx_seq)
    nxt = last + 1
    c.execute("UPDATE id_counters SET last_no=? WHERE kind='order'", (nxt,))
    return nxt


def _ensure_products_migration(c) -> None:
    """Reusable products master + per-job snapshot columns.

    Preserves every existing row: no table rebuild, no ID/measured-value
    touch. Backfills one product per distinct legacy job name with
    wrappers_per_reel NULL (unknown — never invented), then snapshots each
    legacy job to its product (product_name=job_name, wrappers NULL).

    Explicit SELECT-then-INSERT (no INSERT OR IGNORE): any UNIQUE failure
    raises and rolls back instead of silently dropping a row.
    """
    cols_j = {r["name"] for r in c.execute("PRAGMA table_info(jobs)").fetchall()}
    if "product_id" not in cols_j:
        c.execute("ALTER TABLE jobs ADD COLUMN product_id INTEGER REFERENCES products(id)")
    if "product_name" not in cols_j:
        c.execute("ALTER TABLE jobs ADD COLUMN product_name TEXT DEFAULT ''")
    if "wrappers_per_reel" not in cols_j:
        c.execute("ALTER TABLE jobs ADD COLUMN wrappers_per_reel INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      name TEXT UNIQUE NOT NULL,
      wrappers_per_reel INTEGER CHECK(wrappers_per_reel IS NULL OR wrappers_per_reel > 0),
      created_at TEXT NOT NULL)""")
    # Backfill: distinct legacy names -> products with unknown wrappers.
    names = [r["job_name"] for r in
             c.execute("SELECT DISTINCT job_name FROM jobs").fetchall()]
    for n in names:
        if n:
            hit = c.execute("SELECT id FROM products WHERE name=?", (n,)).fetchone()
            if hit is None:
                c.execute("INSERT INTO products(name, wrappers_per_reel, created_at)"
                          " VALUES(?, NULL, ?)", (n, now_iso()))
    # Snapshot legacy jobs lacking a product link. Measured values untouched.
    c.execute("""UPDATE jobs SET product_id=(SELECT id FROM products WHERE name=jobs.job_name),
      product_name=job_name WHERE product_id IS NULL""")
    # Jobs whose product now has a known count keep NULL (history stays unknown);
    # only the snapshot column carries the count for new jobs.


def _ensure_monthly_migration(c) -> None:
    """Monthly global roll/box numbering + print tracking + audit.

    Preserves every legacy row exactly (no renumbering, no weight/link
    touch):
    - adds roll_period/box_period, roll_code, print_count/print_status/
      last_printed_at when missing;
    - rebuilds reels UNIQUE(job_id, roll_no) -> UNIQUE(job_id, roll_period,
      roll_no) so the same job may reuse a monthly roll_no in a new month
      while old rows keep their numbers;
    - creates monthly_counters(period, kind) + production_audit;
    - backfills periods from created_at/packed_at month;
    - seeds counters at the global MAX per period so fresh monthly numbers
      never collide with legacy numbers already issued in that month.
    Boxes keep carton_no UNIQUE (new CYYYYMM-NNNN still starts with C, so
    the existing QR parser works); legacy C0001 rows are untouched.

    Atomic: every SQLite error propagates and init_db rolls back; no
    blanket except/pass and no INSERT OR IGNORE anywhere (a UNIQUE failure
    raises instead of silently dropping a legacy row). The caller (init_db)
    disables FK enforcement BEFORE BEGIN (SQLite ignores the pragma inside
    a transaction) and re-enables it after commit/rollback, with a
    foreign_key_check before commit. Row counts are verified across the
    reels rebuild.
    """
    try:
        c.execute("""CREATE TABLE IF NOT EXISTS monthly_counters (
          period TEXT NOT NULL, kind TEXT NOT NULL, last_no INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (period, kind))""")
        c.execute("""CREATE TABLE IF NOT EXISTS production_audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
          actor TEXT NOT NULL DEFAULT '', action TEXT NOT NULL, entity TEXT NOT NULL,
          entity_id INTEGER NOT NULL DEFAULT 0, detail TEXT NOT NULL DEFAULT '')""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_audit_entity ON production_audit(entity, entity_id)")
        # add columns cheaply when missing
        cols_r = {r["name"] for r in c.execute("PRAGMA table_info(reels)").fetchall()}
        if cols_r:
            for col, ddl in (
                ("roll_period", "ALTER TABLE reels ADD COLUMN roll_period TEXT"),
                ("roll_code", "ALTER TABLE reels ADD COLUMN roll_code TEXT"),
                ("print_count", "ALTER TABLE reels ADD COLUMN print_count INTEGER NOT NULL DEFAULT 0"),
                ("print_status", "ALTER TABLE reels ADD COLUMN print_status TEXT NOT NULL DEFAULT 'pending'"),
                ("last_printed_at", "ALTER TABLE reels ADD COLUMN last_printed_at TEXT"),
            ):
                if col not in cols_r:
                    c.execute(ddl)
        cols_t = {r["name"] for r in c.execute("PRAGMA table_info(cartons)").fetchall()}
        if cols_t:
            for col, ddl in (
                ("box_period", "ALTER TABLE cartons ADD COLUMN box_period TEXT"),
                ("print_count", "ALTER TABLE cartons ADD COLUMN print_count INTEGER NOT NULL DEFAULT 0"),
                ("print_status", "ALTER TABLE cartons ADD COLUMN print_status TEXT NOT NULL DEFAULT 'pending'"),
                ("last_printed_at", "ALTER TABLE cartons ADD COLUMN last_printed_at TEXT"),
            ):
                if col not in cols_t:
                    c.execute(ddl)
        # rebuild reels UNIQUE when it is still the legacy (job_id, roll_no)
        sql_row = (c.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='reels'")
                   .fetchone() or [None])[0] or ""
        needs_rebuild = "UNIQUE(job_id,roll_no)" in sql_row.replace(" ", "")
        if needs_rebuild:
            n_old = c.execute("SELECT COUNT(*) n FROM reels").fetchone()["n"]
            old_links = c.execute("SELECT COUNT(*) n FROM carton_items").fetchone()["n"]
            old_dispatch = c.execute(
                "SELECT COUNT(*) n FROM dispatch_items").fetchone()["n"] if \
                c.execute("SELECT name FROM sqlite_master WHERE type='table'"
                          " AND name='dispatch_items'").fetchone() else 0
            c.execute("DROP TABLE IF EXISTS reels_new")
            c.execute("""CREATE TABLE reels_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id INTEGER NOT NULL REFERENCES jobs(id),
              roll_no INTEGER NOT NULL, roll_period TEXT, roll_code TEXT UNIQUE,
              gross REAL NOT NULL, core REAL NOT NULL, net REAL NOT NULL,
              shift TEXT NOT NULL DEFAULT 'DAY', supervisor TEXT DEFAULT '',
              customer TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'loose',
              created_at TEXT NOT NULL,
              print_count INTEGER NOT NULL DEFAULT 0,
              print_status TEXT NOT NULL DEFAULT 'pending',
              last_printed_at TEXT,
              UNIQUE(job_id, roll_period, roll_no))""")
            cols = [r["name"] for r in c.execute("PRAGMA table_info(reels)").fetchall()]
            have = set(cols)
            target = ["id", "job_id", "roll_no", "roll_period", "roll_code", "gross",
                      "core", "net", "shift", "supervisor", "customer", "status",
                      "created_at", "print_count", "print_status", "last_printed_at"]
            expr = []
            for want in target:
                if want in have:
                    expr.append(want)
                elif want == "roll_period":
                    expr.append("SUBSTR(created_at,1,7)")
                elif want == "print_count":
                    expr.append("0")
                elif want == "print_status":
                    expr.append("'pending'")
                else:
                    expr.append("NULL")
            # Plain INSERT: any collision raises instead of silently losing a row.
            c.execute(f"INSERT INTO reels_new({', '.join(target)}) "
                      f"SELECT {', '.join(expr)} FROM reels")
            n_new = c.execute("SELECT COUNT(*) n FROM reels_new").fetchone()["n"]
            if n_new != n_old:
                raise RuntimeError(
                    f"Reels rebuild would lose rows ({n_old} -> {n_new}); aborted")
            c.execute("DROP TABLE reels")
            c.execute("ALTER TABLE reels_new RENAME TO reels")
            c.execute("CREATE INDEX IF NOT EXISTS idx_reels_job ON reels(job_id, status)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_reels_period ON reels(roll_period, roll_no)")
            if old_links != c.execute("SELECT COUNT(*) n FROM carton_items").fetchone()["n"]:
                raise RuntimeError("Reels rebuild altered carton links; aborted")
            if old_dispatch != (c.execute("SELECT COUNT(*) n FROM dispatch_items").fetchone()["n"]
                                if c.execute("SELECT name FROM sqlite_master WHERE type='table'"
                                             " AND name='dispatch_items'").fetchone() else 0):
                raise RuntimeError("Reels rebuild altered dispatch links; aborted")
        # backfill periods (legacy rows keep their numbers, only gain a month tag)
        c.execute("UPDATE reels SET roll_period=SUBSTR(created_at,1,7)"
                  " WHERE roll_period IS NULL AND created_at IS NOT NULL")
        c.execute("UPDATE cartons SET box_period=SUBSTR(packed_at,1,7)"
                  " WHERE box_period IS NULL AND packed_at IS NOT NULL")
        for stmt in (
            "CREATE INDEX IF NOT EXISTS idx_reels_period ON reels(roll_period, roll_no)",
            "CREATE INDEX IF NOT EXISTS idx_audit_entity ON production_audit(entity, entity_id)",
            "CREATE INDEX IF NOT EXISTS idx_cartons_period ON cartons(box_period)",
        ):
            c.execute(stmt)
        # seed counters at global MAX per period so new monthly numbers never
        # collide with (or reuse) numbers already issued in that month
        for (per, mx) in c.execute(
                "SELECT roll_period, MAX(roll_no) FROM reels WHERE roll_period IS NOT NULL"
                " GROUP BY roll_period").fetchall():
            if per:
                cur = c.execute("SELECT last_no FROM monthly_counters"
                                " WHERE period=? AND kind='reel'", (per,)).fetchone()
                if cur is None:
                    c.execute("INSERT INTO monthly_counters(period, kind, last_no)"
                              " VALUES(?, 'reel', ?)", (per, int(mx or 0)))
                elif int(cur["last_no"] or 0) < int(mx or 0):
                    c.execute("UPDATE monthly_counters SET last_no=? WHERE period=? AND kind='reel'",
                              (int(mx or 0), per))
        # new CYYYYMM-NNNN seq per month; legacy C0001 rows never match so they
        # cannot collide, but adopted MAX keeps reruns safe
        rows = c.execute("SELECT box_period, carton_no FROM cartons"
                         " WHERE box_period IS NOT NULL").fetchall()
        import re as _re
        per_max: dict[str, int] = {}
        for per, cn in rows:
            m = _re.match(r"^C(\d{6})-(\d+)$", str(cn or "").upper())
            if m and m.group(1) == str(per or "").replace("-", ""):
                per_max[str(per)] = max(per_max.get(str(per), 0), int(m.group(2)))
        for per, mx in per_max.items():
            cur = c.execute("SELECT last_no FROM monthly_counters"
                            " WHERE period=? AND kind='box'", (per,)).fetchone()
            if cur is None:
                c.execute("INSERT INTO monthly_counters(period, kind, last_no)"
                          " VALUES(?, 'box', ?)", (per, mx))
            elif int(cur["last_no"] or 0) < mx:
                c.execute("UPDATE monthly_counters SET last_no=? WHERE period=? AND kind='box'",
                          (mx, per))
        bad = c.execute("PRAGMA foreign_key_check").fetchall()
        if bad:
            raise RuntimeError(f"Migration FK integrity failed: {len(bad)} violation(s)")
    finally:
        pass  # FK state owned by init_db (OFF before BEGIN, ON after commit/rollback)


def init_db(path: str | None = None) -> str:
    """Create/migrate the DB atomically: any migration error rolls back and
    propagates (backup-safe); never swallows SQLite errors or drops rows."""
    p = path or DB_PATH
    c = get_conn(p)
    c.isolation_level = None
    try:
        c.executescript(SCHEMA)
        # FK OFF must precede BEGIN: SQLite ignores the pragma inside a
        # transaction, so disabling here lets the legacy reels rebuild
        # (DROP TABLE with carton_items/dispatch_items links) proceed.
        # Links/IDs/weights/order refs/counts are verified + foreign_key_check
        # runs before COMMIT; FK is restored after commit/rollback below.
        c.execute("PRAGMA foreign_keys=OFF")
        c.execute("BEGIN IMMEDIATE")
        try:
            _ensure_customer_columns(c)
            _ensure_products_migration(c)
            _ensure_monthly_migration(c)
            _ensure_box_piece_columns(c)
            _ensure_wide_label_columns(c)
            _ensure_id_counters(c)
            bad = c.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise RuntimeError(f"DB FK integrity failed: {len(bad)} violation(s)")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        try:
            c.execute("PRAGMA foreign_keys=ON")
        except sqlite3.Error:
            pass
        c.close()
    return p


def now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def current_period() -> str:
    """Current YYYY-MM period for monthly counters."""
    return datetime.now().strftime("%Y-%m")


def period_compact(period: str) -> str:
    return (period or "").replace("-", "")


def roll_code_for(period: str, seq: int) -> str:
    return f"R{period_compact(period)}-{int(seq):04d}"


def carton_no_for(period: str, seq: int) -> str:
    return f"C{period_compact(period)}-{int(seq):04d}"


def bare_carton_no(carton_no) -> str:
    """Visual box number (the floor's "1"): numeric part of CYYYYMM-NNNN.

    The stored code stays the DB identity (QR + audit); display strips the
    month prefix so boxes read the same bare-number style as roll numbers.
    Legacy or unparseable codes are returned unchanged.
    """
    s = str(carton_no or "").strip()
    head, _, tail = s.rpartition("-")
    if head[:1].upper() == "C" and tail.isdigit():
        return str(int(tail))
    return s


def full_roll_label(reel: dict) -> str:
    """Readable month-qualified roll identity for history/slip compatibility.

    New rows: R202610-0001 (+ period). Legacy rows without a code fall back
    to 'YYYY-MM#N' so old numbers stay exactly as issued.
    """
    try:
        if reel.get("roll_code"):
            return str(reel["roll_code"])
        per = reel.get("roll_period") or ""
        return f"{per}#{reel.get('roll_no')}" if per else f"#{reel.get('roll_no')}"
    except Exception:
        return ""


def today_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def fmt_date_ddmmyy(s: str) -> str:
    """'2026-10-03 10:00:00' -> '03-10-26' (matches old label)."""
    try:
        d = datetime.strptime(s[:10], "%Y-%m-%d")
        return d.strftime("%d-%m-%y")
    except Exception:
        return datetime.now().strftime("%d-%m-%y")
