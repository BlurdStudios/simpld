# Licence-backed short-lived push token — design

## Intent and why

Factory sync currently needs a separate vendor-provisioned API key even though
every installed copy already carries an Ed25519-signed licence that proves its
`site_id`, serial, edition, expiry and machine binding. The operator cannot enter
that key anywhere in v1.8.0, and there is no installer/heartbeat path that
provisions it, so a fresh machine can never sync. The owner chose to keep sync
zero-config and use the licence as the credential.

This adds an OAuth2-client-credentials-style exchange: the factory sends its
signed licence to the dashboard, the dashboard verifies the signature and
issues a short-lived push token, and all factory POSTs use that token. No key is
entered or stored on the PC. API keys remain accepted so existing sites do not
break.

## Goals

- A licensed PC can push with no key, env var or UI input.
- The dashboard verifies the licence signature with the vendor public key and
  binds the token to that licence's `site_id` and serial.
- Tokens expire (1 hour) and can be invalidated by revoking the licence serial.
- Existing API-key-authenticated factories keep working unchanged.
- No secrets are logged or shown in the UI; token cache is local app data only.

## Non-goals

- Full OAuth authorization-code/device flow, user consent or refresh tokens.
- A new admin UI for licence revocation in this change (D1 tooling is enough for
  the first cut; the table and helper are in place).
- Changing what the licence entitles the PC to do locally.

## Anti-goals

- Sending the licence as the Bearer token itself (copyable, no rotation).
- Requiring any box in Settings → Sync.
- Breaking the existing push payload contract or viewer/portal sessions.

## Constraints

- Factory side is stdlib-only (`urllib`, `json`); no new dependency.
- Dashboard is TypeScript on Cloudflare Workers with Web Crypto, D1, KV/R2.
- Licence signature algorithm and payload keys must stay byte-identical to
  `factory/license.py` and `dashboard/src/lib/license.ts`.
- The vendor public key currently ships in `factory/license.py:PUBLIC_KEY_B64`;
  the dashboard must get the same value (env override + matching default).
- Versioning (AGENTS.md): additive backward-compatible functionality is MINOR.
  This ships as `1.9.0`, not `1.8.1`. Never retag `v1.8.0`.

## Approaches considered

1. **Raw licence as Bearer** — smallest code change, but a copied `license.lic`
   can push as the site until expiry and there is no practical revocation.
   Rejected.
2. **Licence → short-lived token (chosen)** — licence proves identity once,
   server issues an expiring token bound to serial/site. Zero-config, revocable,
   backward compatible with API keys.
3. **Keep API key** — already implemented; rejected because a fresh machine has
   no supported way to receive the key.

## Design

### Dashboard

- `dashboard/src/lib/license.ts`: add a default `LICENSE_PUBKEY_B64` constant
  equal to the factory's current public key; env `SIMPLD_LICENSE_PUBKEY` wins.
- `dashboard/src/lib/push-token.ts` (new): `TOKEN_TTL_SECONDS = 3600`, token
  format `pt1.<base64url(payload)>.<base64url(HMAC-SHA256(payload, secret))>`,
  payload `{v:1, site_id, serial, iat, exp}`. Export `issuePushToken()` and
  `verifyPushToken()`; both pure and secret-driven, no network or DB.
- `dashboard/src/routes/api/push-token.ts` (new): `POST /api/auth/push-token`
  with `{site_id, licence}`. Verify full licence shape/signature via
  `verifyLicence(lic, {pubkeyB64})`, reject `licenceStatus().state === "blocked"`,
  require `licence.site_id === site_id`, require the serial exists in D1
  `licences` for that site and is absent from `revoked_licences`. Return
  `{ok:true, token, expires_at}`. Never echo the secret or token in errors.
- `dashboard/src/lib/db.ts`: add `revoked_licences(serial TEXT PRIMARY KEY,
  site_id TEXT NOT NULL, revoked_at TEXT NOT NULL, reason TEXT)` to `SCHEMA_SQL`,
  plus `AccountsStore` methods `licenceForSite(serial, siteId)` and
  `isLicenceRevoked(serial)` with D1 and in-memory implementations.
