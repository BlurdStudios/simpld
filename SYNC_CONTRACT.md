# SYNC_CONTRACT v2 — factory → dashboard push contract + vendor portal

> Versioned contract. Cloud (`dashboard/`) and factory (`factory/sync.py`) must agree
> on these shapes. Bump the version and update both sides together.
> Design: **outbound-only**. The factory POSTs cached payloads; the dashboard never
> calls into the factory. No inbound ports, no live DB queries from the web.

## Endpoints (dashboard Worker)

| Method | Path | Body | Auth |
|---|---|---|---|
| POST | `/api/push-day` | `{site_id, day_report[, reporter, stations, licence]}` | site key |
| POST | `/api/push-orders` | `{site_id, orders, stock_counts[, reporter, stations]}` | site key |
| GET | `/api/s/:siteId` | — (viewer data) | site key **or** portal session |
| POST | `/api/auth/login` | `{email, password}` | — (sets session cookie) |
| POST | `/api/auth/logout` | — | session |
| GET | `/api/auth/me` | — | session |
| POST | `/api/admin/seed` | `{token, email, password}` | seed token, first-admin-only |
| GET/POST | `/api/admin/users` | user create/list | vendor_admin session |
| POST | `/api/admin/users/:id` | `{role?, site_id?, disabled?}` | vendor_admin session |
| GET/POST | `/api/admin/keys` | key create/list (show-once) | vendor_admin session |
| POST | `/api/admin/keys/:id/revoke` | — | vendor_admin session |
| GET | `/api/admin/sites` | sites overview | vendor_admin session |
| GET | `/api/admin/sites/:siteId/reporters` | reporter history | vendor_admin session |
| POST | `/api/admin/sites/:siteId/freeze` | revoke all site keys | vendor_admin session |
| POST | `/api/admin/sites/:siteId/unfreeze` | clear frozen flag | vendor_admin session |
| POST | `/api/licenses/issue` | licence fields (below) | session (clients: own site only) |
| GET | `/api/licenses` | issuance log | session (clients: own site only) |
| GET | `/api/releases` | manifest list | session |
| GET | `/api/releases/file/:name` | installer bytes | session |
| POST | `/api/admin/releases` | multipart installer upload | vendor_admin session |

Viewer pages: `GET /s/<site_id>` (SPA shell; fetches `/api/s/:siteId` with the key).
Portal pages: `/login`, `/licence`, `/download`, `/admin` (SPA shells over the
session API above).

## Auth

Push/viewer keys (unchanged from v1, extended in v2):

- Header: `Authorization: Bearer <API_KEY>` **or** `X-Site-Key: <API_KEY>`.
- Each key is bound to exactly one `site_id`. Resolution order: **`api_keys`
  table (D1, unrevoked) first, `SITE_KEYS` env map fallback** (dev default
  `dev-key` → `demo-site` when neither is configured).
- Missing key → `401`; unknown/revoked key → `401`; key for site A used on site B
  (push or viewer) → `403`. Buyer isolation is enforced in the push handlers and
  the viewer loader — never trust the URL alone.
- Key rotation: vendor issues a new key, factory config updated, old key disabled.
  Pushes with the old key start failing `401`; factory logs and keeps queuing with
  the new key. Viewer keys rotate the same way.
- Revoke: disable/delete the key server-side → pushes `401`, viewer `401`.
  Already-cached data stays frozen (no new writes) until retention pruning.
  The factory keeps running offline, unaffected.

Portal accounts (new in v2):

- Tables: `users(id,email,pass_hash,role,site_id,created_at)`,
  `sessions(token,user_id,expires)` (`dashboard/migrations/0001_init.sql`).
- Roles: `vendor_admin` (sees/does everything), `client` (bound to one
  `site_id`; every site-scoped check is `canAccessSite`: admins pass, clients
  must match their own site).
- Passwords: PBKDF2-SHA256 (100k iterations, 16B salt), stored as
  `pbkdf2-sha256$iter$salt$hash` (base64). No self-registration: the first
  vendor_admin is created via `POST /api/admin/seed` (token-gated, succeeds only
  when the users table is empty); further accounts are created by an admin.
- Sessions: 256-bit random token, `HttpOnly; Path=/; SameSite=Lax` cookie
  `simpld_session`, 7-day expiry. Logout deletes the token server-side.
- `GET /api/s/:siteId` also accepts a portal session instead of a key: clients
  may read their own site, admins any site. No key and no session → `401`.

## Payloads

### `day_report` (from `factory/services.py::day_report`)

```json
{
  "day": "2026-10-26",
  "reel_rows": [
    {"roll_code": "RC-001", "roll_label": "L-12", "job_id": "J-7", "net_kg": 24.5}
  ],
  "box_rows": [
    {"carton_no": "C-101", "bundle_no": "B-3", "job_id": "J-7",
     "num_reels": 4, "total_gross_kg": 102.1, "linked_rolls": ["RC-001"]}
  ],
  "totals": {"reel_count": 10, "reel_net_kg": 245.0, "box_count": 3, "box_gross_kg": 310.4},
  "by_job": {"J-7": {"reels": 10, "reel_net_kg": 245.0, "boxes": 3, "box_gross_kg": 310.4}}
}
```

Rules: reel weights and box weights are never merged (same rule as `get_slip`).
`day` is `YYYY-MM-DD`; validation rejects anything else with `400`.

### `order_progress` + `stock_counts`

