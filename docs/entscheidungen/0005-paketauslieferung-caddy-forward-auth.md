# 0005 Paketauslieferung durch Caddy mit forward_auth

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Projektpakete sind mehrere Gigabyte groß und werden per HTTP-Range gelesen. Jede Anfrage muss
trotzdem prüfen, ob die Sitzung das Projekt sehen darf, und ein Rechteentzug muss sofort wirken.

## Entscheidung

- **Caddy liefert die Paketdateien direkt aus** (`file_server`, Range, sendfile); der Plattform-Server
  fasst große Dateien nie an.
- Für jede Anfrage unter `/data/` fragt Caddy per **`forward_auth`** beim Server nach. Der Server
  prüft Projektkennung, Pfad (keine Pfadtricks, nur bekannte Dateiarten) und Recht.
- Ohne gültige Sitzung → 401 als JSON; ohne Recht → 404.
- Pakete liegen ohne Symlinks unter einer nur lesbaren Paketwurzel; ausgeliefert werden sie als
  Daten (`sandbox`-CSP, `nosniff`), ohne Kompression.
- Verworfen: signierte URLs (weitergebbar, Widerruf schwer).
- Zuerst ohne Cache gemessen; ein kurzer Cache der Rechteentscheidung nur, wenn die Messung ihn
  verlangt.

## Konsequenzen

- Rechteentzug und Abmelden wirken bei der nächsten Anfrage.
- Caddy ist Teil der Sicherheitsgrenze; seine Konfiguration liegt im Repo
  (`deploy/platform/`) und wird mit einem eigenen Prüfskript getestet.
- Die interne Route `/api/authz` ist von außen gesperrt.