- `dashboard/src/lib/portal.ts`: `authorizeFactory(req, expectedSite, deps)`
  accepts a valid `pt1.` token for the expected site (checking revocation) or
  falls back to the existing `authorizeAsync` API-key path. Use it in
  `push-day.ts`, `push-orders.ts`, `licence-heartbeat.ts` and `trial-request`
  handling so all factory calls work without a key.
- Worker passes `tokenSecret` (env `PUSH_TOKEN_SECRET`, dev fallback
  `dev-push-token-secret`) to those handlers. Document the secret in
  `dashboard/DEPLOY.md` and `dashboard/README.md`.

### Factory

- `factory/sync.py`:
  - `_cfg()` still returns `(url, site, key)`; `key` becomes optional fallback.
  - `configured()` requires only URL + site (site comes from the licence).
  - `setup_hint()` now says sync needs a valid licence on this PC, not a vendor
    key; keep the "nothing to enter in the app" wording.
  - `_licence()` returns the installed signed licence dict only when its local
    `license_status()` state is `ok`, `expiring` or `grace`.
  - `push_token(force=False)` reads `<app_data>/sync_token.json` (token,
    `expires_at`, `site_id`, `serial`), otherwise POSTs the licence to
    `/api/auth/push-token` and caches the result. Never logs the token.
  - `_auth_headers()` prefers a legacy configured API key, otherwise uses
    `push_token()`. `_post()`/`_post_json()` use it; on HTTP 401 with a token,
    force one refresh and retry once, then queue/report failure as today.
  - Heartbeat and trial request use the same auth path so offers still arrive.
- `factory/ui/pages_misc.py` and `factory/ui/settings_dialog.py`: use the new
  `setup_hint()`; no key-related wording remains.

### Security notes

- Revocation is checked when a token is issued; a 1-hour TTL bounds the window
  if a serial is revoked afterwards.
- The licence remains machine-bound locally (`packager_fp`), so a copied
  `license.lic` cannot be loaded by the factory app on another board; direct API
  abuse is bounded by TTL plus serial revocation.
- No token, licence signature or API key is written to logs or dialogs.

## Legacy users and compatibility

- Portal model is layman-simple: ONE password per account, set and shared by
  the vendor admin (`POST /api/admin/users/:id/password`, shown once with a
  copy button in the UI; licence issuance auto-creates the client account and
  returns the password in the same response). No one-time codes, no setup
  flow — the earlier OTP/serial setup endpoints were removed.
- Accounts created during the brief OTP era (`setup_done = 0`, unknown random
  placeholder password) just get a password from the admin via the same
  Set-password action; the D1 columns from that era stay in the schema but
  are no longer read or written.
- Factory installs from v1.7.x that already saved `sync.key` (or set
  `SIMPLD_SYNC_KEY`) keep using that key: `_auth_headers()` prefers it and
  `authorizeFactory()` still accepts API keys. Nothing is removed or reset.
- Existing dashboard API keys, sessions and viewer access are untouched.
- The licence on an old PC is signed with the current vendor key (rotation
  happened before any licence was issued), so it also works for token exchange.
- Portal passwords are unrelated to factory sync; the token exchange uses
  the signed licence already installed on the PC, not the portal password.

## Tests

- Dashboard: token issue/verify, expiry, wrong site, bad signature, revoked
  serial, API-key fallback, and one end-to-end push using only a licence.
- Factory: token cache reuse, refresh on 401, licence-missing/blocked refusal,
  legacy key path unchanged, updated setup hint, heartbeat auth.
- Full Python suite and dashboard `npm test` must pass.

## What is left to the builder

Exact function signatures are listed above; bodies, test fixture wiring and
error message wording follow existing module patterns. Any decision not pinned
here (token cache filename, dev secret name) is the builder's call as long as it
matches the names in this document.
