# Plan 001: Update the slip-dialog test to expect the qualified roll label

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: This project is not a git repo. Confirm `factory/ui/label_preview.py` line ~514 contains `r.get("roll_label") or f"Roll #{r['roll_no']}"`. If not, STOP.

## Status

- **Priority**: P1
- **Effort**: S
- **Risk**: LOW
- **Depends on**: none
- **Category**: tests
- **Planned at**: commit `unversioned` (no git), 2026-10-17

## Why this matters

Defect 3 in `PRODUCTION_READINESS.md` was fixed so the slip dialog shows the qualified roll label (`R2026-10-1-0001`) instead of a bare roll number that repeats every month. The fix is correct, but the test `tests/test_slip_evidence.py::test_dialog_identifies_reel_box_rows` still asserts the old bare `#1`, so the full suite now fails. Leaving a red test in place hides real regressions. The test must be updated to the new intended behavior, not weakened.

## Current state

- `tests/test_slip_evidence.py:308` currently reads:
  `assert f"#{r['roll_no']}" in d.table.item(0, 1).text()`
  The actual cell text is `'R2026-10-1-0001'`, so the assertion fails.
- `factory/ui/label_preview.py:514` (the fixed code, do not change it):
  `QTableWidgetItem(str(r.get("roll_label") or f"Roll #{r['roll_no']}"))`
- `factory/slip_pdf.py:153` provides the `roll_label` key in each reel row returned by `get_slip`.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Single test | `SIMPLD_DB=/tmp/opencode/p001.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_slip_evidence.py -q -p no:cacheprovider` | `N passed`, 0 failed |
| Full suite | `SIMPLD_DB=/tmp/opencode/p001-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed (1 skipped is acceptable) |

## Scope

**In scope** (the only file you should modify):
- `tests/test_slip_evidence.py` (only the one assertion at about line 308)

**Out of scope**:
- `factory/ui/label_preview.py` — the code is correct; do not change it to satisfy the test.
- Any other test assertion. Do not delete or weaken other assertions.

## Steps

### Step 1: Confirm the roll_label value

Open `tests/test_slip_evidence.py` around lines 296-312 and `factory/slip_pdf.py` around line 153. Confirm the test's `r` fixture row and the `slip["rows"]` entry for the reel both carry `roll_label`.

**Verify**: `grep -n "roll_label" factory/slip_pdf.py tests/test_slip_evidence.py` → shows `factory/slip_pdf.py:153` and the test file references it or the row dict. If `roll_label` is absent from `slip["rows"]`, STOP.

### Step 2: Change the assertion to the qualified label

Replace the line:
```python
    assert f"#{r['roll_no']}" in d.table.item(0, 1).text()
```
with an assertion that the cell shows the row's `roll_label` taken from the slip data:
```python
    assert slip["rows"][0]["roll_label"] == d.table.item(0, 1).text()
```
Keep the surrounding lines unchanged.

**Verify**: `SIMPLD_DB=/tmp/opencode/p001.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_slip_evidence.py -q -p no:cacheprovider` → all tests pass, including `test_dialog_identifies_reel_box_rows`.

### Step 3: Run the full suite

**Verify**: the full-suite command in the table above → 0 failed.

## Test plan

- No new tests. This plan only corrects an out-of-date assertion.
- Verification is the full suite passing, including `test_dialog_identifies_reel_box_rows`.

## Done criteria

- [ ] `tests/test_slip_evidence.py::test_dialog_identifies_reel_box_rows` passes
- [ ] Full suite: 0 failed
- [ ] Only `tests/test_slip_evidence.py` was modified
- [ ] `plans/README.md` row 001 updated to DONE

## STOP conditions

- `roll_label` is not present in the slip rows (Step 1) — the fix in `label_preview.py` would be showing blanks.
- Any other test fails after Step 2 — report it; do not patch other tests.
- The line numbers or assertion text differ from this plan — the code has drifted.

## Maintenance notes

- Any future test on slip rows should expect the qualified `roll_label`, not `roll_no`.
- A reviewer should check that only the single assertion changed and no other test was weakened.
