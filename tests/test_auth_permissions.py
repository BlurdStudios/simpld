"""Auth RBAC: opaque session tokens, DB-resolved permissions, admin-only user mgmt.

Real SQLite temp DBs only (never production data/factory.db), stdlib only,
no mocks. Covers: real authenticate issues token, missing/forged/stale
(deleted)/demoted sessions denied, unknown role fails login closed, user
mgmt requires admin session, last-admin safeguards preserved
(transactional), self-mutation rejected, bootstrap does not overwrite,
plus auth-correction round: token bound to canonical DB identity + stable
user id + credential hash, cross-DB replay denied, delete/recreate never
revives, raw password mutation revokes, monotonic expiry, ContextVar
thread/request isolation, failed login clears context.
"""
import os
import secrets
import sys
import tempfile
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._auth_helper import ensure_default_users


@pytest.fixture()
def dbpath():
    from factory import db as DB
    from factory import auth as A
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auth-"), "factory.db")
    DB.init_db(tmp)
    ensure_default_users(tmp)
    A._SESSIONS.clear()
    A.set_session(None)
    yield tmp
    A._SESSIONS.clear()
    A.set_session(None)


def _mkdb():
    from factory import db as DB
    from factory import auth as A
    tmp = os.path.join(tempfile.mkdtemp(prefix="simpld-auth2-"), "factory.db")
    DB.init_db(tmp)
    ensure_default_users(tmp)
    return tmp


def _login(A, user, pw, path):
    out = A.authenticate(user, pw, path)
    assert out is not None, f"authenticate failed for {user}"
    assert out["username"] == user.lower()
    assert "session_token" in out and out["session_token"]
    return out


def test_authenticate_returns_session_token_and_resolves_role(dbpath):
    from factory import auth as A
    out = _login(A, "admin", "admin123", dbpath)
    assert out["role"] == "admin"
    assert out["token"] == out["session_token"]
    assert A.current_token() == out["session_token"]
    actor = A.require_permission("users.manage", dbpath)
    assert actor == {"username": "admin", "role": "admin"}
    # Second login mints a distinct opaque token.
    out2 = A.authenticate("admin", "admin123", dbpath)
    assert out2["session_token"] != out["session_token"]
    # Bad password fails closed and clears the current context.
    A.set_session(out2["session_token"])
    assert A.authenticate("admin", "nope", dbpath) is None
    assert A.current_token() is None
    assert A.current_user(dbpath) is None


def test_missing_and_forged_sessions_denied(dbpath):
    from factory import auth as A
    A.set_session(None)
    with pytest.raises(PermissionError):
        A.require_permission("production.create", dbpath)
    A.set_session(secrets.token_urlsafe(32))  # never issued
    with pytest.raises(PermissionError):
        A.require_permission("production.create", dbpath)
    with pytest.raises(PermissionError):
        A.require_permission("users.manage", dbpath)


def test_unknown_permission_and_unknown_role_denied(dbpath):
    from factory import auth as A
    from factory.db import get_conn
    _login(A, "admin", "admin123", dbpath)
    with pytest.raises(PermissionError):
        A.require_permission("nope.notreal", dbpath)
    # Unknown role stored directly: login now fails closed (no token).
    c = get_conn(dbpath)
    try:
        c.execute("UPDATE users SET role='ghost' WHERE username='packager'")
        c.commit()
    finally:
        c.close()
    A.set_session(None)
    assert A.authenticate("packager", "pack123", dbpath) is None
    assert A.current_token() is None
    assert A.current_user(dbpath) is None
    with pytest.raises(PermissionError):
        A.require_permission("production.create", dbpath)


def test_permission_mapping_per_role(dbpath):
    from factory import auth as A
    # packager: production yes, dispatch no, admin-only no
    _login(A, "packager", "pack123", dbpath)
    for perm in ("production.create", "production.reweigh", "production.print",
                 "product.manage", "job.manage", "stock.read", "read"):
        assert A.require_permission(perm, dbpath)["role"] == "packager"
    for perm in ("dispatch.mutate", "delete", "users.manage", "summary.read"):
        with pytest.raises(PermissionError):
            A.require_permission(perm, dbpath)
    # dispatcher: dispatch yes, production no
    _login(A, "dispatcher", "dispatch123", dbpath)
    for perm in ("dispatch.mutate", "stock.read", "history.read", "report.read"):
        A.require_permission(perm, dbpath)
    for perm in ("production.create", "product.manage", "users.manage", "delete"):
        with pytest.raises(PermissionError):
            A.require_permission(perm, dbpath)
    # admin: everything listed
    _login(A, "admin", "admin123", dbpath)
    for perm in ("production.create", "production.reweigh", "production.print",
                 "product.manage", "job.manage", "dispatch.mutate", "delete",
                 "users.manage", "stock.read", "history.read", "summary.read",
                 "report.read"):
        A.require_permission(perm, dbpath)


