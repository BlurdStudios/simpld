# Production readiness — NOT READY (software scope verified, release gates open)

Audit date: 2026-10-13 (final review sub-agent, Sol6.1 planning ONLY).
Scope: READONLY source/tests inspection + one safe suite + offscreen screenshots.
No source/test edits in this round. No production DB writes, no hardware calls,
no server launch. Only this file was updated.
Integration `simpld-integration-20261013-2` and `simpld-ui-tests-20261013-2`
are both terminal COMPLETED (verified via t3_thread_read); prior "still running"
snapshot below is superseded.

## Terminal task outcomes accepted as evidence (not rerun)

- Integration: monthly 10/10 (legacy-FK fix at `factory/db.py:486` FK OFF before
  BEGIN + count/link/`foreign_key_check` guards), auth 28
  (`test_auth_permissions` 16 + `test_integration_auth_production` 12),
  `test_data_access` 9 + `test_lan_transport` 10 = 19, UI smoke set 17
  (`test_ui_smoke` + `test_debug_data` + `test_products` + `test_history`).
  Full `tests/` pass reported 173 passed / 1 failed; the single failure is
  `test_citizenusb_backend.py::test_live_discovery_safe_readonly` (expects a
  physical Citizen USB printer string; no printer on this host; files in that
  area untouched — environmental, not a regression).
- UI tests: `tests/test_requested_ui.py` 14 passed,
  `tests/test_requested_lan_ui.py` 3 passed (isolated HTTPS fixture only).
  Only `tests/test_requested_ui*.py` touched, no source edits, no weakened
  assertions.

## Verified in this round (fresh runs, temp DB / offscreen only)

- `tests/test_requested_ui.py`: **14 passed in 6.13s**
  (`QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1`, temp `SIMPLD_DB`).
  Physical-discovery check before choosing: `test_citizenusb_backend.py`
  runs a real sysfs/USB discovery subprocess and `test_hardware_real.py`
  touches serial ports — both EXCLUDED per instructions. The chosen suite
  uses only `SIMPLD_ALLOW_MOCK` + `QApplication` offscreen + temp-DB auth
  fixtures (grep-verified, no `serial`/`list_ports`/`socket`/`urlopen`).
- Native Qt screenshots (offscreen, temp DB, mock permitted in test process
  only) — full `MainWindow` at both minimum sizes, Stage 1 vs Stage 2:
  - `/tmp/simpld-review-shots/win1080-stage1b.png` (1080x680, Stage 1)
  - `/tmp/simpld-review-shots/win1080-stage2b.png` (1080x680, Stage 2)
  - `/tmp/simpld-review-shots/win1320-stage1b.png` (1320x840, Stage 1)
  - `/tmp/simpld-review-shots/win1320-stage2b.png` (1320x840, Stage 2)
  - Inspected: distinct captions ("Stage 1 — weigh a single reel…" vs
    "Stage 2 — weigh a packed box… Boxes are independent of reels"),
    core row VISIBLE in Stage 1 / HIDDEN in Stage 2, preview card max 288
    (`page_packaging.py:373`) with "Type-1 reel (NO QR)" vs
    "Type-2 carton (WITH QR)" art, action button "Save_print reel" vs
    "Pack_print carton", history lists ALL reels with qualified IDs
    (R202610-…), no overlap or clipped controls at 1080x680. Stage-only
    visibility is real (`_refresh_state`, `core_wrap.setVisible(is_reel)`).
- Pre-existing slip PDF artifacts still on disk
  (`/tmp/simpld-slip-verify/output/`: 5 PDFs + 7 page PNGs); visual-PDF
  credit stays with the slip task, not re-claimed here.

## Requirement coverage (source lines, current state)

- Products reusable, wrappers strict positive int (`services.py:141-156`,
  `_parse_wrappers`; UI placeholder "e.g. 16500" at `page_packaging.py:64,106`
  never assumed); legacy names backfilled with wrappers NULL, never invented
  (`db.py:269-306`, `services.py:217-224`); `set_product_wrappers` is
  catalog-only, job snapshots keep NULL (`services.py:199-214`).
- No Size/Core in new dialogs (`page_packaging.py:132-226`); legacy
  size/core rows preserved in DB; job core default 0.5, per-job `core_wt`
  drives the spinbox (`page_packaging.py:660-667`); no `.720` assumption
  found (only legacy seed rows at `seed.py:49-65` keep their recorded values).
- Monthly global roll/box start-at-1, reset per month, never reused
  (`services.py:82-93` `_alloc_seq` in `BEGIN IMMEDIATE`;
  `db.py:230-266` monotonic `id_counters` for jobs/orders;
  `db.py:308-470` migration seeds counters at legacy MAX;
  `services.py:833-847` box alloc with legacy-collision guard).
  Jobs/orders continuous across months (`page_packaging.py:197` hint +
  no period reset on jobs/orders).
- Slips list every reel + every box with qualified IDs and separate totals
  (`slip_pdf.py:95-229` `get_slip`; `342` wrappers unknown; geometry
  `24-73`, pagination `232-270`, paint `294-480`; LAN-safe
  `save_pdf`/`print_slip`/`preview_images` `slip=` params at `497-633`;
  `data_access.slip_data` at `572-581`).
