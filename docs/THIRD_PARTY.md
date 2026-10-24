# Drittkomponenten

## import/ (Python)

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| pye57 | 0.4.19 | MIT | Python-Import, bindet libE57Format (Boost-1.0) |
| numpy | 2.5.3 | BSD-3-Clause (Teilkomponenten: 0BSD, MIT, Zlib, CC0-1.0) | Python-Import |
| Pillow | 13.0.0 | MIT-CMU (HPND) | Python-Import |
| laspy | 2.7.0 | BSD-2-Clause | Python-Import |
| lazrs | 0.8.2 | MIT | Python-Import, laspy-Extra `[lazrs]` (LAZ-Codec) |
| pyproj | 3.8.0 | MIT (Wheel bündelt PROJ 9.8 MIT, libtiff libtiff, SQLite Public Domain, curl curl, zlib Zlib) | Python-Import, Höhenumrechnung `hoehen.py` (explizite PROJ-Pipeline, kein Netzzugriff) |
| certifi | 2026.10.19 | MPL-2.0 | transitive Abhängigkeit von pyproj und requests, unverändert |
| GCG2016 (`de_bkg_gcg2016.tif`, aus PROJ-data) | 2016, sha256 `598f1832…f7ac` | CC-BY-4.0, © Bundesamt für Kartographie und Geodäsie | Datendatei in `import/src/scanview_import/gitter/`, Quasigeoid für ellipsoidisch ↔ DHHN2016 |
| pyquaternion | 0.9.9 | MIT | Python-Import, transitive Abhängigkeit von pye57 |
| PDAL | 2.10.2 (Docker `pdal/pdal:2.10.2@sha256:8e0eb32d…2883`, per Digest gepinnt in `pdal_runner.IMAGE`) | BSD | **externer Prozess**, nicht gelinkt |
| untwine | 1.5.1 (conda-forge, im Image `scanview-untwine`) | GPL-3.0 | **externer Prozess** (Docker), nicht gelinkt; nur Modus `staged` (LAZ → COPC) |
| scanview-untwine (Image) | `condaforge/miniforge3@sha256:3a41fcca…1901` (per Digest gepinnt) + conda-forge `untwine=1.5.1`, gebaut aus `import/docker/untwine.Dockerfile` | Basis BSD-3-Clause (Miniforge), Inhalt s. untwine | lokal gebautes Werkzeug-Image, **externer Prozess**, wird nicht verteilt |
| pygltflib | 1.16.5 | MIT | **Laufzeitabhängigkeit von `scanview-import`** (`import/pyproject.toml`, direkte `dependencies`, kein Spike-/Dev-Extra) — glb/glTF lesen/schreiben; aktuell genutzt vom Spike-Vertragsprüfer `import/scripts/spike/tiles_contract.py`, der in Teil 5 nach `scanview_import` wechselt |
| pvlib | 0.16.1 | BSD-3-Clause | Extra `[sonne]` von `scanview-import` (`scanview-sonne`): Sonnenstand, Transposition, Ertragsmodell |
| meshoptimizer | 0.2.30a0 | MIT (Klassifikator „OSI Approved :: MIT License“; bindet die meshoptimizer-Bibliothek, MIT) | Laufzeitabhängigkeit von `scanview-import` (Mesh-Kompression); nur als Quellpaket auf PyPI, auf dem Host ohne root aus dem per Hash gelockten Quellpaket gebaut (`nachweise/nur-sdist.toml`) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | transitive Laufzeitabhängigkeit der Import-Umgebung (`import/uv.lock`, ohne Entwicklungswerkzeuge) |
| embreex | 4.4.0 | BSD-2-Clause AND Apache-2.0 — festgestellt aus dem Lizenztext im Wheel (Metadaten ohne Lizenzangabe; Feststellung in `nachweise/lizenzen.toml`); Wheel bündelt Embree 4 und oneTBB, beide Apache-2.0 | Extra `[sonne]`: Raycast |
| pandas | 3.0.6 | BSD-3-Clause | Extra `[sonne]`, Abhängigkeit von pvlib |
| h5py | 3.16.0 | BSD-3-Clause (Wheel bündelt HDF5 BSD-3-Clause-artig, libaec BSD-2-Clause) | Extra `[sonne]`, Abhängigkeit von pvlib, nicht genutzt |
| requests | 2.34.2 | Apache-2.0 | Extra `[sonne]`, Abhängigkeit von pvlib; der PVGIS-Abruf nutzt `urllib` der Standardbibliothek |
| urllib3 | 2.8.0 | MIT | Extra `[sonne]`, Abhängigkeit von requests |
| charset-normalizer | 3.5.2 | MIT | Extra `[sonne]`, Abhängigkeit von requests |
| idna | 3.20 | BSD-3-Clause | Extra `[sonne]`, Abhängigkeit von requests |
| python-dateutil | 2.9.0.post0 | Apache-2.0 OR BSD-3-Clause | Extra `[sonne]`, Abhängigkeit von pandas |
| six | 1.17.0 | MIT | Extra `[sonne]`, Abhängigkeit von python-dateutil |
| pytz | 2026.5 | MIT | Extra `[sonne]`, Abhängigkeit von pandas |
| tzdata | 2026.5 | Apache-2.0 | Extra `[sonne]`, Abhängigkeit von pandas |
| dataclasses-json | 0.6.7 | MIT | transitive Abhängigkeit von pygltflib |
| marshmallow | 3.26.2 | MIT | transitive Abhängigkeit von pygltflib → dataclasses-json |
| typing-inspect | 0.9.0 | MIT | transitive Abhängigkeit von pygltflib → dataclasses-json |
| mypy-extensions | 1.1.0 | MIT | transitive Laufzeitabhängigkeit von pygltflib → dataclasses-json → typing-inspect (bisher nur Dev-Gruppe über mypy, siehe Absatz unten) |
| typing-extensions | 4.16.0 | PSF-2.0 | transitive Laufzeitabhängigkeit von pygltflib → dataclasses-json → typing-inspect (bisher nur Dev-Gruppe über mypy, siehe Absatz unten) |
| Deprecated | 3.0.0 | MIT | transitive Abhängigkeit von pygltflib |
| wrapt | 2.5.0 | BSD-2-Clause | transitive Abhängigkeit von pygltflib → Deprecated |
| scipy | 1.18.1 | BSD-3-Clause (Kernpaket); Linux-Wheel bündelt zusätzlich OpenBLAS (BSD-3-Clause), LAPACK (BSD-3-Clause-artig) sowie GCC-Runtime `libgfortran*.so` (GPL-3.0-or-later **WITH GCC-Runtime-Library-Exception**) und `libquadmath` (LGPL-2.1-or-later) | Python-Import (`scipy.spatial.cKDTree` in `verify.py`) |

