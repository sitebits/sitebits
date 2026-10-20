# 0012 Öffentlicher Export über Positivliste

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Der Quelltext soll öffentlich werden. Das Entwicklungs-Repository enthält aber auch Betriebsdetails
einzelner Installationen, Details aus Sicherheitsprüfungen und Compliance-Aufzeichnungen, die nicht
öffentlich sein sollen. Ein Umschreiben der Historie ist fehleranfällig und müsste für jede künftige
Datei wiederholt werden.

## Entscheidung

- Das Entwicklungs-Repository bleibt privat und kanonisch. Öffentlich ist ein **eigenes, dauerhaftes
  Repository** mit einem Commit je Release und je Sicherheits-Hotfix; keine gemeinsamen Refs.
- Der Export entsteht **nur aus den Git-Blobs eines Commits**, ausgewählt über
  `veroeffentlichung/positivliste.txt`. Symlinks, Submodule und Arbeitsnotizen brechen ab, auch wenn
  ein Muster sie erfassen würde.
- Prüfungen des ganzen Pakets: Binärdateien nur mit freigegebenem Hash, Geheimnissuche, Suche nach
  verbotenen Begriffen (Kunden-, Projekt-, Host-, Nutzernamen; Liste außerhalb des Repos), Verweise
  auf private Dokumente, und der Abzug muss für sich allein bauen und seine Tests bestehen.
- Jede Änderung gegenüber dem vorigen Export erscheint als Release-Diff und wird ausdrücklich
  bestätigt. Solange `SECURITY.md` die Meldestelle für Schwachstellen als noch nicht eingerichtet
  markiert, bricht die eigenständige Veröffentlichung ab; in der Release-Kette wird der Export als
  nicht veröffentlichbar vermerkt.

## Konsequenzen

- Neue Dateien werden nur öffentlich, wenn die Positivliste sie ausdrücklich aufnimmt.
- Arbeitsnotizen gehören nicht ins Entwicklungs-Repository (siehe `CONTRIBUTING.md`); die Prüfung
  im Export ist die zweite Sicherung.
- Das Import-Werkzeug ist vorerst nicht Teil des Exports.
