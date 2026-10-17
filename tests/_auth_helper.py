"""Shared test helper: real temp DB + real authenticated sessions.

Production service mutations resolve their actor via auth.require_permission()
on the SAME DB path they write — never from caller-supplied role dicts.
Tests must therefore bootstrap a temp DB (schema + default logins) and
authenticate explicitly. No production bypass exists; this helper only makes
the required real-login pattern one call.

Usage:
    from _auth_helper import fresh_db  # sys.path: tests dir
    p = fresh_db("admin")   # temp DB path with an installed admin session
    S.create_job(..., path=p)  # session is DB-bound to p

Roles map to the default bootstrap passwords (test fixtures only).
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CREDS = {
    "admin": ("admin", "admin123"),
    "packager": ("packager", "pack123"),
    "dispatcher": ("dispatcher", "dispatch123"),
}


# Test-only bootstrap: creates the default logins on an empty DB. Lives here
# (not in factory/auth.py) so the shipped app cannot create known-password
# accounts. Production creates its first admin in the login dialog.
def ensure_default_users(path: str | None = None) -> None:
    import hashlib
    import secrets
    from factory.db import get_conn, now_iso

    def _hash(pw: str) -> str:
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000)
        return f"{salt}${dk.hex()}"

    c = get_conn(path)
    try:
        n = c.execute("SELECT COUNT(*) n FROM users").fetchone()["n"]
        if n:
            return
        for role, (user, pw) in _CREDS.items():
            c.execute(
                "INSERT INTO users(username, pass_hash, role, created_at) VALUES(?,?,?,?)",
                (user, _hash(pw), role, now_iso()))
        c.commit()
    finally:
        c.close()


def fresh_db(role: str = "admin", prefix: str = "simpld-tst-") -> str:
    from factory.db import init_db
    from factory import auth as A
    p = os.path.join(tempfile.mkdtemp(prefix=prefix), "factory.db")
    init_db(p)
    ensure_default_users(p)
    user, pw = _CREDS[role]
    out = A.authenticate(user, pw, p)
    assert out is not None, f"bootstrap login failed for {role}"
    return p


def login_as(role: str, path: str):
    from factory import auth as A
    user, pw = _CREDS[role]
    out = A.authenticate(user, pw, path)
    assert out is not None, f"login failed for {role}"
    return out


def reset_auth():
    from factory import auth as A
    A._SESSIONS.clear()
    A.set_session(None)
