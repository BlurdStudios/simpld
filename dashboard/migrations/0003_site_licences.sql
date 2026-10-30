CREATE TABLE IF NOT EXISTS site_licences (
  site_id TEXT PRIMARY KEY,
  edition TEXT NOT NULL DEFAULT '',
  expires TEXT NOT NULL DEFAULT '',
  days_left INTEGER,
  state TEXT NOT NULL DEFAULT '',
  app_version TEXT NOT NULL DEFAULT '',
  packager_fp TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL DEFAULT ''
);
