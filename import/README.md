# scanview-Import

Der Import baut aus E57, LAS/LAZ/COPC, Punkt-PLY, PTS oder XYZ/TXT/CSV ein Paket
mit `project.json` und `cloud.copc.laz`. OBJ, Mesh-PLY, STL und glTF/GLB werden
als Mesh-Ebene mit `model.glb` und `preview.glb` importiert.
Er braucht Python 3.12, uv und für die Wolkenkonvertierung Docker mit dem vorhandenen PDAL-Image.
Die Befehle unten laufen aus diesem Verzeichnis mit `uv run --locked scanview-import …`.

## Punktformate direkt importieren

| Quelle | Leser | Einheit / Spalten |
|---|---|---|
| E57 | `readers.e57`, bestehender Scanpfad | Meter, Scan-IDs und Standpunkte wie bisher |
| LAS / LAZ / `.copc.laz` | `readers.las` | Meter laut Importvertrag |
| PLY (nur Punkte) | `readers.ply` | `--unit` oder Sidecar erforderlich; ASCII und binär |
| PTS | `readers.pts` | `--unit` oder Sidecar; Punktzahlkopf, XYZ/Intensity/RGB |
| XYZ / TXT / CSV | `readers.text` | `--unit` oder Sidecar **und** `--columns` erforderlich |

PLY mit Faces wird als Mesh importiert (siehe unten).
PTX gehört nicht zu diesem Befehl. Scanlose Formate bekommen keine
Standpunkte, alle `PointSourceId` sind 0, `pointSourceIsScan` ist false. Format allein
belegt weder Scannerherkunft noch Maßstab (`scaleVerified: false`).

CRS kommt aus LAS-WKT/GeoKeys, sonst aus `<datei>.scanview.json`, sonst aus `--crs`.
Mehrere Angaben müssen übereinstimmen; Widersprüche brechen ab. Ohne CRS bleibt die
Wolke lokal (`wkt: null`). LAS-GeoKey 3072=32767 mit Local-Citation ist ausdrücklich lokal.
Einheit und CRS werden niemals aus Koordinatenwerten abgeleitet. Beispiel-Sidecar:

```json
{"crs": "EPSG:25833", "unit": "m"}
```

PLY/PTS/Text brauchen `--unit m|cm|mm|ft` oder diese Sidecar-Angabe. Ausgabe ist immer in
Metern. `--up y` dreht `(x,y,z)` nach `(x,-z,y)`; sonst bleiben die Dateiachsen erhalten.
Bei Text ist die Spaltenfolge verbindlich, etwa `--columns z,x,y,r,g,b`. Erlaubt sind
x/y/z, r/g/b (auch red/green/blue) und intensity (auch i); RGB nur vollständig.
Komma, Tab und Leerzeichen sind Feldtrenner; ein vorhandener benannter Header muss zur
Angabe passen. Ohne Header beginnt die erste Zeile direkt mit Punkten.
Text-RGB folgt der 16-bit-LAS-Konvention (0–65535); PLY-uchar und PTS-RGB (0–255)
werden ×257 skaliert. Zahlenwerte dienen nie zur Erkennung der Farbbitbreite.

```bash
uv run --locked scanview-import convert /daten/wolke.laz /daten/paket
uv run --locked scanview-import convert /daten/skulptur.ply /daten/skulptur-paket --unit m
uv run --locked scanview-import convert /daten/punkte.csv /daten/text-paket --unit cm --columns x,y,z
uv run --locked scanview-import verify /daten/punkte.csv /daten/text-paket --samples 10000
```

`--ausduennen <meter>` ist optional für **alle** Formate einschließlich E57 und behält je
Voxel den Originalpunkt nächst zum Zellzentrum (`filters.voxelcenternearestneighbor`).
Ohne Option läuft kein Ausdünnungsfilter. Bei Ausdünnung wird die Quelle zuerst in
Chunks gelesen, dann auf Platte an globalen Voxelgrenzen räumlich geteilt. Jede
PDAL-Kachel enthält höchstens **1.000.000 echte Punkte** plus einen anschließend
entfernten Ursprungspunkt. Überfüllte Kacheln werden weiter geteilt; einzelne besonders
dichte Voxel werden in Chunks von 250.000 Punkten reduziert. Bei gleicher Entfernung
gewinnt wie in PDAL der erste Quellpunkt. Zusätzlicher temporärer Plattenplatz wird
benötigt (Binärzwischenstand und beim Teilen dessen Kopie, zusätzlich zur Quell-LAZ).

