-- Auswertungen: Freigabe eines Berichts für Bearbeiter, je Projekt und Auftrag.
-- Bewusst hier und nicht in jobs.db: Die Freigabe ist eine Rechteentscheidung, und jobs.db ist
-- für den Import-Worker beschreibbar (der fremde Dateien liest). Ein ausgebrochener Job darf
-- seinen Bericht nicht selbst für weitere Rollen sichtbar machen. Löschen des Auftrags entfernt
-- die Zeile (das Protokollereignis bleibt, ohne Inhalt).
CREATE TABLE auswertung_freigaben (
  project_id TEXT NOT NULL,
  auftrag_id TEXT NOT NULL CHECK (length(auftrag_id) = 16),
  freigegeben_von INTEGER REFERENCES users(id),
  freigegeben_am INTEGER NOT NULL,
  PRIMARY KEY (project_id, auftrag_id)
);
