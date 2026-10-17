"""Authenticated LAN data service (packager computer hosts the shared DB).

Architecture (GPT6.1 decision, confirmed by user): SQLite stays a LOCAL file
on the packager computer. This module exposes ONE authenticated HTTPS service
backed by that single file; native Qt clients talk to it over the LAN via
factory/lan_client.py. There is deliberately:

- no shared-file SQLite access (no SMB/NFS DB opens),
- no DB copies / sync / merge,
- no local-write fallback in clients,
- no SQL, file paths, periods, actor dicts, or roles accepted from clients.

Wire protocol (stdlib only: http.server + urllib + ssl):
    POST /api   JSON {"op": "<allowlisted>", "args": {...}, "request_id": "...?"}
                Header X-Session-Token: <opaque token> (except login)
    GET  /health  unauthenticated liveness probe -> {"ok": true, ...}

Responses are always JSON: {"ok": true, "data": ...} or
{"ok": false, "error": {"code": ..., "message": ...}} with an HTTP status of
401/403/409/413/422/429/500. Messages are generic and safe: they never echo
tokens, passwords, tracebacks, or server paths.

Auth model:
- login validates credentials against the REAL server DB via
  auth.authenticate() and returns the opaque session token.
- Every other op resolves the caller via auth.require_permission() against
  the server DB on EVERY call (demoted/deleted users lose access
  immediately; forged tokens are denied).
- The resolved server-side {"username", "role"} is passed as actor= into
  service calls that accept it, so the services layer enforces RBAC a
  second time. Client-supplied actor/role arguments are rejected outright.
- Login is rate-limited per source IP (bounded in-memory table).

Reliability contract (honest, no invented guarantees):
- The client NEVER auto-retries writes. An optional caller-supplied
  request_id (string, tracing only) is validated and echoed, NOT deduplicated:
  the server implements NO idempotency store.
- Consequence: if a write call raises a connection/timeout error, its
  outcome is UNKNOWN (it may have committed). Callers MUST refresh via a
  read op (list/ready_stock/order_progress/...) and only then decide whether
  a retry with a NEW request_id is safe. Retrying blindly can create a
  duplicate. See LAN_SETUP.md.

Startup: python -m factory.lan_server --bind <ip> --port <port> \\
    --db <server-local factory.db> --cert <cert.pem> --key <key.pem>
All four flags are required; TLS is mandatory. The server refuses to start
against a missing DB or a DB with zero users (the main app must run
init_db + first-run admin setup first). Files/backups/hardware/printing stay
local to each machine and are never exposed here.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import auth as _auth
from . import db as _db
from . import services as _svc
from . import slip_pdf as _slip

# --------------------------------------------------------------------------
# Limits
# --------------------------------------------------------------------------

MAX_BODY_BYTES = 256 * 1024          # request bound (413 over)
MAX_STR_LEN = 512                    # generic string arg bound
MAX_TOKEN_LEN = 256
MAX_REQUEST_ID_LEN = 64
SESSION_HEADER = "X-Session-Token"

# Login rate limit: >MAX_LOGIN_FAILS failures from one IP inside
# LOGIN_WINDOW_S -> 429 until the window slides. Bounded: at most
# _MAX_TRACKED_IPS entries kept.
MAX_LOGIN_FAILS = 10
LOGIN_WINDOW_S = 300.0
_MAX_TRACKED_IPS = 512

_LOGIN_FAILS: dict[str, list[float]] = {}
_LOGIN_FAILS_LOCK = threading.Lock()


def _login_allowed(ip: str) -> bool:
    now = time.monotonic()
    with _LOGIN_FAILS_LOCK:
        hits = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < LOGIN_WINDOW_S]
        _LOGIN_FAILS[ip] = hits
        return len(hits) < MAX_LOGIN_FAILS


def _record_login_failure(ip: str) -> None:
    now = time.monotonic()
    with _LOGIN_FAILS_LOCK:
        hits = [t for t in _LOGIN_FAILS.get(ip, []) if now - t < LOGIN_WINDOW_S]
        hits.append(now)
        if len(_LOGIN_FAILS) >= _MAX_TRACKED_IPS and ip not in _LOGIN_FAILS:
            _LOGIN_FAILS.pop(next(iter(_LOGIN_FAILS)), None)
        _LOGIN_FAILS[ip] = hits


def _clear_login_failures(ip: str) -> None:
    with _LOGIN_FAILS_LOCK:
        _LOGIN_FAILS.pop(ip, None)


# --------------------------------------------------------------------------
# Errors (mapped to HTTP status; messages stay generic and safe)
# --------------------------------------------------------------------------

class _AuthRequired(Exception):
    """Missing/invalid/expired session (HTTP 401)."""


class _ValidationError(Exception):
    """Bad op/args/request shape (HTTP 422)."""


class _ConflictError(Exception):
    """State conflict incl. duplicate/integrity (HTTP 409)."""


class _RateLimited(Exception):
    """Login rate limit hit (HTTP 429)."""


_CONFLICT_HINTS = ("already", "duplicate", "exists", "full", "mismatch",
                   "loaded in", "in another order")


def _service_error(exc: Exception) -> tuple[int, str, str]:
    """Map a services-layer exception to (status, code, safe message)."""
    if isinstance(exc, _AuthRequired):
        return 401, "auth_required", str(exc) or "Authentication required."
    if isinstance(exc, PermissionError):
        msg = str(exc) or "Forbidden."
        if "no authenticated session" in msg.lower():
            return 401, "auth_required", "Authentication required."
        return 403, "forbidden", msg
    if isinstance(exc, (_ValidationError, json.JSONDecodeError)):
        return 422, "validation", str(exc) or "Invalid request."
    if isinstance(exc, _ConflictError) or isinstance(exc, sqlite3.IntegrityError):
        return 409, "conflict", str(exc) or "Conflicting state."
    if isinstance(exc, ValueError):
        msg = str(exc) or "Invalid value."
        lowered = msg.lower()
        if any(h in lowered for h in _CONFLICT_HINTS):
            return 409, "conflict", msg
        return 422, "validation", msg
    if isinstance(exc, _RateLimited):
        return 429, "rate_limited", str(exc) or "Too many attempts; try later."
    return 500, "server", "Internal error."


# --------------------------------------------------------------------------
# Strict argument validation (exact args only; rejects path/period/actor/...)
# --------------------------------------------------------------------------

# Argument names that are NEVER accepted from a client on any op.
FORBIDDEN_ARGS = frozenset({
    "path", "db", "db_path", "period", "actor", "role", "token",
    "session_token", "pass_hash", "password_hash", "username_lower",
})


def _reject_forbidden(args: dict) -> None:
    for key in args:
        if key in FORBIDDEN_ARGS:
            raise _ValidationError(
                f"Argument '{key}' is never accepted over LAN.")


def _check_exact(args: dict, allowed: frozenset[str], op: str) -> None:
    unknown = set(args) - allowed
    if unknown:
        raise _ValidationError(
            f"Unknown argument(s) for '{op}': {sorted(unknown)[0]}.")


def _v_str(args: dict, key: str, *, required: bool = True,
           max_len: int = MAX_STR_LEN, allow_empty: bool = False,
           upper: bool = False) -> str | None:
    if key not in args:
        if required:
            raise _ValidationError(f"Missing argument '{key}'.")
        return None
    val = args[key]
    if not isinstance(val, str):
        raise _ValidationError(f"Argument '{key}' must be a string.")
    if not allow_empty and not val.strip():
        raise _ValidationError(f"Argument '{key}' must not be empty.")
    if len(val) > max_len:
        raise _ValidationError(f"Argument '{key}' is too long.")
    return val.strip().upper() if upper else val.strip()


def _v_opt_str(args: dict, key: str, *, max_len: int = MAX_STR_LEN,
               upper: bool = False, default: str = "") -> str:
    if key not in args or args[key] is None:
        return default
    val = args[key]
    if not isinstance(val, str):
        raise _ValidationError(f"Argument '{key}' must be a string.")
    if len(val) > max_len:
        raise _ValidationError(f"Argument '{key}' is too long.")
    val = val.strip()
    return val.upper() if upper else val


def _v_int(args: dict, key: str, *, required: bool = True,
           minimum: int | None = None, maximum: int | None = None,
           default: int | None = None) -> int | None:
    if key not in args or args[key] is None:
        if required:
            raise _ValidationError(f"Missing argument '{key}'.")
        return default
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, int):
        raise _ValidationError(f"Argument '{key}' must be a whole number.")
    if minimum is not None and val < minimum:
        raise _ValidationError(f"Argument '{key}' is too small.")
    if maximum is not None and val > maximum:
        raise _ValidationError(f"Argument '{key}' is too large.")
    return val


def _v_float(args: dict, key: str, *, required: bool = True,
             minimum: float | None = None, maximum: float | None = None,
             default: float | None = None) -> float | None:
    if key not in args or args[key] is None:
        if required:
            raise _ValidationError(f"Missing argument '{key}'.")
        return default
    val = args[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        raise _ValidationError(f"Argument '{key}' must be a number.")
    val = float(val)
    if minimum is not None and val < minimum:
        raise _ValidationError(f"Argument '{key}' is too small.")
    if maximum is not None and val > maximum:
        raise _ValidationError(f"Argument '{key}' is too large.")
    return val


def _v_int_list(args: dict, key: str, *, required: bool = True,
                max_items: int = 3) -> list[int]:
    if key not in args:
        if required:
            raise _ValidationError(f"Missing argument '{key}'.")
        return []
    val = args[key]
    if not isinstance(val, list) or not val:
        raise _ValidationError(f"Argument '{key}' must be a non-empty list.")
    if len(val) > max_items:
        raise _ValidationError(f"Argument '{key}' has too many items.")
    out: list[int] = []
    for item in val:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise _ValidationError(f"Argument '{key}' must hold positive ids.")
        out.append(item)
    return out


def _v_enum(args: dict, key: str, choices: tuple[str, ...], *,
            required: bool = True, default: str | None = None) -> str | None:
    if key not in args or args[key] is None:
        if required:
            raise _ValidationError(f"Missing argument '{key}'.")
        return default
    val = args[key]
    if not isinstance(val, str) or val.strip().upper() not in choices:
        raise _ValidationError(
            f"Argument '{key}' must be one of {', '.join(choices)}.")
    return val.strip().upper()


# --------------------------------------------------------------------------
# Operation handlers: (permission, allowed args, callable)
# permission None -> login (no session); "SESSION" -> any valid session.
# --------------------------------------------------------------------------

Handler = Callable[[dict, dict, str], Any]
# handler signature: (args, actor, db_path) -> JSON-serializable data.
# actor is {} for login; for SESSION ops it is the resolved {"username","role"}.

OpSpec = tuple[str | None, frozenset[str], Handler]


def _h_login(args: dict, _actor: dict, db_path: str, ip: str = "") -> dict:
    username = _v_str(args, "username", max_len=64)
    password = args.get("password")
    if not isinstance(password, str) or not password or len(password) > 256:
        _record_login_failure(ip)
        raise _AuthRequired("Invalid username or password.")
    assert username is not None
    if not _login_allowed(ip):
        raise _RateLimited("Too many login attempts; try again later.")
    rec = _auth.authenticate(username, password, db_path)
    if rec is None:
        _record_login_failure(ip)
        raise _AuthRequired("Invalid username or password.")
    _clear_login_failures(ip)
    return {"username": rec["username"], "role": rec["role"],
            "session_token": rec["session_token"]}


def _need(args: dict, op: str, allowed: frozenset[str]) -> None:
    _reject_forbidden(args)
    _check_exact(args, allowed, op)


OPS: dict[str, OpSpec] = {}


def _op(name: str, perm: str | None, allowed: tuple[str, ...]):
    def deco(fn: Handler) -> Handler:
        OPS[name] = (perm, frozenset(allowed), fn)
        return fn
    return deco


@_op("login", None, ("username", "password"))
def _op_login(args: dict, actor: dict, dbp: str) -> dict:
    raise AssertionError("login is handled inline (needs client IP)")


@_op("logout", "SESSION", ())
def _op_logout(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "logout", frozenset())
    _auth.logout_current()
    return {"ok": True}


@_op("whoami", "SESSION", ())
def _op_whoami(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "whoami", frozenset())
    return dict(actor)


@_op("list_products", "read", ())
def _h_list_products(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "list_products", frozenset())
    return _svc.list_products(dbp)


@_op("get_product", "read", ("product_id",))
def _h_get_product(args: dict, actor: dict, dbp: str) -> dict | None:
    _need(args, "get_product", frozenset({"product_id"}))
    return _svc.get_product(_v_int(args, "product_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("create_product", "product.manage", ("name", "wrappers_per_reel"))
def _h_create_product(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "create_product", frozenset({"name", "wrappers_per_reel"}))
    name = _v_str(args, "name", max_len=256)
    w = _v_int(args, "wrappers_per_reel", minimum=1, maximum=10_000_000)
    assert name is not None and w is not None
    return _svc.create_product(name, w, dbp)


@_op("set_product_wrappers", "product.manage",
     ("product_id", "wrappers_per_reel"))
def _h_set_wrappers(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "set_product_wrappers",
          frozenset({"product_id", "wrappers_per_reel"}))
    pid = _v_int(args, "product_id", minimum=1)
    w = _v_int(args, "wrappers_per_reel", minimum=1, maximum=10_000_000)
    assert pid is not None and w is not None
    return _svc.set_product_wrappers(pid, w, dbp, actor=actor)


@_op("list_jobs", "read", ("status",))
def _h_list_jobs(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "list_jobs", frozenset({"status"}))
    status = _v_opt_str(args, "status", default="")
    if status and status not in ("open", "closed"):
        raise _ValidationError("Argument 'status' must be open or closed.")
    return _svc.list_jobs(status or None, dbp)


@_op("get_job", "read", ("job_no",))
def _h_get_job(args: dict, actor: dict, dbp: str) -> dict | None:
    _need(args, "get_job", frozenset({"job_no"}))
    return _svc.get_job_by_no(_v_int(args, "job_no", minimum=1), dbp)  # type: ignore[arg-type]


@_op("create_job", "job.manage", ("job_name", "size_mm", "core_wt"))
def _h_create_job(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "create_job", frozenset({"job_name", "size_mm", "core_wt"}))
    name = _v_str(args, "job_name", max_len=256)
    size = _v_float(args, "size_mm", required=False, minimum=0,
                    maximum=10_000, default=0) or 0
    core = _v_float(args, "core_wt", required=False, minimum=0.01,
                    maximum=500, default=0.5) or 0.5
    assert name is not None
    return _svc.create_job(name, size_mm=size, core_wt=core, path=dbp)


@_op("create_job_for_product", "job.manage", ("product_id", "core_wt"))
def _h_create_job_fp(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "create_job_for_product",
          frozenset({"product_id", "core_wt"}))
    pid = _v_int(args, "product_id", minimum=1)
    core = _v_float(args, "core_wt", required=False, minimum=0.01,
                    maximum=500, default=0.5) or 0.5
    assert pid is not None
    return _svc.create_job_for_product(pid, core_wt=core, path=dbp)


@_op("set_job_status", "job.manage", ("job_id", "status"))
def _h_set_job_status(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "set_job_status", frozenset({"job_id", "status"}))
    jid = _v_int(args, "job_id", minimum=1)
    status = _v_enum(args, "status", ("OPEN", "CLOSED"))
    assert jid is not None and status is not None
    _svc.set_job_status(jid, status.lower(), dbp)
    return {"ok": True}


@_op("delete_job", "delete", ("job_id",))
def _h_delete_job(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "delete_job", frozenset({"job_id"}))
    _svc.delete_job(_v_int(args, "job_id", minimum=1), dbp, actor=actor)  # type: ignore[arg-type]
    return {"ok": True}


@_op("job_stats", "read", ("job_id",))
def _h_job_stats(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "job_stats", frozenset({"job_id"}))
    return _svc.job_stats(_v_int(args, "job_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("loose_reels", "read", ("job_id",))
def _h_loose(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "loose_reels", frozenset({"job_id"}))
    out = _svc.loose_reels(_v_int(args, "job_id", minimum=1), dbp)  # type: ignore[arg-type]
    for d in out:
        d.pop("full_roll", None)
    from .db import full_roll_label as _lbl
    for d in out:
        d["full_roll"] = _lbl(d)
    return out


@_op("job_reels", "read", ("job_id",))
def _h_job_reels(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "job_reels", frozenset({"job_id"}))
    return _svc.job_reels(_v_int(args, "job_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("create_reel", "production.create",
     ("job_id", "gross", "core", "shift", "supervisor", "customer"))
def _h_create_reel(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "create_reel", frozenset(
        {"job_id", "gross", "core", "shift", "supervisor", "customer"}))
    jid = _v_int(args, "job_id", minimum=1)
    gross = _v_float(args, "gross", minimum=0.001, maximum=500)
    core = _v_float(args, "core", required=False, minimum=0.01, maximum=500)
    shift = _v_enum(args, "shift", ("DAY", "NIGHT"), required=False,
                    default="DAY") or "DAY"
    sup = _v_opt_str(args, "supervisor", max_len=128, upper=True)
    cust = _v_opt_str(args, "customer", max_len=128, upper=True)
    assert jid is not None and gross is not None
    return _svc.create_reel(jid, gross, core=core, shift=shift,
                            supervisor=sup, customer=cust, path=dbp,
                            actor=actor)


@_op("reweigh_reel", "production.reweigh",
     ("reel_id", "new_gross", "new_core"))
def _h_reweigh(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "reweigh_reel", frozenset({"reel_id", "new_gross", "new_core"}))
    rid = _v_int(args, "reel_id", minimum=1)
    gross = _v_float(args, "new_gross", minimum=0.001, maximum=500)
    core = _v_float(args, "new_core", required=False, minimum=0.01,
                    maximum=500)
    assert rid is not None and gross is not None
    return _svc.reweigh_reel(rid, gross, path=dbp, actor=actor, new_core=core)


@_op("delete_reel", "delete", ("reel_id",))
def _h_delete_reel(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "delete_reel", frozenset({"reel_id"}))
    _svc.delete_reel(_v_int(args, "reel_id", minimum=1), dbp, actor=actor)  # type: ignore[arg-type]
    return {"ok": True}


@_op("pack_weight_carton", "production.create",
     ("job_id", "gross_weight", "shift", "customer"))
def _h_pack_weight(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "pack_weight_carton", frozenset(
        {"job_id", "gross_weight", "shift", "customer"}))
    jid = _v_int(args, "job_id", minimum=1)
    w = _v_float(args, "gross_weight", minimum=0.001, maximum=500)
    shift = _v_enum(args, "shift", ("DAY", "NIGHT"), required=False,
                    default="DAY") or "DAY"
    cust = _v_opt_str(args, "customer", max_len=128, upper=True)
    assert jid is not None and w is not None
    return _svc.pack_weight_carton(jid, w, shift=shift, customer=cust,
                                   path=dbp, actor=actor)


@_op("pack_carton", "production.create",
     ("job_id", "reel_ids", "shift", "customer"))
def _h_pack_carton(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "pack_carton", frozenset(
        {"job_id", "reel_ids", "shift", "customer"}))
    jid = _v_int(args, "job_id", minimum=1)
    reels = _v_int_list(args, "reel_ids", max_items=3)
    shift = _v_enum(args, "shift", ("DAY", "NIGHT"), required=False,
                    default="DAY") or "DAY"
    cust_raw = args.get("customer", None)
    cust: str | None
    if cust_raw is None:
        cust = None
    elif isinstance(cust_raw, str) and len(cust_raw) <= MAX_STR_LEN:
        cust = cust_raw.strip().upper()
    else:
        raise _ValidationError("Argument 'customer' must be a string.")
    assert jid is not None
    return _svc.pack_carton(jid, reels, shift=shift, path=dbp,
                            customer=cust, actor=actor)


@_op("delete_carton", "delete", ("carton_id",))
def _h_delete_carton(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "delete_carton", frozenset({"carton_id"}))
    _svc.delete_carton(_v_int(args, "carton_id", minimum=1), dbp, actor=actor)  # type: ignore[arg-type]
    return {"ok": True}


@_op("mark_reel_printed", "production.print", ("reel_id",))
def _h_mark_reel(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "mark_reel_printed", frozenset({"reel_id"}))
    return _svc.mark_reel_printed(_v_int(args, "reel_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("mark_carton_printed", "production.print", ("carton_id",))
def _h_mark_carton(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "mark_carton_printed", frozenset({"carton_id"}))
    return _svc.mark_carton_printed(_v_int(args, "carton_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("ready_stock", "stock.read", ("job_id",))
def _h_ready_stock(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "ready_stock", frozenset({"job_id"}))
    jid = _v_int(args, "job_id", required=False, minimum=1)
    return _svc.ready_stock(jid, dbp)


@_op("find_carton", "stock.read", ("scan_text",))
def _h_find_carton(args: dict, actor: dict, dbp: str) -> dict | None:
    _need(args, "find_carton", frozenset({"scan_text"}))
    return _svc.find_carton(_v_str(args, "scan_text", max_len=128), dbp)


@_op("carton_detail", "stock.read", ("carton_id",))
def _h_carton_detail(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "carton_detail", frozenset({"carton_id"}))
    return _svc.carton_detail(_v_int(args, "carton_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("carton_verify", "stock.read", ("scan_text",))
def _h_carton_verify(args: dict, actor: dict, dbp: str) -> dict | None:
    _need(args, "carton_verify", frozenset({"scan_text"}))
    return _svc.carton_verify(_v_str(args, "scan_text", max_len=128), dbp)


@_op("predict_next_reel_seq", "read", ())
def _h_pred_reel(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "predict_next_reel_seq", frozenset())
    return {"next_seq": _svc.predict_next_reel_seq(path=dbp)}


@_op("predict_next_box_seq", "read", ())
def _h_pred_box(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "predict_next_box_seq", frozenset())
    return {"next_seq": _svc.predict_next_box_seq(path=dbp)}


@_op("todays_reels", "read", ())
def _h_today(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "todays_reels", frozenset())
    return _svc.todays_reels(dbp)


@_op("production_history", "history.read", ("limit",))
def _h_history(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "production_history", frozenset({"limit"}))
    limit = _v_int(args, "limit", required=False, minimum=1, maximum=2000)
    rows = _svc.production_history(dbp)
    return rows[: (limit if limit is not None else 200)]


@_op("list_audit", "history.read", ("entity", "entity_id", "limit"))
def _h_audit(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "list_audit", frozenset({"entity", "entity_id", "limit"}))
    entity = _v_opt_str(args, "entity", default="")
    if entity and entity not in ("reel", "carton", "job"):
        raise _ValidationError("Argument 'entity' must be reel/carton/job.")
    eid = _v_int(args, "entity_id", required=False, minimum=1)
    limit = _v_int(args, "limit", required=False, minimum=1, maximum=2000,
                   default=200) or 200
    return _svc.list_audit(entity or None, eid, dbp, limit)


@_op("create_dispatch_order", "dispatch.mutate",
     ("customer", "job_id", "required_bundles", "order_date"))
def _h_create_order(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "create_dispatch_order", frozenset(
        {"customer", "job_id", "required_bundles", "order_date"}))
    cust = _v_str(args, "customer", max_len=256)
    jid = _v_int(args, "job_id", minimum=1)
    n = _v_int(args, "required_bundles", minimum=1, maximum=100_000)
    date = _v_opt_str(args, "order_date", default="")
    if date:
        import re as _re
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise _ValidationError(
                "Argument 'order_date' must be YYYY-MM-DD.")
    assert cust is not None and jid is not None and n is not None
    return _svc.create_dispatch_order(cust, jid, n,
                                      order_date=date or None, path=dbp)


@_op("list_orders", "report.read", ("status",))
def _h_list_orders(args: dict, actor: dict, dbp: str) -> list:
    _need(args, "list_orders", frozenset({"status"}))
    status = _v_opt_str(args, "status", default="")
    if status and status not in ("loading", "complete", "cancelled"):
        raise _ValidationError(
            "Argument 'status' must be loading/complete/cancelled.")
    return _svc.list_orders(status or None, dbp)


@_op("order_progress", "report.read", ("order_id",))
def _h_progress(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "order_progress", frozenset({"order_id"}))
    return _svc.order_progress(_v_int(args, "order_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("scan_into_order", "dispatch.mutate", ("order_id", "scan_text"))
def _h_scan(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "scan_into_order", frozenset({"order_id", "scan_text"}))
    oid = _v_int(args, "order_id", minimum=1)
    scan = _v_str(args, "scan_text", max_len=128)
    assert oid is not None and scan is not None
    return _svc.scan_into_order(oid, scan, dbp)


@_op("remove_from_order", "dispatch.mutate", ("order_id", "carton_id"))
def _h_remove(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "remove_from_order", frozenset({"order_id", "carton_id"}))
    oid = _v_int(args, "order_id", minimum=1)
    cid = _v_int(args, "carton_id", minimum=1)
    assert oid is not None and cid is not None
    _svc.remove_from_order(oid, cid, dbp)
    return {"ok": True}


@_op("complete_dispatch", "dispatch.mutate", ("order_id",))
def _h_complete(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "complete_dispatch", frozenset({"order_id"}))
    return _svc.complete_dispatch(_v_int(args, "order_id", minimum=1), dbp)  # type: ignore[arg-type]


@_op("cancel_order", "dispatch.mutate", ("order_id",))
def _h_cancel(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "cancel_order", frozenset({"order_id"}))
    _svc.cancel_order(_v_int(args, "order_id", minimum=1), dbp)  # type: ignore[arg-type]
    return {"ok": True}


@_op("daily_summary", "summary.read", ("day",))
def _h_summary(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "daily_summary", frozenset({"day"}))
    day = _v_opt_str(args, "day", default="")
    if day:
        import re as _re
        if not _re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            raise _ValidationError("Argument 'day' must be YYYY-MM-DD.")
    return _svc.daily_summary(day or None, dbp)


@_op("get_slip", "read", ("job_id",))
def _h_slip(args: dict, actor: dict, dbp: str) -> dict:
    _need(args, "get_slip", frozenset({"job_id"}))
    return _slip.get_slip(_v_int(args, "job_id", minimum=1), dbp)  # type: ignore[arg-type]


_REQUEST_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_")


def _validate_request_id(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise _ValidationError("request_id must be a non-empty string.")
    if len(value) > MAX_REQUEST_ID_LEN:
        raise _ValidationError("request_id is too long.")
    if any(c not in _REQUEST_ID_CHARS for c in value):
        raise _ValidationError("request_id has invalid characters.")
    return value


# --------------------------------------------------------------------------
# HTTP layer
# --------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    db_path: str = ""
    server_version = "SimpldLAN/1"

    def log_message(self, fmt: str, *log_args: Any) -> None:
        # Deliberately minimal: status lines only, never args/tokens.
        sys.stderr.write(f"[lan] {self.client_address[0]} {fmt % log_args}\n")

    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status: int, code: str, message: str) -> None:
        self._send(status, {"ok": False,
                            "error": {"code": code, "message": message}})

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"ok": True, "service": "simpld-lan"})
            return
        self._fail(404, "not_found", "Unknown endpoint.")

    def do_POST(self) -> None:
        if self.path != "/api":
            self._fail(404, "not_found", "Unknown endpoint.")
            return
        try:
            length_raw = self.headers.get("Content-Length", "")
            try:
                length = int(str(length_raw).strip())
            except ValueError:
                self._fail(422, "validation", "Content-Length required.")
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._fail(413, "too_large", "Request body too large.")
                return
            raw = self.rfile.read(length)
            if len(raw) != length:
                self._fail(422, "validation", "Truncated request body.")
                return
            try:
                msg = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._fail(422, "validation", "Request must be JSON.")
                return
            if not isinstance(msg, dict):
                self._fail(422, "validation", "Request must be a JSON object.")
                return
            op = msg.get("op")
            if not isinstance(op, str) or op not in OPS:
                self._fail(422, "validation", "Unknown operation.")
                return
            args = msg.get("args", {})
            if not isinstance(args, dict):
                self._fail(422, "validation", "args must be an object.")
                return
            try:
                request_id = _validate_request_id(msg.get("request_id"))
            except _ValidationError as exc:
                self._fail(422, "validation", str(exc))
                return

            perm, _allowed, _fn = OPS[op]
            ip = self.client_address[0]

            if perm is None:  # login: no session, rate-limited, real DB auth
                if op != "login":
                    self._fail(422, "validation", "Unknown operation.")
                    return
                _reject_forbidden(args)
                _check_exact(args, frozenset({"username", "password"}), op)
                if "password" in args and args["password"] in (None,):
                    self._fail(401, "auth_required",
                               "Invalid username or password.")
                    return
                _auth.bind_request_session(None)
                try:
                    try:
                        data = _h_login(dict(args), {}, self.db_path, ip)
                    except Exception as exc:
                        status, code, message = _service_error(exc)
                        self._fail(status, code, message)
                        return
                    resp: dict = {"ok": True, "data": data}
                    if request_id is not None:
                        resp["request_id"] = request_id
                    self._send(200, resp)
                    return
                finally:
                    _auth.reset_session_context()
                return

            token = self.headers.get(SESSION_HEADER, "") or ""
            token = token.strip()
            if not token or len(token) > MAX_TOKEN_LEN:
                self._fail(401, "auth_required", "Authentication required.")
                return
            _auth.bind_request_session(token)
            try:
                try:
                    if perm == "SESSION":
                        actor = _auth.current_user(self.db_path)
                        if actor is None:
                            raise _AuthRequired("Authentication required.")
                    else:
                        actor = _auth.require_permission(perm, self.db_path)
                    data = _fn(dict(args), dict(actor), self.db_path)
                except Exception as exc:
                    status, code, message = _service_error(exc)
                    self._fail(status, code, message)
                    return
                resp = {"ok": True, "data": data}
                if request_id is not None:
                    resp["request_id"] = request_id
                self._send(200, resp)
                return
            finally:
                # Thread sessions must never leak into the next request.
                _auth.reset_session_context()
        except (ConnectionError, BrokenPipeError):
            return
        except Exception:
            try:
                self._fail(500, "server", "Internal error.")
            except Exception:
                pass


def check_db_ready(db_path: str) -> None:
    """Refuse to serve a missing/uninitialized DB.

    The main app owns init_db + first-run admin setup; this service never
    creates, migrates, or seeds production data.
    """
    import os as _os
    if not db_path or not _os.path.isfile(db_path):
        raise RuntimeError(
            f"DB file not found: {db_path!r}. Run the main app first so it "
            "creates and initializes the database.")
    c = _db.get_conn(db_path)
    try:
        try:
            n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        except Exception as exc:
            raise RuntimeError(
                "DB is not initialized (users table unreadable). Run the "
                f"main app first. ({exc})")
    finally:
        c.close()
    if not n:
        raise RuntimeError(
            "DB has no users. Run the main app first-run setup to create "
            "the admin account, then start the LAN service.")


def create_server(db_path: str, bind: str, port: int,
                  certfile: str, keyfile: str) -> ThreadingHTTPServer:
    """Build (not start) the TLS-wrapped server. Raises on bad config/DB."""
    import os as _os
    for label, p in (("cert", certfile), ("key", keyfile)):
        if not p or not _os.path.isfile(p):
            raise RuntimeError(f"TLS {label} file not found: {p!r}. "
                               "Generate one per LAN_SETUP.md.")
    check_db_ready(db_path)

    class _Bound(_Handler):
        pass
    _Bound.db_path = db_path

    httpd = ThreadingHTTPServer((bind, port), _Bound)
    httpd.daemon_threads = True
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(certfile, keyfile)
    httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
    return httpd


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Simpld authenticated LAN data service (packager host).")
    ap.add_argument("--bind", required=True,
                    help="LAN IP to bind, e.g. 192.168.1.10")
    ap.add_argument("--port", required=True, type=int,
                    help="TCP port, e.g. 8443")
    ap.add_argument("--db", required=True,
                    help="Server-local SQLite file (created by main app)")
    ap.add_argument("--cert", required=True, help="PEM certificate file")
    ap.add_argument("--key", required=True, help="PEM private key file")
    ns = ap.parse_args(argv)
    if not (1 <= ns.port <= 65535):
        print("error: --port must be 1-65535", file=sys.stderr)
        return 2
    try:
        httpd = create_server(ns.db, ns.bind, ns.port, ns.cert, ns.key)
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    addr = httpd.server_address
    print(f"[lan] serving user DB {ns.db} on https://{addr[0]}:{addr[1]}",
          file=sys.stderr)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
