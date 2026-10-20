-- Auch `INSERT OR REPLACE` über den eindeutigen Schlüssel (ohne id) darf keine vorhandene Zeile
-- ersetzen und damit `zeit` umschreiben: ein gleicher Schlüssel im selben Fenster wird still
-- übergangen (IGNORE, kein Abbruch — `erfassen` schreibt mit INSERT OR IGNORE und der
-- Erfassungspfad ist fail-closed). Trigger einzeilig (_statements).
CREATE TRIGGER lesezugriffe_kein_ersetzen_schluessel BEFORE INSERT ON lesezugriffe WHEN EXISTS (SELECT 1 FROM lesezugriffe WHERE nutzer_id=NEW.nutzer_id AND project_id=NEW.project_id AND art=NEW.art AND objekt=NEW.objekt AND kanal=NEW.kanal AND token_name=NEW.token_name AND fenster=NEW.fenster) BEGIN SELECT RAISE(IGNORE); END;
