-- Accounts, sessions, push keys, licence log, reporter registry.
-- SYNC_CONTRACT v2. Applied with: wrangler d1 migrations apply simpld-dashboard
-- (see dashboard/DEPLOY.md). The Worker also runs this best-effort on the
-- bootstrap path (admin/seed), so ordering mistakes fail safe, not silent.

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  pass_hash TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('vendor_admin','client')),
  site_id TEXT,
  disabled INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  site_id TEXT NOT NULL,
  key_hash TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  revoked_at TEXT
);
CREATE TABLE IF NOT EXISTS licences (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL,
  serial TEXT NOT NULL,
  edition TEXT NOT NULL,
  packager_fp TEXT NOT NULL,
  max_stations INTEGER NOT NULL,
  issued TEXT NOT NULL,
  expires TEXT NOT NULL,
  max_version TEXT NOT NULL,
  issued_by TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reporter_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  site_id TEXT NOT NULL,
  station_id TEXT NOT NULL,
  packager_fp TEXT NOT NULL,
  seen_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_reporter_site ON reporter_events(site_id, seen_at);
CREATE TABLE IF NOT EXISTS sites (
  site_id TEXT PRIMARY KEY,
  last_push_at TEXT,
  last_reporter_fp TEXT,
  frozen INTEGER NOT NULL DEFAULT 0
);
