# Koordinaten, Punktklassen, Genauigkeit und Fenster-Effekt

Vier Funktionen im Viewer, die mit Version 0.10 dazukommen. Alle lesen nur, was das Paket mitbringt;
fehlt eine Angabe, sagt der Viewer das, statt einen Wert anzunehmen.

## Koordinaten am Zeiger

- **Einschalten:** Taste `C` (oder Ansichtslink mit `ko=1`). Unten links erscheint eine Zeile mit
  Rechtswert, Hochwert und Höhe, dazu Koordinatensystem und Höhensystem, zum Beispiel
  `E 367 334,404  N 5 807 317,817  H 59,837 m NHN · ETRS89/UTM33`. Pakete ohne Koordinatensystem
  heißen „lokal“ und zeigen `x y z`.
- **Genau oder Vorschau:** Während die Maus läuft, zeigt die Zeile eine schnelle Vorschau. Ruht sie
  kurz, sucht der Viewer den Punkt so genau wie beim Messen und hält ihn fest.
- **Kopieren:** Klick auf die Zeile oder Taste `U` kopiert den festgehaltenen Punkt als
  `E;N;H;System;Höhensystem` mit Dezimalpunkt, passend für GIS-Programme.
- **Zweite Höhe:** Trägt das Paket die Quasigeoidhöhe (Import ab 0.10, Gitter GCG2016 des BKG),
  steht die andere Höhe daneben, etwa `H 59,837 m NHN (ellips. 100,000 m ±0,4 cm)`. Sie erscheint
  nur, wenn der Unterschied über das ganze Paket höchstens 1 cm schwankt.
- **Exporte:** Die Koordinatenspalte der Mess-CSV, der Server-Export und die Mängelliste nennen
  Koordinaten- und Höhensystem im Spaltenkopf, zum Beispiel `Koordinaten X Y Z [m, ETRS89/UTM33, NHN]`.

## Punktklassen und Echos

Pakete aus klassifizierten Laserscans (LAS/LAZ) bringen ihre Klassen mit (Boden, Gebäude,
Vegetation, Rauschen …).

- **Farbmodus „Klassen“** färbt die Punkte nach den ASPRS-Standardfarben.
- **Ordner „Klassen und Echos“:** ein Schalter je vorhandener Klasse mit Punktzahl. Ausgeblendete
  Klassen fehlen in jedem Farbmodus und werden beim Messen nicht getroffen.
- **Echos:** „nur erste Echos“ (etwa Kronendach) und „nur letzte Echos“ (etwa Boden unter Bäumen).
  „Nur letzte“ gibt es nur, wenn der Import die letzten Echos markiert hat; das Paket nennt das als
  Attribut `returnsLast`.
- **Ansichtslink:** `kx=` (ausgeblendete Klassen) und `ec=1`/`ec=2` (erste/letzte Echos). Ein Link
  wirkt nur mit Klassen und Echos, die das Paket hat.

## Genauigkeit

Ob eine Messung auf Zentimeter stimmt, hängt davon ab, wie die Punktwolke entstanden und geprüft
ist. scanview zeigt das, wenn der Genauigkeitsnachweis importiert wurde
(`scanview-import genauigkeit`, aus dem Export der Photogrammetrie-Software).

- **Ebenen-Panel:** Abschnitt „Genauigkeit“ je Punktwolke und Mesh, getrennt nach **Lage** (wo im
  Koordinatensystem) und **Maßstab** (stimmen Strecken). Dazu Quelle (Programm, Datum) und Bericht.
- **Was als geprüft gilt:** Nur Kontrollpunkte und Kontrollstrecken, die nicht in die Berechnung
  eingeflossen sind. Passpunkte und die GNSS-Positionen der Drohne zeigen, wie gut das Modell zu
  ihnen passt, aber nicht, wie genau es ist. Dafür gibt es nie einen ±-Wert, sondern zum Beispiel
  „Lage nicht unabhängig geprüft (nur Drohnen-GNSS)“ oder „Maßstab aus 1 Strecke gesetzt, nicht
  geprüft“.
- **Ohne Nachweis** steht dort „kein Genauigkeitsnachweis“.
- **An Messwerten:** Messungen aus einer Ebene mit Nachweis tragen denselben Hinweis, in der
  Messliste und in der Spalte „Hinweis“ der CSV (Viewer und Server).
- **Für Agenten:** Projektkarte und MCP `projekt_lesen` liefern je Ebene `genauigkeit` mit Text
  und maschinenlesbarem Status; `export.json`, Mängelliste und Git-Export nennen ihn ebenfalls.

## Kontrollpunkte am Modell messen

Panel **Kontrollpunkte** (Panelmenü): eine Kontrollpunktliste laden, jede Marke selbst am Modell
picken und die Liste mit Ist-Werten speichern. Diese Datei liest der Katalogprüfer
(`scanview-pruefen`) direkt; mit ihr prüft er Lage (RMSE_H) und Maßstab aus
Kontrollpunktpaaren, die er sonst nur aus Lieferantenwerten nachrechnen könnte.

