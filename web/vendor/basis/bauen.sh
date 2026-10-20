#!/usr/bin/env bash
# Basis-Universal-Transcoder ohne dynamische Codeerzeugung bauen (für die CSP ohne 'unsafe-eval').
# Das Upstream-Build (auch three/examples/jsm/libs/basis) erzeugt embind-Aufrufer per Function()
# und läuft deshalb unter script-src 'self' 'wasm-unsafe-eval' nicht: Mesh-Texturen fehlten still.
#
#   vendor/basis/bauen.sh              baut und vergleicht mit den eingecheckten Dateien
#   vendor/basis/bauen.sh --schreiben  baut und ersetzt die eingecheckten Dateien
#
# Bitgleich reproduzierbar (zwei Läufe, gleiche SHA-256). Quelle per Commit, Toolchain per
# Image-Digest gepinnt, Bau ohne Netz; Flags wie webgl/transcoder/CMakeLists.txt plus
# -sDYNAMIC_EXECUTION=0. Herkunft und Lizenzen: README.md daneben.
set -euo pipefail
QUELLE=https://github.com/BinomialLLC/basis_universal.git
COMMIT=051ad6d8a64bb95a79e8601c317055fd1782ad3e   # Tag v1_50_0, wie three r180
IMAGE=emscripten/emsdk@sha256:2e5a1ae55cc0ef2d0c5d5c87deb4a74bea15c6ae03b2333d8aca02516671345b  # 3.1.67
FLAGS=-sDYNAMIC_EXECUTION=0

hier="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
arbeit="${XDG_CACHE_HOME:-$HOME/.cache}/scanview-basis-build"
mkdir -p "$arbeit"
if [[ ! -d "$arbeit/quelle/.git" ]]; then
  git init -q "$arbeit/quelle"
  git -C "$arbeit/quelle" remote add origin "$QUELLE"
fi
git -C "$arbeit/quelle" fetch -q --depth 1 origin "$COMMIT"
git -C "$arbeit/quelle" -c advice.detachedHead=false checkout -q FETCH_HEAD
if [[ "$(git -C "$arbeit/quelle" rev-parse HEAD)" != "$COMMIT" ]] \
    || [[ -n "$(git -C "$arbeit/quelle" status --porcelain)" ]]; then
  echo "Quelle nicht sauber auf $COMMIT" >&2; exit 1
fi
bau="$arbeit/bau"
rm -rf -- "$bau"; mkdir -p "$bau"
docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$arbeit:/src" -w /src/bau "$IMAGE" \
  bash -c "emcmake cmake ../quelle/webgl/transcoder -DCMAKE_EXE_LINKER_FLAGS='$FLAGS' >/dev/null && emmake make -j8 >/dev/null"
if grep -qE '(^|[^A-Za-z_.])(new Function|Function\(|eval\()' "$bau/basis_transcoder.js"; then
  echo "Gebauter Transcoder erzeugt noch Code zur Laufzeit (Function/eval)" >&2; exit 1
fi
if [[ "${1:-}" == "--schreiben" ]]; then
  cp "$bau/basis_transcoder.js" "$bau/basis_transcoder.wasm" "$hier/"
  (cd "$hier" && sha256sum basis_transcoder.js basis_transcoder.wasm)
else
  for f in basis_transcoder.js basis_transcoder.wasm; do
    cmp -s "$bau/$f" "$hier/$f" || { echo "$f weicht vom Neubau ab" >&2; exit 1; }
  done
  echo "Neubau bitgleich mit den eingecheckten Dateien"
fi
