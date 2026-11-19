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
  bereinigtes Manifest von `/api/teilen/manifest`. Eine Punktwolke ist keine Voraussetzung: Reine Mesh-Pakete
  haben eine Gast-Ansicht; ohne jede zulässige Ebene gibt es keine (Nachtrag 04.11.2026, 0.12.1).
- **Optionales Link-Passwort:** bewusst einfach (ab 4 Zeichen), argon2, Sperre nach 5 Fehlversuchen je Link.
- **Drosseln:** Austausch je IP und global, Abrufe je Link und Minute; Mehrfach-Range für Gäste abgelehnt.
- **Kein Zwischenspeicher:** Caddy setzt für Anfragen mit Gast-Cookie `Cache-Control: no-store` auch auf
  Binärdaten, sonst zeigte der Browser nach einem Widerruf noch eine Stunde lang Daten.
- Anlegen nur durch Projektleitung oder Admin, im Browser und mit frischer Sitzung (wie Passkey und API-Token).

## Konsequenzen

- `mesh-tiles` ist aufgenommen: Die Positivliste umfasst das Tileset und seine geprüften Mesh-Verweise.
- Was ein Gast im Browser sieht, ist technisch kopierbar; es gibt nur keine Download- oder Exportfunktion.
  Das steht so im Teilen-Abschnitt der Projekt-Einstellungen.
- Nachvollziehbar ist die Freigabe, nicht die Person: je Link Anzahl und Zeit der Öffnungen, Anlegen und
  Widerruf im Verlauf des Projekts.
- Wer den Link hat, hat bis zum Widerruf Zugriff. Das Passwort hilft nur gegen versehentliches Weiterreichen.
- **KI-Nutzung:** bewusst nur im Browser. Liste, Anlegen und Widerruf laufen über die Sitzung, nicht über
  API-Token oder MCP. Ein Link öffnet ein Projekt für Menschen ohne Konto; diese Entscheidung trifft ein
  Mensch, wie bei Passkey und API-Token. Agenten sehen Anlegen und Widerruf im Verlauf des Projekts.
- Neue Ebenenarten (Splats, Mesh-Kacheln F29) erscheinen für Gäste erst, wenn `teilen.py` ihre Dateien in
  die Positivliste aufnimmt (Splats: Nachtrag 2026-11-10).

## Bewusste Grenzen (Sicherheitsreview 15.10.)

- **Parallelität je Link:** umgesetzt sind Abrufe je Link und Minute und die Sperre von Mehrfach-Range. Eine Grenze
  gleichzeitiger Verbindungen gibt es nicht; der Viewer ist dabei kaum messbar, eine harte Grenze gehört in Caddy und
  kommt erst, wenn Missbrauch beobachtet wird.
- **Unzulässige Verweise im Paket:** Ausbrechende, kodierte oder auf Fotopfade zeigende Verweise nimmt die Plattform zur Laufzeit
  aus der Gast-Ansicht (fail closed, Warnung im Log). Der Import lehnt solche Pakete noch nicht ab — nachziehen, wenn
  der Upload im Browser kommt.
- Die Positivliste wird nach `project.json` (Änderungszeit, Größe) zwischengespeichert. Ein Paket wird immer als Ganzes
  ersetzt (`paket-bereitstellen.sh`), dabei ändert sich `project.json` mit.


## Nachtrag 2026-11: Import lehnt ab (seit 0.12.2)

Die offene Grenze „Der Import lehnt solche Pakete noch nicht ab“ ist geschlossen. Jeder Verify-Pfad des
Imports (`verify`, `verify_points`, `verify_mesh`, für reine Splat-Pakete ohne Punktwolke `verify_splats`)
prüft zuerst alle Verweise des Pakets und bricht beim ersten unzulässigen ab, bevor er eine Datei des Pakets
öffnet oder den Prüfbericht schreibt. Die Prüfung steht an einer Stelle
(`import/src/scanview_import/verweise.py`).

- **Geprüfte Verweise:** im Manifest `url`, `previewUrl`, `mobileUrl` und `verify.report` jeder Ebene,
  `topview.url` sowie bei Schema 1 `cloud.url` und `stations.url`; in `stations.json` die Würfelseiten
  `panorama.faces` und das Stufenmuster `panorama.levelFile` (geprüft mit einer Ziffer für `{level}`); in
  glTF-Dateien die `uri` von Buffern und Bildern (`data:` ausgenommen, verschachtelte .gltf bis zur Tiefe 4);
  in `tileset.json` `content.uri` und `contents[].uri` aller Kacheln und, wenn eine Kachel auf eine .glb oder
  .gltf zeigt, deren Buffer- und Bildverweise relativ zur Kachel — wie die Plattform zur Laufzeit.
