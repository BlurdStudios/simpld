# Plan 009: Licensing tiers + Daily report + Client dashboard (phased)

> **Executor instructions**: This is a GOAL doc for T3 nightly (`/goal`). Do NOT implement everything at once. Execute Phase 1 first, verify, then Phase 2, then Phase 3 slice. Follow steps in order. Run every verification command. If any STOP condition hits, stop and report — do not improvise. When a phase lands, update its row in `plans/README.md`.
>
> **Drift check (run first)**: Only the files listed in Scope matter.
> - `grep -n "VERSION" factory/version.py` → `VERSION = "1.2.1"`, `DEMO_ENABLED = False`.
> - `grep -n "def daily_summary" factory/services.py` → line ~2011.
> - `grep -n "def production_history" factory/services.py` → line ~782.
> - `grep -n "def get_slip" factory/slip_pdf.py` → line ~95.
> - `grep -n "class SummaryPage" factory/ui/pages_misc.py` → line ~305.
> - `grep -n "def configure_from_env" factory/data_access.py` → LAN/local select, no fallback.
> - `grep -n "Startup.*--db.*--cert" factory/lan_server.py` → 4 required flags, TLS mandatory.
> If any differ, STOP and report.

## Status

- **Priority**: P1 (licensing + daily report), P2 (dashboard slice)
- **Effort**: L (3 phases)
- **Risk**: MED (licensing touches startup; dashboard touches network — keep both fail-closed/offline-safe)
- **Depends on**: none (phases depend on each other: 1 → 2 → 3)
- **Category**: feature / licensing / reporting / cloud
- **Planned at**: 2026-10-26, HEAD `run.py` offline-first, 2 PCs/site (packager + dispatcher), 50-site target

## Why this matters (owner intent)

1. **Flexible but not abusable licensing.** 2 PCs on this site (packaging + dispatch can be same PC or split). One site licence, packager-enforced. Tiers later: simple (1 PC, no LAN) / pro (2-3 stations) / premium (5 stations + extra features). Flexible for replacements/grace, hard against copy-paste to another factory or white-label resale.
2. **Daily report button.** Like the packing slip (`slip_pdf.get_slip`, job-scoped) but day-scoped across all jobs: all reels + boxes labelled that day with full data. Operator prints/saves PDF + CSV.
3. **Client web dashboard.** Factory stays offline-first (SQLite on packager, LAN only). Dashboard is vendor-hosted, outbound-sync only. No inbound ports on factory. Vendor owns hosting/cost, customer pays via AMC.

## Non-goals (explicit)

- No online-required activation. Floor never stops on router failure.
- No per-PC key files. No 50 custom installers. One generic installer + one `license.lic` per site.
- No live factory DB exposure to internet. No port-forwarding, no inbound proxy to packager.
- No sticker/slip paper-layout change for licensing (no `Licensed to` on Type-1/2/3). Attribution lives in title bar + About + logs only.
- Dashboard Phase 3 is a thin slice (sync 2 payloads + read-only viewer), not a full ERP.

## Current state (ground truth)

- `run.py:main()` → `DA.configure_from_env()` → local (`ensure_production_db`) or LAN client. No license check exists.
- `factory/auth.py:40 PERMISSION_ROLES`, `ROLE_PAGES` — packager/dispatcher/admin gates. No edition gate.
- `factory/services.py:2011 daily_summary` — 6 counters only. `factory/services.py:782 production_history` — all rows, no date filter. `factory/slip_pdf.py:95 get_slip(job_id)` — job evidence + totals, never mixes reel/box weights.
- `factory/ui/pages_misc.py:305 SummaryPage` — cards + Export CSV (metrics only) + Evening backup. No line-item report. `HistoryPage` — filterable table, no date filter, no print.
- `factory/lan_server.py` — packager HTTPS service, 4 flags, allowlisted ops, `production_history` + `daily_summary` already exposed. `factory/updater.py` — GitHub + LAN + folder channels, hash-verified, offline-safe.
- `factory/version.py:24` — `DEMO_ENABLED=False` prod vs demo split. Keep it: demo bypasses license, prod never does.

---

## Phase 1 — Daily report (do first, offline, ~1 day)

**Goal**: Operator picks a day (default today) → sees every reel weighed + every box packed that day across all jobs → Print / Save PDF / Export CSV.

### Scope P1

