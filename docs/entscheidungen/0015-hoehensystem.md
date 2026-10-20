# 0015 Höhensystem: NHN als Standard, Umrechnung im Import

- **Status:** angenommen
- **Datum:** 2026-10-09

## Kontext

Bisher benennt der Import das Höhensystem eines Pakets nur (`frame.crs.verticalDatum`), rechnet aber
nicht um. Daten aus verschiedenen Quellen passen dadurch in der Höhe nicht zusammen: Photogrammetrie
aus Metashape liefert oft ellipsoidische Höhen (ETRS89/GRS80), amtliche Daten in Deutschland
Normalhöhen über NHN im DHHN2016. Der Unterschied ist die Quasigeoidhöhe, in Brandenburg rund 40 m.
Viele Quelldateien deklarieren gar kein Höhensystem (LAS/LAZ ohne vertikales CRS, E57).

PROJ wählt bei fehlender Gitterdatei still eine Näherung ohne Höhenkorrektur („ballpark“). Die Höhe
bleibt dann unverändert, ohne Fehlermeldung — auch mit `only_best`. Eine Umrechnung über die
automatische Transformationsauswahl ist deshalb nicht sicher.

## Entscheidung

- **Ein Höhensystem je Paket**, festgelegt beim ersten Import. **Standard ist DHHN2016 (NHN)**;
  ellipsoidisch (ETRS89) ist wählbar.
- Geschrieben werden nur die Werte `dhhn2016`, `ellipsoidal-etrs89` und `dhhn92`. Gelesen wird
  weiterhin jeder Text und `null`; bekannte ältere Schreibweisen werden zugeordnet, unbekannte
  angezeigt, aber nie umgerechnet.
- **Quell- und Zielhöhensystem sind getrennt.** Deklariert eine Eingabedatei nichts, muss ihr
  Höhensystem beim Import ausdrücklich angegeben werden; es gibt keine Annahme. Beim Ergänzen eines
  Pakets bestimmt dessen Rahmen das Ziel.
- Der Import rechnet um, wenn Quelle und Ziel verschieden sind, und zwar nur über eine **explizite
  PROJ-Pipeline** mit dem Quasigeoid GCG2016 des BKG (`de_bkg_gcg2016.tif`). Fehlt die Gitterdatei
  oder liegt ein Punkt außerhalb des Gitters, bricht der Import ab.
- Umgerechnet wird nur bei Quell-CRS mit bekanntem ETRS89-Bezug. Lokale Rahmen ohne geodätische
  Zuordnung bekommen keine Umrechnung und kein NHN, ihr Höhensystem bleibt unbekannt.
- Die Gitterdatei (rund 1 MB, CC BY 4.0, © Bundesamt für Kartographie und Geodäsie) wird mit
  fester Prüfsumme mitgeliefert. Zur Laufzeit gibt es keinen Netzzugriff auf Gitterdateien.

## Konsequenzen

- Bestehende Pakete bleiben gültig. Ihr Höhensystem wird beim nächsten Import normiert.
- Viewer, Messwerte und Koordinatenanzeige nennen das Höhensystem des Pakets.
- Umrechnungen werden an Prüfpunkten gegen den Online-Rechner des BKG belegt (≤ 1 cm).
- DHHN92 wird nur gelesen bzw. umgerechnet, wenn ein Altbestand es verlangt.
- Die Namensnennung des BKG steht in `CREDITS.md` und `REUSE.toml`.