Versionen aus `import/uv.lock` nach `uv sync` (2026-09-25), Lizenzen per
`importlib.metadata.metadata(<paket>)` gegen die installierten Distributionen
verifiziert. `lazrs` ist entgegen der ursprünglichen Annahme MIT-lizenziert
(nicht Apache-2.0).

**pygltflib-Transitivbaum:** Mit `pygltflib` kamen sechs Pakete in `import/uv.lock`:
`pygltflib` selbst sowie `dataclasses-json`, `marshmallow`, `typing-inspect`, `deprecated`,
`wrapt` (transitiv, siehe Tabelle oben) — alle MIT oder BSD-2-Clause, per
`importlib.metadata.metadata(<paket>)` gegen die installierten Distributionen verifiziert
(`wrapt`: `License-Expression: BSD-2-Clause`, `Deprecated`: `License-Expression: MIT`, beide aus
`METADATA`, da das `License`-Feld von `importlib.metadata` bei diesen zwei Paketen leer bleibt).
`typing-inspect`s eigene transitive Abhängigkeiten `mypy-extensions` (1.1.0, MIT) und
`typing-extensions` (4.16.0, PSF-2.0) standen als Pakete schon vorher in `import/uv.lock`
(transitiv über `mypy`, Dev-Gruppe). Neu ist ihre **Laufzeitrolle**: `typing-inspect` ist selbst
eine Laufzeitabhängigkeit (über `pygltflib` → `dataclasses-json`) und zieht
`mypy-extensions`/`typing-extensions` damit in den Laufzeitpfad — deshalb stehen beide auch in der
Tabelle oben, mit derselben Kette und denselben, per `importlib.metadata.metadata(<paket>)`
verifizierten Lizenzen (`License-Expression: MIT` bzw. `License-Expression: PSF-2.0`, beide aus
`METADATA`, da `License` bei diesen zwei Paketen leer bleibt).

**Lizenzregeln (bindend):**

- **Lizenzregel: permissive Lizenzen außerhalb der wörtlichen Liste sind zulässig.** Die
  Allowlist der Projektvorgaben (MIT/BSD/Apache/ISC/LGPL/…) meint „keine Copyleft-Einbindung“,
  Ziel ist IP-Freiheit. Damit zulässig: `libE57Format` (Boost-1.0, als Binärteil von `pye57`
  importiert), `Pillow` (MIT-CMU/HPND), Teile von `numpy` (Zlib, CC0-1.0, 0BSD). Begründung: alle
  vier Lizenzen sind permissiv, verlangen nur Erhalt von Copyright-/Lizenzhinweis und haben keine
  Copyleft-Wirkung auf scanview-Code.
- **Lizenzregel: scipy-Wheel mit `libgfortran` ist zulässig.** scipy selbst ist BSD-3-Clause.
  Das `manylinux`-Wheel bündelt vorkompilierte GCC-Laufzeitbibliotheken: `libgfortran` unter
  GPL-3.0-or-later **mit GCC Runtime Library Exception** und `libquadmath` unter LGPL-2.1-or-later.
  Begründung: Die Runtime-Exception erlaubt ausdrücklich das Bündeln/Verlinken mit
  nicht-GPL-Software ohne Copyleft auf das Gesamtwerk; LGPL steht auf der Liste. Betroffen ist nur
  das Import-Werkzeug (`verify.py`, `scipy.spatial.cKDTree`), nicht das Web-Bundle. Bei Weitergabe
  des Import-Werkzeugs samt Umgebung gehören die Lizenztexte aus dem Wheel dazu (gesammelt in
  `scipy-1.18.1.dist-info/LICENSE.txt`, inkl. `libgfortran` mit Runtime-Exception).
- Kein Paket wird gegen GPL-Code ohne Ausnahme gelinkt; GPL-Werkzeuge (untwine, busybox im
  nginx-Image) laufen ausschließlich als externe Prozesse in Docker.

