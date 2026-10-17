# Plan 006: Move the default-credential helper out of the shipped package

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Confirm `factory/auth.py` contains `DEFAULT_USERS = (` at line ~36 and `def ensure_default_users(` at line ~426, and that `grep -rn "ensure_default_users" factory | grep -v __pycache__` lists only `factory/seed.py:38` and `factory/auth.py:426`. If not, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW (test-only refactor; the shipped code loses a function that production never calls)
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

`factory/auth.py` still ships two test-fixture artifacts inside the frozen Windows `.exe`: the `DEFAULT_USERS` table (which hardcodes `admin/admin123`, `packager/pack123`, `dispatcher/dispatch123` at `factory/auth.py:36-40`) and the `ensure_default_users()` bootstrap helper (`factory/auth.py:426`). Production startup never calls them — `factory/seed.py` is schema-only and the first-run admin is created in the login dialog — but the helper is a live "create three known-password accounts with no session required" function sitting inside the shipped binary. Anyone who can import the package or reach the frozen interpreter can call it. Removing it from the production package removes that footgun permanently. The tests keep the same bootstrap behavior; only its location changes.

## Current state

- `factory/auth.py:36-40`:
  ```python
  DEFAULT_USERS = (
      ("admin", "admin123", "admin"),
      ("packager", "pack123", "packager"),
      ("dispatcher", "dispatch123", "dispatcher"),
  )
  ```
- `factory/auth.py:426-443`: `def ensure_default_users(path: str | None = None)` — inserts the three users if the `users` table is empty. Its docstring says "Test-fixture helper only".
- `factory/seed.py:35-41`: `seed_sample_data()` calls `A.ensure_default_users(path)` then `A.authenticate("admin", "admin123", path)`.
- `tests/_auth_helper.py` already exists and already holds the same credentials in its `_CREDS` dict (lines 22-26). It is the natural home for this helper.
- Tests import `factory.auth` for real authentication as well, so **the `from factory import auth as A` imports must stay** in test files; only the calls to `ensure_default_users` change.
- `tests/test_auth_permissions.py:235` is named `test_ensure_default_users_is_bootstrap_only` and calls `A.ensure_default_users(dbpath)` at lines 239 and 242.
- `tests/test_data_access.py:294` asserts `"ensure_default_users" not in src` where `src` is `factory/data_access.py`; that assertion is about the LAN path and is unaffected by this plan, but the same test file also calls `A.ensure_default_users(dbp)` at lines 63 and 85 and calls `A.authenticate("admin", "admin123")` at lines 177 and 249.
- Call sites in tests (all of the form `A.ensure_default_users(x)` or `_A.ensure_default_users(x)`): `tests/_auth_helper.py:34`, `tests/test_auth_permissions.py:239,242`, `tests/test_backup.py:19`, `tests/test_data_access.py:63,85`, `tests/test_flow.py:14`, `tests/test_hardware_real.py:34`, `tests/test_history.py:16`, `tests/test_integration_auth_production.py:35,350`, `tests/test_lan_slip_preview.py:56`, `tests/test_lan_transport.py:61`, `tests/test_mock_scale.py:27`, `tests/test_monthly_invariants.py:59`, `tests/test_packaging_detect.py:26`, `tests/test_products.py:15,132`, `tests/test_requested_lan_ui.py:51`, `tests/test_requested_ui.py:37`, `tests/test_slip_evidence.py:27`, `tests/test_slip_pdf.py:26`, `tests/test_sticker_preview.py:24`, `tests/test_ui_smoke.py:23`, `tests/test_verify.py:19`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Prove the symbol left the package | `grep -rn "ensure_default_users\|DEFAULT_USERS" factory \| grep -v __pycache__` | no matches |
| Compile check | `python -m py_compile factory/auth.py factory/seed.py tests/_auth_helper.py` | exit 0 |
| Identity test | `SIMPLD_DB=/tmp/opencode/p006.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_auth_permissions.py tests/test_data_access.py tests/test_integration_auth_production.py -q -p no:cacheprovider` | 0 failed |
| Full suite | `SIMPLD_DB=/tmp/opencode/p006-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed, 1 skipped |

## Scope

**In scope**:
- `factory/auth.py` (delete `DEFAULT_USERS` and `ensure_default_users` only)
- `factory/seed.py`
- `tests/_auth_helper.py`
- every test file listed in "Current state" that calls `ensure_default_users`

**Out of scope**:
- The `_CREDS` dict in `tests/_auth_helper.py` — leave it as is.
- Authentication, `require_permission`, sessions, RBAC: do not touch.
- `tests/test_data_access.py:294`'s assertion — leave it exactly as is.
- Any production module other than `factory/auth.py` and `factory/seed.py`.

## Steps

### Step 1: Add the helper to `tests/_auth_helper.py`

Add to `tests/_auth_helper.py` (after `_CREDS`):

```python
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
        for role, (user, pw, _role) in _CREDS.items():
            c.execute(
                "INSERT INTO users(username, pass_hash, role, created_at) VALUES(?,?,?,?)",
                (user, _hash(pw), role, now_iso()))
        c.commit()
    finally:
        c.close()
