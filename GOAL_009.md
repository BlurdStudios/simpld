# GOAL 009 — Licensing + Daily report + Dashboard

Source: `plans/009-licensing-daily-report-dashboard.md`. Execute Phase 1 → 2 → 3 in order. Offline-first, fail-closed.

## Workers (opencode-go models)
- Phase 1 day_report backend+LAN+UI: `opencode-go/deepseek-v4-pro` (bg ses_e892dee48ffe1Zb3VwBlKQoC0h)
- Dashboard TanStack Start + shadcn + wrangler: `opencode-go/muse-spark-1.3-contributor` (bg ses_e892dc634ffe57FEaP2ZRfp1Z9)
- Phase 2 licensing: main thread after Phase 1 lands (touches same lan_server.py)
- Phase 3 factory sync.py: main thread after Phase 1 lands (needs day_report shape)

## Phase 1 — Daily report (do first)
Scope: services.day_report, DA facade, lan_client+lan_server op, slip_pdf render, SummaryPage dialog, tests/test_day_report.py
Verify: `SIMPLD_DB=/tmp/p009.db QT_QPA_PLATFORM=offscreen SIMPLD_ALLOW_MOCK=1 python -m pytest tests/test_day_report.py -q -p no:cacheprovider`
Status: DONE (subagent, 7/7; regression history 2 + data_access 9 + slip/ui 16 green)
- dashboard scaffold DONE (see dashboard/ + SYNC_CONTRACT.md, 8/8 smoke pass)

## Phase 2 — Licensing tiers (do second)
Scope: factory/license.py, db.license_clients migration, run.py + lan_server hooks, About UI + import, tools/issue_license.py, tests/test_licensing.py, requirements.txt += cryptography (Ed25519)
Format frozen in plan. simple=no LAN, pro=2-3, premium=5. Fingerprint board UUID+MAC. Stations=1 packager+N dispatchers. +1 overdraft 72h, 30d grace write-allow then write-block.
Status: DONE (main thread)
- factory/license.py + station registry + tests/test_licensing.py 7/7 green
- factory/db.py license_clients migration + SCHEMA
- tools/issue_license.py, requirements.txt += cryptography==46.0.3
- run.py startup banner + About licence card (splash.py)
- lan_server startup + per-login hooks, simple→refuse LAN,
  license_invalid dispatcher path, write-block gate, LanClient station creds
  (regression test_lan_transport 11/11 green)
- KEY MATERIAL DONE 2026-10-26: real Ed25519 keypair generated;
  PUBLIC_KEY_B64 embedded in factory/license.py; privkey in
  ~/.simpld-vendor/vendor.key (0600, outside repo — move to offline USB);
  cryptography==46.0.3 win_amd64 cp312 wheel confirmed
  (cryptography-46.0.3-cp311-abi3-win_amd64.whl); pilot licence SIMPLD-0017
  issued + verified ok, tamper fails closed (17/17 green)
- ACTIVATE SCREEN DONE 2026-10-26 (self-serve loop, factory side):
  factory/license.py KEY_PREFIX/SIMPLD1. encode_key_string/decode_key_string
  (key-string == .lic bytes, same verify path; accepts key/JSON-paste/file);
  factory/ui/activate.py ActivateDialog (Machine ID + copy, Paste key /
  Import file tabs) + ensure_activated() gate; run.py locks local mode on
  none/invalid/blocked before login (LAN dispatchers server-enforced only);
  tests/test_activate.py 5/5 (22/22 with licensing+day_report+sync)

## Phase 5 — Dashboard UI overhaul (main thread, 2026-10-26)
Status: DONE. User feedback: no dashboard look, pages visible without auth,
no real shadcn. Fixed:
- Tailwind v4 (@tailwindcss/vite) + shadcn theme tokens; real shadcn set:
  button (variants), card (+header/desc/footer), table, badge, input,
  label, separator, skeleton. lucide REMOVED (repo standard is Hugeicons);
  @hugeicons/react + @hugeicons/core-free-icons, verified glyphs.
- Auth: AuthProvider (single /api/auth/me fetch) + RequireAuth (redirect
  /login?redirect=) + RequireAdmin (403 page for clients). Nav is
  role-filtered: logged-out sees Overview+Viewer+Login only; clients never
  see Admin; /licence + /download require login.
- Shell: sidebar (brand, nav, user email + role badge, logout) + mobile top
  bar; overview cards per role; viewer stat cards + order progress bars +
  badges + skeletons; login centered card; licence/download/admin restyled.
- `npm run build` green, `npm run smoke` 11/11 green (backend untouched).
- Follow-up 2026-10-26: public site+key picker REMOVED from logged-out landing
  (every client gets a provisioned account; shared ?key= links still work).
  Client Overview links straight to own site via session (viewer sends the
  key header only when a key exists — empty Bearer no longer shadows the
  session). /viewer route + nav item deleted.

## Phase 4 — Self-serve portal + god-mode admin (dashboard worker)
Status: DONE 2026-10-26 (dashboard `muse-spark`, factory loop closed main thread)
- Dashboard: login (D1 users/sessions/api_keys, PBKDF2), vendor_admin vs
  client roles, /admin (users, keys show-once + auto-revoke-old, sites +
  freeze/reporters, licences log, R2 releases upload), /licence (Machine ID
  → signed .lic download + key-string copy), /download (R2 + SHA-256),
  Worker-side Ed25519 issuance via LICENSE_PRIVKEY_B64 secret.
  Push auth migrated D1 api_keys → SITE_KEYS env fallback.
  `npm run smoke` 11/11 independently re-verified (incl. Python↔TS
  canonical-JSON byte parity + live cross-verify).
- Factory: sync.py pushes now carry reporter {station_id, packager_fp}
  (admin Sites view feed); Activate screen already accepts paste + file.
  22/22 factory tests + lan_transport/data_access 20/20 green.
- Deploy runbook: dashboard/DEPLOY.md (D1 migrate, R2 create, secrets,
  seed admin, deploy). Remaining ops: run the runbook on your Cloudflare
  account, set LICENSE_PRIVKEY_B64 to the real vendor key, pilot 1–2 sites.
Factory: factory/sync.py build_push()+push_now(), SummaryPage Sync now + last-sync label, outbound POST only, airplane-mode queued.
Cloud: dashboard/ TanStack Start + shadcn + wrangler.toml (Cloudflare Workers + D1), POST /push-day, POST /push-orders, GET /s/<site_id>, buyer isolation 403, SYNC_CONTRACT.md versioned.
Status: DONE (dashboard subagent scaffold + main-thread factory sync)
- factory/sync.py build_push()+push_now() (queue+log offline), SummaryPage
  Sync now + last-sync label, tests/test_sync.py 3/3 green

## Done criteria
- [x] day_report prints/saves line-items any day, local+LAN identical (7/7)
- [x] 1 licence runs 1-PC + 2-PC, 3rd rejected + overdraft (7/7)
- [x] cloned licence fails closed, expired+30d blocks writes but backup works
- [x] Licensed to in About/title, nothing on sticker/slip paper
- [x] dashboard pushes 2 payloads outbound-only, viewer isolation proven (8/8)
- [x] plans/README rows updated, no file outside Scope modified

## STOP conditions
- internet required for launch/check → STOP
- factory DB/LAN exposed inbound → STOP
- 50 installers / per-PC keys → STOP
- live queries / machine control from web → STOP
- Type-1/2/3 sticker geometry or slip paper match change → STOP
