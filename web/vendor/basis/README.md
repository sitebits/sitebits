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
| SHA-256 `.wasm` | `68b26a493951b7840d873637f790d56072ae3a6b8a4e5e9349cae286291092e3` |
| Lizenzen | Basis Universal Apache-2.0; enthaltenes Zstandard (`zstd/zstddeclib.c`) BSD-3-Clause (gewählt aus „BSD-3-Clause OR GPL-2.0-only“), Text `web/licenses/zstd-BSD-3-Clause.txt` |

Nicht byte-gleich mit dem Upstream-Prebuilt (anderer emsdk-Stand), aber dieselbe Quelle und
dieselbe JS-Schnittstelle (`BASIS`, `KTX2File`, `initializeBasis`).

**Neu bauen / prüfen:** `vendor/basis/bauen.sh` baut und vergleicht bitgenau mit den
eingecheckten Dateien; `--schreiben` ersetzt sie. Der Test `tests/basisTranscoder.test.ts` schlägt
an, sobald ein three-Update seinen Transcoder ändert — dann Quelle/Commit hier nachziehen, neu
bauen und `KTX2Loader` gegen die neue Schnittstelle prüfen.
