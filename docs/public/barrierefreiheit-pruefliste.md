# Barrierefreiheit — manuelle Prüfliste (Portal)

Grundlage: die Prüfschritte des BIK BITV-Tests (Web) für die **interne Selbstbewertung**
(https://github.com/BIK-BITV/BIK-Web-Test). Diese Liste fasst sie für scanview zusammen; sie ersetzt
weder den BIK BITV-Test durch eine Prüfstelle noch Tests mit Betroffenen. Automatische Prüfungen
(axe-core) finden nur einen Teil der Barrieren.

Ergebnis je Ansicht: ✓ erfüllt · ✗ nicht erfüllt (mit Befund-ID) · n. g. nicht geprüft (mit Grund).
Ansichten: Anmelden, Einladung, Willkommen, Start/Projektliste mit Suche, Einstellungen/API-Zugänge,
Verlauf, Verwaltung, Barrierefreiheit — jeweils leer, mit Daten, mit Fehlermeldung.

| Nr | Prüfpunkt | WCAG (Stufe) | Wie |
|---|---|---|---|
| 1 | Tastatur: alles erreichbar, logische Reihenfolge, Fokus immer sichtbar, keine Falle | 2.1.1 (A), 2.1.2 (A), 2.4.3 (A), 2.4.7 (AA), 2.4.11 (AA, 2.2) | nur Tab/Umschalt+Tab/Enter/Leertaste/Esc, Maus weglegen |
| 2 | Screenreader: Überschriften, Landmarken, Formularbeschriftungen, Fehler- und Statusmeldungen werden angesagt | 1.3.1 (A), 2.4.6 (AA), 3.3.1 (A), 3.3.2 (A), 4.1.2 (A), 4.1.3 (AA) | NVDA mit Firefox und Chrome, VoiceOver mit Safari (macOS, iOS), TalkBack mit Chrome (Android) |
| 3 | Zoom 200 % ohne Verlust; 400 % bzw. 320 CSS-px ohne waagerechtes Scrollen | 1.4.4 (AA), 1.4.10 (AA) | Browser-Zoom, schmales Fenster |
| 4 | Textabstand erhöhen, nichts wird abgeschnitten | 1.4.12 (AA) | Stil einfügen (Zeile 1,5, Absatz 2, Buchstaben 0,12, Wörter 0,16) |
| 5 | Kontrast in Zuständen, die axe nicht sieht (Hover, Fokus, deaktiviert) | 1.4.3 (AA), 1.4.11 (AA) | Kontrastmesser, Stichprobe; axe-„needs review“-Einträge des Laufs |
| 6 | Hoher Kontrastmodus: Knöpfe, Fokus, Zustände erkennbar | 1.4.11 (AA) | Windows-Kontrastdesign bzw. `forced-colors: active` |
| 7 | Systemeinstellung „Bewegung reduzieren“ wird befolgt | 2.3.3 (AAA, Ziel nach BITV § 3 Abs. 4) | Systemeinstellung bzw. `prefers-reduced-motion`; im Viewer zusätzlich die Einstellung „Bewegung reduzieren“: der Wechsel zwischen Panoramen ist dann ein Schnitt ohne Fahrt |
| 8 | Anmeldung ohne Gedächtnis- oder Rechenaufgabe, Einfügen erlaubt, keine Zeitgrenze | 3.3.8 (AA, 2.2), 2.2.1 (A) | Passwort einfügen, Passkey-Weg, lange warten |
| 9 | Fehlermeldungen sagen, was zu tun ist; Begriffe einheitlich | 3.3.1 (A), 3.3.3 (AA), 3.2.4 (AA) | lesen, vergleichen |
| 10 | Hilfe und Feedback an gleicher Stelle auf jeder Seite, Feedback-Weg funktioniert | 3.2.6 (A, 2.2) | Fußzeile auf jeder Seite, „Barriere melden“ öffnet Mailprogramm bzw. Formular |