## web/ (Browser)

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| @giro3d/giro3d | 2.0.4 | MIT | npm-Abhängigkeit, Viewer-Kern (Punktwolken-Rendering) |
| three | 0.180.0 | MIT | npm-Abhängigkeit, Peer von Giro3D (WebGL), GLTFLoader und MeshoptDecoder |
| BinomialLLC/basis_universal (Basis Universal, Transcoder als Eigenbau) | 1.50.0 | Apache-2.0 AND BSD-3-Clause | KTX2-Transcoder, aus Commit `051ad6d8` (Tag v1_50_0, wie three 0.180.0) ohne dynamische Codeerzeugung selbst gebaut (`web/vendor/basis/`, SBOM-Erklärung `nachweise/eigenbau.toml`), lokal als JavaScript/Wasm unter wasm/basis ausgeliefert; BSD-3-Clause für das enthaltene Zstandard (gewählt aus „BSD-3-Clause OR GPL-2.0-only“) |
| facebook/zstd (Zstandard, im Basis-Transcoder enthalten) | 1.5.7 | BSD-3-Clause | nur der Decoder `zstd/zstddeclib.c`, von `web/vendor/basis/bauen.sh` aus zstd v1.5.7 (Commit `f8745da6`) erzeugt und statt des in basis_universal mitgelieferten 1.4.9 mitgebaut; gewählt aus „BSD-3-Clause OR GPL-2.0-only“, Text `web/licenses/zstd-BSD-3-Clause.txt` |
| proj4 | 2.22.0 | MIT | npm-Abhängigkeit, CRS-Umrechnung |
| ol (OpenLayers) | 10.10.0 | BSD-2-Clause | npm-Abhängigkeit, Peer von Giro3D (Kartenkomponenten) |
| lil-gui | 0.17.0 | MIT | npm-Abhängigkeit, Debug-/Steuer-UI |
| @sparkjsdev/spark | 2.3.0 | MIT | npm-Abhängigkeit, Darstellung der Gaussian-Splat-Sichtschicht (SPZ/PLY/RAD) |
| @mediapipe/tasks-vision | 1.0.1 | Apache-2.0 | npm-Abhängigkeit, Gesichtserkennung für den Fenster-Effekt (F32), erst beim Einschalten geladen; Wasm aus dem Paket lokal unter wasm/mediapipe ausgeliefert |
| MediaPipe Face Landmarker (Modell `face_landmarker.task`, float16/1) | 1 | Apache-2.0 | Modelldatei von Google (storage.googleapis.com/mediapipe-models), unverändert in `web/vendor/mediapipe/` mit SHA-256 (README dort), lokal unter wasm/mediapipe ausgeliefert |
| typescript | 5.9.3 | Apache-2.0 | Dev-Abhängigkeit (Build/Typecheck), nicht im Browser-Bundle |
| vite | 6.4.3 | MIT | Dev-Abhängigkeit (Build/Dev-Server), nicht im Browser-Bundle |
| vitest | 2.1.9 | MIT | Dev-Abhängigkeit (Tests), nicht im Browser-Bundle |
| @types/three | 0.180.0 | MIT | Dev-Abhängigkeit (Typdeklarationen) |
| @types/proj4 | 2.5.6 | MIT | Dev-Abhängigkeit (Typdeklarationen) |
| eslint-plugin-playwright | 2.12.0 | MIT | Dev-Abhängigkeit (E2E-Linter); Mindestalter wie übrige npm-Pakete |
| eslint | 9.39.5 | MIT | Dev-Abhängigkeit (Linter) |
| typescript-eslint | 8.70.1 | MIT | Dev-Abhängigkeit (Linter-Regeln für TS) |
| @playwright/test | 1.63.0 | Apache-2.0 | Dev-Abhängigkeit (E2E-Tests) |
| @axe-core/playwright | 4.13.0 | MPL-2.0 | Dev-Abhängigkeit (Barrierefreiheitsprüfung des Portals; Prüfsuite nicht im öffentlichen Stand); nicht im Bundle |
| axe-core | 4.13.0 | MPL-2.0 | Dev-Abhängigkeit (Prüfregeln für @axe-core/playwright); nicht im Bundle, unverändert (Lizenzregel F10) |
| @types/node | 22.20.4 | MIT | Dev-Abhängigkeit (Typdeklarationen für die Playwright-E2E-Tests) |

Versionen und Lizenzen aus den installierten `package.json`-Dateien unter
`web/node_modules/` nach `npm install` (2026-09-25, `web/package-lock.json`
committet) ausgelesen — Versionsangaben in `package.json` sind Bereichs-/Mindestangaben
(`^`/`~`); hier stehen die tatsächlich aufgelösten Versionen. `@giro3d/giro3d`
hat kein `exports`-Feld in seiner `package.json`; der tiefe Unterpfad-Import
`@giro3d/giro3d/core/geographic/CoordinateSystem.js` in `crsGiro3d.ts` löst
daher regulär gegen `main`-basierte Dateipfade auf.

**Vite-Versionsspaltung:** `vite@6.4.3` (Root, für `dev`/`build`) und ein
verschachteltes `vite@5.4.21` unter `vitest@2.1.9` (`vite-node`,
`@vitest/mocker`) koexistieren, weil `vitest ^2.1.0` und
`vite ^6.0.0` sich als Peers nicht vollständig vertragen; npm
hat das per Nesting statt per ERESOLVE-Fehler aufgelöst. Beide sind MIT.

**Transitiver Baum, korrekt geprüft (Nachtrag, ersetzt die erste — fehlerhafte
— Fassung dieses Commits):** Der erste Scan lief nur über
`node_modules/*` und `node_modules/@scope/*` und übersah verschachtelte
Pakete (u. a. das oben genannte `vite@5.4.21`); er fand dadurch 196 statt der
tatsächlich 213 von `npm ls --all --parseable` aufgelösten Knoten (47 davon
im Produktions-Teilbaum, `npm ls --all --omit=dev --parseable`), und die
damalige Lizenzliste war das Prüf-Allowlist-Muster, nicht das tatsächliche
Scan-Ergebnis. Korrigierter Befund (Lizenzfeld jeder `package.json`,
dedupliziert nach Name@Version):

- **Produktions-Teilbaum (46 Pakete unter den 5 direkten `dependencies`):**
  ausschließlich MIT, BSD-2/3-Clause und Apache-2.0 — bis auf eine
  Ausnahme: `pako@2.2.0` (transitiv über `geotiff`, das sowohl `@giro3d/giro3d`
  als auch `ol` einbinden) trägt die SPDX-Zeichenkette `(MIT AND Zlib)`. MIT
  und Zlib stehen beide einzeln auf der freigegebenen Liste der Binding
  Constraints — die Klammer-Schreibweise ließ ein naives Präfix-Muster im
  ersten Scan-Versuch fälschlich anschlagen, inhaltlich ist das unauffällig.
