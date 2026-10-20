# 0010 Release-Kette fail-closed

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Der Cyber Resilience Act verlangt für Produkte mit digitalen Elementen nachvollziehbare
Software-Stücklisten, Schwachstellenbehandlung und Nachweise über Jahre. Diese Nachweise sollen
nicht von Hand zusammengetragen werden, und ein Release ohne sie soll gar nicht erst entstehen.

## Entscheidung

- Ein Release entsteht aus einem **eingefrorenen Commit** in einem frischen Worktree; gebaut wird nur
  von dort.
- Stufen: Qualitätstor, **SBOM je Artefakt** (CycloneDX 1.6 nach BSI TR-03183-2),
  **Schwachstellen-Scan** (ein verbindlicher Scanner, Datenbank ≤ 24 h, im Container ohne Netz),
  Lizenzabgleich, **Befundregister-Gate**, öffentlicher Export, **signiertes Manifest** (SSH-Signatur),
  Archiv in einem eigenen, nur anhängenden Nachweis-Repo.
- **Jeder Fehler bricht ab**: Werkzeugfehler, veraltete Datenbank, fehlende oder unvollständige
  SBOM, Befund unbekannter Schwere (zählt als hoch), fehlende Signatur. Einen Scanner-Treffer ab
  „hoch“ gibt allein eine VEX-Aussage `fixed` oder `not_affected` frei. Eine Risikoakzeptanz
  (befristet, begründet, freigegeben im Befundregister) wirkt nur im Befund-Gate — sie gibt
  keinen Scanner-Treffer frei; das Scan-Gate ist damit bewusst strenger als die Spezifikation.
- Nach Archivierung und Tag entsteht ein eigener, signierter **Abschlussnachweis**; erst mit ihm
  gilt die Kette als vollständig.
- Deploy nur mit den Artefakten des Releases; das Deploy-Skript prüft Manifest- und
  Abschluss-Signatur und die Hashes gegen das signierte Manifest und protokolliert jede
  Aktualisierung.

## Konsequenzen

- Ein Release ist ohne vollständige Nachweise nicht möglich, auch nicht aus Versehen.
- Befunde, die nur nicht ausgelieferte Artefakte betreffen, werden im Release informativ geführt,
  sperren es aber nicht.
- Negativkontrollen belegen je Release, dass die Kette bei Fehlern tatsächlich abbricht.
