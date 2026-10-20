-- Vorschlag-Zustand für KI-Agenten: Token-Merkmal „für einen KI-Agenten“,
-- Herkunft und Prüfstand an POIs/Messungen, Eintragsart `vorschlag` im Befund-Faden.
-- befund_eintraege ändert den CHECK von `art` → Neuaufbau (wie project_roles in 0010). Die
-- Trigger fallen zuerst (sonst sperrte kein_delete das implizite Leeren beim DROP), danach
-- entstehen sie neu mit den zusätzlichen Spalten im Gleichheitsvergleich. Die ids bleiben,
-- AUTOINCREMENT übernimmt den Höchststand über sqlite_sequence. `bezug` ist der Schlüssel des
-- Vorschlags, über den ein Eintrag entscheidet. Nur vorwärts; Anweisungen einzeilig.
ALTER TABLE api_tokens ADD COLUMN agent INTEGER NOT NULL DEFAULT 0 CHECK (agent IN (0, 1));
ALTER TABLE pois ADD COLUMN herkunft TEXT NOT NULL DEFAULT 'mensch' CHECK (herkunft IN ('mensch', 'ki'));
ALTER TABLE pois ADD COLUMN pruefstand TEXT NOT NULL DEFAULT 'gueltig' CHECK (pruefstand IN ('gueltig', 'vorschlag', 'verworfen'));
ALTER TABLE pois ADD COLUMN loeschen_vorgeschlagen INTEGER;
ALTER TABLE messungen ADD COLUMN herkunft TEXT NOT NULL DEFAULT 'mensch' CHECK (herkunft IN ('mensch', 'ki'));
ALTER TABLE messungen ADD COLUMN pruefstand TEXT NOT NULL DEFAULT 'gueltig' CHECK (pruefstand IN ('gueltig', 'vorschlag', 'verworfen'));
ALTER TABLE messungen ADD COLUMN loeschen_vorgeschlagen INTEGER;
DROP TRIGGER befund_eintraege_kein_update;
DROP TRIGGER befund_eintraege_kein_delete;
DROP TRIGGER befund_eintraege_kein_ersetzen;
CREATE TABLE befund_eintraege_neu (id INTEGER PRIMARY KEY AUTOINCREMENT, schluessel TEXT NOT NULL UNIQUE CHECK (length(schluessel) = 16), project_id TEXT NOT NULL, poi_id TEXT NOT NULL, art TEXT NOT NULL CHECK (art IN ('kommentar', 'status', 'zustaendig', 'frist', 'link', 'markierung', 'vorschlag')), text TEXT, wert TEXT NOT NULL, intern INTEGER NOT NULL DEFAULT 0 CHECK (intern IN (0, 1) AND (intern = 0 OR art IN ('kommentar', 'link'))), erstellt_von INTEGER REFERENCES users(id), erstellt_am INTEGER NOT NULL, kanal TEXT NOT NULL CHECK (kanal IN ('web', 'api', 'cli')), token_name TEXT, zurueckgezogen_am INTEGER, herkunft TEXT NOT NULL DEFAULT 'mensch' CHECK (herkunft IN ('mensch', 'ki')), bezug TEXT CHECK (bezug IS NULL OR length(bezug) = 16), FOREIGN KEY (project_id, poi_id) REFERENCES pois(project_id, id));
INSERT INTO befund_eintraege_neu (id, schluessel, project_id, poi_id, art, text, wert, intern, erstellt_von, erstellt_am, kanal, token_name, zurueckgezogen_am) SELECT id, schluessel, project_id, poi_id, art, text, wert, intern, erstellt_von, erstellt_am, kanal, token_name, zurueckgezogen_am FROM befund_eintraege;
DROP TABLE befund_eintraege;
ALTER TABLE befund_eintraege_neu RENAME TO befund_eintraege;
CREATE INDEX befund_eintraege_poi ON befund_eintraege(project_id, poi_id, id);
CREATE INDEX befund_eintraege_bezug ON befund_eintraege(project_id, poi_id, bezug);
CREATE TRIGGER befund_eintraege_kein_update BEFORE UPDATE ON befund_eintraege WHEN NOT (OLD.zurueckgezogen_am IS NULL AND NEW.zurueckgezogen_am IS NOT NULL AND NEW.text IS NULL AND OLD.art IN ('kommentar', 'link') AND NEW.id IS OLD.id AND NEW.schluessel IS OLD.schluessel AND NEW.project_id IS OLD.project_id AND NEW.poi_id IS OLD.poi_id AND NEW.art IS OLD.art AND NEW.wert IS OLD.wert AND NEW.intern IS OLD.intern AND NEW.erstellt_von IS OLD.erstellt_von AND NEW.erstellt_am IS OLD.erstellt_am AND NEW.kanal IS OLD.kanal AND NEW.token_name IS OLD.token_name AND NEW.herkunft IS OLD.herkunft AND NEW.bezug IS OLD.bezug) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_delete BEFORE DELETE ON befund_eintraege BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_ersetzen BEFORE INSERT ON befund_eintraege WHEN EXISTS (SELECT 1 FROM befund_eintraege WHERE id=NEW.id OR schluessel=NEW.schluessel) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