- **Nur im Dev-Teilbaum (Build/Test/Lint, nicht im Browser-Bundle):**
  zusätzlich ISC, 0BSD, CC0-1.0, sowie zwei Funde außerhalb der Binding-
  Constraints-Liste (MIT/BSD/Apache/ISC/LGPL/HPND/Zlib/CC0/Boost):
  `minimatch@10.2.6` (BlueOak-1.0.0, transitiv über
  `typescript-eslint@8.70.1` → `@typescript-eslint/typescript-estree`) und
  `argparse@2.0.1` (Python-2.0, transitiv über `eslint@9.39.5` → `js-yaml`).
  Beide sind reine Build-/Lint-Zeit-Werkzeuge, werden nicht mitverteilt und
  laufen nie im ausgelieferten Browser-Bundle. **Lizenzregel:** beide Lizenzen
  sind permissiv und nicht im ausgelieferten Bundle — zulässig.
- Kein GPL/AGPL im gesamten Baum (Prod + Dev).

`npm audit` (Stand nach `npm install`, ungeändert): 5 Findings (3 moderate,
1 high, 1 critical), alle in `esbuild`/`vite@5.4.21`/`@vitest/mocker`
innerhalb des oben beschriebenen verschachtelten Vitest-Dev-Server-Pfads
(bekannte Klasse „Dev-Server beantwortet beliebige Origin-Requests",
GHSA-67mh-4wv8-2f99 u. a.). `npm audit --omit=dev` liefert **keine**
Findings — bestätigt, dass ausschließlich der Dev-/Test-Pfad betroffen ist,
nicht der Produktions-Teilbaum. Ein Fix (`npm audit fix --force`) würde
`vitest` auf Major-Version 5 heben und damit von der festgelegten
Version (`^2.1.0`) abweichen — nicht durchgeführt.

### Ausgeliefert im Viewer

Neue direkte `dependency`: `laz-perf` (exakt gepinnt auf `0.0.7`, die von `@giro3d/giro3d` →
`copc` ohnehin aufgelöste Version, `npm ls laz-perf` zeigt sie als dedupliziert). Grund:
`web/vite.config.ts` liest `laz-perf/lib/laz-perf.wasm` direkt per `require.resolve` — das darf
nicht vom Hoisting einer transitiven Abhängigkeit abhängen. `copc` bleibt transitiv, wird aber
ausgeliefert und deshalb einzeln aufgeführt:

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| laz-perf | 0.0.7 | Apache-2.0 | direkte npm-Abhängigkeit (gepinnt, identisch mit der transitiven über `@giro3d/giro3d` → `copc`); LAZ-Decoder (WebAssembly). `laz-perf.wasm` wird **selbst ausgeliefert** (`/wasm/laz-perf.wasm`, Vite-Plugin in `web/vite.config.ts`, beim Build aus `node_modules` nach `dist/wasm/` kopiert) statt wie in Giro3D voreingestellt von `cdn.jsdelivr.net` geladen |
| copc | 0.0.8 | MIT | transitiv über `@giro3d/giro3d`; COPC-Leser (Hierarchie, Range-Zugriffe) im JS-Bundle |

Versionen und Lizenzen aus `web/node_modules/laz-perf/package.json` und
`web/node_modules/copc/package.json` (2026-09-26). Für die Sichtprüfung lädt
`npx playwright install chromium` zusätzlich Chrome Headless Shell (Playwright-Build
1243) — reines Test-Werkzeug im Nutzer-Cache, nicht im Repo und nicht ausgeliefert.
Für die Barrierefreiheitsprüfung der axe-Matrix unter einer zweiten Engine lädt
`npx playwright install firefox` zusätzlich Firefox 155.0 (Playwright-Build 1543, MPL-2.0) —
ebenfalls reines Test-Werkzeug im Nutzer-Cache, nicht im Repo und nicht ausgeliefert
(Firefox-Lauf der Barrierefreiheitsprüfung; die Prüfsuite liegt nicht im öffentlichen Stand).

### Vollständiger Produktions-Teilbaum (SBOM, Release-Kette)

Alle npm-Komponenten, die im ausgelieferten Web-Bundle landen, mit der Lizenz laut SBOM
(`package.json` des Pakets). Die Release-Kette prüft je Release, dass jede Komponente des SBOMs
hier mit Name und Version steht (`scanview-nachweise`, Stufe „lizenzen“). Die bereits oben
genannten direkten Abhängigkeiten sind nicht wiederholt.

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| @mapbox/point-geometry | 1.1.0 | ISC | transitiv, im Bundle |
| @mapbox/vector-tile | 2.0.5 | BSD-3-Clause | transitiv, im Bundle |
| @petamoriken/float16 | 3.9.3 | MIT | transitiv, im Bundle |
| @types/geojson | 7946.0.16 | MIT | transitiv (Typdeklarationen, ohne Laufzeitcode) |
| @types/rbush | 4.0.0 | MIT | transitiv (Typdeklarationen, ohne Laufzeitcode) |
| @zarrita/storage | 0.2.0 | MIT | transitiv, im Bundle |
| 3d-tiles-renderer | 0.4.28 | Apache-2.0 | transitiv, im Bundle |
| chart.js | 3.9.1 | MIT | transitiv, im Bundle |
| cross-fetch | 3.2.0 | MIT | transitiv, im Bundle |
| earcut | 2.2.4 | ISC | transitiv, im Bundle |
| earcut | 3.2.3 | ISC | transitiv, verschachtelt unter `ol` |
| fflate | 0.8.3 | MIT | transitiv, im Bundle |
| geotiff | 3.0.5 | MIT | transitiv, im Bundle |
| geotiff | 3.1.0-beta.0 | MIT | transitiv, verschachtelt unter `ol` |
| lerc | 3.0.0 | Apache-2.0 | transitiv, im Bundle |
| lru-cache | 10.4.3 | ISC | transitiv, im Bundle |
| mgrs | 1.0.0 | MIT | transitiv, im Bundle |
| node-fetch | 2.7.0 | MIT | transitiv, im Bundle |
| numcodecs | 0.3.2 | MIT | transitiv, im Bundle |
| pako | 2.2.0 | MIT AND Zlib | transitiv, im Bundle |
| parse-headers | 2.0.6 | MIT | transitiv, im Bundle |
| pbf | 4.0.2 | BSD-3-Clause | transitiv, im Bundle |
| pbf | 5.1.2 | BSD-3-Clause | transitiv, verschachtelt unter `ol` |
| pmtiles | 4.5.0 | BSD-3-Clause | transitiv, im Bundle |
| protocol-buffers-schema | 3.6.1 | MIT | transitiv, im Bundle |
| quick-lru | 6.1.2 | MIT | transitiv, im Bundle |
| quickselect | 3.0.0 | ISC | transitiv, im Bundle |
| rbush | 4.0.1 | MIT | transitiv, im Bundle |
| reference-spec-reader | 0.2.0 | MIT | transitiv, im Bundle |
| resolve-protobuf-schema | 2.1.0 | MIT | transitiv, im Bundle |
| tr46 | 0.0.3 | MIT | transitiv, im Bundle |
| unzipit | 2.0.0 | MIT | transitiv, im Bundle |
| web-worker | 1.5.0 | Apache-2.0 | transitiv, im Bundle |
| webidl-conversions | 3.0.1 | BSD-2-Clause | transitiv, im Bundle |
| whatwg-url | 5.0.0 | MIT | transitiv, im Bundle |
| wkt-parser | 1.5.6 | MIT | transitiv, im Bundle |
| xml-utils | 1.10.2 | CC0-1.0 | transitiv, im Bundle |
| zarrita | 0.7.5 | MIT | transitiv, im Bundle |
| zstddec | 0.2.0 | MIT AND BSD-3-Clause | transitiv, im Bundle |

