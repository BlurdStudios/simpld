# Plan 003: Log crashes to a file and show an error dialog instead of failing silently

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Confirm `run.py` has a `def main():` that calls `QApplication(sys.argv)` and that `factory/db.py` defines `app_data_dir()`. Confirm `grep -rn "excepthook\|import logging" factory run.py` returns nothing. If any of these differ, STOP.

## Status

- **Priority**: P1
- **Effort**: M
- **Risk**: LOW
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

The Windows build is windowed (`console=False` in `build/windows/simpld.spec`). Any uncaught exception in the factory app therefore disappears: the user sees the app close, and nobody has a log. The codebase has no `logging` usage and no `sys.excepthook`. A production crash on the floor cannot be diagnosed or reported. This plan adds a rotating log file in the per-user data directory and a global exception hook that records the traceback and tells the operator what happened.

## Current state

- `run.py` lines 65-146: `def main():` creates `QApplication`, configures `DA` (`factory.data_access`), shows the login dialog, and runs `app.exec()`. There is no exception hook.
- `factory/db.py:17` defines `app_data_dir()`, which returns the writable per-user directory (`%LOCALAPPDATA%\Simpld` when frozen, `<repo>/data` in source). Reuse it for the log location.
- `build/windows/simpld.spec` sets `console=False`, so `stderr` is not visible to the user.
- Log content rule: never log passwords, session tokens, or the `pass_hash` column. Log only exception type, message, and traceback.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compile check | `python -m py_compile run.py factory/logging_setup.py` | exit 0 |
| New test | `SIMPLD_DB=/tmp/opencode/p003.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_logging_setup.py -q -p no:cacheprovider` | all pass |
| Full suite | `SIMPLD_DB=/tmp/opencode/p003-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed |

## Scope

**In scope** (the only files you should modify or create):
- `factory/logging_setup.py` (create)
- `run.py` (modify: call the setup function near the top of `main()`)
- `tests/test_logging_setup.py` (create)

**Out of scope**:
- Any UI page, service, auth, or DB module.
- `build/windows/simpld.spec` — do not change `console=False`.
- Do not add log lines to existing business code.

## Steps

### Step 1: Create `factory/logging_setup.py`

Create the module with two functions:

- `setup_logging(log_dir: str | None = None) -> str`
  - Resolve `log_dir` as `os.path.join(app_data_dir(), "logs")` when not given (import `app_data_dir` from `factory.db`).
  - `os.makedirs(log_dir, exist_ok=True)`.
  - Create a `logging.handlers.RotatingFileHandler` writing `simpld.log` in that folder, `maxBytes=1_000_000`, `backupCount=5`, `encoding="utf-8"`, with formatter `"%(asctime)s %(levelname)s %(name)s: %(message)s"`.
  - Attach it to the root logger, set level `logging.INFO`.
  - Return the path of the log file.
- `install_excepthook(log_path: str) -> None`
  - Set `sys.excepthook` to a function `hook(exc_type, exc, tb)` that:
    1. calls `logging.getLogger("simpld").critical("Unhandled exception", exc_info=(exc_type, exc, tb))`
    2. calls the original hook for default behavior only when `exc_type` is `KeyboardInterrupt` (return after logging otherwise)
    3. tries to show a `QMessageBox.critical` with the text `"Simpl'd hit an unexpected error and needs to close.\n\nDetails were saved to:\n{log_path}"`; wrap the dialog call in `try/except Exception: pass` so a dialog failure never masks the log.

Do not log any argument values; only the traceback.

**Verify**: `python -m py_compile factory/logging_setup.py` → exit 0.

### Step 2: Call the setup from `run.py`

In `run.py`, inside `main()`, immediately after `app.setStyle` / before `DA.configure_from_env()`, add:

```python
    from factory.logging_setup import setup_logging, install_excepthook
    log_path = setup_logging()
    install_excepthook(log_path)
```

Keep it inside `main()` so the source checkout and the frozen build behave the same.

**Verify**: `python -m py_compile run.py` → exit 0.

### Step 3: Add tests in `tests/test_logging_setup.py`

Model after `tests/test_toast.py` for the Qt setup if needed. Tests:
1. `setup_logging(tmp_path)` creates `tmp_path/simpld.log` after one `logging.getLogger("simpld").info("hello")` call and the file contains `hello`.
2. Calling `setup_logging` twice on the same dir does not add duplicate handlers to the root logger (count `RotatingFileHandler` instances whose `baseFilename` matches; must be 1).
3. After `install_excepthook(path)`, calling `sys.excepthook(ValueError, ValueError("boom"), None)` writes a line containing `Unhandled exception` and `boom` to the log. Restore the original `sys.excepthook` in a `finally` block. Patch `QMessageBox.critical` to a no-op with `unittest.mock.patch` so the test does not open a dialog.
4. The log must not contain the strings `pass_hash` or `session_token` after logging a message that includes them (the hook does not log values, so just assert the hook output does not echo a fake token string you pass as the exception message is out of scope — skip this case if it is hard to write; record that in your report).

**Verify**: `SIMPLD_DB=/tmp/opencode/p003.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_logging_setup.py -q -p no:cacheprovider` → all pass.

### Step 4: Full suite

**Verify**: full-suite command in the table → 0 failed.

## Test plan

- New file `tests/test_logging_setup.py` with the cases above.
- Verification: new tests pass plus full suite 0 failed.

## Done criteria

- [ ] `factory/logging_setup.py` exists with `setup_logging` and `install_excepthook`
- [ ] `run.py` calls both inside `main()`
- [ ] `tests/test_logging_setup.py` exists and passes
- [ ] Full suite: 0 failed
- [ ] No file outside Scope was modified
- [ ] `plans/README.md` row 003 updated to DONE

## STOP conditions

- `factory/db.py:app_data_dir` no longer exists or has a different signature.
- Adding the hook changes behavior of `tests/test_ui_smoke.py` or other UI tests — report and stop; do not patch them.
- A log line would contain a password, token, or `pass_hash`. Remove it; if you cannot, STOP.

## Maintenance notes

- Whoever reads the log should know it lives at `%LOCALAPPDATA%\Simpld\logs\simpld.log` on Windows (the frozen data root).
- Future code should use `logging.getLogger("simpld.<area>")` instead of `print`.
- A reviewer should confirm no secret values are logged anywhere.
