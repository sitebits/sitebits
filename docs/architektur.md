# Architektur

scanview besteht aus einem Import-Werkzeug, das Scandaten offline zu **Projektpaketen** aufbereitet,
einem **Viewer** im Browser, der diese Pakete direkt streamt, und einer selbst betriebenen
**Plattform** (Server, Portal, API, MCP-Server), die regelt, wer welches Paket sehen und bearbeiten
darf. Rechenintensives passiert beim Import; zur Laufzeit liefert der Host nur Dateien aus und
beantwortet Rechtefragen.

Sicherheitsaspekte (Vertrauensgrenzen, Bedrohungen, Maßnahmen) stehen in der
[Sicherheitsarchitektur](public/sicherheitsarchitektur.md), der Betrieb in der
[Betriebsanleitung](public/betrieb.md), die Begründungen der tragenden Entscheidungen unter
[`entscheidungen/`](entscheidungen/).

## Komponenten

| Komponente | Verzeichnis | Technik | Läuft |
|---|---|---|---|
| Import-Werkzeug `scanview-import` | `import/` | Python-CLI; PDAL und untwine als externe Prozesse in Containern | beim Aufbereiten der Daten, ohne Netz; ab Manifest-Schema /3 ausgeliefert: auf dem Host nur in den isolierten Aufträgen des Jobläufers (neue Projekte aus Mesh oder Splats, ohne Container) |
| Viewer | `web/` (`index.html`) | TypeScript, Vite, Giro3D/Three.js | im Browser |
| Portal | `web/` (`portal.html`, `src/portal/`) | TypeScript ohne Framework, gemeinsames Designsystem (`tokens.css`) | im Browser |
| Plattform-Server `scanview-server` | `server/` | Python, FastAPI, uvicorn (ein Prozess), SQLite | auf dem Host, nur Loopback |
| Reverse Proxy | `deploy/platform/` | Caddy mit `forward_auth` | auf dem Host |
| MCP-Server `scanview-mcp` | `mcp-server/` | Python, MCP-SDK, stdio | beim Nutzer |
| Release-Kette `scanview-nachweise` | `nachweise/` | Python; SBOM, Schwachstellen-Scan, Signatur, Export | beim Hersteller |

## Datenfluss

```
Scandaten (E57)
      │  scanview-import: inventory → convert → panos → overview → verify
      ▼
Projektpaket <projectId>/  (project.json Schema 2, COPC, Panoramen, Draufsicht, Berichte)
      │  paket-bereitstellen.sh: Kopie ohne Symlinks, Rechte, Dienst neu laden
      ▼
Host:  Browser ──HTTPS──> Caddy
                           ├─ /api/*, /auth/*        → scanview-server (127.0.0.1)
                           ├─ /data/<projectId>/*    → forward_auth /api/authz
                           │                             └─ erlaubt → Datei aus der Paketwurzel (Range)
                           ├─ /ansicht               → Viewer
                           └─ /, /anmelden, …        → Portal
       scanview-server ── SQLite (Konten, Rechte, POIs, Messungen, Protokoll, Statistik)
                       └─ liest project.json der Pakete, nur lesend
MCP-Server (beim Nutzer) ──HTTPS + API-Token──> /api/v1/*
```

Paketdateien fließen nie durch den Plattform-Server: Er entscheidet je Anfrage nur, ob die Sitzung
die Datei lesen darf; Caddy liefert sie mit HTTP-Range aus.

## Projektpaket (Schema 2)

Ein Paket ist ein Verzeichnis mit festem Vertrag, versioniert über `schemaVersion` in
`project.json`. Der Import liest die Quelldaten nur und schreibt jedes Paket in ein eigenes
Verzeichnis; `revision` kennzeichnet den Stand des Pakets, POIs und Messungen merken sich die
Revision, auf der sie entstanden sind.

