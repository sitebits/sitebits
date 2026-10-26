# Orientierte Fotos

Pakete können die Fotos einer photogrammetrischen Ausrichtung als eigene Ebene „Fotos“ enthalten,
zum Beispiel Drohnenbilder. Jedes Foto steht an seiner ausgerichteten Kameraposition, als
Blickpyramide in der 3D-Ansicht. Ein Klick auf die Spitze einer Pyramide öffnet das Foto im
Fenster „Foto“.

## Bedienung

- **Ebenen:** Die Ebene „Fotos“ listet ihre Gruppen (zum Beispiel Nadir, Fassade) mit der
  Anzahl der Fotos. Jede Gruppe lässt sich einzeln ein- und ausblenden. Ab 500 Fotos ist die
  Ebene zunächst ausgeblendet. „Einrahmen“ zeigt alle Kamerapositionen.
- **Foto-Fenster:** Mausrad oder `+`/`−` zoomen um den Mauszeiger, Ziehen verschiebt, `0` passt
  das Foto wieder ein. `←`/`→` blättern zur vorigen oder nächsten Kamera derselben Gruppe. Das
  Fenster lädt die passende Auflösungsstufe, bei Paketen mit voller Auflösung beim Hineinzoomen
  auch Kacheln.
- **Durch die Kamera:** Die 3D-Ansicht springt exakt in die Position und Blickrichtung des Fotos,
  das Foto liegt deckungsgleich über der Szene. Der Regler im Foto-Fenster blendet zwischen Foto
  und 3D über. `Esc` kehrt zur vorigen Ansicht zurück.

## Datenschutz

Baustellen- und Außenfotos zeigen oft Personen und Fahrzeuge. Betrachter und Rückmelder
bekommen Fotos nur, wenn die Projektleitung sie in den Projekt-Einstellungen freigegeben hat
(„Freigabe von Panoramen und Fotos“). Die Freigabe gilt genau für die Dateien, die beim
Freigeben vorlagen: Der Server speichert Pfad und SHA-256 jeder Datei, ein danach
ausgetauschtes Bild und jede nicht genannte Datei (etwa ein Sicherungsordner) sind für sie
gesperrt. Ein Statuswert in `images.json` allein gibt nichts frei. Bis zur Freigabe sehen nur
Bearbeiter, Projektleitung und Admin die Ebene. Die Sperre wirkt auf dem Server, nicht nur im
Viewer. Der statische Betrieb ohne Anmeldung (nginx) liefert Fotos grundsätzlich nicht aus.
Für Rahmenfotos gibt es noch keinen Anonymisierungsschritt; ohne ihn kann nur ein Admin eine
Fotoebene als „personenfrei“ erklären (protokolliert). Panoramen: siehe `import/README.md`,
`panos-kameras --anonymisiert`.

## Import

1. Die Kameras aus der Photogrammetrie-Software ins Austauschformat `kameras.json` exportieren
   (Beschreibung in `import/README.md`, „Austauschformat `kameras.json`“). Für Rahmenkameras
   enthält der Export die **angepasste** Kalibrierung (nicht die Startwerte), je Sensor
   Referenzpunkte aus der eigenen Projektion der Software, Tie Points und
   Tie-Point-Projektionen. Je Sensor nennt der Export `rollingShutter` und
   `calibrationAdjusted`.
2. `scanview-import fotos-kameras --kameras kameras.json <paket>` schreibt die Ebene in ein
   vorhandenes Paket. `--max-kante 4096` (Standard) begrenzt die lange Kante,
   `--max-kante voll` schreibt zusätzlich Kacheln in voller Auflösung. `--gruppe` wählt Gruppen,
   `--prozesse` entzerrt parallel. Jeder Prozess braucht in der Spitze rund 55 MB je Megapixel
   des größten Fotos (gemessen: 24 MP ≈ 1,3 GB, 45 MP ≈ 2,4 GB); `--prozesse 4` mit 45-MP-Fotos
   also knapp 10 GB Arbeitsspeicher.
   Ohne eigenen Export: `fotos-odm` (OpenDroneMap) oder `fotos-colmap` (COLMAP, georeferenziert
   per `model_aligner`) — siehe `import/README.md`. Das Kameramodell belegen dort die
   Merkmalspositionen der Rekonstruktion (Median-Restvektor ≤ 0,1 px) statt der Referenzpunkte
   aus `kameras.json`.
3. `scanview-import fotos-pruefen <paket>` prüft Dateien, Größen, Metadatenfreiheit und ob die
   Inhalts-Revision jedes Fotos zu seinen Dateien passt.

Jedes Foto liegt unter einer Adresse, die seinen Inhalt kennzeichnet (`fotos/<ID>/<Revision>/`).
Ein erneuter Import, etwa nach der Anonymisierung, legt geänderte Fotos unter neuer Adresse ab:
Browser zeigen danach nie das alte Bild aus ihrem Zwischenspeicher. Pakete älterer Versionen
(ohne Revision) bleiben lesbar.

Der Import bricht ab, ohne das Paket zu verändern, wenn

- das Verzeichnungsmodell die Referenzpunkte des Exports nicht auf 0,1 px trifft,
- ein Sensor Rolling-Shutter-Kompensation nutzt (`rollingShutter: true`) oder keine angepasste
  Kalibrierung hat (`calibrationAdjusted: false`); fehlen die Angaben, gilt beides als erfüllt,
- die Tie Points nicht zur Punktwolke des Pakets passen (anderer Ausrichtungsstand; Grenze
  0,05 m, ab 0,20 m sicher abgewiesen, dazwischen nur mit `--rahmen-toleranz`),
- die Rückprojektion der Tie Points im Median mehr als 2 px abweicht — geprüft je Foto einer
  Stichprobe (je Gruppe höchstens 50 Kameras mit bis zu 200 Tie Points); sie prüft Pose,
  Koordinatensystem und Verzeichnungsmodell, nicht jedes einzelne Foto. Mit
  `--rueckprojektion-grenze PX` (höchstens 10) lässt sich die Grenze anheben, wenn der eigene
  Ausgleichsrest der Photogrammetrie-Software für einzelne Fotos knapp darüber liegt; Fehler der
  Kette zeigen sich mit vielen Pixeln.
  `images.json` vermerkt Grenze, Standardgrenze und die Fotos über 2 px (`aboveDefault`),
- ein Foto eine EXIF-Drehung trägt. Ausnahme `--exif-drehung-ignorieren`: Drehungen um 90°
  (Orientation 5 bis 8) werden übergangen, wenn die Rohpixelmaße eines nicht quadratischen Fotos
  der exportierten Kamera entsprechen — dann wurde ungedreht in Sensorpixeln gerechnet (so bei
  Drohnen, die hochkant aufnehmen und das nur im EXIF vermerken). Spiegeln und 180° (2 bis 4)
  bleiben gesperrt, weil die Maße dort nichts belegen. Die Anzahl steht in `images.json` unter
  `provenance.exifOrientationIgnored`.

Die Fotos werden einmal auf eine Lochkamera entzerrt (Hauptpunkt in der Bildmitte, Rand ohne
leere Pixel beschnitten). Jede Stufe wird aus den Pixeln neu kodiert. EXIF, GPS, XMP und
Farbprofile der Originale gelangen nicht ins Paket.

## Grenzen

- Eine Szene ohne Flächen in allen drei Richtungen (nur flacher Boden) lässt sich nicht auf den
  Ausrichtungsstand prüfen und wird abgewiesen.
- „Fotos zu diesem Punkt“, Messen im Foto und der Synchron-Strahl zwischen Foto und 3D folgen in
  einer späteren Version.