In: `factory/services.py` (new `day_report(day)`), `factory/data_access.py` (+facade), `factory/lan_client.py` + `factory/lan_server.py` (new `day_report` op), `factory/slip_pdf.py` (new `render_day_report` reusing geometry, or CSV-first if PDF risks layout), `factory/ui/pages_misc.py` (`SummaryPage` date field + Daily report dialog), `tests/test_day_report.py` (new), `plans/README.md`.

Out: dashboard, licensing, schema migrations (read-only queries only, `date(created_at)=day`, `date(packed_at)=day`).

### Steps P1

1. `services.day_report(day)` — return `{"day", "reel_rows", "box_rows", "totals", "by_job"}`. Reel rows: `reels JOIN jobs WHERE date(created_at)=day` with `roll_code/roll_label` via same helper as `get_slip`; box rows: `cartons JOIN jobs WHERE date(packed_at)=day` with `carton_no/bundle_no/num_reels/total_gross/linked rolls`. Totals: reel count+net, box count+gross, per-job subtotals. Never merge reel weights into box weights (same rule as `get_slip`).
2. Facade + LAN: `DA.day_report(day)` → local `_svc.day_report` or `client.day_report`. Add `@_op("day_report","history.read",("day",))` server-side, `LanClient.day_report` client-side. Validate `day` as `YYYY-MM-DD`, default today.
3. UI: `SummaryPage` add date edit + `Daily report` button → dialog with table (same columns as slip evidence table) + `Print` / `Save PDF` / `Save CSV` reusing `PackingSlipDialog` print path and `SummaryPage.export` CSV pattern. Works in LAN mode via `DA` (no direct `services` import from pages — facade only).
4. Tests: temp `SIMPLD_DB` only. Seed 2 jobs + reels/boxes on 2 days → assert day filter isolates, totals match, empty day returns empty rows + zero totals, LAN round-trip returns same payload.

**Verify P1**: `SIMPLD_DB=/tmp/p009.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_day_report.py -q -p no:cacheprovider` → 0 failed. Full suite per-file green.

**Done P1**: date picker + report dialog prints/saves identical data locally and over LAN; CSV has one row per reel/box; no DB writes.

---

## Phase 2 — Licensing tiers (do second, ~3 days)

**Goal**: One `license.lic` per site on packager. Dispatcher inherits. `simple=1 station/no LAN`, `pro=2-3/LAN`, `premium=5/LAN+features`. Flexible grace, hard against cloning.

### License format (frozen)

```json
{"serial":"SIMPLD-0017","customer":"Vasavi","site_id":"vasavi-f1",
 "edition":"pro","packager_fp":"<board-hash>","max_stations":2,
 "features":["lan","history","summary"],"issued":"2026-10-26",
 "expires":"2027-10-26","max_version":"2.x","sig":"ed25519-base64"}
```

- New dep: `cryptography` (Ed25519 verify). Private key offline with vendor, public key in app (near `factory/version.py`). Never embed private key.
- Fingerprint: board UUID (`wmic csproduct get UUID` / `/sys/class/dmi`) + MAC fallback, hashed. Reinstall on same board = same ID.
- Stations = 1 packager + N dispatchers. Dispatcher ID = `station.id` UUID in `%LOCALAPPDATA%/Simpld/` + HW hash, sent on login. Server table `license_clients(station_id, fp, last_seen)`; count distinct seen in 30d vs `max_stations`.
- Flexibility: +1 overdraft 72h with banner+log; admin can release 1 old station self-serve; packager swap = vendor re-sign (allow 2-3 resets/yr). Expiry + 30d grace write-allow, then write-block / read+backup only. Never delete data.
- Enforcement points only: `run.py` startup (local) + `lan_server.py` startup + per-login. `simple` → `lan_server --serve` refuses (`lan:false`). Dispatcher app does no local check — server returns `license_invalid`, UI shows "contact admin", no local writes.

### Steps P2

1. `factory/license.py` (new, stdlib + `cryptography`): `machine_fingerprint()`, `load_license()`, `verify_license()`, `station_id()`, `license_status()` (`ok/expiring/expired/invalid`). No Qt, no network.
2. `factory/db.py`: additive `license_clients` table migration (same pattern as `_ensure_*`). No row rewrites.
3. Hook: `run.py` after `configure_from_env`, before login — invalid/over-limit = banner + mode (grace/write-block) but always allow Backup/Export. `lan_server.py` — refuse start on bad packager licence; per-login check returns 403 `license_invalid` when over `max_stations`.
4. UI: About → `Licensed to <customer> · <edition> · stations x/y · expires d` + Machine ID + Import licence (file pick → `%LOCALAPPDATA%/Simpld/license.lic`) + station list + release button (admin).
5. Vendor tool: `tools/issue_license.py` (offline, private key via env/file, never in repo) → prints `.lic` + logs serial/fp/expiry to vendor registry (sheet/CSV to start).
6. Tests: `tests/test_licensing.py` — valid/invalid sig, expired/grace/block, simple refuses LAN, over-limit rejects N+1 but allows overdraft once, dispatcher inherits, fingerprint stable across reinstall mock.

