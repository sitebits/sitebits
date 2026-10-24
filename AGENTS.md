# Hinweise für KI-Agenten

Gilt für alle automatisierten Beiträge zu diesem Repository. Bei Widerspruch gilt der Auftrag des
Menschen, dann diese Datei, dann `CONTRIBUTING.md`.

## Grundsätze
- Nichts behaupten, was nicht gemessen ist: Testläufe, Zahlen und Messwerte nur mit Befehl und
  Ausgabe. „Sollte funktionieren“ ist kein Ergebnis.
- Prüfer nicht umgehen: keine `skip`/`xfail`/`.only`, kein `# noqa`/`eslint-disable`/`type: ignore`
  ohne Begründung im selben Kommentar, keine Änderung an Basis- oder Unterdrückungsdateien
  (`eslint-suppressions.json`, `*/ruff-basis.json`) außer zum Senken.
- Keine Tests, die nur Konstanten mit sich selbst vergleichen.
- Keine neuen Abhängigkeiten ohne Auftrag; Lizenzen nur permissiv (siehe `docs/THIRD_PARTY.md`).

## Vor jedem Commit (Qualitätstor)
- `web/`: `npm run typecheck && npm run lint && npm test`
- `clients/sitebits/`: `npm run typecheck && npm run lint && npm test` (nach API-Änderungen vorher `npm run typen`)
- je Python-Teil (`server/`, `mcp-server/`, `nachweise/`, `import/`):
  `uv run --locked ruff check --ignore BLE,C90,ASYNC . && uv run --locked mypy src && uv run --locked pytest -q`
- Ratsche: `cd nachweise && uv run --locked scanview-nachweise pruefen ratsche`
- Server-Tests mit Marker `oom_live` (Unit endet mit `Result=oom-kill`, KDE meldet das) sind lokal übersprungen; sie laufen nur mit `SCANVIEW_OOM_LIVE=1`, das die Release-Kette (Stufe `qualitaetstor`) verpflichtend setzt.

## Git
- Signierte Commits (`git commit -S`), ein Commit je Aufgabe, sachlicher Betreff.
- Nie auf `main` pushen, nie mergen, rebasen oder force-pushen — Merge entscheidet ein Mensch.

## Inhalt
- Keine Arbeitsnotizen (Übergaben, Eingangslisten, Protokolle, Rohpläne) ins Repository.
- Test-Fixtures nur synthetisch; keine Kunden-, Projekt-, Host- oder Personennamen in Code,
  Tests, Commits oder Doku — der Export prüft das und bricht ab.
- Content-Security-Policy, Anmeldung, Rollen und Berechtigungen nur mit ausdrücklichem Auftrag ändern.