### Lizenzhinweise bei Weitergabe von `web/dist/`

Wer `web/dist/` weitergibt (z. B. an einen Kunden zum Selbstbetrieb), verteilt die gebündelten Pakete
weiter. MIT, BSD, ISC und Apache-2.0 verlangen dabei den Lizenztext, Apache-2.0 zusätzlich eine
vorhandene NOTICE-Datei. Dafür erzeugt `npm run build` automatisch
**`dist/THIRD_PARTY_LICENSES.txt`** (Vite-Schritt `scanview-third-party-licenses` in
`web/vite.config.ts`, keine zusätzliche Abhängigkeit): Name, Version, Lizenzfeld und alle
`LICENSE*`/`LICENCE*`/`COPYING*`/`NOTICE*`-Dateien jedes npm-Pakets, dessen Module im Bundle
landen, plus `laz-perf` (die `.wasm`-Datei) und Basis Universal mit Zstandard (lokaler KTX2-Transcoder,
Eigenbau).
`laz-perf@0.0.7` bringt keine Lizenzdatei mit; für
Apache-2.0-Pakete ohne eigene Datei legt der Schritt den Standardtext bei, einschließlich
Basis Universal. Der mit three gelieferte MeshoptDecoder stammt aus meshoptimizer 0.22
(MIT, Copyright 2016–2024 Arseny Kapoulkine); Lizenztext und Copyright aus
`web/licenses/meshoptimizer-MIT.txt` werden in die Build-Lizenzdatei aufgenommen. Stand Build
26.09.2026: 11 Pakete — `@giro3d/giro3d`, `copc`, `cross-fetch`, `laz-perf`, `lil-gui`,
`lru-cache` (ISC), `mgrs`, `ol` (BSD-2-Clause), `proj4`, `three`, `wkt-parser`. Diese Datei muss
mit `dist/` ausgeliefert werden; sie liegt im Deploy unter `/THIRD_PARTY_LICENSES.txt`.

## server/ (Python, Plattform-Server)

Laufzeit-Abhängigkeiten des Plattform-Servers (`server/uv.lock`, ohne Entwicklungsgruppe), Lizenz
laut SBOM (PyPI-Metadaten). Gemeinsame Pakete mit dem MCP-Server (anyio, cffi, click,
cryptography, h11, idna, pycparser, pydantic, starlette, typing-*, uvicorn) stehen dort.

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| fastapi | 0.141.1 | MIT | Python-Import, direkte Abhängigkeit (HTTP-API) |
| annotated-doc | 0.0.5 | MIT | transitiv (fastapi) |
| argon2-cffi | 25.1.0 | MIT | Python-Import, direkte Abhängigkeit (Passwort-Hashes) |
| argon2-cffi-bindings | 26.1.0 | MIT | transitiv (argon2-cffi) |
| webauthn | 3.0.0 | BSD-3-Clause | Python-Import, direkte Abhängigkeit (Passkeys) |
| cbor2 | 6.1.4 | MIT | transitiv (webauthn) |
| pyOpenSSL | 26.4.0 | Apache-2.0 (Trove-Klassifikator) | transitiv (webauthn) |
| pyasn1 | 0.6.4 | BSD-2-Clause | transitiv (webauthn) |
| pyasn1_modules | 0.4.2 | BSD (Trove-Klassifikator „BSD License“) | transitiv (webauthn) |

## deploy/

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| nginx | 1.27 (Docker-Image `nginx:1.27-alpine`, Tag gepinnt) | nginx-eigene Lizenz (2-Clause-BSD-artig) | **externer Prozess** (Docker), liefert `web/dist` und die Punktwolken-Pakete statisch aus, nicht gelinkt, nicht verteilt |

Analog zum `PDAL`/`untwine`-Fall oben, zur Vollständigkeit: Das Basis-Image
`nginx:1.27-alpine` bringt (Alpine-typisch) auch `busybox` mit, das unter
GPL-2.0 steht. Auch das läuft ausschließlich als externer Prozess im
Container — es wird nichts davon in scanview-eigenen Code eingebunden oder
mit ihm verlinkt, und das Image wird nicht weiterverteilt (nur lokal per
`docker compose` gestartet). Kein neuer npm-/Python-Paketeintrag nötig.

