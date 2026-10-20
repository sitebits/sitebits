-- Rolle Rückmelder: CHECK von project_roles erweitern. SQLite ändert einen CHECK nur über einen
-- Neuaufbau der Tabelle. Keine Tabelle verweist auf project_roles, der Neuaufbau läuft deshalb mit
-- eingeschalteten Fremdschlüsseln in der Transaktion von db.migrate. Nur vorwärts; der Rückweg im
-- Betrieb ist die Sicherung.
CREATE TABLE project_roles_neu (user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE, project_id TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('betrachter', 'rueckmelder', 'bearbeiter')), PRIMARY KEY (user_id, project_id));
INSERT INTO project_roles_neu (user_id, project_id, role) SELECT user_id, project_id, role FROM project_roles;
DROP TABLE project_roles;
ALTER TABLE project_roles_neu RENAME TO project_roles;