- **Zulässig** ist nur, was auch die Plattform zur Laufzeit annimmt: ASCII aus `A–Z a–z 0–9 . _ - /`,
  relativ, ohne leere, `.`- oder `..`-Segmente und ohne Segmente mit führendem Punkt. Abgelehnt werden
  damit absolute Verweise (`/…`, `C:…`, `file:…`), `..` und alles Kodierte (`%2e`, `%2f`, Backslash, NUL
  und andere Steuerzeichen, Zeichen, die sich unter Unicode-Normalisierung ändern, sonstiges Nicht-ASCII).
- CLI, API-Import und MCP `import_starten` laufen über denselben Verify-Schritt (`scanview-import verify`
  im Import-Worker); eine eigene Werkzeugzeile gibt es dafür nicht.
- Der Laufzeitfilter der Plattform bleibt als zweite Linie.


## Nachtrag 2026-11-10: Sichtschicht, Feld-Positivliste, Ansicht im Link (unveröffentlicht)

- **Sichtschicht (Splats) für Gäste.** Die Positivliste nimmt `splats` auf. Bei `spz` und `ply` ist das
  genau die Datei aus `url`. Bei `rad` sind es die Kopfdatei (`RAD0`, u32-Länge, Kopf und JSON
  zusammen höchstens 1 MB, JSON `version` 1 mit nichtleerer `chunks`-Liste) und jeder dort genannte
  Chunk. Ein Chunk hat einen Namen aus `[A-Za-z0-9._-]` mit der Endung `.radc`, liegt flach im selben
  Ordner, ist eine reguläre Datei und kein Symlink. Jeder Formfehler nimmt die ganze Ebene heraus (fail
  closed, Warnung im Log). Eine Splat-Ebene ohne erklärtes Format (Platzhalter) bekommen Gäste nicht,
  weil der Viewer sie nicht zeigt. Der Prüfbericht `conversion-report.json` bleibt gesperrt.
- **Feld-Positivliste je Ebenenart.** Abweichend vom ersten Entwurf („Felder gehen wie bei Mesh
  unverändert mit“) gibt das Gast-Manifest je Ebene nur die Felder weiter, die der Viewer liest
  (`teilen.GAST_FELDER`, abgeleitet aus `web/src/manifest.ts`). Das gilt für Wolke, Mesh, Mesh-Kacheln
  und Splats; bei Splats geht `frameCheck` nur mit `ok`, `samples`, `p50M` und `p95M` hinaus. Die
  Draufsicht geht nur mit den Feldern hinaus, die `parseTopview` liest (`teilen.GAST_DRAUFSICHT`), ohne
  `colorFrom` und `image`.
  - Nicht mehr an Gäste gehen: Quelldateiname und SHA-256 (`source`), Importoptionen (`sourceOptions`),
    Ausrichtung (`alignment`), `buildLod`, `verify` samt Berichtspfad, `readerPointCount`,
    `invalidDropped` und `conversion` je Ebene.
  - Weiter mit geht `provenance` samt `accuracy`. Der Viewer zeigt daraus den Genauigkeitsnachweis,
    einschließlich Software, Version, Projekt-SHA-256 und Name des Genauigkeitsberichts.
  - Beleg: `web/tests/gastManifest.test.ts` liest das volle und das gekürzte Manifest mit dem
    Viewer-Parser und erwartet dasselbe Projekt.
- **Ansicht im Link.** Ein Link darf eine Startansicht tragen: `/teilen#t=<Token>&cam=…&tgt=…&ly=…`.
  - Erlaubt sind nur Kamera, Ziel und sichtbare Ebenen, mit Zeichen aus `[0-9A-Za-z_.,;:-]` und
    höchstens 512 Zeichen.
  - Die Ansicht steht nur im Fragment. Die Teilen-Seite liest sie vor dem Aufräumen der Adresszeile, der
    Austausch bleibt `{token[, passwort]}` (Zusatzfelder ergeben 422). Erst hinter einer geprüften
    Projekt-ID wird sie an das Viewer-Ziel gehängt. Server, Datenbank und Logs sehen sie nie.
  - Befundlinks, Messungen, Standpunkte und Filter reisen nicht mit, denn Gäste sehen keine Befunde
    (siehe oben).
  - Angelegt wird so ein Link wie bisher nur im Browser. Ein Agent kann den `ly`/`cam`-Teil bauen
    (`docs/ki-paritaet.md`).
