"""Local offline auth. Roles gate which areas each login sees.

Roles:
  packager   -> Packaging, Ready Stock, Jobs
  dispatcher -> Ready Stock, Dispatch, History
  admin      -> everything + user management

Passwords are PBKDF2-SHA256 hashes (stdlib only, fully offline).

RBAC model (GPT6.1 decision): never trust a role dict handed in from the UI.
Callers hold an opaque authenticated session token (installed per-context
via set_session); every require_permission() call re-resolves id/username/
role/pass_hash from the canonical DB, so deleted/demoted users lose
permission immediately and tokens never cross DB files.
Session state is memory-only (stdlib secrets); nothing is persisted.
"""
from __future__ import annotations
import contextvars
import hashlib
import hmac
import os
import secrets
import threading
import time
from contextlib import contextmanager
from .db import get_conn, now_iso

ROLES = ("packager", "dispatcher", "admin")

ROLE_PAGES = {
    "packager": ("packaging", "stock", "jobs"),
    "dispatcher": ("stock", "dispatch", "history"),
    "admin": ("packaging", "stock", "dispatch", "jobs", "history", "summary"),
}

# Explicit permission -> roles allowed. Fail closed: unknown permission or
# unknown role is denied. Readonly ops (stock/history/summary reads) are
# listed here so subsequent caller integration can gate on them; production
# and dispatch callers must enforce the mutate entries.
PERMISSION_ROLES: dict[str, tuple[str, ...]] = {
    # Production floor: weigh/create/reweigh/print + catalog/job management.
    "production.create": ("packager", "admin"),
    "production.reweigh": ("packager", "admin"),
    "production.print": ("packager", "admin"),
    "product.manage": ("packager", "admin"),
    "job.manage": ("packager", "admin"),
    # Dispatch bay mutations (orders, scans, complete/cancel).
    "dispatch.mutate": ("dispatcher", "admin"),
    # Destructive deletions (any domain) are admin-only.
    "delete": ("admin",),
    # User management is admin-only.
    "users.manage": ("admin",),
    # Reads: stock visible to floor roles; history to dispatch/admin;
    # daily summary to admin; generic report alias to dispatch/admin.
    "stock.read": ("packager", "dispatcher", "admin"),
    "read": ("packager", "dispatcher", "admin"),
    "history.read": ("dispatcher", "admin"),
    "summary.read": ("admin",),
    "report.read": ("dispatcher", "admin"),
    "reporting": ("dispatcher", "admin"),
}

# Sessions live for one work shift; expiry is enforced with a monotonic
# clock so wall-clock jumps cannot extend a stolen token.
SESSION_TTL_SECONDS = 12 * 3600

# ---- per-request session state (memory only) ----
# opaque token -> {"db": canonical db identity, "user_id": int,
#   "username": lowercased, "pass_hash": str at login, "created": monotonic}
_SESSIONS: dict[str, dict] = {}
_SESSIONS_LOCK = threading.Lock()

# Per-context current token (replaces the old global _CURRENT_TOKEN so
# concurrent LAN requests/threads cannot leak identities into each other).
# Preserved APIs: set_session()/current_token() read/write this var.
_current_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "simpld_current_token", default=None)


def _canonical_db_path(path: str | None = None) -> str:
    """Canonical identity of a DB file for session binding.

    Resolves the effective path dynamically (DB.DB_PATH when path is None
    so env overrides apply), then abspath + realpath so symlinks / relative
    spellings of the same file bind to one identity.
    """
    from . import db as _db
    p = path if path is not None else _db.DB_PATH
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(str(p))))
    except Exception:
        return os.path.abspath(str(p))


def set_session(token: str | None) -> None:
    """Establish (or clear with None) the current-context session.

    Any string may be installed; forged/unknown tokens are simply denied
    later by require_permission(). Does not touch the DB. Context-local:
    setting it in one thread/request never leaks into another.
    """
    _current_token_var.set(token)


def current_token() -> str | None:
    return _current_token_var.get()


def reset_session_context() -> None:
    """Explicitly clear the current context (LAN server: per-request reset)."""
    _current_token_var.set(None)


def bind_request_session(token: str | None) -> None:
    """LAN-server entrypoint: bind an incoming request to a session token."""
    _current_token_var.set(token)


@contextmanager
def session_scope(token: str | None):
    """Temporarily install a session token, restoring the previous one after."""
    prev = _current_token_var.get()
    _current_token_var.set(token)
    try:
        yield token
    finally:
        _current_token_var.set(prev)