- Auth context-bound + DB-bound (`auth.py:82-133` ContextVar,
  `166-233` re-resolve every call); every `services.py` mutation resolves
  its actor via `require_permission` on the same DB path (`26-51`); first-run
  admin is `BEGIN IMMEDIATE` check+insert, LAN-refused (`login.py:40-80`);
  production bootstrap schema-only (`seed.py:12-25`, `run.py:91-93`,
  `DA.configure_from_env` before local access at `run.py:84-89`).
- Hardware fails closed (`devices.py:135-159`, `mock_allowed` test-only);
  blank config blocks with a pointer to the device pill; failed reads invalidate card to 0
  (`page_packaging.py:793-831`); LAN never falls back to local
  (`data_access.py:1-31`, `describe_error` `620-635`); unknown-outcome writes
  single-attempt + refresh-before-retry messaging throughout packaging/
  dispatch/stock pages; dispatch exact-count complete, immutable
  complete/cancelled, mismatch/duplicate/capacity blocked
  (`services.py:1156-1213`, `1239-1313`).
- Facade migration: no page imports `services`/`auth`/`get_conn` for data
  anymore (grep). Residual `factory.db` imports are LAN-guarded or pure:
  `login.py:13,31,60` (LAN early-return, local bootstrap only),
  `main_window.py:36,169` (local-only `init_db`/display path),
  `page_packaging.py:520,569,591,990` (pure `current_period`/`carton_no_for`/
  `roll_code_for`/`full_roll_label`/`now_iso` — no DB I/O),
  `label_preview.py:9,614` (`COMPANY` constant + `app_data_dir`),
  `pages_misc.py:362` (`app_data_dir`). Acceptable; NOT a bypass.
- Deletes/RBAC: reels loose+unlinked only, cartons Ready+unloaded only
  (reels released to loose), jobs zero-production-and-orders only, all
  admin-only with no-reuse audit (`services.py:591-692`); UI gates match
  (packaging history delete admin-only, jobs delete admin-only, dispatch
  dispatcher/admin, reweigh packager/admin).
- TLS/docs: `LAN_SETUP.md` topology + 4 env vars + SAN + 15s refresh +
  unknown-outcome rule match `data_access.configure_from_env:119-143`,
  `lan_client.py:78-96`, `lan_server.py:44-50,875-928`. Rate limit,
  256KB bound, allowlist, no user-mgmt over LAN all present.

## Ranked defects requiring code changes (parent plans fixes; NOT fixed here)

1. **Slip print/PDF over LAN uses the wrong DB** — `label_preview.py:620-648`
   `do_print`/`do_pdf` call `SP.print_slip(self, job_id, …)` /
   `SP.save_pdf(job_id, path)` WITHOUT the prefetched `slip=` dict the
   dialog already holds (`self._slip = DA.slip_data(job_id)` at `:446`).
   Over LAN this renders/prints from LOCAL SQLite instead of the shared
   packager DB. Fix: pass `slip=self._slip` (and `db_path` where applicable).
2. **LAN slip preview is misleadingly empty** — `label_preview.py:552-566`:
   in LAN mode `pages = []` unconditionally, so the right pane shows
   "Nothing packed for this job yet" even when `self._slip["rows"]` is
   non-empty (evidence table on the left proves it). Fix: render preview
   from `self._slip` via the `slip=` param (PDF-task owned) or show an
   honest "preview unavailable over LAN — evidence table is live" message.
3. **Slip dialog left table shows bare roll numbers** — `label_preview.py:503`
   `f"Roll #{r['roll_no']}"` ignores the qualified `roll_label`/`roll_code`
   in the same row dict (bare numbers repeat monthly). Fix: display
   `r['roll_label']` like `slip_pdf` does.
4. **Users button visible over LAN though unsupported** —
   `main_window.py:122-127` adds Users for admin regardless of `DA.is_lan()`.
   Requirement is hidden/clear remote admin user-manager. Current dialog
   degrades to a warning (`users_dialog.py:86-98` via `RemoteUnsupportedError`)
   but the entry point is not hidden/disabled. Fix: hide or disable with the
   local-admin-only message when `DA.is_lan()`.
5. **README contradicts production bootstrap** — `README.md:24-34` (default
   passwords table) + `:94` (fresh DBs seed demo content) vs actual
   schema-only `seed.py:12-25` + `run.py:91-93`. Fix: docs task rewrites
   README login/seed narrative to first-run-admin + empty floor DB.
6. **Stale dev database on disk** — `data/factory.db` + WAL present (dev
   machine artifact, not evidence). No action in this round (preserve DB);
   installer task must ensure the shipped/Windows build never bundles it
   (`DEMO_WINDOWS.md:59-66` exclusion list is the gate).

## Release gates (all must be evidenced; none are claimed here)

Physical Essae weigh + Citizen labels + office-printer A4 slips (multi-page,
footers, totals) + Windows installed app, per-user data dir, no demo DB
(`DEMO_WINDOWS.md:15-33` artifact NOT YET PRODUCED — Wine prefix blocked;
`build/` has only `windows/` spec) + real two-computer LAN (login, shared
jobs/boxes, 15s refresh, offline-never-writes, retry-after-refresh) +
fresh-bootstrap/first-admin/migration/concurrent-ID/backup-restore suites
green + docs updated. Core physical-tare behavior is preserved-values only;
no fixed-tare claim is made. Until the six defects above are fixed AND the
gates are evidenced: **NOT READY**.
