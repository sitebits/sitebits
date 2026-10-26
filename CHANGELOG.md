# Changelog

Alle nennenswerten Änderungen an scanview stehen in dieser Datei. Das Format folgt
[Keep a Changelog 1.1.0](https://keepachangelog.com/de/1.1.0/), die Versionierung
[Semantic Versioning 2.0.0](https://semver.org/lang/de/).

Jede Version hat einen Abschnitt **Sicherheit** mit den behobenen Schwachstellen, ihrer Schwere,
den betroffenen Versionen und den nötigen Maßnahmen der Betreiber — auch wenn er „keine“ lautet.
Kennungen `B-…` verweisen auf das [Befundregister](docs/public/befunde.md).

## [Unreleased]

## [0.12.0] – 2026-10-26

Gekachelte Meshes im Viewer und in Gast-Links, der Kachler im Import-Werkzeug und die Etappe Mitmachen: Fotos und PDFs
am Befund-Faden, E-Mail-Benachrichtigung und eine Freigabe von Panoramen und Fotos, die an die Dateien gebunden ist.

**Betreiber, bitte vor dem Update lesen:**
- **Panoramen und Fotos mit Personen neu freigeben (F20):** Ein Statuswert in `images.json` genügt nicht mehr. Wer
  Fotos so freigegeben hatte, gibt sie nach dem Update einmal im Portal frei (Admin: „personenfrei“). Ältere
  Panoramen ohne Anonymisierungsblock bleiben sichtbar, bis „Panoramen nur freigegeben zeigen“ eingeschaltet wird
  (Abschnitt „Sicherheit“).
- **Neue Funktionen sind ab Werk aus:** Anhänge am Faden (`SCANVIEW_ANHAENGE`, dazu die Caddy-Grenze von 21 MiB aus
  `deploy/platform/scanview.caddy`) und E-Mail-Benachrichtigung (Dienst `scanview-post`, Einrichtung von Hand nach
  `docs/public/betrieb.md`). Ohne diese Schalter ändert sich am Caddyfile nichts.
- **Migrationen 0016 bis 0018** laufen beim Start automatisch (Freigaben, Benachrichtigung, Anhänge). Rücksprung auf
  0.11 nur mit Datenbanksicherung.
- **Gekachelte Meshes mit dem Beispiel-nginx:** `deploy/nginx.conf` hat drei neue Locations (Kacheln, `tileset.json`,
  Texturen).

```
⣿⣿⣿⣿⣿⣿⣿⣻⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣯⣿⣿⡿⡽⣝⡯⣯⣻⣿⣿⣟⣟⢿⢝⣟⢽⢯⡻⡿⣟⢯⡫⣳⢹⢜⢮⡺⣜⢮⡳⣝⢮⡳⣝⡽⣽⣿⣿⣟⣟⣿⣿⣿⣽⣯⡿⣿⣿⢿⣿⣿
⣿⣿⣯⣿⣿⣷⣿⣿⣻⣿⣻⣿⣿⣿⣿⣿⣿⣿⣟⣿⢿⣿⣻⣿⢿⣿⣿⣿⣿⣿⣯⣿⢾⣵⣷⣗⣷⣽⡾⣿⣾⣵⣟⣮⣯⣿⢿⢿⣻⣳⣯⣷⣿⣿⣿⣯⣷⣻⣞⣾⣻⣿⣿⣿⣷⣿⣿⣿⣿⣿⣿⣾
⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣿⣽⢿⣾⣻⡿⣿⣻⡿⣿⣻⢿⡾⣟⣷⣿⣻⢿⣻⢽⢽⢯⢯⣟⣷⢿⣻⣽⡿⣯⡿⣿⢿⡿⣟⣿⡿⣾⣻⣽⣾⢿⣻⣿⣿⢿⣟⣿⣿⢿⣿⡿⣿⣿⢿⣿⣿
⠁⠁⠂⠂⠂⢈⢰⣰⡴⣬⢼⢬⡪⢘⢌⢊⠌⠌⡐⠨⠨⠠⠁⠈⠠⢈⡠⣈⣂⢧⡢⡄⣅⢤⡡⢬⠤⣅⢆⢔⢅⡥⡦⣅⠅⡉⠂⠡⢁⢑⠠⠑⣈⢐⠨⢐⢁⢑⠨⢐⠡⢡⠑⢌⢈⢂⢉⠈⡈⠈⠄⠡
⢨⠢⣄⢤⢬⡚⢙⠈⠌⡂⡂⡂⠢⠁⠢⢐⢈⠈⠀⠁⠂⠈⠐⠀⠁⠠⡹⢘⢌⢇⠣⡙⢌⠢⢃⠣⡑⢅⠣⡃⢏⠪⢊⢊⠢⡐⡡⢁⢂⢔⡀⢅⠄⢢⢈⢐⣄⠢⠡⠂⠂⠁⠅⢁⢳⢕⢮⢢⢆⢕⢭⢢
⠱⡩⠪⡪⢪⢒⠔⢄⢄⢁⠐⠠⢁⠂⠍⠀⠀⡅⡀⠁⠈⠈⡀⡐⢈⠆⠂⡁⢂⠂⠅⠂⢂⠨⠐⠀⠂⠐⠐⡈⡐⢈⠐⠨⢐⢴⡰⢰⠠⢒⠀⠐⢨⢐⠐⢘⠌⠅⠕⠙⢌⠤⡡⡂⠅⠅⠕⡑⡑⢍⠊⢆
⠨⢐⠡⢊⢢⠣⡱⡡⢂⠅⡂⠄⢀⠈⠄⢁⢁⢀⣇⢧⢊⡱⡍⢆⢅⠄⠄⠀⠐⠈⠄⠁⠂⡀⠂⠁⠠⠁⠐⠐⠀⠂⠈⠐⠀⢃⢣⢐⠨⠠⠨⢐⢈⢀⢢⢫⠠⠩⠈⠂⢁⢌⠌⠠⢁⠈⠐⠀⠌⢀⠊⠠
⠐⢔⠥⡱⡜⡮⣲⡸⡐⡐⡐⡈⠄⢂⠁⡂⢑⢐⢸⡎⣻⡎⠈⠌⢦⠨⢄⠁⠈⡀⠄⠁⠠⠀⠂⠁⢈⠂⠁⠈⠀⠀⠀⠀⠀⠂⠂⠅⢣⡢⡬⡐⠱⠱⢛⢼⢆⠕⠱⠺⡸⢕⢀⠡⠀⠀⠈⠀⠀⠄⠠⠁
⡸⡌⡷⣝⡗⣝⡮⣞⢜⢔⠢⡂⡑⠄⠌⠀⡀⢮⣛⢗⢜⡟⣩⠎⠌⠈⡀⠠⠀⢀⠠⠀⠠⠀⠀⢐⡀⠀⠠⠀⠀⠀⠀⠀⠀⠀⠁⠀⠂⠗⡆⡨⡌⠾⣞⢕⢶⠂⡐⡐⡀⢂⠑⠀⠀⠀⠀⠀⠀⢇⠁⢄
⡪⡪⡳⢕⢭⡪⡏⡎⡇⣇⢇⠣⡊⠌⢌⠐⡀⠂⢴⠹⣕⡳⣄⠐⠀⠄⠀⠄⠐⠀⠀⠀⠀⡀⠀⠢⠁⠣⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠈⢈⡸⡎⡗⣕⠁⠀⠈⡀⠀⠀⠀⠀⠠⠀⠂⢁⠂⡡⡱
⢌⠪⠈⢆⢃⠎⠪⡘⠌⢜⢘⠜⡐⢅⢂⢁⢐⡸⡳⣩⢖⣕⢳⢵⣄⡂⠂⠀⠄⠠⠐⠈⠀⠀⡀⠀⠁⠁⠀⠂⠀⡀⠐⠀⠀⠂⠀⠂⠁⢀⢠⡢⡗⡍⣎⢎⢪⡣⡎⠀⠀⠀⠀⠠⢐⠀⠂⢌⠰⢨⢢⠣
⣂⣁⣁⡢⣠⢨⡐⡄⣅⢔⣠⢄⣆⣆⣖⡲⡵⡵⡫⣞⢵⡳⣫⢦⣣⡫⣫⢳⡲⡲⡢⡦⢦⡢⡴⡰⡤⣢⡢⡆⡦⡔⣔⢴⢔⢖⡲⣪⢺⣩⡣⡫⡮⡺⣪⢺⢲⢕⡭⣝⡲⣢⢣⡢⣜⣔⢕⣕⢽⢬⣒⢕
```

Bild der Version: `glienicker-bruecke-1` · © 2026 André Stiebitz · CC BY 4.0

### Hinzugefügt

- Gast-Links zeigen gekachelte Meshes (`mesh-tiles`): Nur das geprüfte `tileset.json` und seine
  referenzierten Mesh-Dateien samt glTF-Zubehör sind erreichbar; ungültige oder übergroße Tilesets
  entfernen die ganze Ebene aus der Gast-Ansicht (ADR 0016).
- `scanview-import kacheln`: zerlegt ein texturiertes OBJ in Projektkoordinaten in eine 3D-Tiles-1.1-Hierarchie
  (räumliche Teilung, vereinfachte innere Stufen mit gebackener Textur, Blätter in voller Quelldichte) und packt sie
  mit `--packen` als meshopt-/KTX2-Paket. Texturseiten haben Kanten als Vielfaches von 4 (KTX2-Pflicht), der
  Packschritt verweigert andere Maße. `--jobs` (Standard: Prozessorzahl, höchstens 8) backt und packt Kacheln
  parallel mit bytegleichem Ergebnis zu `--jobs 1`. Messpunkte je Kachel als JSON-Zeilen auf stderr.
- E-Mail-Benachrichtigung zu Befund-Fäden (Etappe Mitmachen, F8, ab Werk aus, ADR 0017): Wer in seinen
  Einstellungen einwilligt und die Adresse per Link bestätigt, bekommt bei neuem Kommentar, Link, Status oder
  Zuständigkeit an eigenen Befunden eine kurze Mail: nur die Art des Ereignisses und Links, keine Projektnamen,
  Titel, Texte oder Koordinaten. Der Link führt nach der normalen Anmeldung zum Verlauf (nur für das Konto, an das
  die Mail ging); geantwortet wird im Browser. Abbestellen je Befund (Verlauf, API, MCP) und für alles, auch per
  Ein-Klick-Link ohne Anmeldung; Projektleitungen schalten Mails im Projekt ab. Der Webdienst bleibt ohne Netz:
  ein Hintergrund-Thread prüft vor dem Rendern Rechte, interne Sicht, Einwilligung und Drosselung (10 je Stunde,
  eine je Befund in 15 Minuten) und legt die Nachricht in ein Spool-Verzeichnis; versendet wird sie vom neuen
  Dienst `scanview-post` (eigener Nutzer, keine Datenbank, SMTP mit TLS, herstellerneutral). Einrichtung von Hand
  nach `docs/public/betrieb.md` (Unit und Umgebungsdatei in `deploy/platform/`). Migration 0017.
- Fotos und PDFs am Befund-Faden (Etappe Mitmachen, F7, ab Werk aus): Beteiligte mit Recht zum
  Kommentieren laden JPEG, PNG oder PDF bis 20 MB hoch. Fotos werden im Jobläufer isoliert neu kodiert
  (höchstens 40 Megapixel, 512 MB, 60 Sekunden), ohne EXIF/GPS, Geräte- oder Personennamen und angehängte
  Inhalte. Bis dahin zeigt der Faden „in Prüfung“, danach das Foto; PDFs bleiben unverändert und werden
  nur als Download mit dem Hinweis „PDF aus externer Quelle“ angeboten. Zugriff nur mit Projektrecht,
  nie über Gast-Links; Zurückziehen nur durch den Verfasser. Einrichtung mit `SCANVIEW_ANHAENGE`,
  Projektquote und Löschverhalten in `docs/public/betrieb.md`. Negativtests `NT-ANHANG`, Migration 0018.
- Freigabe von Panoramen und Fotos an Dateien gebunden (Etappe Mitmachen, F20): Die Projektleitung gibt in den
  Projekt-Einstellungen frei („Freigabe von Panoramen und Fotos“); der Server hasht dabei jede Bilddatei und
  speichert Pfad und SHA-256. Betrachter und Rückmelder bekommen nur Dateien einer aktiven Freigabe, deren Hash
  noch passt. Ein danach ausgetauschtes Bild, Original- und Sicherungsordner (`panos.alt/`, `fotos-alt/` …) und
  Dateien, die kein Manifest nennt, sind für sie 404. Art „anonymisiert“ verlangt eine geprüfte Anonymisierung im
  Paket; Art „personenfrei“ erklärt ein Admin (Altbestand), protokolliert. `scanview-import panos-kameras
  --anonymisiert <manifest.json>` übernimmt Kugelpanoramen nur aus einem geprüften Anonymisierungslauf, prüft dessen
  Bedingungen selbst nach (Entscheidung fertig, gleiche Revision, Quelle unverändert, Ausgabe verifiziert) und
  schreibt Werkzeug, Manifest-SHA-256 und den Hash jeder Stufendatei nach `stations.json`. API
  `GET/POST/DELETE /api/v1/projekte/{pid}/freigaben[/{ebene}]` (Freigeben nur im Browser), MCP-Werkzeug
  `freigaben_lesen`, Client `freigaben()`. Migration 0016.
- Panoramen sind für Betrachter und Rückmelder nicht mehr ohne Weiteres sichtbar: Jeder Panorama-Import schreibt
  `anonymization.required` mit Dateiliste nach `stations.json`, solche Panoramen erscheinen erst nach einer
  Freigabe. Ältere Pakete ohne diesen Block bleiben sichtbar, bis die Projektleitung den neuen Schalter „Panoramen
  nur freigegeben zeigen“ einschaltet (für neu eingerichtete Projekte an). Der Viewer zeigt die Standpunkte weiter,
  ohne Panoramabilder anzufragen. Für Fotos genügt `anonymization.status: released` in `images.json` nicht mehr:
  Betreiber, die Fotos so freigegeben hatten, geben sie einmal im Portal frei (Admin: „personenfrei“).
- Gekachelte Meshes (3D Tiles 1.1) als Mesh-Ebene im Viewer: Ebenenart `mesh-tiles` im Manifest (Schema 2, alte Pakete
  unverändert), Darstellung über Giro3D `Tiles3D` mit Bildfehler und Speichergrenze je Gerät (Touch-Geräte gröber),
  Ebenen-Register, Schnittbox, Live-Sonne und Deckkraft wie beim GLB-Mesh. Messen und Markieren gehen über einen
  eigenen Blattabruf auf die feinste Stufe; ohne rechtzeitiges Blatt bleibt es bei der bestehenden Meldung, ein
  Ersatzwert aus einer gröberen Stufe ist ausgeschlossen. Der Viewer prüft das Tileset beim Laden (Rahmen, Lage,
  Hierarchie, relative Pfade) und sperrt nur diese Ebene mit Begründung. KTX2 und Meshopt laufen mit den
  mitgelieferten Decodern, kein Fremdserver. `deploy/nginx.conf` liefert `tileset.json`, Kachel-GLBs und `texturen/`
  aus (und `.glb` als `model/gltf-binary`); drei Einträge in `egress-ausnahmen.toml` für Giro3D-Vorgabewerte, die
  `tilesLayer.ts` überschreibt.

### Sicherheit

- Neue Vertrauensgrenzen mit der E-Mail-Benachrichtigung (ab Werk aus): Webdienst ↔ Postdienst nur über ein
  Spool-Verzeichnis, Postdienst ↔ SMTP-Server mit TLS; Bedrohungsmodell und Negativtests NT-POST. Der Webdienst
  behält `IPAddressDeny=any`. Betreiber: keine Maßnahme, solange die Benachrichtigung aus bleibt.
- Personenbilder (F20): Die Freigabe von Panoramen und Fotos gilt nur noch für die Dateien, deren SHA-256 der
  Server beim Freigeben festgehalten hat; vorher genügte für Fotos ein Statuswert in `images.json`, und Panoramen
  waren gar nicht gesperrt. Betreiber: nach dem Update Panoramen und Fotos mit Personen einmal freigeben, bei
  Altbeständen „Panoramen nur freigegeben zeigen“ einschalten. Negativtests NT-FREIGABE.

## [0.11.0] – 2026-10-24

Upload im Browser, Auswertungen auf dem Server, Sonnen- und Prüfberichte, eine typisierte API mit Client und eine
neue Controller- und Handy-Bedienung. Zum ersten Mal liefert das Release auch das Import-Werkzeug aus.

**Betreiber, bitte vor dem Update lesen:**
- **Startskript erneuern (alle Hosts):** Das Release nutzt Manifest-Schema `/3`. Ein altes
  `/root/scanview-bootstrap.sh` lehnt es ab. Erst den Block aus `docs/public/betrieb.md` übernehmen, dann wie gewohnt
  einspielen (Abschnitt „Sicherheit“ unten).
- **Neue Funktionen sind ab Werk aus:** Jobläufer (`SCANVIEW_JOBLAEUFER=1`, verlangt systemd ≥ 257) und Import/Upload
  (`SCANVIEW_IMPORT=1` mit Pflichtwert `SCANVIEW_UPLOAD_QUOTE_GB`). Ohne diese Schalter verhält sich die Instanz
  wie 0.10.
- **Migrationen 0014 und 0015** laufen beim Start automatisch (0014 Freigaben für Auswertungen, 0015 Uploads).
  Rücksprung auf 0.10 nur mit Datenbanksicherung.
- **Umbenannte Import-Befehle:** `panos-kameras` und `fotos-kameras` (Abschnitt „Geändert“).

```
⣿⢛⢟⡻⡻⣻⢻⡻⣻⢻⣻⡻⣟⣿⣻⣟⡿⣻⣻⣟⣿⣻⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⢿⣿⣿⢿⡿⣿⡿⣿⢿⡿⣿⢿⣟⣿⣻⡻⣻⣻⣻⡿⣿⢿⡿⣿⢿⣟⣿⣻⢟⣟⡟⣟⢟⢿
⣿⣪⢗⡝⡮⣓⢯⢺⡪⡗⣗⢽⣕⣗⣗⢗⢯⢻⣺⣺⣺⣳⣟⣯⣷⢿⣻⢽⢳⡫⡻⡫⣟⢽⣯⣿⣾⣿⣿⣷⣿⣻⣽⣷⣿⣿⢿⣿⣿⣿⣻⡾⣺⢽⡪⣎⢮⢫⢯⣻⢽⢯⢟⡞⡞⣎⢯⣚⡞⣞⢮⣿
⣿⢪⡳⣝⢮⡳⣳⡣⣗⢷⢵⣣⢧⢧⡳⣝⡵⡳⣪⡪⡮⣺⢼⢺⣪⣫⡪⣎⢮⣪⣺⢝⣞⡽⣝⣟⣿⢿⣿⢿⣿⡿⣿⣗⣿⣞⣿⣺⣳⢯⡯⣿⢽⡯⣟⡮⣗⡯⣟⡾⣽⣹⣕⡯⣫⣞⢗⣗⢽⣪⢗⣿
⣿⡽⣞⣗⣯⢾⡵⡯⡾⣽⣳⢽⡳⡯⡯⣗⣯⣟⣾⣺⢽⣳⢯⣟⣾⣺⢾⡯⣟⡷⣯⣟⡷⣟⣯⢿⡽⣯⢿⢯⡷⡿⡯⡿⡷⡿⣯⢿⡽⣿⣻⣽⢿⣻⣽⢿⣽⢯⡿⣽⢷⣻⣞⣯⡷⣯⢿⣞⣿⣺⣗⣿
⣟⠌⠪⠐⠅⠅⠕⠡⢃⠑⠌⡊⠜⢘⢘⢘⢐⠰⠐⠌⡊⠢⢑⠢⢂⠊⢆⠕⠥⠣⢒⠢⢃⠣⠪⠪⠪⠊⠎⠕⡊⠪⢊⠪⠪⡘⠄⠕⢅⠕⡰⢘⠌⢆⠕⡑⢌⠕⡑⡑⢅⢃⠪⢐⠡⠡⡑⠰⡐⢌⢂⢿
⡷⠈⠌⠌⠌⠌⠌⠌⠄⠅⠂⠐⠈⢄⢔⢔⢄⠂⡁⠡⢀⠑⡐⢈⠐⡈⠄⠨⠠⠁⠢⠂⠂⡐⢂⠢⠂⠡⠈⡐⢈⢈⠄⠨⠐⠀⠁⢁⠐⠀⡈⠠⠈⠐⠐⠈⠄⠅⢌⢐⠡⠐⡈⢐⠨⠐⠌⡂⠌⡐⠄⢿
⡯⠀⠅⠨⠐⠈⠄⡁⠂⡂⠡⠨⠐⠐⡀⢂⠡⠐⡀⠅⠐⡀⠂⡐⢀⠂⠈⠄⠌⡈⠂⠌⠠⡀⢢⢢⡢⡈⡔⡀⠂⠅⡅⠅⠂⡁⠌⠀⠐⠀⢂⠐⠈⡀⠡⠁⡈⢈⠠⠐⡀⠅⠐⠠⠨⠈⠄⠂⠡⠐⠀⣻
⡯⠠⠁⠡⠈⡐⠐⠀⠡⠀⡈⠠⠈⠠⠀⡂⢀⠁⠄⢀⢐⢔⢀⡀⠄⠂⠁⠐⠀⡀⠂⢂⠀⠄⠑⢐⠪⡂⠕⠀⠂⡁⢄⠢⠀⡀⠄⠈⠔⡁⢇⠏⢖⠐⠄⠁⠓⠉⠊⠀⠀⠂⠁⢐⠀⡁⠠⠁⠂⠁⡁⢽
⡯⠀⠌⠐⠐⠀⡀⢁⠐⠠⠀⠠⠈⠀⠂⠀⠂⠈⠀⠁⠁⠄⢄⠈⠠⠀⡁⡐⡀⠠⠐⠀⠄⠂⢈⠠⢐⣰⣨⣄⡁⠀⠄⠀⢂⠤⡦⡢⡡⡪⡓⡎⡐⡐⠠⠁⡀⠁⠄⠁⡈⠠⠁⠄⠠⠀⠄⠐⢀⠁⡀⢽
⡯⠀⠈⢀⠈⠠⠀⠄⠐⠀⠈⠀⢀⠐⠀⠄⠂⠐⠀⠂⠢⠊⠠⢑⠢⡑⡐⠄⣂⢢⠪⡝⢌⢁⢐⢐⠕⠝⠟⢟⠻⡐⡈⢨⢢⢣⢇⢪⡸⠨⠊⠊⠀⠂⠂⢂⠄⠐⠀⠁⢀⠀⠄⠂⠐⠀⠀⠂⠠⠀⠀⣽
⡯⢂⠡⠀⡀⠂⠂⠐⢀⠁⠄⠡⠐⠀⠁⡀⠂⠠⠀⠄⠨⢈⢂⠠⡂⡐⠌⡃⠕⠡⠃⢂⠂⠂⠂⠂⡁⢁⠁⠄⢁⠈⢀⠁⡁⡑⢌⢃⠪⠈⡂⠅⠁⡀⠁⠐⠀⠠⠀⠁⠀⡀⠂⠁⠈⠀⠡⡈⠠⣲⢅⢾
⣿⣔⣵⣱⣔⣕⣬⣴⣤⣢⣬⣤⣬⣰⣤⣰⣌⣎⣎⣎⣥⣥⣥⣅⣎⣬⣰⣨⣨⣬⣌⣆⣆⣥⣮⣢⣔⣔⣔⣌⣔⣬⣰⣠⣢⣤⣢⣔⣤⣤⣠⣄⣅⣄⣆⣅⣤⣄⣤⣄⣥⣠⣢⣢⣢⣕⣥⣢⣽⣜⣵⣿
```

Bild der Version: `telegrafenberg-panorama` · © 2026 André Stiebitz · CC BY 4.0

### Hinzugefügt

- Neues Projekt aus hochgeladenen Daten (Etappe U2a, nur Admins, ab Werk aus): Upload in Stücken zu 64 MiB mit
  SHA-256 je Stück, fortsetzbar (`/api/v1/uploads`), Importauftrag `neu_mesh` (GLB oder PLY ohne Textur) bzw.
  `neu_splats` (PLY, SPZ, SPLAT) über `/api/v1/importe`; der Import-Worker baut das Paket isoliert mit
  `scanview-import neu-ohne-wolke`, prüft es mit `verify` und schaltet es über die Übergabe und das unveränderte
  Freischalt-Skript frei. Portal-Seite „Uploads“, MCP-Werkzeuge `import_starten` und `import_status` (Hochladen
  bleibt bei Mensch oder Skript). Ohne Koordinatensystem trägt das Paket „lokal, Modellmaß“, der Viewer weist darauf
  hin. Einschalten: `SCANVIEW_IMPORT=1` mit Pflichtwert `SCANVIEW_UPLOAD_QUOTE_GB` in
  `/etc/scanview/scanview-joblaeufer.env` (setzt den Jobläufer voraus); Caddy erlaubt 65 MiB nur für Upload-Stücke und 13 MiB für die Upload-Ankündigung.
- Skripte statt Werkzeuge (Etappe C1/C2): öffentliche OpenAPI-Beschreibung der API v1 unter
  `docs/public/api/openapi.json` — genau die Operationen, die ein API-Token erreicht, mit beschriebenen Antworten
  (Sitzung, Projekte, POIs, Messungen, Auswertungen, Ergebnis) und einem einheitlichen Fehlerschema. Offline aus
  dem Server erzeugt (`python -m scanview_server.openapi_v1`); der Dienst liefert weiterhin keine öffentliche
  OpenAPI-Route aus, die angemeldete Sicht `GET /api/v1/openapi.json` zeigt dieselben Beschreibungen. Dazu der
  Client `sitebits` (`clients/sitebits/`, TypeScript ohne Laufzeit-Abhängigkeiten, nur `fetch`) mit typisierten
  Funktionen je Operation, Beispielskripten (rote Zeilen aller Projekte, POIs als CSV, Auswertung starten und
  warten) und der Anleitung [Skripte statt Werkzeuge](docs/public/skripte.md).
- Auswertungen auf dem Server (Etappe U1b/U1c): Projektleitung und Admins reihen einen Prüfbericht ein
  (`POST /api/v1/projekte/{pid}/auswertungen`, Typ `auswerten_pruefen`, Profil aus fester Liste), lesen Stand,
  `ergebnis.json` und den Bericht (HTML als Download ohne Skripte) und geben ihn für Bearbeiter frei; Löschen
  entfernt Bericht, Auftrag und Freigabe. Ein Token für KI-Agenten darf starten, nicht freigeben oder löschen.
  MCP-Werkzeuge `auswertung_starten`, `auswertung_status`, `auswertung_lesen` (nur Urteile je Zeile, keine
  Rohdaten). Ab Werk aus (`SCANVIEW_JOBS_DB`, `SCANVIEW_BERICHTE`); Migration 0014 (Freigaben).
- `scanview-import-worker`: eigener Dienst, der Aufträge aus `jobs.db` FIFO und einzeln abarbeitet — je Auftrag eine
  transiente systemd-Unit ohne Netz und ohne Unix-Sockets, Paket nur lesend, nur der Arbeitsordner beschreibbar,
  RAM (Prüfen 2 GB), Laufzeit und Prozesse begrenzt; Herzschlag (`/api/gesundheit` meldet `worker`), neustartfest,
  Selbsttest der Isolation vor dem Start.
- `einrichten.sh` richtet den Jobläufer mit ein, ab Werk aus: Schalter `SCANVIEW_JOBLAEUFER=1` in der neuen
  `/etc/scanview/scanview-joblaeufer.env`. Prüft vor jeder Änderung systemd ≥ 257, cgroup v2 mit `memory`, logind,
  `dbus-user-session`, Arbeitsspeicher (Budget `SCANVIEW_JOBS_RAM_GB` + 1 GB) und die Import-Umgebung und bricht
  sonst ab; legt Nutzer `scanview-import` (Linger), Gruppe `scanview-jobs`, `/srv/scanview/{jobs,berichte}` und die
  Unit `scanview-import-worker` an, begrenzt den RAM aller Aufträge auf `user@<uid>.service` und wartet auf den
  bestandenen Selbsttest. Webdienst und Sicherung bekommen Drop-ins (Hauptunits unverändert); die Sicherung nimmt
  `jobs.db` (7 Stände) und einen Spiegel der Berichte mit. `0` hält den Dienst wieder an und entfernt Units,
  Drop-ins und Linger, Daten bleiben. Weitere Instanzen auf demselben Host bekommen keinen Jobläufer; die
  Instanznamen `import` und `jobs` sind gesperrt.
- `scanview-import fotos-colmap` und `fotos-odm`: orientierte Fotos aus COLMAP und OpenDroneMap, ohne eigenen
  Kameraexport. Prüfungen wie bei `fotos-kameras`; das Kameramodell belegen die Merkmalspositionen der Rekonstruktion
  (Median-Restvektor ≤ 0,1 px, `provenance.modelCheck`). ODM: eigene UTM-Umrechnung mit echter Kameradrehung
  (am Testdatensatz Aukerman Rückprojektion 1,49 statt 2,02 px gegenüber ODMs eigener Fassung).
- Viewer, Mesh-Ebenen: Darstellung wie in Modell-Inspektoren — Fläche **Textur / Grau / Normalen / Aus** und
  **Drahtgitter** (allein oder über der Fläche); „Schattierung“ wirkt weiter als Licht auf die Textur, Grau ist immer
  beleuchtet. Ohne Normalen im Paket schattieren Grau und Normalen flach im Shader (kein Zusatzspeicher); passt die
  Schattierung nicht ins Grafikbudget, wird sie flach statt abgelehnt. Drahtgitter kostet 24 Byte je Dreieck im
  Mesh-Budget und bleibt über dem Budget aus; Messen und Verdecken laufen immer über die Fläche (bei „Aus“ gar nicht).
- Viewer: Panel **Sonne** (Pakete mit Mesh-Ebene und georeferenziertem Koordinatensystem): Sonnenstand zu Datum und
  Uhrzeit (Ortszeit Berlin, MEZ/MESZ), Zeitraffer, Sonnenbahn mit Sommer-/Wintersonnenwende, Azimut/Höhe und
  Auf-/Untergang. Mesh-Ebenen werfen und empfangen Schatten; Schattenkoordinaten präzise auch in UTM (Rechenfehler
  < 1 mm statt mehrerer Dezimeter auf dem three.js-Standardweg). Punktwolken und Sichtschichten werfen noch keine
  Schatten. Bei lokalem Koordinatensystem ist die Sonne nicht verfügbar (Hinweis).
- Viewer: Panel **Kontrollpunkte** (J3b): Kontrollpunktliste (CSV im Schema des Katalogprüfers, `# crs = EPSG:…+…`)
  lokal laden, Punkt wählen (Kamera fliegt hin), Marke am Modell picken (Punktwolke oder Mesh, wie beim Messen) und
  die Liste mit Ist-Werten exportieren, die `scanview-pruefen --kontrollpunkte` direkt liest (Lage RMSE_H,
  Maßstab). Bezugssystem muss zum Paket passen, sonst Ablehnung ohne Umrechnung. Bewusst ohne Sollmarker und ohne
  Abweichungsanzeige (unabhängige Messung). Picks werden als lokaler Entwurf je Projekt und Soll-Liste (SHA-256)
  gesichert und beim erneuten Laden derselben Liste zum Übernehmen angeboten; Abmelden im Portal löscht sie.
- `nachweise/codekarte/codekarte.sh`: Code-Karte (CodeCharta) eines Git-Stands als statische Datei — Analyse ohne
  Netz im festen Image, ohne Autoren, mit derselben Begriffsprüfung wie die Export-Probe. Für sitebits.de/code/
  nur aus dem öffentlichen Export erzeugt; README verlinkt die Karte.
- `scanview-sonne bericht` (Extra `[sonne]`): Verschattung und Ertrag für eine gemessene Fläche am Mesh eines
  Pakets. Richtungskarte je Stichpunkt (Embree-Raycast), Direktmaske, Himmelssicht und Horizontband getrennt,
  Wetter aus PVGIS 5.3 (2005–2023, eigener Fernhorizont statt PVGIS-Horizont), pvlib mit Huld-Modell, Faiman und
  AC-Kappung; Ertrag mit Band P50/P90, Sonnenstunden je Monat, Wärmekarte, PDF-Bericht mit PVGIS-Gegenprobe.
  `scanview-sonne gegenprobe` vergleicht den Rechenweg ohne Verschattung mit PVGIS PVcalc.
- `scanview-sonne garten`: Sonnenbericht für eine Gartenfläche im Grundriss. Boden je Stichpunkt per Strahl von
  unten (Suchfenster um die Eckenebene), direkte Sonnenstunden je Punkt und Monat, Lichtlage nach RHS
  (Juni/Juli-Mittel, Grenzbereich ±0,4 h), Himmelssicht, Kategorien- und Stundenkarte, PDF nach der Berichts-Norm.
  Zweite Rechnung mit geschlossenen Baumkronen (Kronen im Mesh sind löchrig), alle Zahlen als Spanne.
  Pflanzenliste noch nicht enthalten.
- `scanview-pruefen` (Etappe P, Stufe P1): Lieferung gegen Anforderungskatalog (TOML) prüfen, Urteil je Zeile
  erfüllt / nicht erfüllt / nicht prüfbar mit Grund und Belegen, Prüfbericht nach der Berichts-Norm (HTML/PDF) und
  deterministisches `ergebnis.json`. Prüfarten Bezugssystem, Klassen, Raster (GeoTIFF ohne GDAL), Formate,
  Prüfsummen, Anhang, Bericht; Beispielprofil „Bestandserfassung Liegenschaft“ mit 18 Zeilen.
- `scanview-pruefen` Stufe P2: Punktdichte, Datenlücken und Bodenauflösung über der Auftragsfläche des
  Auftraggebers (`--flaeche`, GeoJSON), mit Lücken- und Dichtekarte im Bericht. Doppelt gelieferte Wolken zählen
  nicht doppelt (Maximum je Zelle); die Bodenauflösung kommt aus den orientierten Fotos eines Pakets.
- `scanview-pruefen` Stufe P3 (Genauigkeitsnachweis F15 Teil 2): Kontrollpunkte als CSV aus der Lieferung oder vom
  Auftraggeber. Höhe von siteBits an der Wolke gemessen, Lage aus den Ist-Werten der Liste nachgerechnet, RMSE mit
  eingerechneter Genauigkeit der Kontrollpunkte; Passpunkte zählen nie, Bezugssysteme werden nie still umgerechnet.
  Maßstab aus Kontrollpunktpaaren.
- `scanview-pruefen` Stufe B1: Boden gegen das amtliche DGM des Auftraggebers (`--dgm`). Je DGM-Pixel wird das Mittel
  der Bodenpunkte mit dem DGM verglichen; `klassen_richtig` findet Dächer oder Bewuchs, die als Boden klassifiziert
  sind (mit Karte), `einpassung` die mittlere Höhenabweichung auf dem Boden. Beispielprofil um Zeile L19 ergänzt.
- `scanview-pruefen`: Kontrollpunktlisten aus dem Viewer (am Modell gepickt) werden als solche ausgewiesen; jeder RMSE
  wird zur Orientierung in BFR-Verm OGL/OGH, USIBD LOA und Eckstein-Stufen eingeordnet. Bezugssystem-Deklarationen
  gelten unabhängig von der Schreibweise, ein BOM am Dateianfang stört nicht mehr.
- `scanview-pruefen` Bodenauflösung (L06): Mit Referenz-DGM misst sie bis zum DGM statt bis zur Bodenklasse der
  Lieferung — als Boden klassifizierte Dächer machten sie sonst zu günstig. Ohne DGM nennt der Beleg diesen Vorbehalt.
- `scanview-import convert --rgb-8bit`: LAS/LAZ mit Farbe 0 bis 255 in den 16-bit-Feldern (ODM) auf 16 bit skalieren;
  bricht ab, wenn ein Wert über 255 liegt.

### Geändert

- **Viewer, Controller:** Die Steuerung ist neu und folgt der Konvention von Konsolen- und PC-Spielen. Sie wirkt jetzt
  in der Übersicht, im Begehen, im Panorama und in Dialogen statt nur im Begehen. Linker Stick bewegt, rechter Stick
  schaut bzw. dreht um den Drehpunkt. L2/R2 (LT/RT) sind analog die Höhe bzw. der Zoom, L1/R1 wechseln das Werkzeug.
  ✕/A setzt und bestätigt, ○/B geht zurück wie Esc, □/X zielt bzw. schaltet den Fang, △/Y startet das Begehen bzw.
  setzt einen Befund. Options öffnet ein Menü, Create bzw. der Touchpad-Klick zeigt die Belegung.
  Die Sticks haben eine radiale Totzone (0,15), eine Ansprechkurve und eine Beschleunigung beim Halten. Alles rechnet
  mit der Bildzeit, unter reduzierter Bewegung ohne Nachlauf. Beim Verbinden erscheint eine Meldung, und eine
  Kontextleiste nennt die Knöpfe. Die Symbole folgen dem Controller (PlayStation, Xbox, sonst neutral). Neu sind die
  Einstellungen „Controller-Empfindlichkeit“ und „Vibration“. Die Belegung steht im Dialog „Bedienung“ und in
  [Controller-Steuerung](docs/public/controller.md).
  Gegenüber bisher setzt ✕/A statt R2, Zielen liegt auf □/X statt L2, die Höhe auf L2/R2 statt auf dem Steuerkreuz,
  Options öffnet das Menü statt der Karte. Eigene Controller-Umbelegungen aus früheren Versionen werden verworfen.
  Tastatur und Maus sind unverändert.
- **Viewer und Portal auf Handy und Tablet:** Auf dem Handy eine Leistenzeile oben (Pfeil statt „← Projekte“,
  Werkzeuge, Fenster-Symbol), seltene Werkzeuge hinter „Mehr“, Tippziele 44 px. Der Viewer startet ohne offene
  Panels, es ist immer nur ein Panel als Blatt offen (quer rechts), Befundformular und Entwurf ebenfalls als Blatt.
  Leisten, Docks, Blätter und Dialoge halten sichere Ränder ein (Notch). Auf Desktop und Tablet stehen Meldungszeile
  und Scan-Hinweis in der freien Lücke zwischen Panels und Leisten; Tastenhinweise treten zurück, wenn sie verdeckt
  wären. Geprüft über 15 Geräte (hoch und quer) ohne sich überdeckende Bedienelemente.
- Release-Manifest Schema `/3`: Das Import-Werkzeug (`scanview-import-X.Y.Z.tar.gz`) wird ausgeliefert und auf dem
  Host entpackt, weil der Jobläufer daraus die Import-Umgebung baut. Es zählt damit in Scan-, Lizenz- und
  Befund-Gate und im Wochenscan wie web, server und mcp. Archivierte Manifeste `/1` und `/2` bleiben gegen ihre
  eigene Auslieferungsmenge prüfbar; ein `/2`-Manifest mit ausgeliefertem import oder ein `/3`-Manifest ohne wird
  abgelehnt. `import/` trägt jetzt die gemeinsame Produktversion. Der Inhalt der Werkzeug-Images des Imports
  (PDAL, untwine) steht in einem eigenen, nicht ausgelieferten Nachweis-SBOM `scanview-importbilder-X.Y.Z.cdx.json`;
  das ausgelieferte Import-SBOM beschreibt nur die Umgebung aus `import/uv.lock`.
- `scanview-server.env.beispiel` trägt die Auswertungspfade nicht mehr; sie stehen in
  `scanview-joblaeufer.env.beispiel` und erreichen den Webdienst nur mit eingeschaltetem Jobläufer.
- Viewer: Der Hinweis „Modellmaß — Maßstab nicht belegt“ erscheint bei Messungen über mehrere Ebenen einmal mit
  allen Quellen; Anzeige englisch, wenn der Browser nicht auf Deutsch steht. Exporte bleiben deutsch.
- Foto-Importe akzeptieren ein Paket-CRS ohne EPSG-Autorität, wenn PROJ es als gleichwertig erkennt (ODM-LAZ).
- `images.json`: `provenance.maxReferenceErrorPx` steht nur noch, wenn Referenzpaare geprüft wurden.
- Import: Die bisherigen Befehle `panos-…` und `fotos-…` mit Herstellerbezug im Namen heißen jetzt
  `panos-kameras` und `fotos-kameras` (neben `fotos-colmap` und `fotos-odm`); die alten Namen entfallen ohne
  Alias, Skripte bitte anpassen. Ebenso heißt der vorgesehene Auftragstyp jetzt `ergaenzen_fotos_kameras`.
  `kameras.json` ist als herstellerneutrales Austauschformat beschrieben (Felder, Einheiten, Bezugssystem,
  Achsen, Kameramodell; `import/README.md`), so dass jede Photogrammetrie-Software es erzeugen kann.
  Exportskripte für einzelne Programme gehören nicht mehr zum Repository. Neue Fotoebenen vermerken
  `provenance.source: "kameras"`; vorhandene Pakete behalten ihren bisherigen Wert.
- `kameras.json`: Sensoren tragen `rollingShutter` und `calibrationAdjusted`. `fotos-kameras` bricht ab, wenn ein
  Sensor Rolling-Shutter-Kompensation nutzt oder keine angepasste Kalibrierung hat — bisher prüfte das nur das
  Exportskript. Fehlen die Felder (ältere Exporte), gilt `false` bzw. `true`.

### Behoben

- Upload-Ankündigung (`POST /api/v1/uploads`) mit langer Dateiliste: Caddy kappte den Körper bei 1 MB, das Schema
  erlaubt bis 10 001 Einträge mit Pfaden bis 1300 Zeichen (≈ 12,8 MiB). Schon ab durchschnittlich ~65 Zeichen
  Pfadlänge bei voller Liste kam ein leeres 413 von Caddy statt einer Antwort des Dienstes. Eigene Grenze 13 MiB nur
  für diese Route, Rechenweg in `scanview.caddy`, Drift-Test gegen das Schema (B-FUZZ-5). Betraf nur unveröffentlichte
  Stände.
- OpenAPI: Fehlerantworten der Datei-Routen (CSV, XLSX, JSON Lines, Prüfbericht) als `application/json` beschrieben
  statt mit dem Medientyp der Datei; HEAD des Upload-Stands ohne Fehlerkörper beschrieben. Nur Beschreibung und
  Client-Typen, Verhalten unverändert.

### Sicherheit

- certifi 2026.7.22 → 2026.10.19 in MCP-Server und Import (GHSA-6q48-6c7w-9gw2, kritisch: das CA-Bündel
  enthielt die emSign-Wurzel, deren Zwischenzertifikate kompromittiert sind; betroffen sind alle bisherigen
  Stände mit MCP-Server). Pillow 12.3.0 → 13.0.0 im Import (29 Schwachstellen in Bilddecodern und -encodern,
  darunter CVE-2026-112393, CVE-2026-112403 und CVE-2026-112900 kritisch; der Import wird mit diesem Release
  erstmals ausgeliefert, kein früherer ausgelieferter Stand ist betroffen). Befund B-SCAN-IMPORT-LOCK.
  Betreiber: Update einspielen, keine weitere Maßnahme.
- Neu ist eine Vertrauensgrenze: Der Server liest mit Auswertungsjobs fremde
  Dateien; das geschieht nur im Import-Worker mit Isolation je Auftrag (Bedrohungsmodell
  `docs/public/sicherheitsarchitektur.md`). Ab Werk aus. Mit dem Import kommen zwei weitere hinzu (Uploader ↔
  Webdienst, Worker ↔ Übergabe ↔ Freischalten), ebenfalls ab Werk aus.
- Der KTX2-Transcoder des Viewers enthält jetzt den Zstandard-Decoder 1.5.7 statt 1.4.9 (eigener Bau aus
  gepinnter Quelle, Schnittstelle zu three unverändert; Lieferketten-Befund B-LK-ZSTD behoben). Für 1.4.9 meldet
  OSV `OSV-2022-15`; die Lücke liegt im zstd-Kompressor, der nie eingebaut war, also keine ausnutzbare
  Schwachstelle in ausgelieferten Ständen. Weil Mesh-Texturen inzwischen aus hochgeladenen Paketen kommen, läuft
  der Decoder auf aktuellem Stand statt mit einer VEX-Ausnahme. Betreiber: keine Maßnahme.
- Lieferkette des Imports gehärtet (B-LK-IMPORT): fester Mindestalter-Stichtag in `import/pyproject.toml` und
  `uv.lock`, Build-Backend `hatchling==1.32.3` gepinnt, Fremdpakete nur als Wheel.
- **Maßnahme der Betreiber (alle Hosts, vor dem ersten Release mit Manifest-Schema `/3`):** Die vorhandene geprüfte
  Skriptkopie (`/root/scanview-deploy/<alte Version>`) lehnt `/3` ab (`NACHWEIS-FEHLER manifest: unbekanntes
  Schema …`, nichts wird verändert), und das bisherige `/root/scanview-bootstrap.sh` bricht mit
  `Abbruch: Artefakte` ab, weil es genau drei Archive erwartet. Ablauf:
  1. `/root/scanview-bootstrap.sh` durch den Block aus [`docs/public/betrieb.md`](docs/public/betrieb.md),
     Abschnitt „Aktualisieren“, Schritt 2 ersetzen — aus der Anleitung (Quelltext oder öffentliches Repository),
     **nie aus dem Release**. Er kennt die Auslieferungsmenge je Schema (`/1`, `/2`: web, server, mcp; `/3`: dazu
     import) und bricht bei einem unbekannten Schema ab.
  2. Release-Ordner wie gewohnt hochladen und `sudo bash /root/scanview-bootstrap.sh <upload-ordner> X.Y.Z`
     ausführen. Das Skript prüft Signaturen gegen `/etc/scanview/allowed_signers`, Abschluss und die Prüfsummen
     aller vier Archive, entpackt erst danach die Prüfskripte aus dem geprüften Server-Archiv nach
     `/root/scanview-deploy/X.Y.Z` und richtet mit ihnen ein (`einrichten.sh --release` prüft alles noch einmal).
  3. Künftige Updates laufen wieder mit dieser neuen Skriptkopie. Nie den `release_pruefen.py` oder das
     `einrichten.sh` eines noch ungeprüften Releases direkt aufrufen.

## [0.10.3] – 2026-10-19

Erste öffentliche Version auf GitHub. Inhaltlich wie 0.10.2, dazu Dokumente für die öffentliche Ablage und ein paar
Eigenheiten für Neugierige. Keine Migration, keine neuen Pflicht-Variablen.

**Betreiber:** nur aktualisieren.

```
⡕⡵⡱⡕⡵⡱⡕⡵⡱⡕⡵⡱⡕⡵⡱⡕⣕⢵⢱⢕⢕⢇⢗⡕⡧⡳⣱⢣⡳⡕⡧⡳⡕⣗⢕⢗⣕⢗⣕⢗⣕⢗⣕⢗⢵⢳⢕⣗⣕⢧⡳⣕⢧⢳⡱⡕⡵⡱⣕⢵⢱⡣⡳⡱⡕⡵⡱⡕⡵⡱⡕⣕
⢎⢮⢪⢎⢮⢪⡪⡎⡮⡪⡎⡮⡪⡎⡮⣪⢪⡪⡪⣪⢣⡫⡎⡮⡺⡸⡪⣣⡣⣳⢹⢜⢞⢼⢕⢯⡪⡧⣳⢳⣕⢯⢮⡫⣏⢷⢝⡶⣕⢷⢝⡮⣺⢕⡵⡝⣎⢧⡳⡕⡧⡳⡹⡜⣎⢞⢜⢎⢞⢜⢎⢮
⡣⡳⡱⡕⣇⢧⢣⢇⡗⣕⢧⢳⢕⢽⡸⡜⣎⢮⢺⢜⢎⢮⢺⡸⣪⢳⡹⡲⡹⣜⢵⢝⣝⢮⣫⡳⣝⡮⣗⢯⣞⡵⣯⣻⣺⢽⡽⣺⢽⣝⣗⡯⣗⣟⡮⡯⣞⡵⣳⢝⢮⡫⡮⣳⢱⢝⢎⢗⡝⣜⢵⢱
⣝⢮⢳⡹⣜⢮⢳⢵⢝⡼⣪⡳⣝⣕⢗⡽⣜⢮⡳⣕⢯⡪⡧⡫⡮⡺⣜⢵⡫⡮⣳⢝⣮⣳⡳⣝⡷⡽⣝⣷⣳⡯⣷⣗⣯⣿⡽⣯⣿⣺⣗⣟⣷⣳⣯⢯⣗⣯⣗⡯⡷⣝⣞⢮⡳⣝⢵⡳⣹⢜⢮⡳
⡮⣳⣝⣞⢮⣳⣫⢷⣝⢾⢵⣻⣪⢾⢵⣫⢮⣗⢽⣪⢗⣽⣪⢯⢞⡽⣪⢷⢽⣝⢾⣝⣞⣮⢯⣷⣻⣽⣟⣾⢷⣟⣿⣾⢿⣷⣿⣿⣾⣿⣽⣯⣿⢷⣻⣯⡿⣾⣺⣽⢯⣗⣗⡯⣞⣗⡽⡮⣳⣫⢗⡽
⡯⣗⡷⡽⣽⣺⣞⣗⣯⡯⣟⣾⣺⣽⡽⣞⣯⢾⣻⣺⣽⣺⣺⡽⣽⢽⡽⣝⡷⣽⣳⣻⢾⣽⣻⣾⣻⣾⣯⣿⣿⣿⣿⣿⣿⣿⣿⣿⣷⣿⣿⣿⣿⣿⣿⣿⣿⣟⣯⣿⣯⣷⢿⣽⣳⣗⣯⡯⣷⢽⣽⣺
⣿⡽⣯⡿⣽⣾⣳⣟⣷⣟⣿⣳⡿⣞⣿⡽⣯⣿⡽⣷⣻⡾⡷⣟⣯⣿⡽⣯⡿⣯⣿⣽⣿⣽⣿⣽⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⡿⣟⣯⣿⣾⢷⡿⣯⡿⣞⣷
⣷⢿⡯⣟⣿⣺⡽⣾⣳⢯⣷⣻⡽⣯⢷⣟⡿⣺⣟⣯⡷⣿⣟⣿⣽⡾⣟⣿⣟⣿⣷⣿⡿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣿⣟⣿⡿⣯⡿⣿⣽
⢯⢯⢯⣻⡺⡮⣻⣺⣺⢽⡺⣮⡻⡺⡽⢮⢯⢗⣗⣗⢯⣗⣟⢞⣗⣟⡿⣽⢽⣻⢾⢯⡿⣿⣻⣟⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⡿⠿⠿⡿⡿⡿⡿⡿⡿⡿⡿⠿⡿⣻⢿⢽⢯⢿⢽⢯⢟⢷⡫
⠂⡂⢁⠀⠔⣁⣑⢔⠆⢅⠃⠂⠎⠂⠂⡒⠔⠐⠀⢘⠁⠄⠐⠅⠒⠔⠔⠄⠪⠀⠂⠂⠠⠀⠄⠠⡠⢀⠄⠀⠀⠠⠠⡀⡄⡀⡀⡀⡀⡀⢄⠄⠀⠀⠄⠀⠀⠀⠀⡀⡀⣄⠠⠀⢀⠂⡂⠂⢂⢂⠂⡂
⠈⢌⠔⠅⡣⡑⡕⡜⡌⢇⢣⠋⢆⢒⢖⢴⠲⡢⣢⢪⡂⢉⡆⠀⠀⡂⡤⢢⢒⠁⢠⠂⡀⠀⢁⢁⢐⠁⢨⢀⠄⠁⠁⠢⠠⡱⡠⢂⠃⠤⠁⠄⡸⢰⡑⠌⠄⠐⠠⡐⡄⡤⠂⠈⠐⠑⢨⠨⡂⠡⡐⠐
⢀⣇⢊⠉⡣⣉⣄⣔⠭⢖⢅⢣⢣⢳⠙⡓⢱⡘⡌⡎⠎⢇⠊⡒⢤⢈⢄⣡⢱⢈⠆⡢⢸⣊⠣⡱⡑⠕⡕⣇⡆⠐⠀⢀⠀⡔⡜⡬⡙⡀⠁⡘⣜⠠⠘⡨⡀⡄⢈⠄⢂⠄⠢⣅⠨⣨⣈⡘⠡⢑⢨⢀
⡐⡔⢇⢕⢀⠸⣲⢖⢯⠻⠝⠿⢯⢇⡂⡊⡀⡐⡅⢏⢎⢂⢂⠐⠡⠨⡺⣪⢧⣣⡢⡯⣳⣞⣗⣎⣇⣯⣪⡲⢔⠬⢰⢑⠕⢅⢑⠀⢁⠡⠢⡵⣇⡇⠅⠓⡉⡈⡤⡨⡘⠜⠉⠔⡕⡦⢎⢵⢈⠂⡎⢌
⢪⢑⢕⢇⡇⡌⡂⠣⡱⠕⡐⡍⢎⢎⡣⡳⡘⢴⡪⣎⢧⣣⡢⡱⣐⠡⡑⢕⢕⢕⢬⣫⣿⣨⣩⣰⣸⣨⣡⣷⡲⡸⡔⡤⠕⠝⢌⢆⢕⢌⢏⢫⢐⠕⠅⡉⡊⡌⡔⢐⠄⢍⡐⢀⠰⣙⠎⡢⢂⢫⡾⡕
⢪⢪⢪⣞⢖⢜⢼⢸⡐⡅⢆⠑⢁⢓⢝⠺⠪⢿⢺⣿⡽⡾⡷⡕⡆⡣⢪⠪⡪⠸⡰⠸⡿⠛⣿⠙⡙⡿⠛⠿⡕⢝⢸⢪⢳⢸⢰⢱⢱⠺⠹⠮⡗⠯⡁⠐⢕⠮⠓⠱⠩⡒⠌⢂⠝⡜⡌⡪⡲⠹⢪⠪
⢱⢸⢸⢲⡵⣓⢫⠩⡑⣄⡥⡕⡕⡵⢆⢇⢏⢮⢆⢪⢹⡪⡺⡪⡱⡱⡡⡣⡑⡑⡌⢕⢌⢪⠠⡂⡢⡨⡨⡰⡡⡱⠡⠣⡳⡱⡣⡣⡣⡙⢜⢜⢔⢵⢰⠬⡢⢢⠣⡊⢤⢦⣶⣶⣽⣳⣓⡃⣫⣥⣵⡕
⡪⡪⡪⡣⡫⡓⡳⡫⣫⢪⡢⣣⢳⢹⡸⡕⣝⢜⢎⢮⢪⡪⡎⡞⣜⢜⢜⢌⠎⢌⠢⢑⢅⢌⢜⢸⡘⡌⣊⢢⢑⢌⡪⢪⢊⢎⢎⢞⣜⢼⢱⡱⡅⡇⡇⡇⡇⡇⡇⡇⡇⡇⣇⢏⣛⡻⡫⡳⡟⡿⡗⣇
⡎⣞⢜⢎⢞⡜⣕⢝⢜⢜⡜⣜⢎⢇⢧⢫⡪⡎⡧⡣⡣⡥⢬⢌⢎⡜⡜⣌⢎⢖⢕⢕⡲⡱⡩⣊⢎⣪⢢⢣⡣⡳⡸⡕⣕⢇⢏⡎⣎⢎⢎⢆⢇⡇⣏⢎⢎⡎⣎⢮⢪⢣⡣⣣⢣⡣⡳⣹⢸⢪⡪⡲
```

Bild der Version: `2023-06-21-dji360-1410-verbessert-nr-panorama` · © 2026 André Stiebitz · CC BY 4.0

### Hinzugefügt

- Ein paar Eigenheiten für Neugierige in Konsole und Kommandozeile.

### Dokumentation

- Öffentliche Repo-Dokumente für den ersten GitHub-Push vorbereitet: Links zu Quelltext, Issues
  und Releases, englische Issue- und Pull-Request-Vorlagen samt vertraulichem Sicherheitsmeldeweg.
- Sicherheitsrichtlinie: unterstützte Versionslinie 0.10.x und zusätzlicher Schlüssel-Download
  über sitebits.de, mit unverändertem Fingerabdruck.
- GitHub-Vorlagen in Positivliste und REUSE-Abdeckung aufgenommen; Verweise auf die interne
  Entwicklungsumgebung in der öffentlichen Dokumentation bereinigt.

### Sicherheit

- Keine behobenen Schwachstellen. Maßnahmen der Betreiber: keine.

## [0.10.2] – 2026-10-18

Patch für die Bedienung auf dem Handy: Flächen schließen, Werkzeugleiste, Hinweise, Messbeschriftungen, Querformat.
Dazu ein Fix im Kameraexport des Imports. Keine Migration, keine neuen Pflicht-Variablen.

**Betreiber:** nur aktualisieren.

### Behoben

- Fläche messen auf dem Handy: Ein Tipp in der Nähe des ersten Eckpunkts schließt die Fläche (Fang 36 px mit dem
  Finger, 16 px mit der Maus, ab drei Eckpunkten); nach dem dritten Punkt sagt eine Meldung, wie man schließt.
  Abschließen und Verwerfen stehen beim Zeichnen vorn in der Leiste — vorher lagen sie auf dem iPhone abgeschnitten
  am Ende.
- Werkzeugleiste auf schmalen Bildschirmen: Teilen, Messliste, Hilfe, Einstellungen und Vollbild liegen hinter
  „Mehr“ statt abgeschnitten am Rand. Am Desktop unverändert.
- Hinweise auf dem Handy (Scan-Filter im Panorama, Bedienhinweise) liegen über dem Panel statt darauf; der Hinweis
  zum Scan-Filter lässt sich wegtippen und kommt erst bei einer anderen Filterart wieder.
- Messbeschriftungen: der Messwert steht allein in der ersten Zeile, Zusätze (nicht eben, Modellmaß, Vorschlag)
  kleiner und umbrechend darunter; auf dem Handy nur an der ausgewählten Messung, sonst ein Zeichen ⚠/ⓘ. Vorher
  lief eine einzige lange Zeile über den Bildschirmrand und überlagerte andere Messungen.
- Handy quer: bekommt jetzt das mobile Layout mit dem Panel als Leiste rechts (vorher schwebende Desktop-Panels,
  die das Bild zudeckten).
- Import, Kameraexport (Exportskript der Photogrammetrie-Software): Tie Points im Unendlichen (homogene Koordinate
  w = 0) werden übersprungen. Vorher brach der Export mit einer Division durch null ab, und die Software stürzte
  danach ab.

### Sicherheit

- Keine behobenen Schwachstellen. Maßnahmen der Betreiber: keine.

## [0.10.1] – 2026-10-17

Erste öffentliche Version unter dem Namen **siteBits**, dazu ein Patch für Handys. Ausgeliefert werden Viewer und
Portal (`web`), der Plattform-Server (`server`) und der MCP-Server (`mcp-server`). Der Quelltext des Imports
(`import/`) ist ab dieser Version Teil des öffentlichen Repos. Keine Migration, keine neuen Pflicht-Variablen.

**Betreiber:** nur aktualisieren. Für Handys lohnt es sich, große Mesh-Ebenen mit
`scanview-import mobilstufe` um eine Mobilstufe zu ergänzen und das Paket neu bereitzustellen.

### Hinzugefügt

- Mesh-Import: dritte Stufe `layers/mesh/mobile.glb` für Handys (Standard 400 000 Dreiecke, Texturen KTX2-ETC1S
  bis 2048 px; `--mobil-dreiecke 0` schaltet sie ab), geprüft wie die Vollstufe, Warnung über 100 MiB. In
  `project.json` stehen `mobileUrl`, `mobileSizeBytes` und `mobileTriangleCount`. Der neue Befehl
  `scanview-import mobilstufe <quelle> <ziel>` ergänzt ein vorhandenes Paket ohne Originalquelle aus seiner
  `model.glb`, als neues Paket (Hardlink-Kopie, `revision` + 1); das Quellpaket bleibt unverändert. Nur-Lese-Links
  geben die Mobilstufe für Gäste frei.
- Viewer: Mesh-Ebenen mit Mobilstufe (`mobileUrl`) laden auf Handy und Tablet diese statt der Vollstufe. Sie ist
  vereinfacht und dient nur der Anzeige; Messen auf dem Mesh bleibt der Vollstufe vorbehalten (Desktop).

### Geändert

- Neuer Produktname **siteBits**: Seitentitel, Portal, Viewer-Meldungen, Web-App-Name, OpenAPI-Titel,
  Standardname der Passkey-Anmeldung (`SCANVIEW_RP_NAME`, falls nicht gesetzt) und README. Bezeichner im
  Code (Pakete, Befehle, Umgebungsvariablen, Pfade, Dienste) bleiben `scanview`; Upgrades ändern daran nichts.
- Meldestelle für Schwachstellen ist jetzt `security@sitebits.de`, Meldungen lassen sich mit OpenPGP
  verschlüsseln (Fingerabdruck in `SECURITY.md`).
- README: Herkunftsvermerk (KI-Anteil, Lizenz gilt, soweit Schutzrechte bestehen) und Supportgrenze.

### Behoben

- Viewer auf schmalen Bildschirmen (Handy): Die Werkzeugleiste war breiter als das Display, Einstellungen und
  Vollbild waren nicht erreichbar. Sie ist jetzt höchstens bildschirmbreit und seitlich wischbar, die Texte
  „Markieren“/„Teilen“ entfallen dort. Die Meldungszeile liegt mobil unter der Leiste statt darunter verdeckt.
- Vollbild-Knopf auch auf dem iPhone: Er erklärt dort den Weg über „Zum Home-Bildschirm“ (iPhone-Safari erlaubt
  Webseiten kein Vollbild); im eigenen Fenster entfällt er.
- Leistungsanzeige (Debug) sitzt jetzt klein unten links und lässt sich per Tipp oder Enter einklappen; auf
  Handy und Tablet startet sie eingeklappt. Vorher lag sie unten rechts über den Schaltern des Panels.
- Viewer auf Handy und Tablet: Eine Mesh-Vollstufe über 128 MB wird nicht mehr geladen, die Vorschau bleibt
  stehen, mit Meldung. Vorher lud der Viewer die ganze Datei und prüfte das Grafikbudget erst nach dem Entpacken;
  bei einem Mesh von 390 MB beendete Safari auf dem iPhone den Tab. Desktop unverändert.
- `deploy/platform/test/caddy-pruefen.sh` brach gelegentlich mit Exit-Code 141 ab (SIGPIPE beim Lesen des
  Statuscodes unter `pipefail`); der Statuscode wird jetzt ohne Pipe gelesen. Betrifft nur das Prüfskript.
- Mesh-Import: glTF-Primitive, die sich einen Vertexpuffer teilen (je Material ein Primitive), wurden beim
  Lesen je Primitive kopiert. Prüfberichte wiesen dadurch ein Vielfaches der Vertices aus, und an Materialgrenzen
  entstanden offene Ränder. Betroffen waren nur Prüfung und Mobilstufe, nicht die ausgelieferte Vollstufe.

### Sicherheit

- Keine behobenen Schwachstellen. Maßnahmen der Betreiber: keine.

## [0.10.0] – 2026-10-16

Funktionsversion: KI-Vorschläge mit menschlicher Prüfung, Gaussian Splats, Koordinaten, Punktklassen und
Genauigkeit im Viewer, Fotos zu einer Stelle finden, Fenster-Effekt. Ausgeliefert werden `web`, `server` und
`mcp-server`.

**Betreiber:** Migration **0013** (`befund_eintraege` wird neu aufgebaut, Inhalte und Sperren bleiben) läuft
beim Update; vorher sichert das Update die Datenbank wie gewohnt. `scanview.caddy` ist geändert (Kamera nur auf
`/ansicht`); im äußeren Caddy-Block `?Permissions-Policy` statt `Permissions-Policy` setzen, sonst bleibt der
Fenster-Effekt gesperrt (deploy/platform/README.md). Wer die Mess- oder Server-CSV über Spaltennamen oder
-positionen liest, muss anpassen (siehe „Geändert“). Keine neuen Pflicht-Variablen.

### Hinzugefügt

- KI-Agenten im Tokenformular kennzeichnen; Vorschläge im Portal unter „Zu prüfen“ einzeln oder gesammelt prüfen.
- Viewer kennzeichnet Vorschläge mit Text und gestricheltem Schild, bietet Prüfknöpfe und einen Ebenenschalter (`kv=0` im Ansichtslink). Exporte lassen Vorschläge aus.
- Befund-Fäden zeigen KI-Herkunft, offene Vorschläge und verknüpfte Entscheidungen.
- Große Gaussian-Sichtschichten als RAD/RADC: externes, per SHA256 geprüftes Spark-build-lod
  v2.3.0, Bhattacharyya-LoD und Rückprüfung sämtlicher Blattpositionen; Herkunft im Paketbericht.
- RAD-Viewer lädt Details mit begrenztem Pager nach; Ladeanzeige bis zum ersten gefüllten Bild.
  Das 1-Millionen-Gate bleibt für SPZ/PLY. Paketinhalt nennt RAD-Format, Chunks und LoD-Methode.
- Gaussian-Sichtschicht als Ebene `splats` (Spark 2.3.0), schaltbar und als Startebene über
  `initialView.layers`; Messen/Fangen bleibt auf Punktwolke/Mesh. Ressourcenbudget und LOD.
- Generischer Viewerstart ohne Punktwolke für Mesh-/Splat-Pakete, Bounds aus den Ebenen.
- Import `add-splats`: PLY/SPLAT/SPZ v2/v3, Rahmenbeleg über gleiche Koordinaten oder
  COLMAP-Kamerapaare/Ähnlichkeit, explizite Residuen-Gates und Drehung/Erhalt von SH Grad 1–3.
  Standardausgabe SPZ v3 mit quantisierungsbasierter Positionsrückprüfung, PLY als expliziter
  Notweg; höchstens eine Million Gaussians ohne RAD-Paging.
- Optionale Startkamera aus ausdrücklich angegebenen Trainings-Weltzentren; SPZ/SH-Viewer
  mit Float32-Mittelpunkten und SH-Pufferbudget.
- Paketinhalt für Agenten enthält Splat-Format, Chunkanzahl, SH-Grad und Rahmen-/Kamerabericht.
- KI-Vorschläge (G7, Server/API/MCP): Ein API-Token lässt sich als „für einen KI-Agenten“
  markieren (`"agent": true`). Was darüber angelegt oder geändert wird, ist ein Vorschlag, bis ein
  Mensch ihn annimmt: POIs und Messungen tragen `herkunft` und `pruefstand`, Löschen wird zum
  Löschvorschlag (HTTP 202), Status, Frist und Zuständigkeit im Befund-Faden werden ein Eintrag
  `vorschlag` (HTTP 202). Neue Routen `…/annehmen` und `…/verwerfen` (nur Menschen) und
  `GET …/zu-pruefen` (ab Bearbeiter). `GET …/pois|messungen` zeigt fremde offene Vorschläge nur mit
  `?vorschlaege=1`; ohne Parameter bleibt die Antwort wie bisher. Exporte und Git-Export enthalten
  nur Gültiges. Neue Felder in Antworten (`herkunft`, `pruefstand`, `loeschenVorgeschlagen`,
  Faden-Einträge `herkunft`, `bezug`) sind zusätzlich. Migration 0013 baut `befund_eintraege` neu
  auf (Inhalte und Sperren bleiben).
- Koordinate am Zeiger (F23): Taste C blendet unten links eine Statuszeile ein (im Ansichtslink `ko=1`),
  sie zeigt E/N/H mit Koordinaten- und Höhensystem bzw. „lokal“. Ruht der Zeiger, wird der Punkt wie
  beim Messen genau gesucht und festgehalten; Klick auf die Zeile oder Taste U kopiert ihn als
  `E;N;H;System;Höhensystem` (Dezimalpunkt, für GIS).
- Punktklassen und Echos im Viewer (F30): Farbmodus „Klassen“ (ASPRS-Farben, deutsche Namen), Ordner
  „Klassen und Echos“ mit einem Schalter je vorhandener Klasse und dem Filter „nur erste Echos“. Der Filter
  wirkt beim Dekodieren: ausgeblendete Klassen werden in jedem Farbmodus weder gezeigt noch beim Messen
  getroffen. Zustand im Ansichtslink (`kx=` ausgeblendete Klassen, `ec=1` erste, `ec=2` letzte Echos).
- „Nur letzte Echos“ (F30): Der Punktimport markiert bei LAS/LAZ-Quellen mit Mehrfachechos die letzten
  Echos im Standardfeld UserData (= 1) und meldet das als Attribut `returnsLast`; `verify` prüft die
  Markierung. Benutzt die Quelle UserData schon, wird nichts überschrieben und der Filter nicht
  angeboten. Grund: Giro3D filtert beim Dekodieren nur Standardfelder gegen feste Werte.
- Genauigkeit im Viewer (F15 Teil 1): Das Ebenen-Panel zeigt je Punktwolke und Mesh einen Abschnitt
  „Genauigkeit“ mit Lage, Maßstab, Quelle und Bericht, ohne Nachweis „kein Genauigkeitsnachweis“.
  Messwerte aus einer Ebene mit Nachweis tragen statt „Maßstab nicht belegt“ den genauen Stand, etwa
  „Maßstab aus 1 Strecke gesetzt (A–B Laser), nicht geprüft“ oder „Maßstab geprüft: +0,05 % an 1
  Kontrollstrecke“; das gilt auch für die Spalte „Hinweis“ der Mess-CSV. Ausgleichsresiduen und
  Kamera-GNSS ergeben nie einen ±-Wert. Ein fehlerhafter Nachweis im Paket bricht das Laden ab.
- MCP: neue Werkzeuge `poi_loeschen`, `messung_loeschen` (weich, mit `version`), `link_anlegen`,
  `zustaendig_setzen`, `frist_setzen`, `gewerke_lesen` und `paket_lesen` (neue Route `GET …/paket`: Ebenen,
  Koordinaten-/Höhensystem, Zweithöhe, Punktklassen; Fotos-Ebene nur für berechtigte Rollen) — damit decken Agenten alles am Befund-Faden ab, was
  die Oberfläche kann. Die Projektkarte (`projekt_lesen`) nennt zusätzlich `einheit` (Koordinaten- und
  Höhensystem). Übersicht Funktion → API → MCP mit Begründungen: `docs/ki-paritaet.md`.
- Fenster-Effekt (F32): Ordner „Fenster-Effekt (Webcam)“ im Viewer. Eingeschaltet folgt die Perspektive im
  Orbit dem Kopf (Webcam, Gesichtserkennung MediaPipe lokal im Browser), der Bildschirm wirkt wie ein Fenster in
  den Scan. Aus ab Werk, Kamera erst auf Klick, das Bild verlässt den Browser nicht; Hinweis mit Aus-Knopf, beim
  Wechsel des Tabs geht die Kamera aus. Solange er an ist, wird nicht gemessen; im Panorama pausiert er. Für eine
  Person vor dem Bildschirm (Laptop), nicht für Beamer. Neue Abhängigkeit `@mediapipe/tasks-vision` (Apache-2.0),
  Modell und Wasm vom eigenen Host; die eingebaute Nutzungsstatistik des Pakets (Versand an Google) entfernt der
  Bau. **Betrieb:** Kamera nur auf `/ansicht` erlaubt — im äußeren Caddy-Block
  `?Permissions-Policy` setzen (deploy/platform/README.md).
  Doku zu Koordinaten, Punktklassen, Genauigkeit und Fenster-Effekt: `docs/public/koordinaten-klassen-genauigkeit.md`.
- Genauigkeit in API, MCP und Exporten (F15 Teil 1): Die Projektkarte (`projekt_lesen`) nennt je
  Punktwolke und Mesh `genauigkeit` mit denselben Zeilen wie das Ebenen-Panel. Die Server-CSV
  `export.csv` hat eine neue letzte Spalte „Hinweis“ mit den Messhinweisen wie im Viewer (vor den
  Befund-Faden-Spalten; wer Spalten nach Position liest, muss anpassen). `export.json` trägt
  `genauigkeit`, die Mängelliste nennt Lage und Maßstab in Zeile 1, und der Git-Export legt
  `genauigkeit.json` ins Repository (im Commit der jeweiligen Bearbeitung, nur bei Änderung).
- Genauigkeitsnachweis im Import (F15 Teil 1): `scanview-import genauigkeit` liest `genauigkeit.json`
  (Pass-/Kontrollpunkte, Maßstabsstrecken, Kamera-GNSS je Gruppe), rechnet RMSE/max selbst und schreibt
  `provenance.accuracy` an die messbaren Ebenen. Nur Kontrollpunkte und -strecken zählen als Nachweis;
  Ausgleichsresiduen werden nie als ±-Wert ausgewiesen. Bericht-PDF nur per Prüfsumme vermerkt.

- Fotos zu einer Stelle finden (F4): Werkzeug im Panel „Fotos“ für einen Punkt, ein geklicktes Polygon, eine
  Flächenmessung oder die Schnittbox. Es sucht die Fotos, die die Stelle sehen, prüft Verdeckung an der
  Punktwolke und sortiert nach Abdeckung und Auflösung (mm/px). Ein Punkt zählt ab einer sichtbaren Stelle,
  ein Bereich ab 10 % sichtbarer Stichproben. Treffer erscheinen in der Bildleiste der geteilten Ansicht.
- Geteilte Ansicht 3D | Foto mit verschiebbarem Trenner (Maus, Finger, Tasten), quer oder auf Touch-Geräten
  hochkant, gemerkt je Browser; Bildleiste mit denselben Vorschaubildern wie das Panel „Fotos“.
- Rahmengröße der Kamerapyramiden (0,1–20 m) im Panel „Fotos“, je Projekt gemerkt.
- Zweithöhe in der Koordinaten-Statuszeile (F23), wenn das Paket eine Geoidundulation (`frame.undulation`)
  mitbringt; gesetzt vom Import.
- Restore-Probe für Betreiber: `deploy/platform/test/restore-probe.sh` spielt eine Sicherung auf eine
  Wegwerf-Instanz zurück und prüft Integrität, Schema, Protokoll und Start; Ergebnis als JSON-Nachweis
  (docs/public/betrieb.md).

### Geändert

- Koordinatensystem und Höhensystem beim Messen benannt (F23): Die Koordinatenspalte der Mess-CSV
  heißt jetzt `Koordinaten X Y Z [m, <System>, <Höhensystem>]`, z. B. `[m, ETRS89/UTM33, NHN]`, bei
  lokalen Paketen `[m, lokal]`. Wer die Spalte über ihren Namen liest, muss den Abgleich anpassen.
  Dasselbe gilt für den Server-Export `export.csv`; die Mängelliste (`maengelliste.xlsx`) nennt
  beides in der ersten Zeile.
- Die Merkmalskanten für den Kantenfang auf Meshes rechnet ein Web Worker; der Viewer bleibt dabei bedienbar
  (Pilotpaket: Vorbereitung 7,2 s → 0,8 s im Hauptfaden).
- `scanview.caddy`: `Permissions-Policy` mit `camera=(self)` nur für `/ansicht` (Fenster-Effekt).

### Behoben

- Portal: Scheiterte „Link anlegen“ (Nur-Lese-Link) an einer zu alten Anmeldung, stand der Hinweis oben
  außerhalb des Sichtfelds und der Klick wirkte wirkungslos. Die Anleitung erscheint jetzt am Formular.
- SBOM: MediaPipe-Dateien und das Face-Landmarker-Modell des Fenster-Effekts sind belegt.

### Sicherheit

- **B-RL-N1** (niedrig, betroffen 0.1.0–0.9.2): Paketdateien bleiben bis zu einer Stunde privat im
  Browser-Cache. Nach dem Abmelden konnte eine andere Person im selben Browser sie ohne Rechteprüfung aus
  dem Cache laden. Anmelden, Einladung einlösen und Abmelden senden jetzt `Clear-Site-Data: "cache"`
  (Cookies und lokale Entwürfe des Viewers bleiben; der erste Aufruf nach einer Anmeldung lädt die
  Programmdateien neu). Betreiber: nur aktualisieren.
- KI-Agenten-Token schreiben nur noch Vorschläge; gültig wird ein Eintrag erst durch einen Menschen. Keine
  neuen Zugänge.

## [0.9.2] – 2026-10-15

Patch für Mobilgeräte und große Meshes nach dem ersten iPhone-Test in einem Pilotprojekt. Ausgeliefert werden
Viewer und Portal (`web`), der Plattform-Server (`server`) und der MCP-Server (`mcp-server`), die beiden
Server bis auf die Version unverändert. Keine Migration, keine neuen Variablen.

**Betreiber:** `scanview.caddy` ist geändert (siehe „Geändert“) und wird wie in 0.9.1 übernommen.
Neue Paketfelder (`startVisible`) setzt nur der Import; ältere Pakete laufen unverändert.

### Hinzugefügt

- Ebenen können beim Start ausgeblendet sein: Feld `startVisible: false` je Ebene in `project.json`,
  gesetzt mit `scanview-import start-sichtbar <paket> <ebene> an|aus`. Der Viewer übernimmt es als
  Voreinstellung (auch in Nur-Lese-Links); eine ausgeblendete Punktwolke wird gar nicht erst geladen.
  `ly=` im Link hat weiter Vorrang.
- Panel „Fotos“: Die orientierten Fotos stehen als Galerie mit Vorschaubildern je Gruppe in einem eigenen
  Panel; Klick öffnet das Foto. Das Ebenenmenü zeigt nur noch einen Eintrag je Fotoebene (Tastenbelegung
  und `ly=` bleiben gleich). Vorschaubilder laden erst beim Aufklappen einer Gruppe.
- Fester Knopf „× Panorama verlassen“ im Panoramamodus (auf dem iPhone verdeckte die Infobox den bisherigen Ausgang).
- `?debug=1` zeigt in einer zweiten Zeile die geladene Mesh-Stufe, ihren Speicher und die Geräteklasse.

### Geändert

- `scanview.caddy`: Die Zeile `Referrer-Policy: no-referrer` für `/teilen` aus 0.9.1 ist entfernt.
  Sie wirkte nie: Der `header`-Block des Host-Caddyfiles (verzögert wegen `-Server`) setzt
  `same-origin` danach. Die Angabe im Abschnitt Sicherheit von 0.9.1 war damit falsch. Ein Risiko
  bestand nicht: Das Link-Token steht im Fragment, Browser senden Fragmente nie als Referrer, und die
  Teilen-Seite entfernt es sofort aus der Adresszeile.
- Touch-Geräte (`pointer: coarse`, z. B. iPhone/iPad) bekommen kleinere Grenzen: 500.000 Punkte, 256 MiB
  Mesh, 64 MiB Panoramatexturen, Mesh-Texturen eine Mip-Stufe kleiner. Gemessen am Pilotprojekt in
  iPhone-Emulation: Mesh-Speicher 485 → 165 MB, JS-Heap 620 → 296 MB. Desktop unverändert.

### Behoben

- Viewer fror beim Laden einer großen Mesh-Vollstufe 33 s und länger ein (Pilotprojekt, 2,2 Mio. Dreiecke;
  auf dem iPhone Absturz mit „wiederholt ein Problem aufgetreten“): Die Merkmalskanten für den Kantenfang
  wurden sofort und mit Text-Schlüsseln gerechnet. Jetzt erst beim Messen und über Zahlenfelder (~6 s
  einmalig, Ergebnis bitgleich).
- Haken von rund einer Sekunde bei jedem Dreh- und Zoomstart vor großen Meshes: Zoom-Stopp und Messen auf dem
  Mesh schneiden den Strahl jetzt blockweise über die Zahlenfelder statt über three.js je Dreieck (2,2 Mio.
  Dreiecke: ~1 ms statt ~160 ms je Strahl am Desktop, Treffer bitgleich).

### Sicherheit

- Keine behobenen Schwachstellen, keine neuen Zugänge. Korrektur zu 0.9.1: `/teilen` lieferte
  `Referrer-Policy: same-origin` statt `no-referrer` (siehe „Geändert“); ohne Risiko, weil das Link-Token
  nur im Fragment steht. Betreiber: neue `scanview.caddy` übernehmen, sonst nichts.

## [0.9.1] – 2026-10-15

Ausgeliefert werden wie in 0.9.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`, bis auf die Version unverändert). Neu sind Nur-Lese-Links. Das ist eine neue
Funktion mit Migration in einem Patch-Release, vor 1.0 bewusst: 0.10.0 ist für den nächsten größeren
Schritt vorgesehen. Migration 0012 (Schema 12) läuft beim Start, `scanview.caddy` ist geändert, keine
neuen Umgebungsvariablen.

### Hinzugefügt

- Nur-Lese-Links (ADR 0016): Projektleitung oder Admin legen in den Projekt-Einstellungen einen Link
  `/teilen#t=…` an, mit dem Menschen ohne Konto Punktwolke und Meshes eines Projekts ansehen, sonst
  nichts (keine Panoramen, Fotos, Befunde, Messungen, Exporte, keine Projekt-API). Schalter
  „Nur-Lese-Links erlaubt“ je Projekt, Standard aus. Gültig 14 Tage (höchstens 90), optional mit
  einfachem Passwort (ab 4 Zeichen, Sperre nach 5 Fehlversuchen), jederzeit zurückziehbar mit
  sofortiger Wirkung. Je Link zählt das Portal Öffnungen und den letzten Abruf; Anlegen und
  Zurückziehen stehen im Verlauf. Anlegen nur im Browser mit frischer Anmeldung.

### Sicherheit

- Keine behobenen Schwachstellen. Neu ist ein Zugang ohne Konto: Gäste bekommen eine Gast-Sitzung im
  Cookie `__Host-scanview-gast`, `/api/authz` lässt sie nur an eine exakte Positivliste der Wolken-
  und Mesh-Dateien. Belegt durch die Tests in `server/tests/test_teilen.py` und
  `web/e2e/plattform/teilen.spec.ts`.
- **Betreiber, Reihenfolge beachten:** erst die neue `scanview.caddy` einspielen (Gast-Antworten mit
  `Cache-Control: no-store`, `Referrer-Policy: no-referrer` auf `/teilen`), danach in einem Projekt
  „Nur-Lese-Links erlaubt“ einschalten. Mit der alten Caddy-Konfiguration würden Browser von Gästen
  Binärdaten bis zu einer Stunde zwischenspeichern und nach einem Zurückziehen noch zeigen. Solange
  der Schalter aus ist (Standard), ändert sich ohne neue Caddy-Konfiguration nichts.
- fail2ban: falsche Link-Passwörter werden wie falsche Anmeldungen gemeldet; die bestehende Regel
  greift ohne Änderung.

## [0.9.0] – 2026-10-14

### Hinzugefügt

- `fotos-kameras --rueckprojektion-grenze PX` (Standard 2, höchstens 10): hebt die
  Rückprojektionsgrenze ausdrücklich an; `images.json` nennt Grenze, Standard und die Fotos über
  2 px (`provenance.reprojection.aboveDefault`).
- `fotos-kameras --exif-drehung-ignorieren`: Fotos mit EXIF-Orientation 5 bis 8 werden als
  Sensorpixel übernommen, wenn ihre Rohpixelmaße der exportierten Kamera entsprechen (nicht quadratisch);
  Spiegeln/180° bleiben gesperrt. Vermerk in `images.json` (`provenance.exifOrientationIgnored`).
- Lageanpassung im Import: `convert --verschiebung DX,DY` (oder JSON mit `dx_m`/`dy_m`) bzw.
  `--transformation` mit einer starren 4x4-Matrix (Drehung + Verschiebung, Maßstab 1), etwa zur
  Anpassung einer Drohnenwolke an LoD2/DGM1. Vermerk in `sourceOptions.lage`; `panos-kameras`/
  `fotos-kameras` (Position, Blickrichtung, Tie Points), `add` (Mesh-Vertices, Vermerk in der
  Mesh-Ebene; frameCheck gegen die Wolke) und `verify` übernehmen sie aus dem Paket.
- Mesh-frameCheck misst nur Wolkenpunkte im Grundriss des Meshes (1-m-Raster aus den
  Dreiecksrechtecken, `frameCheck.within`). Ein Teil-Mesh (z. B. ein Gebäude im Gelände) scheiterte
  sonst an allen Punkten außerhalb (p95 30 m); unter 1.000 Punkten im Grundriss bricht `add` ab.
- `add --rahmen-grenze P50_MM,P95_MM` (Standard 5,25; höchstens 50,300): hebt die frameCheck-Grenzen
  ausdrücklich an, vermerkt in `frameCheck.limitsM`/`defaultLimitsM`/`raised`; `verify` prüft mit
  der vermerkten Grenze. `add` rechnet frameCheck und Stufenprüfung vollständig und bricht erst
  dann mit beiden Berichten ab (die Wolke wird dabei nur noch einmal gelesen).
- `add --rahmen-ohne-klassen K1,K2`: frameCheck ohne diese Wolkenklassen (z. B. Vegetation, die
  aus dem Mesh entfernt wurde), vermerkt in `frameCheck.excludedClasses`, von `verify` übernommen;
  Abbruchmeldung nennt die Stufenzahlen auch dann, wenn nur frameCheck scheitert.
- `add --ausdehnung-toleranz MM` (höchstens 500): eigene Grenze für die Ausdehnungsabweichung der
  Stufen statt `--toleranz` (die sonst p95 und Ausdehnung gemeinsam steuert), vermerkt als
  `sourceOptions.ausdehnungToleranzM`/`boundsLimitMm`; `verify` nimmt sie. Höhenumrechnung und Bewegung laufen in einem Durchgang.
- Punktklassen im Import (F30, Teil Import): LAS/LAZ/COPC-Pakete tragen ein Klassen-Histogramm der
  ausgelieferten Punkte (`cloud.classes`) und der Quelle (`sourceOptions.classes`); `attributes`
  nennt `classification` und `returns`, wenn Klassen außerhalb 0/1 bzw. Mehrfachechos vorkommen.
  `verify` prüft das Histogramm gegen COPC und Quelle. Farbmodus und Filter im Viewer folgen.
- Höhensystem im Import (Entscheidung 0015): Pakete haben ein Höhensystem, Standard DHHN2016 (NHN),
  ellipsoidisch (ETRS89) wählbar. `convert`/`add` mit `--quell-hoehensystem`/`--ziel-hoehensystem`
  rechnen Punktwolken (LAS/LAZ/PLY/PTS/Text) und Meshes zwischen ellipsoidisch und DHHN2016 um,
  über das Quasigeoid GCG2016 des BKG (mitgeliefert, CC BY 4.0) und eine explizite PROJ-Pipeline;
  fehlendes Gitter oder Punkte außerhalb brechen ab, ohne Quellangabe wird nichts angenommen.
  `panos-kameras`/`fotos-kameras` rechnen Kamerahöhen und Tie Points ins Paket-Höhensystem um.
  `verify` vergleicht mit gleich umgerechneten Quellpunkten; `sourceOptions.hoehe` belegt Quelle,
  Ziel und Gitter-Prüfsumme. E57 nur Deklaration. Neue Abhängigkeit `pyproj`.
- Messen auf Meshes: Messungen und POIs treffen sichtbare Mesh-Vollstufen; Vertex- und
  Merkmalskantenfang auf Wunsch (Taste E). „Messen auf“ entscheidet innerhalb eines 5-mm-Gleichstands;
  Panorama misst weiter nur Punktwolke. Quellen je Eckpunkt bleiben durch Liste,
  CSV/JSON, Server, MCP und Git-Export erhalten; Modellhinweise begleiten die Werte.
- Befund-/Mängelliste: XLSX-Download und Druckansicht mit „Drucken / als PDF speichern“
  auf der Projektkarte. Aktive POIs mit Koordinaten, Urhebern und UTC-Zeiten;
  Exportrechte und der Projektschalter „Export für Bearbeiter“ gelten wie bei CSV.
- Befund-Faden (Server, API, MCP, Oberfläche): Kommentare, Links, Status (offen, in Arbeit, erledigt,
  abgeschlossen, abgelehnt), Zuständigkeit und Frist je Befund, unveränderlich und mit
  Markierungsänderungen; interne Einträge nur für die Bauherrenseite, in keinem anderen Kanal.
  Liste „Fällig/überfällig“, Zähler auf der Projektkarte, Spalten in CSV, JSON und Mängelliste,
  Fäden im Git-Export, MCP-Werkzeuge `faden_lesen`, `kommentar_anlegen`, `status_setzen`.
  Projektschalter „Befund-Fäden“ (ab Werk an bei „Bauprojekt“); ein Gewerk, das noch an Befunden
  zuständig ist, lässt sich nicht löschen. Datenbankmigration 0011.
  Oberfläche: Bereich „Verlauf“ in der Befundkarte des Viewers mit Formularen für Kommentar/Link,
  Status, Zuständigkeit und Frist (nur, was die Rolle darf), „Rückmeldung zu diesem Befund“
  schreibt in den Faden; Kennzeichen (Status, Frist, „überfällig“) als Text in Befundliste und am
  Marker, Filter wie im Portal. Portal-Seite „Befunde“ (`/befunde`) zum Lesen und Kommentieren
  ohne 3D, mit „an der Stelle öffnen“; „N überfällig“ und Schalter in den Projekt-Einstellungen;
  Befundlink mit Eintrags-Anker `#e=`.

### Geändert

- **Lizenz:** scanview steht ab dieser Version unter der European Union Public Licence v. 1.2
  (`EUPL-1.2`) statt `AGPL-3.0-only`; frühere Releases bleiben unter ihrer Lizenz. Beiträge mit
  Herkunftserklärung (DCO, `Signed-off-by`) statt CLA, Dateikennzeichnung nach REUSE
  (`REUSE.toml`, `LICENSES/`), KI-Assistenz in `AI-USAGE.md`. Der Quelltext-Link im Portal heißt
  „Quelltext (EUPL-1.2)“. Entscheidung: `docs/entscheidungen/0014-lizenz-eupl.md`.
- Bedienung mit Fingern im Orbit wie bei Sketchfab: ein Finger dreht um den Drehpunkt, zwei Finger
  zoomen und verschieben. Bisher verschob ein Finger, und Drehen ging nur mit zwei Fingern. Die
  Finger folgen der Einstellung „Linke Maustaste und ein Finger im Orbit“; wer dort „verschiebt“
  wählt, behält die bisherige Belegung. Hinweiszeile und Dialog „Bedienung“ zeigen die neue Belegung.
- Viewer: Giro3D, three und proj4 liegen in einem eigenen Bundle-Teil. Nach einem Update lädt der
  Browser nur den eigenen Code neu (etwa 115 kB statt 700 kB komprimiert), solange sich die
  Bibliotheken nicht geändert haben.

### Behoben

- Server-Start: Eine Migration mit kleinerer Nummer als die höchste eingespielte, die nie lief, bricht den
  Start ab (`MigrationsLuecke`), statt still übersprungen zu werden. Betrifft parallel vergebene Nummern.

- Meshes: Heranzoomen (Mausrad, zwei Finger, Knopf „Näher“) hält vor der Oberfläche an, statt durch
  sie hindurch zum Drehpunkt in der Modellmitte zu laufen. Wer näher heran will, setzt den Drehpunkt
  per Doppelklick auf die Fläche.

### Sicherheit

- Keine behobenen Schwachstellen. Neu ist eine Zugriffsregel: Interne Einträge im Befund-Faden sehen
  nur Bauherrenseite und Projektleitung. Der Server filtert sie in Faden, Befundliste, Mängelliste,
  Exporten, Verlauf und MCP, belegt durch die Negativtests `NT-FADEN`. Einträge sind per Trigger
  unveränderlich. Betreiber: Migration 0011 läuft beim Start, keine weitere Maßnahme.

## [0.8.2] – 2026-10-07

Ausgeliefert werden wie in 0.8.1 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Geändert ist nur der Viewer: Mesh-Texturen erscheinen wieder. Plattform-
und MCP-Server sind bis auf die Version unverändert; der Import (nicht ausgeliefert) schreibt
texturierte Vollstufen ohne Vertexfarben. Keine Migration, das Schema bleibt bei 10, keine neuen
Umgebungsvariablen, `scanview.caddy` und die CSP sind unverändert. Betreiber müssen nichts übernehmen.

### Behoben

- Meshes: Texturen werden wieder angezeigt (seit 0.7.0 nur Vertexfarben, „blass“). Unter der CSP
  ohne `'unsafe-eval'` scheiterte der Basis-Transcoder still. Eingebettete KTX2-Texturen werden jetzt
  ohne `blob:` gelesen, der Transcoder ist ein eigener, reproduzierbarer Bau ohne
  Laufzeit-Codeerzeugung (`web/vendor/basis/`, in der SBOM als Eigenbau). Fehlt eine deklarierte
  Textur trotzdem, sagt der Viewer es, statt blass weiterzuzeichnen. Die CSP bleibt unverändert.
  Texturierte Meshes zeichnen ohne zusätzliche Vertexfarben (sonst doppelt abgedunkelt); der Import
  schreibt sie in texturierten Vollstufen nicht mehr.
- Meshes: Die Vollstufe wird wieder gezeichnet (sie meldete sich geladen, blieb aber unsichtbar) und
  lädt automatisch, sobald das Mesh ein Viertel der Bildhöhe füllt; bei Datensparen des Browsers
  weiter erst beim Heranzoomen oder Messen.

### Sicherheit

- Keine behobenen Schwachstellen. Der KTX2-Transcoder (Basis Universal 1.50.0 mit dem Decoder von
  zstd 1.4.9) ist jetzt ein eigener, reproduzierbarer Bau ohne Laufzeit-Codeerzeugung; die CSP bleibt
  ohne `'unsafe-eval'`. In der SBOM steht er als Eigenbau mit Quelle, Commit und Toolchain, zstd als
  enthaltene Bibliothek. Weil der Schwachstellen-Scanner selbst gebauten C-Code nicht kennt, prüft
  die Release-Kette beide Komponenten über OSV.dev (per Commit und Tag). Die gemeldete Lücke
  OSV-2022-15 liegt im zstd-Kompressor, der nicht eingebaut ist (VEX „nicht betroffen“).
  Betreiber: keine Maßnahme.

## [0.8.1] – 2026-10-07

Pflege- und Sicherheitsrelease. Ausgeliefert werden wie in 0.8.0 Viewer und Portal (`web`), der
Plattform-Server (`server`) und der MCP-Server (`mcp-server`). Geändert ist nur der Viewer;
Plattform-Server und MCP-Server sind bis auf die Version unverändert. Der Import (nicht
ausgeliefert) prüft Fotos strenger. Es gibt keine Migration, das Schema bleibt bei 10, keine neuen
Umgebungsvariablen, `scanview.caddy` ist unverändert. Betreiber müssen nichts übernehmen.

### Behoben

- Fotos: Nach einem erneuten Import (zum Beispiel nach der Anonymisierung) zeigte der Browser bis
  zu einer Stunde das alte Bild unter derselben Foto-ID. Jedes Foto liegt jetzt unter einer
  Inhalts-Revision (`fotos/<ID>/<Revision>/`); `fotos-pruefen` prüft sie. Ältere Pakete bleiben lesbar.
- Fotos: Kamerapyramiden hinter Wänden lassen sich nicht mehr anklicken; eine Größenänderung des
  Foto-Fensters behält Zoom und Bildausschnitt.
- Fotos-Import: Kalibrierungen mit p3/p4 ≠ 0 und Kamerabezeichnungen in mehreren Gruppen werden mit
  Grund abgelehnt, statt still falsch oder ohne Erklärung zu scheitern. Der Kameraexport rechnet
  nur noch die Stichprobe der Tie Points um (schneller bei großen Projekten).
- Messen und Befunde: Ein zweiter Punkt kurz nach dem ersten (oder die Suche nach einer Rückfrage) läuft
  nicht mehr in die grobe Stufe. Dekodierte Punktknoten werden zwischen Suchen geteilt (bis 64 MB),
  Folgesuchen in derselben Gegend brauchen 10–200 ms statt bis zu 500 ms.
- SBOM: Mehrere ausgelieferte Dateien desselben Pakets tragen jeweils ihren SHA-512
  in verschachtelten Dateikomponenten mit Lieferpfad; der Paket-Tarball-Hash bleibt erhalten.
- Fotos-Export (Exportskript): Die Referenzpaare zur Prüfung des Verzeichnungsmodells decken
  jetzt auch den Bildrand ab, wo k3/k4 am stärksten wirken. Der Import kann damit Exporte abweisen,
  deren Modell erst am Rand abweicht.

### Sicherheit

- Entwicklungswerkzeug source-map-js 1.2.1 → 1.2.2 (Event-Loop-DoS über indizierte
  Source-Map-Abschnitte); `npm audit` meldet keine Lücken mehr, B-WB-N7 (niedrig) ist behoben.
  Betroffen waren nur Build- und Entwicklungsumgebungen, die ausgelieferten Pakete nicht;
  Betreiber: keine Maßnahme.

## [0.8.0] – 2026-10-06

Ausgeliefert werden wie in 0.7.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Der Viewer zeigt orientierte Fotos (Ebene „Fotos“ mit Blickpyramiden,
Foto-Fenster, „Durch die Kamera“) und im Panorama sichtbare Nachbar-Marken mit Wechsel per
Doppelklick. Der Server liefert Fotos ohne freigegebene Anonymisierung nur an Bearbeiter,
Projektleitung und Admin; die beiden breiten Ausnahmebehandlungen im Katalog sind eingeengt. Der
MCP-Server ist bis auf die Version unverändert. Der Import (nicht ausgeliefert) übernimmt
Rahmenkameras aus `kameras.json` als Fotos und weist E57 mit Float32-Koordinaten ab — die in 0.7.0
angekündigte Korrektur. Es gibt keine Migration, das Schema bleibt bei 10, keine neuen
Umgebungsvariablen; `scanview.caddy` ist unverändert (Fotos liegen unter `/data/` und laufen durch die
vorhandene Rechteprüfung). Die nginx-Beispielkonfiguration gibt Fotos bewusst nicht frei. Betreiber
müssen nichts übernehmen.

### Hinzugefügt

- Orientierte Fotos: `scanview-import fotos-kameras` übernimmt Rahmenkameras aus `kameras.json` (etwa
  Drohnenbilder) mit angepasster Kalibrierung, entzerrt sie auf eine Lochkamera und schreibt die
  Ebene „Fotos“ (Stufen bis 4096 px, wahlweise Kacheln in voller Auflösung). Der Import prüft das
  Verzeichnungsmodell gegen die Referenzpaare des Exports, den Ausrichtungsstand gegen die Punktwolke und die
  Rückprojektion je Foto (≤ 2 px). Der Viewer zeigt Blickpyramiden je Gruppe, ein Foto-Fenster
  und „Durch die Kamera“ mit Überblendung. Doku `docs/public/fotos.md`.

### Geändert

- Qualitätstor: ESLint mit `--max-warnings=0`, typgestützten Sicherheitsregeln und
  Playwright-Regeln; gezählter Altbestand in `web/eslint-suppressions.json`.
- Ruff-Zusatzregeln für blinde Ausnahmebehandlung, Komplexität (15) und Async-Code über eine
  Ratsche je Datei/Regel; bisherige Regeln bleiben ohne tolerierte Funde. Neue Negativkontrolle
  für `BLE001`; die beiden breiten Katalog-Ausnahmebehandlungen sind eingeengt.
- Import: `mypy --strict` für Quellen und Skripte mit ergänzten Typannotationen.
- `AGENTS.md` hält Produktregeln und verpflichtende Prüfungen für automatisierte Beiträge fest.
- Mutations-Ausgangswerte der Sicherheitsmodule als Vorbereitung einer späteren Gate-Entscheidung.

### Behoben

- Panorama: Nachbar-Standpunkte erscheinen als Marken fester Bildschirmgröße. Bisher lagen sie als flache
  Scheiben auf Scannerhöhe, wenn ein Standpunkt keine Bodenhöhe hatte (etwa Panoramen aus `kameras.json` ohne
  `overview`), und waren vom Nachbarn aus nur von der Kante zu sehen. Liegt kein Nachbar innerhalb von 25 m,
  zeigen die nächsten drei bis 100 m. Neu: Doppelklick im Panorama wechselt zum Standpunkt in dieser Richtung.

- Import: E57 mit Float32-Koordinaten und lokalen Beträgen ab 16 384 m wird beim Punktimport und
  bei der Quellenprüfung mit einer klaren Exportanweisung abgewiesen (bekannte Einschränkung in
  0.7.0; PDAL schreibt E57 standardmäßig so, Abhilfe `writers.e57.double_precision=true`).
  Ein ausdrücklicher Durchgang mit `--float32-zulassen` weist die tatsächliche Rasterweite im
  Report aus.

### Sicherheit

- Fotos ohne freigegebene Anonymisierung liefert `/api/authz` nur an Bearbeiter, Projektleitung
  und Admin; Betrachter und Rückmelder erhalten 404. Der statische nginx-Betrieb gibt Fotos nicht
  frei. Betreiber: keine Maßnahme; `caddy-pruefen.sh` prüft das Tor optional (`FOTO`,
  `BETRACHTER_COOKIE`).
- Entwicklungswerkzeug vitest 2.1.9 → 4.1.11; damit entfallen die mitgezogenen vite 5, esbuild 0.21,
  vite-node und tinypool mit ihren bekannten Lücken (B-WB-N7, niedrig). Betroffen waren nur Test-
  und Entwicklungsumgebungen, die ausgelieferten Pakete nicht; Betreiber: keine Maßnahme.

## [0.7.0] – 2026-10-06

Ausgeliefert werden wie in 0.6.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Der Viewer zeigt Mesh-Ebenen aus dem neuen Mesh-Import und stellt Mess-
und Befundpunkte als 3D-Kugeln dar; der Server meldet einen fehlenden Befundtitel einheitlich; der
MCP-Server ist bis auf die Version unverändert. Der Import (nicht ausgeliefert, läuft beim
Betreiber der Daten) liest zusätzlich Meshes und weitere Punktformate. Es gibt keine Migration, das
Schema bleibt bei 10, keine neuen Umgebungsvariablen; `scanview.caddy` ist unverändert (die
Mesh-Decoder liegen unter dem schon freigegebenen Pfad `/wasm/`), Betreiber müssen nichts übernehmen.

**Bekannte Einschränkung (Import):** `convert` nimmt E57-Dateien mit Koordinaten in einfacher
Genauigkeit (Float32) und Beträgen ab 16 384 m ohne Warnung an; die Koordinaten liegen dann auf
einem Raster von 2 mm oder gröber. Die Korrektur folgt in 0.7.1; bis dahin E57 mit doppelter
Genauigkeit oder lokalen Koordinaten liefern.

### Hinzugefügt

- Mesh-Ebenen (glTF aus dem Import Weg A) im Viewer: Vorschau zuerst, Vollstufe beim
  Heranzoomen, Ebenen-Register, `F` rahmt alle sichtbaren Ebenen, Schnittbox beschneidet
  Meshes. Messen auf Meshes folgt.
- Mesh-Import Weg A für OBJ/MTL/Texturen, Mesh-PLY, STL und glTF/GLB: metrischer
  Rahmen, lokale standardkonforme GLB-Dateien, zielgerechte Verkleinerung und
  KTX2-Voll-/Vorschaustufen. `add --same-frame` prüft die Deckung gegen die
  bestehende Punktwolke; `verify` misst beide Oberflächenrichtungen, Rahmen,
  Texturen und Deckung. Ist ein Dreiecksziel unter Erhalt der UV-Nähte unerreichbar,
  gilt die kleinste nahterhaltende Stufe, ausgewiesen im Manifest.
- Direkter Punktimport von LAS/LAZ/COPC, Punkt-PLY, PTS und XYZ/TXT/CSV; explizite
  CRS-/Einheitenauflösung aus Datei, Sidecar und CLI, verbindliche Textspalten und
  optionale Achsendrehung. PLY-uchar-/PTS-Farben werden in LAS-16-bit-RGB umgerechnet.
- Optionale räumliche Ausdünnung `--ausduennen <meter>` für alle Punktformate einschließlich
  E57; getrennte Roh-/Ausdünnungsbilanz und Quellenprüfung mit Seed 42, bei Ausdünnung
  COPC-Stichproben gegen Originalpunkte. Herkunft, Einheit und Bounds-Prüfung stehen im Report.

### Geändert

- Mesh-Vorschau mit exakt verschweißten Positionen und im linearen sRGB gemittelten
  Vertexfarben, ohne Textur: Das 200.000-Dreiecksziel hängt nicht mehr von UV-Nähten ab.
  Beide GLB-Stufen verwenden meshopt-Kompression, optimierte Pufferreihenfolge und
  quantisierte Positionen/UV; UASTC-RDO verbessert die Zstd-Texturkompression;
  `verify` dekodiert die ausgelieferten Dateien und weist Schrittweiten und Farbabweichung aus.
- Echte Nullflächen werden vor der Mesh-Ausgabe entfernt und getrennt nach Quelle,
  Verkleinerung und Quantisierung bilanziert; die Zielsuche berücksichtigt die Bereinigung.
- Räumliche Ausdünnung verarbeitet Kacheln mit höchstens einer Million echten Punkten
  je PDAL-View. Globaler Rasterursprung, ursprüngliche Attribute und Gleichstandregel
  bleiben erhalten; Dichte-Hotspots werden weiter geteilt, Einzelvoxel chunkweise reduziert.
  Rohkoordinaten bleiben bis zur Auswahl erhalten. Das Containerlimit bleibt bei 40 GiB;
  die falsche 14-GiB-Angabe wurde durch dokumentierte synthetische Messwerte ersetzt.
- Viewer: Mess- und Befundpunkte sind schattierte 3D-Kugeln mit Radius in Weltmetern,
  begrenzt durch Mindest- und Höchstgröße am Bildschirm. Befunde zeigen die Kugel in der
  Farbe ihrer Art, das Schild steht darüber; der Fangradius der Griffe wächst mit der Kugel.
- Viewer: Eine per `ly=` oder im Ebenen-Register ausgeblendete Punktwolke lädt keine Punktdaten
  mehr nach; beim Einblenden lädt sie wie gewohnt.

### Behoben

- Viewer: Im Ebenen-Panel standen Titel und Art ohne Abstand nebeneinander („PunktwolkePunktwolke“),
  Mesh-Hinweise liefen in den Titel. Jetzt Schalter, Titel und Art in einer Zeile, Hinweise und
  Regler eingerückt darunter.
- Fehlt der Titel eines Befunds ganz oder ist er `null`, meldet der Server „Titel fehlt.“
  statt „Titel muss Text sein.“ — wie bei einem leeren Titel.

### Sicherheit

Behobene Schwachstellen: keine. Betroffene Versionen und nötige Maßnahmen der Betreiber: keine.

- Viewer: keine Änderung an der Content-Security-Policy. Mesh-Decoder (Basis Universal,
  meshoptimizer) kommen aus dem eigenen Build vom eigenen Ursprung; GLB-Dateien mit externen
  Puffern oder PNG-/JPEG-Texturen werden vor dem Parser abgewiesen; ein gemeinsames
  512-MiB-Budget begrenzt den Grafikspeicher der Meshes. Keine neue npm-Abhängigkeit.
- Server: keine Änderung an Anmeldung, Rollen oder Berechtigungen.
- Import-SBOM: `meshoptimizer` gibt es nur als Quellpaket; es wird aus `uv.lock` belegt (Version,
  sdist-SHA-256, Lizenz MIT) und als unvollständig ausgewiesen, statt im gehärteten SBOM-Container
  gebaut zu werden.
- Punktimport: keine Änderung an Anmeldung oder Berechtigungen; keine neue Abhängigkeit.
  Punktquellen benötigen widerspruchsfreie CRS-/Einheitenangaben und PLY ohne Faces; Quellen
  werden schreibgeschützt gemountet. Der E57-Pfad ohne Ausdünnung behält seine bestehende Pipeline.

## [0.6.0] – 2026-10-05

Ausgeliefert werden wie in 0.5.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`); alle drei sind bis auf die Version unverändert. Neu ist, dass ein Host
mehrere Instanzen betreiben kann: `einrichten.sh --instanz <name> --port <port>` richtet eine weitere
Instanz mit eigenen Konten, Pfaden, Units, Rechten und Sicherungen ein. Ohne `--instanz` verhalten
sich `einrichten.sh`, das Freischalt-Skript und alle Vorlagen bytegleich wie in 0.5.0. Der Import
liest Kugelpanoramen aus `kameras.json` als Standpunkte (`panos-kameras`). Es gibt keine Migration, das
Schema bleibt bei 10, keine neuen Umgebungsvariablen; `scanview.caddy` ist unverändert, Betreiber der
Standardinstanz müssen nichts übernehmen.

### Hinzugefügt

- `einrichten.sh --instanz <name> --port <port>` für mehrere Instanzen auf demselben Host:
  eigene Konten, Pfade, Units, Update-Protokolle, Datenbanken und Sicherungen. Ohne Instanzangabe
  bleiben die Standardvorlagen bytegleich zu 0.5.0. Rootloser Render- und Isolationsnachweis.
- `paket-bereitstellen.sh --instanz <name>` und dokumentierter `sv-beispiel`-Helfer.
  Caddy und fail2ban erhalten eigene gerenderte Betreiberdateien; benannte Instanzen bekommen
  keine Git-Export-Units.
- Import von Kugelpanoramen als Standpunkte über `panos-kameras`: geprüftes
  Kameraformat, bilineare Würfelseiten und Exportskript mit numerischen Gitterrotationen.
  Paket-CRS und Höhensystem müssen übereinstimmen; unbekannte Pakethöhen brauchen eine
  ausdrückliche Erklärung. Gruppen erscheinen als Präfix im Standpunktnamen. Große
  Kugelpanoramen (z. B. PTGui-Luftpanoramen mit rund 180 MP) liest der Import mit einer eigenen
  Pixelgrenze. Ablauf und Voraussetzungen: [Import-Dokumentation](import/README.md).

### Sicherheit

- Je Instanz eigenes Freischalt-Skript mit fest eingebauten Pfaden und eigener sudoers-Regel,
  die ausschließlich dieses Skript erlaubt. Kein Pfad- oder Instanzargument beim Freischalten.
- Prüfung auf Portkollision, fremde Instanzpfade und zusätzliche Instanzgruppenrechte vor der
  Einrichtung; eigene reguläre Vertrauensanker ohne stillen Rückfall auf andere Instanzen.
- Atomarer Ersatz aller gerenderten Dateien mit gesetzten Eigentümern und Rechten vor der
  Aktivierung. Bestehende Umgebungsdateien bleiben erhalten.
- Panorama-Import: keine Änderung an Anmeldung oder Berechtigungen. Kameraformat und
  Bildquellen werden vor dem Schreiben geprüft; keine neue Abhängigkeit. Der bestehende
  E57-Import bleibt unverändert. Pillows Schutz gegen Dekompressionsbomben wird nur beim Öffnen
  eines Kugelpanoramas durch eine eigene, ausdrückliche Grenze (1 Mrd. Pixel) ersetzt.

## [0.5.0] – 2026-10-03

Ausgeliefert werden wie in 0.4.1 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu ist der erste Teil der Zusammenarbeit: Eine Projektleitung organisiert
ihr Projekt selbst, mit Beteiligten, projektgebundenen Einladungen, Gewerken, Kategorien je Projekt
und zwei Schaltern auf der neuen Portalseite „Projekt-Einstellungen“; der Viewer übernimmt Rechte und
Kategorien aus dem Projekt, der MCP-Server bekommt das Lesewerkzeug `beteiligte_lesen`. Der Import
ist unverändert. **Migration 0010** (`projektverwaltung`) läuft beim Dienststart und hebt das Schema
von 9 auf 10; sie baut die Tabelle der Projektrollen neu auf (vorhandene Rollen bleiben erhalten) und
läuft nur vorwärts, `einrichten.sh` legt vorher die Sicherung der Datenbank an. Es gibt keine neuen
Umgebungsvariablen; `scanview.caddy`, `deploy/nginx.conf`, `einrichten.sh` und das Freischalt-Skript
sind unverändert, Betreiber müssen an Caddy nichts übernehmen.

### Hinzugefügt

- **Rolle Projektleitung** oberhalb des Bearbeiters: darf alles, was ein Bearbeiter darf, und
  verwaltet ihr Projekt, vergibt aber nie die eigene Stufe und ändert keine Admins und keine andere
  Projektleitung.
- **Portalseite „Projekt-Einstellungen“** (Link auf der Projektkarte) mit Beteiligten, Einladen,
  offenen Einladungen, Gewerken, Kategorien je Projekt, zwei Schaltern („Rückmeldungen erlaubt“,
  „Export für Bearbeiter“) und, für Admins, der Vorlage „Bestandsaufnahme“ oder „Bauprojekt“.
- **Projektgebundene Einladung:** neue E-Mail-Adresse erhält einen Link, ein bestehendes Konto
  bekommt den Zugriff sofort, ohne Zurücksetzen der Anmeldung.
- **Gewerke** je Projekt mit Zuordnung der Beteiligten.
- **MCP-Werkzeug `beteiligte_lesen`:** Beteiligte mit Rolle und Gewerk, ohne E-Mail-Adressen.

### Geändert

- Die Rechteprüfung folgt einer Rangfolge statt einer Menge schreibender Rollen.
- Die Kategorien im Viewer kommen aus dem Projekt (aktive, in der festgelegten Reihenfolge); der
  Server nimmt weiterhin jede Kategorie an. Ohne Projektliste bleibt die bisherige Auswahl.
- Das Einlösen einer Einladung stuft eine vorhandene Rolle nie herab.
- Der Verlauf zeigt der Projektleitung zusätzlich die Verwaltung ihres Projekts (Einladungen, Rechte,
  Gewerke, Kategorien, Schalter); Token sehen weiterhin nur Bearbeitungen.
- Ein Schalter „Export für Bearbeiter“ aus sperrt den Export (auch per Token und MCP) für Bearbeiter.
  Er ordnet den Ablauf und ist keine Datenschutzgrenze: Bearbeiter lesen dieselben Daten weiter über
  die Listen der POIs und Messungen.
- Je Projekt sind höchstens 200 offene Einladungen möglich; weitere weist der Server mit einer
  deutschen Meldung ab, Widerruf macht wieder Platz.
- Das Gewerk einer Einladung wird in der Schreibtransaktion geprüft; ein parallel gelöschtes Gewerk
  führt zu einer Fehlermeldung (422) statt eines Serverfehlers.

### Sicherheit

Keine behobenen Schwachstellen. Hinweis für Betreiber: Projektleitungen verwalten die Beteiligten
ihres Projekts selbst und erfahren dabei, ob eine E-Mail-Adresse schon ein Konto hat. Die Migration
0010 baut beim Dienststart die Tabelle der Projektrollen neu auf und läuft nur vorwärts — vorher
sichern (`einrichten.sh` legt die Sicherung an).

## [0.4.1] – 2026-10-02

Ausgeliefert werden wie in 0.4.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu ist das Bedien-Paket des Viewers: Dialog „Bedienung“, Hinweise für
Maus und Finger, Web-App mit Vollbild sowie Trenner und Querformat der Empfängeransicht. Dazu
kommt die Korrektur des Freischalt-Skripts für Pakete. Plattform-Server und MCP-Server sind bis
auf die Version unverändert, der Import ebenso; es gibt keine Migration, das Schema bleibt bei 9.
Betreiber übernehmen beim Update `scanview.caddy` (neuer Zweig für Manifest und Symbole der
Web-App), in der Docker-Variante auch `deploy/nginx.conf`; das Freischalt-Skript kommt mit dem
Update.

### Hinzugefügt

- **Viewer, Dialog „Bedienung“:** Die Tastenhilfe wird zum Dialog „Bedienung“ (Taste `?`, Knopf in
  der Werkzeugleiste): oben Maus und Finger je Ansicht, die aktive Ansicht zuerst (Orbit,
  Panorama, Begehen; in der Empfängeransicht die Knöpfe der Befundkarte), darunter wie bisher die
  Tasten. Der Wechsel zwischen Punktwolke und Panorama wird mit einem kurzen Satz angesagt.
- **Viewer, Hinweisleiste für Maus und Finger:** Im Orbit nennt eine Zeile vor den Tastenhinweisen
  die Mausbedienung bzw. auf reinen Touch-Geräten die Fingergesten. Sie verschwindet wie die
  Tastenhinweise nach drei Ausführungen.
- **Viewer, Web-App und Vollbild:** Viewer und Portal bringen ein Manifest und ein eigenes Symbol
  mit; auf dem iPhone startet scanview „Zum Home-Bildschirm“ hinzugefügt ohne Adresszeile. Wo der
  Browser die Vollbild-API hat, schaltet der Knopf „Vollbild“ in der Werkzeugleiste den ganzen
  Bildschirm ein.
- **Viewer, Trenner in der Empfängeransicht:** Mobil trennt bei ausgeklappter Befundkarte ein
  verschiebbarer Trenner 3D-Ansicht und Karte, hochkant waagerecht, quer senkrecht mit der Karte
  neben der Ansicht. Ziehen mit Maus oder Finger, Doppeltipp wechselt zwischen Standard und großer
  3D-Ansicht, mit Fokus Pfeiltasten, Pos1 und Ende; die Aufteilung gilt je Browser.

### Geändert

- **Viewer, Leistungsanzeige:** Die Leiste mit Bildrate, Punkten und Anfragen ist ein Schalter im
  Einstellungsdialog (Abschnitt „Anzeige“), gespeichert wie bisher `?debug=1`; der Parameter wirkt
  weiter für einen einzelnen Aufruf.
- **Viewer, Querformat der Empfängeransicht:** Die ausgeklappte Befundkarte steht im Querformat
  neben statt unter der 3D-Ansicht; bisher blieb von der Ansicht nur ein schmaler Streifen.
- **Viewer, Messbeschriftungen in der Empfängeransicht:** kleiner und ruhiger, mit dem Schalter
  „Messwerte zeigen“ in der Befundkarte abschaltbar. Eine Beschriftung unter der Werkzeugleiste
  oder „← Projekte“ oder am Rand der Ansicht wird ausgeblendet statt angeschnitten. Die
  Werkzeugansicht bleibt unverändert.

### Behoben

- **Pakete bereitstellen:** Das Freischalt-Skript lehnte jedes Paket ab („ACL bzw. erweiterte
  Attribute an .“), sobald die Datenwurzel die Default-ACL für Caddy trug, die `einrichten.sh`
  selbst setzt — die Prüfkopie erbte sie. Die Kopie legt die geerbten POSIX-ACLs jetzt ab; die
  Caddy-ACL setzt das Skript wie bisher gezielt. ACLs oder andere erweiterte Attribute im Upload
  führen weiter zum Abbruch.
- **Release-Kette:** Manifest und Symbole der Web-App sind in der SBOM-Abdeckung der
  Hauptkomponente zugeordnet (genau die fünf beim Build erzeugten Namen), und der Egress-Test
  durchsucht `.png` wie `.wasm` in den ASCII-Anteilen, statt an der unbekannten Endung
  abzubrechen.

### Sicherheit

Keine behobenen Schwachstellen. Maßnahmen der Betreiber: `scanview.caddy` beim Update nach
`/etc/caddy` übernehmen und Caddy nach bestandener Validierung neu laden
([Betrieb](docs/public/betrieb.md), Update Schritt 4): ein neuer Zweig liefert Manifest und Symbole
der Web-App aus, ohne ihn fielen beide Pfade in den Portal-Zweig. Wer die Docker-Variante mit
`deploy/nginx.conf` betreibt, übernimmt auch diese Datei (neue `location` liefert das Manifest als
`application/manifest+json` statt `application/octet-stream`). Das Freischalt-Skript kommt mit
dem Update.

## [0.4.0] – 2026-10-02

Ausgeliefert werden wie in 0.3.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu sind Links zu Impressum und Datenschutzhinweisen je Instanz und eine
geänderte Mausbelegung im Orbit. Der Plattform-Server bringt keine Migration mit; `einrichten.sh`
und die Beispiel-Umgebung (`scanview-server.env.beispiel`) kennen die beiden neuen Variablen. Der
MCP-Server ist bis auf die Version unverändert, der Import ebenso.

### Hinzugefügt

- **Portal, Impressum und Datenschutz:** Betreiber verlinken ihr Impressum und ihre
  Datenschutzhinweise mit `SCANVIEW_IMPRESSUM_URL` und `SCANVIEW_DATENSCHUTZ_URL` (https-Adresse
  ohne Zugangsdaten oder Pfad derselben Origin). Die Links stehen in der Fußzeile jeder
  Portalseite, auch auf der Anmeldeseite. scanview liefert keinen Rechtstext. Ein ungültiger Wert
  verhindert den Start. Fehlt ein Wert, warnt der Dienst und `einrichten.sh` schreibt
  `warnung=impressum-datenschutz-fehlen` ins Update-Protokoll
  ([Betrieb](docs/public/betrieb.md)).
- **Viewer, Drehpunkt sichtbar:** Ein Doppelklick auf einen Punkt setzt wie bisher den Drehpunkt;
  jetzt erscheint dort ein kleiner Marker, beim Setzen und während des Drehens, und blendet nach
  kurzer Ruhe aus (bei Bewegungsreduktion ohne Übergang). Die Live-Region sagt „Drehpunkt gesetzt“.

### Geändert

- **Update-Protokoll:** Eine Zeile kann jetzt mehrere `warnung=…`-Kennungen tragen;
  `scanview-nachweise betrieb updates` liest sie alle.
- **Viewer, Maus im Orbit:** Links ziehen dreht um den Drehpunkt, rechts oder Umschalt+links
  verschiebt (wie CloudCompare und Potree); bisher verschob links und drehte rechts. Die alte
  Belegung ist im Einstellungsdialog unter „Linke Maustaste im Orbit“ wählbar und wird wie die
  übrigen Bedien-Einstellungen im Browser gespeichert. Ein Klick ohne Ziehen misst und markiert
  wie bisher.
- **Release-Anleitung:** Signieren der Archiv-Commits ohne Agent und Terminal; Ablauf für
  Änderungen am Vertrauensanker von Hand mit Probesignatur vor dem Commit.

### Behoben

- **Viewer, Mausrad im Begehen:** Nach dem ersten Esc, das nur die Zeigersperre löst, lief das
  Begehen weiter, das Mausrad bewirkte aber nichts mehr. Es ändert jetzt das Lauftempo, solange
  das Begehen läuft, und das neue Tempo wird angesagt („Tempo 2,4 m/s“).
- **Release-Werkzeug (`scanview-nachweise`):** Sperrzeilen des Vertrauensankers werden so getrennt
  und eingerückt gelesen wie von ssh-keygen; eine fehlerhafte Sperrzeile meldet die Kette schon
  beim Laden des Ankers mit dem Archiv-Commit, der sie einträgt. Die Probesignatur läuft ohne
  Terminal, ein Schlüssel mit Passphrase scheitert sofort, und ihre Fehlermeldung behauptet keine
  Sperre mehr. Das DAST-Ziel gilt mit Standardport 443 oder Punkt am Hostende als derselbe
  Ursprung. Das Update-Protokoll lehnt eine doppelte Warnkennung in einer Zeile ab.

### Sicherheit

Keine behobenen Schwachstellen. Maßnahmen der Betreiber: keine.

Hinweis für Betreiber: Die Datenschutzhinweise sollten auch die Daten nennen, die im Browser
bleiben, also lokale Merkpunkte und den lokalen Befundentwurf (IndexedDB).

## [0.3.0] – 2026-10-02

Ausgeliefert werden wie in 0.2.1 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu ist der zweite Teil der Bedienung im Viewer: Befunde setzen und
teilen, eine Empfängeransicht für geteilte Befunde und die Rolle Rückmelder. Der Plattform-Server
bringt dafür die Migration 0009 mit; am MCP-Server ändern sich Texte und die Pseudonymisierung des
neuen Urhebernamens. Der Import ist unverändert.

### Hinzugefügt

- **Viewer, Befund setzen:** Im Begehen und am Panorama-Fadenkreuz setzt `F` (am Gamepad Y) einen
  Befund an der Stelle unter dem Fadenkreuz, sonst ein Klick mit dem Werkzeug „Markieren“ — wie
  Messpunkte nur auf den Originaldaten. Das Formular öffnet sich rechts als Inspektor; im Begehen steht die Kamera, solange
  es offen ist.
- **Viewer, Befundlink:** „Teilen“ (Vorgabetaste `t`, umbelegbar) erzeugt einen Link mit Befund und
  Blickposition. Ein gelöschter, fremder oder veralteter Befund im Link ergibt einen Hinweis statt
  eines Fehlers; die Blickposition gilt trotzdem.
- **Viewer, Empfängeransicht:** Ein Befundlink öffnet für alle Rollen eine reduzierte Ansicht mit
  Befundkarte, Befundliste als Textalternative, Drehen in festen Schritten und Zoom-Knöpfen;
  „Im Werkzeug öffnen“ führt in die volle Ansicht. Wer darf, gibt dort eine Rückmeldung zu einem
  Befund oder markiert eine eigene Stelle.
- **Viewer, lokaler Entwurf:** Ein angefangener Befund übersteht einen Verbindungsabbruch im Browser
  (je Konto und Projekt) und wird nach dem Neuladen angeboten, bis der Server ihn bestätigt hat
  ([Sicherheitsarchitektur](docs/public/sicherheitsarchitektur.md), Datenübersicht).
- **Viewer, Tastenhinweise:** Kurze Hinweise zu den nächsten Tasten; jeder verschwindet nach drei
  Ausführungen. In den Einstellungen lassen sie sich dauerhaft einblenden oder zurücksetzen.
- **Viewer, Panorama:** Mit einem Werkzeug zeigt auch das Panorama ein Fadenkreuz; die Pfeiltasten
  drehen in Schritten, die Leertaste misst wie ein Klick, `F` setzt einen Befund.
- **Viewer:** Sprunglinks „Zur 3D-Ansicht“ und „Zu den Werkzeugen“ (in der Empfängeransicht auch
  „Zur Befundliste“); Höhenfarben mit einer Farbskala, die auch bei Farbsehschwäche unterscheidbar
  bleibt.
- **Rolle Rückmelder:** Darf ein Projekt ansehen und eigene Einträge der Kategorie „Rückmeldung“
  anlegen, ändern und löschen (höchstens 100 aktive je Konto und Projekt), sonst nichts; serverseitig
  geprüft. Vergabe im Portal oder mit `scanview-server einladen --projekt <id>=rueckmelder`
  ([Betriebsanleitung](docs/public/betrieb.md), „Rolle Rückmelder“).
- **Plattform-Server:** POIs und Messungen tragen im API-JSON zusätzlich den Namen des Urhebers
  (`erstelltVonName`); der MCP-Server pseudonymisiert ihn im datensparsamen Modus wie die übrigen
  Namen. Das Exportformat bleibt unverändert.

### Geändert

- **Viewer:** Das POI-Formular steht rechts als Inspektor; nach dem Speichern meldet der Viewer
  „Befund gespeichert“ und gibt den Fokus an die 3D-Ansicht zurück. Der POI-Knopf heißt
  „Markieren“.
- **Viewer, Panorama:** Bei reduzierter Bewegung wechselt das Panorama ohne Fahrt, als Schnitt.
- **MCP-Server:** Werkzeugbeschreibungen nennen die Rolle Rückmelder und ihre Grenzen.
- **Betriebsanleitung:** `caddy validate` beim Aktualisieren mit derselben Umgebung wie der
  Caddy-Dienst, Rückfall nur an die Prüfung gehängt, Rechte der vorhandenen Datei übernehmen.
  **Release-Anleitung:** Voraussetzungen für den Vorablauf der Negativkontrollen und den Trockenlauf.

### Sicherheit

Keine behobenen Schwachstellen.

Hinweis für Betreiber: Die neue Rolle Rückmelder erweitert Schreibrechte (nur eigene
Rückmeldungen). Migration 0009 baut beim Dienststart die Rollentabelle neu auf — vorher sichern
([Betriebsanleitung](docs/public/betrieb.md), „Aktualisieren“).

Maßnahmen der Betreiber:

- Auf 0.3.0 aktualisieren ([Betriebsanleitung](docs/public/betrieb.md), „Aktualisieren“); die
  Pflicht-Sicherung vor der Migration legt `einrichten.sh` selbst an. Die Caddy-Konfiguration
  (`scanview.caddy`) und `einrichten.sh` sind gegenüber 0.2.1 unverändert.
- Der lokale Befundentwurf liegt im Browser der Nutzer (IndexedDB, je Konto und Projekt); die
  Datenschutzhinweise der Instanz sollten ihn nennen
  ([Sicherheitsarchitektur](docs/public/sicherheitsarchitektur.md), Datenübersicht).

Offene Befunde stehen mit Schwere und Status im [Befundregister](docs/public/befunde.md); kein
offener Befund erreicht die Release-Schwelle in einem ausgelieferten Artefakt.

## [0.2.1] – 2026-10-02

Ausgeliefert werden wie in 0.2.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu ist der erste Teil der Bedienung im Viewer (Tastenbelegung, Begehen,
Fadenkreuz); dazu kommt ein Sicherheitsfix im Portal. Am Plattform-Server ändern sich das
Einrichtungsskript, die Beispielkonfiguration und eine interne Schnittstelle ohne geändertes
Verhalten; der MCP-Server ändert sich nur in der Versionsnummer.

### Hinzugefügt

- **Viewer, Begehen:** Die Ansicht lässt sich von innen mit Tastatur, Maus (Zeigersperre) oder Gamepad
  begehen — vorwärts, seitwärts, auf und ab, drehen (auch in festen Schritten) und neigen, dazu ein
  einstellbares Sichtfeld und Tempo. Bewegungsreduktion des Systems wird beachtet. Beendet wird es mit
  Esc oder dem Knopf; die Kamera bleibt sofort stehen, wenn der Fokus verloren geht.
- **Viewer, Tastenbelegung:** Alle Kürzel sind umbelegbar und die Einzeltasten-Kürzel gesammelt
  abschaltbar; Konflikte werden gemeldet. Die Tastenhilfe (`?`) zeigt die aktuelle Belegung, ein
  Einstellungsdialog bündelt Belegung und Bedien-Einstellungen. Die 3D-Ansicht ist per Tab erreichbar.
  Pfeiltasten, Leertaste, Pos1 und Ende bleiben außerhalb des Begehens fest; Esc schließt auch das
  POI-Formular und gibt den Fokus zurück.
- **Viewer, Zielpunkt:** Im Begehen zeigt ein Fadenkreuz den Messwert (Abstand oder Strecke) laufend an
  und sagt ihn nach kurzer Ruhe an. Punkte lassen sich am Fadenkreuz setzen — Messpunkte wie
  Interessenpunkte, nur auf den Originaldaten. Liegen an der Stelle zwei Flächen hintereinander, fragt
  der Viewer zurück, welche gemeint ist; ohne Treffer wird nichts gesetzt.

### Geändert

- **Einrichtung:** `einrichten.sh` warnt im Update-Protokoll (`warnung=barrierefreiheit-angaben-fehlen`
  bzw. `…-nicht-pruefbar`), wenn Kontakt oder Erklärung zur Barrierefreiheit der Instanz fehlen; kein
  Abbruch ([Betriebsanleitung](docs/public/betrieb.md), „Aktualisieren“).
- **Release-Kette und Betriebsnachweise** (`nachweise/`, nicht ausgeliefert): weitere Läufe eines
  Tages erhalten eine Laufnummer, das Update-Protokoll wird mit allen Zusätzen ausgewertet, der
  Stichtag der DAST-Freigabe gilt in UTC, der Signierschlüssel wird vor jedem Archiv-Commit gegen den
  Vertrauensanker geprüft, der wöchentliche Scan prüft nur SSH-Signaturen; der Schalter für den
  DAST-Lauf gegen die Produktivinstanz heißt jetzt `--betreiber-freigabe`.

### Behoben

- **Viewer:** B-BF-V1 — Einzeltasten-Kürzel wirkten seitenweit und ließen sich weder
  abschalten noch umbelegen (WCAG 2.1.4). Sie sind jetzt abschaltbar und umbelegbar und wirken nicht
  in Textfeldern und Dialogen.

### Sicherheit

Betroffen sind die Versionen 0.1.0 und 0.2.0. Behoben und mit Tests belegt:

| Kennung | Schwere | Behoben |
|---|---|---|
| B-BD-R8-I1 | mittel | Der Rücksprung nach der Anmeldung (`weiter=`) führt nur noch auf Seiten der eigenen Instanz. Pfade wie `/.//host`, `/x/..//host` oder `/%2e//host` bestanden vorher die Prüfung und wurden erst beim Normalisieren zu einem Verweis auf eine fremde Seite; ein präparierter Link auf die echte Instanz konnte so nach der Anmeldung auf eine fremde Seite führen (Phishing). Sitzung, Daten und Rechte waren dabei nicht direkt betroffen. |

Maßnahmen der Betreiber:

- Auf 0.2.1 aktualisieren ([Betriebsanleitung](docs/public/betrieb.md), „Aktualisieren“). Die
  Caddy-Konfiguration (`scanview.caddy`) ist gegenüber 0.2.0 unverändert.

Offene Befunde stehen mit Schwere und Status im [Befundregister](docs/public/befunde.md); kein
offener Befund erreicht die Release-Schwelle in einem ausgelieferten Artefakt.

## [0.2.0] – 2026-10-01

Ausgeliefert werden wie in 0.1.0 Viewer und Portal (`web`), der Plattform-Server (`server`) und der
MCP-Server (`mcp-server`). Neu sind das barrierefreie Portal, das Lesezugriffsprotokoll und die
vorbereitete `security.txt`; die Release-Kette prüft zusätzlich Quelltext, API, ausgehende
Verbindungen und die Signaturen von Commits und npm-Paketen.

### Hinzugefügt

- **Portal:** Seite „Barrierefreiheit“ mit der Erklärung der betreibenden Stelle (Datei in der
  Instanz-Konfiguration, Vorlage im Server-Paket) und Link „Barriere melden“ in der Fußzeile jeder
  Seite, auch ohne Anmeldung (`SCANVIEW_BARRIEREFREIHEIT_KONTAKT`, `SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG`).
- **Prüfung:** automatische Barrierefreiheitsprüfung aller Portal-Ansichten mit axe-core (WCAG 2.2
  AA) in Chromium und Firefox als Prüfskript, kein erzwungenes Release-Tor (die Prüfsuite liegt nicht
  im öffentlichen Stand)
  und manuelle Prüfliste (`docs/public/barrierefreiheit-pruefliste.md`); kein Konformitätsnachweis.
- **Lesezugriffsprotokoll:** wer hat wann welches Projekt gelesen, je Konto, Projekt, Art und
  15-Minuten-Fenster zusammengefasst, unveränderlich, ohne IP-Adresse, Gerät oder E-Mail-Adresse;
  Aufbewahrung `SCANVIEW_LESEZUGRIFFE_TAGE` (Standard 90 Tage, `0` schaltet es ab). Einsicht und
  Export als JSON Lines nur für Admins per Browser-Sitzung im Abschnitt „Lesezugriffe“ der
  Verwaltung; jede Einsicht wird selbst protokolliert (siehe
  [Betriebsanleitung](docs/public/betrieb.md), „Lesezugriffsprotokoll — Datenschutz“).
- **Meldeweg:** `security.txt` nach RFC 9116 vorbereitet — Vorlage, Erzeugen und Prüfen per
  `scanview-nachweise`, Caddy liefert `/.well-known/security.txt`, die Richtlinie und den
  öffentlichen Schlüssel aus `/etc/scanview/meldeweg` aus. Solange das Verzeichnis leer ist,
  antworten die Pfade mit 404; aktiviert wird der Meldeweg erst mit eingerichtetem Postfach.
- **Release-Kette** (`nachweise/`), neue Pflichtstufen:
  - statische Sicherheitsanalyse mit Semgrep (vendierter Regelsatz) und Bandit über alle
    Quellbäume und die Host-Skripte; jeder Fund steht im Befundregister oder als begründete
    Ausnahme;
  - API-Fuzzing mit Schemathesis gegen eine Wegwerf-Instanz, als Betrachter und als Admin;
  - Egress-Test: keine ausgehenden Verbindungen von Portal und Viewer, statisch im gebauten
    Bundle und mit einem Headless-Browser gegen eine Wegwerf-Instanz;
  - Prüfung der npm-Registry-Signaturen des Web-Baus;
  - Prüfung der Signatur jedes Commits seit einem Stichtag gegen einen Vertrauensanker
    außerhalb des Quellbaums; Manifest-Schema `scanview-release-manifest/2`.
- **Betriebsnachweise** (`scanview-nachweise`): passiver DAST-Lauf mit OWASP ZAP gegen die
  Testinstanz nach jedem Deploy; Auswertung des Update-Protokolls je Instanz gegen das signierte
  Manifest mit der Liste der unterstützten Versionen; wöchentlicher Schwachstellen-Scan aller
  unterstützten Versionen; jährliche Zweitablage des Nachweis-Archivs.
- **Abhängigkeiten:** Renovate-Konfiguration (`renovate.json`) mit Mindestalter von 7 Tagen und
  Digest-Pins; Prüfwerkzeuge der Kette laufen wahlweise mit Podman.

### Geändert

- **Portal:** eigener Seitentitel je Seite, sichtbarer Fokus auch im Kontrastmodus, kontrastreichere
  Feldränder, angesagte Fehler- und Suchmeldungen, Fokus kehrt nach dem Schließen von Boxen zurück;
  „Abmelden“ steht in der Fußzeile jeder Seite, sobald man angemeldet ist.
- **API v1:** Schalter nehmen nur noch JSON-Wahrheitswerte an, Ganzzahlen in Pfad und Anfrage sind
  auf 64 Bit begrenzt (darüber 422), die OpenAPI-Beschreibung nennt die tatsächlichen Inhaltstypen
  der CSV- und JSON-Lines-Antworten.
- **MCP-Server:** pyjwt 2.15.0.

### Sicherheit

Betroffen ist die Version 0.1.0. Behoben und mit Tests belegt:

| Kennung | Schwere | Behoben |
|---|---|---|
| B-FUZZ-4 | mittel | Sehr große Ganzzahlen in der Anfrage führen nicht mehr zu einem Serverfehler, sondern werden mit 422 abgewiesen; ebenso die Token-ID beim Widerrufen eines API-Tokens. |
| B-FUZZ-2 | niedrig | Schalter wie die Einwilligung in die Namensnennung im Git-Export nehmen nur noch echte Wahrheitswerte an und deuten andere Werte nicht mehr still um. |
| B-FUZZ-1 | niedrig | Die OpenAPI-Beschreibung nennt die tatsächlich gelieferten Inhaltstypen. |

- **Abhängigkeit:** pyjwt im MCP-Server von 2.14.0 auf 2.15.0 angehoben (CVE-2026-101918, mittel);
  der betroffene Programmteil wird vom MCP-Server nicht verwendet.
- **Neuer öffentlicher Endpunkt** `GET /api/barrierefreiheit` ohne Eingabe und ohne Personenbezug.

Maßnahmen der Betreiber:

- Auf 0.2.0 aktualisieren. Das Release trägt das Manifest-Schema `scanview-release-manifest/2`;
  eine geprüfte Skriptkopie aus 0.1.0 lehnt es ab — das Update läuft deshalb wie die
  Ersteinrichtung über das Bootstrap-Skript ([Betriebsanleitung](docs/public/betrieb.md),
  „Aktualisieren“).
- Das Lesezugriffsprotokoll ist personenbezogen und ab Werk mit 90 Tagen Aufbewahrung aktiv:
  Rechtsgrundlage und gegebenenfalls Mitbestimmung für den eigenen Einsatz klären oder es mit
  `SCANVIEW_LESEZUGRIFFE_TAGE=0` abschalten.

Offene Befunde stehen mit Schwere und Status im [Befundregister](docs/public/befunde.md); kein
offener Befund erreicht die Release-Schwelle in einem ausgelieferten Artefakt.

## [0.1.0] – 2026-09-28

Erste Version mit Release-Nachweisen. Ausgeliefert werden Viewer und Portal (`web`), der
Plattform-Server (`server`) und der MCP-Server (`mcp-server`).

### Hinzugefügt

- **Viewer:** Punktwolken im COPC-Format mit Detailstufen nach Sichtbarkeit, Panoramen je
  Standpunkt mit Sprung zwischen Standpunkten, Messen von Strecken, Höhen und Flächen,
  Merkpunkte (POIs) mit Notizen, Draufsicht und Minikarte.
- **Plattform-Server:** Konten per Einladung, Anmeldung per Passkey mit Passwort als
  Rückfallweg, Sitzungen, Rollen je Projekt (Betrachter, Bearbeiter) und Administratoren,
  Zugriffsprüfung für jede Paketdatei über Caddy `forward_auth`.
- **Portal:** Projektkarten mit Suche, Umbenennen und Beschreibung, Bearbeitungsverlauf,
  Export von Merkpunkten und Messungen als CSV und JSON, Verwaltung von Einladungen, Konten und
  Rechten, datensparsame Nutzungsstatistik je Projekt.
- **API v1** unter `/api/v1/…` für alle Funktionen von Portal und Viewer, OpenAPI-Beschreibung für
  angemeldete Nutzer, persönliche API-Tokens (lesen bzw. lesen und schreiben, befristet,
  widerrufbar), `GET /api/v1/version` für angemeldete Nutzer.
- **Bearbeitungsprotokoll:** unveränderliches Protokoll aller Konto-, Rechte- und
  Datenänderungen mit Kanal (Web, Kommandozeile, API).
- **MCP-Server** für KI-Assistenten, der ausschließlich die API v1 mit einem persönlichen Token
  nutzt.
- **Git-Export** der Bearbeitungen je Projekt (ab Werk aus), Autoren pseudonym.
- **Betrieb:** Einrichtungsskript, gehärtete systemd-Units, Caddy-Konfiguration, tägliche
  Datenbanksicherung, fail2ban-Filter, Prüfskript für eine laufende Instanz.
- **Release-Nachweise** (`nachweise/`): fail-closed Release-Kette mit SBOM je Artefakt
  (CycloneDX 1.6 nach BSI TR-03183-2), Schwachstellen-Scan, Lizenzabgleich, Sicherheits-
  Negativtests, Negativkontrollen der Kette, signiertem Manifest und öffentlichem Export über eine
  Positivliste.
- **Produktdokumente:** [`SECURITY.md`](SECURITY.md),
  [Support und Patches](docs/public/support-und-patches.md),
  [Sicherheitsarchitektur](docs/public/sicherheitsarchitektur.md),
  [Pentest-Plan](docs/public/pentest-plan.md), [Betriebsanleitung](docs/public/betrieb.md),
  [Release-Prozess](docs/public/release.md), [Kontrollmatrix](docs/public/kontrollmatrix.md).

### Sicherheit

Diese Version ist die erste veröffentlichte; es gibt keine früheren Versionen, die Betreiber
aktualisieren müssten. Vor der Veröffentlichung wurden in eigenen Sicherheitsprüfungen (Quelltext-
und Instanzprüfung, Review der API-Tokens) folgende Befunde behoben und mit Tests belegt:

| Kennung | Schwere | Behoben |
|---|---|---|
| B-WB-M1 | mittel | Die Kontosperre nach fünf Fehlversuchen hält auch parallelen Anfragen stand. |
| B-WB-M2 | mittel | Globale Bremse für fehlgeschlagene Passwort-Anmeldungen; das fail2ban-Jail erhält die Client-Adresse. |
| B-WB-M3 | mittel | Passwortprüfungen laufen in einem eigenen, begrenzten Rechenpool und bremsen die Zugriffsprüfung der Paketdateien nicht mehr. |
| B-TE-M1 | mittel | Anlegen von API-Tokens und Einwilligung in die Namensnennung im Git-Export verlangen eine frische Anmeldung. |
| B-TE-M2 | mittel | Token-Namen erscheinen nicht mehr in Commits des Git-Exports. |
| B-WB-N1 | niedrig | Obergrenze und effizientes Aufräumen für Passkey-Challenges. |
| B-WB-N2 | niedrig | Die Drosseltabelle wird aufgeräumt und wächst nicht mehr unbegrenzt. |
| B-WB-N4 | niedrig | Ein defektes `project.json` verhindert nicht mehr den Dienststart; das Paket wird mit Meldung übersprungen. |
| B-WB-N5 | niedrig | Paketdateien werden als Daten ausgeliefert (`sandbox`-CSP, `nosniff`, `Cross-Origin-Resource-Policy`) und können keine aktiven Inhalte in der Origin der Plattform ausführen. |
| B-WB-N8 | niedrig | Höchstens 10 Sitzungen je Konto; eine erneute Anmeldung ersetzt die alte Sitzung. |
| B-BB-N1 | niedrig | Sicherheits-Header auch auf Fehlerantworten, ohne Server-Banner. |
| B-TE-N1 | niedrig | Der Einwilligungstext zum Git-Export beschreibt die tatsächliche Wirkung. |
| B-TE-N2 | niedrig | Namensprüfung erkennt auch Unicode-Steuerzeichen (Bidi, Nullbreite). |
| B-WB-I2 | info | `Cache-Control: no-store` auf allen API- und Anmeldeantworten. |
| B-BB-I2 | info | Der `Via`-Header verrät den Reverse Proxy nicht mehr. |

Maßnahmen der Betreiber: keine über die [Betriebsanleitung](docs/public/betrieb.md) hinaus.
Offene Befunde stehen mit Schwere und Status im [Befundregister](docs/public/befunde.md); kein
offener Befund erreicht die Release-Schwelle in einem ausgelieferten Artefakt.
