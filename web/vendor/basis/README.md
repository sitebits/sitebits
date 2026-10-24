# Basis-Universal-Transcoder (eigener Bau ohne dynamische Codeerzeugung)

`basis_transcoder.js` und `basis_transcoder.wasm` transkodieren KTX2/UASTC-Mesh-Texturen
(Zstandard) im Browser in das GPU-Format des Geräts (BC7, ASTC, ETC2 …). Der Viewer lädt sie
über `KTX2Loader` aus `/wasm/basis/` (Plugin `scanview-basis-transcoder` in `vite.config.ts`).

**Warum ein eigener Bau:** Das Upstream-Build (dasselbe liegt in `three/examples/jsm/libs/basis/`)
erzeugt embind-Aufrufer zur Laufzeit per `Function()`. Unter der CSP von scanview
(`script-src 'self' 'wasm-unsafe-eval'`, kein `'unsafe-eval'`) scheitert das, und GLTFLoader fällt
still auf Vertexfarben zurück — Mesh-Texturen fehlten in 0.7.0 bis 0.8.1. Dieser Bau nutzt
`-sDYNAMIC_EXECUTION=0` und enthält weder `Function()` noch `eval`. Die CSP bleibt unverändert.
Entscheidung: Council 07.10.2026, Option (b).

| | |
|---|---|
| Quelle | https://github.com/BinomialLLC/basis_universal, Commit `051ad6d8a64bb95a79e8601c317055fd1782ad3e` (Tag `v1_50_0`, dieselbe Version wie three r180) |
| Toolchain | `emscripten/emsdk@sha256:2e5a1ae55cc0ef2d0c5d5c87deb4a74bea15c6ae03b2333d8aca02516671345b` (emsdk 3.1.67), Bau ohne Netz |
| Flags | wie `webgl/transcoder/CMakeLists.txt` (`--bind -O3 MODULARIZE EXPORT_NAME=BASIS emmalloc`, KTX2 + Zstandard) plus `-sDYNAMIC_EXECUTION=0` |
| SHA-256 `.js` | `6f2a04342d3c17c740859938ea678ece979b30ccc2ef89e9879b6af0619b25c1` |
| SHA-256 `.wasm` | `23cbe1a04eb8cdda925df80e19ee3dd6ab7a9ffa57afa5dcd183a9a3b402e01a` |
| Zstandard | Decoder von zstd **1.5.7** (https://github.com/facebook/zstd, Commit `f8745da6ff1ad1e7bab384bd1f9d742439278e99`, Tag `v1.5.7`) statt des mitgelieferten 1.4.9: `bauen.sh` erzeugt `zstd/zstddeclib.c` mit zstds eigenem `build/single_file_libs/combine.py` im selben Image und ersetzt damit (und mit `zstd.h`, `zstd_errors.h`) die Dateien in einer Kopie des Quellbaums |
| Lizenzen | Basis Universal Apache-2.0; enthaltenes Zstandard (`zstd/zstddeclib.c`) BSD-3-Clause (gewählt aus „BSD-3-Clause OR GPL-2.0-only“), Text `web/licenses/zstd-BSD-3-Clause.txt` (aus zstd v1.5.7) |

Nicht byte-gleich mit dem Upstream-Prebuilt (anderer emsdk-Stand, neuerer zstd-Decoder), aber
dieselbe basis_universal-Quelle und dieselbe JS-Schnittstelle (`BASIS`, `KTX2File`,
`initializeBasis`; `basis_transcoder.js` ist unverändert).

**Warum zstd 1.5.7:** basis_universal v1_50_0 (wie three r180) liefert das unveränderte
Decoder-Amalgam von zstd 1.4.9 mit (bitgleich mit `contrib/single_file_libs` von zstd v1.4.9
erzeugt, geprüft 22.10.2026). Für 1.4.9 meldet OSV `OSV-2022-15`. Die Lücke liegt im Kompressor
(`ZSTD_compressBlock_opt0`, behoben ab v1.5.2) und war im Decoder-Bau nie enthalten; der
Scanner kennt aber nur die Version. Da die Texturen aus hochgeladenen Paketen stammen, läuft der
Decoder auf nicht vertrauenswürdigen Daten — deshalb der aktuelle Decoder statt einer
VEX-Ausnahme. basis_universal 2.x brächte zstd 1.5.7 selbst mit, ändert aber die
Transcoder-Schnittstelle (`basis_wrappers.cpp`), zu der three r180 nicht passt.

**Neu bauen / prüfen:** `vendor/basis/bauen.sh` baut und vergleicht bitgenau mit den
eingecheckten Dateien; `--schreiben` ersetzt sie. Der Test `tests/basisTranscoder.test.ts` schlägt
an, sobald ein three-Update seinen Transcoder ändert — dann Quelle/Commit hier nachziehen, neu
bauen und `KTX2Loader` gegen die neue Schnittstelle prüfen.
