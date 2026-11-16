CREATE TABLE IF NOT EXISTS licence_offers (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL,
  serial TEXT NOT NULL,
  packager_fp TEXT NOT NULL,
  licence_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  acked_at TEXT
);
CREATE INDEX IF NOT EXISTS licence_offers_site ON licence_offers(site_id, acked_at);
CREATE TABLE IF NOT EXISTS trial_requests (
  id TEXT PRIMARY KEY,
  site_id TEXT NOT NULL,
  days INTEGER NOT NULL,
  reason TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  requested_at TEXT NOT NULL,
  decided_at TEXT,
  decided_by TEXT
);
CREATE INDEX IF NOT EXISTS trial_requests_status ON trial_requests(status, requested_at);
