-- Fotos und PDFs am Befund-Faden (F7). Ein Anhang ist ein Faden-Eintrag der Art `anhang`
-- (unveränderlich wie alle Einträge; der Verfasser kann ihn zurückziehen, dann wird die Datei nicht
-- mehr ausgeliefert). Der veränderliche Verarbeitungsstand (Bilder prüft der Jobläufer isoliert)
-- steht in `anhaenge`. Neuaufbau von befund_eintraege nur, weil die CHECK-Bedingung `art` erweitert
-- wird (Muster 0013): Trigger weg, neue Tabelle, Daten kopieren, alte weg, umbenennen, Indizes und
-- Trigger neu (Zurückziehen jetzt auch für `anhang`).
DROP TRIGGER befund_eintraege_kein_update;
DROP TRIGGER befund_eintraege_kein_delete;
DROP TRIGGER befund_eintraege_kein_ersetzen;
CREATE TABLE befund_eintraege_neu (id INTEGER PRIMARY KEY AUTOINCREMENT, schluessel TEXT NOT NULL UNIQUE CHECK (length(schluessel) = 16), project_id TEXT NOT NULL, poi_id TEXT NOT NULL, art TEXT NOT NULL CHECK (art IN ('kommentar', 'status', 'zustaendig', 'frist', 'link', 'markierung', 'vorschlag', 'anhang')), text TEXT, wert TEXT NOT NULL, intern INTEGER NOT NULL DEFAULT 0 CHECK (intern IN (0, 1) AND (intern = 0 OR art IN ('kommentar', 'link'))), erstellt_von INTEGER REFERENCES users(id), erstellt_am INTEGER NOT NULL, kanal TEXT NOT NULL CHECK (kanal IN ('web', 'api', 'cli')), token_name TEXT, zurueckgezogen_am INTEGER, herkunft TEXT NOT NULL DEFAULT 'mensch' CHECK (herkunft IN ('mensch', 'ki')), bezug TEXT CHECK (bezug IS NULL OR length(bezug) = 16), FOREIGN KEY (project_id, poi_id) REFERENCES pois(project_id, id));
INSERT INTO befund_eintraege_neu (id, schluessel, project_id, poi_id, art, text, wert, intern, erstellt_von, erstellt_am, kanal, token_name, zurueckgezogen_am, herkunft, bezug) SELECT id, schluessel, project_id, poi_id, art, text, wert, intern, erstellt_von, erstellt_am, kanal, token_name, zurueckgezogen_am, herkunft, bezug FROM befund_eintraege;
DROP TABLE befund_eintraege;
ALTER TABLE befund_eintraege_neu RENAME TO befund_eintraege;
CREATE INDEX befund_eintraege_poi ON befund_eintraege(project_id, poi_id, id);
CREATE INDEX befund_eintraege_bezug ON befund_eintraege(project_id, poi_id, bezug);
CREATE TRIGGER befund_eintraege_kein_update BEFORE UPDATE ON befund_eintraege WHEN NOT (OLD.zurueckgezogen_am IS NULL AND NEW.zurueckgezogen_am IS NOT NULL AND NEW.text IS NULL AND OLD.art IN ('kommentar', 'link', 'anhang') AND NEW.id IS OLD.id AND NEW.schluessel IS OLD.schluessel AND NEW.project_id IS OLD.project_id AND NEW.poi_id IS OLD.poi_id AND NEW.art IS OLD.art AND NEW.wert IS OLD.wert AND NEW.intern IS OLD.intern AND NEW.erstellt_von IS OLD.erstellt_von AND NEW.erstellt_am IS OLD.erstellt_am AND NEW.kanal IS OLD.kanal AND NEW.token_name IS OLD.token_name AND NEW.herkunft IS OLD.herkunft AND NEW.bezug IS OLD.bezug) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_delete BEFORE DELETE ON befund_eintraege BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_ersetzen BEFORE INSERT ON befund_eintraege WHEN EXISTS (SELECT 1 FROM befund_eintraege WHERE id=NEW.id OR schluessel=NEW.schluessel) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
-- status: pruefung (Bild wartet auf den Jobläufer) → bereit | abgelehnt; entfernt = Datei nach
-- Zurückziehen gelöscht. sha256/groesse: ausgelieferte Datei (Bild neu kodiert, PDF unverändert).
-- `name`: Anzeigename aus dem Upload (bereinigt, nie als Pfad verwendet).
CREATE TABLE anhaenge (
  id TEXT PRIMARY KEY CHECK (length(id) = 32),
  schluessel TEXT NOT NULL UNIQUE,
  project_id TEXT NOT NULL,
  poi_id TEXT NOT NULL,
  typ TEXT NOT NULL CHECK (typ IN ('bild', 'pdf')),
  status TEXT NOT NULL CHECK (status IN ('pruefung', 'bereit', 'abgelehnt', 'entfernt')),
  auftrag TEXT,
  name TEXT,
  groesse INTEGER NOT NULL CHECK (groesse >= 0),
  sha256 TEXT CHECK (sha256 IS NULL OR length(sha256) = 64),
  breite INTEGER,
  hoehe INTEGER,
  aufnahme TEXT,
  fehler TEXT,
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  geaendert_am INTEGER NOT NULL
);
CREATE INDEX anhaenge_projekt ON anhaenge(project_id, status);
CREATE INDEX anhaenge_poi ON anhaenge(project_id, poi_id);
