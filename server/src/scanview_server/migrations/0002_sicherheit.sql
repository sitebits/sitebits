-- Security-Audits 27.09.2026.
-- WB-N1/BB-I5: Aufräumen und Obergrenze offener Challenges ohne vollen Tabellenscan.
CREATE INDEX challenges_exp ON challenges(expires_at);
-- WB-N2/BB-I6: Zeitpunkt der letzten Änderung je Drosselzeile, damit das Aufräumen auch Zeilen
-- mit offenen Fehlversuchen (1–4) nach Ablauf entfernen kann. Bestehende Zeilen altern ab jetzt.
ALTER TABLE throttle ADD COLUMN geaendert_am INTEGER NOT NULL DEFAULT 0;
UPDATE throttle SET geaendert_am = CAST(strftime('%s', 'now') AS INTEGER);
