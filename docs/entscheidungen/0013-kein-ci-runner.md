# 0013 Kein CI-Runner

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Ein CI-Runner führt Code aus dem Repository auf eigener Infrastruktur aus und braucht dafür
Zugangsdaten. Entwickelt wird von einer Person, externe Beiträge werden derzeit nicht übernommen.

## Entscheidung

- **Kein CI-Runner** und keine andere CI-Ausführung.
- Ersatz ist die lokale, fail-closed Release-Kette ([ADR 0010](0010-release-kette-fail-closed.md)),
  die Veröffentlichung und Deploy verhindert, wenn Nachweise fehlen.

## Konsequenzen

- Keine zusätzliche Angriffsfläche durch einen Runner mit Zugangsdaten.
- Qualitätstore laufen lokal vor jedem Commit und im Release; es gibt keine automatische Prüfung
  eingehender Änderungen.
- Neu zu bewerten, sobald fremde Beiträge angenommen werden oder das öffentliche Repository aktiv
  genutzt wird.
