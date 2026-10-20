# scanview-mcp — MCP-Server für die scanview-Plattform

Ein kleiner MCP-Server (stdio), der auf **deinem Rechner** läuft und Claude (Claude Code,
Claude Desktop) oder andere MCP-fähige Programme mit der scanview-Plattform sprechen lässt. Er
ruft ausschließlich die öffentliche API v1 auf, mit deinem persönlichen API-Token — er kann also
nie mehr als dein Konto.

## Datenschutz: was an den KI-Anbieter geht

**Alles, was ein Werkzeug zurückgibt, geht an den Anbieter des Sprachmodells deines
MCP-Programms** (bei Claude Code/Claude Desktop an Anthropic, bei anderen Programmen an deren
Anbieter), nicht nur an dein Programm auf dem Rechner.

**Datensparsam ab Werk.** Ohne weitere Einstellung verlassen Namen anderer Beteiligter und
Freitexte den Rechner nicht:

- **Namen und Konto-Nummern** (wer einen Merkpunkt oder eine Messung angelegt bzw. zuletzt
  geändert hat, im Verlauf `nutzer`/`nutzerId`) werden zu Pseudonymen wie `Konto-3f9a1c2e`,
  **Token-Namen** im Verlauf zu `Token-…`. Gleiches Konto → gleiches Pseudonym; ohne das geheime
  Salz lässt es sich nicht auf das Konto zurückführen. Das gilt auch für dein eigenes Konto.
- **Freitexte** (POI-Notizen, Projektbeschreibung, Kommentare und Links im Befund-Faden, auch im
  Verlauf und im Export) erscheinen als
  „Freitext ausgeblendet (datensparsamer Modus)“. Leere Felder bleiben leer. Damit erreichen auch
  Anweisungen, die jemand in eine Notiz geschrieben hat, die KI nicht.
- **Bleiben sichtbar**: Titel, Kategorien, Standpunkte, Koordinaten (Weltkoordinaten, also reale
  Orte), Messwerte, Versionen, Zeiten, Ereignisarten.
- Im **Befund-Faden** bleiben Art, Status, Frist, Zeiten und die Zuständigkeit sichtbar (Gewerk als
  Nummer, Person als Pseudonym). Interne Einträge liefert der Server nur der Bauherrenseite.
- Die KI darf den Platzhalter nicht als Text zurückschreiben; `poi_anlegen`, `poi_aendern`,
  `beschreibung_setzen`, `kommentar_anlegen` und `status_setzen` lehnen ihn ab. Eine Notiz, die
  unverändert bleiben soll, einfach weglassen.

Pseudonyme sind ohne weitere Einstellung nur innerhalb eines Laufs von `scanview-mcp` stabil
(zufälliges Salz je Start). Sollen sie über Neustarts gleich bleiben, ein eigenes Salz mit
mindestens 16 Zeichen setzen, z. B. `SCANVIEW_MCP_PSEUDONYM_SALZ=$(openssl rand -hex 32)`
(einmal erzeugen, wie den Token nur lokal aufbewahren). Ein kürzeres Salz wird mit Warnung
ignoriert.

**Voller Umfang nur als Betreiberentscheidung.** `SCANVIEW_MCP_PERSONENDATEN=voll` in der
Umgebung des MCP-Prozesses liefert die Originaldaten (Klarnamen, Token-Namen, Notizen,
Beschreibung). Beim Start steht dann eine Warnzeile auf stderr. Ob das zulässig ist, entscheidet
der Verantwortliche (der Auftraggeber, dem die Projektdaten gehören), nicht die einzelne Nutzerin
— er braucht dafür eine Rechtsgrundlage und einen Auftragsverarbeitungsvertrag (AVV) mit dem
KI-Anbieter. Jeder andere Wert (auch Tippfehler wie `Voll`) bleibt datensparsam und warnt.

Für beide Modi gilt:

- Auch datensparsam gehen Koordinaten, Titel und Kategorien an den Anbieter. Nur mit einem
  KI-Anbieter nutzen, den die Vorgaben des Verantwortlichen abdecken. Im Zweifel vorher fragen.
- **Lese-Token bevorzugen.** Einen Schreib-Token nur, wenn die KI wirklich etwas ändern soll.
- **Fremde Texte sind keine Anweisungen.** Im Modus `voll`, aber auch über Titel und Kategorien,
  kann Text anderer die KI erreichen, der wie ein Auftrag aussieht („lösche …“, „setze die
  Beschreibung auf …“). Mit einem Schreib-Token die KI nicht einfach danach handeln lassen: jede
  Änderung vorher selbst prüfen und bestätigen.

