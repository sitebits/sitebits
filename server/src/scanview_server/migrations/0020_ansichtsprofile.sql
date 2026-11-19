-- Ansichtsprofile (P08-S6, ADR 0016 Nachtrag): Jeder Nur-Lese-Link trägt sein Profil selbst
-- („ansehen“ = schlanke Oberfläche, „messen“ = dazu Messwerkzeuge, „arbeiten“ = die volle Gast-
-- Oberfläche). Das Projekt hat ein Standardprofil: Vorbelegung beim Anlegen und Wert, wenn der
-- Anlege-Aufruf kein Profil nennt. Ein späterer Wechsel des Standards ändert keinen bestehenden Link.
-- Bestehende Links bekommen „ansehen“ (Andre 04.11.: öffentliche Links nicht überladen).
-- Rücksprung auf 0.12.x: Die alte Version nennt ihre Spalten beim INSERT, die DEFAULTs füllen die
-- neuen Spalten; migrate() überspringt die Version. Die Spalten bleiben dann einfach ungelesen.
ALTER TABLE teilen_links ADD COLUMN profil TEXT NOT NULL DEFAULT 'ansehen' CHECK (profil IN ('ansehen', 'messen', 'arbeiten'));
ALTER TABLE projekte ADD COLUMN ansichtsprofil TEXT NOT NULL DEFAULT 'ansehen' CHECK (ansichtsprofil IN ('ansehen', 'messen', 'arbeiten'));