```
<projectId>/
  project.json                 Manifest (Schema 2): Rahmen, Ebenen, Draufsicht, Startansicht
  cloud.copc.laz               Punktwolke (COPC, per HTTP-Range gestreamt)
  stations.json                Standpunkte: Pose, Panorama-Stufen, Bildgeometrie (eigenes Schema 2)
  panos/<station>/<seite>/     Würfelseiten je Stufe (512, 1024, 2048, 4096 px)
  topview.png                  Draufsicht mit Georeferenz im Manifest (optional)
  inventory.json, conversion-report.json
```

**Ebenen.** `project.json` beschreibt einen gemeinsamen Rahmen (`frame`: Koordinatensystem,
Einheit, `originWorld`) und eine Liste von Ebenen (`layers`) mit `kind` `pointcloud`, `mesh` oder
`panoramas` (`images`, `splats`, `ifc` sind reserviert). Der Viewer stellt heute Punktwolken-, glTF-Mesh- und
Panorama-Ebenen dar; andere Arten zeigt er als „nicht unterstützt“ und lädt den Rest. Jede Ebene mit Geometrie trägt eine Herkunftsangabe
(`provenance`: Scan oder Photogrammetrie, interpoliert, Maßstab belegt) und das Ergebnis ihrer
Prüfung (`verify`). Ebenen bringen keine eigene Transformation mit; der Import rechnet alles in den
Paketrahmen ein.

**Gekachelte Meshes.** Die Ebenenart `mesh-tiles` (`format: 3dtiles`) verweist auf ein `tileset.json`
(3D Tiles 1.1, ein GLB je Kachel, `REPLACE`, `geometricError` streng fallend bis 0 im Blatt). Die Kacheln liegen
in den Koordinaten des Paketrahmens (Z oben); `crs` und `heightSystem` der Ebene müssen zu `extras` des Tilesets
passen, die Hülle der Wurzel zu `boundsWorld` (±1 m), alle Kachel-Transformationen sind reine Verschiebungen.
Gezeichnet wird, was der Renderer nach Bildfehler wählt (Startwerte je Gerät in `web/src/geraet.ts`). Gemessen wird
nie auf dem Gezeichneten, sondern auf Blättern, die der Viewer gezielt entlang des Strahls lädt; kommt das Blatt
nicht rechtzeitig, gibt es keinen Wert („Feinste Stufe nicht rechtzeitig — erneut klicken“), nie einen Ersatzwert
aus einer gröberen Stufe. Der Webserver liefert `layers/<id>/tileset.json`, die Kachel-GLBs und `texturen/` aus
(Allowlist in `deploy/nginx.conf`; die Plattform lässt die Dateiendungen schon zu). Freigabelinks (Gäste) tragen
die Ebene nicht.

**Koordinaten.** Weltkoordinaten sind in allen JSON-Dateien Float64. Für die Darstellung gilt
`pLocal = pWorld − originWorld` (gerundete Mitte der Grenzen, ganze Meter), damit Float32-Puffer auch
bei projizierten Koordinaten Millimeter auflösen. Koordinatensystem, Einheit, Achse und Versatz
werden nie geraten: Sie kommen aus der Datei, einer Begleitdatei oder der Kommandozeile; fehlt das
Koordinatensystem, gilt „lokal“, fehlt die Einheit eines Mesh-Formats, bricht der Import ab.
Zwei Ebenen teilen einen Rahmen nur bei gleichem Koordinatensystem oder ausdrücklicher Erklärung
plus bestandener Deckungsprüfung.

**Abwärtskompatibilität.** Der Viewer liest Schema 1 und 2 und übersetzt beide in dasselbe
Innenmodell; `scanview-import migrate` schreibt ein Schema-1-Manifest als Schema 2 und sichert das
Original als `project.v1.json`. Neue Pakete schreibt der Import als Schema 2. Der Plattform-Server
liest nur Schema 2.

## Import

`scanview-import` liest heute E57-Dateien (Punktwolken, Scan-Posen, Würfelbilder). Punktwolken laufen
über eine PDAL-Pipeline in ein COPC; bei großen Quellen gestaffelt über LAZ und untwine (`--mode
auto|direct|staged`). PDAL und untwine laufen als externe Prozesse in Containern mit
Speichergrenze und ohne Root-Rechte.

