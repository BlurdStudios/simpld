# Plan 005: Fix the malformed installer AppId and pin runtime dependencies

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Confirm `build/windows/simpld.iss` line 24 reads `AppId={{8C4F2A1B-6D3E-4F7A-9C1B-2E5A7D4F6B9C0}`. Confirm `requirements.txt` has four `>=` lines. If not, STOP.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: MED (the AppId change breaks in-place upgrades from any installer built with the old id; no release has shipped, so this is acceptable — see step 1)
- **Depends on**: none
- **Category**: dx
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

Two build-reproducibility problems:

1. The Inno Setup `AppId` in `build/windows/simpld.iss` has a last GUID group of 13 hex characters. A GUID requires 12. Inno treats AppId as an opaque string so it may still work, but the value is not a valid GUID and two different tools could compute different identities from it. This must be fixed before the first release, because the AppId is the key Windows uses to recognize an installed copy for upgrades.
2. `requirements.txt` uses `>=` for PySide6, qrcode, Pillow, and pyserial, and `build/windows/build_requirements.txt` uses `pyinstaller>=6.0`. The Windows CI installs the newest versions on every run, so the same commit can produce different `.exe` files, and a new PySide6 release can break the Qt plugin bundle silently.

## Current state

- `build/windows/simpld.iss:24`: `AppId={{8C4F2A1B-6D3E-4F7A-9C1B-2E5A7D4F6B9C0}` (note: last group `2E5A7D4F6B9C0` is 13 characters)
- `requirements.txt` (four lines):
  ```
  PySide6>=6.6
  qrcode[pil]>=7.4
  Pillow>=10.0
  pyserial>=3.5
  ```
- `build/windows/build_requirements.txt`: `pyinstaller>=6.0`
- The advisor's local `.venv` is Python 3.14. The CI runner uses Python 3.12. Pins must be installable for Python 3.12 on Windows.
- Exact versions are available from the local venv metadata: `.venv/lib/python3.14/site-packages/*.dist-info/METADATA` (read `Name:` and `Version:`).

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Validate new GUID | `python -c "import re,sys; s=open('build/windows/simpld.iss').read(); m=re.search(r'AppId=\{\{([0-9A-F-]+)\}', s); print(m.group(1)); print(bool(re.fullmatch(r'[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}', m.group(1))))"` | prints the GUID and `True` |
| Check a pin exists for Windows cp312 | `python -m pip download --only-binary=:all: --platform win_amd64 --python-version 3.12 --no-deps -d /tmp/opencode/p005-wheels "<name>==<version>"` | exit 0 for each pin |

## Scope

**In scope** (the only files you should modify):
- `build/windows/simpld.iss` (line 24 only)
- `requirements.txt`
- `build/windows/build_requirements.txt`

**Out of scope**:
- `build/windows/simpld.spec`, `build_windows.bat`, `.github/workflows/build-windows.yml`
- Any `factory/` code.
- Do not install anything into the project `.venv`. Downloads go to `/tmp/opencode/p005-wheels` only.

## Steps

### Step 1: Replace the AppId with a valid GUID

Generate one new GUID and write it, in uppercase, into line 24 as `AppId={{<GUID>}`. Generate it with:
`python -c "import uuid; print(str(uuid.uuid4()).upper())"`

Keep the double-brace prefix used by Inno Setup. Keep every other line unchanged.

Note for the reviewer: the existing `dist/Simpld-Setup-1.0.0.exe` was built with the old id and was never released, so changing the id does not break a shipped upgrade path. Record this in the report.

**Verify**: the GUID validation command in the table → prints the new GUID and `True`.

### Step 2: Pin runtime dependencies to exact versions

For each package in `requirements.txt`, read its installed version from `.venv/lib/python3.14/site-packages/*.dist-info/METADATA` (`Name:` and `Version:` lines). Write each as an exact pin with `==`, keeping the extras for `qrcode[pil]`:

```
PySide6==<version>
qrcode[pil]==<version>
Pillow==<version>
pyserial==<version>
```

**Verify**: for each pin, run the wheel-check command in the table with the exact `<name>==<version>`. Expected exit 0. If any pin has no `win_amd64` cp312 wheel, STOP and report which one.

### Step 3: Pin the build tool

Read the installed PyInstaller version from the venv metadata if present. If PyInstaller is not in the venv, STOP and report — do not guess a version.

Write `build/windows/build_requirements.txt` with `pyinstaller==<version>` and keep the existing comment lines.

**Verify**: wheel-check command with `pyinstaller==<version>` → exit 0.

## Test plan

- No new tests. Verification is the GUID regex check and the wheel-availability checks above.
- Final check: `python -m pytest tests -q -p no:cacheprovider` with `SIMPLD_DB` pointing to `/tmp/opencode/p005.db` still reports 0 failed (pins must not break the local environment; this runs against the existing venv, not a reinstall).

## Done criteria

- [ ] `build/windows/simpld.iss` AppId passes the GUID regex check
- [ ] `requirements.txt` uses `==` for all four packages, each verified for win_amd64 cp312
- [ ] `build/windows/build_requirements.txt` pins pyinstaller with `==`
- [ ] Only the three in-scope files modified
- [ ] `plans/README.md` row 005 updated to DONE

## STOP conditions

- A pinned version has no win_amd64 cp312 wheel. Report which package; do not choose a different version silently.
- PyInstaller is not installed in `.venv`. Report; do not guess.
- The AppId line differs from "Current state" before you edit it.

## Maintenance notes

- Pins must be bumped deliberately, one package at a time, followed by a Windows build and a smoke test of the bundled Qt plugins (see the check in `build_windows.bat` lines 14-18).
- A reviewer should confirm the AppId is used nowhere else (grep `build/` for the old GUID's first group `8C4F2A1B`).
