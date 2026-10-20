-- Bearbeiten, Verlauf und Statistik. Trigger stehen einzeilig (db._statements trennt an ';' am Zeilenende).
-- throttle.geaendert_am kommt aus 0002_sicherheit.sql.
ALTER TABLE project_meta ADD COLUMN version INTEGER NOT NULL DEFAULT 0;
CREATE TABLE pois (
  project_id TEXT NOT NULL,
  id TEXT NOT NULL,
  daten TEXT NOT NULL,
  paket_revision INTEGER,
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  geaendert_von INTEGER REFERENCES users(id),
  geaendert_am INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  geloescht_am INTEGER,
  PRIMARY KEY (project_id, id)
);
CREATE TABLE messungen (
  project_id TEXT NOT NULL,
  id TEXT NOT NULL,
  daten TEXT NOT NULL,
  paket_revision INTEGER,
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  geaendert_von INTEGER REFERENCES users(id),
  geaendert_am INTEGER NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  geloescht_am INTEGER,
  PRIMARY KEY (project_id, id)
);
-- Append-only: nutzer_id ohne ON DELETE CASCADE (ein Cascade-DELETE scheiterte am Trigger).
CREATE TABLE ereignisse (
  id INTEGER PRIMARY KEY,
  zeit INTEGER NOT NULL,
  nutzer_id INTEGER REFERENCES users(id),
  kanal TEXT NOT NULL CHECK (kanal IN ('web', 'cli', 'api')),
  project_id TEXT,
  art TEXT NOT NULL,
  objekt_id TEXT,
  vorher TEXT,
  nachher TEXT
);
CREATE INDEX ereignisse_projekt ON ereignisse(project_id, id);
CREATE TRIGGER ereignisse_kein_update BEFORE UPDATE ON ereignisse BEGIN SELECT RAISE(ABORT, 'ereignisse sind unveränderlich'); END;
CREATE TRIGGER ereignisse_kein_delete BEFORE DELETE ON ereignisse BEGIN SELECT RAISE(ABORT, 'ereignisse sind unveränderlich'); END;
-- INSERT OR REPLACE löscht die Konfliktzeile intern — ohne PRAGMA recursive_triggers (sqlite3-Shell, bloßes
-- connect) feuert dabei der DELETE-Trigger nicht. Dieser Trigger verbietet jede Einfügung mit vorhandener id.
CREATE TRIGGER ereignisse_kein_ersetzen BEFORE INSERT ON ereignisse WHEN EXISTS (SELECT 1 FROM ereignisse WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT, 'Protokoll ist unveränderlich'); END;
CREATE TABLE statistik_tag (
  project_id TEXT NOT NULL,
  tag TEXT NOT NULL,
  oeffnen INTEGER NOT NULL DEFAULT 0,
  standpunkt INTEGER NOT NULL DEFAULT 0,
  messung INTEGER NOT NULL DEFAULT 0,
  poi INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (project_id, tag)
);