| Befehl | Ergebnis |
|---|---|
| `inventory <e57>` | Metadaten als JSON: Scans, Bilder, Posen, Koordinatensystem |
| `convert <e57> <paket> [--crs …] [--max-points N] [--mode …]` | COPC, `project.json`, `inventory.json` |
| `panos <e57> <paket> [--levels …]` | Würfelpanoramen je Standpunkt, `stations.json` |
| `overview <paket>` | Draufsicht, Bodenhöhe je Standpunkt |
| `verify <e57> <paket> [--samples N]` | Prüfbericht: Punktbilanz, Stichproben Quelle gegen COPC, Wertebereiche, Laufzeit, Speicher |
| `build <e57> <paket>` | `convert` → `panos` → `overview` → `verify` in einem Lauf |
| `migrate <paket>` | Schema 1 → Schema 2 |

`verify` ist die Freigabe der Punktwolke: Nur ein vollständig bestandener Bericht setzt `verify.ok`.
Die Würfelseiten eines Standpunkts werden aus den tatsächlichen Bildposen zugeordnet, nicht aus
der Reihenfolge in der Datei.

## Viewer

- Lädt ein Paket über `?p=<paket-url>`, prüft das Manifest und baut daraus ein Ebenen-Register
  (Sichtbarkeit, Deckkraft, Einrahmen, Schnittbox für alle Ebenen).
- Punktwolken über Giro3D mit Detailstufen und Eye-Dome-Lighting; Panoramen als Würfel mit
  Stufen nach Blickwinkel und einem festen Grafikspeicherbudget; Minikarte aus der Draufsicht.
- glTF-Meshes aus Import Weg A mit Meshopt-Dekodierung, lokaler KTX2-Transkodierung,
  Vorschau zuerst und Vollstufe beim Heranzoomen; gemeinsames Mesh-Budget von 512 MiB
  je Viewer, unabhängig vom Panorama-Budget. [Bedienung und Grenzen](public/mesh-ebenen.md).
- **Messen nur auf Originaldaten:** Strecke, Polylinie, Fläche, Höhe. Jeder Eckpunkt ist ein
  Originalpunkt der Punktwolke, gefunden über eine Strahlsuche mit Nachladen der feinsten Stufe. Ohne Treffer gibt es keinen Messwert. Die Messgeometrie
  hängt an einer schmalen Pick-Schnittstelle, die jede Datenquelle umsetzt.
- Deep-Links kodieren Kamera, Standpunkt und Messungen im URL-Hash; Export als JSON und CSV.
- **Einzel- und Plattformmodus:** Beim Start fragt der Viewer `/api/sitzung`. Nur eine JSON-Antwort
  des Dienstes schaltet den Plattformmodus ein (POIs und Messungen auf dem Server, Zurück zum
  Portal, Nutzungszähler); sonst läuft er als statischer Einzel-Viewer mit Browser-Speicher.
- Lädt ausschließlich von der eigenen Origin; Decoder (WASM) werden mit dem Build ausgeliefert.

## Plattform

**Server.** Ein Prozess (FastAPI/uvicorn) hinter Caddy, gebunden an Loopback, ohne ausgehende
Verbindungen. Zustand in SQLite (WAL, Fremdschlüssel an, nummerierte SQL-Migrationen); Pakete
nur lesend. Keine IP-Adressen oder User-Agents in der Datenbank.

**Konten und Anmeldung.** Konten entstehen nur per Einladung (einmaliger Link, 7 Tage gültig,
Token im URL-Fragment, erst beim erfolgreichen Anlegen verbraucht). Anmeldung per Passkey
(WebAuthn), Passwort (argon2id) als Rückfallweg; Wiederherstellung über eine neue Einladung durch
einen Admin. Sitzung als `__Host-`-Cookie, serverseitig gespeichert als Hash, mit Leerlauf- und
Höchstdauer. Ändernde Anfragen verlangen die eigene `Origin` und den Header `X-Scanview`.

**Rechte.** Rollen je Projekt: Betrachter (lesen), Bearbeiter (Metadaten, POIs, Messungen,
Export); Admins zusätzlich Verwaltung. Jede Prüfung serverseitig; ein Projekt ohne Recht verhält
sich wie ein nicht vorhandenes (404).

