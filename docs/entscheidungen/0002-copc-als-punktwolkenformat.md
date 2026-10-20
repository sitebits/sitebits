# 0002 COPC als Punktwolkenformat

- **Status:** angenommen
- **Datum:** 2026-09-25

## Kontext

Punktwolken der Zielprojekte haben hunderte Millionen bis über eine Milliarde Punkte. Der Browser
muss eine erste Übersicht in wenigen Sekunden zeigen und beim Messen gezielt die feinste Stufe
eines kleinen Bereichs nachladen können, ohne eigenen Kachel-Server.

## Entscheidung

- Punktwolken werden beim Import in **Cloud Optimized Point Cloud (COPC)** umgewandelt: eine
  einzige LAZ-Datei mit Oktalbaum-Hierarchie, die per **HTTP-Range** abschnittsweise gelesen wird.
- Umwandlung mit PDAL (`writers.copc`), bei sehr großen Quellen gestaffelt über LAZ und untwine;
  beide laufen als externe Prozesse in Containern.
- Skala 1 mm, Versatz = `originWorld`; ungültige Punkte werden verworfen und gezählt.
- `verify` vergleicht deterministische Stichproben der Quelle mit dem COPC (≤ 1 mm) und prüft die
  Punktbilanz.

## Konsequenzen

- Kein eigener Dienst für Punktwolken: jeder Webserver mit korrekter Range-Unterstützung genügt.
  Kompression auf `.laz` ist abgeschaltet, sonst bricht Range.
- Der Viewer braucht einen LAZ-Decoder (WASM), der mit dem Build ausgeliefert wird.
- Die Zuordnung Punkt → Scan (`PointSourceId`) wird im COPC mitgeführt und ist nur mit
  bestandenem `verify` belastbar.