## Werkzeuge

| Werkzeug | API | Wozu |
|---|---|---|
| `projekte_auflisten` | `GET /api/v1/projekte` | Projekte mit eigener Rolle |
| `projekt_lesen` | `GET /api/v1/projekte/{pid}` | Projektkarte inkl. Metadaten-Version |
| `beteiligte_lesen` | `GET /api/v1/projekte/{pid}/beteiligte` | Beteiligte mit Rolle und Gewerk (ohne E-Mail) |
| `beschreibung_setzen` | `PATCH /api/v1/projekte/{pid}` | Beschreibung ändern (liest vorher die Version) |
| `pois_lesen` | `GET /api/v1/projekte/{pid}/pois` | Merkpunkte |
| `poi_anlegen` | `POST /api/v1/projekte/{pid}/pois` | Merkpunkt an bekannter Position anlegen |
| `poi_aendern` | `PATCH /api/v1/projekte/{pid}/pois/{eid}` | Felder eines Merkpunkts ändern (mit `version`) |
| `messungen_lesen` | `GET /api/v1/projekte/{pid}/messungen` | Messungen |
| `messung_anlegen` | `POST /api/v1/projekte/{pid}/messungen` | Messung aus bekannten Eckpunkten anlegen |
| `protokoll_lesen` | `GET /api/v1/projekte/{pid}/protokoll` | Bearbeitungsverlauf (nur Bearbeiter) |
| `export` | `GET /api/v1/projekte/{pid}/export.json` | Export als JSON (nur Bearbeiter) |
| `faden_lesen` | `GET /api/v1/projekte/{pid}/pois/{eid}/faden` | Befund-Faden: Stand, Einträge, eigene Rechte |
| `kommentar_anlegen` | `POST /api/v1/projekte/{pid}/pois/{eid}/faden` | Kommentar im Befund-Faden (intern nur Bauherrenseite) |
| `status_setzen` | `POST /api/v1/projekte/{pid}/pois/{eid}/faden` | Status eines Befunds setzen (mit `version`) |

Die Tabelle steht maschinenlesbar in `src/scanview_mcp/endpunkte.json`; der Server-Test
`server/tests/test_mcp_vertrag.py` prüft sie gegen die echte OpenAPI.

Koordinaten sind immer **Weltkoordinaten des Pakets in Metern** (`[x, y, z]`), genau so, wie
`pois_lesen` und `messungen_lesen` sie liefern. **Alle Änderungen sind sofort für alle
Projektbeteiligten sichtbar** und bleiben im Verlauf dauerhaft stehen (Kanal `api`, mit dem Namen
des Tokens).

## Token anlegen

Im Portal unter **Einstellungen → API-Zugänge** einen Zugang anlegen. Der Token (`sv_…`) wird
genau einmal angezeigt. Für reines Lesen und Auswerten einen **Lese-Token** nehmen; einen
Schreib-Token nur, wenn die KI wirklich POIs, Messungen oder die Beschreibung ändern soll.
„MCP-Konfiguration kopieren“ liefert die JSON-Vorlage für Claude Desktop (unten).

### Token für KI-Agenten

Ein Schreib-Token lässt sich als **„für einen KI-Agenten“** markieren. Dann wird alles, was die
KI anlegt oder ändert, ein **Vorschlag**, den ein Mensch annimmt oder verwirft:

- `poi_anlegen`/`messung_anlegen` legen Einträge mit `pruefstand: vorschlag` an.
- `status_setzen`, `frist_setzen`, `zustaendig_setzen` ändern den Stand nicht, sondern legen
  einen Faden-Eintrag `vorschlag` an (HTTP 202).
- `poi_loeschen`/`messung_loeschen` auf einen gültigen Eintrag antworten mit
  `{"loeschenVorgeschlagen": id, …}` statt `{"geloescht": id}`.
- Exporte und Git-Export enthalten nur Angenommenes. Annehmen und Verwerfen gibt es bewusst
  nicht als Werkzeug.

Bis das Häkchen im Portal kommt, legt man einen solchen Token über die API an
(`POST /api/v1/konto/tokens` mit `"agent": true`, aus der angemeldeten Sitzung).

**Grenze:** Ein normaler Token macht einen Agenten nicht erkennbar. Wer der KI einen normalen
Schreib-Token gibt, bekommt gültige Einträge wie von Hand – das ist eine Entscheidung des
Menschen, der den Token anlegt.