**Paketauslieferung.** Caddy fragt für jede Anfrage unter `/data/` per `forward_auth` beim Server
nach. Der Server prüft Projektkennung, Pfad (keine Pfadtricks, nur bekannte Dateiarten) und Recht;
danach liefert Caddy die Datei mit Range-Unterstützung aus der Paketwurzel, als Daten ohne aktive
Inhalte.

**Bearbeiten und Protokoll.** Titel und Beschreibung, POIs und Messungen liegen in eigenen
Tabellen, optimistisch gesperrt (`version`, Konflikt → 409). Jede schreibende Aktion aus Web, CLI
oder API erzeugt ein Ereignis im Bearbeitungsprotokoll mit Vorher/Nachher; die Tabelle ist per
Trigger unveränderlich. Optional schreibt ein Git-Export die Bearbeitungen je Projekt als Commits
mit pseudonymem Autor.

**Portal.** Projektkarten mit Draufsicht und Kennzahlen, Suche, Bearbeiten, Verlauf, Export,
Verwaltung (Konten, Einladungen, Rechte-Matrix, Statistik) und API-Zugänge. Nur `textContent`,
strenge Content-Security-Policy.

**API.** Alles, was Portal und Viewer nutzen, ist eine dokumentierte Route unter `/api/v1/…`
(JSON, OpenAPI unter `/api/v1/openapi.json` für angemeldete Nutzer). Persönliche API-Tokens
(`Authorization: Bearer sv_…`) mit Umfang lesen oder lesen+schreiben, Ablaufdatum, Drosselung und
Widerruf; sie gelten nur für `/api/v1/*`, nie für Paketdateien, Verwaltung oder das Konto selbst.

**MCP-Server.** Läuft beim Nutzer (stdio) und bietet KI-Assistenten Werkzeuge über die API v1
mit einem persönlichen Token an — mit den Rechten des Kontos, nicht mehr. Die Werkzeugliste wird
per Test gegen die OpenAPI-Beschreibung geprüft. Ab Werk datensparsam: Namen anderer Konten
pseudonymisiert, Freitexte ausgeblendet.

**Statistik.** Tageszähler je Projekt (Öffnungen, Standpunktsprünge, Messungen, POIs), keine
Einzelereignisse mit Personenbezug.

## Geplant

- **Weitere Quellformate:** Adapter je Format (LAS/LAZ, PLY, PTS/PTX, XYZ; Meshes aus OBJ, PLY,
  STL, glTF), erkannt am Dateiinhalt, ein Befehl zum Hinzufügen weiterer Ebenen zu einem Paket.
- **Weitere Mesh-Unterstützung:** Messen auf der vollen Mesh-Stufe und große Meshes als
  3D Tiles 1.1 mit gemessenem `geometricError` und Blättern in voller Auflösung
  ([ADR 0003](entscheidungen/0003-eigener-kachler.md)).
- **Reservierte Dateiarten im Paket:** Meshes (`.glb`, `.gltf`, `.bin`, `.ktx2`), 3D Tiles
  (`tileset.json`, `.b3dm`, `.pnts`, `.i3dm`, `.cmpt`, `.subtree`) und Gaussian Splats (`.ply`,
  `.splat`, `.spz`); die Paketauslieferung lässt sie schon heute zu, alle anderen Dateiarten nicht.

## Release und Veröffentlichung

Ein Release entsteht aus einem eingefrorenen Commit: bauen, Qualitätstor, SBOM je Artefakt,
Schwachstellen-Scan, Befundregister-Gate, öffentlicher Export, signiertes Manifest, Archiv. Jeder
Fehler bricht ab. Einzelheiten im [Release-Prozess](public/release.md), die Entscheidungen in
[ADR 0010](entscheidungen/0010-release-kette-fail-closed.md),
[ADR 0012](entscheidungen/0012-oeffentlicher-export-positivliste.md) und
[ADR 0013](entscheidungen/0013-kein-ci-runner.md).