```json
{
  "site_id": "vasavi-f1",
  "orders": [
    {"order_id": "O-55", "buyer": "Acme", "target_qty": 500, "loaded_qty": 320, "status": "in_progress"}
  ],
  "stock_counts": {"raw_rolls": 120, "packed_cartons": 40, "dispatched_cartons": 310}
}
```

Viewer shows orders as **loaded / target** plus the three stock counts.

### Reporter extension (new in v2, optional, backward compatible)

Pushes may carry who sent them; validators ignore unknown fields, so old factory
builds keep passing and the Worker persists what it can:

```json
{
  "site_id": "vasavi-f1",
  "day_report": { "...": "..." },
  "reporter": {"station_id": "<dispatcher/station uuid>", "packager_fp": "<32-char board fp>"},
  "stations": [{"station_id": "<uuid>", "packager_fp": "<fp>"}],
  "licence": {"edition": "trial", "expires": "2026-11-03", "days_left": 4, "state": "expiring", "app_version": "1.3.0", "packager_fp": "<32-char board fp>"}
}
```

- Missing/empty `reporter`/`stations`/`licence` (or malformed entries) are ignored, never `400`.
- `licence` upserts the per-site snapshot behind Admin → Licences → Live tracking (edition/countdown/state/version).
- Persisted per site (D1 `reporter_events` + `sites.last_reporter_fp`); the admin
  Sites view lists last push, fingerprints seen, and per-site reporter history.
  Without the D1 binding the log is process-local memory (dev only).

### Licence issuance (new in v2)

Format is **frozen** and mirrors `factory/license.py` exactly:

```json
{
  "serial": "SIMPLD-20261026-001",
  "customer": "Vasavi Foods",
  "site_id": "vasavi-f1",
  "edition": "pro",
  "packager_fp": "<32-char board fp>",
  "max_stations": 2,
  "features": ["lan", "history", "summary"],
  "issued": "2026-10-26",
  "expires": "2027-10-26",
  "max_version": "2.x",
  "sig": "<base64-std Ed25519 over canonical JSON of all other keys>"
}
```

Canonical bytes: `json.dumps(body-without-sig, sort_keys=True,
separators=(",", ":"))` utf-8 (ensure_ascii). Signed with the vendor private key
(`LICENSE_PRIVKEY_B64` secret); the public key ships in the factory (fails closed).

- `POST /api/licenses/issue {site_id, customer, packager_fp, edition?,
  max_stations?, features?, issued?, expires, max_version?, serial?}` → `201`
  with the `.lic` JSON. Server defaults: `serial SIMPLD-YYYYMMDD-001`,
  edition features, `issued` today (UTC), `max_version 2.x`; `simple` clamps
  `max_stations` to 1. Every issuance is logged (`licences` table).
- Transport: the response bytes ARE the key — paste the JSON into the packager
  Activate screen or save as `license.lic` and import the file (documented on
  the Licence page). Both carry identical bytes; the factory accepts either.

### Releases (new in v2)

Manifest reuses the `UPDATES.md` `latest.json` shape:

```json
{"version": "1.0.1", "file": "Simpld-Setup-1.0.1.exe",
 "sha256": "<64 lowercase hex>", "notes": "one-liner"}
```

- R2 layout: `releases/<file>` installer bytes, `releases/latest.json` current
  manifest, `releases/index.json` all versions newest-first.
- Admin uploads via `POST /api/admin/releases` (multipart `file` +
  optional `notes`/`force`): filename must match `Simpld-Setup-X.Y.Z.exe`,
  SHA-256 is computed server-side, downgrades are refused (`409`) unless
  `force=1`.
- Clients list/download via `/download` (session auth). Without the R2 binding
  the endpoints return `501` — publish with `wrangler r2 object put` instead
  (DEPLOY.md).

## Push behavior (factory side)

- Trigger: on internet-available (reuse `updater.py` trigger point) + manual
  **Sync now** after Evening backup. Timeout + key from env/config; tokens redacted
  on error logging.
- Airplane mode: push fails → queue payload locally, log only, retry next window.
  The floor never blocks on sync; sync disabled = factory works normally.
- Payload scope per push: `day_report(today)` (+ yesterday on viewer from cache)
  + full `orders`/`stock_counts` snapshot. `reporter`/`stations` are attached by
  `factory/sync.py` when available (packager_fp = `license.machine_fingerprint()`,
  station_id = `license.station_id()`).

## Retention

- Detail rows (per-day reports, per-order snapshots): **2 years**.
- Older than 2 years: roll up to per-month summaries, prune detail
  (D1: `DELETE FROM day_reports WHERE day < date('now','-2 years')` keeping summaries).
- Revoked sites: frozen viewer, pruned on the same schedule unless the contract
  requires earlier deletion.

## Error codes

| Code | Meaning |
|---|---|
| 400 | bad JSON / shape mismatch (see validator error) |
| 401 | missing / invalid / revoked key, or portal login required (bad credentials, expired session) |
| 403 | key valid but bound to a different site; client using another site's portal data; non-admin on admin endpoints; seed after first admin |
| 404 | viewer: no push received for this site yet |
| 409 | release upload would downgrade (resend with force=1) |
| 501 | endpoint needs an unbound backend (D1/R2) or unconfigured secret (LICENSE_PRIVKEY_B64) |

## Version history

- v2 (2026-10-26): portal accounts (login/sessions/RBAC), one-time admin seed,
  D1 `api_keys` table as primary push-key source (env map fallback), licence
  issuance endpoint + log, key-string transport, R2 releases, reporter/stations
  push extension, admin Sites/Licences/Releases views.
- v1 (2026-10-26): initial slice — `push-day`, `push-orders`, buyer-scoped viewer.