def logout_current() -> None:
    """Invalidate the current session token and clear the context."""
    tok = _current_token_var.get()
    _current_token_var.set(None)
    if tok:
        with _SESSIONS_LOCK:
            _SESSIONS.pop(tok, None)


def _invalidate_user_sessions(username: str, db_id: str | None = None) -> None:
    u = (username or "").strip().lower()
    with _SESSIONS_LOCK:
        stale = [tok for tok, rec in _SESSIONS.items()
                 if rec.get("username") == u
                 and (db_id is None or rec.get("db") == db_id)]
        for tok in stale:
            _SESSIONS.pop(tok, None)


def _session_snapshot(token: str) -> dict | None:
    with _SESSIONS_LOCK:
        rec = _SESSIONS.get(token)
        return dict(rec) if rec else None


def _drop_session(token: str) -> None:
    with _SESSIONS_LOCK:
        _SESSIONS.pop(token, None)


def current_user(path: str | None = None) -> dict | None:
    """Resolve the current session to {"username","role"} from the DB.

    Returns None when there is no session, the token is forged/unknown/
    expired, the token was issued for a different DB file, the user id row
    is gone (delete+recreate never revives an old token), the credential
    hash changed (raw or API password mutation), or the stored role is
    unknown. Never trusts caller-supplied role data.
    """
    tok = _current_token_var.get()
    if not tok:
        return None
    rec = _session_snapshot(tok)
    if not rec:
        return None
    try:
        if (time.monotonic() - float(rec.get("created", 0))) > SESSION_TTL_SECONDS:
            _drop_session(tok)
            return None
    except Exception:
        return None
    req_db = _canonical_db_path(path)
    if rec.get("db") != req_db:
        return None
    uid = rec.get("user_id")
    if uid is None:
        return None
    c = get_conn(path)
    try:
        r = c.execute("SELECT id, username, role, pass_hash FROM users WHERE id=?",
                      (uid,)).fetchone()
    finally:
        c.close()
    if not r:
        _drop_session(tok)
        return None
    if str(r["username"]).strip().lower() != str(rec.get("username") or "").strip().lower():
        _drop_session(tok)
        return None
    if r["pass_hash"] != rec.get("pass_hash"):
        # Credential changed underneath the session (raw SQL or API reset
        # that bypassed invalidation): revoke the stale token.
        _drop_session(tok)
        return None
    if r["role"] not in ROLES:
        return None
    return {"username": r["username"], "role": r["role"]}


def require_permission(permission: str, path: str | None = None) -> dict:
    """Enforce one permission against the current session.

    Re-resolves the actor from the canonical DB on every call. Returns
    {"username","role"} on success; raises PermissionError when there is
    no session, the token is forged/stale/expired/bound to another DB,
    the user was deleted/demoted, the role is unknown, or the permission
    is unknown/not granted.
    """
    allowed = PERMISSION_ROLES.get(permission)
    if allowed is None:
        raise PermissionError(f"Unknown permission '{permission}' (denied)")
    actor = current_user(path)
    if actor is None:
        raise PermissionError("No authenticated session (denied)")
    if actor["role"] not in allowed:
        raise PermissionError(
            f"Role '{actor['role']}' may not '{permission}' (denied)")
    return actor


def _require_users_manage(path: str | None = None) -> dict:
    return require_permission("users.manage", path)


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 200_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        salt, hexhash = stored.split("$", 1)
    except ValueError:
        return False
    return hmac.compare_digest(hash_password(password, salt), stored)


def pages_for(role: str) -> tuple[str, ...]:
    return ROLE_PAGES.get(role, ())


def authenticate(username: str, password: str, path: str | None = None) -> dict | None:
    """Verify credentials; on success issue an opaque session token.

    The token is bound to the canonical DB identity plus the stable user
    id and the credential hash at login, so it cannot be replayed against
    another DB file (even one with the same username) nor revived by
    deleting/recreating the username. Returns {"username","role",
    "session_token","token"} and installs the token as the current
    context session. Returns None on failure (unknown user, bad password,
    or unknown stored role — fail closed) and clears the current context
    so a failed attempt never leaves a prior identity installed.
    """
    u = (username or "").strip().lower()
    if not u or not password:
        _current_token_var.set(None)
        return None
    c = get_conn(path)
    try:
        r = c.execute("SELECT * FROM users WHERE username=?",
                      (u,)).fetchone()
    finally:
        c.close()
    if not r or not verify_password(password, r["pass_hash"]):
        _current_token_var.set(None)
        return None
    if r["role"] not in ROLES:
        # Unknown stored role: fail login closed (no token issued).
        _current_token_var.set(None)
        return None
    token = secrets.token_urlsafe(32)
    rec = {
        "db": _canonical_db_path(path),
        "user_id": r["id"],
        "username": str(r["username"]).strip().lower(),
        "pass_hash": r["pass_hash"],
        "created": time.monotonic(),
    }
    with _SESSIONS_LOCK:
        _SESSIONS[token] = rec
    _current_token_var.set(token)
    return {"username": r["username"], "role": r["role"],
            "session_token": token, "token": token}


