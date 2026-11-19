# Anleitung für KI-Agenten

Diese Anleitung richtet sich an Sprachmodelle, die mit einer siteBits-Instanz arbeiten, über den
MCP-Server oder über Skripte gegen den Client `sitebits`. Sie steht wortgleich als MCP-Ressource
`sitebits://agenten-anleitung` und im Skill `clients/sitebits/skill/SKILL.md`.

## Grundregeln

1. **Die KI schlägt vor, ein Mensch prüft.** Mit einem Token „für einen KI-Agenten“ wird alles,
   was du anlegst oder änderst, ein Vorschlag (`pruefstand: "vorschlag"`, Antwort 201 oder 202).
   Annehmen oder verwerfen kann nur ein Mensch. Melde dem Menschen, was du vorgeschlagen hast,
   und behaupte nie, es sei schon gültig.
2. **Zahlen nur aus Werkzeugen.** Maße, Flächen, Urteile und Genauigkeiten übernimmst du aus den
   Antworten der Instanz (`messungen_lesen`, `auswertung_lesen`, `projekt_lesen` → `genauigkeit`).
   Rechne sie nicht nach, runde sie nicht schöner und erfinde keine. Fehlt ein Wert, sag das.
3. **Herkunft benennen.** Jeder Befund und jede Messung trägt `herkunft` (`mensch` oder `ki`),
   `pruefstand` und `grundlage`, worauf die Koordinaten beruhen:
   - `gemessen`: Ein Mensch hat jeden Punkt im Viewer in einer Ebene des Pakets gewählt, und
     deren Maßstab ist geprüft.
   - `ungeprueft`: wie gemessen, aber eine Quell-Ebene hat keinen geprüften Maßstab.
   - `angegeben`: Koordinaten ohne Punkt im Paket oder über ein Token gesetzt (Skript oder
     KI-Agent). Was du anlegst, ist immer `angegeben`, auch mit Punktquellen.
   - `unbekannt`: Eine Quell-Ebene gibt es im aktuellen Paket nicht mehr.

   Gib das weiter, wenn du Ergebnisse berichtest, und stelle Angegebenes nie als gemessen dar.
4. **Auf den Code verzweigen, nicht auf den Text.** Jede Fehlerantwort hat einen festen `code` und
   oft einen `hinweis`, was stattdessen ginge (Liste: `docs/public/fehlercodes.md`). Häufige Fälle:
   - `konflikt`: neu lesen, mit der aktuellen `version` erneut senden. Nicht blind wiederholen.
   - `zustand`, `id_vergeben`: Der Vorgang passt nicht zum Stand. Lesen und neu entscheiden.
   - `nur_lesen`, `keine_berechtigung`, `nur_im_browser`, `mensch_prueft`: Das darfst du nicht.
     Sag dem Menschen, wer es tun kann; versuche keinen Umweg.
   - `nur_vorschlag`: Lege einen neuen Vorschlag an, statt Gültiges zu ändern.
   - `nicht_gefunden`: ID prüfen. Die Antwort sagt bewusst nicht, ob es das Projekt gibt.
   - `zu_viele_anfragen`, `zu_viele_auftraege`, `ueberlastet`, `gesperrt`: warten, nicht in
     Schleife fragen.
5. **Änderungen tragen `version`.** Ändern, Löschen, Status, Zuständigkeit und Frist brauchen die
   `version`, die du zuletzt gelesen hast.

## Koordinaten

Koordinaten sind Weltkoordinaten des Pakets in Metern, `[x, y, z]`. Das Koordinaten- und
Höhensystem steht in `projekt_lesen` (`crs`, `einheit`). Ohne `crs` ist das Projekt lokal im
Modellmaß: Dann sind Abstände nur so gut wie der belegte Maßstab (`genauigkeit`). In die
Punktwolke klicken kannst du nicht; Messungen legst du mit bekannten Koordinaten an.

## Werkzeuge oder Skripte

- **MCP-Werkzeuge** für einzelne Schritte: lesen, einen Befund anlegen, kommentieren.
- **Skripte gegen den Client `sitebits`** (`docs/public/skripte.md`), wenn du viele Einträge
  liest, filterst oder mehrere Auswertungen vergleichst. Die Zwischendaten bleiben im Skript; ins
  Gespräch kommt nur das Ergebnis.
- **Große Dateien** (Punktwolken, Meshes) gehen nie durch das Modell. Uploads macht ein Skript
  oder ein Mensch im Portal; du bekommst die `upload_id`.

## Datenschutz

Im datensparsamen Modus erscheinen Namen anderer Konten als Pseudonym und Freitexte als
Platzhalter „Freitext ausgeblendet (datensparsamer Modus)“. Schreibe den Platzhalter nie als
Inhalt zurück; lass ein Feld weg, das unverändert bleiben soll. Panoramen und Fotos mit Personen gibt nur ein Mensch frei.