## mcp-server/ (Python)

Lokaler MCP-Server (`scanview-mcp`, stdio), läuft beim Nutzer. Laufzeit-Abhängigkeiten
(`uv sync --locked --no-dev`, Linux):

| Komponente | Version | Lizenz | Einbindung |
|---|---|---|---|
| mcp (MCP Python SDK) | 2.2.0 | MIT | Python-Import, direkte Abhängigkeit |
| httpx | 0.28.1 | BSD-3-Clause | Python-Import, direkte Abhängigkeit (Client für die API v1) |
| mcp-types | 2.2.0 | MIT | transitiv (mcp) |
| httpx2 / httpcore2 | 2.13.0 / 2.13.0 | BSD-3-Clause | transitiv (mcp, eigener HTTP-Stapel des SDK) |
| httpcore | 1.0.9 | BSD-3-Clause | transitiv (httpx) |
| truststore | 0.10.4 | MIT | transitiv (httpx2) |
| anyio | 4.15.1 | MIT | transitiv |
| h11 | 0.16.0 | MIT | transitiv |
| idna | 3.20 | BSD-3-Clause | transitiv |
| certifi | 2026.10.19 | MPL-2.0 | transitiv (httpx), unverändert |
| pydantic / pydantic-core | 2.13.5 / 2.46.5 | MIT | transitiv (mcp) |
| annotated-types | 0.8.0 | MIT | transitiv |
| typing-extensions | 4.16.0 | PSF-2.0 | transitiv |
| typing-inspection | 0.4.4 | MIT | transitiv |
| jsonschema / jsonschema-specifications | 4.26.0 / 2025.9.1 | MIT | transitiv (mcp) |
| referencing / rpds-py / attrs | 0.37.0 / 2026.6.3 / 26.1.0 | MIT | transitiv (jsonschema) |
| opentelemetry-api | 1.44.0 | Apache-2.0 | transitiv (mcp) |
| pyjwt (Extra `crypto`) | 2.15.0 | MIT | transitiv (mcp) |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause (Wheel bündelt OpenSSL, Apache-2.0) | transitiv (pyjwt) |
| cffi / pycparser | 2.1.1 / 3.0 | MIT-0 / BSD-3-Clause | transitiv (cryptography) |
| python-multipart | 0.0.32 | Apache-2.0 | transitiv (mcp) |
| sse-starlette / starlette | 3.4.11 / 1.6.0 | BSD-3-Clause | transitiv (mcp, für dessen HTTP-Transporte) |
| uvicorn / click | 0.53.0 / 8.5.0 | BSD-3-Clause | transitiv (mcp) |
| colorama | 0.4.6 | BSD-3-Clause | nur unter Windows (click) |
| pywin32 | 312 | PSF-2.0 | nur unter Windows (mcp) |
| httpx2-jsfetch | 1.0 | BSD-3-Clause | nur unter Emscripten (httpx2) |

Entwicklung (nicht ausgeliefert): pytest 9.1.1 (MIT), ruff 0.16.8 (MIT), mypy 2.3.1 (MIT) mit
mypy-extensions/librt/ast-serialize (MIT), pathspec (MPL-2.0), pluggy/iniconfig (MIT),
packaging (Apache-2.0 OR BSD-2-Clause), pygments (BSD-2-Clause); Build: hatchling 1.32.3 (MIT).

Versionen aus `mcp-server/uv.lock` (Stichtag `exclude-newer = 2026-09-20T00:00:00Z`, mcp 2.2.0
erschien 2026-09-07), Lizenzen per `importlib.metadata` gegen die installierten Distributionen
(`License-Expression`/`License`/Classifier) geprüft, die drei nur unter Windows/Emscripten
gezogenen Pakete per PyPI-Metadaten (27.09.2026). **Alle permissiv bzw. MPL-2.0 (F10-verträglich),
kein GPL/AGPL.** Der MCP-Server wird nicht als Binärpaket verteilt; wer ihn installiert, bezieht
die Pakete selbst von PyPI.

## clients/sitebits/ (TypeScript, API-Client)

Client für die API v1 (`sitebits`), läuft beim Nutzer (Node ≥ 22.18, Deno, Browser). **Keine
Laufzeit-Abhängigkeiten** (`dependencies` leer): er nutzt nur das eingebaute `fetch`, die Typen
sind erzeugter Quelltext (`src/schema.d.ts`). Nicht Teil der Release-Artefakte (web, server,
mcp, import).

Entwicklung (nicht ausgeliefert, exakt gepinnt in `clients/sitebits/package.json` und
`package-lock.json`, `.npmrc` mit denselben Lieferkettenregeln wie `web/`: Mindestalter 7 Tage,
keine Install-Skripte):

| Komponente | Version | Lizenz | Zweck |
|---|---|---|---|
| openapi-typescript | 7.13.0 | MIT | TypeScript-Typen aus `docs/public/api/openapi.json` (`npm run typen`, Drift-Test) |
| @redocly/openapi-core | 1.34.21 | MIT | transitiv (openapi-typescript), Einlesen der OpenAPI-Datei |
| typescript | 5.9.3 | Apache-2.0 | Typprüfung (`tsc --noEmit`), wie in `web/` |
| eslint / typescript-eslint | 9.39.5 / 8.70.1 | MIT | Lint, wie in `web/` |
| @types/node | 22.20.4 | MIT | Typen für Tests und Beispiele |

Transitiv insgesamt 136 Pakete, Lizenzen laut `package-lock.json` (22.10.2026): MIT, ISC,
Apache-2.0, BSD-2-Clause, BSD-3-Clause, dazu `argparse` 2.0.1 (Python-2.0), `minimatch` 10.2.6
(BlueOak-1.0.0, über typescript-eslint) und `type-fest` 4.41.0 (MIT OR CC0-1.0). **Alle in der
Positivliste `nachweise/lizenzen.toml`, kein Copyleft.** Tests laufen mit dem eingebauten
`node:test`, ohne Testframework.