- **Restrisiko Texturen und Splats ohne Anonymisierung (angenommen vom Betreiber am
  2026-11-11).** Mesh-Texturen, Kachel-Texturen und Splats entstehen aus Fotos und können Personen
  zeigen. Anders als Panoramen und Fotos (F20, `freigabe.py`) gibt es für sie keine Anonymisierung und
  keine Freigabe je Datei; sie gehen per Gast-Link so hinaus, wie sie im Paket liegen. Der Hinweistext
  in den Projekt-Einstellungen sagt das („Fototexturen und Sichtschicht entstehen aus Fotos und können
  Personen zeigen“). Die Anonymisierung (F20-Ausweitung) ist für 1.x vorgesehen; bis dahin gilt die Annahme,
  Wiederaufnahme mit F20 oder bei einer Instanz, die Gast-Links mit Personen auf Fototexturen öffentlich teilt.
- **Nicht in 1.0: Einbettung per iframe und öffentliche Modellseite.** Das Gast-Cookie ist
  `SameSite=Lax` und reist in einem fremden iframe nicht mit. `scanview.caddy` setzt
  `frame-ancestors 'none'`. Safari sperrt Drittanbieter-Cookies grundsätzlich. Eine Einbettung bräuchte
  also einen eigenen Weg (CSP `frame-ancestors` je Link, ein anderes Sitzungsmodell) und eine neue
  öffentliche Fläche. Die Modellseite stellt dazu die Veröffentlichungsfrage. Beides ist für 1.x
  vorgemerkt.
- **nginx-Variante** (`deploy/nginx.conf`): Sie liefert Meshes samt Texturen und Panoramen ohne
  Anmeldung aus, Splats gar nicht. Sie läuft nur lokal (`deploy/README.md`, „Was diese Variante ohne
  Anmeldung zeigt“).

## Nachtrag 2026-11-10: Ansichtsprofile (unveröffentlicht, Migration 0020)

Anlass: Öffentlich geteilte Links sollen nicht die volle Arbeitsoberfläche zeigen, sondern „eine minimal variante so
wie bei sketchfab. wo man nur anschauen kann und ein paar settings ändern aber nicht die ganzen funktionen wie der
editor“ (Andre, 04.11.).

- **Drei Profile.** „Ansehen“ zeigt die 3D-Ansicht im Vollbild mit einer schmalen Leiste: Name, Grau, eine Strecke
  messen und ein Einstellmenü (Hintergrund, Sonnenlicht, Punktgröße, Vollbild, Steuerungshilfe, Info). „Messen“ bringt
  zusätzlich Linienzug, Fläche und Höhe. „Arbeiten“ ist die bisherige Gast-Oberfläche. Werkzeugleiste, Panels und
  Editor sind in den beiden schlanken Profilen ausgeblendet und `inert`. Kürzel und Controller-Menü wirken nur für
  eine Positivliste (`web/src/profil.ts`).
- **Profil am Link.** Jede Zeile in `teilen_links` hat ihr Profil (`profil`, Standard `ansehen`). Das Projekt hat
  eine Standardansicht (`projekte.ansichtsprofil`, Standard `ansehen`). Sie belegt das Formular vor und gilt, wenn
  der Anlege-Aufruf kein Profil nennt. Ein späterer Wechsel ändert keinen bestehenden Link. Bestehende Links aus der
  Zeit vor 0020 bekommen `ansehen`.
- **Gast kann nicht wechseln.** Der Viewer liest das Profil aus `/api/sitzung` (`gast.profil`). Die Adresse kann es
  nur verkleinern: `?zeigen=1` ist der Alias für „Ansehen“, damit Messe-Links und QR-Codes gültig bleiben. Einen
  Parameter, der vergrößert, gibt es nicht. Der Austausch bleibt `{token[, passwort]}`, ein Zusatzfeld `profil`
  ergibt 422. Die Verwaltung (`/api/v1/projekte/{pid}/teilen`, `/einstellungen`) bleibt mit dem Gast-Cookie 401.
- **Keine Rechtegrenze.** Das Profil ist Oberfläche. Wer die Seite selbst verändert, kann im Browser jede Bedienung
  nachbauen. Was ein Gast laden darf, entscheidet weiter allein die Positivliste (`gast_paket`), und zwar für jedes
  Profil gleich. Beleg: `test_profil_erweitert_die_positivliste_nicht` in `server/tests/test_teilen.py`.
- **Angemeldete Beteiligte** arbeiten wie bisher in „Arbeiten“. Mit `?zeigen=1` sehen sie „Ansehen“. Die
  Standardansicht des Projekts gilt nur für Gast-Links.
- **KI-Nutzung.** Die Standardansicht steht in `GET /projekte/{pid}` (`einstellungen.ansichtsprofil`, MCP
  `projekt_lesen`). Ändern lässt sie sich nur im Browser (Verwaltung der Projektleitung). Das Profil eines Links wird
  wie der Link selbst nur im Browser gewählt (`docs/ki-paritaet.md`).
- **Nicht in 1.0:** eine Merkfunktion je Nutzer und ein Profil-Wähler für Beteiligte (1.x).
