-- Nur-Lese-Links (F28, ADR 0016): Gäste sehen Wolke und Meshes eines Projekts ohne Konto.
-- Schalter je Projekt, Standard aus. Gespeichert wird nur der SHA-256 des Link-Tokens und der
-- Gast-Sitzung; das optionale Link-Passwort als argon2-Hash. Ein Link wird nie gelöscht, nur
-- widerrufen (Nachvollziehbarkeit: `abrufe`, `letzter_abruf` je Freigabe statt Person).
ALTER TABLE projekte ADD COLUMN nur_lese_links INTEGER NOT NULL DEFAULT 0 CHECK (nur_lese_links IN (0, 1));
CREATE TABLE teilen_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  project_id TEXT NOT NULL,
  name TEXT NOT NULL CHECK (length(name) BETWEEN 1 AND 80),
  token_hash TEXT NOT NULL UNIQUE CHECK (length(token_hash) = 64),
  passwort_hash TEXT,
  erstellt_von INTEGER REFERENCES users(id),
  erstellt_am INTEGER NOT NULL,
  ablauf INTEGER NOT NULL CHECK (ablauf > erstellt_am),
  widerrufen_am INTEGER,
  abrufe INTEGER NOT NULL DEFAULT 0,
  letzter_abruf INTEGER
);
CREATE INDEX teilen_links_projekt ON teilen_links(project_id, id);
CREATE TABLE gast_sitzungen (
  token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
  link_id INTEGER NOT NULL REFERENCES teilen_links(id),
  erstellt_am INTEGER NOT NULL,
  ablauf INTEGER NOT NULL
);
CREATE INDEX gast_sitzungen_link ON gast_sitzungen(link_id);
