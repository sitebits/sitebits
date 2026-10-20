-- Persönliche API-Tokens. Nur der SHA-256 des Tokens wird gespeichert.
CREATE TABLE api_tokens (
  id INTEGER PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  umfang TEXT NOT NULL CHECK (umfang IN ('lesen', 'schreiben')),
  erstellt_am INTEGER NOT NULL,
  laeuft_ab INTEGER NOT NULL,
  zuletzt_benutzt INTEGER,
  widerrufen_am INTEGER
);
CREATE INDEX api_tokens_nutzer ON api_tokens(user_id);
-- Protokoll: welcher Token schrieb (Name, nie der Wert). ADD COLUMN ändert keine Zeile — die Trigger bleiben.
ALTER TABLE ereignisse ADD COLUMN token_name TEXT;
-- Git-Export: bis zu welchem Ereignis ein Projekt exportiert ist.
CREATE TABLE git_export_stand (
  project_id TEXT PRIMARY KEY,
  letzte_ereignis_id INTEGER NOT NULL
);
