# 0003 Eigener Kachler für große Meshes

- **Status:** angenommen, durch Vorstudie bestätigt (2026-09-28)
- **Datum:** 2026-09-28

## Kontext

Geplant ist, Meshes als Ebene auszuliefern. Meshes über etwa 4 Mio. Dreiecken oder mit Texturen
über 8192² lassen sich nicht als ein glTF ausliefern; sie müssen als 3D Tiles mit Detailstufen gekachelt werden. Der Ausgabevertrag verlangt
unter anderem Blätter in voller Auflösung, gemessenen `geometricError`, begrenzte Dreiecke und
Texturgröße je Kachel ohne Verlust an Texeldichte. Zwei vorhandene Werkzeuge (Obj2Tiles 1.6.2,
mago-3d-tiler 1.16.3) wurden in einem Spike an einem Mesh mit 7,7 Mio. Dreiecken gegen diesen
Vertrag gemessen.

## Entscheidung

- Große Meshes werden mit einem **eigenen Kachler** im Import-Werkzeug in 3D Tiles 1.1 umgewandelt.
- Grundlage ist das Spike-Ergebnis: Obj2Tiles hielt die Geometrie, verfehlte aber Laufzeit,
  Texeldichte der Blätter und die Dreiecksgrenze je Kachel; mago-3d-tiler scheiterte strukturell
  (keine räumliche Teilung).
- Vor dem verbindlichen Eigenbau klärt eine kurze Vorstudie die Machbarkeit der kritischen Teile
  (Textur je Kachel, gemessener Fehler, Prüfer).

## Konsequenzen

- Rückfallentscheidung aus der Rangfolge, keine technische Notwendigkeit; die Vorstudie kann Weg B
  neu bewerten.
- Höherer Aufwand als erwartet (Schätzung 8–12 Arbeitstage ohne Machbarkeitsnachweis). Mesh-Ebenen
  sind im Paketformat vorgesehen, werden aber noch nicht importiert oder dargestellt.
- Nicht geprüfte Alternativen (andere Werkzeugoptionen, Vorzerlegung, weitere Kachler) bleiben als
  Rückweg offen.

## Nachtrag Vorstudie 28.09.2026

Die Vorstudie bestätigt die Entscheidung. Geprüft wurde zuerst, ob ein nativer 3D-Tiles-Export einer
Photogrammetrie-Software unverändert durchgereicht werden kann: Am selben Testmesh (7,7 Mio. Dreiecke) verletzt er
den Ausgabevertrag strukturell: Punkte der Quelle weichen bis 1,46 mm von den Blattkacheln ab
(Grenze 0,35 mm), und die Blattkacheln haben 6,1 % mehr Fläche als die Quelle, weil sich benachbarte
Kacheln überlappen (Grenze 0,1 %; nach Entfernen exakter Duplikate bleiben 3,3 %). Die übrigen Werkzeuge tragen ebenfalls nicht: Obj2Tiles mit unveränderten Texturen
behält eine Wurzelkachel mit 2,57 Mio. Dreiecken (Grenze 500 000), py3dtiles 12.1.1 hat keinen
Mesh-Pfad für OBJ, Py3DTilers teilt ein zusammenhängendes Mesh nicht, und eine Vorzerlegung
übernähme rund die Hälfte des Eigenbaus. Offen ist allein ein angepasster mago-3d-tiler (kleinere
Blattkacheln); deshalb beginnt die Umsetzung mit einem auf 1,5 Arbeitstage begrenzten Versuch damit
und geht bei Scheitern in den eigenen Kachler über.

Vertragsänderung: Ein Wurzel-Transform ungleich der Einheitsmatrix ist im Paket zulässig, wenn er
starr ist (Drehanteil orthonormal mit Determinante +1, keine Skalierung oder Scherung) und die Lage im Paketrahmen die Genauigkeitsgrenze einhält. Ein affiner
Wurzel-Transform, wie ihn projizierte Pakete (UTM) bräuchten, ist damit nicht zugelassen; darüber
wird mit dem ersten solchen Paket entschieden.

Der Messbericht liegt im Arbeitsarchiv.
