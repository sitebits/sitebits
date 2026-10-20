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
(`scanview-import genauigkeit`, aus Metashape).

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