- **Liste:** CSV aus einer lokalen Datei (nichts wird hochgeladen), Kopf `id;rolle;e;n;h`, optional
  `sigma_lage;sigma_hoehe`, Trenner `;` (Dezimalkomma oder -punkt) oder `,` (nur Dezimalpunkt),
  UTF-8. Pflicht ist die Kommentarzeile `# crs = EPSG:<Lage>+<Höhe>`, z. B. `EPSG:25833+7837`.
  Rollen `check`/`kontrollpunkt` und `control`/`passpunkt` wie im Prüfer.
- **Bezugssystem:** Die Liste muss zum Paket passen — Lage wie das Paket (EPSG:25832, 25833 oder
  3068), Höhe wie dessen Höhensystem (DHHN2016 = EPSG:7837, DHHN92 = EPSG:5783). Sonst wird sie mit
  Grund abgelehnt; umgerechnet wird nie. Pakete in lokalen Koordinaten können keine Kontrollpunkte
  aufnehmen.
- **Streng wie der Prüfer:** unbekannte Spalten, doppelte IDs, fehlende Sollwerte, unklare Zahlen
  werden abgelehnt. Zusätzlich lehnt der Viewer IDs ab, die mit `= + - @` oder `#` beginnen oder
  `;` enthalten — sie gingen unverändert in die Exportdatei.
- **Picken:** Punkt in der Liste wählen → die Kamera fliegt in die Nähe. Mit „Picken“ gehört der
  nächste Klick in die Ansicht diesem Punkt (die Werkzeugleiste springt auf Navigieren). Gepickt wird
  wie beim Messen: auf Punktwolke oder Mesh, mit Rückfrage vorn/hinten, „Kanten einrasten“ (Taste E)
  gilt auch hier. „Neu picken“ überschreibt, „Leeren“ entfernt den Ist-Wert.
- **Bewusst kein Sollmarker und keine Abweichung:** Die Messung soll unabhängig sein. Wer die
  Sollposition sieht, pickt sie statt der Marke im Modell. Ausgewertet wird nur im Prüfer.
- **Export:** `kontrollpunkte-ist-<Projekt>.csv`, Spalten
  `id;rolle;e;n;h;sigma_lage;sigma_hoehe;ist_e;ist_n;ist_h`, Soll und σ wie geladen, Ist in Metern
  mit 3 Nachkommastellen, Dezimalpunkt. Oben `# crs = …` wie geladen, `# quelle = siteBits-Viewer
  <Version>, am Modell gepickt` und je gepicktem Punkt `# ebene <id> = <Ebene>`. Ungepickte Punkte
  haben leere Ist-Zellen. Ist-Werte, die schon in der geladenen Liste standen, übernimmt der Export
  nicht (der Viewer sagt das beim Laden).
- **Entwurf:** Jeder Pick wird in diesem Browser gesichert (je Projekt und Soll-Liste, erkannt am
  SHA-256 der Datei). Lädt man dieselbe Liste später wieder, fragt das Panel „n gepickte Punkte aus
  dem Entwurf übernehmen?“ — Übernehmen oder Verwerfen; still übernommen wird nichts. Ein Export
  lässt den Entwurf stehen; „Entwurf verwerfen“ löscht ihn. Beim Abmelden im Portal werden alle
  Entwürfe gelöscht. Ohne Browser-Speicher (privates Fenster, gesperrt) gibt es keinen Entwurf.
- **Exportdatei ist kein Zwischenstand:** lädt man sie wieder, bringt sie ihre Ist-Werte nicht mit
  (sie zählen wie Lieferantenwerte und werden verworfen).
- **Prüfer:** die Datei mit `scanview-pruefen … --kontrollpunkte <datei>` übergeben. Liegt sie in
  der Lieferung neben einer weiteren `*kontrollpunkt*.csv`, ist die Liste dort nicht eindeutig.

## Fenster-Effekt (Webcam)

Die Perspektive folgt dem Kopf: Wer den Kopf bewegt, sieht wie durch ein Fenster an Dingen vorbei.

- **Einschalten:** Ordner „Fenster-Effekt (Webcam)“ → „Perspektive folgt dem Kopf“. Der Browser
  fragt nach der Kamera. Gerade hinsetzen und „Mitte setzen“ drücken; Bildschirmgröße und Stärke
  einstellen.
- **Datenschutz:** Aus ab Werk. Das Kamerabild wird nur im Browser ausgewertet und nirgends
  hingeschickt oder gespeichert. Solange die Kamera läuft, steht oben ein Hinweis mit „Aus“. Beim
  Wechsel in einen anderen Tab geht die Kamera aus.
- **Grenzen:** Für eine Person direkt vor dem Bildschirm (Laptop), nicht für Beamer oder Gruppen.
  Wirkt in der 3D-Ansicht (Orbit); im Panorama pausiert er, weil ein Panorama keine Tiefe hat.
  Solange er an ist, sind die Messwerkzeuge gesperrt.
- **Betrieb:** Die Instanz muss die Kamera für den Viewer erlauben (`Permissions-Policy`, siehe
  `deploy/platform/README.md`); sonst meldet der Viewer „Kamera nicht freigegeben“.
