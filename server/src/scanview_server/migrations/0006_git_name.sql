-- Klarname in der Git-Historie nur mit Einwilligung des Kontos (ADR 0011), Vorgabe aus
-- (jede Person schaltet selbst über /api/v1/konto ein; gilt nur für künftige Commits).
ALTER TABLE users ADD COLUMN git_name_zeigen INTEGER NOT NULL DEFAULT 0;
