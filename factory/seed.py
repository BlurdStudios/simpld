"""Production bootstrap (no sample data) + explicit test fixture helper.

Production startup MUST NOT create sample jobs/reels/cartons/orders: the
floor DB starts empty and the administrator creates the first login via the
login dialog. Test/demo fixture data lives ONLY in seed_sample_data(),
which is never called by run.py or MainWindow.
"""
from __future__ import annotations
from .db import get_conn, init_db


def ensure_production_db(path: str | None = None) -> str:
    """Production bootstrap: create/migrate schema only. No users, no jobs,
    no reels/cartons/orders. First-run admin setup happens in the login
    dialog (administrator chooses the password)."""
    return init_db(path)


def seed(path: str | None = None):
    """Back-compat production entry: schema only (no sample data).

    Older callers expected demo jobs; they now get an empty production DB.
    Tests needing fixtures must call seed_sample_data() explicitly.
    """
    return ensure_production_db(path)


def seed_sample_data(path: str | None = None, admin_username: str = "",
                     admin_password: str = "") -> dict:
    """Explicit TEST-ONLY fixture: realistic sample jobs/reels/cartons/order.

    Never called by production startup. Tests bootstrap the DB and install a
    default admin login via the tests/_auth_helper helper module (its default-
    login bootstrap), then
    pass admin_username/admin_password here. Creates the schema, authenticates
    an admin session on THIS db, then writes the old demo fixture. Returns
    {"jobs": [...], "order": ...}.
    """
    from . import auth as A
    from . import services as S
    init_db(path)
    admin = A.authenticate(admin_username, admin_password, path)
    if admin is None:
        raise RuntimeError("seed_sample_data: bootstrap the DB and pass admin credentials")
    c = get_conn(path)
    try:
        n = c.execute("SELECT COUNT(*) n FROM jobs").fetchone()["n"]
        if n:
            return {"jobs": S.list_jobs(path=path), "order": None}
    finally:
        c.close()
    j1 = S.create_job("APPY FIZZ-160ML (SEP.28) BODY", size_mm=160, core_wt=0.280, path=path)
    for g in (11.380, 11.120, 10.960, 12.050, 11.240, 11.510):
        S.create_reel(j1["id"], gross=g, core=0.280, shift="DAY", supervisor="RAMESH", path=path)
    loose = S.loose_reels(j1["id"], path=path)
    if len(loose) >= 2:
        S.pack_carton(j1["id"], [loose[0]["id"], loose[1]["id"]], shift="DAY", path=path)
    loose = S.loose_reels(j1["id"], path=path)
    if len(loose) >= 2:
        S.pack_carton(j1["id"], [loose[0]["id"], loose[1]["id"]], shift="DAY", path=path)
    order = None
    try:
        order = S.create_dispatch_order("PARLE AGRO HYD", j1["id"], 4, path=path)
    except ValueError:
        pass
    j2 = S.create_job("BAILLEY-1LTR (SEP.36C)", size_mm=200, core_wt=0.720, path=path)
    for g in (19.060, 19.340, 19.440, 18.720, 17.820):
        S.create_reel(j2["id"], gross=g, core=0.720, shift="DAY", supervisor="", path=path)
    return {"jobs": [j1, j2], "order": order}


if __name__ == "__main__":
    print("seed.py is production-safe (schema only). Tests: seed_sample_data(path).")
