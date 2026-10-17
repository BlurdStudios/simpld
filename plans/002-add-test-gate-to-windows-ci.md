# Plan 002: Run the test suite in CI before building the Windows app

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Confirm `.github/workflows/build-windows.yml` has a single job named `build` with steps starting at `actions/checkout@v4`. If the structure differs, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: 001 (the suite must be green first, or the gate blocks every build)
- **Category**: tests
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

The Windows build workflow compiles and uploads the `.exe` and installer on every push to `main`/`master`, but it never runs the tests. A regression in slip printing, auth, or the LAN service can ship in an installer with no warning. Adding a test job that the build depends on means a red test blocks the artifact.

## Current state

- `.github/workflows/build-windows.yml` has one job, `build`, on `runs-on: windows-latest`, with steps:
  - `actions/checkout@v4`
  - `actions/setup-python@v5` with `python-version: "3.12"`
  - install `requirements.txt` and `build/windows/build_requirements.txt`
  - `python -m PyInstaller --noconfirm build/windows/simpld.spec`
  - verify `dist/Simpld/Simpld.exe` exists
  - compile installer with Inno Setup
  - upload `dist/Simpld/` and the installer
- The test suite needs `QT_QPA_PLATFORM=offscreen`, `SIMPLD_ALLOW_MOCK=1` (test-only hardware override), and a temporary `SIMPLD_DB` so tests never touch a real database. On the advisor's Linux host the full suite runs in about 100 seconds: 179 passed, 1 skipped, 0 failed once plan 001 is done.
- `tests/test_citizenusb_backend.py` and `tests/test_hardware_real.py` may probe real hardware. They must not be weakened. They are the only tests that could behave differently on a Windows runner.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Parse workflow YAML | `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/build-windows.yml')); print('ok')"` | prints `ok` (if `yaml` is missing, install PyYAML into `/tmp/opencode/venv` and use that interpreter; do not install into the project) |
| Local suite (same as CI) | `SIMPLD_DB=/tmp/opencode/p002.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed |

## Scope

**In scope** (the only files you should modify):
- `.github/workflows/build-windows.yml`

**Out of scope**:
- `build/windows/simpld.spec`, `build/windows/simpld.iss`, `requirements.txt` — do not change the build itself.
- Any test file.

## Steps

### Step 1: Add a `test` job

Add a new top-level job `test` above `build`. It must:
- use `runs-on: windows-latest`
- check out the repo with `actions/checkout@v4`
- set up Python `"3.12"` with `actions/setup-python@v5`
- run `python -m pip install -r requirements.txt` and `python -m pip install pytest`
- set these environment variables for the pytest step only:
  - `QT_QPA_PLATFORM: offscreen`
  - `SIMPLD_ALLOW_MOCK: "1"`
  - `SIMPLD_DB: ${{ runner.temp }}\\test-factory.db`
- run `python -m pytest tests -q -p no:cacheprovider`

**Verify**: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/build-windows.yml')); print(sorted(d['jobs']))"` → prints `['build', 'test']`.

### Step 2: Make `build` depend on `test`

Add `needs: test` to the existing `build` job.

**Verify**: `python -c "import yaml; d=yaml.safe_load(open('.github/workflows/build-windows.yml')); print(d['jobs']['build']['needs'])"` → prints `test`.

### Step 3: Confirm the YAML still parses and the build steps are unchanged

**Verify**: the YAML command in Step 1 → `ok`. Compare the `build` job's `steps` list with the original; the only addition should be `needs`.

## Test plan

- No new tests. This plan adds a CI gate that runs the existing suite.
- Verification is the local suite command passing with 0 failed on the advisor's host, plus the YAML checks above. A real CI run on GitHub is the final proof and is a manual step.

## Done criteria

- [ ] YAML parses; jobs are `build` and `test`
- [ ] `build` has `needs: test`
- [ ] `test` sets `QT_QPA_PLATFORM`, `SIMPLD_ALLOW_MOCK`, and `SIMPLD_DB` to a temp path
- [ ] Only `.github/workflows/build-windows.yml` was modified
- [ ] `plans/README.md` row 002 updated to DONE (note: a real GitHub run is still required)

## STOP conditions

- The workflow structure differs from "Current state" (Drift check).
- The local suite is not green (plan 001 not applied) — do not add the gate yet.
- You are tempted to add `continue-on-error` or skip failing tests. Do not. Report instead.
- You are tempted to hardcode `SIMPLD_DB` to a repo path. Do not; it must be a temp path.

## Maintenance notes

- Tests that depend on real hardware must be marked so CI skips them, not made to pass. A reviewer should check that no hardware test was silently disabled.
- Keep the `SIMPLD_ALLOW_MOCK` test-only setting out of the build job; only the `test` job sets it. Plan 004 removes this override from the frozen app.
