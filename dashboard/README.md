# simpld-dashboard

Vendor-hosted **viewer + self-serve portal** for Simpld sites (Plan 009 Phase 3
slice, portal in v2). Separate app — not part of `factory/`. Outbound-push only:
the factory POSTs cached payloads here; this app never calls into the factory.
No inbound factory ports, no live DB queries from the web.

Stack: TanStack Router (file-based routes in `src/routes/`) + React + Vite,
shadcn-style UI (`src/components/ui/`), Cloudflare Workers (`wrangler.toml`)
with D1 (accounts/keys/licences/reporters), R2 (installers), KV (snapshots).

## Run

```bash
cd dashboard
npm install
npm run dev        # SPA on http://localhost:5173 (mock data fallback, no backend needed)
npm run dev:worker # full stack incl. API (needs wrangler login + D1/KV/R2 ids)
npm run build      # tsc + vite build -> ./dist
npm run smoke      # node --test suite: login, RBAC, issuance, keys, releases (11 checks)
npm run deploy     # wrangler deploy (set D1/KV/R2 ids + secrets first)
```

## Configure

1. `wrangler.toml`: fill `PUSHES.id` / `preview_id`, `DB.database_id`, R2 bucket.
   Full runbook (migrations, secrets, first admin, deploy): **`DEPLOY.md`**.
2. Secrets (never commit):
   ```bash
   wrangler secret put LICENSE_PRIVKEY_B64  # base64 raw 32B Ed25519 vendor key
   wrangler secret put ADMIN_SEED_TOKEN     # long random string, one-time bootstrap
   wrangler secret put SITE_KEYS            # legacy fallback map (prefer api_keys table)
   ```
   Without bindings, dev falls back to in-memory + `dev-key` → `demo-site`.
   Copy `.dev.vars.example` → `.dev.vars` for local `wrangler dev` secrets.
3. Seed the first vendor admin (one time):
   ```bash
   curl -X POST http://127.0.0.1:8787/api/admin/seed \
     -H 'Content-Type: application/json' \
     -d '{"token":"<ADMIN_SEED_TOKEN>","email":"admin@example.com","password":"<long-random>"}'
   ```
   Further accounts are vendor-provisioned in the Admin panel (no self-signup).
4. Factory side (`factory/sync.py`, Phase 3 step 1): POST `day_report` to
   `/api/push-day` and orders/stock to `/api/push-orders` with the site key
   header. Optionally attach `reporter`/`stations` (SYNC_CONTRACT.md v2).

## Routes

- `/` — site picker / landing.
- `/s/<site_id>` — read-only viewer: yesterday + today production, orders
  loaded/target, stock counts. Key-scoped (key must match the site, else 403)
  or portal session (client: own site; admin: any).
- `/login` — vendor-provisioned account login (no self-registration).
- `/licence` — issue a signed `.lic` for a Machine ID; download the file or copy
  the key text (same bytes — paste or import on the packager).
- `/download` — installer list with SHA-256 (login required, R2-backed).
- `/admin` — vendor_admin only: users, API keys (show-once), sites + freeze,
  licences log, release upload.
- `POST /api/push-day`, `POST /api/push-orders` — push endpoints (handlers in
  `src/routes/api/`, served by `src/worker.ts`). Keys resolve from the D1
  `api_keys` table first, `SITE_KEYS` env map fallback.

## Contract

See `SYNC_CONTRACT.md` (mirrored at repo root). v2: accounts, activate/issue,
releases, reporter extension. Licence format is frozen and mirrors
`factory/license.py` exactly (sorted-keys compact JSON, base64-std Ed25519).
