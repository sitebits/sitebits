-- Zusammenarbeit: Rolle Projektleitung, Gewerke je Projekt, projektgebundene Einladungen,
-- Projekt-Einstellungen mit Vorlage und Kategorien je Projekt. project_roles bekommt den
-- erweiterten CHECK und die Spalte gewerk_id; SQLite ändert einen CHECK nur über einen Neuaufbau,
-- keine Tabelle verweist auf project_roles. gewerke entsteht vorher (Fremdschlüssel). Dass ein
-- Gewerk zum selben Projekt gehört, prüft der Code. Nur vorwärts; der Rückweg im Betrieb ist die
-- Sicherung. Anweisungen einzeilig (db._statements).
CREATE TABLE gewerke (id INTEGER PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, kurz TEXT NOT NULL, bauherrenseite INTEGER NOT NULL DEFAULT 0 CHECK (bauherrenseite IN (0, 1)), angelegt_am INTEGER NOT NULL);
CREATE UNIQUE INDEX gewerke_name ON gewerke(project_id, name COLLATE NOCASE);
CREATE TABLE project_roles_neu (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, project_id TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('betrachter', 'rueckmelder', 'bearbeiter', 'projektleitung')), gewerk_id INTEGER REFERENCES gewerke(id) ON DELETE SET NULL, PRIMARY KEY (user_id, project_id));
INSERT INTO project_roles_neu (user_id, project_id, role) SELECT user_id, project_id, role FROM project_roles;
DROP TABLE project_roles;
ALTER TABLE project_roles_neu RENAME TO project_roles;
ALTER TABLE invites ADD COLUMN project_id TEXT;
ALTER TABLE invites ADD COLUMN gewerk_id INTEGER REFERENCES gewerke(id) ON DELETE SET NULL;
CREATE INDEX invites_projekt ON invites(project_id);
CREATE TABLE projekte (project_id TEXT PRIMARY KEY, vorlage TEXT NOT NULL CHECK (vorlage IN ('bestandsaufnahme', 'bauprojekt')), rueckmeldungen INTEGER NOT NULL DEFAULT 1 CHECK (rueckmeldungen IN (0, 1)), export_bearbeiter INTEGER NOT NULL DEFAULT 1 CHECK (export_bearbeiter IN (0, 1)), angelegt_am INTEGER NOT NULL);
CREATE TABLE projekt_kategorien (id INTEGER PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projekte(project_id) ON DELETE CASCADE, name TEXT NOT NULL, reihenfolge INTEGER NOT NULL, aktiv INTEGER NOT NULL DEFAULT 1 CHECK (aktiv IN (0, 1)));
CREATE UNIQUE INDEX projekt_kategorien_name ON projekt_kategorien(project_id, name COLLATE NOCASE);