[PDAL 2.10.2](https://github.com/PDAL/PDAL/blob/2.10.2/filters/VoxelCenterNearestNeighborFilter.cpp)
verankert sein Raster am Minimum der Eingabe. Der Ursprungspunkt hält das **globale**
Minimum auch in dünn besetzten Kacheln fest; bloßes Zuschneiden würde die Auswahl ändern.
Rohkoordinaten bleiben bis zur Auswahl als double erhalten, danach folgt die gewohnte
Millimeter-Quantisierung. Temporäre Dimensionen und Ursprungspunkte gelangen nicht ins Paket.

Gemessen am 05.10.2026 mit **2.000.000 synthetischen Punkten**, cell 0,02 m:
gekachelt **6,75 s / 310 MiB Spitzen-RSS**, Ganzwolkenfilter **5,29 s / 343,75 MiB**;
beide liefern dieselben **1.984.035 Punkte samt allen Attributen**. Der gekachelte Peak
ist das Maximum aus Container-RSS und bisheriger Host-Prozess-Hochwassermarke.
Diese Zahlen sind keine Hochrechnung auf große Echtdaten. Ein früherer Ganzwolkenlauf
mit 510 Mio. Punkten wurde bei 40g OOM-beendet (Kernel anon-rss 41,8 GB); die frühere
Angabe „14 GiB“ war eine Momentaufnahme. Ein gekachelter Echtdatenlauf steht noch aus.

PTS benötigt weiterhin den PDAL-Standardmodus beim Lesen. COPC-Ausgabe bleibt je nach
Modus separat speicherabhängig. Jeder Container bleibt standardmäßig auf **40 GiB inklusive Swap**
begrenzt. `--mode auto|direct|staged` und `--max-points` sind weiterhin möglich.
`SCANVIEW_PDAL_MEMORY` (z. B. `12g`) setzt die Speichergrenze inklusive Swap,
`SCANVIEW_PDAL_CPUS` (z. B. `4`) begrenzt die CPUs und `SCANVIEW_PDAL_LABEL`
(nur `A-Z`, `a-z`, `0-9`, `.`, `_`, `-`) setzt das Containerlabel `scanview.lauf`.
`build` benötigt E57, weil es auch E57-Panoramen erzeugt; scanlose Quellen mit `convert` bauen.

```bash
uv run --locked scanview-import convert /daten/wolke.laz /daten/paket --ausduennen 0.01
uv run --locked scanview-import verify /daten/wolke.laz /daten/paket
```

Manifest/Inventar protokollieren Quellname, SHA-256, Größe, Format, CRS-/Einheitenherkunft,
Quelleneinheit und Achse. `conversion-report.json` enthält Roh-, ausgedünnte und geschriebene
Punktzahlen sowie Containerzeit und Spitzen-RSS. Nach `convert` ist `ok: false`;
erst `verify` bestätigt das Paket. Seine Stichprobe verwendet Seed 42 und liest die
Originalquelle unabhängig von PDAL, chunkweise. Ohne Ausdünnung werden Originalpunktzahl,
Bounds (1,5 mm) und Quellpunkte gegen COPC geprüft. Bei Ausdünnung wird rückwärts geprüft:
Jeder COPC-Stichprobenpunkt muss im Original innerhalb eines Skalenschritts (1 mm) existieren.
Der Report zeigt entfernte Randpunkte separat (`sourceMaxDifferenceM`,
`sourceWithinE57Tolerance`): die Schrumpfung darf pro Achse höchstens cell + 1,5 mm betragen;
die Bounds der ausgedünnten Quellstufe müssen weiterhin innerhalb 1,5 mm mit COPC übereinstimmen.

### Punktklassen und Echos

Bei LAS/LAZ/COPC-Quellen zählt der Import die Klassenwerte der **ausgelieferten** Punkte (nach
Ausdünnung) in `cloud.classes` (Klassenwert als Text → Anzahl), die der ganzen Quelldatei in
`sourceOptions.classes` (entfällt mit `--max-points`). `classification` steht in `attributes`, sobald
ein Wert außerhalb von 0/1 (nie klassifiziert/unklassifiziert) vorkommt — auch bei nur einer Klasse;
`returns`, wenn ein Punkt mehr als ein Echo hat. Klasse, Echonummer und Echoanzahl bleiben je Punkt
erhalten. `verify` zählt die COPC erneut: exakt gleich dem Manifest, ohne Ausdünnung auch gleich der
Quelle, mit Ausdünnung je Klasse höchstens so viele wie in der Quelle.

## Höhensystem

Ein Paket hat **ein** Höhensystem (`frame.crs.verticalDatum`), festgelegt beim ersten Import.
Standard ist **DHHN2016 (NHN)**, ellipsoidisch (ETRS89/GRS80) ist wählbar
(Entscheidung [0015](../docs/entscheidungen/0015-hoehensystem.md)).

`convert` (Punktwolken und Meshes) und `add` (Meshes) nehmen:

| Option | Bedeutung |
|---|---|
| `--quell-hoehensystem` | Höhensystem der Eingabedatei: `dhhn2016` (auch `nhn`), `ellipsoidisch` (auch `ellipsoidal`), `dhhn92` |
| `--ziel-hoehensystem` | Höhensystem des Pakets; ohne Angabe `dhhn2016` |

```bash
uv run --locked scanview-import convert /daten/wolke.laz /daten/paket --quell-hoehensystem ellipsoidisch
uv run --locked scanview-import verify /daten/wolke.laz /daten/paket
```

- **Keine Annahme:** Ohne `--quell-hoehensystem` bleibt das Höhensystem unbekannt (`null`) und es
  wird nichts umgerechnet. Ein Ziel ohne Quelle bricht ab.
- **Umgerechnet** wird nur zwischen ellipsoidisch und DHHN2016, über das Quasigeoid GCG2016 des BKG
  (`gitter/de_bkg_gcg2016.tif`, Prüfsumme fest) und eine explizite PROJ-Pipeline. Das Quell-CRS
  muss ETRS89 UTM 32N oder 33N sein (EPSG:25832/25833, auch Varianten mit Achsfolge N-E wie
  EPSG:3045). WGS-84-UTM (z. B. EPSG:32632), lokale Rahmen und Pakete ohne CRS werden nicht
  umgerechnet. Fehlt das Gitter oder liegt ein Punkt außerhalb, bricht der Import ab. DHHN92 kann
  deklariert, aber nicht umgerechnet werden.
- **Ergänzen (`add`):** Das Paket bestimmt das Ziel. Ein abweichendes `--ziel-hoehensystem` oder
  ein unbekanntes Paket-Höhensystem bricht ab.
- **E57:** nur Deklaration, beide Optionen gleich (z. B. `--quell-hoehensystem dhhn2016
  --ziel-hoehensystem dhhn2016`); eine Umrechnung lehnt der Import ab.
- **Nachweis:** `sourceOptions.hoehe` der Ebene hält Quelle, Ziel, Gitter und dessen SHA-256.
  `verify` rechnet die Quellpunkte für den Vergleich genauso um.
- **Kameras aus `kameras.json`:** `panos-kameras` und `fotos-kameras` rechnen Kamerapositionen sowie
  Tie Points ins Paket-Höhensystem um, wenn `kameras.json` ellipsoidisch und das Paket DHHN2016
  ist (oder umgekehrt); die Drehung bleibt. Andere Abweichungen brechen ab.

## Lageanpassung (Verschiebung oder 3D-Ausrichtung)

Drohnenaufnahmen ohne RTK liegen oft etwas versetzt und leicht gekippt gegenüber den amtlichen
Daten (LoD2, DOP, DGM1). `convert` korrigiert das beim Import mit einer starren Bewegung:

- `--verschiebung DX,DY` (Meter im Paket-CRS) oder `--verschiebung datei.json` mit `dx_m`/`dy_m`:
  reine Verschiebung der Lage.
- `--transformation datei.json` mit `matrix_4x4` (zeilenweise, absolute Koordinaten im Paket-CRS):
  Drehung plus Verschiebung in x, y und z, Maßstab 1. `drehpunkt` [E, N, H] wird mitgeprüft.

Alle übrigen Felder der Datei (Referenz, Restfehler, Stand) landen als Herkunft in
`sourceOptions.lage` der Wolke.

- **Reihenfolge:** erst die Höhen an der Originallage umrechnen (`--quell-hoehensystem`), dann die
  Bewegung anwenden. Die Matrix muss also gegen die bereits umgerechneten Höhen bestimmt sein.
- **Ein Wert für alle Ebenen:** `panos-kameras` und `fotos-kameras` übernehmen die Bewegung aus
  dem Paket, Kameras samt Blickrichtung und Tie Points. `add` bewegt die Mesh-Vertices (nach der
  Höhenumrechnung) und vermerkt die Matrix in der Mesh-Ebene; frameCheck misst danach gegen die
  Wolke. `verify` bewegt die Quelle vor dem Vergleich genauso. Das Mesh also im Bezug der
  Ausgangswolke exportieren (vor der Bewegung), nicht im bereits bewegten.
- **Grenzen:** nur starre Bewegungen (orthonormal, det 1), Drehung höchstens 5°, Versatz höchstens
  100 m (Koordinatensystem prüfen statt verschieben). Für E57 noch nicht eingebaut.

## Panoramen aus `kameras.json`

Kugelpanoramen und orientierte Fotos kommen über das herstellerneutrale Austauschformat
`kameras.json` ins Paket (Felder und Konventionen unter „Austauschformat `kameras.json`“). Jede
Photogrammetrie-Software, die ausgerichtete Kameras samt Kalibrierung ausgeben kann, lässt sich
damit anbinden; Exportwerkzeuge für einzelne Programme gehören nicht zu diesem Repository.

Wolke und Kameras müssen **aus demselben Ausrichtungsstand** kommen, mit **demselben Ziel-CRS
und demselben Höhensystem**. Eine Übereinstimmung der EPSG-Nummer allein genügt nicht:
Ellipsoidhöhen und Gebrauchshöhen unterscheiden sich. Die Panoramen im **unbegradigten Original**
verwenden. Begradigte Kopien haben andere Pixelrichtungen und passen nicht zur exportierten Pose.

1. In der Photogrammetrie-Software die Punktwolke als LAS/LAZ oder E57 im gewählten metrischen
   Ziel-CRS exportieren.
2. Die ausgerichteten Kameras desselben Stands als `kameras.json` exportieren, zum Beispiel
   nach `/daten/projekt/kameras.json`. Für den Export gilt:
   - Kameras mit Gruppe (etwa `Boden`, `Luft`) behalten sie im Feld `group`; der Gruppenname
     wird im Standpunktnamen vorangestellt, etwa `Luft / Kamera A`.
   - Nicht ausgerichtete oder bewusst ausgelassene Kameras gehören mit Grund unter `skipped`.
     Bildpfade müssen absolut und zugänglich sein.
   - `crs.verticalDatum` **beschreibt** das tatsächlich exportierte Höhensystem; die Angabe
     rechnet keine Höhen um. Für Gebrauchshöhen müssen CRS (gegebenenfalls ein zusammengesetztes
     CRS) und Geoid-Konfiguration der Software dazu passen.
   - Kameraachsen numerisch ins Gitter des Ziel-CRS projizieren und orthonormalisieren, nicht
     die geografische Nordrichtung übernehmen (Meridiankonvergenz).
3. Zuerst das Wolkenpaket erzeugen, dann die Panoramen hinein schreiben:

   ```bash
   uv run --locked scanview-import convert /daten/projekt/wolke.laz /daten/projekt/paket
   uv run --locked scanview-import verify /daten/projekt/wolke.laz /daten/projekt/paket
   uv run --locked scanview-import panos-kameras --kameras /daten/projekt/kameras.json /daten/projekt/paket --hoehensystem ellipsoidisch > /daten/projekt/panorama-import.json
   ```

   Der LAZ-Export muss das CRS enthalten (sonst Sidecar oder `--crs EPSG:32632`).
   So lautet der Ablauf LAZ-Export → `convert` → `verify` → `panos-kameras`,
   ohne Zwischenumwandlung in E57. Ohne `--quell-hoehensystem` trägt `convert` kein bekanntes
   Höhensystem ein. In diesem Fall bestätigt
   `--hoehensystem` ausdrücklich die Höhen der **Punktwolke** und schreibt die Erklärung in das
   bereits vorhandene Feld `verticalDatum`. Sie muss exakt zur Kameraangabe passen.
   Ist das Höhensystem im Paket bereits deklariert, kann die Option entfallen; sie darf
   eine vorhandene Angabe nicht ändern. Weichen Kameras und Paket ab, rechnet der Import die
   Kamerahöhen um, sofern möglich (siehe „Höhensystem“). Fehlendes Paket, abweichendes oder nicht
   belegbares CRS, nicht umrechenbares Höhensystem, fehlende Bilder und ungültige Rotationen führen
   zum Abbruch.

### Orientierte Fotos (Rahmenkameras)

Derselbe Export (`formatVersion: 2`) kann auch ausgerichtete Rahmenkameras enthalten, zum
Beispiel Drohnenbilder (`kind: "frame"`). Danach in das vorhandene Paket:

```bash
uv run --locked scanview-import fotos-kameras --kameras /daten/projekt/kameras.json /daten/projekt/paket --prozesse 4
uv run --locked scanview-import fotos-pruefen /daten/projekt/paket
```

Die Wolke des Pakets muss aus **demselben Ausrichtungsstand** stammen wie die Kameras (sonst
Abbruch der Rahmenprüfung). `--hoehensystem` wirkt wie bei `panos-kameras`.
Prüfungen, Abbruchgründe und Datenschutz: `docs/public/fotos.md`.
Gemessen: 36,6-MP-Foto entzerren ≈ 13,5 s je Prozess.

### Orientierte Fotos aus COLMAP und OpenDroneMap

Ohne eigenen `kameras.json`-Export: eine georeferenzierte Rekonstruktion aus COLMAP oder
OpenDroneMap (ODM) direkt lesen.

```bash
# ODM-Lauf mindestens bis odm_georeferencing; Paket aus ODMs Wolke (Farbe dort 0–255):
uv run --locked scanview-import convert --rgb-8bit /daten/odm/projekt/odm_georeferencing/odm_georeferenced_model.laz /daten/paket
uv run --locked scanview-import fotos-odm --projekt /daten/odm/projekt --hoehensystem ellipsoidisch /daten/paket --prozesse 4

# COLMAP: Modell vorher mit model_aligner ins Paket-CRS bringen (Referenzkoordinaten im Ziel-CRS):
uv run --locked scanview-import fotos-colmap --modell /daten/colmap/sparse_utm --bilder /daten/colmap/bilder \
  --epsg 25833 --hoehensystem DHHN2016 /daten/paket
```

- `--hoehensystem` ist Pflicht: Es nennt das Höhensystem der Rekonstruktion und wird nicht geraten. Bei Ausrichtung
  über EXIF-GPS hängt es von der Kamera ab (Hersteller schreiben ellipsoidische oder Normalhöhen) — vorher prüfen. EPSG liest `fotos-odm` aus `odm_georeferencing/coords.txt`, `fotos-colmap` braucht `--epsg`;
  `--versatz E,N,H` addiert einen festen Versatz, falls das Modell im verschobenen Rahmen ausgerichtet wurde.
- Kameramodelle: COLMAP SIMPLE_PINHOLE, PINHOLE, SIMPLE_RADIAL, RADIAL, OPENCV, FULL_OPENCV ohne k4–k6;
  ODM/OpenSfM perspective und brown. Fisheye und Kugel brechen ab.
- Statt der Referenzpaare aus `kameras.json` belegen die Merkmalspositionen der Rekonstruktion das Kameramodell: Der
  Median-Restvektor muss ≤ 0,1 px sein (`modellbeleg` im Bericht, `provenance.modelCheck` in `images.json`).
- ODM: gelesen wird die lokale Rekonstruktion; scanview rechnet selbst nach UTM (gleiche Abbildung wie ODMs Wolke,
  Kameradrehung als echte Drehung). Die Rahmenprüfung gegen ODMs eigene Wolke belegt Konsistenz, keine Genauigkeit.
- Rahmen- und Rückprojektionsprüfung wie bei `fotos-kameras`. Die Wolke muss aus derselben Rekonstruktion stammen;
  eine dünne SfM-Wolke über flachem Gelände reicht dafür nicht („Szene zu eben“).

Jedes Panorama liefert sechs 90°-Lochkameraseiten. Die Seitengröße ist Bildbreite / 4 (abgerundet),
höchstens 4096 Pixel. Standardstufen sind 512, 1024, 2048 und 4096; `--levels 256,512,1024`
setzt andere Stufen. Keine Stufe wird hochskaliert. Kameras ohne erreichbare Stufe werden mit Grund
übersprungen; bleibt keine Kamera übrig, wird nichts geschrieben. Das JSON-Protokoll auf stdout
enthält `exported` und `skipped`.

Der Befehl ersetzt die Standpunktliste des Pakets wie der bestehende `panos`-Befehl; er hängt keine
E57-Standpunkte an. Paketformat und Viewer bleiben gleich. Kameraposen behalten absolute
Weltkoordinaten, der vorhandene Paketursprung bleibt erhalten. Die Punkte werden keinem Panorama
zugeordnet (`pointSourceId` ist `null`). Die Seiten werden erst beim Schreiben einzeln erzeugt.

Vor Nutzung mit echten Daten müssen der Kameraexport und die Deckung im Viewer geprüft werden.
Die synthetischen Tests belegen Format, Richtungen, Kanten und Projektion; sie ersetzen keine
Messung der realen Bilddeckung.

## Genauigkeitsnachweis (`genauigkeit.json`)

```bash
uv run --locked scanview-import genauigkeit --datei /daten/projekt/genauigkeit.json /daten/projekt/paket
```

Die Datei ist ein herstellerneutrales Austauschformat (Felder unten): Ein Export aus der
Photogrammetrie-Software schreibt Marker, Strecken und Kameragruppen samt Soll-, Ist- und
Genauigkeitswerten im Paket-CRS. Strecken, die außerhalb des Ausgleichs zum Skalieren dienten, tragen
`role: control` (Passstrecke); sonst zählen sie als Kontrollstrecke (`check`). `source.projectSha256`
ist die Prüfsumme der gespeicherten Projektdatei.

Schreibt `provenance.accuracy` an alle Punktwolken- und Mesh-Ebenen (`--ebene ID` wählt aus) und legt
`genauigkeit.json` ins Paket. Nur **Kontroll**punkte (`role: check`) und **Kontroll**strecken ergeben einen
Nachweis; Passpunkte (`control`) und Kamera-GNSS sind Ausgleichsresiduen und erscheinen nie als ±-Wert.

| Feld | Werte |
|---|---|
| `absolute.status` | `checked` (RMSE/max aus Kontrollpunkten), `adjustedOnly` (nur Passpunkte oder GNSS), `none` |
| `scale.status` | `checked` (alle Kontrollstrecken ≤ 3 × Sollgenauigkeit), `deviates`, `set` (nur Passstrecken), `none` |

Kennzahlen rechnet der Import selbst aus Soll- und Ist-Koordinaten. CRS (EPSG) muss zum Paket passen;
Höhen werden wie beim Punktimport ins Paket-Höhensystem umgerechnet. `scaleVerified` wird `true`, sobald
eine Strecke den Maßstab belegt (gesetzt oder geprüft), `scaleSource` nennt sie. `--bericht report.pdf`
prüft nur die Prüfsumme gegen `report.sha256`; das PDF wird nicht ins Paket kopiert (es kann Pfade,
Kameraseriennummern und Vorschaubilder enthalten). Bei jedem Fehler bleibt das Paket unverändert.

Format (`formatVersion: 1`): `crs` {`epsg`, `verticalDatum`}, `source` {`software`, `version`, `created`,
`projectSha256`}, `markers` [{`name`, `role`, `reference`, `referenceAccuracy`, `estimated`, `images`}],
`scalebars` [{`name`, `role`, `reference`, `referenceAccuracy`, `estimated`}], `cameraGroups` [{`name`,
`n`, `xyMedian`, `xy95`, `dzMedian`, `dz95`}], `report` ({`filename`, `sha256`} oder `null`). Unbekannte
Felder, nicht endliche Zahlen und mehr als 10 000 Einträge je Liste brechen ab.

## Austauschformat `kameras.json`

Herstellerneutrales JSON (UTF-8) für ausgerichtete Kameras, gelesen von `panos-kameras` und
`fotos-kameras`. Jede Software kann es erzeugen; der Import prüft die ganze Datei samt Bildern,
bevor er etwas schreibt.

**Kopf**

| Feld | Inhalt |
|---|---|
| `formatVersion` | `1` (nur Kugelkameras) oder `2` (Kugel- und Rahmenkameras) |
| `crs.epsg` | positive ganze Zahl, metrisches Ziel-CRS der Wolke (z. B. 25833) |
| `crs.verticalDatum` | Höhensystem der Positionen als Text, z. B. `ellipsoidisch`, `DHHN2016` |
| `cameras` | Liste der Kameras |
| `skipped` | optional: [{`label`, `reason`}] — ausgelassene Kameras mit Grund |
| `sensors` | nur v2, Pflicht bei Rahmenkameras: Sensoren mit Kalibrierung |
| `tiePoints` | nur v2, optional: [[E, N, H], …] Verknüpfungspunkte im Weltrahmen |
| `tieProjections` | nur v2, optional: [{`camera`, `pixel`: [u, v], `world`: [E, N, H]}] |
| `document` | nur v2, optional: Objekt zur Herkunft (etwa `path`, `mtime`, `chunk`); landet unverändert in `provenance.document` von `images.json` |

**Kamera** (`cameras[]`)

| Feld | Inhalt |
|---|---|
| `label` | Name, Text; bei Rahmenkameras über alle Gruppen eindeutig (Tie-Point-Projektionen hängen nur am Label) |
| `group` | Gruppe, Text (leer erlaubt) |
| `kind` | nur v2: `spherical` oder `frame`; andere Arten werden übersprungen |
| `imagePath` | absoluter Pfad zum Originalbild (unbegradigt, unverändert) |
| `width`, `height` | Bildmaße in Pixeln; Kugelbild 2:1, Rahmenkamera gleich dem Sensor |
| `sensor` | nur Rahmenkamera: `id` des Sensors |
| `position` | [E, N, H] Projektionszentrum in Metern im Ziel-CRS, Höhe im `verticalDatum` |
| `rotation` | 3×3-Matrix (zeilenweise); ihre **Spalten** sind die Kameraachsen X, Y, Z in Weltkoordinaten (Kamera → Welt), rechtshändig orthonormal (Toleranz 1e-6) |

**Achsen.** Kamerabasis des Austauschformats: +X rechts, +Y unten, +Z Blickrichtung (bei
Kugelkameras die Mitte des Equirektangularbilds; x im Bild nach rechts, y nach unten). Die Achsen
liegen im Gitter des Ziel-CRS (Easting, Northing, Höhe), nicht in geografischer Nordrichtung. Die
Umrechnung in die Paket-Konvention (+Y oben, -Z Blick, `R_paket = R @ diag(1, -1, -1)`) macht
ausschließlich der Import.

**Pixel.** Ursprung in der linken oberen Bildecke, die Mitte des Pixels (i, j) liegt bei
(i + 0,5, j + 0,5). Gerechnet wird in Sensorpixeln: Bilder mit EXIF-Orientation ≠ 1 brechen ab
(Ausnahme `fotos-kameras --exif-drehung-ignorieren`, siehe `docs/public/fotos.md`).

**Sensor** (`sensors[]`, nur Rahmenkameras): `id` (ganze Zahl), `label`, `type: "frame"`,
`width`, `height`, `rollingShutter`, `calibrationAdjusted`, `calibration` und `referencePairs`.

- `rollingShutter` (true/false, Pflichtangabe des Exports): ob die Ausrichtung für diesen Sensor
  eine Rolling-Shutter-Kompensation verwendet. Das Kameramodell bildet sie nicht ab; bei `true`
  bricht der Import ab.
- `calibrationAdjusted` (true/false, Pflichtangabe des Exports): ob `calibration` die in der
  Ausrichtung angepasste Kalibrierung ist (nicht nur Startwerte). Bei `false` bricht der Import ab.
- Fehlen die Felder (ältere Exporte), gilt `rollingShutter: false` und `calibrationAdjusted: true`.

`calibration` enthält `f`, `cx`, `cy`, `k1`, `k2`, `k3`, `k4`, `p1`, `p2`, `b1`, `b2` (alle Pflicht,
Pixel bzw. dimensionslos; `p3`/`p4` nur mit Wert 0). Modell für einen Punkt (X, Y, Z) im
Kamerakoordinatensystem:

```text
x = X/Z, y = Y/Z, r² = x² + y²
x' = x(1 + k1r² + k2r⁴ + k3r⁶ + k4r⁸) + (p1(r² + 2x²) + 2p2xy)
y' = y(1 + k1r² + k2r⁴ + k3r⁶ + k4r⁸) + (p2(r² + 2y²) + 2p1xy)
u = width/2 + cx + x'f + x'b1 + y'b2
v = height/2 + cy + y'f
```

`cx`/`cy` sind also Versätze des Hauptpunkts gegen die Bildmitte. Die Tangentialterme stehen in
anderer Reihenfolge als bei OpenCV (OpenCV-`p1` entspricht hier `p2` und umgekehrt); Koeffizienten
nie nur nach Namen übertragen.

`referencePairs` belegt, dass dieses Modell der Projektion der Software entspricht: mindestens 8
Einträge `{"ray": [x, y], "pixel": [u, v]}`, `ray` als normierte Kamerakoordinaten (X/Z, Y/Z),
`pixel` aus der **eigenen Projektionsfunktion der Software** (nicht aus obigen Formeln), über das
Bild verteilt bis nahe an den Rand. Weicht ein Paar mehr als 0,1 px vom Modell ab, bricht der Import
ab.

`tiePoints` dienen der Rahmenprüfung (Fotos und Paketwolke aus demselben Ausrichtungsstand),
`tieProjections` der Rückprojektionsprüfung: gemessene Pixel eines Verknüpfungspunkts im
Originalbild der Kamera `camera` samt seiner Weltkoordinate. Eine Stichprobe genügt (etwa
höchstens 50 Kameras je Gruppe mit je bis zu 200 Projektionen); Punkte im Unendlichen weglassen.

Version 1 kennt nur `formatVersion`, `crs`, `cameras` (ohne `kind`, alle Kugelkameras) und
`skipped`.

## Sonne: Verschattung und Ertrag (`scanview-sonne`, Extra `[sonne]`)

```bash
uv sync --extra sonne
# Fläche: Flächenmessung aus dem Viewer als JSON ({"kind": "area", "points": [[x, y, z], …]}, Weltkoordinaten)
uv run scanview-sonne bericht --paket <paket> --flaeche flaeche.json --modul-wp 800 --kappung-w 800 --aus bericht/
# Rechenweg ohne Verschattung gegen PVGIS PVcalc (Neigung/Azimut, Süd = 180)
uv run scanview-sonne gegenprobe --breite 52.39 --laenge 13.03 35/180 90/270
```

- Liest die erste Mesh-Ebene (`--ebene`), legt Stichpunkte im Raster (`--raster`, Vorgabe 10 cm) auf das Mesh und
  rechnet je Stichpunkt eine Richtungskarte der Verdeckung (2° × 1°). Daraus folgen Direktmaske, Himmelssicht und
  Horizontband; der Fernhorizont kommt aus PVGIS `printhorizon`, die Einstrahlung aus `seriescalc`/`tmy` mit
  `usehorizon=0`.
- PVGIS wird nur von hier abgerufen und mit URL und Abrufdatum unter `~/.cache/scanview-sonne/pvgis` abgelegt;
  `--offline` rechnet nur aus dem Cache.
- Ausgabe: `ergebnis.json` (alle Zahlen, Prüfsumme in `lauf.json`), `stichpunkte.csv`/`.geojson`, `waermekarte.png`,
  `bericht.html` und `bericht.pdf` (headless Chromium). Gleiche Eingaben ergeben dieselbe Prüfsumme.
- Große Pakete als Unit mit Speichergrenze starten (`systemd-run --user -p MemoryMax=…`); ein Paket mit 2,2 Mio.
  Dreiecken braucht ≈ 2 GB.

### Sonnenbericht Garten (`scanview-sonne garten`)

```bash
uv run scanview-sonne garten --paket <paket> --flaeche beet.json --aus garten/ --belaubung "voll belaubt"
```

- Die Fläche gilt als **Grundriss**: Stichpunkte im XY-Raster (`--raster`, Vorgabe 20 cm), der Boden je Punkt ist
  der erste Treffer eines senkrechten Strahls von unten, gesucht nur ±`--fenster` (Vorgabe 1 m) um die Ebene durch
  die Ecken. So landet ein Punkt unter einem Baum am Boden und nicht durch ein Mesh-Loch in der Krone.
- Je Bodenpunkt direkte Sonnenstunden je Tag und Monat (Viertelstunden 2023, ohne Wolken), Lichtlage nach RHS aus
  dem Juni/Juli-Mittel: sonnig ab 6 h, halbschattig 3–6 h, mäßig schattig 2–3 h, schattig unter 2 h; ±0,4 h um
  eine Schwelle gelten als Grenzbereich. Dazu die Himmelssicht (Streulicht).
- Baumkronen aus Fotos sind im Mesh löchrig. Deshalb rechnet der Bericht zweimal: mit dem Mesh wie aufgenommen und
  mit einem dichten Kronendach (höchste Mesh-Ecke je 50-cm-Zelle über 2 m Wuchshöhe, nie näher als 2 m über einem
  Stichpunkt). Jede Zahl steht als Spanne; „unsicher“ heißt Grenzbereich oder je nach Kronen andere Lichtlage.
- PVGIS nur für den Geländehorizont (`printhorizon`). Ausgabe wie beim Ertragsbericht, Karten `kategorien.png`
  und `stunden.png`. Eine Pflanzenliste gibt es noch nicht; der Bericht nennt Boden und Feuchte als Pflichtfragen.

## Lieferung prüfen (`scanview-pruefen`, Stufen P1 bis P3, B1)

```bash
uv run scanview-pruefen <lieferordner> --profil bestandserfassung --aus pruefung/ [--paket <paket>] [--pdf]
uv run scanview-pruefen <lieferordner> --katalog eigener-katalog.toml --aus pruefung/
# Dichte, Lücken, Bodenauflösung brauchen die Auftragsfläche des Auftraggebers
uv run scanview-pruefen <lieferordner> --profil bestandserfassung --flaeche auftrag.geojson --paket <paket> --aus pruefung/
# Boden gegen das amtliche DGM des Auftraggebers (Kacheln: --dgm mehrfach)
uv run scanview-pruefen <lieferordner> --profil bestandserfassung --dgm dgm1_33_365_5806.tif --aus pruefung/
```

- Prüft eine fremde Lieferung gegen einen Anforderungskatalog (TOML, `schema = 1`; Beispielprofil
  `src/scanview_import/pruefen/profile/bestandserfassung.toml`). Je Zeile **erfüllt**, **nicht erfüllt** oder
  **nicht prüfbar**, mit Grund und Belegen. Fehlt, was die Zeile verlangt (Datei, Deklaration), ist das „nicht
  erfüllt“; „nicht prüfbar“ heißt nur, dass das Prüfwerkzeug fehlt (z. B. Lagemessung am Modell, Befunde, Zeitstände).
- Prüfarten P1: `bezugssystem` (LAS-Kopf, GeoTIFF-Schlüssel, E57, Paket), `klassen`, `raster` (GeoTIFF ohne GDAL),
  `formate` (Kopf und Lesbarkeit; RCP/DWG nur Vorhandensein), `pruefsummen` (sha256sum-Liste; fehlend,
  abweichend, überzählig getrennt), `anhang`, `bericht`.
- Prüfarten P2 über der Auftragsfläche (`--flaeche`, GeoJSON; `crs`-Member oder WGS84, sonst `--flaeche-epsg`,
  geraten wird nie): `dichte` (Punkte/m² je Zelle, Anteil der Fläche), `luecken` (Anteil Zellen ohne Punkt, Karte im
  Bericht), `gsd` (Bodenauflösung je senkrechtem Luftbild aus `images.json` des Pakets: Entfernung entlang der
  Blickachse bis zum Boden durch `focalPx`; Boden = Referenz-DGM, wenn `--dgm` passt, sonst Klasse 2 der Wolke mit
  Vorbehalt im Beleg). Ein Lesedurchgang für alle drei; je Zelle zählt das Maximum
  über die Dateien, damit eine doppelt gelieferte Wolke die Dichte nicht verdoppelt. Ohne Fläche: „nicht prüfbar
  (Grundlage fehlt)“.
- Prüfarten P3 (Kontrollpunkte, F15 Teil 2): Liste als CSV aus der Lieferung (`*kontrollpunkt*.csv`) oder vom
  Auftraggeber (`--kontrollpunkte`, `--kontrollpunkte-crs`). Kopf `id;rolle;e;n;h` plus optional
  `sigma_lage;sigma_hoehe;ist_e;ist_n;ist_h`, Trenner `;` (Dezimalkomma erlaubt) oder `,`; Bezugssystem mit Höhe muss
  deklariert sein (`# crs = EPSG:25833+7837`), umgerechnet wird nie. Nur Rolle `check`/`kontrollpunkt` zählt,
  `control`/`passpunkt` nie. `kontrollpunkte` mit `rmse_v_m`: Höhe misst siteBits an der Wolke (Median im Radius,
  Klasse 2 bevorzugt); mit `rmse_h_m`: Lage aus den Ist-Spalten — vom Messenden geliefert oder im Viewer-Panel
  „Kontrollpunkte“ am Modell gepickt (Export mit `# quelle = siteBits-Viewer …, am Modell gepickt`, im Beleg so
  ausgewiesen). Je RMSE eine Einordnung in BFR-Verm OGL/OGH, USIBD LOA und Eckstein-Stufen (Orientierung, kein Urteil). RMSE = √(Messung² + RMS σ²),
  Ausreißer bleiben drin, nicht messbare Punkte einzeln. `massstab`: Strecken aus Kontrollpunktpaaren.
  Messwerte je Punkt in `ergebnis.json` und `<Zeile>-messwerte.csv`.
- Prüfarten B1 gegen das Referenz-DGM des Auftraggebers (`--dgm`, Float-GeoTIFF ohne GDAL gelesen; Höhensystem aus
  GeoKey 4096 oder `--dgm-crs EPSG:25833+7837`, umgerechnet wird nie; Kacheln auf demselben Pixelgitter). Verglichen
  wird je DGM-Pixel: Mittel der Bodenpunkte (Klasse 2) minus DGM, nicht interpoliert. `einpassung`: mittlere
  absolute Abweichung über alle verglichenen Pixel, große Abweichungen bleiben drin (nur Höhe; bDOM und Lage folgen in
  J4). `klassen_richtig`: Anteil der Pixel mit Boden jenseits `toleranz_m`, getrennt darüber (Dach oder Bewuchs als
  Boden) und darunter, mit Karte. Betrachtet werden die Pixel der Auftragsfläche, ohne Fläche alle mit Punkten;
  Pixel ohne DGM-Wert oder ohne Bodenpunkt werden gezählt, `min_ueberdeckung` verlangt einen Mindestanteil. Ohne
  DGM: „nicht prüfbar (Grundlage fehlt)“.
- Die Lieferung wird nur gelesen, symbolische Links werden nicht verfolgt, Pfade aus Katalog und Prüfsummenliste
  bleiben im Lieferordner. Kein Netz. Fläche, Referenz-DGM und externes Paket stehen mit SHA-256 in der Kette.
- Ausgabe: `ergebnis.json` (deterministisch, Prüfsumme in `lauf.json`), `pruefsummen.sha256` der gelesenen
  Dateien, Karten `<Zeile>-dichte.png`/`-luecken.png`/`-boden.png`, `bericht.html`, mit `--pdf` auch `bericht.pdf`. Exit 0: nichts „nicht erfüllt“, 1: mindestens eine
  Zeile nicht erfüllt, 2: Katalog- oder Eingabefehler.

## Prüfung

```bash
uv run --locked pytest
uv run --locked ruff check .
uv run --locked mypy src
```

Die Tests verwenden synthetische Bilder und Kameras. Für die bestehenden
Docker-Smoke-Tests muss `busybox:latest` lokal vorhanden sein.


## Ebene beim Start ausblenden

```
scanview-import start-sichtbar <paket> <ebenen-id> aus   # bzw. an
```

Setzt `startVisible: false` an der Ebene in `project.json` (`an` entfernt das Feld wieder). Der Viewer
zeigt die Ebene dann beim Öffnen nicht; eine so ausgeblendete Punktwolke lädt er gar nicht erst. Nutzer
schalten sie im Ebenenmenü ein, ein Link mit `ly=` hat Vorrang. Gedacht etwa für Projekte, die mit dem
Mesh beginnen sollen, weil die Punktwolke aus der Nähe grob wirkt.

## Mesh-Import Weg A

```bash
# Einmalig KTX-Werkzeug bauen. Laufzeit verwendet die geprüfte Image-ID, keinen Tag.
docker build -f docker/meshtools.Dockerfile -t scanview-meshtools:4.4.2 docker
docker image inspect scanview-meshtools:4.4.2 --format '{{.Id}}'

uv run --locked scanview-import convert /daten/modell.obj /daten/mesh-paket --unit m
uv run --locked scanview-import convert /daten/modell.glb /daten/glb-paket
uv run --locked scanview-import add --same-frame /daten/modell.obj /daten/wolken-paket --unit m
uv run --locked scanview-import verify /daten/modell.obj /daten/wolken-paket
# Rahmenprüfung ohne Verkleinerung:
uv run --locked scanview-import convert /daten/modell.obj /daten/rahmen-paket --unit m --dreiecke 0
```

Die Image-ID muss mit `KTX_IMAGE` in `src/scanview_import/mesh_textures.py` übereinstimmen.
Ein Neubau kann eine andere ID liefern (Debian-Pakete); dann bewusst prüfen und den Pin
aktualisieren. Es gibt keinen automatischen Tag-Fallback. Ohne Textur braucht das Mesh
keinen KTX-Prozess. Die Container laufen ohne Netz, als eigene UID, mit 40-GiB-Grenze,
allen Capabilities entzogen und nur einem eigenen schreibbaren Arbeitsordner.
Auch die KTX-Container werten `SCANVIEW_PDAL_MEMORY` (Standard `40g`, inklusive Swap),
`SCANVIEW_PDAL_CPUS` (optional) und `SCANVIEW_PDAL_LABEL` (optional, Label `scanview.lauf`) aus.

OBJ liest Positionen streaming in Float64, trennt Grafikvertices an UV-/Normalennähten
und übernimmt `v x y z r g b` als `COLOR_0`. MTL-`map_Kd` muss vorhanden sein;
fehlende Dateien oder fehlende UV führen zum Abbruch. Optionen in `map_Kd` werden
explizit abgelehnt. PLY unterstützt ASCII sowie binär (beide Endianness), scalar
Vertexattribute und einen `vertex_indices`-Face-Index; STL binär/ASCII. OBJ/PLY müssen
bereits trianguliert sein. Animationen, Skinning, Draco und transparente glTF-Materialien
werden abgelehnt. Meshopt-Quellen werden für ATTRIBUTES/TRIANGLES (UInt32-Indizes),
Filter NONE, unterstützt; andere Modi/Filter werden explizit abgelehnt. FBX/DAE/3D Tiles folgen
nicht in dieser Scheibe. glTF-Node-Matrizen/TRS und BaseColor-Faktoren werden übernommen.

OBJ/PLY/STL benötigen `--unit m|cm|mm|ft` oder `<datei>.scanview.json`.
`--up y` dreht nach Z oben; glTF ist immer Meter/Y oben, Widersprüche werden abgelehnt.
CRS und Einheit verwenden denselben Resolver wie der Punktimport. Ohne CRS bleibt die
Quelle lokal. Keine Registrierung und keine Umprojektion. Exportiertes glTF ist
standardkonform Y oben: `(x,y,z)` im lokalen Paketrahmen wird `(x,z,-y)`;
`originWorld` bleibt Float64 im Manifest. Beide GLBs verwenden
`EXT_meshopt_compression`, ohne unkomprimierte Geometriekopie. Positionen sind lokale
UInt16-Werte (`KHR_mesh_quantization`, 8-Byte-Stride); eine von Anfang an definierte
Node-Translation und isotrope Skalierung dekodieren sie in den lokalen Paketrahmen.
Cache-/Fetch-Reihenfolge wird pro Material optimiert, UV im Bereich [0,1]
werden als normalisierte UInt16-Werte gespeichert (maximaler Rundungsfehler
1/131070 UV, etwa 0,031 Pixel bei 4096²); UV außerhalb dieses Bereichs bleiben
Float32. Attribute werden immer gemeinsam umgeordnet. Die Positionsschrittweite
ist maximale Ausdehnung/65535 und steht für die Vollstufe unter
`positionStepM`, für beide Stufen unter `positionStepsM`. Rundungsfehler bleiben
≤0,5 mm in 3D. Bei Ausdehnungen über etwa 37,84 m wird stattdessen Float32 auf
mindestens 0,25-mm-Raster verwendet (keine Integer-Erweiterung); das
Kontrollvertex-Tor bleibt ≤1 mm. Kein nachträglicher Rahmenpatch.

Vollstufe standardmäßig **1.000.000** Dreiecke (`--dreiecke`), Vorschau **200.000**
(`--vorschau-dreiecke`). `0` erhält die Quelle; kleine Quellen werden nicht vergrößert.
In der Vollstufe verwendet meshoptimizer UV/Farben als Attribute, erhält Nähte
und sucht das Fehlerziel.
Ist ±5 % unter diesen Bedingungen unerreichbar, verwendet der Import die kleinste
nahterhaltende Stufe, warnt und weist Ziel, erreichte Zahl und Grund unter `stufen` im
Manifest aus. Photogrammetrie-Meshes bestehen aus vielen kleinen Texturinseln; jede
Inselgrenze ist eine Naht (Denkmal-Mesh, Stufe normal: Untergrenze rund 700.000 von
2.992.910 Dreiecken). Nähte kollabieren nie, die Textur verrutscht also nicht.
Texturen der Vollstufe: ≤4096 Pixel je Kante, KTX2-UASTC mit RDO λ=2
(deterministisch), Zstd 9 und Mipmaps; `uastcRdoLambda`/`zstdLevel` stehen im Texturreport.
Die Vorschau verschweißt ausschließlich **exakt gleiche Float64-Positionen**,
abtastet Farben aus der Quelltextur und mittelt alle UV-/Materialkopien im **linearen
sRGB-Farbraum**. Ohne Textur verwendet sie Vertexfarben, sonst Weiß. Danach wird mit
Farbe als Simplifier-Attribut auf das Ziel ±5 % verkleinert. `preview.glb` enthält
lineares, normalisiertes UInt8-`COLOR_0`, keine UV und keine Textur;
`previewColor: "vertex"`, `textures.stages.preview: []`. Ein unerreichbares
Vorschauziel führt zum Abbruch.

Echt entartete Dreiecke (doppelte Indizes oder exakt Fläche null, kein
Flächenschwellwert) werden vor dem Schreiben entfernt. Der Report und das Manifest
weisen die Entfernungen getrennt nach Quelle, Simplifier und Quantisierung aus
(`degenerateRemoval`). Die Zielsuche berücksichtigt die nach Bereinigung tatsächlich
ausgelieferte Dreieckszahl. `--dreiecke 0` bewahrt alle Kontrollvertices und alle
gültigen Flächen, entfernt jedoch ebenfalls Nullflächen.

`convert` verlangt ein neues Paket. `add` verlangt Schema 2, eine Punktwolke,
`--same-frame`, gleiches CRS und widerspruchsfreie Quelleneinheit. Es verändert weder
Punktwolke noch deren Ebene oder `originWorld`. Nur eine Mesh-Ebene pro Paket in
Weg A. Beide Ausgaben werden vor Veröffentlichung zurückgelesen und gemessen.
Ein fehlgeschlagenes verify/frameCheck veröffentlicht keine Ebene.

Report `layers/mesh/conversion-report.json`: Quell-/Stufenzahlen, entartete Dreiecke,
16 Kontrollvertices (6 Achsenextrema + 10 zufällige, Seed 42), Grenzen, 10.000
flächengewichtete baryzentrische Stichproben **je Richtung und Stufe**, p50/p95/max
in mm, Texturzahl/Pixels/Kodierung und mittleres |ΔRGB| aus 1.000 UV-Proben je Textur
gegen die wirklich dekodierte KTX2-Datei. Für die Vertexfarben-Vorschau werden
1.000 Quell-UV-/Materialkopien (Seed 42) mit der nächsten ausgelieferten Vertexfarbe
verglichen; Mittel |ΔRGB| im sRGB-Raum (0-255) wird unter `colors` ausgewiesen und
ist **kein Farbtreuetor**. Die Geometrieprüfung dekodiert die **ausgelieferte
meshopt-Datei selbst** mit dem meshoptimizer-Decoder, inklusive der
Quantisierungs-Node-Transformation. Beim unverkleinerten Lauf müssen alle
Kontrollvertices ≤1 mm liegen. Vollstufe→Quelle p95 und Bounds müssen ≤`--toleranz`
liegen (Standard **10 mm**). Vorschauwerte werden ausgewiesen; ihr geometrisches
p95 ist kein Vollstufentor. Die Vorschau muss das Dreiecksziel, vorhandene Vertexfarben, Texturfreiheit und
null entartete Dreiecke bestehen. Ein unter Erhalt der Nähte unerreichbares Ziel gilt nur mit genau der im Manifest
ausgewiesenen Dreieckszahl als bestanden (`targetUnreachableDeclared`). Laufzeit, Container-/Host-Peak-RAM und GLB-Größen stehen ebenfalls im Report.

`frameCheck` verwendet bis zu 100.000 echte Wolkenpunkte (Seed 42, bei kleineren
Wolken alle) **im Grundriss des Meshes** (1-m-Raster aus den Dreiecksrechtecken; ein Teil-Mesh wie
ein Gebäude im Gelände wird nur dort gemessen, unter 1.000 Punkten Abbruch), exakten
Punkt-Dreieck-Abstand zur geschriebenen Vollstufe, p50 ≤5 mm und p95 ≤25 mm. Photogrammetrische
Meshes gegen photogrammetrische Wolken streuen stärker (Vegetation, Kabel): `add --rahmen-grenze
P50_MM,P95_MM` hebt die Grenzen ausdrücklich an (höchstens 50/300 mm — echte Rahmenfehler wie
fehlende Lagekorrektur, Geoid oder Maßstab liegen bei Dezimetern bis Metern), vermerkt als
`frameCheck.limitsM`/`raised`. Wurde Vegetation aus dem Mesh entfernt (Nachbearbeitung), steht sie
noch in der Wolke: `add --rahmen-ohne-klassen 5,7` lässt diese Wolkenklassen bei der Messung weg,
vermerkt als `frameCheck.excludedClasses`. Die Stufenprüfung nutzt `--toleranz` für p95 und Ausdehnung; ein
bereinigtes Teil-Mesh verliert am Rand oft mehr Ausdehnung als Fläche: `add --ausdehnung-toleranz
MM` (höchstens 500 mm) trennt beides, vermerkt als `sourceOptions.ausdehnungToleranzM`. `add` rechnet frameCheck und Stufenprüfung und bricht erst danach
mit beiden Berichten ab. `verify` misst auch diese Deckung neu (mit der vermerkten Grenze), statt
einen alten Manifestwert zu übernehmen. Manifest `verify.ok` ist erst danach wahr.
Herkunft bleibt vorsichtig: `scaleVerified: false`, `interpolated: true`; das
Format belegt keine metrische Genauigkeit. Viewer/Messen auf Meshes folgt separat.


### E57-Koordinaten in einfacher Genauigkeit (Float32)

Seit dem Fix für 0.7.1 brechen `convert`, `build`, `verify`, Bounds-Ermittlung und
E57-Ausdünnungsprüfung ab, wenn der Koordinaten-Prototyp Float32 deklariert und
lokale Beträge mindestens 16 384 m erreichen. Ab dieser Grenze beträgt der
Float32-Schritt etwa 1,95 mm; bei 5 806 700 m sind es 0,5 m. Eine große
Pose-Translation bei kleinen lokalen Koordinaten ist davon nicht betroffen.
Double und ScaledInteger bleiben erlaubt. Fehlen lokale `cartesianBounds`,
werden bis zu 10 000 Punkte vor der Pose als Stichprobe gelesen; spätere
Extremwerte kann diese Vorprüfung ohne vollständigen Scan nicht ausschließen.

Die Quelle mit doppelter Genauigkeit neu exportieren (PDAL `writers.e57`:
`double_precision=true`). Bereits verlorene Genauigkeit lässt sich durch den
Import nicht wiederherstellen. Ein bewusster Durchgang ist mit
`--float32-zulassen` bei `convert`, `build` und `verify` möglich. Das Manifest
(`conversion`) und der Prüfbericht enthalten dann `float32Schritt_m` und
`warnings`. Ein Vergleich mit derselben gerasterten Quelle bestätigt keine
ursprüngliche Messgenauigkeit.

### Gaussian-Sichtschicht

Nur für Ansicht, niemals zum Messen/Fangen. Eingaben: Standard-3DGS `.ply` (aktivierte Skalen
und Opacity werden aus den üblichen Log-/Logit-Feldern gelesen), `.splat` (32 Byte/Gaussian),
`.spz` v2/v3 ohne Erweiterungen oder Antialiasing-Flag. SPZ RUB wird gemäß Format in RDF
zurückgeführt. Nicht unterstützte Varianten werden ausdrücklich abgelehnt.

```sh
scanview-import add-splats quelle.ply paket --same-frame --crs EPSG:25833 --startebene
scanview-import add-splats quelle.spz paket --colmap images.bin --kameras welt.csv --startebene
```

Beim ersten Weg bedeutet `--same-frame`: Quelle bereits in Metern, Achsen und CRS des Pakets.
Höhenumrechnung: `--quell-hoehensystem` (Paket bestimmt Ziel, wie Mesh); anschließend wird die
Lagematrix aus `sourceOptions.lage` der Paketwolke übernommen. Gauß-Mittelpunkte, Quaternions
und Skalen werden gemeinsam transformiert. Beim Kameraweg liegen die Weltzentren **bereits im
endgültigen Paketrahmen/Höhensystem**, inklusive Lageanpassung; keine zweite Anwendung.
Kameranamen müssen exakt übereinstimmen, kein Abschneiden von Pfaden oder Dateiendungen.
Welt-CSV: `label,E,N,H`. Kamera-XML (Elemente `chunk`, `transform`, `camera`): ein lokaler Chunk samt
Chunk-Transformation;
georeferenziertes XML wird konservativ abgewiesen, dafür Welt-CSV exportieren.

Mindestens vier nicht kollineare Kamerapaare, 80 % Inlier, positiver Maßstab; standardmäßig
höchstens 25 mm Residuum je Inlier, ausdrücklich anhebbar mit `--kamera-grenze MM` bis 300 mm.
Der Bericht nennt alle Residuen, Ausreißer, Maßstab/Drehung/Translation und angehobene Grenze.
SH-Grad 1–3 bleibt erhalten und wird bandweise mit der Rahmenrotation gedreht.
Die reelle 3DGS-Basis folgt `l=1..3, m=-l..l`, Condon-Shortley; Rotation erfüllt
`F_neu(R d) = F_alt(d)`. Tests prüfen bekannte und beliebige Drehungen mit unabhängigen
Blickrichtungen, außerdem vollständigen Import mit Kamerafit und SPZ-Dekodierung.

Danach Deckungsprüfung der **geschriebenen** Gaussian-Mittelpunkte (Opacity ≥ 0,5) gegen
Wolkenpunkte in belegten 1-m-Footprintzellen; mindestens je 1.000, standardmäßig p50 ≤ 5 mm,
p95 ≤ 25 mm. `--rahmen-grenze P50_MM P95_MM` hebt die Grenzen ausdrücklich an (max. 50/300 mm),
mit Vermerk. Keine Veröffentlichung bei fehlendem oder negativem Beleg. Ohne Referenzwolke
ist `add-splats` deshalb gesperrt; bereits geprüfte Splat-only-Pakete kann der Viewer lesen.

Kleine Ebenen: Standardausgabe SPZ v3 (gzip, SH bis 3) ohne weitere Abhängigkeit, nativ im Python-Import.
Mittelpunkte relativ zu `originWorld`, RUB auf Platte; Spark wird im Viewer zurückgedreht.
Positionsschritt 2^-12 m, Rückprüfung nach Dekodieren gegen transformierte Zentren:
maximal √3 × 2^-13 m + 10 nm Rechentoleranz, etwa 0,2115 mm. Auch die Footprintzellen
berücksichtigen ausschließlich diese Kodiertoleranz; die Distanz-Gates bleiben gleich.
Log-Skalen quantisieren mit 1/16, SH mit 1/128, Opacity mit 1/255. Farb-/SH-/Skalenüberlauf
und Positionen außerhalb ±2048 m zum Ursprung scheitern vor Veröffentlichung. Kein stilles
Clipping. `--ausgabeformat ply` liefert den expliziten Float32-Notweg mit 1-mm-Rückprüfung
und ungekürzten SH-Koeffizienten. Quelle bleibt unverändert.

`--startkameras welt.csv` (oder lokales XML wie oben) setzt nur bei fehlender `initialView`
eine Startposition aus dem komponentenweisen Median der ausdrücklich angegebenen
Trainings-Weltzentren, mit Blick auf den Gaussian-Schwerpunkt. Bestehende Ansichten bleiben.
`--startebene` wählt zusätzlich die Sichtschicht. Weltzentren müssen im endgültigen
Paketrahmen liegen; kein automatisches Lesen/Erraten von Trainingskameras.

Eine Sichtschicht pro Paket. Ohne Paging bleiben maximal 1.000.000 Gaussians;
RAD unterstützt bis zu 10.000.000, Eingaben bis 4 GiB. Das ist eine Importgrenze,
kein garantierter GPU-Verbrauch oder Durchsatz für echte Szenen.

`--ausgabeformat auto` (Standard) wählt über einer Million RAD, wenn
`--build-lod /pfad/build-lod` oder `SCANVIEW_BUILD_LOD` gesetzt ist. Kleine Ebenen bleiben SPZ.
`--ausgabeformat rad` verlangt das Werkzeug ausdrücklich. Fehlt es, bleibt der SPZ-Weg mit
seinem bisherigen Limit: eine große Quelle scheitert mit einer klaren Meldung, kein ungeprüfter Export.
Es wird nichts installiert oder kompiliert. Freigegeben ist Spark Tag **v2.3.0**, Commit
`cce78e2cf5505679abbaf64236e0834de385010c`, MIT; der SHA256 des freigegebenen Binaries ist
`a90040fa53a4830ea894905a53375501918e766b5d150dfde80440104e1c4fbe`.
Andere Binaries werden abgewiesen, statt ihnen dieselbe Herkunft zuzuschreiben.

Nach Rahmen-Transformation und Deckungsprüfung schreibt der Import temporäres Float32-PLY
in Paketachsen, führt `build-lod --quality --max-sh=3 --rad-chunked` aus und prüft die Ausgabe.
Die Ebene enthält `model-lod.rad` und alle `model-lod-N.radc`; Herkunft, LoD-Methode,
Chunkanzahl und Gesamtbytes stehen im Manifest/Bericht. Bericht enthält auch Buildzeit,
Hauptdatei-/ersten Chunkbytes und LoD-Gesamtanzahl. `buildSeconds` umfasst die
Werkzeugausführung samt anschließendem vollständigem Positionsaudit, ohne PLY-Zwischenschreiben. Keine Entfernung von Blättern ist erlaubt.
Nur RAD-Blätter (`child_count == 0`) werden als Float32-Positionsmultimenge einschließlich
Vielfachheiten geprüft. Toleranz: Norm der halben Float32-ULP je lokaler Achse plus 10 nm.
Danach erneut `frameCheck` gegen die Wolke, mit derselben Opacitymaske in zugeordneter Reihenfolge.
LoD-Eltern sind ausschließlich Sichtdaten, niemals Mess-/Fangpunkte.

Viewer: RAD wird mit `paged: true` geladen, ohne Vollpuffer. Desktop: 500.000 gerenderte
Gaussians, 1.048.576 Pagerplätze; Touch/≤4 GB: 100.000 und 131.072. Bei SPZ/PLY bleiben
256/64 MiB (Datei plus 192 Byte/Gaussian + 48 Byte je SH-Grad) und das 1-Millionen-Gate.
Das sind Budgetvorgaben, keine Messwerte des Grafikspeichers.

Synthetischer Belastungskörper (Daten außerhalb des Repos, ≤65.536 Punkte im Generatorblock):

```sh
cd import
uv run --frozen --no-sync python tests/measure_large_rad.py /tmp/synthetic-rad-10m \
  --build-lod /pfad/build-lod
cd ../web
# Vite separat starten; Browserlauf außerhalb der Port-Sandbox.
SCANVIEW_URL=http://127.0.0.1:5173 node e2e/measure-splats.mjs /tmp/synthetic-rad-10m/pkg
```

Der Import-Messläufer erzeugt das synthetische Paket mit Referenzwolke und metrischem Rahmen.
`--spacing` setzt Rasterabstand und passende Gauß-Skalen; Standard bleibt 0,1 m.
Der 10-Mio.-Prüflauf nutzt 0,001 m und zehn Höhenlagen, damit die Wolkenstichprobe den Rahmen
belegen kann. Kompression/Laufzeit sind keine Schätzung für echte Trainingsdaten.
Der Browserläufer übernimmt den geprüften Manifest-Eintrag, liefert RAD/RADC einschließlich
Range-Antworten aus dem lokalen Testpaket und startet selbst keinen Listener.
Er meldet den ersten `after-render` mit gefülltem Pager und prüft unabhängig eine Canvasänderung
bei derselben Kamera durch Aus-/Einblenden. Übertragene Datei-Bytes bis zum Observer-Aufruf
sind eine obere Grenze (HTTP-Overhead nicht enthalten), alle beteiligten Chunks werden genannt.
121 RAF-Stichproben liefern Scheduler-FPS mit tatsächlichem Framezähler separat, JS-Heap
falls verfügbar und Renderername. WebGL liefert keine verlässlichen GPU-Bytes (`null`).
Browser- und schwache-GPU-Abnahme müssen außerhalb der Sandbox erfolgen.
