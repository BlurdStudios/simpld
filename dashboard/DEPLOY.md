# DEPLOY.md — dashboard (vendor portal) deploy runbook

Vendor portal: TanStack SPA + Cloudflare Worker, D1 (accounts/keys/licences/reporters),
R2 (installers), KV (push snapshots). The factory is untouched by deploys here.

## 0. Prereqs

```bash
cd dashboard
npm install
npx wrangler login
```

## 1. D1 — create + migrate

```bash
# one time: create the database, paste the id into wrangler.toml [d1_databases]
npx wrangler d1 create simpld-dashboard

# every deploy with schema changes (idempotent: CREATE TABLE IF NOT EXISTS)
npx wrangler d1 migrations apply simpld-dashboard
# local dev loop instead: npx wrangler d1 migrations apply simpld-dashboard --local
```

Migration source: `dashboard/migrations/0001_init.sql`
(tables: users, sessions, api_keys, licences, reporter_events, sites).
The Worker also runs the schema best-effort on the seed path, but the migration
is authoritative — run it.

## 2. R2 — create bucket (+ optional manual publish)

```bash
# one time (name must match wrangler.toml [[r2_buckets]]: simpld-releases)
npx wrangler r2 bucket create simpld-releases
```

Prefer the Admin panel (Releases tab) for uploads — it hashes + updates the
manifest. Without a backend (or for recovery), publish by hand:

```bash
sha256sum Simpld-Setup-1.0.1.exe
npx wrangler r2 object put simpld-releases/releases/Simpld-Setup-1.0.1.exe \
  --file Simpld-Setup-1.0.1.exe
# then write releases/latest.json + releases/index.json in the UPDATES.md shape:
# {"version":"1.0.1","file":"Simpld-Setup-1.0.1.exe","sha256":"<hex>","notes":"..."}
printf '%s' '{"version":"1.0.1","file":"Simpld-Setup-1.0.1.exe","sha256":"<hex>","notes":"..."}' \
  | npx wrangler r2 object put simpld-releases/releases/latest.json
printf '%s' '[{"version":"1.0.1","file":"Simpld-Setup-1.0.1.exe","sha256":"<hex>","notes":"..."}]' \
  | npx wrangler r2 object put simpld-releases/releases/index.json
```

## 3. KV (push snapshots) + vars

```bash
npx wrangler kv namespace create PUSHES   # paste ids into wrangler.toml
```

## 4. Secrets (NEVER commit — not in wrangler.toml, not in `.dev.vars`)

```bash
# Ed25519 vendor private key, base64 of the RAW 32-byte seed (NOT the .lic).
# Generate offline ONCE and store offline; this secret is the only copy online:
#   python3 tools/issue_license.py --gen-key   # run in the private repo/offline
npx wrangler secret put LICENSE_PRIVKEY_B64

# Long random string gating the one-time first-admin bootstrap:
npx wrangler secret put ADMIN_SEED_TOKEN

# Legacy push-key map (fallback only; prefer the api_keys table):
#   npx wrangler secret put SITE_KEYS   # JSON: {"<site_id>": "<api_key>", ...}
```

Local dev: `cp .dev.vars.example .dev.vars` and fill it in
(`wrangler dev` reads `.dev.vars`; it is gitignored). The example contains a
zeroed placeholder key — signing in local dev works, licences it makes verify
only against a matching throwaway pubkey.

## 5. Seed the first vendor admin (one time, then rotate the token)

```bash
curl -X POST https://<worker>/api/admin/seed \
  -H 'Content-Type: application/json' \
  -d '{"token":"<ADMIN_SEED_TOKEN>","email":"admin@vendor.example","password":"<long-random>"}'
# -> 200 + session cookie; the endpoint 403s forever after (users table non-empty).
# Further accounts (vendor_admin/client) are created in the Admin panel (Users tab).
```

Then rotate: `npx wrangler secret put ADMIN_SEED_TOKEN` (new random value —
the old one is useless post-seed, but rotate anyway).

Verify: log in at `https://<worker>/login` and open `/admin`.

## 6. Deploy

```bash
npm run build      # tsc --noEmit + vite build -> ./dist (assets served by Worker)
npm run smoke      # 11 checks: login, RBAC, issuance round-trip, keys, releases
npx wrangler deploy
```

## 7. Operate

- **New site onboarding**: Admin → Users → create client (site_id) → Keys →
  create key for that user/site (confirm checked; copy the show-once key) →
  put it in the factory config + hand the client their login.
- **Rotation**: create a new key for the user (old ones auto-revoke at creation),
  update the factory, done. Old pushes start `401` immediately.
- **Freeze a site** (non-payment / incident): Admin → Sites → Freeze = revokes ALL
  keys for the site (pushes + viewer `401`) and flags it frozen. Cached data stays
  frozen until retention pruning. Unfreeze clears the flag; issue fresh keys to
  resume pushes.
- **Licence for a site**: Licence page → enter Machine ID (packager_fp from the
  packager About screen) → Issue → Download `license.lic` or copy the key text.
  The factory accepts the JSON pasted or imported as a file (same bytes).
- **Publish an installer**: Admin → Releases → upload `Simpld-Setup-X.Y.Z.exe`
  (+ notes). Downgrades are refused unless forced. Clients download from
  `/download` (login required); the updater SHA-256-verifies before install.
- **No D1/R2 bound** (dev without `wrangler dev` bindings): accounts fall back to
  process memory, releases endpoints return `501` — production must bind both.
