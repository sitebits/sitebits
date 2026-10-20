# 0016 Nur-Lese-Links für Gäste

- **Status:** angenommen
- **Datum:** 2026-10-15
- **Ergänzt:** [0005](0005-paketauslieferung-caddy-forward-auth.md) (dort verworfen: weitergebbare URLs)

## Kontext

Projektleitungen wollen ein Projekt Menschen ohne Konto zeigen, etwa einer Denkmalbehörde oder für eine
Vorführung. Einladung plus Passkey ist dafür eine zu hohe Hürde. ADR 0005 hatte signierte URLs verworfen,
weil sie weitergebbar und schwer zu widerrufen sind. Ein Gast-Link ist ebenfalls weitergebbar; die Frage
ist, wie er trotzdem eng und widerrufbar bleibt.

## Entscheidung

- **Kein signierter Daten-URL, sondern ein Austausch.** Der Link `/teilen#t=<Token>` (256 bit, im Fragment,
  nie in Server- oder Proxy-Logs) wird per POST gegen eine Gast-Sitzung im HttpOnly-Cookie
  `__Host-scanview-gast` getauscht. Gespeichert werden nur SHA-256-Hashes.
- **Jede Anfrage prüft neu:** Widerruf, Ablauf (Standard 14, höchstens 90 Tage) und den Projektschalter
  „Nur-Lese-Links erlaubt“ (Standard aus). Widerruf wirkt bei der nächsten Anfrage, wie ein Rechteentzug in 0005.
- **Ein Gast ist keine Rolle.** Die Projekt-API (`/api/v1`) kennt ihn nicht. `/api/authz` lässt ihn nur an
  eine **exakte Positivliste**: Punktwolke, Mesh-Dateien samt rekursiv aufgelöster glTF-Verweise und die
  Draufsicht der Wolke. Keine Präfixe. Ein Verweis, der das Paket verlässt oder kodiert ist, nimmt die
  Mesh-Ebene aus der Gast-Ansicht. Das rohe `project.json` bleibt gesperrt; der Viewer bekommt ein
  bereinigtes Manifest von `/api/teilen/manifest`.
- **Optionales Link-Passwort:** bewusst einfach (ab 4 Zeichen), argon2, Sperre nach 5 Fehlversuchen je Link.
- **Drosseln:** Austausch je IP und global, Abrufe je Link und Minute; Mehrfach-Range für Gäste abgelehnt.
- **Kein Zwischenspeicher:** Caddy setzt für Anfragen mit Gast-Cookie `Cache-Control: no-store` auch auf
  Binärdaten, sonst zeigte der Browser nach einem Widerruf noch eine Stunde lang Daten.
- Anlegen nur durch Projektleitung oder Admin, im Browser und mit frischer Sitzung (wie Passkey und API-Token).

## Konsequenzen

- Was ein Gast im Browser sieht, ist technisch kopierbar; es gibt nur keine Download- oder Exportfunktion.
  Das steht so im Teilen-Abschnitt der Projekt-Einstellungen.
- Nachvollziehbar ist die Freigabe, nicht die Person: je Link Anzahl und Zeit der Öffnungen, Anlegen und
  Widerruf im Verlauf des Projekts.
- Wer den Link hat, hat bis zum Widerruf Zugriff. Das Passwort hilft nur gegen versehentliches Weiterreichen.
- **KI-Nutzung:** bewusst nur im Browser. Liste, Anlegen und Widerruf laufen über die Sitzung, nicht über
  API-Token oder MCP. Ein Link öffnet ein Projekt für Menschen ohne Konto; diese Entscheidung trifft ein
  Mensch, wie bei Passkey und API-Token. Agenten sehen Anlegen und Widerruf im Verlauf des Projekts.
- Neue Ebenenarten (Splats, Mesh-Kacheln F29) erscheinen für Gäste erst, wenn `teilen.py` ihre Dateien in
  die Positivliste aufnimmt.

## Bewusste Grenzen (Sicherheitsreview 15.10.)

- **Parallelität je Link:** umgesetzt sind Abrufe je Link und Minute und die Sperre von Mehrfach-Range. Eine Grenze
  gleichzeitiger Verbindungen gibt es nicht; der Viewer ist dabei kaum messbar, eine harte Grenze gehört in Caddy und
  kommt erst, wenn Missbrauch beobachtet wird.
- **Unzulässige Verweise im Paket:** Ausbrechende, kodierte oder auf Fotopfade zeigende Verweise nimmt die Plattform zur Laufzeit
  aus der Gast-Ansicht (fail closed, Warnung im Log). Der Import lehnt solche Pakete noch nicht ab — nachziehen, wenn
  der Upload im Browser kommt.
- Die Positivliste wird nach `project.json` (Änderungszeit, Größe) zwischengespeichert. Ein Paket wird immer als Ganzes
  ersetzt (`paket-bereitstellen.sh`), dabei ändert sich `project.json` mit.