def test_demoted_user_loses_permission_immediately(dbpath):
    from factory import auth as A
    admin = _login(A, "admin", "admin123", dbpath)
    A.create_user("w1", "w1pass", "packager", dbpath)
    w1 = _login(A, "w1", "w1pass", dbpath)
    A.require_permission("production.create", dbpath)
    with pytest.raises(PermissionError):
        A.require_permission("dispatch.mutate", dbpath)
    # Admin re-roles w1 while w1's token stays installed: DB re-resolve
    # must flip the verdict without re-login.
    A.set_session(admin["session_token"])
    A.set_role("w1", "dispatcher", dbpath)
    A.set_session(w1["session_token"])
    A.require_permission("dispatch.mutate", dbpath)
    with pytest.raises(PermissionError):
        A.require_permission("production.create", dbpath)


def test_deleted_user_session_goes_stale(dbpath):
    from factory import auth as A
    admin = _login(A, "admin", "admin123", dbpath)
    A.create_user("gone", "gonepass", "packager", dbpath)
    victim = _login(A, "gone", "gonepass", dbpath)
    A.require_permission("stock.read", dbpath)
    A.set_session(admin["session_token"])
    A.delete_user("gone", dbpath)
    A.set_session(victim["session_token"])  # stale: user row is gone
    with pytest.raises(PermissionError):
        A.require_permission("stock.read", dbpath)
    assert A.current_user(dbpath) is None


def test_password_change_invalidates_stale_token(dbpath):
    from factory import auth as A
    admin = _login(A, "admin", "admin123", dbpath)
    A.create_user("pw1", "pw1pass", "packager", dbpath)
    vic = _login(A, "pw1", "pw1pass", dbpath)
    A.set_session(admin["session_token"])
    A.set_password("pw1", "newpass99", dbpath)
    A.set_session(vic["session_token"])
    with pytest.raises(PermissionError):
        A.require_permission("stock.read", dbpath)
    assert A.authenticate("pw1", "newpass99", dbpath) is not None


def test_user_management_requires_admin_session(dbpath):
    from factory import auth as A
    A.set_session(None)
    for fn in (lambda: A.list_users(dbpath),
               lambda: A.create_user("x", "xpass", "packager", dbpath),
               lambda: A.set_password("packager", "newpass99", dbpath),
               lambda: A.set_role("packager", "dispatcher", dbpath),
               lambda: A.delete_user("packager", dbpath)):
        with pytest.raises(PermissionError):
            fn()
    _login(A, "packager", "pack123", dbpath)
    for fn in (lambda: A.list_users(dbpath),
               lambda: A.create_user("x", "xpass", "packager", dbpath),
               lambda: A.set_password("dispatcher", "newpass99", dbpath),
               lambda: A.set_role("dispatcher", "packager", dbpath),
               lambda: A.delete_user("dispatcher", dbpath)):
        with pytest.raises(PermissionError):
            fn()
    # Admin session works.
    _login(A, "admin", "admin123", dbpath)
    assert {u["username"] for u in A.list_users(dbpath)} >= {"admin", "packager", "dispatcher"}


def test_last_admin_safeguards_and_self_mutation(dbpath):
    from factory import auth as A
    admin = _login(A, "admin", "admin123", dbpath)
    # Only one admin: demote/delete blocked as ValueError, row intact.
    with pytest.raises(ValueError, match="last admin"):
        A.set_role("admin", "packager", dbpath)
    with pytest.raises(ValueError, match="last admin"):
        A.delete_user("admin", dbpath)
    assert A.authenticate("admin", "admin123", dbpath) is not None
    A.set_session(admin["session_token"])
    # Second admin unblocks operating on the first, but self-mutation stays out.
    A.create_user("admin2", "admin2pass", "admin", dbpath)
    A.set_session(admin["session_token"])
    with pytest.raises(PermissionError, match="own role"):
        A.set_role("admin", "packager", dbpath)
    with pytest.raises(PermissionError, match="yourself"):
        A.delete_user("admin", dbpath)
    # Operating on the OTHER admin is allowed now.
    A.delete_user("admin2", dbpath)
    admins = [u for u in A.list_users(dbpath) if u["role"] == "admin"]
    assert [u["username"] for u in admins] == ["admin"]
    # Validation compat: username/role/password rules unchanged.
    with pytest.raises(ValueError, match="Username required"):
        A.create_user("  ", "somepass", "packager", dbpath)
    with pytest.raises(ValueError, match="Role must be"):
        A.create_user("zz", "somepass", "ghost", dbpath)
    with pytest.raises(ValueError, match="at least 4"):
        A.create_user("zz", "ab", "packager", dbpath)
    with pytest.raises(ValueError, match="already exists"):
        A.create_user("packager", "somepass", "packager", dbpath)
    with pytest.raises(ValueError, match="at least 4"):
        A.set_password("packager", "ab", dbpath)


