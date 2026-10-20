# 0001 Paketformat v2 mit Ebenen

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Der Viewer soll neben Laserscans auch Meshes und künftig weitere Datenarten zeigen und gemeinsam
vermessen. Das erste Paketformat (Schema 1) kannte genau eine Punktwolke und eine Liste von
Standpunkten; jede neue Datenart hätte den Vertrag zwischen Import und Viewer gebrochen. Zugleich
gibt es bereits Pakete im Schema 1, die weiter funktionieren müssen.

## Entscheidung

- Alles Rechenintensive passiert offline beim Import; ausgeliefert wird ein **statisches
  Projektpaket**, das der Browser direkt streamt.
- `project.json` Schema 2 beschreibt **einen Rahmen je Paket** (`frame`: Koordinatensystem, Einheit,
  `originWorld`) und eine Liste von **Ebenen** mit geschlossenem `kind` (`pointcloud`, `mesh`,
  `panoramas`; weitere reserviert). Ebenen bringen keine eigene Transformation mit.
- Jede Ebene mit Geometrie trägt Pflichtangaben zur Herkunft (`provenance`: Scan oder
  Photogrammetrie, interpoliert, Maßstab belegt) und zur Prüfung (`verify`); der Viewer lehnt ein
  Manifest ohne Herkunftsangabe ab.
- Koordinatensystem, Einheit, Achse und Versatz werden **nie geraten**. Weltkoordinaten bleiben
  Float64, dargestellt wird relativ zu `originWorld`.
- Der Viewer liest Schema 1 und 2 in dasselbe Innenmodell; `scanview-import migrate` hebt ein Paket
  auf Schema 2 und sichert das Original. Neue Pakete entstehen nur noch als Schema 2.

## Konsequenzen

- Neue Datenarten kommen als Adapter im Import und als Ebene im Viewer hinzu, ohne den Vertrag zu
  ändern. Umgesetzt sind Punktwolken- und Panorama-Ebenen; Mesh-Ebenen sind vorgesehen.
- Zwei Ebenen teilen einen Rahmen nur mit gleichem Koordinatensystem oder ausdrücklicher Erklärung
  plus bestandener Deckungsprüfung; „ungefähr passende“ Daten werden abgelehnt statt verschoben.
- Umprojizieren von Meshes ist ausgeschlossen, bis es eigens entschieden wird.
- Der Plattform-Server liest nur Schema 2; ältere Pakete werden vor der Bereitstellung migriert.

## Additive Sichtschicht (14.10.2026)

`kind: splats` und `initialView.layers` ergänzen Schema 2, ohne Bestandsmigration.
Der genaue Vertrag und die Abweisung fehlerhafter Startebenen/Rahmenbelege stehen in
[Gaussian-Sichtschicht](../public/splats.md). Die Sichtschicht ist nicht messbar und trägt daher
keine Messprovenienz; stattdessen verpflichtenden Deckungsbeleg und Importbericht.
