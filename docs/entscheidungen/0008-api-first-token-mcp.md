# 0008 API-first mit persönlichen Tokens und MCP-Server

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Die Plattform soll sich nicht nur über das Portal, sondern auch aus Skripten und KI-Assistenten
steuern lassen, ohne dass dafür eigene Rechte- oder Datenwege entstehen.

## Entscheidung

- Jede Funktion von Portal und Viewer ist eine dokumentierte Route unter **`/api/v1/…`**; die
  OpenAPI-Beschreibung ist für angemeldete Nutzer abrufbar und die Quelle der Wahrheit.
- **Persönliche API-Tokens** (`sv_…`): Name, Ablauf ≤ 1 Jahr, Umfang lesen oder lesen+schreiben,
  einmal angezeigt, als Hash gespeichert, gedrosselt, widerrufbar. Sie gelten nur für `/api/v1/*`,
  nicht für Paketdateien, Verwaltung oder Konto; Tokens können keine Tokens erzeugen.
- Schreibende Token-Nutzung erscheint im Bearbeitungsprotokoll (Kanal `api`, Token-Name); lesende
  Nutzung nur als „zuletzt benutzt“.
- Ein **MCP-Server** läuft beim Nutzer (stdio) mit einem solchen Token; seine Werkzeuge sind von
  Hand geschrieben und werden per Test gegen die OpenAPI geprüft. Ab Werk gibt er Namen anderer
  Konten nur pseudonymisiert und keine Freitexte an das Sprachmodell weiter.

## Konsequenzen

- Kein zweiter Rechteweg: Skripte und Assistenten sehen genau, was das Konto sieht.
- Neue API-Funktionen brauchen ein neues MCP-Werkzeug von Hand.
- Wer ein Cloud-Sprachmodell nutzt, gibt Werkzeugergebnisse an dessen Anbieter; volle Daten nur nach
  ausdrücklicher Einstellung des Verantwortlichen.
