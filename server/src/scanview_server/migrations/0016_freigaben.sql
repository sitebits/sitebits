-- Freigabe von Panoramen und Fotos an Dateien gebunden (F20, Etappe Mitmachen).
-- Eine Freigabe gilt nur für die Dateien, deren Pfad und SHA-256 hier stehen; der Server hat sie
-- beim Freigeben selbst gehasht. Eine Datei, die danach ausgetauscht wird, passt nicht mehr und ist
-- für Betrachter und Rückmelder gesperrt (404). Freigaben werden nie gelöscht, nur zurückgenommen.
-- Art `anonymisiert`: Ebene trägt eine geprüfte Anonymisierung (Werkzeug, Manifest-SHA-256);
-- `personenfrei`: Erklärung eines Admins, dass keine Personen zu sehen sind (Altbestand).
CREATE TABLE freigaben (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  ebene TEXT NOT NULL CHECK (ebene IN ('panos', 'fotos')),
  art TEXT NOT NULL CHECK (art IN ('anonymisiert', 'personenfrei')),
  werkzeug TEXT,
  manifest_sha256 TEXT CHECK (manifest_sha256 IS NULL OR length(manifest_sha256) = 64),
  dateien INTEGER NOT NULL CHECK (dateien >= 0),
  freigegeben_von INTEGER REFERENCES users(id),
  freigegeben_am INTEGER NOT NULL,
  zurueckgenommen_von INTEGER REFERENCES users(id),
  zurueckgenommen_am INTEGER
);
CREATE UNIQUE INDEX freigaben_aktiv ON freigaben(project_id, ebene) WHERE zurueckgenommen_am IS NULL;
CREATE TABLE freigabe_dateien (
  freigabe_id INTEGER NOT NULL REFERENCES freigaben(id),
  pfad TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  PRIMARY KEY (freigabe_id, pfad)
) WITHOUT ROWID;
CREATE TRIGGER freigabe_dateien_unveraenderlich BEFORE UPDATE ON freigabe_dateien BEGIN SELECT RAISE(ABORT, 'freigabe_dateien sind unveränderlich'); END;
CREATE TRIGGER freigabe_dateien_kein_delete BEFORE DELETE ON freigabe_dateien BEGIN SELECT RAISE(ABORT, 'freigabe_dateien sind unveränderlich'); END;
-- Übergang: Pakete ohne Anonymisierungsblock zeigen Panoramen wie bisher, bis die Projektleitung
-- „Panoramen nur freigegeben zeigen“ einschaltet. Bestehende Projekte: aus; neu eingerichtete: an.
ALTER TABLE projekte ADD COLUMN panoramen_nur_freigegeben INTEGER NOT NULL DEFAULT 0 CHECK (panoramen_nur_freigegeben IN (0, 1));