**Verify P2**: target test file 0 failed; manual: copy `.lic` to 2nd board → invalid; expired +31d → writes blocked, backup works.

**Done P2**: same-PC and split-PC both count correctly; 2-station site runs on 1 file; cloning fails closed.

---

## Phase 3 — Dashboard slice (do last, vendor-hosted)

**Goal**: One vendor cloud serves all sites. Factory pushes, never serves inbound.

### Architecture (frozen decision)

- Factory: outbound POST on internet-available (reuse `updater.py` trigger point) + manual `Sync now` after Evening backup. Payloads: `day_report(day)` + `order_progress` + `stock_counts`. Auth: `site_id + API key` header. Retry next window. Fail = log only, floor unaffected.
- Cloud: single multi-tenant app + Postgres. Tables: `sites, pushes, day_reports, orders, viewers`. Retention: 2yr detail, older = summaries. Viewer auth: owner sees all jobs/stock; buyer sees only its orders (decide per site at onboarding — default buyer-scoped).
- No per-site servers. No factory port-forward. No live DB queries from web.

### Steps P3 (slice)

1. `factory/sync.py` (new): `build_push()` (calls `DA.day_report(today)` + `DA.list_orders`/`order_progress`), `push_now()` with timeout + key from env/config, redacts tokens on error. `SummaryPage` → `Sync now` button + last-sync label.
2. Cloud repo (separate, not in this repo): 2 endpoints `POST /push-day`, `POST /push-orders` + viewer `GET /s/<site_id>` read-only. Document contract in `SYNC_CONTRACT.md` here (JSON shapes, auth, retention).
3. Pilot 1-2 sites before rolling to 50. Revoke = disable key + freeze viewer; factory keeps running offline.

**Verify P3**: airplane-mode push → queued/logged, no crash; key rotation works; viewer for buyer A cannot fetch site B (403).

**Done P3**: dashboard shows yesterday + today production and order loaded/target without touching factory LAN.

## Test plan (all phases)

- New files only: `tests/test_day_report.py`, `tests/test_licensing.py`, sync contract test with mocked HTTP. No `data/` writes — temp `SIMPLD_DB` + `QT_QPA_PLATFORM=offscreen`.
- Regression: `tests/test_flow.py`, `tests/test_history.py`, `test_lan_transport`, UI smoke stay green. Full suite via `tools/run_tests.py` (per-file, one retry — known host Qt flake, do not fix here).

## Done criteria (goal complete)

- [ ] Daily report prints/saves line-items for any day, local + LAN identical
- [ ] 1 site licence runs 1-PC and 2-PC layouts; 3rd station rejected with clear message + overdraft path
- [ ] Cloned licence on different board fails closed; expired +30d blocks writes but allows backup/export
- [ ] `Licensed to` visible in About/title bar; nothing added to sticker/slip paper layouts
- [ ] Dashboard slice pushes 2 payloads outbound-only; viewer isolation proven; factory works with sync disabled
- [ ] `plans/README.md` rows updated; no file outside Scope modified

## STOP conditions

- Tempted to require internet for launch or licence check — STOP. Floor is offline-first.
- Tempted to expose factory DB/LAN to internet or add inbound ports — STOP. Outbound sync only.
- Tempted to build 50 installers or per-PC keys — STOP. Generic installer + 1 site file.
- Dashboard wants live queries or machine control — STOP. Cached pushes only.
- Any change that alters Type-1/2/3 sticker geometry or slip paper match — STOP, separate plan.

## Maintenance notes

- Keep `SYNC_CONTRACT.md` versioned; cloud and factory must agree on `day_report` shape.
- Vendor private key offline + rotation plan; public key rollover via updater channel.
- Track `serial/site_id/fp/expiry/resets` per site from day one — this is your 50-site support DB.
- Cost control: one cloud tenant for all sites; per-site hosting is rejected by design.