def test_bootstrap_helper_is_test_only(dbpath):
    from factory import auth as A
    # No session needed for bootstrap, and a second call never overwrites.
    A.set_session(None)
    ensure_default_users(dbpath)
    assert A.authenticate("admin", "admin123", dbpath) is not None
    A.set_session(None)
    ensure_default_users(dbpath)
    assert A.authenticate("packager", "pack123", dbpath) is not None
    assert A.authenticate("dispatcher", "dispatch123", dbpath) is not None


def test_session_bound_to_origin_db_denies_other_db(dbpath):
    """Parent repro: admin in A must NOT authorize against a second DB B."""
    from factory import auth as A
    db_b = _mkdb()
    try:
        admin_a = _login(A, "admin", "admin123", dbpath)
        A.require_permission("users.manage", dbpath)
        # Same token against the other DB file: denied (even though B has
        # an identical 'admin' username with the same password).
        with pytest.raises(PermissionError):
            A.require_permission("users.manage", db_b)
        with pytest.raises(PermissionError):
            A.list_users(db_b)
        assert A.current_user(db_b) is None
        # Origin DB still works with the same installed token.
        A.require_permission("users.manage", dbpath)
        # Realpath spelling of the same file still binds (same identity).
        assert A.require_permission(
            "users.manage", os.path.realpath(dbpath))["username"] == "admin"
        # A login in B mints an independent token bound to B only.
        admin_b = _login(A, "admin", "admin123", db_b)
        assert admin_b["session_token"] != admin_a["session_token"]
        A.require_permission("users.manage", db_b)
        with pytest.raises(PermissionError):
            A.require_permission("users.manage", dbpath)
    finally:
        A._SESSIONS.clear()
        A.set_session(None)


def test_deleted_then_recreated_username_does_not_revive_token(dbpath):
    from factory import auth as A
    admin = _login(A, "admin", "admin123", dbpath)
    A.create_user("revive", "revivepass", "packager", dbpath)
    victim = _login(A, "revive", "revivepass", dbpath)
    old_token = victim["session_token"]
    A.set_session(admin["session_token"])
    A.delete_user("revive", dbpath)
    # Recreate the SAME username (fresh stable id, new credential hash).
    A.create_user("revive", "revivepass", "packager", dbpath)
    A.set_session(old_token)  # old token must stay dead
    with pytest.raises(PermissionError):
        A.require_permission("stock.read", dbpath)
    assert A.current_user(dbpath) is None


def test_raw_password_mutation_revokes_session(dbpath):
    from factory import auth as A
    from factory.db import get_conn
    vic = _login(A, "packager", "pack123", dbpath)
    A.require_permission("stock.read", dbpath)
    # Raw credential mutation underneath the session (bypasses the API).
    c = get_conn(dbpath)
    try:
        c.execute("UPDATE users SET pass_hash=? WHERE username='packager'",
                  (A.hash_password("attacker-set"),))
        c.commit()
    finally:
        c.close()
    A.set_session(vic["session_token"])
    with pytest.raises(PermissionError):
        A.require_permission("stock.read", dbpath)
    assert A.current_user(dbpath) is None


def test_session_expiry_denies_stale_token(dbpath):
    from factory import auth as A
    out = _login(A, "packager", "pack123", dbpath)
    tok = out["session_token"]
    A.require_permission("stock.read", dbpath)
    # Age the session past the monotonic TTL.
    A._SESSIONS[tok]["created"] = time.monotonic() - A.SESSION_TTL_SECONDS - 1
    with pytest.raises(PermissionError):
        A.require_permission("stock.read", dbpath)
    assert A.current_user(dbpath) is None


def test_contextvar_isolates_concurrent_requests(dbpath):
    from factory import auth as A
    out = _login(A, "admin", "admin123", dbpath)
    main_token = out["session_token"]
    probe: dict = {}

    def worker():
        # Fresh context: no leaked identity from the main thread.
        probe["token"] = A.current_token()
        probe["user"] = A.current_user(dbpath)
        # Binding a forged token here must not touch the main context.
        A.set_session(secrets.token_urlsafe(32))
        probe["after"] = A.current_token()

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert probe["token"] is None
    assert probe["user"] is None
    assert probe["after"] is not None
    # Main context untouched by the worker.
    assert A.current_token() == main_token
    assert A.require_permission("users.manage", dbpath)["username"] == "admin"
    # Explicit reset clears only the calling context.
    A.reset_session_context()
    assert A.current_token() is None
    assert A.current_user(dbpath) is None


def test_failed_authenticate_clears_context(dbpath):
    from factory import auth as A
    out = _login(A, "admin", "admin123", dbpath)
    assert A.current_token() == out["session_token"]
    assert A.authenticate("admin", "wrongpw", dbpath) is None
    assert A.current_token() is None
    assert A.authenticate("nosuchuser", "whatever", dbpath) is None
    assert A.current_token() is None
    assert A.authenticate("", "", dbpath) is None
    assert A.current_token() is None
