# Plan 008: Show "No of Pieces" on the reel sticker

> **Executor instructions**: Follow this plan step by step. Run every verification command and confirm the expected result before moving to the next step. If anything in "STOP conditions" occurs, stop and report — do not improvise. When done, update the status row for this plan in `plans/README.md`.
>
> **Drift check (run first)**: Other plans may have landed concurrently — only the files listed in Scope matter for this check.
> - `grep -n "REEL_ROWS = " factory/hardware/labels.py` → one line, six weights, starting `[0.115, 0.135, 0.115, 0.155, 0.15, 0.23]`.
> - `grep -n "def render_reel" factory/hardware/labels.py` → line ~157, and `_table_base(REEL_ROWS, {4, 5}, 3, ...)` inside.
> - `grep -n "def reel_label_text" factory/ui/label_preview.py` → line ~204; the date line ends `No of Reels:1`.
> - `grep -n "mid_y = int(H \* 0.55)" tests/test_sticker_preview.py` → line ~151.
> - `grep -n "wrappers_per_reel" factory/data_access.py | head -3` → the product helpers exist; `get_job_by_id` does `SELECT * FROM jobs` (line ~335), so the job dict on every sticker path carries `wrappers_per_reel`.
> If any of these differ, STOP and report.

## Status

- **Priority**: P2
- **Effort**: S
- **Risk**: LOW (one art layout + one preview text string; no data, DB or print payload changes)
- **Depends on**: none
- **Category**: feature / labels
- **Planned at**: commit `7cb00f3` (plan 007 may land concurrently — unrelated files)

## Why this matters

The operator and the people handling the reels need to know how many pieces (wrappers) a reel contains, and the reel sticker is the one thing that travels with the reel. Today the sticker shows Gross/Core/Net and the roll number but no piece count — while the dispatch slip already shows the count as `Wrappers/Reel: N`. Per the owner's decision: the reel sticker gets a plain-language line **`No of Pieces:<N>`** (not the word "wrappers"), mirroring the existing `No of Reels:1` style. Legacy jobs without a count show **`No of Pieces:unknown`** — the same "never invent" rule the slip uses. Scope is deliberately the reel sticker and its on-screen preview only; the slip, product form and table headers keep the word "wrappers".

The count comes from the job snapshot (`jobs.wrappers_per_reel`, entered once on the product form and copied to the job at creation). Every sticker call path already has it: `page_packaging.current_job()` → `DA.get_job_by_id()` → `SELECT * FROM jobs`, and the print dialog passes the same job dict in its payload.

## Current state

