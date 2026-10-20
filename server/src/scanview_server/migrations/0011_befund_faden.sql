-- Befund-Fäden: POIs bleiben bestehen (kein Neuaufbau), nur zusätzliche Stand-Spalten; der
-- Status-CHECK steht im ADD COLUMN (konstanter Standard). Schalter je Projekt nach Vorlage.
-- Ein Gewerk, das an einem nicht gelöschten Befund zuständig ist, lässt sich nicht löschen
-- (projektverwaltung); nur an weich gelöschten Befunden wird die Zuständigkeit dann NULL.
-- Nach außen trägt jeder Eintrag nur seinen zufälligen `schluessel` (16 hex); die fortlaufende id
-- bleibt intern (Sortierung), sonst verrieten Lücken interne Einträge.
-- befund_eintraege ist append-only: UPDATE nur fürs einmalige Zurückziehen (zurueckgezogen_am
-- NULL → Wert und zugleich text NULL, alles andere gleich), kein DELETE, kein Ersetzen.
ALTER TABLE pois ADD COLUMN status TEXT NOT NULL DEFAULT 'offen' CHECK (status IN ('offen', 'in_arbeit', 'erledigt', 'abgeschlossen', 'abgelehnt'));
ALTER TABLE pois ADD COLUMN zustaendig_gewerk_id INTEGER REFERENCES gewerke(id) ON DELETE SET NULL;
ALTER TABLE pois ADD COLUMN zustaendig_nutzer_id INTEGER REFERENCES users(id);
ALTER TABLE pois ADD COLUMN frist TEXT;
ALTER TABLE projekte ADD COLUMN befund_faeden INTEGER NOT NULL DEFAULT 0 CHECK (befund_faeden IN (0, 1));
UPDATE projekte SET befund_faeden = (vorlage = 'bauprojekt');
CREATE TABLE befund_eintraege (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  schluessel TEXT NOT NULL UNIQUE CHECK (length(schluessel) = 16),
  project_id TEXT NOT NULL,
  poi_id TEXT NOT NULL,
  art TEXT NOT NULL CHECK (art IN ('kommentar', 'status', 'zustaendig', 'frist', 'link', 'markierung')),
  text TEXT,
  wert TEXT NOT NULL,
  intern INTEGER NOT NULL DEFAULT 0 CHECK (intern IN (0, 1) AND (intern = 0 OR art IN ('kommentar', 'link'))),
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  kanal TEXT NOT NULL CHECK (kanal IN ('web', 'api', 'cli')),
  token_name TEXT,
  zurueckgezogen_am INTEGER,
  FOREIGN KEY (project_id, poi_id) REFERENCES pois(project_id, id)
);
CREATE INDEX befund_eintraege_poi ON befund_eintraege(project_id, poi_id, id);
CREATE INDEX ereignisse_projekt_objekt ON ereignisse(project_id, objekt_id);
CREATE TRIGGER befund_eintraege_kein_update BEFORE UPDATE ON befund_eintraege WHEN NOT (OLD.zurueckgezogen_am IS NULL AND NEW.zurueckgezogen_am IS NOT NULL AND NEW.text IS NULL AND OLD.art IN ('kommentar', 'link') AND NEW.id IS OLD.id AND NEW.schluessel IS OLD.schluessel AND NEW.project_id IS OLD.project_id AND NEW.poi_id IS OLD.poi_id AND NEW.art IS OLD.art AND NEW.wert IS OLD.wert AND NEW.intern IS OLD.intern AND NEW.erstellt_von IS OLD.erstellt_von AND NEW.erstellt_am IS OLD.erstellt_am AND NEW.kanal IS OLD.kanal AND NEW.token_name IS OLD.token_name) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_delete BEFORE DELETE ON befund_eintraege BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
CREATE TRIGGER befund_eintraege_kein_ersetzen BEFORE INSERT ON befund_eintraege WHEN EXISTS (SELECT 1 FROM befund_eintraege WHERE id=NEW.id OR schluessel=NEW.schluessel) BEGIN SELECT RAISE(ABORT, 'befund_eintraege sind unveränderlich'); END;
