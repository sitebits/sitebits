-- Panoramen-Tor fail-closed auch für Bestand (F20): Bis 0.12 blieben Panoramen älterer Pakete ohne
-- Anonymisierungsblock für Betrachter und Rückmelder sichtbar, solange die Projektleitung den Schalter
-- „Panoramen nur freigegeben zeigen“ nicht eingeschaltet hatte (Migration 0016: Bestand aus). Ab hier ist
-- er für jedes bestehende Projekt an; Projekte ohne Zeile in `projekte` deckt die Vorgabe im Code ab.
-- Abschalten bleibt eine bewusste, protokollierte Entscheidung der Projektleitung (einstellung.geaendert).
UPDATE projekte SET panoramen_nur_freigegeben = 1;
-- Prüfstand der aktiven Freigabe merken: Zeitpunkt der letzten Hash-Prüfung (`?pruefen=true`) und wie viele
-- freigegebene Dateien dabei abwichen. Ergebnis der Server-eigenen Prüfung, kein Nutzerinhalt; NULL = nie geprüft.
-- Rücksprung auf 0.12.x: Die alte Version nennt ihre Spalten beim INSERT, die neuen bleiben NULL und
-- ungelesen; den Schalter stellt sie nicht zurück (sicherer Rest).
ALTER TABLE freigaben ADD COLUMN zuletzt_geprueft_am INTEGER;
ALTER TABLE freigaben ADD COLUMN zuletzt_abweichend INTEGER CHECK (zuletzt_abweichend IS NULL OR zuletzt_abweichend >= 0);
