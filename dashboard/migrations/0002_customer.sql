-- 0002: record the customer name on every issued licence (needed for the
-- site picker labels and one-click renewals). Idempotent.
ALTER TABLE licences ADD COLUMN customer TEXT NOT NULL DEFAULT '';
