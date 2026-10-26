-- E-Mail-Benachrichtigung zu Befund-Fäden (F8). Ausgehend über SMTP aus einem eigenen Dienst
-- (scanview-post); der Webdienst schreibt nur die fertige Nachricht in ein Spool-Verzeichnis.
-- Ab Werk aus (Instanz: SCANVIEW_POST), je Person nur nach Einwilligung und bestätigter Adresse.

-- Einwilligung je Konto. `bestaetigt_hash`: SHA-256 der bestätigten Adresse (klein geschrieben),
-- keine Klartext-Kopie — ändert sich users.email, gilt sie nicht mehr. Token nur als SHA-256.
CREATE TABLE benachrichtigung_konten (
  user_id INTEGER PRIMARY KEY REFERENCES users(id),
  eingeschaltet INTEGER NOT NULL DEFAULT 0 CHECK (eingeschaltet IN (0, 1)),
  bestaetigt_hash TEXT CHECK (bestaetigt_hash IS NULL OR length(bestaetigt_hash) = 64),
  bestaetigt_am INTEGER,
  bestaetigung_hash TEXT CHECK (bestaetigung_hash IS NULL OR length(bestaetigung_hash) = 64),
  bestaetigung_ablauf INTEGER,
  geaendert_am INTEGER NOT NULL
);
-- Abgemeldet je Faden (Befund).
CREATE TABLE benachrichtigung_aus (
  user_id INTEGER NOT NULL REFERENCES users(id),
  project_id TEXT NOT NULL,
  poi_id TEXT NOT NULL,
  seit INTEGER NOT NULL,
  PRIMARY KEY (user_id, project_id, poi_id)
) WITHOUT ROWID;
-- Ausgang: nur Bezüge und Zustand, nie Text. status: wartend → gerendert (im Spool) | verworfen
-- (grund: rechte, aus, unbestaetigt, drossel, projekt_aus …) → gesendet | fehlgeschlagen | unklar.
CREATE TABLE benachrichtigung_ausgang (
  id TEXT PRIMARY KEY CHECK (length(id) = 32),
  user_id INTEGER NOT NULL REFERENCES users(id),
  project_id TEXT,
  poi_id TEXT,
  eintrag TEXT,
  ereignis TEXT NOT NULL CHECK (ereignis IN ('kommentar', 'link', 'status', 'zustaendig', 'anhang', 'bestaetigung')),
  status TEXT NOT NULL CHECK (status IN ('wartend', 'gerendert', 'verworfen', 'gesendet', 'fehlgeschlagen', 'unklar')),
  grund TEXT,
  erstellt_am INTEGER NOT NULL,
  geaendert_am INTEGER NOT NULL
);
CREATE INDEX benachrichtigung_ausgang_status ON benachrichtigung_ausgang(status, erstellt_am);
CREATE INDEX benachrichtigung_ausgang_empfaenger ON benachrichtigung_ausgang(user_id, status, geaendert_am);
-- Projektleitung kann Mail im Projekt ganz ausschalten (Standard an, wirkt nur bei Instanz an).
ALTER TABLE projekte ADD COLUMN benachrichtigung INTEGER NOT NULL DEFAULT 1 CHECK (benachrichtigung IN (0, 1));