def list_users(path: str | None = None) -> list[dict]:
    _require_users_manage(path)
    c = get_conn(path)
    try:
        rows = c.execute("SELECT id, username, role, created_at FROM users ORDER BY username").fetchall()
        return [dict(r) for r in rows]
    finally:
        c.close()


def create_user(username: str, password: str, role: str, path: str | None = None) -> dict:
    _require_users_manage(path)
    u = (username or "").strip().lower()
    if not u:
        raise ValueError("Username required")
    if role not in ROLES:
        raise ValueError(f"Role must be one of {', '.join(ROLES)}")
    if len(password or "") < 4:
        raise ValueError("Password must be at least 4 characters")
    c = get_conn(path)
    try:
        if c.execute("SELECT id FROM users WHERE username=?", (u,)).fetchone():
            raise ValueError(f"User '{u}' already exists")
        c.execute("INSERT INTO users(username, pass_hash, role, created_at) VALUES(?,?,?,?)",
                  (u, hash_password(password), role, now_iso()))
        c.commit()
        r = c.execute("SELECT id, username, role, created_at FROM users WHERE username=?", (u,)).fetchone()
        return dict(r)
    finally:
        c.close()


def set_password(username: str, new_password: str, path: str | None = None):
    actor = _require_users_manage(path)
    _ = actor
    if len(new_password or "") < 4:
        raise ValueError("Password must be at least 4 characters")
    u = (username or "").strip().lower()
    if not u:
        raise ValueError("Username required")
    c = get_conn(path)
    try:
        cur = c.execute("UPDATE users SET pass_hash=? WHERE username=?",
                        (hash_password(new_password), u))
        if cur.rowcount == 0:
            raise ValueError(f"User '{username}' not found")
        c.commit()
    finally:
        c.close()
    # Stale tokens must not survive a credential change (scoped to this DB
    # so same-named users in other DB files keep their own sessions).
    _invalidate_user_sessions(u, _canonical_db_path(path))
    # The actor may have just reset their own password: leave the (now
    # invalid) token installed so the caller must re-authenticate.
    # current_user() will return None.


def set_role(username: str, role: str, path: str | None = None):
    actor = _require_users_manage(path)
    if role not in ROLES:
        raise ValueError(f"Role must be one of {', '.join(ROLES)}")
    u = (username or "").strip().lower()
    if not u:
        raise ValueError("Username required")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            me = c.execute("SELECT role FROM users WHERE username=?", (u,)).fetchone()
            if not me:
                raise ValueError(f"User '{username}' not found")
            if role != "admin" and me["role"] == "admin":
                n_admin = c.execute(
                    "SELECT COUNT(*) n FROM users WHERE role='admin' AND username!=?",
                    (u,)).fetchone()["n"]
                if n_admin == 0:
                    raise ValueError("Cannot demote the last admin")
            if u == str(actor["username"]).strip().lower() and role != actor["role"]:
                raise PermissionError("Cannot change your own role while logged in")
            c.execute("UPDATE users SET role=? WHERE username=?", (role, u))
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
    finally:
        c.close()


def delete_user(username: str, path: str | None = None):
    actor = _require_users_manage(path)
    u = (username or "").strip().lower()
    if not u:
        raise ValueError("Username required")
    c = get_conn(path)
    c.isolation_level = None
    try:
        c.execute("BEGIN IMMEDIATE")
        try:
            me = c.execute("SELECT role FROM users WHERE username=?", (u,)).fetchone()
            if not me:
                raise ValueError(f"User '{username}' not found")
            if me["role"] == "admin":
                n_admin = c.execute("SELECT COUNT(*) n FROM users WHERE role='admin'").fetchone()["n"]
                if n_admin <= 1:
                    raise ValueError("Cannot delete the last admin")
            if u == str(actor["username"]).strip().lower():
                raise PermissionError("Cannot delete yourself while logged in")
            c.execute("DELETE FROM users WHERE username=?", (u,))
            c.execute("COMMIT")
        except Exception:
            try:
                c.execute("ROLLBACK")
            except Exception:
                pass
            raise
    finally:
        c.close()
    _invalidate_user_sessions(u, _canonical_db_path(path))
