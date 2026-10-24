-- Uploads in Stücken: Kopf und Dateiliste. Die Dateien selbst liegen außerhalb der Paketwurzel im
-- Upload-Ordner (SCANVIEW_UPLOADS/<id>/<pfad>); hier stehen nur Angaben und der Stand. Bewusst in
-- scanview.db und nicht in jobs.db: der Import-Worker liest fremde Dateien und darf weder Stand
-- noch Prüfsumme eines Uploads umschreiben.
CREATE TABLE uploads (
  id TEXT PRIMARY KEY CHECK (length(id) = 32),
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  geaendert_am INTEGER NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('offen', 'abgeschlossen')),
  groesse INTEGER NOT NULL CHECK (groesse >= 0),
  kanal TEXT NOT NULL
);
CREATE TABLE upload_dateien (
  upload_id TEXT NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
  nr INTEGER NOT NULL CHECK (nr >= 0),
  pfad TEXT NOT NULL,
  groesse INTEGER NOT NULL CHECK (groesse > 0),
  stand INTEGER NOT NULL DEFAULT 0 CHECK (stand >= 0 AND stand <= groesse),
  sha256 TEXT,
  art TEXT,
  PRIMARY KEY (upload_id, nr)
);