## Import-Spike Kachler (externe Werkzeuge, nur Docker)

Diese Werkzeuge ruft der Kachler-Spike (`import/scripts/spike/`, Ergebnis und Entscheidung:
`docs/entscheidungen/0003-eigener-kachler.md`) per Docker als eigenständige Prozesse auf. **Kein
Code hieraus wird gelinkt oder importiert** — alles läuft ausschließlich als externer Prozess in
einem isolierten Container (Lizenzregel F10), ausschließlich im Import-Spike, nie im Browser, nie
als Netzdienst. Nach der Entscheidung für einen eigenen Kachler (ADR 0003) sind Obj2Tiles und
mago-3d-tiler **nicht Teil des Produktpfads**; sie bleiben nur als Spike-/Vergleichswerkzeuge
dokumentiert.

### Obj2Tiles

| Feld | Wert |
|---|---|
| Name | Obj2Tiles (OpenDroneMap/Obj2Tiles) |
| Version | 1.6.2 |
| Lizenz | AGPL-3.0 (siehe `LICENSE.md` im Release; verifiziert gegen den Tag `v1.6.2`) |
| Quelle | `https://github.com/OpenDroneMap/Obj2Tiles/releases/download/v1.6.2/Obj2Tiles-Linux64.zip` |
| sha256 (Asset-Zip) | `34a576e0b8ebbd73da5e2271d238724a9b39be3ee1edc167214b5b28bed2baa0` |
| Nutzung | unveränderter externer Prozess nur im Import (F10) — Docker-Image `scanview-spike/obj2tiles:1.6.2`, gebaut aus `import/docker/spike/obj2tiles.Dockerfile`, aufgerufen über `import/scripts/spike/run_tool.sh`. Binary + `libktx.so` unverändert aus dem Release-Zip nach `/opt/obj2tiles` kopiert (kein Patchen, kein Neu-Linken). |

**Bundled: libktx** — die Release-Binärdatei enthält `libktx.so` (KTX-Software v4.4.2,
Apache-2.0, laut Obj2Tiles-README per P/Invoke aus dem .NET-Binary genutzt für
`--texture-format Ktx2`). Der Spike nutzt das Standardformat `Jpeg` (kein
`--texture-format Ktx2`), die Datei wird aber unverändert mitkopiert, weil das Release-Zip
als Ganzes unangetastet bleibt („unverändert nach /opt/obj2tiles“).

**Basis-Images (Digest gepinnt, `import/docker/spike/obj2tiles.Dockerfile`):**

| Image | Digest |
|---|---|
| `debian:bookworm-slim` (Fetch-Stage: curl+unzip+sha256sum) | `sha256:3783cc01769c7b2b1b83a5c5ad96c815348e28ed7da68e2e3687004faa906251` |
| `mcr.microsoft.com/dotnet/runtime-deps:10.0` (Laufzeit) | `sha256:099f6f87ed745377dd27bd722f0d1a352bca71b4fddaabfd75e7c064bcaa82da` |

Version 10.0 statt der im Plan zunächst genannten 8.0: Das offizielle
Obj2Tiles-Dockerfile genau auf Tag `v1.6.2` (nicht `main`) baut mit `dotnet/sdk:10.0`
und läuft auf `dotnet/runtime-deps:10.0` — geprüft gegen
`raw.githubusercontent.com/OpenDroneMap/Obj2Tiles/v1.6.2/Dockerfile`, nicht angenommen.

### 3d-tiles-validator (K14)

| Feld | Wert |
|---|---|
| Name | 3d-tiles-validator (CesiumGS) |
| Version | 0.6.1 (npm, `dist-tags.latest` zum Zeitpunkt des Baus) |
| Lizenz | Apache-2.0 |
| Quelle | `https://registry.npmjs.org/3d-tiles-validator` |
| Nutzung | externer Prozess nur im Import (F10) — Docker-Image `scanview-spike/validator:0.6.1`, gebaut aus `import/docker/spike/validator.Dockerfile` (Node 22, Version zur Build-Zeit per `npm install --global` fest installiert, kein Laufzeit-Netzzugriff nötig). |

**Basis-Image (Digest gepinnt, `import/docker/spike/validator.Dockerfile`):**

| Image | Digest |
|---|---|
| `node:22-slim` | `sha256:43ac6c60b8f89723f746e8a92ce91abd5017e627ce1ddfe4238355d3a30b772c` |

### mago-3d-tiler

| Feld | Wert |
|---|---|
| Name | mago-3d-tiler (Gaia3D, Inc.) |
| Version | 1.16.3 |
| Lizenz | MPL-2.0 (verifiziert per `gh api repos/Gaia3D/mago-3d-tiler/license` — `license.spdx_id: MPL-2.0`) |
| Quelle | `https://hub.docker.com/r/gaia3d/mago-3d-tiler` (offizielles Docker-Image), Projekt `https://github.com/Gaia3D/mago-3d-tiler` |
| Upstream-Image-Digest | `gaia3d/mago-3d-tiler@sha256:6ec0c8bad0d1ced130a3a91ccf3f7473c9a0bae0978055d7569ab850c36ba022` (Tag `1.16.3` → Digest per `docker pull`/`docker image inspect --format '{{.RepoDigests}}'` ermittelt, nicht angenommen) |
| Nutzung | unveränderter externer Prozess nur im Import (F10) — Docker-Image `scanview-spike/mago:1.16.3`, gebaut aus `import/docker/spike/mago.Dockerfile` (FROM exakt diesem Upstream-Digest + `JAVA_TOOL_OPTIONS`-Env-Var, Begründung im Dockerfile-Kopfkommentar; kein Patchen der JAR-Datei), aufgerufen über `import/scripts/spike/run_tool.sh --rw-input` (mago verlangt laut eigenem Vorabcheck Schreibzugriff im Eingabeordner — deshalb `--rw-input` auf eine eigene Kopie unter `scanview-spike/input/hq-copy/`, niemals auf die Originalquelle). |

