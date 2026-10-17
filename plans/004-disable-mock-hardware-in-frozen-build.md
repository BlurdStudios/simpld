# Plan 004: Ignore the mock-hardware override in the frozen Windows app

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Confirm `factory/hardware/devices.py` lines 135-138 define `mock_allowed()` returning `os.environ.get("SIMPLD_ALLOW_MOCK") == "1"`. Confirm `factory/db.py` defines `_is_frozen()`. If either differs, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: security
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

`SIMPLD_ALLOW_MOCK=1` makes `require_scale()` and `require_printer()` accept mock drivers when no real scale or printer is configured. On the factory floor, an operator or a stray environment variable could then produce fake weigh records and "printed" labels that are saved as real production data. The code comment says "Production never sets this", but nothing enforces it. The frozen `.exe` is the production build, so the override should be ignored there. Tests keep the override in source checkouts.

## Current state

- `factory/hardware/devices.py:135-138`:
  ```python
  def mock_allowed() -> bool:
      """Test-compat override: SIMPLD_ALLOW_MOCK=1 lets tests exercise the
      mock drivers. Production never sets this — blank config blocks."""
      return os.environ.get("SIMPLD_ALLOW_MOCK") == "1"
  ```
- `factory/hardware/devices.py:141-159`: `require_scale` and `require_printer` both return early when `mock_allowed()` is true.
- `factory/db.py:12-14` defines `_is_frozen()`: true when `sys.frozen` or `sys._MEIPASS` is set (the PyInstaller build).
- `factory/ui/page_packaging.py:332` and `:807` contain test-compat comments referencing `SIMPLD_ALLOW_MOCK`. Do not change their behavior.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compile check | `python -m py_compile factory/hardware/devices.py` | exit 0 |
| New test | `SIMPLD_DB=/tmp/opencode/p004.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_mock_scale.py tests/test_packaging_detect.py -q -p no:cacheprovider` | all pass |
| Full suite | `SIMPLD_DB=/tmp/opencode/p004-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed |

## Scope

**In scope** (the only files you should modify):
- `factory/hardware/devices.py`
- `tests/test_mock_scale.py` (add one test only)

**Out of scope**:
- `factory/ui/page_packaging.py` and every other UI file.
- `build/windows/simpld.spec` and any build file.
- Do not remove the `SIMPLD_ALLOW_MOCK` setting for source checkouts.

## Steps

### Step 1: Block mock when frozen

Change `mock_allowed()` so it returns `False` whenever the code runs frozen. Use the same check as `factory/db.py`:

```python
def mock_allowed() -> bool:
    """Test-compat override: SIMPLD_ALLOW_MOCK=1 lets tests exercise the
    mock drivers. Never honored in the frozen production build."""
    from factory.db import _is_frozen
    if _is_frozen():
        return False
    return os.environ.get("SIMPLD_ALLOW_MOCK") == "1"
```

Keep the import inside the function to avoid a circular import at module load (check `factory/hardware/devices.py` imports first; if `factory.db` is already imported at top level there, use that import instead).

**Verify**: `python -m py_compile factory/hardware/devices.py` → exit 0.

### Step 2: Add a regression test

In `tests/test_mock_scale.py` add one test:
- `monkeypatch.setenv("SIMPLD_ALLOW_MOCK", "1")`
- `monkeypatch.setattr("factory.db._is_frozen", lambda: True)` — note: `devices.py` imports `_is_frozen` inside the function, so patching `factory.db._is_frozen` takes effect.
- assert `devices.mock_allowed() is False`
- assert `pytest.raises(RuntimeError)` around `devices.require_scale({})` (use an empty config dict; the blank config must not be a scale).

Also add the opposite case in the same test file: with `_is_frozen` returning `False` and `SIMPLD_ALLOW_MOCK=1`, `devices.mock_allowed() is True`.

**Verify**: `SIMPLD_DB=/tmp/opencode/p004.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_mock_scale.py -q -p no:cacheprovider` → all pass, including the new test.

### Step 3: Full suite

**Verify**: full-suite command in the table → 0 failed. Note: some existing tests depend on mock in source mode; that is expected because `_is_frozen()` is False there.

## Test plan

- One added test in `tests/test_mock_scale.py` covering the frozen block and the source-mode allow.
- Model the test after the existing monkeypatch usage in the same file.

## Done criteria

- [ ] `mock_allowed()` returns False when `_is_frozen()` is True, regardless of env
- [ ] New test passes; full suite 0 failed
- [ ] Only `factory/hardware/devices.py` and `tests/test_mock_scale.py` modified
- [ ] `plans/README.md` row 004 updated to DONE

## STOP conditions

- Existing tests that use mock fail after the change in source mode (should not happen since `_is_frozen()` is False there). Report with the failing test names; do not patch them.
- A circular import appears when importing `factory.db` from `devices.py`. Report; do not restructure modules.
- The check `_is_frozen` does not exist any more in `factory/db.py`.

## Maintenance notes

- Plan 005 removes the default-credential helper from the frozen build; this plan only covers the hardware override.
- A reviewer should confirm that no production code path sets `SIMPLD_ALLOW_MOCK` (grep the `factory/` package; only comments and this function should mention it).
