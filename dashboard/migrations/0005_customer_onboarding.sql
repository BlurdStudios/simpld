-- 0005: customer onboarding (licence issue creates the client account).
-- One-time setup code (hash only) + expiry + pending flag on users, and a
-- partial unique index so two clients can never share a site_id.
-- ALTER TABLE ... ADD COLUMN cannot carry a UNIQUE constraint in SQLite/D1,
-- hence the separate index. Idempotent.
ALTER TABLE users ADD COLUMN setup_code_hash TEXT;
ALTER TABLE users ADD COLUMN setup_expires TEXT;
ALTER TABLE users ADD COLUMN setup_done INTEGER NOT NULL DEFAULT 1;
CREATE UNIQUE INDEX IF NOT EXISTS users_client_site_unique ON users(site_id) WHERE role = 'client';