**Lokal gebautes Image (`scanview-spike/mago:1.16.3`, `import/docker/spike/mago.Dockerfile`):**
Id `sha256:35f14b4533253bb285d616632e8ed33c4c2988fb55a073f93cea1603c5ed8e6b` — dieser Wert
(nicht der Upstream-Digest) erscheint als `imageDigest` in den `run.json`-Dateien der echten
Läufe (`run_tool.sh` liest `docker image inspect <image> --format '{{.Id}}'` gegen das lokal
getaggte Image).

Zusammenfassung der Messläufe und der Entscheidung: `docs/entscheidungen/0003-eigener-kachler.md`.

## Mesh-Import Weg A

- **meshoptimizer Python 0.2.30a0**, MIT, Pin in `import/uv.lock`.
  Quelle: https://github.com/zeux/meshoptimizer, Lizenz des Quellpakets
  `LICENSE.md` geprüft (MIT). Attribute-Simplifier mit expliziter C-ABI-Deklaration,
  weil der Python-Wrapper diese Funktion in 0.2.30a0 nicht registriert.
- **KTX-Software 4.4.2**, Apache-2.0 einschließlich mitgelieferter Third-Party-
  Lizenzhinweise; unveränderter externer Docker-Prozess, kein Linken.
  Release: https://github.com/KhronosGroup/KTX-Software/releases/tag/v4.4.2.
  Debian-Asset SHA-256 `ca635ed489d8bf54fac8d7687056c651193de0740830a7738cc034adc63e3027`.
  Upstream `LICENSE.md` nennt zusätzlich `lib/etcdec.cxx` unter Ericsson-Lizenz;
  der Import verwendet Basis-Transcoding (`ktx extract`) und die UASTC/ETC1S-Encoder.
  Lizenzdateien bleiben im unveränderten Release-Paket enthalten.
- Basis: `python:3.12-slim@sha256:e41613d42d4891e4930f79523f93f81bbc7632584ec65e36ab055f41a800b41e`.
  Dockerfile `import/docker/meshtools.Dockerfile` prüft das Release-Asset.
  Lokales Laufzeit-Image per Inhalt gepinnt:
  `sha256:36a148dcd76049e9cf0a199324d144a9284f2444e6c7229a9745a0f2273ad3e2`.
  Ein Neubau muss bewusst geprüft und dessen neue Image-ID übernommen werden;
  kein automatischer Fallback auf einen beweglichen Tag.

## Gaussian-Sichtschicht

- `@sparkjsdev/spark` **2.3.0**, MIT, exakt in web/package.json und package-lock.json gepinnt.
  Peer: three ≥ 0.180.0, vorhandenes three ~0.180.0. Lizenztext: web/licenses/spark-MIT.txt,
  zusätzlich im vom Vite-Build erzeugten THIRD_PARTY_LICENSES.txt.
- Release 30.09.2026 (Changelog 29.09.), am 14.10. mindestens 14 Tage alt:
  https://github.com/sparkjsdev/spark/releases/tag/v2.3.0.
  Relevante Änderungen: Rendering auf Anforderung, Cookie-/Header-Unterstützung, Freigabe von
  GPU-Ressourcen und SPZ-Korrekturen. Keine andere neue direkte Abhängigkeit.
- Spark kompiliert eingebettetes WASM und startet Blob-Worker. Die bestehende CSP wird **nicht**
  angepasst. Die vorhandene Viewer-CSP (deploy/platform/scanview.caddy) erlaubt
  WASM-Kompilierung und Blob-Worker bereits. Bei abweichender/strengerer CSP bleibt
  die Sichtschicht gesperrt; Wolke/Mesh sind weiter erreichbar.
- SPZ v2/v3: eigenständiger Formatleser und nativer v3-Encoder nach der öffentlich dokumentierten Niantic-Referenz
  https://github.com/nianticlabs/spz, keine zusätzliche Laufzeitabhängigkeit und kein übernommener
  Quellcode. Standardausgabe SPZ, PLY als ausdrücklich gewählter Notweg.
- SH-Konvention: reelle 3DGS-Basis entsprechend
  https://github.com/graphdeco-inria/gaussian-splatting/blob/main/utils/sh_utils.py;
  eigene bandweise Rotation mittels vorhandener SciPy `sph_harm_y`/Least-Squares,
  kein Code kopiert und keine neue Abhängigkeit.
- Externes Importwerkzeug **build-lod**, Spark Tag **v2.3.0**, Commit
  `cce78e2cf5505679abbaf64236e0834de385010c`, MIT.
  Quelle: https://github.com/sparkjsdev/spark/tree/v2.3.0/rust/build-lod;
  Lizenz: https://github.com/sparkjsdev/spark/blob/v2.3.0/LICENSE (bereits web/licenses/spark-MIT.txt).
  Vorgebaut mit `cargo build --release --locked -p build-lod`, Rust 1.99 (bereitgestellt, nicht im Import gebaut).
  Freigegebener Binary-SHA256: `a90040fa53a4830ea894905a53375501918e766b5d150dfde80440104e1c4fbe`.
  Nur expliziter Pfad über Option/Umgebung, SHA-Prüfung vor Ausführung. Kein Binary vendort,
  kein zusätzlicher Paketmanager-/Netzzugriff; das Werkzeug läuft lokal ohne Shell.
  RAD-Kodierung und eigener Blattleser basieren auf `spark-lib/src/rad.rs` derselben Revision.
  Bericht/Manifest dokumentieren Herkunft und konkrete Binärdatei. LoD-Eltern bleiben Sichtdaten.