- `factory/hardware/labels.py:152-191` — `REEL_ROWS = [0.115, 0.135, 0.115, 0.155, 0.15, 0.23]` (header, job, date, gross, core, net) and `render_reel(job, reel)`: `_table_base(REEL_ROWS, {4, 5}, 3, ...)`; rows 3-5 left = weight labels in column A / values in B; right box C→E = "Roll No#" caption + big roll identity. Row 2 = date (A→C) | `No of Reels:1` (C→E).
- `factory/ui/label_preview.py:204-216` — `reel_label_text(job, reel)`: the dialog's text preview, whose docstring/UI says it "matches the printed reel sticker layout exactly". It has the same rows minus the art.
- `factory/ui/label_preview.py:242-304` — `ReelLabelDialog`: shows `reel_label_text` + a PRINT button; `do_print` sends `{"job": job, "reel": reel}` to the printer driver.
- `factory/hardware/qtprint.py:55-67` — real driver renders the PNG with `LabelArt.render_reel(job, reel)`; the mock logs the dict. Both pick up the art change automatically.
- `factory/ui/page_packaging.py:587-593` — live preview builds a preview reel dict and calls `render_reel(job, reel)` with `job = current_job()`.
- `tests/test_sticker_preview.py:137-153` — `test_reel_table_art_matches_photo_layout` renders the art and probes `(int(W * 0.85), int(H * 0.55))` for white (no grid line through the Roll No# box). **The layout change moves the text bands; this probe must move** (see Step 3).
- `tests/test_hardware_real.py:265-289` — print-path test spies on `render_reel` with a job dict that has **no** `wrappers_per_reel` key — `render_reel` must therefore treat a missing/None key as `unknown`, not crash.

## Commands you will need

| Purpose | Command | Expected on success |
|---|---|---|
| Compile check | `python -m py_compile factory/hardware/labels.py factory/ui/label_preview.py tests/test_sticker_preview.py` | exit 0 |
| Target tests | `SIMPLD_DB=/tmp/opencode/p008.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_sticker_preview.py tests/test_hardware_real.py tests/test_lan_slip_preview.py -q -p no:cacheprovider` | 0 failed |
| Full suite | `SIMPLD_DB=/tmp/opencode/p008-full.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests -q -p no:cacheprovider` | 0 failed, 1 skipped |
| Art still 4×3in | covered by target tests | (812, 609) asserted there |

**Known host flake (updated 2026-10-17 12:50)**: the full suite can segfault (exit 139, no summary) on this machine at `tests/test_packaging_detect.py:167` (`app.processEvents()`). Later evidence: it is timing-sensitive on this host — it reproduced in clean HEAD worktrees both with and without the repo `data/` dir, while earlier the same day the identical code passed the full suite in those worktrees; single files (`python -m pytest tests/test_packaging_detect.py`) pass. It is a host-side Qt issue (PySide6 6.11.2 + system Python 3.14 here; CI runs Python 3.12 + PySide6 6.12.0), not a product bug and not related to your change. Procedure: if the full suite segfaults, rerun it once; if it crashes twice, run **every test file individually** (one `python -m pytest tests/<file> -q -p no:cacheprovider` process per file, same env) and report that per-file table. Every file passing individually is an acceptable substitute for the full-suite run — do NOT try to fix the flake and do not stop on it.

## Scope

**In scope**:
- `factory/hardware/labels.py` (reel sticker art only — leave `render_carton` / `render_weight_carton` untouched)
- `factory/ui/label_preview.py` (`reel_label_text` only — leave `carton_label_text` untouched)
- `tests/test_sticker_preview.py`
- `plans/README.md` (status row)

**Out of scope**:
- The dispatch slip (`slip_pdf.py`) — it keeps `Wrappers/Reel:` exactly as is.
- Product/job forms, table headers, validation messages — "wrappers" stays there.
- Carton stickers — do not add a piece count there; it would need a sum the data does not state.
- The DB schema, `services.py`, `data_access.py`, `lan_*`, print payloads.
- Any file not listed above.

## Steps

### Step 1 — `factory/hardware/labels.py`: add the pieces row to the reel art

1. Change `REEL_ROWS` to seven rows (insert the pieces row after date):
   ```python
   # Row weights: header, job, date, pieces, gross, core, net.
   REEL_ROWS = [0.115, 0.135, 0.115, 0.115, 0.155, 0.15, 0.23]
   ```
   (`_table_base` normalises by the sum, so no other weight change is needed.)
2. In `render_reel`, switch the grid to `_table_base(REEL_ROWS, {5, 6}, 4, width_mm, height_mm, dpi)` and unpack seven rows:
   ```python
   (r0a, r0b), (r1a, r1b), (r2a, r2b) = rows[0], rows[1], rows[2]
   (r3a, r3b), (r4a, r4b), (r5a, r5b), (r6a, r6b) = rows[3], rows[4], rows[5], rows[6]
   ```
   Mapping: r0 header, r1 job name, r2 date + `No of Reels:1`, **r3 pieces (new)**, r4/r5/r6 gross/core/net. The A|B split (`ab_rows`) moves from `{4, 5}` to `{5, 6}` and the weight block starts at row 4 (`weight_first=4`); the C→E right box (Roll No#) now spans **r4a → r6b** — update the caption/ident cells accordingly (`mid = r4a + (r6b - r4a) * 0.28` and the caption `(C, r4a, E, mid)`, ident `(C, mid, E, r6b)`).
3. Insert the pieces line after the date row — full width, same left-aligned style as the Job Name row:
   ```python
   wpr = job.get("wrappers_per_reel")
   try:
       pieces_txt = f"No of Pieces:{int(wpr)}" if wpr is not None else "No of Pieces:unknown"
   except (TypeError, ValueError):
       pieces_txt = "No of Pieces:unknown"
   _cell_text(d, (A, r3a, E, r3b), pieces_txt, 30)
   ```
   Rules: exact string `No of Pieces:<N>` (no space after the colon — matches `No of Reels:1`); `unknown` for missing/None/unparseable; never a guessed value, never `16500.0`.
4. Update the module docstring's layout description to include the pieces row.

**Verify**: `python -m py_compile factory/hardware/labels.py`; render a quick check in a REPL:
`L.render_reel({"job_no": 1, "job_name": "X", "wrappers_per_reel": 16500}, {"gross": 11.38, "core": 0.28, "net": 11.1, "created_at": "2026-10-03 10:00:00", "roll_no": 23}).size == (812, 609)` and the same call with no `wrappers_per_reel` key also returns an image.

### Step 2 — `factory/ui/label_preview.py`: keep the dialog text in sync

`reel_label_text` gains the same line between the date line and the Gross line:
```python
f"No of Pieces:{_pieces_txt(job)}\n"
```
where the value is formatted by the same rule (export a tiny module-level helper `_pieces_txt(job)` in this file, or inline the same try/int/unknown formatting used in Step 1 — but do not duplicate the DB access; both lines read only `job.get("wrappers_per_reel")`). Keep the existing lines byte-identical otherwise.

**Verify**: `python -m py_compile factory/ui/label_preview.py`; REPL: `reel_label_text({"job_no": 1, "job_name": "X", "wrappers_per_reel": 16500}, {"gross": 1, "core": 0, "net": 1, "created_at": "2026-10-03 10:00:00", "roll_no": 1})` contains `No of Pieces:16500`; without the key it contains `No of Pieces:unknown`.

### Step 3 — `tests/test_sticker_preview.py`: update the probe + add the pieces test

1. `test_reel_table_art_matches_photo_layout` — the job dict there has no `wrappers_per_reel` (fine; unknown path). The new geometry moves the Roll No# caption band to ≈0.52–0.58·H and the big ident glyphs to ≈0.68–0.94·H, so the old probe at `H * 0.55` would hit the caption text. Move the probe into the blank band between the two text rows:
   ```python
   mid_y = int(H * 0.63)
   ```
   Keep the assertion's meaning: `(int(W * 0.85), mid_y)` must be white — no grid line crosses the right box. If 0.63 lands on art on your machine, pick another y in the 0.59–0.66 band and note it in your report; do not change the assertion's intent or probe x.
2. New test `test_reel_sticker_shows_no_of_pieces`:
   - known job: `{"job_no": 4167, "job_name": "APPY FIZZ-160ML BODY", "wrappers_per_reel": 16500}` — `reel_label_text` contains `No of Pieces:16500`; `render_reel` returns (812, 609) and the image bytes **differ** from the same render with `wrappers_per_reel` set to `None` (proves the count is drawn into the art, not only stated).
   - unknown job (no key at all — same shape as `test_hardware_real._job_reel_detail`): `reel_label_text` contains `No of Pieces:unknown` and `render_reel` still returns (812, 609) without raising.
   - add the new test to the `__main__` runner list at the bottom of the file.
3. Do not change any other test in the file.

**Verify**: run the "Target tests" command → 0 failed.

### Step 4 — `plans/README.md`

Set row 008 to `DONE` (or `BLOCKED` with a one-line reason).

## Test plan

- Extended/added tests only in `tests/test_sticker_preview.py`; no new test file.
- `tests/test_hardware_real.py` (print path with a job lacking the key) and `tests/test_lan_slip_preview.py` (slip regression — the slip must keep saying `Wrappers/Reel`) must stay green **unmodified**.
- No test touches `data/` — temp `SIMPLD_DB` only.

## Done criteria

- [ ] `render_reel` draws `No of Pieces:<N>` (or `unknown`) as a full-width row between date and gross; art is still (812, 609) at 203dpi
- [ ] `reel_label_text` contains the same value; dialog preview and art agree
- [ ] `grep -n "wrappers_per_reel" factory/hardware/labels.py factory/ui/label_preview.py` shows reads only — no writes, no DA imports added
- [ ] Slip text unchanged: `grep -n "Wrappers/Reel" factory/slip_pdf.py` → still the old string
- [ ] Target tests 0 failed; full suite 0 failed, 1 skipped (one rerun allowed for the known segfault flake; if it crashes twice, the per-file table from the note above is the acceptable substitute)
- [ ] `plans/README.md` row 008 updated
- [ ] No file outside Scope modified

## STOP conditions

- The art change would alter the fixed 4×3in (101.6 × 76.2 mm / 812 × 609 px) sticker size — it must not.
- You are tempted to touch the slip, carton stickers, forms, or DB — STOP; this plan is sticker-only by explicit owner decision.
- A job value that is a non-integer (e.g. `"16500"` from LAN JSON round-trips) makes `int()` raise — that is expected: keep the `unknown` fallback, do not coerce floats like `16500.0` into the string.
- `test_hardware_real.py` fails on a job without the key — your missing-key path is wrong; fix `render_reel`, never the test.
- The full suite segfaults twice in a row (known host flake) — do NOT stop: run every test file individually (one pytest process per file) and report that per-file table (see the "Known host flake" note above). Do not try to fix the flake.

## Maintenance notes

- The count is the **job snapshot** (`jobs.wrappers_per_reel`), not a per-reel measurement; the sticker states the planned pieces-per-reel, same as the slip's `Wrappers/Reel`. If per-reel actual counts are ever needed, that is a data-model change, not a label change.
- If the carton sticker later needs a piece count, compute it from stored reel links only; do not multiply `num_reels × wrappers_per_reel` unless the owner confirms that is the real quantity.
- Keep the two renderers in lockstep: any future `render_reel` row change should be mirrored in `reel_label_text` (the dialog promises "matches the printed layout").
