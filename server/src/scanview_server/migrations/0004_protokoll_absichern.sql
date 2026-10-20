-- Nachzug für Datenbanken, die 0003 vor 944e246 ausgeführt haben (ohne ereignisse_kein_ersetzen):
-- migrate() überspringt bereits angewandte Versionen, der Trigger käme dort sonst nie an.
-- Auf frischen Datenbanken und L (Schema 2) wirkungslos (IF NOT EXISTS). Trigger einzeilig (_statements).
CREATE TRIGGER IF NOT EXISTS ereignisse_kein_ersetzen BEFORE INSERT ON ereignisse WHEN EXISTS (SELECT 1 FROM ereignisse WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT, 'Protokoll ist unveränderlich'); END;
-- verwaltung.ist_anonymisiert / anonymisierte_ids suchen bei jeder Einladung/Rechteänderung nach art + objekt_id.
CREATE INDEX IF NOT EXISTS ereignisse_art_objekt ON ereignisse(art, objekt_id);
