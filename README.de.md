# siteBits

[English](README.md) · **Deutsch**

Quelltext: [GitHub](https://github.com/sitebits/sitebits) · [Issues](https://github.com/sitebits/sitebits/issues) · [Releases](https://github.com/sitebits/sitebits/releases)

siteBits zeigt Laserscans im Browser: Punktwolken, Panoramen der Scan-Standpunkte, Messungen und
Merkpunkte, bereitgestellt über eine selbst betriebene Plattform mit Einladung, Anmeldung per
Passkey und Rechten je Projekt. Scandaten werden einmal zu Projektpaketen aufbereitet und danach
ohne Zusatzsoftware im Browser geöffnet.

Bezeichner im Code (Pakete, Befehle, Umgebungsvariablen, Pfade) tragen weiter den Arbeitsnamen
`scanview`.

## Funktionen

- **Viewer:** Punktwolken im COPC-Format mit Detailstufen, Sprung zwischen Standpunkten mit
  Panorama, Messen (Strecke, Höhe, Fläche, Polylinie), Merkpunkte mit Notizen, Draufsicht und
  Minikarte.
- **Plattform:** Konten nur per Einladung, Passkey-Anmeldung (Passwort als Rückfallweg), Rollen
  Betrachter, Rückmelder und Bearbeiter je Projekt, Verwaltung im Portal, Bearbeitungsverlauf, Export als CSV
  und JSON.
- **API und Werkzeuge:** REST-API unter `/api/v1/…` mit OpenAPI-Beschreibung, persönliche
  API-Tokens und ein MCP-Server, über den KI-Assistenten mit den Rechten des eigenen Kontos
  arbeiten können.

## Aufbau

```
Browser ──HTTPS──> Caddy ──┬─ /api, /auth ──────> scanview-server (Python, SQLite, nur Loopback)
                           ├─ /data/<projekt> ──> Zugriffsprüfung je Datei, dann Auslieferung
                           └─ Portal, Viewer ───> statisches Web-Bundle
MCP-Server (beim Nutzer) ──HTTPS + API-Token──> /api/v1
```

| Verzeichnis | Inhalt |
|---|---|
| `web/` | Viewer und Portal (TypeScript, Vite, Three.js/Giro3D) |
| `server/` | Plattform-Server (Python, FastAPI, SQLite) |
| `mcp-server/` | MCP-Server für KI-Assistenten (Python) |
| `deploy/platform/` | Einrichtung und Betrieb: systemd-Units, Caddy-Konfiguration, Skripte |
| `nachweise/` | Release-Kette: SBOM, Schwachstellen-Scan, Signatur, öffentlicher Export |
| `docs/` | [Architektur](docs/architektur.md) und [Entscheidungen](docs/entscheidungen/README.md) (ADR) |
| `docs/public/` | Produktdokumente (Sicherheit, Betrieb, Support, Release-Prozess) |

Einzelheiten zu Komponenten, Paketformat und Datenflüssen: [Architektur](docs/architektur.md);
Vertrauensgrenzen und Bedrohungen: [Sicherheitsarchitektur](docs/public/sicherheitsarchitektur.md);
Begründungen der tragenden Entscheidungen: [Entscheidungen](docs/entscheidungen/README.md).

## Schnellstart (Entwicklung)

Voraussetzungen: Node.js ≥ 22.12 mit npm, Python 3.11 oder 3.12, [uv](https://docs.astral.sh/uv/).

```bash
# Viewer und Portal (von der Repo-Wurzel aus)
cd web
npm ci
npm run typecheck && npm run lint && npm test
npm run dev                      # http://localhost:5173

# Plattform-Server (zweites Terminal); Pakete unter <datenordner>/<projectId>/
cd server
uv sync --locked
uv run pytest
SCANVIEW_DATA=<datenordner> SCANVIEW_STATE=<zustandsordner> \
SCANVIEW_ORIGIN=http://localhost:5173 SCANVIEW_RP_ID=localhost \
  uv run scanview-server serve    # 127.0.0.1:8803, der Vite-Devserver leitet /api und /auth weiter
SCANVIEW_DATA=… SCANVIEW_STATE=… SCANVIEW_ORIGIN=… SCANVIEW_RP_ID=… \
  uv run scanview-server einladen --name Test --email test@example.org --admin

# MCP-Server und Nachweiskette (von der Repo-Wurzel aus)
(cd mcp-server && uv sync --locked && uv run pytest)
(cd nachweise && uv sync --locked && uv run pytest)
```

Punktwolken und Metashape-Panoramen importieren: [Import-Anleitung](import/README.md).

Mesh-Ebenen bedienen: [Ebenen, Rahmung und Grenzen](docs/public/mesh-ebenen.md).

Einrichtung auf einem Server: [Betriebsanleitung](docs/public/betrieb.md).

## Angaben nach Anhang II des Cyber Resilience Act

| Angabe | |
|---|---|
| **Hersteller** | Stiebitz Souverain Systems UG (haftungsbeschränkt), Potsdam, Amtsgericht Potsdam HRB 42359 P. Die Postanschrift wird mit dem Inverkehrbringen ergänzt; bis dahin über die Kontaktstelle. |
| **Kontaktstelle für Schwachstellen** | security@sitebits.de, Meldeweg und Fristen in [`SECURITY.md`](SECURITY.md) |
| **Produkt** | siteBits, Version 0.10.3 (Viewer/Portal, Plattform-Server, MCP-Server tragen dieselbe Version; die installierte Version zeigt `GET /api/v1/version` angemeldeten Nutzern) |
| **Bestimmungsgemäße Verwendung** | Anzeige, Vermessung und gemeinsame Bearbeitung von Laserscan-Projekten im Browser auf einer selbst betriebenen Plattform, zugänglich nur für eingeladene Nutzer mit Recht auf das jeweilige Projekt |
| **Umgebung** | Linux-Server mit Caddy als Reverse Proxy und HTTPS; aktuelle Browser mit WebGL 2 und WebAssembly |
| **Sicherheitshinweise** | Nur über HTTPS betreiben; Härtung nach [Betriebsanleitung](docs/public/betrieb.md#härtung) umsetzen; Einladungslinks und API-Tokens nur über vertrauliche Kanäle weitergeben; API-Tokens mit dem kleinsten nötigen Umfang anlegen; beim MCP-Server gehen Werkzeugergebnisse an den Anbieter des Sprachmodells (Hinweise in `mcp-server/README.md`); Messwerte hängen von der Genauigkeit der Scandaten ab und ersetzen keine amtliche Vermessung |
| **Sicherheitsupdates** | kostenlos als Patch-Release der neuesten Minor-Version, signiert; Fristen und Upgradepfad in [Support und Patches](docs/public/support-und-patches.md), Hinweise je Version im [CHANGELOG](CHANGELOG.md) |
| **Supportende** | bis zur rechtlichen Einordnung: neueste Version, ohne Enddatum-Zusage; das Enddatum wird mit dem Inverkehrbringen festgelegt und hier genannt ([Support und Patches](docs/public/support-und-patches.md#supportzeitraum)) |
| **Software-Stückliste (SBOM)** | je Release und ausgeliefertem Artefakt im Format CycloneDX 1.6 nach BSI TR-03183-2 (`scanview-web-<version>.cdx.json`, `scanview-server-<version>.cdx.json`, `scanview-mcp-<version>.cdx.json`), veröffentlicht mit den Release-Artefakten |
| **Anleitungen** | Einrichtung, Aktualisierung, Sicherung, Löschen von Daten und Entfernen der Installation: [Betriebsanleitung](docs/public/betrieb.md) |
| **Rechtliche Einordnung** | [Anwendbarkeit](docs/public/anwendbarkeit.md), [Kontrollmatrix](docs/public/kontrollmatrix.md) |

## Sicherheit

Schwachstellen bitte vertraulich nach [`SECURITY.md`](SECURITY.md) melden. Wie Releases geprüft,
signiert und nachvollziehbar gemacht werden, beschreibt der
[Release-Prozess](docs/public/release.md); offene und behobene Befunde stehen im
[Befundregister](docs/public/befunde.md).
Antworten für Sicherheitsfragebögen von Auftraggebern, jeweils mit Beleg und offen benannten
Lücken: [Antwortvorlage](docs/public/sicherheitsfragebogen.md).

## Lizenz

Copyright © 2026 Stiebitz Souverain Systems UG (haftungsbeschränkt). Urheber: Andre Stiebitz.

siteBits steht unter der European Union Public Licence v. 1.2 (`EUPL-1.2`), siehe
[`LICENSE`](LICENSE). Wer siteBits oder eine geänderte Fassung weitergibt oder als Netzdienst
zugänglich macht, muss den Quelltext dieser Fassung bereitstellen (EUPL Art. 1 und 5). Lizenzen der Drittkomponenten:
[`docs/THIRD_PARTY.md`](docs/THIRD_PARTY.md). Hinweise zu Beiträgen:
[`CONTRIBUTING.md`](CONTRIBUTING.md).

**Herkunft und Reichweite der Rechte.** Der Code ist überwiegend mit KI-Werkzeugen unter menschlicher
Leitung entstanden. Andre Stiebitz hat die Ziele gesetzt, die Lösungen ausgewählt und angeordnet, die
Arbeit gesteuert und jede Änderung geprüft ([`AI-USAGE.md`](AI-USAGE.md)). Die Lizenz gilt, soweit am
Code Urheber- oder sonstige Schutzrechte bestehen.

**Unterstützung:** Abgesehen von den Sicherheitsupdates oben (Tabelle zum Cyber Resilience Act) sind
Hilfe, Fehlerbehebungen und neue Funktionen freiwillig; ein Anspruch darauf besteht nur, wenn er vertraglich
vereinbart ist.

```text
⣿⣿⣻⢽⢯⣯⢯⡷⣯⣻⡺⣝⡷⡽⣮⣳⣝⣮⣳⣝⢮⡳⣝⢮⡳⡵⣝⣽⢽⢽⢽⢽⡽⣽⣻⣻⣟⣟⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣽⣽⢾⣽⣺⡵⣫⣞⢽⣽⢽⣝⡮⣗⡽⣪⣟⣿
⣿⣺⣯⣯⢷⣻⣽⢯⡷⣷⣻⣯⣿⣻⣗⣯⢾⣺⣞⡾⣽⣽⣺⣳⡽⣽⡺⡽⣯⣿⣽⣳⢿⣽⣯⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⣷⣯⣿⣿⣽⣾⣽⡾⣾⣻⣽⣻
⣿⣿⣻⣾⡿⣯⣿⢿⣻⣿⣽⣾⣯⣷⣯⣿⣯⣿⣺⣯⣷⡿⣽⢷⣟⣷⣟⣿⣗⣯⣿⣿⣿⣷⣿⣽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣻⣿⣯⣿⣳⣷⣿⣾
⣟⡷⡿⡿⡿⡿⡿⣿⢿⡷⡿⡿⢿⠿⠿⡟⡿⢟⢿⠿⠷⢿⠿⡿⢿⡻⡿⡻⠿⠿⠿⢿⠿⢿⠿⡿⡿⡿⡿⣿⢿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⡿⣿⡻⡿⡽⡿⡽⡯⣟⢿⢽⢯⢿⢽⡻⣞⢿
⡲⡪⡺⡲⡕⡗⡟⡞⡼⡨⣊⢊⡂⢅⠑⠤⡁⡅⠕⡈⢌⠂⠅⢂⠂⠂⡌⡘⠸⠌⠮⡤⢬⢔⡴⣔⢴⢴⣼⣤⣑⣈⡂⡣⢓⠭⢣⢣⢑⢨⠐⡁⢒⢐⠨⠨⠀⢂⡁⣖⠼⡔⡎⡖⡕⡕⡖⢆⢇⢎⢖⢕
⠂⠕⠐⡐⢈⠂⡑⢈⠢⠑⡀⠂⠌⡂⠅⡁⠀⠠⠑⠄⠅⡂⢑⠠⠑⠁⠂⠄⠡⠈⠌⡘⠌⡊⠜⢘⠨⠪⠪⠪⢙⠕⠠⠂⠢⠡⠁⠊⠄⠡⠁⠂⠂⠀⡈⢀⢎⠣⡱⢘⠘⠌⡊⠔⠨⢂⠅⡑⠐⡐⠡⠁
⠀⠀⠁⠀⠀⠀⠀⠀⠀⠀⠀⠁⠀⠄⠀⠀⠁⠀⠀⠈⠀⠀⠀⠀⠈⠀⠀⠐⠀⠠⠀⠀⠀⡀⠈⠀⠈⠈⠈⠀⡀⠁⡁⠄⡢⠀⡂⡈⠀⡁⠀⠂⠆⠅⠊⡐⠠⠁⡐⠀⢈⠐⠀⠌⠐⠀⠠⠀⠀⠀⢀
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠐⠀⠐⠀⠁⠀⠀⠀⠄⠀⢁⠐⠀⠠⠀⠐⠈⢀
```

Bild: © 2026 André Stiebitz · [CC BY 4.0](LICENSES/CC-BY-4.0.txt) · [spleen/bilder/glienicker-see.txt](spleen/bilder/glienicker-see.txt)

> A map is not the territory it represents, but, if correct, it has a similar structure to the territory, which accounts for its usefulness.
>
> — Alfred Korzybski, Science and Sanity, 1933
