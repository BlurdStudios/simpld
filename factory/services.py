"""Business rules. Keeps PACKAGING separate from DISPATCH.

Production: Job -> Reels (single sticker, NO QR) -> Cartons (bundle sticker WITH QR) -> Ready Stock
Dispatch: Customer Order -> Scan Ready Cartons -> Load -> Dispatch
"""
from __future__ import annotations
import base64
import io
import json
import math
import sqlite3
from datetime import datetime
from .db import (get_conn, now_iso, today_iso, current_period, roll_code_for,
                 carton_no_for, bare_carton_no, full_roll_label, _alloc_job_no,
                 _alloc_order_seq)
from . import auth as _auth

START_JOB_NO = 4169  # old system was at 4168 (see photos)
QR_PREFIX = "VASAVI"  # QR payload format: VASAVI:<carton_no>


def qr_payload(carton_no: str) -> str:
    return f"{QR_PREFIX}:{carton_no}"


# ---------- authenticated RBAC (DB-bound sessions; no caller role dicts) ----------
# Every mutation below resolves its actor via auth.require_permission() on the
# SAME DB path it writes. Caller-supplied `actor` kwargs are retained only for
# signature compatibility and are NEVER trusted for authorization or audit.
def _prod_actor(path: str | None) -> dict:
    return _auth.require_permission("production.create", path)


def _reweigh_actor(path: str | None) -> dict:
    return _auth.require_permission("production.reweigh", path)


def _print_actor(path: str | None) -> dict:
    return _auth.require_permission("production.print", path)


def _product_actor(path: str | None) -> dict:
    return _auth.require_permission("product.manage", path)


def _job_actor(path: str | None) -> dict:
    return _auth.require_permission("job.manage", path)


def _dispatch_actor(path: str | None) -> dict:
    return _auth.require_permission("dispatch.mutate", path)


def _delete_actor(path: str | None) -> dict:
    return _auth.require_permission("delete", path)


def _settings_actor(path: str | None) -> dict:
    return _auth.require_permission("settings.manage", path)


def _finite_weight(value, label: str) -> float:
    try:
        w = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} must be a number (read the scale)")
    if not math.isfinite(w):
        raise ValueError(f"{label} must be a finite number (read the scale)")
    return w


def _audit(c: sqlite3.Connection, actor: dict | None, action: str,
           entity: str, entity_id: int, detail: str = "") -> None:
    """Persist one audit row inside the caller's transaction.

    Fail loudly on SQLite errors so the caller rolls back atomically; never
    swallowed. Actor is always the session-resolved dict, never caller data.
    """
    name = ""
    try:
        name = str((actor or {}).get("username") or "")
    except Exception:
        name = ""
    c.execute("INSERT INTO production_audit(created_at, actor, action, entity, entity_id, detail)"
              " VALUES(?,?,?,?,?,?)",
              (now_iso(), name, action, entity,
               int(entity_id or 0), str(detail or "")))


def _alloc_seq(c: sqlite3.Connection, period: str, kind: str) -> int:
    """Increment monthly counter inside the caller's BEGIN IMMEDIATE txn."""
    r = c.execute("SELECT last_no FROM monthly_counters WHERE period=? AND kind=?",
                  (period, kind)).fetchone()
    if r is None:
        c.execute("INSERT INTO monthly_counters(period, kind, last_no) VALUES(?,?,1)",
                  (period, kind))
        return 1
    nxt = int(r["last_no"] or 0) + 1
    c.execute("UPDATE monthly_counters SET last_no=? WHERE period=? AND kind=?",
              (nxt, period, kind))
    return nxt


def _alloc_prod_seq(c: sqlite3.Connection, period: str, product_id: int, kind: str) -> int:
    """Per-product monthly counter (P0): each product restarts at 1 each month.

    Runs inside the caller's BEGIN IMMEDIATE txn. Never reuses deleted
    numbers (monotonic per product+month). product_id None (legacy job
    without product) falls back to 0 bucket so allocation never fails.
    """
    pid = int(product_id) if product_id is not None else 0
    r = c.execute("SELECT last_no FROM monthly_product_counters"
                  " WHERE period=? AND product_id=? AND kind=?",
                  (period, pid, kind)).fetchone()
    if r is None:
        # Seed from existing MAX prod_seq so legacy backfill + new writes
        # never collide.
        if kind == "reel":
            m = c.execute(
                "SELECT MAX(r.prod_seq) m FROM reels r JOIN jobs j ON j.id=r.job_id"
                " WHERE r.roll_period=? AND COALESCE(j.product_id,0)=?",
                (period, pid)).fetchone()
        else:
            m = c.execute(
                "SELECT MAX(ct.prod_seq) m FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
                " WHERE ct.box_period=? AND COALESCE(j.product_id,0)=?",
                (period, pid)).fetchone()
        base = int(m["m"] or 0) if m and m["m"] else 0
        nxt = base + 1
        c.execute("INSERT INTO monthly_product_counters(period, product_id, kind, last_no)"
                  " VALUES(?,?,?,?)", (period, pid, kind, nxt))
        return nxt
    nxt = int(r["last_no"] or 0) + 1
    c.execute("UPDATE monthly_product_counters SET last_no=? WHERE period=? AND product_id=? AND kind=?",
              (nxt, period, pid, kind))
    return nxt


def _product_id_for_job(c: sqlite3.Connection, job_id: int) -> int | None:
    r = c.execute("SELECT product_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not r:
        return None
    return r["product_id"]


def predict_next_reel_seq(period: str | None = None, path: str | None = None,
                          product_id: int | None = None, job_id: int | None = None) -> int:
    """Next reel seq. Global when no product/job given (legacy preview);
    per-product 1..N when product_id or job_id is given (P0 sticker preview)."""
    per = period or current_period()
    c = get_conn(path)
    try:
        pid: int | None = product_id
        if pid is None and job_id is not None:
            r = c.execute("SELECT product_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            pid = r["product_id"] if r else None
        if pid is not None:
            r = c.execute("SELECT last_no FROM monthly_product_counters"
                          " WHERE period=? AND product_id=? AND kind='reel'",
                          (per, int(pid))).fetchone()
            base = int(r["last_no"] or 0) if r else 0
            m = c.execute(
                "SELECT MAX(r.prod_seq) m FROM reels r JOIN jobs j ON j.id=r.job_id"
                " WHERE r.roll_period=? AND COALESCE(j.product_id,0)=?",
                (per, int(pid))).fetchone()
            if m and m["m"]:
                base = max(base, int(m["m"]))
            return base + 1
        r = c.execute("SELECT last_no FROM monthly_counters WHERE period=? AND kind='reel'",
                      (per,)).fetchone()
        base = int(r["last_no"] or 0) if r else 0
        # never collide with legacy numbers already issued in this month
        m = c.execute("SELECT MAX(roll_no) m FROM reels WHERE roll_period=?",
                      (per,)).fetchone()
        if m and m["m"]:
            base = max(base, int(m["m"]))
        return base + 1
    finally:
        c.close()


def predict_next_box_seq(period: str | None = None, path: str | None = None,
                         product_id: int | None = None, job_id: int | None = None) -> int:
    import re as _re
    per = period or current_period()
    c = get_conn(path)
    try:
        pid: int | None = product_id
        if pid is None and job_id is not None:
            r = c.execute("SELECT product_id FROM jobs WHERE id=?", (job_id,)).fetchone()
            pid = r["product_id"] if r else None
        if pid is not None:
            r = c.execute("SELECT last_no FROM monthly_product_counters"
                          " WHERE period=? AND product_id=? AND kind='box'",
                          (per, int(pid))).fetchone()
            base = int(r["last_no"] or 0) if r else 0
            m = c.execute(
                "SELECT MAX(ct.prod_seq) m FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
                " WHERE ct.box_period=? AND COALESCE(j.product_id,0)=?",
                (per, int(pid))).fetchone()
            if m and m["m"]:
                base = max(base, int(m["m"]))
            return base + 1
        r = c.execute("SELECT last_no FROM monthly_counters WHERE period=? AND kind='box'",
                      (per,)).fetchone()
        base = int(r["last_no"] or 0) if r else 0
        mx = 0
        for (cn,) in c.execute("SELECT carton_no FROM cartons WHERE box_period=?",
                               (per,)).fetchall():
            mm = _re.match(r"^C(\d{6})-(\d+)$", str(cn or "").upper())
            if mm and mm.group(1) == per.replace("-", ""):
                mx = max(mx, int(mm.group(2)))
        return max(base, mx) + 1
    finally:
        c.close()


def parse_scan(text: str) -> str:
    """USB scanner acts as keyboard wedge + Enter. Accept 'C0007' or 'VASAVI:C0007'."""
    t = (text or "").strip().upper()
    if ":" in t:
        t = t.split(":")[-1].strip()
    return t


# ---------- products (reusable master; jobs snapshot name + wrappers) ----------
def _parse_wrappers(value) -> int:
    """Strict positive integer. 16500 is only a UI placeholder, never assumed."""
    if isinstance(value, bool) or value is None:
        raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500)")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500)")
        value = int(value)
    if isinstance(value, str):
        t = value.strip()
        if not t.isdigit():
            raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500)")
        value = int(t)
    if not isinstance(value, int) or value <= 0:
        raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500)")
    return value


