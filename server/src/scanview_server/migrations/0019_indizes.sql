-- Leistung (nur Indizes, keine Datenänderung). Ein Rücksprung auf eine ältere Version läuft ohne
-- Eingriff: migrate() überspringt Versionen bis zur höchsten eingetragenen, die Indizes stören dort nicht.
-- Rollen eines Projekts (Projektverwaltung, Benachrichtigungen): der Primärschlüssel (user_id, project_id)
-- deckt nur die Suche je Nutzer ab.
CREATE INDEX project_roles_projekt ON project_roles(project_id);
-- Einladungen je E-Mail (Einlösen, Verwaltung, Widerrufen); NOCASE kommt aus der Spalte.
CREATE INDEX invites_email ON invites(email);