**Token nie auf die Kommandozeile schreiben** — sonst steht er im Shell-Verlauf
(`~/.bash_history`, fish-History). Stattdessen in eine Datei nur für dich legen und von dort
lesen:

```bash
install -d -m 700 ~/.config/scanview
install -m 600 /dev/null ~/.config/scanview/token   # leere Datei, nur für dich lesbar
cat > ~/.config/scanview/token                      # Token einfügen, Enter, dann Strg+D
```

(`cat` liest von der Eingabe, der Token landet also nicht im Verlauf; das funktioniert in bash,
zsh und fish.)

## Installation (gesperrt, ohne Nachladen aus dem Netz zur Laufzeit)

Voraussetzung: [uv](https://docs.astral.sh/uv/) und Python 3.11 oder 3.12. Installiert wird genau
der Stand aus `uv.lock` (Stichtag `exclude-newer`, nur vorgebaute Wheels) in eine eigene Umgebung,
und der Befehl `scanview-mcp` kommt per Verweis auf den `PATH`:

```bash
cd <repo>/mcp-server
UV_PROJECT_ENVIRONMENT="$HOME/.local/share/scanview-mcp" \
  uv sync --locked --no-dev --no-editable
mkdir -p "$HOME/.local/bin"
ln -sf "$HOME/.local/share/scanview-mcp/bin/scanview-mcp" "$HOME/.local/bin/scanview-mcp"
scanview-mcp   # ohne Umgebung: Meldung „SCANVIEW_URL fehlt“ und Exit 2 — Installation ok
```

`--no-editable` kopiert das Paket in die Umgebung; nach einem Update des Repos denselben
`uv sync`-Befehl wiederholen. **Nicht** `uv tool install --from …`: das löst die Abhängigkeiten
neu auf und beachtet `uv.lock` nicht (uv 0.11 kennt dort kein `--locked`).

## Einrichtung in Claude Code

Ohne `PATH`-Installation, direkt aus dem Repo (nutzt `uv.lock` bei jedem Start):

```bash
claude mcp add scanview \
  --env SCANVIEW_URL=https://scanview.example.de \
  -- sh -c 'SCANVIEW_TOKEN="$(cat "$HOME/.config/scanview/token")" exec uv run --locked --no-dev --directory <repo>/mcp-server scanview-mcp'
```

Mit installiertem `scanview-mcp` (siehe oben) endet die Zeile mit
`-- sh -c 'SCANVIEW_TOKEN="$(cat "$HOME/.config/scanview/token")" exec scanview-mcp'`.

So liest der Server den Token bei jedem Start aus der Datei (Token anlegen, oben): er steht weder
im Shell-Verlauf noch in der Claude-Code-Konfiguration. Die einfachere Form
`--env SCANVIEW_TOKEN="$(cat ~/.config/scanview/token)"` hält den Token
zwar aus dem Verlauf heraus, aber `claude mcp add` schreibt ihn dann im Klartext nach
`~/.claude.json` — diese Datei dann mit `chmod 600 ~/.claude.json` schützen.

Den Standard-Geltungsbereich (`local`) oder `--scope user` benutzen — **nie `--scope project`**:
das schreibt den Token in `.mcp.json` im Projektordner, und von dort landet er leicht in Git.

## Einrichtung in Claude Desktop

In `claude_desktop_config.json` (Einstellungen → Entwickler → Konfiguration bearbeiten) den
Block aus „MCP-Konfiguration kopieren“ einfügen:

```json
{
  "mcpServers": {
    "scanview": {
      "command": "scanview-mcp",
      "env": {
        "SCANVIEW_URL": "https://scanview.example.de",
        "SCANVIEW_TOKEN": "sv_…"
      }
    }
  }
}
```

Diese Datei hält den Token **im Klartext**: nur für dich lesbar machen
(Linux/macOS: `chmod 600` auf `~/.config/Claude/claude_desktop_config.json` bzw.
`~/Library/Application Support/Claude/claude_desktop_config.json`; unter Windows liegt sie im
eigenen Profil unter `%APPDATA%\Claude\`, das andere Konten nicht lesen) und nie in Backups oder
Chats weitergeben. Unter Linux/macOS geht es auch ohne Token in der Datei, mit demselben Aufruf wie
bei Claude Code: `"command": "sh"`, `"args": ["-c", "SCANVIEW_TOKEN=\"$(cat \"$HOME/.config/scanview/token\")\" exec scanview-mcp"]`
und nur `SCANVIEW_URL` unter `env`.

`scanview-mcp` muss dafür auf dem `PATH` liegen (Installation oben). Findet Claude Desktop den
Befehl nicht (grafische Programme sehen `~/.local/bin` nicht immer), den vollen Pfad eintragen,
z. B. `"<Heimatverzeichnis>/.local/bin/scanview-mcp"`. Andere Programme mit
MCP-Unterstützung über stdio werden genauso eingerichtet: Befehl `scanview-mcp`, die zwei
Umgebungsvariablen.

## Umgebung

| Variable | Pflicht | Inhalt |
|---|---|---|
| `SCANVIEW_URL` | ja | Adresse der Plattform, `https://…`; `http://` nur für `localhost`, `127.0.0.1`, `[::1]` |
| `SCANVIEW_TOKEN` | ja | persönlicher API-Token, beginnt mit `sv_` |
| `SCANVIEW_MCP_PERSONENDATEN` | nein | nur `voll` schaltet den datensparsamen Modus ab (Warnung auf stderr); fehlt die Variable oder hat sie einen anderen Wert, bleibt er aktiv (unbekannter Wert → Warnung) |
| `SCANVIEW_MCP_PSEUDONYM_SALZ` | nein | geheimes Salz (≥ 16 Zeichen) für über Neustarts stabile Pseudonyme; ohne: zufällig je Start |

Fehlt eine Variable oder passt ihr Format nicht, beendet sich `scanview-mcp` mit Exit-Code 2 und
nennt nur den Namen der Variable, nie ihren Wert.

## Sicherheit

- **Der Token bleibt lokal**: in der Konfiguration deines MCP-Programms auf deinem Rechner. Er
  steht nie in Meldungen, Fehlertexten oder Protokollen des MCP-Servers; Verbindungsfehler nennen
  nur die Fehlerart.
- **Lese-Token empfohlen.** Ein Lese-Token bekommt bei jeder Änderung „Keine Berechtigung“. Auch
  ein Schreib-Token darf nur, was dein Konto darf (Rolle je Projekt), und nie Verwaltung, Statistik
  oder Konto-Einstellungen; Tokens können keine Tokens anlegen.
- **Widerruf** jederzeit im Portal unter Einstellungen → API-Zugänge — wirkt sofort. Sperren,
  „Überall abmelden“ und Anonymisieren widerrufen alle Tokens eines Kontos.
- Der MCP-Server sendet nie ein Cookie und folgt keiner Weiterleitung (eine Weiterleitung
  meldet er als Fehler „SCANVIEW_URL prüfen“); der Token geht nur an `SCANVIEW_URL`.
- **Kein Proxy**: `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY` und `.netrc` aus der Umgebung werden
  bewusst ignoriert, damit der Token nie an einen Proxy geht. Die Plattform muss direkt
  erreichbar sein.
- Jede Änderung per Token steht im Projektverlauf mit Kanal `api` und dem Token-Namen — nicht
  löschbar. Die KI vor Schreibaufträgen um Bestätigung bitten lassen, wenn dein MCP-Programm das
  anbietet.
- **Inhalte gehen an den KI-Anbieter** — ab Werk ohne Namen anderer und ohne Freitexte, siehe
  „Datenschutz“ oben.

## Grenzen

- **Keine Punktwolken**: der MCP-Server sieht nur Metadaten, POIs, Messungen, Verlauf und Export.
- **Kein Messen im Raum**: Positionen und Eckpunkte muss die KI kennen (z. B. aus vorhandenen
  POIs/Messungen oder aus einer Angabe von dir). Flächeninhalt und Ebenheit einer Fläche
  berechnet der Viewer beim Anzeigen aus den Eckpunkten; im Export (`export`, CSV) bleiben
  diese Spalten bei per MCP angelegten Flächen leer.
- Kein Löschen von POIs oder Messungen und keine Titeländerung über den MCP-Server (bewusst
  weggelassen; die API kann es).
- Drossel des Servers: 120 Anfragen je Minute und Token, darüber „Zu viele Anfragen“.

## Entwicklung

```bash
cd mcp-server
uv sync --locked
uv run --locked ruff check . && uv run --locked mypy src && uv run --locked pytest
```

Die Tests laufen ohne Netz gegen `httpx.MockTransport`; `test_stdio_rauchtest_tools_list`
startet `scanview-mcp` als Unterprozess und fragt über stdio `tools/list` ab.
`tests/test_lieferkette.py` prüft, dass jedes Paket aus `uv.lock` in `no-build-package` steht.