def _parse_wrappers_optional(value) -> int | None:
    """Optional pieces: blank/None means a kg-based product (NULL).

    Returns None for empty input, else the strict positive int. Never
    invents a count — blank stays unknown.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500) or blank for kg")
    if isinstance(value, float):
        if not value.is_integer():
            raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500) or blank for kg")
        value = int(value)
    if isinstance(value, str):
        t = value.strip()
        if t == "":
            return None
        if not t.isdigit():
            raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500) or blank for kg")
        value = int(t)
    if not isinstance(value, int) or value <= 0:
        raise ValueError("Pieces/reel must be a positive whole number (e.g. 16500) or blank for kg")
    return value


def create_product(name: str, wrappers_per_reel: int | None = None, path: str | None = None,
                   actor: dict | None = None, unit: str | None = None) -> dict:
    """Add-once product with an explicit unit: 'pieces' (Appy Fizz: counted)
    or 'kg' (Bailey: weighed only, pieces never apply).

    unit None infers from wrappers (set -> pieces, blank -> kg). Explicit
    unit='kg' with a count, or unit='pieces' without one, is rejected.
    """
    _product_actor(path)
    name = (name or "").strip().upper()
    if not name:
        raise ValueError("Product name required (e.g. APPY FIZZ-160ML (SEP.28) BODY)")
    u = str(unit or "").strip().lower() or None
    if u is not None and u not in ("pieces", "kg"):
        raise ValueError("Unit must be 'pieces' or 'kg'")
    w = _parse_wrappers_optional(wrappers_per_reel)
    if u is None:
        u = "pieces" if w is not None else "kg"
    elif u == "kg" and w is not None:
        raise ValueError("Kg products take no pieces/reel count — leave it blank")
    elif u == "pieces" and w is None:
        raise ValueError("Pieces products need a pieces/reel count (e.g. 16500)")
    c = get_conn(path)
    try:
        try:
            cur = c.execute(
                "INSERT INTO products(name, wrappers_per_reel, unit, created_at) VALUES(?,?,?,?)",
                (name, w, u, now_iso()))
        except sqlite3.IntegrityError:
            raise ValueError(f"Product '{name}' already exists — pick it from the list.")
        c.commit()
        r = c.execute("SELECT * FROM products WHERE id=?", (cur.lastrowid,)).fetchone()
        return dict(r)
    finally:
        c.close()


def list_products(path: str | None = None) -> list[dict]:
    c = get_conn(path)
    try:
        rows = c.execute("SELECT * FROM products ORDER BY name").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def get_product(product_id: int, path: str | None = None) -> dict | None:
    c = get_conn(path)
    try:
        r = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def set_product_wrappers(product_id: int, wrappers_per_reel, path: str | None = None,
                         actor: dict | None = None) -> dict:
    """Set pieces for a migrated-unknown product. Catalog only: historical
    job snapshots keep their NULL and are never rewritten. Kg products
    (Bailey-style) refuse — pieces never apply to them."""
    resolved = _product_actor(path)
    w = _parse_wrappers(wrappers_per_reel)
    c = get_conn(path)
    try:
        r = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
        if not r:
            raise ValueError("Product not found")
        if str(r["unit"] or "") == "kg":
            raise ValueError(f"'{r['name']}' is a kg product — pieces do not apply to it.")
        c.execute("UPDATE products SET wrappers_per_reel=?, unit='pieces' WHERE id=?", (w, product_id))
        c.commit()
        return dict(c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone())
    finally:
        c.close()


def _ensure_product_for_name(job_name: str, c: sqlite3.Connection) -> int | None:
    """Legacy path: keep the catalog consistent without inventing a count."""
    r = c.execute("SELECT id FROM products WHERE name=?", (job_name,)).fetchone()
    if r:
        return r["id"]
    cur = c.execute("INSERT INTO products(name, wrappers_per_reel, created_at)"
                    " VALUES(?, NULL, ?)", (job_name, now_iso()))
    return cur.lastrowid


# ---------- jobs ----------
def next_job_no(c: sqlite3.Connection) -> int:
    """Read-only preview of the next job_no (MAX+1). Allocation uses the
    monotonic id_counters table so deleted numbers are never reused."""
    r = c.execute("SELECT MAX(job_no) m FROM jobs").fetchone()
    m = r["m"] if r and r["m"] else START_JOB_NO - 1
    return int(m) + 1


def create_job(job_name: str, size_mm: float = 0, core_wt: float = 0.5,
               path: str | None = None, actor: dict | None = None) -> dict:
    """Legacy API kept for seed/existing callers/tests.

    Catalog side-effect only: ensures a product row for the name with
    wrappers NULL when unknown — never invents a count. Snapshots
    product_name=job_name and the product's current wrappers (NULL for
    legacy/unknown). Prefer create_job_for_product for new jobs.

    Requires job.manage on the same DB; job_no allocated transactionally
    from the monotonic counter (deleted numbers never reused).
    """
    resolved = _job_actor(path)
    if not (job_name or "").strip():
        raise ValueError("Job name required (e.g. APPY FIZZ-160ML (SEP.28) BODY)")
    cw = _finite_weight(core_wt, "Core weight")
    if cw <= 0:
        raise ValueError("Core weight must be > 0")
    sm = _finite_weight(size_mm or 0, "Size")
    if sm < 0:
        raise ValueError("Size must be >= 0")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            jn = _alloc_job_no(c)
            nm = job_name.strip().upper()
            pid = _ensure_product_for_name(nm, c)
            w = c.execute("SELECT wrappers_per_reel, unit FROM products WHERE id=?", (pid,)).fetchone()
            wv = w["wrappers_per_reel"] if w else None
            uv = (w["unit"] if w and w["unit"] else ("pieces" if wv is not None else "")) or ""
            cur = c.execute(
                "INSERT INTO jobs(job_no, job_name, size_mm, core_wt, product_id,"
                " product_name, wrappers_per_reel, unit, status, created_at)"
                " VALUES(?,?,?,?,?,?,?,?, 'open', ?)",
                (jn, nm, float(sm), float(cw), pid, nm, wv, uv, now_iso()),
            )
            jid = cur.lastrowid
            _audit(c, resolved, "create_job", "job", jid, f"J:{jn} {nm}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return get_job_by_no(jn, path) or {"job_no": jn}
    finally:
        c.close()


def create_job_for_product(product_id: int, core_wt: float = 0.5,
                           path: str | None = None, actor: dict | None = None) -> dict:
    """New packaging job from the products master. Snapshots name + wrappers
    so later catalog edits cannot rewrite history. Core behavior unchanged."""
    resolved = _job_actor(path)
    cw = _finite_weight(core_wt, "Core weight")
    if cw <= 0:
        raise ValueError("Core weight must be > 0")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            p = c.execute("SELECT * FROM products WHERE id=?", (product_id,)).fetchone()
            if not p:
                raise ValueError("Select a product first (or add it once, then reuse it).")
            p = dict(p)
            jn = _alloc_job_no(c)
            cur = c.execute(
                "INSERT INTO jobs(job_no, job_name, size_mm, core_wt, product_id,"
                " product_name, wrappers_per_reel, unit, status, created_at)"
                " VALUES(?,?, 0, ?,?,?,?,?, 'open', ?)",
                (jn, p["name"], float(cw), p["id"], p["name"],
                 p["wrappers_per_reel"], p.get("unit") or "", now_iso()),
            )
            jid = cur.lastrowid
            _audit(c, resolved, "create_job", "job", jid, f"J:{jn} {p['name']}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return get_job_by_no(jn, path) or {"job_no": jn}
    finally:
        c.close()


def get_job_by_no(job_no: int, path: str | None = None) -> dict | None:
    c = get_conn(path)
    try:
        r = c.execute("SELECT * FROM jobs WHERE job_no=?", (job_no,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def list_jobs(status: str | None = None, path: str | None = None) -> list[dict]:
    c = get_conn(path)
    try:
        if status:
            rows = c.execute("SELECT * FROM jobs WHERE status=? ORDER BY job_no DESC", (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM jobs ORDER BY job_no DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def set_job_status(job_id: int, status: str, path: str | None = None,
                   actor: dict | None = None):
    resolved = _job_actor(path)
    if status not in ("open", "closed"):
        raise ValueError("Status must be 'open' or 'closed'")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            j = c.execute("SELECT job_no FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not j:
                raise ValueError("Job not found")
            if status == "closed":
                c.execute("UPDATE jobs SET status='closed', closed_at=? WHERE id=?", (now_iso(), job_id))
            else:
                c.execute("UPDATE jobs SET status='open', closed_at=NULL WHERE id=?", (job_id,))
            _audit(c, resolved, "set_job_status", "job", job_id,
                   f"J:{j['job_no']} -> {status}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


def job_stats(job_id: int, path: str | None = None) -> dict:
    c = get_conn(path)
    try:
        reels = c.execute("SELECT COUNT(*) n, COALESCE(SUM(net),0) w FROM reels WHERE job_id=?", (job_id,)).fetchone()
        loose = c.execute("SELECT COUNT(*) n FROM reels WHERE job_id=? AND status='loose'", (job_id,)).fetchone()
        cart = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_net),0) w FROM cartons WHERE job_id=? AND status='Ready'", (job_id,)).fetchone()
        boxes_all = c.execute("SELECT COUNT(*) n FROM cartons WHERE job_id=?", (job_id,)).fetchone()
        job = c.execute("SELECT target_reels, target_boxes, target_weight_kg, target_pieces,"
                        " wrappers_per_reel FROM jobs WHERE id=?", (job_id,)).fetchone()
        out: dict = {"reels": reels["n"], "reel_wt": round(reels["w"], 3),
                "loose": loose["n"], "ready_cartons": cart["n"], "ready_wt": round(cart["w"], 3),
                "boxes": boxes_all["n"]}
        if job:
            try:
                wpr = int(job["wrappers_per_reel"] or 0)
            except (TypeError, ValueError):
                wpr = 0
            pieces = int(reels["n"] or 0) * wpr if wpr > 0 else None
            out.update({
                "target_reels": job["target_reels"],
                "target_boxes": job["target_boxes"],
                "target_weight_kg": job["target_weight_kg"],
                "target_pieces": job["target_pieces"],
                "pieces": pieces,
                "wrappers_per_reel": job["wrappers_per_reel"],
            })
            # Fulfilment flags (any target set + reached).
            try:
                tr = job["target_reels"]
                out["target_reels_met"] = bool(tr and int(reels["n"] or 0) >= int(tr))
            except (TypeError, ValueError):
                out["target_reels_met"] = False
            try:
                tb = job["target_boxes"]
                out["target_boxes_met"] = bool(tb and int(boxes_all["n"] or 0) >= int(tb))
            except (TypeError, ValueError):
                out["target_boxes_met"] = False
            try:
                tw = job["target_weight_kg"]
                out["target_weight_met"] = bool(tw and float(reels["w"] or 0) >= float(tw))
            except (TypeError, ValueError):
                out["target_weight_met"] = False
            try:
                tp = job["target_pieces"]
                out["target_pieces_met"] = bool(tp and pieces is not None and pieces >= int(tp))
            except (TypeError, ValueError):
                out["target_pieces_met"] = False
            out["any_target_met"] = bool(out.get("target_reels_met") or out.get("target_boxes_met")
                                         or out.get("target_weight_met") or out.get("target_pieces_met"))
            out["any_target_set"] = bool(job["target_reels"] or job["target_boxes"]
                                         or job["target_weight_kg"] or job["target_pieces"])
        return out
    finally:
        c.close()


def set_job_targets(job_id: int, target_reels: int | None = None,
                    target_boxes: int | None = None,
                    target_weight_kg: float | None = None,
                    target_pieces: int | None = None,
                    path: str | None = None, actor: dict | None = None) -> dict:
    """Set packaging targets for a job (any combination; None clears).

    Targets are advisory goals shown with vivid fulfilled state in Packaging;
    they never block weighing/packing. All values must be >0 when given.
    """
    resolved = _job_actor(path)
    def _opt_int(v, label):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            iv = int(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a positive whole number")
        if iv <= 0:
            raise ValueError(f"{label} must be > 0")
        return iv
    def _opt_float(v, label):
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        try:
            fv = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"{label} must be a number > 0")
        if not math.isfinite(fv) or fv <= 0:
            raise ValueError(f"{label} must be > 0")
        return round(fv, 3)
    tr = _opt_int(target_reels, "Target reels")
    tb = _opt_int(target_boxes, "Target boxes")
    tw = _opt_float(target_weight_kg, "Target weight")
    tp = _opt_int(target_pieces, "Target pieces")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            j = c.execute("SELECT job_no FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not j:
                raise ValueError("Job not found")
            c.execute("UPDATE jobs SET target_reels=?, target_boxes=?,"
                      " target_weight_kg=?, target_pieces=? WHERE id=?",
                      (tr, tb, tw, tp, job_id))
            _audit(c, resolved, "set_job_targets", "job", job_id,
                   f"J:{j['job_no']} targets reels={tr} boxes={tb} kg={tw} pcs={tp}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return dict(c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
    finally:
        c.close()


# ---------- reels (Type-1 sticker: single reel, NO QR) ----------
def next_roll_no(job_id: int, c: sqlite3.Connection) -> int:
    """Legacy per-job MAX kept for compat; new writes use the global monthly
    counter (see create_reel). Do not use for allocation."""
    r = c.execute("SELECT MAX(roll_no) m FROM reels WHERE job_id=?", (job_id,)).fetchone()
    return (r["m"] or 0) + 1


def create_reel(job_id: int, gross: float, core: float | None = None,
                shift: str = "DAY", supervisor: str = "", customer: str = "",
                path: str | None = None, actor: dict | None = None,
                period: str | None = None) -> dict:
    """Weigh one reel. Net = gross - core. Monthly global roll_no (resets 1
    each month across ALL jobs) for DB identity + per-product prod_seq
    (P0: each product restarts at 1 each month) for the sticker.

    The sticker shows prod_seq (floor's 1..N per product per month); the
    global roll_no/roll_code stays the unique DB identity (QR-free reels
    still need unambiguous history). Never reused after deletion.
    """
    resolved = _prod_actor(path)
    shift = (shift or "DAY").upper()
    if shift not in ("DAY", "NIGHT"):
        raise ValueError("Shift must be DAY or NIGHT")
    g = _finite_weight(gross, "Gross weight")
    if g <= 0:
        raise ValueError("Gross weight must be > 0 (read the scale)")
    if g > 500:
        raise ValueError("Gross weight looks wrong (>500kg). Check scale.")
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise ValueError("Job not found")
            if job["status"] != "open":
                raise ValueError("Job is closed. Reopen it to add reels.")
            cw_raw = core if core else float(job["core_wt"])
            cw = _finite_weight(cw_raw, "Core weight")
            if cw <= 0 or cw >= g:
                raise ValueError(f"Core ({cw}) must be >0 and < gross ({g})")
            net = round(float(g) - cw, 3)
            if net <= 0:
                raise ValueError("Net weight <= 0. Check gross/core.")
            per = period or current_period()
            seq = _alloc_seq(c, per, "reel")
            # never collide with legacy numbers already issued in this month
            m = c.execute("SELECT MAX(roll_no) m FROM reels WHERE roll_period=?",
                          (per,)).fetchone()
            if m and m["m"] and int(m["m"]) >= seq:
                seq = int(m["m"]) + 1
                c.execute("UPDATE monthly_counters SET last_no=? WHERE period=? AND kind='reel'",
                          (seq, per))
            code = roll_code_for(per, seq)
            # P0 per-product display seq (sticker 1..N per product per month).
            try:
                _pid = job["product_id"]
            except Exception:
                _pid = None
            pseq = _alloc_prod_seq(c, per, int(_pid) if _pid is not None else 0, "reel")
            ts = now_iso()
            cur = c.execute(
                "INSERT INTO reels(job_id, roll_no, roll_period, roll_code, prod_seq, gross, core, net,"
                " shift, supervisor, customer, status, created_at,"
                " print_count, print_status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?, 'loose', ?, 0, 'pending')",
                (job_id, seq, per, code, pseq, round(float(g), 3), round(cw, 3), net,
                 shift, (supervisor or "").upper(), (customer or "").strip().upper(), ts))
            rid = cur.lastrowid
            _audit(c, resolved, "create_reel", "reel", rid,
                   f"J:{job['job_no']} {code} period={per} roll_no={seq} prod_seq={pseq}")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
        r = c.execute("SELECT * FROM reels WHERE id=?", (rid,)).fetchone()
        d = dict(r)
        d["full_roll"] = full_roll_label(d)
        return d
    finally:
        c.close()


def loose_reels(job_id: int, path: str | None = None) -> list[dict]:
    """Loose reels in production order (oldest first) — the box-packing queue.

    The floor packs reels in the order they came off the line, so ascending
    row id (creation order) is the authoritative packing order. Monthly roll
    numbers can reset inside one continuous job, so roll_no alone is wrong.
    """
    c = get_conn(path)
    try:
        rows = c.execute("SELECT * FROM reels WHERE job_id=? AND status='loose' ORDER BY id",
                         (job_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["full_roll"] = full_roll_label(d)
            out.append(d)
        return out
    finally:
        c.close()


def job_reels(job_id: int, path: str | None = None) -> list[dict]:
    """All reels of the current job (loose + packed + dispatched), oldest first,
    each with month-qualified identity + label printed status for the
    packaging history pane."""
    c = get_conn(path)
    try:
        rows = c.execute("SELECT * FROM reels WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["full_roll"] = full_roll_label(d)
            d.setdefault("print_status", "pending")
            d.setdefault("print_count", 0)
            out.append(d)
        return out
    finally:
        c.close()


def job_boxes(job_id: int, path: str | None = None) -> list[dict]:
    """Every box of a job in packing order, with its linked bare roll numbers.

    Boxes are packed in production order, so ascending row id is the queue
    order used by the sticker printer. Roll numbers are the per-product
    visual identity (prod_seq when present, else global roll_no).
    """
    c = get_conn(path)
    try:
        rows = c.execute("SELECT * FROM cartons WHERE job_id=? ORDER BY id",
                         (job_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["box_wt"] = round(float(d.get("box_wt") or 0), 3)
            d["pieces"] = int(d.get("num_reels") or 0)
            rr = c.execute(
                "SELECT r.roll_no, r.prod_seq FROM reels r JOIN carton_items ci ON ci.reel_id=r.id"
                " WHERE ci.carton_id=? ORDER BY r.id", (d["id"],)).fetchall()
            d["rolls"] = [int(x["prod_seq"]) if x["prod_seq"] is not None else int(x["roll_no"])
                          for x in rr]
            out.append(d)
        return out
    finally:
        c.close()


def pending_box_stickers(job_id: int | None = None, path: str | None = None) -> list[dict]:
    """Boxes whose sticker has not been printed yet, in packing order.

    The floor packs boxes first and then prints the queued box stickers
    one-by-one, so this is the authoritative "Print next" queue.
    """
    c = get_conn(path)
    try:
        sql = ("SELECT * FROM cartons WHERE COALESCE(print_status,'pending') != 'printed'")
        params: tuple = ()
        if job_id is not None:
            sql += " AND job_id=?"
            params = (job_id,)
        sql += " ORDER BY id"
        out = []
        for r in c.execute(sql, params).fetchall():
            d = dict(r)
            d["box_wt"] = round(float(d.get("box_wt") or 0), 3)
            d["pieces"] = int(d.get("num_reels") or 0)
            rr = c.execute(
                "SELECT r.roll_no FROM reels r JOIN carton_items ci ON ci.reel_id=r.id"
                " WHERE ci.carton_id=? ORDER BY r.id", (d["id"],)).fetchall()
            d["rolls"] = [x["roll_no"] for x in rr]
            out.append(d)
        return out
    finally:
        c.close()


def mark_reel_printed(reel_id: int, path: str | None = None,
                      actor: dict | None = None) -> dict:
    """Persist print count/last state. Call ONLY when the printer accepted
    the job (returned True). Errors/exceptions must not call this: the record
    stays pending and reprintable."""
    _print_actor(path)
    c = get_conn(path)
    try:
        c.execute("UPDATE reels SET print_count=COALESCE(print_count,0)+1,"
                  " print_status='printed', last_printed_at=? WHERE id=?",
                  (now_iso(), reel_id))
        c.commit()
        r = c.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
        if not r:
            raise ValueError("Reel not found")
        return dict(r)
    finally:
        c.close()


def mark_carton_printed(carton_id: int, path: str | None = None,
                        actor: dict | None = None) -> dict:
    _print_actor(path)
    c = get_conn(path)
    try:
        c.execute("UPDATE cartons SET print_count=COALESCE(print_count,0)+1,"
                  " print_status='printed', last_printed_at=? WHERE id=?",
                  (now_iso(), carton_id))
        c.commit()
        r = c.execute("SELECT * FROM cartons WHERE id=?", (carton_id,)).fetchone()
        if not r:
            raise ValueError("Box not found")
        return dict(r)
    finally:
        c.close()


def reweigh_reel(reel_id: int, new_gross: float, path: str | None = None,
                 actor: dict | None = None, new_core: float | None = None) -> dict:
    """Reweigh a loose/unlinked reel only. Packed/dispatched/legacy-linked
    reels are blocked. Invalidates old print state (back to pending) while
    retaining original roll_no/period/created_at; audit keeps prev/new."""
    resolved = _reweigh_actor(path)
    ng = _finite_weight(new_gross, "New gross weight")
    if ng <= 0:
        raise ValueError("New gross weight must be > 0 (read the scale)")
    if ng > 500:
        raise ValueError("Gross weight looks wrong (>500kg). Check scale.")
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            r = c.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
            if not r:
                raise ValueError("Reel not found")
            r = dict(r)
            if r["status"] != "loose":
                raise ValueError(f"Roll #{r['roll_no']} is {r['status']} — only loose reels may be reweighed.")
            link = c.execute("SELECT carton_id FROM carton_items WHERE reel_id=?",
                             (reel_id,)).fetchone()
            if link:
                raise ValueError(f"Roll #{r['roll_no']} is packed in a box — reweigh blocked.")
            cw_raw = new_core if new_core else float(r["core"])
            cw = _finite_weight(cw_raw, "Core weight")
            if cw <= 0 or cw >= float(ng):
                raise ValueError(f"Core ({cw}) must be >0 and < gross ({ng})")
            net = round(float(ng) - cw, 3)
            if net <= 0:
                raise ValueError("Net weight <= 0. Check gross/core.")
            prev = f"gross={r['gross']} core={r['core']} net={r['net']}"
            c.execute("UPDATE reels SET gross=?, core=?, net=?, print_status='pending'"
                      " WHERE id=?", (round(float(ng), 3), round(cw, 3), net, reel_id))
            _audit(c, resolved, "reweigh", "reel", reel_id,
                   f"{full_roll_label(r)} {prev} -> gross={round(float(ng),3)}"
                   f" core={round(cw,3)} net={net}; print invalidated (count kept)")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
        return dict(c.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone())
    finally:
        c.close()


def delete_reel(reel_id: int, path: str | None = None, actor: dict | None = None) -> None:
    """Admin-only. Only loose/unlinked reels may be deleted. Numbers are
    never recycled (monthly counters only move forward). Audit persisted."""
    resolved = _delete_actor(path)
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            r = c.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
            if not r:
                raise ValueError("Reel not found")
            r = dict(r)
            if r["status"] != "loose":
                raise ValueError(f"Roll #{r['roll_no']} is {r['status']} — only loose reels may be deleted.")
            link = c.execute("SELECT carton_id FROM carton_items WHERE reel_id=?",
                             (reel_id,)).fetchone()
            if link:
                raise ValueError(f"Roll #{r['roll_no']} is packed in a box — delete blocked.")
            _audit(c, resolved, "delete_reel", "reel", reel_id,
                   f"{full_roll_label(r)} job_id={r['job_id']} gross={r['gross']} net={r['net']}"
                   f" number NOT recycled")
            c.execute("DELETE FROM reels WHERE id=?", (reel_id,))
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


def delete_carton(carton_id: int, path: str | None = None, actor: dict | None = None) -> None:
    """Admin-only. Only Ready cartons not loaded in any order. Legacy
    reel-linked cartons release their reels back to loose. Numbers never
    recycled. Audit persisted."""
    resolved = _delete_actor(path)
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            ct = c.execute("SELECT * FROM cartons WHERE id=?", (carton_id,)).fetchone()
            if not ct:
                raise ValueError("Box not found")
            ct = dict(ct)
            if ct["status"] != "Ready":
                raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} is {ct['status']} — only Ready boxes may be deleted.")
            dep = c.execute("SELECT order_id FROM dispatch_items WHERE carton_id=?",
                            (carton_id,)).fetchone()
            if dep:
                raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} is loaded in an order — delete blocked.")
            linked = c.execute("SELECT reel_id FROM carton_items WHERE carton_id=?",
                               (carton_id,)).fetchall()
            _audit(c, resolved, "delete_carton", "carton", carton_id,
                   f"{ct['carton_no']} job_id={ct['job_id']} wt={ct['total_gross']}"
                   f" reels={[x['reel_id'] for x in linked]} number NOT recycled")
            for x in linked:
                c.execute("UPDATE reels SET status='loose' WHERE id=?", (x["reel_id"],))
            c.execute("DELETE FROM carton_items WHERE carton_id=?", (carton_id,))
            c.execute("DELETE FROM cartons WHERE id=?", (carton_id,))
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


def delete_job(job_id: int, path: str | None = None, actor: dict | None = None) -> None:
    """Admin-only. Allowed only with zero production AND zero orders;
    otherwise raise with a clear close/archive reason. Never cascades."""
    resolved = _delete_actor(path)
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            nr = c.execute("SELECT COUNT(*) n FROM reels WHERE job_id=?", (job_id,)).fetchone()["n"]
            nc = c.execute("SELECT COUNT(*) n FROM cartons WHERE job_id=?", (job_id,)).fetchone()["n"]
            no = c.execute("SELECT COUNT(*) n FROM dispatch_orders WHERE job_id=?", (job_id,)).fetchone()["n"]
            j = c.execute("SELECT job_no FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not j:
                raise ValueError("Job not found")
            if nr or nc or no:
                raise ValueError(
                    f"Job J:{j['job_no']} has {nr} reels / {nc} boxes / {no} orders —"
                    " close/archive it instead of deleting; production is preserved.")
            _audit(c, resolved, "delete_job", "job", job_id, f"J:{j['job_no']} empty job deleted")
            c.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


def list_audit(entity: str | None = None, entity_id: int | None = None,
               path: str | None = None, limit: int = 200) -> list[dict]:
    c = get_conn(path)
    try:
        if entity and entity_id is not None:
            rows = c.execute("SELECT * FROM production_audit WHERE entity=? AND entity_id=?"
                             " ORDER BY id DESC LIMIT ?", (entity, entity_id, limit)).fetchall()
        elif entity:
            rows = c.execute("SELECT * FROM production_audit WHERE entity=?"
                             " ORDER BY id DESC LIMIT ?", (entity, limit)).fetchall()
        else:
            rows = c.execute("SELECT * FROM production_audit ORDER BY id DESC LIMIT ?",
                             (limit,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def production_history(path: str | None = None) -> list[dict]:
    """Reel creation and box packing records, newest first, with current status.

    Adds month-qualified identity (roll_period/roll_code, box_period) and
    label printed status; legacy keys (reference/box/etc.) are unchanged.
    """
    c = get_conn(path)
    try:
        # NOTE: ORDER BY sits outside the UNION via subquery wrapper.
        # Bare `... UNION ALL ... ORDER BY recorded_at, id, kind` crashes on
        # this SQLite build ("2nd ORDER BY term does not match any column"),
        # so keep the compound select nameless inside and sort outside.
        rows = c.execute("""
            SELECT kind, id, reference, job_no, job_name, net_weight, shift,
                   status, box, recorded_at, roll_period, roll_code, box_period,
                   print_status, print_count, full_ref FROM (
                SELECT 'Reel' AS kind, r.id AS id, r.roll_no AS reference,
                       j.job_no AS job_no, j.job_name AS job_name,
                       r.net AS net_weight, r.shift AS shift,
                       r.status AS status,
                       COALESCE(ct.carton_no, '') AS box,
                       r.created_at AS recorded_at,
                       r.roll_period AS roll_period, r.roll_code AS roll_code,
                       NULL AS box_period,
                       COALESCE(r.print_status,'pending') AS print_status,
                       COALESCE(r.print_count,0) AS print_count,
                       COALESCE(r.roll_code, r.roll_period || '#' || r.roll_no) AS full_ref
                FROM reels r JOIN jobs j ON j.id = r.job_id
                LEFT JOIN carton_items ci ON ci.reel_id = r.id
                LEFT JOIN cartons ct ON ct.id = ci.carton_id
                UNION ALL
                SELECT 'Box' AS kind, ct.id AS id, ct.carton_no AS reference,
                       j.job_no AS job_no, j.job_name AS job_name,
                       ct.total_net AS net_weight, ct.shift AS shift,
                       ct.status AS status, ct.carton_no AS box,
                       ct.packed_at AS recorded_at,
                       NULL AS roll_period, NULL AS roll_code,
                       ct.box_period AS box_period,
                       COALESCE(ct.print_status,'pending') AS print_status,
                       COALESCE(ct.print_count,0) AS print_count,
                       ct.carton_no AS full_ref
                FROM cartons ct JOIN jobs j ON j.id = ct.job_id
            )
            ORDER BY recorded_at DESC, id DESC, kind ASC
        """).fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def todays_reels(path: str | None = None) -> list[dict]:
    c = get_conn(path)
    try:
        rows = c.execute(
            "SELECT r.*, j.job_no, j.job_name FROM reels r JOIN jobs j ON j.id=r.job_id"
            " WHERE date(r.created_at)=date('now','localtime') ORDER BY r.id DESC").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


# ---------- cartons / bundles (Type-2 sticker: carton WITH QR) ----------
CARTON_TOL = 0.150  # kg — scale-vs-sum agreement needed to PACK when scale is used


def match_reels_by_gross(loose: list[dict], target_gross: float,
                         tolerance: float = CARTON_TOL, max_n: int = 3,
                         min_n: int = 1) -> dict | None:
    """Find the min_n..max_n loose reels whose summed gross best matches the scale.

    Callers pass the scale reading directly; the ±0.150 Kg tolerance covers
    the empty carton. The packaging page always uses min_n=2 (a lone reel on
    the scale is a reel weighing, never a carton).

    Returns {"reels", "total", "delta", "within", "ambiguous"} for the best
    subset, or None when there are no loose reels. "within" means the best
    delta fits the tolerance; "ambiguous" means 2+ subsets fit (operator
    must verify the rolls — same-count combos can differ by ~0.1kg).
    """
    import itertools
    if not loose or target_gross is None or target_gross <= 0:
        return None
    cands = []
    for n in range(max(1, min_n), min(max_n, len(loose)) + 1):
        for combo in itertools.combinations(loose, n):
            tot = round(sum(r["gross"] for r in combo), 3)
            cands.append((abs(tot - target_gross), tot, list(combo)))
    if not cands:
        return None
    cands.sort(key=lambda c: (c[0], -len(c[2])))
    best_diff, best_tot, best_reels = cands[0]
    within = best_diff <= tolerance + 1e-9
    ambiguous = sum(1 for d, _, _ in cands if d <= tolerance + 1e-9) > 1
    return {"reels": best_reels, "total": best_tot,
            "delta": round(target_gross - best_tot, 3),
            "within": within, "ambiguous": ambiguous}


def next_bundle_no(job_id: int, c: sqlite3.Connection) -> int:
    r = c.execute("SELECT MAX(bundle_no) m FROM cartons WHERE job_id=?", (job_id,)).fetchone()
    return (r["m"] or 0) + 1


def next_carton_no(c: sqlite3.Connection) -> str:
    """Legacy preview helper kept for compat. New writes use monthly
    CYYYYMM-NNNN via the box counter; legacy C0001 rows are untouched."""
    import re as _re
    per = current_period()
    r = c.execute("SELECT last_no FROM monthly_counters WHERE period=? AND kind='box'",
                  (per,)).fetchone()
    base = int(r["last_no"] or 0) if r else 0
    mx = 0
    for (cn,) in c.execute("SELECT carton_no FROM cartons WHERE box_period=?",
                           (per,)).fetchall():
        m = _re.match(r"^C(\d{6})-(\d+)$", str(cn or "").upper())
        if m and m.group(1) == per.replace("-", ""):
            mx = max(mx, int(m.group(2)))
    return carton_no_for(per, max(base, mx) + 1)


def _alloc_box(c: sqlite3.Connection, period: str, product_id: int | None = None) -> tuple[str, int, int]:
    """Allocate monthly box numbers: global + per-product display seq.

    Returns (carton_no, global_seq, prod_seq). Global stays UNIQUE (QR);
    prod_seq is the P0 per-product 1..N shown on the sticker.
    """
    import re as _re
    seq = _alloc_seq(c, period, "box")
    mx = 0
    for (cn,) in c.execute("SELECT carton_no FROM cartons WHERE box_period=?",
                           (period,)).fetchall():
        m = _re.match(r"^C(\d{6})-(\d+)$", str(cn or "").upper())
        if m and m.group(1) == period.replace("-", ""):
            mx = max(mx, int(m.group(2)))
    if mx >= seq:
        seq = mx + 1
        c.execute("UPDATE monthly_counters SET last_no=? WHERE period=? AND kind='box'",
                  (seq, period))
    cn = carton_no_for(period, seq)
    pid = int(product_id) if product_id is not None else 0
    pseq = _alloc_prod_seq(c, period, pid, "box")
    return cn, seq, pseq


def pack_weight_carton(job_id: int, gross_weight: float, shift: str = "DAY",
                       customer: str = "", path: str | None = None,
                       actor: dict | None = None, period: str | None = None) -> dict:
    """Pack a BOX-WEIGHT-ONLY carton: the scale's measured box weight is stored.

    Monthly global box number (CYYYYMM-NNNN, resets 1 each month across ALL
    jobs), allocated with BEGIN IMMEDIATE so concurrent packs never reuse a
    number. No reel guessing, no count input, no fabricated roll IDs.
    Compatibility (unknowns are NOT invented): num_reels=0, total_core=0,
    total_gross=total_net=measured box weight.
    """
    resolved = _prod_actor(path)
    w = _finite_weight(gross_weight, "Box weight")
    if w <= 0:
        raise ValueError("Box weight must be > 0 (read the scale)")
    if w > 500:
        raise ValueError("Box weight looks wrong (>500kg). Check scale.")
    shift = (shift or "DAY").upper()
    if shift not in ("DAY", "NIGHT"):
        raise ValueError("Shift must be DAY or NIGHT")
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise ValueError("Job not found")
            if job["status"] != "open":
                raise ValueError("Job is closed. Reopen it to pack boxes.")
            per = period or current_period()
            try:
                _pid = job["product_id"]
            except Exception:
                _pid = None
            cn, _seq, _pseq = _alloc_box(c, per, int(_pid) if _pid is not None else 0)
            r = c.execute("SELECT MAX(bundle_no) m FROM cartons WHERE job_id=?",
                          (job_id,)).fetchone()
            bn = (r["m"] or 0) + 1
            w = round(w, 3)
            if w <= 0:
                raise ValueError("Box weight too small to record (rounds to 0). Check scale.")
            cur = c.execute(
                "INSERT INTO cartons(carton_no, job_id, bundle_no, box_period, prod_seq, num_reels,"
                " box_wt, total_gross, total_core, total_net, shift, customer, status, packed_at,"
                " print_count, print_status)"
                " VALUES(?,?,?,?,?,?,0,?,?,?,?,?, 'Ready', ?, 0, 'pending')",
                (cn, job_id, bn, per, _pseq, 0, w, 0, w, shift,
                 (customer or "").strip().upper(), now_iso()))
            cid = cur.lastrowid
            _audit(c, resolved, "pack_box", "carton", cid,
                   f"{cn} J:{job['job_no']} period={per} bundle={bn} prod_seq={_pseq} box_wt={w}")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
        row = c.execute("SELECT * FROM cartons WHERE id=?", (cid,)).fetchone()
        return dict(row)
    finally:
        c.close()


def pack_next_box(job_id: int, gross_weight: float, box_weight: float,
                  reels_per_box: int = 2, shift: str = "DAY",
                  customer: str = "", path: str | None = None,
                  actor: dict | None = None, period: str | None = None) -> dict:
    """Pack the next loose reels (production order) into one measured box.

    The operator sets the session box/tare weight once; the measured packed
    box gross is stored as-is. Reels are consumed oldest-first (row id), up
    to `reels_per_box`; a partial final box is allowed when fewer loose reels
    remain. Weights: total_gross = measured box, total_core = sum reel cores,
    box_wt = session tare, total_net = gross - tare - cores (= sum reel nets).

    Printing is deliberately NOT part of packing: the box sticker stays
    pending and is printed later, one-by-one, from the sticker queue.
    """
    resolved = _prod_actor(path)
    g = _finite_weight(gross_weight, "Box weight")
    if g <= 0:
        raise ValueError("Box weight must be > 0 (read the scale)")
    if g > 500:
        raise ValueError("Box weight looks wrong (>500kg). Check scale.")
    # Store and compute with the same (round-tripped) tare so
    # total_net always equals total_gross - box_wt - total_core.
    bw = round(_finite_weight(box_weight, "Box/tare weight"), 3)
    if bw < 0:
        raise ValueError("Box/tare weight cannot be negative")
    if bw >= g:
        raise ValueError(f"Box/tare weight ({bw}) must be less than the measured box ({g})")
    try:
        n = int(reels_per_box)
    except (TypeError, ValueError):
        raise ValueError("Reels per box must be a positive whole number")
    if n < 1 or n > 20:
        raise ValueError("Reels per box must be between 1 and 20")
    shift = (shift or "DAY").upper()
    if shift not in ("DAY", "NIGHT"):
        raise ValueError("Shift must be DAY or NIGHT")
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise ValueError("Job not found")
            if job["status"] != "open":
                raise ValueError("Job is closed. Reopen it to pack boxes.")
            loose = c.execute(
                "SELECT * FROM reels WHERE job_id=? AND status='loose'"
                " ORDER BY id LIMIT ?", (job_id, n)).fetchall()
            if not loose:
                raise ValueError("No loose reels to pack. Weigh reels first.")
            reels = [dict(r) for r in loose]
            tg = round(float(g), 3)
            tc = round(sum(float(r["core"]) for r in reels), 3)
            tn = round(tg - bw - tc, 3)
            if tn <= 0:
                raise ValueError(
                    "Box net <= 0. The box/tare plus cores exceed the measured box.")
            per = period or current_period()
            try:
                _pid2 = job["product_id"]
            except Exception:
                _pid2 = None
            cn, _seq, _pseq = _alloc_box(c, per, int(_pid2) if _pid2 is not None else 0)
            r = c.execute("SELECT MAX(bundle_no) m FROM cartons WHERE job_id=?",
                          (job_id,)).fetchone()
            bn = (r["m"] or 0) + 1
            cur = c.execute(
                "INSERT INTO cartons(carton_no, job_id, bundle_no, box_period, prod_seq, num_reels,"
                " box_wt, total_gross, total_core, total_net, shift, customer, status,"
                " packed_at, print_count, print_status)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'Ready', ?, 0, 'pending')",
                (cn, job_id, bn, per, _pseq, len(reels), bw, tg, tc, tn, shift,
                 (customer or "").strip().upper(), now_iso()))
            cid = cur.lastrowid
            for rr in reels:
                c.execute("INSERT INTO carton_items(carton_id, reel_id) VALUES(?,?)",
                          (cid, rr["id"]))
                c.execute("UPDATE reels SET status='packed' WHERE id=?", (rr["id"],))
            _audit(c, resolved, "pack_box_reels", "carton", cid,
                   f"{cn} J:{job['job_no']} period={per} prod_seq={_pseq} reels={[x['id'] for x in reels]}"
                   f" rolls={[x['roll_no'] for x in reels]} tare={bw}"
                   f" gross={tg} net={tn}")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
        row = c.execute("SELECT * FROM cartons WHERE id=?", (cid,)).fetchone()
        return dict(row)
    finally:
        c.close()


def pack_carton(job_id: int, reel_ids: list[int], shift: str = "DAY",
                path: str | None = None, customer: str | None = None,
                actor: dict | None = None, period: str | None = None) -> dict:
    """Legacy reel-linked pack (kept for old cartons/tests). New floor flow
    is weight-only (pack_weight_carton); boxes stay independent otherwise."""
    resolved = _prod_actor(path)
    if not reel_ids:
        raise ValueError("Select at least 1 reel")
    if len(reel_ids) > 3:
        raise ValueError("Max 3 reels per box")
    shift = (shift or "DAY").upper()
    if shift not in ("DAY", "NIGHT"):
        raise ValueError("Shift must be DAY or NIGHT")
    c = get_conn(path)
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise ValueError("Job not found")
            ph = ",".join("?" for _ in reel_ids)
            reels = c.execute(f"SELECT * FROM reels WHERE id IN ({ph})", tuple(reel_ids)).fetchall()
            if len(reels) != len(reel_ids):
                raise ValueError("One of the reels was not found")
            for r in reels:
                if r["job_id"] != job_id:
                    raise ValueError(f"Roll #{r['roll_no']} belongs to another job. Same-job boxes only.")
                if r["status"] != "loose":
                    raise ValueError(f"Roll #{r['roll_no']} is already {r['status']}.")
            tg = round(sum(r["gross"] for r in reels), 3)
            tc = round(sum(r["core"] for r in reels), 3)
            tn = round(sum(r["net"] for r in reels), 3)
            per = period or current_period()
            try:
                _pid3 = job["product_id"]
            except Exception:
                _pid3 = None
            cn, _seq, _pseq = _alloc_box(c, per, int(_pid3) if _pid3 is not None else 0)
            r = c.execute("SELECT MAX(bundle_no) m FROM cartons WHERE job_id=?",
                          (job_id,)).fetchone()
            bn = (r["m"] or 0) + 1
            if customer is None:
                got = {(r["customer"] or "").strip().upper() for r in reels if "customer" in r.keys()}
                customer = got.pop() if len(got) == 1 else ""
            cur = c.execute(
                "INSERT INTO cartons(carton_no, job_id, bundle_no, box_period, prod_seq, num_reels,"
                " box_wt, total_gross, total_core, total_net, shift, customer, status, packed_at,"
                " print_count, print_status)"
                " VALUES(?,?,?,?,?,?,0,?,?,?,?,?, 'Ready', ?, 0, 'pending')",
                (cn, job_id, bn, per, _pseq, len(reels), tg, tc, tn, shift,
                 (customer or "").strip().upper(), now_iso()))
            cid = cur.lastrowid
            for r in reels:
                c.execute("INSERT INTO carton_items(carton_id, reel_id) VALUES(?,?)", (cid, r["id"]))
                c.execute("UPDATE reels SET status='packed' WHERE id=?", (r["id"],))
            _audit(c, resolved, "pack_carton_legacy", "carton", cid,
                   f"{cn} J:{job['job_no']} prod_seq={_pseq} reels={[x['id'] for x in reels]}")
            c.commit()
        except Exception:
            try:
                c.rollback()
            except sqlite3.Error:
                pass
            raise
        row = c.execute("SELECT * FROM cartons WHERE id=?", (cid,)).fetchone()
        return dict(row)
    finally:
        c.close()


def carton_detail(carton_id: int, path: str | None = None) -> dict:
    c = get_conn(path)
    try:
        ct = c.execute(
            "SELECT ct.*, j.job_no, j.job_name, j.size_mm FROM cartons ct"
            " JOIN jobs j ON j.id=ct.job_id WHERE ct.id=?", (carton_id,)).fetchone()
        if not ct:
            raise ValueError("Box not found")
        reels = c.execute(
            "SELECT r.* FROM reels r JOIN carton_items ci ON ci.reel_id=r.id"
            " WHERE ci.carton_id=? ORDER BY r.id", (carton_id,)).fetchall()
        d = dict(ct)
        d["reels"] = [dict(r) for r in reels]
        # Floor reads per-product numbers (prod_seq) when present.
        d["rolls"] = [int(r.get("prod_seq")) if r.get("prod_seq") is not None else int(r["roll_no"])
                      for r in d["reels"]]
        d["box_wt"] = round(float(d.get("box_wt") or 0), 3)
        # Dispatch counts pieces as reels: a 2-reel box = 2 pieces.
        d["pieces"] = int(d.get("num_reels") or 0)
        d["qr"] = qr_payload(d["carton_no"])
        return d
    finally:
        c.close()


def ready_stock(job_id: int | None = None, path: str | None = None) -> list[dict]:
    c = get_conn(path)
    try:
        if job_id:
            rows = c.execute(
                "SELECT ct.*, j.job_no, j.job_name FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
                " WHERE ct.status='Ready' AND ct.job_id=? ORDER BY ct.packed_at DESC", (job_id,)).fetchall()
        else:
            rows = c.execute(
                "SELECT ct.*, j.job_no, j.job_name FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
                " WHERE ct.status='Ready' ORDER BY ct.packed_at DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            rr = c.execute("SELECT r.roll_no, r.prod_seq FROM reels r JOIN carton_items ci ON ci.reel_id=r.id"
                           " WHERE ci.carton_id=? ORDER BY r.id", (d["id"],)).fetchall()
            d["rolls"] = [int(x["prod_seq"]) if x["prod_seq"] is not None else int(x["roll_no"])
                          for x in rr]
            d["box_wt"] = round(float(d.get("box_wt") or 0), 3)
            # Dispatch counts pieces as reels: a 2-reel box = 2 pieces.
            d["pieces"] = int(d.get("num_reels") or 0)
            d["qr"] = qr_payload(d["carton_no"])
            out.append(d)
        return out
    finally:
        c.close()


def ready_stock_summary(path: str | None = None) -> dict:
    """Ready-stock totals per job in ONE aggregate query (no per-job reads).

    Returns {job_id: {"boxes": n, "coils": n, "pieces": n|None, "weight_kg": f}}.
    Pieces is None when the job has no wrappers-per-reel count (kg product);
    weight_kg is the Ready net-kg total for kg targets.
    """
    c = get_conn(path)
    try:
        rows = c.execute(
            "SELECT ct.job_id AS job_id, COUNT(*) AS boxes,"
            " COALESCE(SUM(ct.num_reels), 0) AS coils,"
            " COALESCE(SUM(ct.total_net), 0) AS weight_kg,"
            " j.wrappers_per_reel AS wpr"
            " FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
            " WHERE ct.status='Ready' GROUP BY ct.job_id").fetchall()
        out: dict = {}
        for r in rows:
            jid = int(r["job_id"])
            coils = int(r["coils"] or 0)
            try:
                wpr = int(r["wpr"] or 0)
            except (TypeError, ValueError):
                wpr = 0
            try:
                wkg = round(float(r["weight_kg"] or 0), 3)
            except (TypeError, ValueError):
                wkg = 0.0
            out[jid] = {"boxes": int(r["boxes"] or 0), "coils": coils,
                        "pieces": coils * wpr if wpr > 0 else None,
                        "weight_kg": wkg}
        return out
    finally:
        c.close()


def find_carton(scan_text: str, path: str | None = None) -> dict | None:
    cn = parse_scan(scan_text)
    if not cn:
        return None
    c = get_conn(path)
    try:
        r = c.execute(
            "SELECT ct.*, j.job_no, j.job_name FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
            " WHERE ct.carton_no=?", (cn,)).fetchone()
        return dict(r) if r else None
    finally:
        c.close()


def carton_verify(scan_text: str, path: str | None = None) -> dict | None:
    """Scanner re-verification: batch + box details for one carton QR.

    Returns {"detail", "order"} where order is the dispatch order holding the
    carton, or None when it was never dispatched. None when unknown code.
    The QR stays an opaque ID (VASAVI:Cxxxx); details never leave the app.
    """
    cn = parse_scan(scan_text)
    if not cn:
        return None
    c = get_conn(path)
    try:
        ct = c.execute("SELECT id FROM cartons WHERE carton_no=?", (cn,)).fetchone()
        if not ct:
            return None
    finally:
        c.close()
    detail = carton_detail(ct["id"], path)
    c = get_conn(path)
    try:
        o = c.execute(
            "SELECT o.* FROM dispatch_orders o JOIN dispatch_items di ON di.order_id=o.id"
            " WHERE di.carton_id=? ORDER BY di.id DESC LIMIT 1", (ct["id"],)).fetchone()
        return {"detail": detail, "order": dict(o) if o else None}
    finally:
        c.close()


# ---------- dispatch (authorization distinct from production) ----------
def next_order_no(c: sqlite3.Connection) -> str:
    """Read-only preview (MAX+1). Allocation uses the monotonic id_counters
    table so deleted order numbers are never reused."""
    r = c.execute("SELECT MAX(CAST(SUBSTR(order_no,4) AS INTEGER)) m FROM dispatch_orders WHERE order_no LIKE 'DO-%'").fetchone()
    return f"DO-{(r['m'] or 0) + 1:04d}"


def create_dispatch_order(customer: str, job_id: int, required_bundles: int | None = None,
                          order_date: str | None = None, path: str | None = None,
                          actor: dict | None = None,
                          required_pieces: int | None = None,
                          customer_po: str = "", po_date: str | None = None,
                          batch_no: str = "",
                          piece_unit: str = "wrapper",
                          required_weight_kg: float | None = None) -> dict:
    """Create a loading order.

    Three target kinds (exactly one required):
    - `required_pieces` wrappers (piece products, needs wrappers_per_reel),
    - `required_weight_kg` net-kg (kg products, sums loaded total_net),
    - `required_bundles` legacy box count.

    `customer_po` prints on the wide sticker; `po_date` (YYYY-MM-DD, optional)
    is the client PO date set at order creation and printed beside the PO.
    `batch_no` optional (blank = derive SVSF/MON-DD at print time).
    """
    import re as _re
    resolved = _dispatch_actor(path)
    if not (customer or "").strip():
        raise ValueError("Customer name required")
    given = sum(1 for v in (required_pieces, required_bundles, required_weight_kg)
                if v is not None)
    if given == 0:
        raise ValueError("Provide required_pieces (pieces), required_weight_kg (kg), or required_bundles (legacy)")
    if given > 1:
        raise ValueError("Give only one target: pieces, kg, or boxes — not several")
    unit = str(piece_unit or "wrapper").strip().lower()
    if unit not in ("wrapper", "reel", "kg"):
        raise ValueError("Piece unit must be 'wrapper', 'reel' or 'kg'")
    req_p: int | None = None
    req_b: int | None = None
    req_kg: float | None = None
    if required_pieces is not None:
        try:
            req_p = int(required_pieces)
        except (TypeError, ValueError):
            raise ValueError("Required pieces must be a positive whole number")
        if not math.isfinite(float(req_p)) or req_p <= 0:
            raise ValueError("Required pieces must be > 0")
        req_b = 0
    elif required_weight_kg is not None:
        try:
            req_kg = float(required_weight_kg)
        except (TypeError, ValueError):
            raise ValueError("Required kg must be a number > 0")
        if not math.isfinite(req_kg) or req_kg <= 0:
            raise ValueError("Required kg must be > 0")
        req_kg = round(req_kg, 3)
        req_b = 0
        unit = "kg"
    else:
        try:
            req_b = int(required_bundles)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            raise ValueError("Required bundles must be a positive whole number")
        if not math.isfinite(float(req_b)) or req_b <= 0:
            raise ValueError("Required bundles must be > 0")
        unit = "reel"  # bundle orders have no piece unit
    po = (customer_po or "").strip()
    batch = (batch_no or "").strip().upper()
    if len(po) > 64:
        raise ValueError("Customer PO must be 64 characters or fewer")
    if len(batch) > 64:
        raise ValueError("Batch number must be 64 characters or fewer")
    pod = (po_date or "").strip() if po_date else ""
    if pod:
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", pod):
            raise ValueError("PO date must be YYYY-MM-DD")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not job:
                raise ValueError("Select a product job for this order")
            if req_p is not None and unit == "wrapper":
                wpr = int(job["wrappers_per_reel"] or 0)
                if wpr <= 0:
                    raise ValueError(
                        "This job has no wrappers-per-reel count. Set it on the "
                        "product/job before creating a piece-based order.")
            seq = _alloc_order_seq(c)
            on = f"DO-{seq:04d}"
            cur = c.execute(
                "INSERT INTO dispatch_orders(order_no, customer, customer_po, po_date, batch_no,"
                " job_id, required_bundles, required_pieces, required_weight_kg, piece_unit, status,"
                " order_date, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?, 'loading', ?, ?)",
                (on, customer.strip().upper(), po, pod, batch, job_id, req_b, req_p, req_kg,
                 unit, order_date or today_iso(), now_iso()))
            oid = cur.lastrowid
            if req_p is not None:
                need = f"pieces={req_p} ({unit})"
            elif req_kg is not None:
                need = f"kg={req_kg}"
            else:
                need = f"bundles={req_b}"
            _audit(c, resolved, "create_order", "order", oid,
                   f"{on} J:{job['job_no']} need={need} po={po or '—'} po_date={pod or '—'}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        r = c.execute("SELECT * FROM dispatch_orders WHERE id=?", (oid,)).fetchone()
        return dict(r)
    finally:
        c.close()


def _order_items(c: sqlite3.Connection, order_id: int) -> list[sqlite3.Row]:
    """Loaded cartons with their client box number, in scan order."""
    return c.execute(
        "SELECT ct.*, di.scanned_at, di.client_box_no FROM dispatch_items di"
        " JOIN cartons ct ON ct.id=di.carton_id WHERE di.order_id=?"
        " ORDER BY di.id", (order_id,)).fetchall()


def _piece_counts(items: list, unit: str = "reel",
                  wrappers_per_reel: int | None = None) -> int:
    """Order pieces.

    `wrapper` (new orders): each linked coil contributes the job's
    wrappers_per_reel, so a 3-coil box is 3 × wrappers. `reel` (legacy piece
    orders): one linked reel = one piece, preserving old targets/history.
    """
    if str(unit or "reel").lower() == "wrapper":
        w = int(wrappers_per_reel or 0)
        return sum(int(x["num_reels"] or 0) * w for x in items)
    return sum(int(x["num_reels"] or 0) for x in items)


def _unit_word(unit: str) -> str:
    u = str(unit or "reel").lower()
    if u == "wrapper":
        return "pcs"
    if u == "kg":
        return "kg"
    return "coils"


def list_orders(status: str | None = None, path: str | None = None) -> list[dict]:
    c = get_conn(path)
    try:
        if status:
            rows = c.execute(
                "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
                " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
                " WHERE o.status=? ORDER BY o.id DESC", (status,)).fetchall()
        else:
            rows = c.execute(
                "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
                " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
                " ORDER BY o.id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            items = _order_items(c, d["id"])
            unit = str(d.get("piece_unit") or "reel")
            wpr = int(d.get("wrappers_per_reel") or 0)
            d["piece_unit"] = unit
            d["loaded"] = len(items)          # loaded cartons/bundles
            d["loaded_cartons"] = len(items)
            d["loaded_pieces"] = _piece_counts(items, unit, wpr)
            d["loaded_net"] = round(sum(float(x["total_net"] or 0) for x in items), 3)
            rp = d.get("required_pieces")
            rq = d.get("required_weight_kg")
            if rp is not None:
                rp = int(rp)
                d["required_pieces"] = rp
                d["remaining_pieces"] = max(0, rp - d["loaded_pieces"])
                d["remaining"] = d["remaining_pieces"]  # legacy alias, pieces here
            elif rq is not None:
                try:
                    rq = float(rq)
                except (TypeError, ValueError):
                    rq = 0.0
                d["required_weight_kg"] = rq
                d["remaining_weight_kg"] = round(max(0.0, rq - d["loaded_net"]), 3)
                d["remaining"] = d["remaining_weight_kg"]
                d["remaining_pieces"] = 0
            else:
                d["remaining_pieces"] = 0
                d["remaining"] = max(0, int(d["required_bundles"]) - len(items))
            out.append(d)
        return out
    finally:
        c.close()


def order_progress(order_id: int, path: str | None = None) -> dict:
    c = get_conn(path)
    try:
        o = c.execute(
            "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
            " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
            " WHERE o.id=?", (order_id,)).fetchone()
        if not o:
            raise ValueError("Order not found")
        d = dict(o)
        items = _order_items(c, order_id)
        unit = str(d.get("piece_unit") or "reel")
        wpr = int(d.get("wrappers_per_reel") or 0)
        d["piece_unit"] = unit
        d["pieces_word"] = _unit_word(unit)
        d["items"] = [dict(x) for x in items]
        for i, it in enumerate(d["items"], 1):
            if it.get("client_box_no") is None:
                it["client_box_no"] = i
        d["loaded"] = len(items)
        d["loaded_cartons"] = len(items)
        d["loaded_pieces"] = _piece_counts(items, unit, wpr)
        rp = d.get("required_pieces")
        rq = d.get("required_weight_kg")
        if rp is not None:
            rp = int(rp)
            d["required_pieces"] = rp
            d["remaining_pieces"] = max(0, rp - d["loaded_pieces"])
            d["remaining"] = d["remaining_pieces"]  # legacy alias, pieces here
            d["pieces_pct"] = (round(d["loaded_pieces"] * 100.0 / rp, 1)
                               if rp > 0 else 0.0)
            d["weight_pct"] = 0.0
        elif rq is not None:
            try:
                rq = float(rq)
            except (TypeError, ValueError):
                rq = 0.0
            d["required_weight_kg"] = rq
            d["loaded_net"] = round(sum(float(x["total_net"] or 0) for x in items), 3)
            d["remaining_weight_kg"] = round(max(0.0, rq - d["loaded_net"]), 3)
            d["remaining"] = d["remaining_weight_kg"]
            d["remaining_pieces"] = 0
            d["pieces_pct"] = 0.0
            d["weight_pct"] = (round(d["loaded_net"] * 100.0 / rq, 1) if rq > 0 else 0.0)
        else:
            d["remaining_pieces"] = 0
            d["remaining"] = max(0, int(d["required_bundles"]) - len(items))
            d["pieces_pct"] = 0.0
            d["weight_pct"] = 0.0
        d["loaded_net"] = round(sum(x["total_net"] for x in items), 3)
        return d
    finally:
        c.close()


def _dispatch_slip_rows(c: sqlite3.Connection, order_id: int,
                        unit: str, wpr: int) -> list[dict]:
    """One row per loaded box, in scan order, for the dispatch slip."""
    rows = []
    for i, it in enumerate(_order_items(c, order_id), 1):
        box_no = it["client_box_no"]
        if box_no is None:
            box_no = i
        n_reels = int(it["num_reels"] or 0)
        if str(unit or "reel").lower() == "wrapper":
            pieces = n_reels * int(wpr or 0)
        else:
            pieces = n_reels
        rows.append({
            "box_no": int(box_no),
            "carton_no": bare_carton_no(it["carton_no"]),
            "num_reels": n_reels,
            "pieces": pieces,
            "gross": round(float(it["total_gross"] or 0), 3),
            "net": round(float(it["total_net"] or 0), 3),
            "scanned_at": it["scanned_at"],
        })
    return rows


def _build_dispatch_slip(c: sqlite3.Connection, o: dict) -> dict:
    """Assemble the full dispatch-slip dict from one order row + its items."""
    unit = str(o.get("piece_unit") or "reel")
    wpr = int(o.get("wrappers_per_reel") or 0)
    rows = _dispatch_slip_rows(c, o["id"], unit, wpr)
    rp = o.get("required_pieces")
    rp = int(rp) if rp is not None else None
    rq = o.get("required_weight_kg")
    try:
        rq = float(rq) if rq is not None else None
    except (TypeError, ValueError):
        rq = None
    return {
        "order_id": o["id"],
        "order_no": o["order_no"],
        "customer": o["customer"],
        "customer_po": o.get("customer_po") or "",
        "po_date": o.get("po_date") or "",
        "batch_no": o.get("batch_no") or "",
        "job_no": o.get("job_no"),
        "job_name": o.get("job_name"),
        "piece_unit": unit,
        "pieces_word": _unit_word(unit),
        "required_bundles": int(o.get("required_bundles") or 0),
        "required_pieces": rp,
        "required_weight_kg": rq,
        "loaded_boxes": len(rows),
        "loaded_pieces": sum(r["pieces"] for r in rows),
        "loaded_net": round(sum(r["net"] for r in rows), 3),
        "loaded_gross": round(sum(r["gross"] for r in rows), 3),
        "status": o.get("status"),
        "order_date": o.get("order_date"),
        "completed_at": o.get("completed_at"),
        "rows": rows,
    }


def dispatch_slip_data(order_id: int, path: str | None = None) -> dict:
    """Live-built dispatch-slip dict for one order (any status).

    Used for pre-completion previews. The completed order's printed record
    is the frozen copy — see get_dispatch_slip().
    """
    c = get_conn(path)
    try:
        o = c.execute(
            "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
            " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
            " WHERE o.id=?", (order_id,)).fetchone()
        if not o:
            raise ValueError("Order not found")
        slip = _build_dispatch_slip(c, dict(o))
        slip["frozen"] = False
        return slip
    finally:
        c.close()


def get_dispatch_slip(order_id: int, path: str | None = None) -> dict:
    """The saved dispatch slip for one order.

    Returns the frozen copy written at completion (identical bytes on every
    reprint). Orders completed before this release have no frozen row — their
    slip is rebuilt live from the still-linked dispatch tables instead.
    """
    c = get_conn(path)
    try:
        row = c.execute(
            "SELECT * FROM dispatch_slips WHERE order_id=?", (order_id,)).fetchone()
        if row is not None:
            slip = json.loads(row["slip_json"])
            slip["slip_id"] = row["id"]
            slip["print_count"] = int(row["print_count"] or 0)
            slip["last_printed_at"] = row["last_printed_at"]
            slip["frozen"] = True
            return slip
        o = c.execute(
            "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
            " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
            " WHERE o.id=?", (order_id,)).fetchone()
        if not o:
            raise ValueError("Order not found")
        slip = _build_dispatch_slip(c, dict(o))
        slip["frozen"] = False
        return slip
    finally:
        c.close()


def mark_dispatch_slip_printed(order_id: int, path: str | None = None,
                               actor: dict | None = None) -> dict:
    """Persist print count/last state. Call ONLY when the printer accepted
    the job (or the PDF was written). Errors must not call this: the record
    stays reprintable.

    Dispatcher role (dispatch.mutate) — the slip prints on the dispatch
    station, whose role has no production.print.
    """
    _dispatch_actor(path)
    c = get_conn(path)
    try:
        row = c.execute(
            "SELECT id, print_count FROM dispatch_slips WHERE order_id=?",
            (order_id,)).fetchone()
        if not row:
            raise ValueError("No saved dispatch slip for this order yet.")
        c.execute("UPDATE dispatch_slips SET print_count=print_count+1,"
                  " last_printed_at=? WHERE order_id=?", (now_iso(), order_id))
        c.commit()
        return {"ok": True, "order_id": order_id,
                "print_count": int(row["print_count"] or 0) + 1}
    finally:
        c.close()


def _freeze_dispatch_slip_locked(c: sqlite3.Connection, order_id: int) -> dict:
    """Write the frozen slip row inside the completion transaction.

    The caller must already have marked the order complete in the same
    transaction, so the frozen record and the dispatched state commit
    atomically — the saved slip can never disagree with what went out.
    """
    o = c.execute(
        "SELECT o.*, j.job_no, j.job_name, j.wrappers_per_reel"
        " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id"
        " WHERE o.id=?", (order_id,)).fetchone()
    if not o:
        raise ValueError("Order not found")
    slip = _build_dispatch_slip(c, dict(o))
    c.execute("INSERT INTO dispatch_slips (order_id, order_no, slip_json, created_at)"
              " VALUES (?,?,?,?)",
              (order_id, slip["order_no"], json.dumps(slip, sort_keys=True),
               now_iso()))
    row = c.execute("SELECT id FROM dispatch_slips WHERE order_id=?",
                    (order_id,)).fetchone()
    slip["slip_id"] = row["id"]
    slip["frozen"] = True
    slip["print_count"] = 0
    slip["last_printed_at"] = None
    return slip


def scan_into_order(order_id: int, scan_text: str, path: str | None = None,
                    actor: dict | None = None) -> dict:
    """Loading-bay scan. Validates product match + duplicates. Returns carton."""
    resolved = _dispatch_actor(path)
    cn = parse_scan(scan_text)
    if not cn:
        raise ValueError("Empty scan. Scan the box QR again.")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            o = c.execute("SELECT * FROM dispatch_orders WHERE id=?", (order_id,)).fetchone()
            if not o:
                raise ValueError("Order not found")
            if o["status"] != "loading":
                raise ValueError(f"Order is {o['status']}. Only loading orders accept scans.")
            ct = c.execute("SELECT * FROM cartons WHERE carton_no=?", (cn,)).fetchone()
            if not ct:
                raise ValueError(f"Box {cn} not found. Check the QR.")
            ct = dict(ct)
            if ct["status"] != "Ready":
                raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} is already {ct['status']}.")
            if ct["job_id"] != o["job_id"]:
                j = c.execute("SELECT job_no, job_name FROM jobs WHERE id=?", (ct["job_id"],)).fetchone()
                raise ValueError(
                    f"Product mismatch! Box {bare_carton_no(ct['carton_no'])} is J:{j['job_no']} {j['job_name']}. Order needs another job.")
            dup = c.execute("SELECT order_id FROM dispatch_items WHERE carton_id=?", (ct["id"],)).fetchone()
            if dup:
                if dup["order_id"] == order_id:
                    raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} already scanned in this order (duplicate).")
                raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} is already loaded in another order.")
            items = _order_items(c, order_id)
            rp = o["required_pieces"]
            rq = o["required_weight_kg"]
            unit = str(o["piece_unit"] or "reel")
            job = c.execute("SELECT wrappers_per_reel FROM jobs WHERE id=?",
                            (o["job_id"],)).fetchone()
            wpr = int((job["wrappers_per_reel"] if job else 0) or 0)
            if rp is not None and unit == "wrapper" and wpr <= 0:
                raise ValueError(
                    "This order counts wrappers, but its job has no wrappers-per-reel "
                    "count. Set it on the product/job before loading boxes.")
            pieces_before = _piece_counts(items, unit, wpr)
            net_before = round(sum(float(x["total_net"] or 0) for x in items), 3)
            if rp is not None:
                # Piece-based order: stop once the target is reached. The
                # scan that crosses the target is accepted whole (a 3-coil
                # box may take 190,000 -> 306,000 when 200,000 are needed);
                # the UI flags the overshoot so the operator can remove it.
                rp = int(rp)
                if pieces_before >= rp:
                    raise ValueError(
                        f"Order already full ({pieces_before:,}/{rp:,} {_unit_word(unit)} loaded)."
                        " Complete it first.")
                if int(ct["num_reels"] or 0) <= 0:
                    raise ValueError(
                        f"Box {bare_carton_no(ct['carton_no'])} has no linked reels — it cannot load on a"
                        " piece-based order.")
            elif rq is not None:
                try:
                    rq = float(rq)
                except (TypeError, ValueError):
                    raise ValueError("Order kg target invalid — contact admin")
                if net_before >= rq:
                    raise ValueError(
                        f"Order already full ({net_before:.3f}/{rq:.3f} kg loaded)."
                        " Complete it first.")
            elif len(items) >= int(o["required_bundles"]):
                raise ValueError(f"Order already full ({o['required_bundles']} bundles). Complete it first.")
            next_box_no = len(items) + 1
            try:
                c.execute(
                    "INSERT INTO dispatch_items(order_id, carton_id, client_box_no, scanned_at)"
                    " VALUES(?,?,?,?)",
                    (order_id, ct["id"], next_box_no, now_iso()))
            except sqlite3.IntegrityError:
                raise ValueError(f"Box {bare_carton_no(ct['carton_no'])} already scanned (duplicate).")
            loaded_now = pieces_before + (
                int(ct["num_reels"] or 0) * wpr if unit == "wrapper"
                else int(ct["num_reels"] or 0))
            loaded_net_now = round(net_before + float(ct["total_net"] or 0), 3)
            if rq is not None:
                audit_extra = f" ({loaded_net_now:.3f}/{float(rq):.3f} kg)"
            elif rp is not None:
                audit_extra = f" ({loaded_now:,}/{int(rp):,} {_unit_word(unit)})"
            else:
                audit_extra = ""
            _audit(c, resolved, "scan", "order", order_id,
                   f"{cn} loaded as client box {next_box_no}" + audit_extra)
            ct["client_box_no"] = next_box_no
            ct["loaded_pieces"] = loaded_now
            ct["loaded_net"] = loaded_net_now
            ct["piece_unit"] = unit
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return ct
    finally:
        c.close()


def remove_from_order(order_id: int, carton_id: int, path: str | None = None,
                      actor: dict | None = None):
    """Remove a scanned carton from a LOADING order only. Completed or
    cancelled orders are immutable (safe remove)."""
    resolved = _dispatch_actor(path)
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            o = c.execute("SELECT status FROM dispatch_orders WHERE id=?", (order_id,)).fetchone()
            if not o:
                raise ValueError("Order not found")
            if o["status"] != "loading":
                raise ValueError(f"Order is {o['status']} — scanned boxes can only be removed while loading.")
            cur = c.execute("DELETE FROM dispatch_items WHERE order_id=? AND carton_id=?", (order_id, carton_id))
            if cur.rowcount == 0:
                raise ValueError("Box is not in this order.")
            # Keep the client-facing sequence contiguous 1..N after a removal.
            left = c.execute("SELECT id FROM dispatch_items WHERE order_id=? ORDER BY id",
                             (order_id,)).fetchall()
            for i, row in enumerate(left, 1):
                c.execute("UPDATE dispatch_items SET client_box_no=? WHERE id=?",
                          (i, row["id"]))
            _audit(c, resolved, "unscan", "order", order_id,
                   f"carton_id={carton_id} removed; boxes renumbered 1..{len(left)}")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


def complete_dispatch(order_id: int, path: str | None = None,
                      actor: dict | None = None) -> dict:
    """Complete a loading order once the target is reached.

    Piece orders complete when loaded pieces >= required; kg orders when
    loaded net >= required kg (crossing box accepted whole); bundle orders
    keep the exact count. Completed/cancelled orders cannot be completed again."""
    resolved = _dispatch_actor(path)
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            o = c.execute("SELECT * FROM dispatch_orders WHERE id=?", (order_id,)).fetchone()
            if not o:
                raise ValueError("Order not found")
            if o["status"] != "loading":
                raise ValueError(f"Order is {o['status']} — only loading orders can be completed.")
            items = _order_items(c, order_id)
            if not items:
                raise ValueError("Nothing scanned yet. Scan at least 1 box.")
            rp = o["required_pieces"]
            rq = o["required_weight_kg"]
            unit = str(o["piece_unit"] or "reel")
            job = c.execute("SELECT wrappers_per_reel FROM jobs WHERE id=?",
                            (o["job_id"],)).fetchone()
            wpr = int((job["wrappers_per_reel"] if job else 0) or 0)
            if rp is not None:
                rp = int(rp)
                pieces = _piece_counts(items, unit, wpr)
                if pieces < rp:
                    raise ValueError(
                        f"Partial load: {pieces:,}/{rp:,} {_unit_word(unit)} scanned."
                        " Scan the remaining boxes or remove this order's extras first.")
                done_note = f"{pieces:,}/{rp:,} {_unit_word(unit)}"
            elif rq is not None:
                try:
                    rq = float(rq)
                except (TypeError, ValueError):
                    raise ValueError("Order kg target invalid — contact admin")
                loaded_net = round(sum(float(x["total_net"] or 0) for x in items), 3)
                if loaded_net < rq:
                    raise ValueError(
                        f"Partial load: {loaded_net:.3f}/{rq:.3f} kg scanned."
                        " Scan the remaining boxes or remove this order's extras first.")
                done_note = f"{loaded_net:.3f}/{rq:.3f} kg"
            else:
                if len(items) != int(o["required_bundles"]):
                    raise ValueError(
                        f"Partial load: {len(items)}/{o['required_bundles']} scanned."
                        " Scan the remaining boxes or remove this order's extras first.")
                done_note = f"{len(items)}/{o['required_bundles']} bundles"
            for it in items:
                c.execute("UPDATE cartons SET status='Dispatched', dispatched_at=? WHERE id=?",
                          (now_iso(), it["id"]))
                c.execute("UPDATE reels SET status='dispatched' WHERE id IN"
                          " (SELECT reel_id FROM carton_items WHERE carton_id=?)", (it["id"],))
            c.execute("UPDATE dispatch_orders SET status='complete', completed_at=? WHERE id=?",
                      (now_iso(), order_id))
            _audit(c, resolved, "complete_order", "order", order_id,
                   f"{o['order_no']} {done_note} dispatched")
            slip = _freeze_dispatch_slip_locked(c, order_id)
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        done = order_progress(order_id, path)
        done["slip"] = slip
        done["slip_id"] = slip["slip_id"]
        return done
    finally:
        c.close()


def cancel_order(order_id: int, path: str | None = None,
                 actor: dict | None = None):
    """Cancel a loading order only. Scanned cartons go back to Ready (they
    were never dispatched). Completed/cancelled orders cannot be cancelled
    (safe cancel)."""
    resolved = _dispatch_actor(path)
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            o = c.execute("SELECT status, order_no FROM dispatch_orders WHERE id=?",
                          (order_id,)).fetchone()
            if not o:
                raise ValueError("Order not found")
            if o["status"] != "loading":
                raise ValueError(f"Order is {o['status']} — only loading orders can be cancelled.")
            c.execute("DELETE FROM dispatch_items WHERE order_id=?", (order_id,))
            c.execute("UPDATE dispatch_orders SET status='cancelled', completed_at=? WHERE id=?",
                      (now_iso(), order_id))
            _audit(c, resolved, "cancel_order", "order", order_id, f"{o['order_no']} cancelled")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        c.close()


# ---------- wide client sticker ----------
def _batch_code(ts: str) -> str:
    """Batch shown on the wide sticker: SVSF/<MON>-<DD> from the mfg date.

    The sample (SVSF/OCT-16) matches the coil mfg date, not the pack date.
    """
    try:
        d = datetime.strptime(str(ts or "")[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        d = datetime.now()
    return f"SVSF/{d:%b}-{d.day:02d}".upper()


def _setting_from(c: sqlite3.Connection, key: str, default: str = "") -> str:
    row = c.execute("SELECT value FROM settings WHERE key=?", (str(key),)).fetchone()
    return str(row["value"]) if row else default


def get_setting(key: str, default: str = "", path: str | None = None) -> str:
    c = get_conn(path)
    try:
        return _setting_from(c, key, default)
    finally:
        c.close()


def get_company_logo(path: str | None = None) -> str:
    return get_setting("company_logo", "", path=path)


def set_company_logo(logo_b64: str, path: str | None = None,
                     actor: dict | None = None) -> dict:
    """Admin-only. Store a normalized PNG logo (base64) in settings.

    Empty string clears the logo. The image is validated/re-encoded and
    downscaled so the LAN JSON request stays small and every PC prints the
    same bytes. The logo is part of the shared DB, not a local file.
    """
    resolved = _settings_actor(path)
    data = (logo_b64 or "").strip()
    if data:
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            raise ValueError("Logo must be a base64-encoded image")
        if len(raw) > 768 * 1024:
            raise ValueError("Logo is too large (max 750 KB)")
        try:
            from PIL import Image
            img = Image.open(io.BytesIO(raw))
            img.load()
            img = img.convert("RGBA")
            # A 1200px-wide logo is plenty for a 203dpi 210mm label and
            # keeps the shared DB/JSON payload comfortably small.
            img.thumbnail((1200, 800), Image.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="PNG", optimize=True)
        except Exception:
            raise ValueError("Logo could not be read as a PNG/JPEG image")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            c.execute(
                "INSERT INTO settings(key, value, updated_at) VALUES('company_logo',?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (data, now_iso()))
            _audit(c, resolved, "set_company_logo", "setting", 0,
                   "logo cleared" if not data else "logo updated")
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
        return {"ok": True, "has_logo": bool(data)}
    finally:
        c.close()


def client_label_detail(order_id: int, carton_id: int,
                        path: str | None = None) -> dict:
    """Everything the wide client sticker renders, for ONE loaded box.

    The sticker lists the coils inside the box and the client-facing box
    number (`dispatch_items.client_box_no`), never the scanned carton code.
    Customer PO and the optional batch override come from the dispatch order;
    a blank batch derives SVSF/MON-DD from the earliest coil mfg date.
    """
    c = get_conn(path)
    try:
        o = c.execute(
            "SELECT o.*, j.job_no, j.job_name, j.product_name, j.wrappers_per_reel"
            " FROM dispatch_orders o JOIN jobs j ON j.id=o.job_id WHERE o.id=?",
            (order_id,)).fetchone()
        if not o:
            raise ValueError("Order not found")
        ct = c.execute("SELECT * FROM cartons WHERE id=?", (carton_id,)).fetchone()
        if not ct:
            raise ValueError("Box not found")
        di = c.execute(
            "SELECT * FROM dispatch_items WHERE order_id=? AND carton_id=?",
            (order_id, carton_id)).fetchone()
        if not di:
            raise ValueError("Box is not loaded in this order.")
        coils = c.execute(
            "SELECT r.* FROM reels r JOIN carton_items ci ON ci.reel_id=r.id"
            " WHERE ci.carton_id=? ORDER BY r.id", (carton_id,)).fetchall()
        wpr = int(o["wrappers_per_reel"] or 0)
        rows = []
        for r in coils:
            try:
                _rn = int(r["prod_seq"]) if r["prod_seq"] is not None else int(r["roll_no"])
            except (TypeError, ValueError):
                _rn = int(r["roll_no"])
            rows.append({
                "roll_no": _rn,
                "pieces": wpr,
                "gross": float(r["gross"]),
                "core": float(r["core"]),
                "net": float(r["net"]),
                "mfg_date": str(r["created_at"] or ""),
            })
        mfg_dates = [r["mfg_date"] for r in rows if r["mfg_date"]]
        batch = (o["batch_no"] or "").strip()
        if not batch:
            batch = _batch_code(min(mfg_dates) if mfg_dates else ct["packed_at"])
        total_gross = sum(r["gross"] for r in rows)
        total_tare = sum(r["core"] for r in rows)
        total_net = sum(r["net"] for r in rows)
        if not rows:
            total_gross = float(ct["total_gross"] or 0)
            total_tare = float(ct["total_core"] or 0)
            total_net = float(ct["total_net"] or 0)
        return {
            "order_no": o["order_no"],
            "customer": o["customer"],
            "customer_po": o["customer_po"] or "",
            "po_date": o["po_date"] if "po_date" in o.keys() else "",
            "batch_no": batch,
            "job_no": o["job_no"],
            "job_name": o["job_name"],
            "product_name": o["product_name"] or o["job_name"],
            "carton_no": ct["carton_no"],
            "bundle_no": int(ct["bundle_no"] or 0),
            "packed_at": str(ct["packed_at"] or ""),
            "client_box_no": int(di["client_box_no"] or 1),
            "num_coils": len(rows),
            "wrappers_per_reel": wpr,
            "coils": rows,
            "total_pieces": sum(r["pieces"] for r in rows),
            "total_gross": round(total_gross, 3),
            "total_tare": round(total_tare, 3),
            "total_net": round(total_net, 3),
            "company_name": _setting_from(c, "company_name", ""),
            "company_logo": _setting_from(c, "company_logo", ""),
        }
    finally:
        c.close()


# ---------- daily summary ----------
def daily_summary(day: str | None = None, path: str | None = None) -> dict:
    day = day or today_iso()
    c = get_conn(path)
    try:
        r1 = c.execute("SELECT COUNT(*) n, COALESCE(SUM(net),0) w FROM reels WHERE date(created_at)=?",
                       (day,)).fetchone()
        r2 = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_net),0) w FROM cartons WHERE date(packed_at)=?",
                       (day,)).fetchone()
        r3 = c.execute("SELECT COUNT(*) n FROM cartons WHERE status='Ready'").fetchone()
        r4 = c.execute("SELECT COUNT(*) n, COALESCE(SUM(total_net),0) w FROM cartons WHERE date(dispatched_at)=?",
                       (day,)).fetchone()
        return {
            "day": day,
            "reels_packed": r1["n"], "reel_net": round(r1["w"], 3),
            "cartons_packed": r2["n"], "packed_wt": round(r2["w"], 3),
            "in_stock": r3["n"],
            "dispatched": r4["n"], "dispatched_wt": round(r4["w"], 3),
        }
    finally:
        c.close()


# ---------- daily report (day-scoped line items across all jobs) ----------
def day_report(day: str | None = None, path: str | None = None) -> dict:
    """Every reel weighed + every box packed on one day, across ALL jobs.

    Read-only (never writes). Reel rows: `reels JOIN jobs WHERE
    date(created_at)=day` with month-qualified roll identity via the SAME
    helper as get_slip (`_roll_label`). Box rows: `cartons JOIN jobs WHERE
    date(packed_at)=day` with carton_no / bundle_no / num_reels /
    total_gross and the factual linked rolls (carton_items membership only,
    nothing inferred). Totals keep reel and box weights SEPARATE — reel
    weights are never merged into box weights (same rule as get_slip).
    """
    import re as _re
    day = day or today_iso()
    if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        raise ValueError("day must be YYYY-MM-DD")
    from .slip_pdf import _roll_label
    c = get_conn(path)
    try:
        reels = [dict(r) for r in c.execute(
            "SELECT r.*, j.job_no AS job_no, j.job_name AS job_name"
            " FROM reels r JOIN jobs j ON j.id=r.job_id"
            " WHERE date(r.created_at)=? ORDER BY j.job_no, r.roll_no, r.id",
            (day,)).fetchall()]
        cartons = [dict(r) for r in c.execute(
            "SELECT ct.*, j.job_no AS job_no, j.job_name AS job_name"
            " FROM cartons ct JOIN jobs j ON j.id=ct.job_id"
            " WHERE date(ct.packed_at)=? ORDER BY j.job_no, ct.bundle_no, ct.id",
            (day,)).fetchall()]

        # Factual carton_items links only; a bare roll_no is never displayed
        # alone (labels are month-qualified). No membership is inferred.
        reel_to_carton: dict[int, str] = {}
        for ci in c.execute(
                "SELECT ci.reel_id, ct.carton_no"
                " FROM carton_items ci JOIN cartons ct ON ct.id=ci.carton_id"
                " JOIN reels r ON r.id=ci.reel_id"
                " WHERE date(r.created_at)=?", (day,)).fetchall():
            reel_to_carton[int(ci["reel_id"])] = ci["carton_no"]

        carton_to_rolls: dict[int, list[int]] = {}
        carton_to_labels: dict[int, list[str]] = {}
        for ci in c.execute(
                "SELECT ci.carton_id, r.roll_no, r.roll_period, r.roll_code,"
                " j.job_no AS job_no"
                " FROM carton_items ci JOIN cartons ct ON ct.id=ci.carton_id"
                " JOIN reels r ON r.id=ci.reel_id"
                " JOIN jobs j ON j.id=ct.job_id"
                " WHERE date(ct.packed_at)=? ORDER BY ci.carton_id, r.id",
                (day,)).fetchall():
            carton_to_rolls.setdefault(int(ci["carton_id"]), []).append(int(ci["roll_no"]))
            carton_to_labels.setdefault(int(ci["carton_id"]), []).append(
                _roll_label(dict(ci), ci["job_no"]))
        for v in carton_to_rolls.values():
            v.sort()
    finally:
        c.close()

    reel_rows: list[dict] = []
    for r in reels:
        reel_rows.append({
            "kind": "Reel",
            "roll_no": int(r["roll_no"]),
            "roll_period": (r.get("roll_period") or ""),
            "roll_code": (r.get("roll_code") or ""),
            "roll_label": _roll_label(r, r["job_no"]),
            "job_no": int(r["job_no"]),
            "job_name": (r.get("job_name") or ""),
            "num_reels": 1,
            "weight_only": False,
            "gross": round(float(r["gross"]), 3),
            "core": round(float(r["core"]), 3),
            "net": round(float(r["net"]), 3),
            "status": r.get("status", ""),
            "carton_no": reel_to_carton.get(int(r["id"]), ""),
        })

    box_rows: list[dict] = []
    for ct in cartons:
        linked_roll_nos = carton_to_rolls.get(int(ct["id"]), [])
        linked = bool(linked_roll_nos) or int(ct.get("num_reels") or 0) != 0
        stored_gross = round(float(ct["total_gross"]), 3)
        box_rows.append({
            "kind": "Box",
            "carton_no": ct["carton_no"],
            "bundle_no": int(ct["bundle_no"]),
            "box_period": (ct.get("box_period") or ""),
            "num_reels": int(ct.get("num_reels") or 0),
            "weight_only": not linked,
            "linked": linked,
            "job_no": int(ct["job_no"]),
            "job_name": (ct.get("job_name") or ""),
            "rolls": ",".join(str(n) for n in linked_roll_nos),
            "roll_refs": ", ".join(carton_to_labels.get(int(ct["id"]), [])),
            "gross": stored_gross,
            "core": 0.0,
            "net": 0.0,
            "status": ct.get("status", ""),
            "note": ("legacy reel-linked box" if linked
                     else "independent weighed box"),
        })

    # Totals stay separate: reels counted once from the reels table; boxes
    # summed from stored carton weights. Box weights are never added to
    # reel totals.
    rg = round(sum(r["gross"] for r in reel_rows), 3)
    rc = round(sum(r["core"] for r in reel_rows), 3)
    rn = round(sum(r["net"] for r in reel_rows), 3)
    bg = round(sum(b["gross"] for b in box_rows), 3)

    by_job: dict[int, dict] = {}
    for r in reel_rows:
        j = by_job.setdefault(r["job_no"], {
            "job_no": r["job_no"], "job_name": r["job_name"],
            "reels": 0, "reel_net": 0.0, "boxes": 0, "box_gross": 0.0})
        j["reels"] += 1
        j["reel_net"] = round(j["reel_net"] + r["net"], 3)
    for b in box_rows:
        j = by_job.setdefault(b["job_no"], {
            "job_no": b["job_no"], "job_name": b["job_name"],
            "reels": 0, "reel_net": 0.0, "boxes": 0, "box_gross": 0.0})
        j["boxes"] += 1
        j["box_gross"] = round(j["box_gross"] + b["gross"], 3)

    return {
        "day": day,
        "reel_rows": reel_rows,
        "box_rows": box_rows,
        "totals": {
            "reels": len(reel_rows),
            "reel_gross": rg,
            "reel_core": rc,
            "reel_net": rn,
            "boxes": len(box_rows),
            "box_gross": bg,
        },
        "by_job": [by_job[k] for k in sorted(by_job)],
        "rows": reel_rows + box_rows,
    }


def packing_slip_rows(job_id: int, path: str | None = None) -> tuple[dict, list[dict]]:
    """Rows for the A4 packing slip (matches old format): one row per bundle."""
    c = get_conn(path)
    try:
        job = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if not job:
            raise ValueError("Job not found")
        rows = c.execute(
            "SELECT * FROM cartons WHERE job_id=? ORDER BY bundle_no", (job_id,)).fetchall()
        out = [dict(r) for r in rows]
        linked = [r for r in out if int(r["num_reels"] or 0) > 0]
        boxes = [r for r in out if int(r["num_reels"] or 0) == 0]
        tg = round(sum(r["total_gross"] for r in linked), 3)
        tc = round(sum(r["total_core"] for r in linked), 3)
        tn = round(sum(r["total_net"] for r in linked), 3)
        nr = sum(r["num_reels"] for r in linked)
        bw = round(sum(r["total_gross"] for r in boxes), 3)
        totals = {"bundles": len(out), "reels": nr, "gross": tg, "core": tc,
                  "net": tn, "boxes": len(boxes), "box_wt": bw}
        return dict(job), out, totals
    finally:
        c.close()