```

Key point: build the credentials from the existing `_CREDS` dict so there is exactly one copy of the passwords in the repo. `_CREDS` maps role -> (username, password), which is why the loop keys off `role`.

Also update `fresh_db` (line ~34) to call the local helper: `ensure_default_users(p)`.

**Verify**: `python -m py_compile tests/_auth_helper.py` → exit 0.

### Step 2: Repoint the other test call sites

In every test file listed in "Current state" (except `tests/_auth_helper.py`, already done in Step 1), replace the call to `A.ensure_default_users(...)` / `_A.ensure_default_users(...)` with a call to the helper from `_auth_helper`. Each of these files already does `sys.path` setup for the tests directory indirectly, because they import other test modules; if a file does not already import `_auth_helper`, add `from _auth_helper import ensure_default_users` at the top after the existing imports and call `ensure_default_users(...)` bare.

Keep every `from factory import auth as A` import in place — those files still need `A.authenticate`.

Handle the two special cases:
- `tests/test_auth_permissions.py:235-242` (`test_ensure_default_users_is_bootstrap_only`): update the two calls and rename the test to `test_bootstrap_helper_is_test_only`. Keep asserting that the second call does not add duplicates.
- `tests/test_monthly_invariants.py:56` and `tests/test_integration_auth_production.py:350` use the helper inside fixtures that also authenticate; only the call changes.

**Verify**: `grep -rn "ensure_default_users" tests | grep -v __pycache__ | grep -v "_auth_helper.py"` → every remaining hit is an import line or a call to the bare `ensure_default_users(...)` from the helper, with no `A.ensure_default_users` or `_A.ensure_default_users` left. Then run the identity test command in the table → 0 failed.

### Step 3: Remove the helper from `factory/auth.py`

Delete `DEFAULT_USERS` (lines ~36-40) and the whole `ensure_default_users` function (lines ~426-443) from `factory/auth.py`. Delete nothing else. If a comment above either block references the test fixture, remove the comment with it.

**Verify**: the "Prove the symbol left the package" command in the table → no matches.

### Step 4: Fix `factory/seed.py`

`seed_sample_data()` must no longer call the removed function. Change its signature to take the bootstrap credentials as arguments and stop importing them from `factory.auth`:

```python
def seed_sample_data(path: str | None = None, admin_username: str = "",
                     admin_password: str = "") -> dict:
```

Inside, replace the `A.ensure_default_users(path)` + `A.authenticate("admin", "admin123", path)` pair with:
1. `init_db(path)` (already called)
2. the caller's responsibility note: callers must bootstrap the DB and install an admin session before calling this fixture. Keep `A.authenticate(admin_username, admin_password, path)` using the new arguments, and raise `RuntimeError("seed_sample_data: bootstrap the DB and pass admin credentials")` if the login returns `None`.

Update `factory/seed.py`'s module docstring line that says tests bootstrap via the auth test helper, so it points at `tests/_auth_helper.py`.

**Verify**: `grep -rn "seed_sample_data" tests tools` → check every caller. If any caller exists, update it to bootstrap via `tests/_auth_helper.py` and pass `("admin", "admin123")`. If a caller lives in `tools/`, list it in your report and update it the same way. Then run the full-suite command in the table → 0 failed, 1 skipped.

## Test plan

- No new tests. This plan preserves existing test behavior; the suite itself is the check.
- The renamed `test_bootstrap_helper_is_test_only` in `tests/test_auth_permissions.py` still covers "does not overwrite existing users".

## Done criteria

- [ ] `grep -rn "ensure_default_users\|DEFAULT_USERS" factory | grep -v __pycache__` returns nothing
- [ ] `grep -rn "A.ensure_default_users\|_A.ensure_default_users" tests | grep -v __pycache__` returns nothing
- [ ] Full suite: 0 failed, 1 skipped
- [ ] `tests/_auth_helper.py` is the only place holding the default passwords
- [ ] No file outside Scope was modified
- [ ] `plans/README.md` row 006 updated to DONE

## STOP conditions

- Any non-test module other than `factory/seed.py` imports `ensure_default_users` (run the drift check; if an unexpected caller appears, STOP).
- The suite fails in a test you did not touch — report the failing test; do not patch it.
- Removing the function breaks `tests/test_data_access.py:294`'s assertion about `factory/data_access.py` — that would mean the assertion was checking the wrong module; STOP and report.
- A caller passes real production credentials. Never invent or copy a password other than the test values already in `tests/_auth_helper.py:_CREDS`.

## Maintenance notes

- Any new test that needs a temp DB should call `_auth_helper.fresh_db()` or `_auth_helper.ensure_default_users()`. There is no production bootstrap helper by design; the first-run admin comes from the login dialog.
- A reviewer should confirm the removed code is gone from the frozen build: `grep -c "admin123" dist/Simpld/_internal/base_library.zip` is not a valid check, but after the next Windows build, `grep -a "admin123" dist/Simpld/Simpld.exe dist/Simpld/_internal/base_library.zip` should return no hits.
- Production data paths, auth, and RBAC are unchanged by this plan.
