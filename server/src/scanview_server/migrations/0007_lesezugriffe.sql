-- Lesezugriffsprotokoll: wer hat wann welches Projekt gelesen. Aggregiert je Konto, Projekt, Art,
-- Objekt, Kanal, Token-Name und Zeitfenster (eine Zeile statt einer je Anfrage), unveränderlich,
-- löschbar nur durch lesezugriff.aufraeumen nach Ablauf der Aufbewahrungsfrist. Datensparsam:
-- keine IP, kein User-Agent, keine E-Mail, nie Token-Wert oder -Hash. Trigger einzeilig (_statements).
CREATE TABLE lesezugriffe (
  id INTEGER PRIMARY KEY,
  zeit INTEGER NOT NULL,                 -- erster Zugriff im Fenster
  fenster INTEGER NOT NULL,              -- zeit // FENSTER_S * FENSTER_S
  nutzer_id INTEGER NOT NULL REFERENCES users(id),
  kanal TEXT NOT NULL CHECK (kanal IN ('web', 'api')),
  token_name TEXT NOT NULL DEFAULT '',   -- nur der Name, nie Wert oder Hash (wie ereignisse)
  project_id TEXT NOT NULL,
  art TEXT NOT NULL CHECK (art IN ('oeffnen', 'paket', 'export', 'api')),
  objekt TEXT NOT NULL DEFAULT '',       -- z. B. csv|json|verlauf|pois|messungen
  UNIQUE (nutzer_id, project_id, art, objekt, kanal, token_name, fenster)
);
CREATE INDEX lesezugriffe_projekt ON lesezugriffe(project_id, id);
CREATE INDEX lesezugriffe_zeit ON lesezugriffe(zeit);
CREATE TRIGGER lesezugriffe_kein_update BEFORE UPDATE ON lesezugriffe BEGIN SELECT RAISE(ABORT, 'lesezugriffe sind unveränderlich'); END;
CREATE TRIGGER lesezugriffe_kein_ersetzen BEFORE INSERT ON lesezugriffe WHEN EXISTS (SELECT 1 FROM lesezugriffe WHERE id=NEW.id) BEGIN SELECT RAISE(ABORT, 'Protokoll ist unveränderlich'); END;
