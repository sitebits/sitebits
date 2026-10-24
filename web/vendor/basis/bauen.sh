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
#
# Zstandard: basis_universal v1_50_0 liefert den Decoder von zstd 1.4.9 mit (zstd/zstddeclib.c,
# unverändertes Amalgam). Der Bau ersetzt ihn durch den Decoder von zstd 1.5.7 (gepinnter Commit),
# erzeugt mit zstds eigenem build/single_file_libs/combine.py im selben Image, dazu zstd.h und
# zstd_errors.h (die 1.5.7-Fassung von zstd.h bindet es ein).
# Der basis_universal-Checkout selbst bleibt sauber; ersetzt wird in einer Kopie unter bau/.
set -euo pipefail
QUELLE=https://github.com/BinomialLLC/basis_universal.git
COMMIT=051ad6d8a64bb95a79e8601c317055fd1782ad3e   # Tag v1_50_0, wie three r180
IMAGE=emscripten/emsdk@sha256:2e5a1ae55cc0ef2d0c5d5c87deb4a74bea15c6ae03b2333d8aca02516671345b  # 3.1.67
FLAGS=-sDYNAMIC_EXECUTION=0
ZSTD_QUELLE=https://github.com/facebook/zstd.git
ZSTD_COMMIT=f8745da6ff1ad1e7bab384bd1f9d742439278e99   # Tag v1.5.7 (Tag-Objekt ac66b19e…)

hier="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
arbeit="${XDG_CACHE_HOME:-$HOME/.cache}/scanview-basis-build"
mkdir -p "$arbeit"
holen() {  # holen <ordner> <repo> <commit>: flacher Checkout genau dieses Commits, sauber
  local ordner="$arbeit/$1"
  if [[ ! -d "$ordner/.git" ]]; then
    git init -q "$ordner"
    git -C "$ordner" remote add origin "$2"
  fi
  git -C "$ordner" fetch -q --depth 1 origin "$3"
  git -C "$ordner" -c advice.detachedHead=false checkout -q FETCH_HEAD
  if [[ "$(git -C "$ordner" rev-parse HEAD)" != "$3" ]] \
      || [[ -n "$(git -C "$ordner" status --porcelain)" ]]; then
    echo "Quelle $1 nicht sauber auf $3" >&2; exit 1
  fi
}
holen quelle "$QUELLE" "$COMMIT"
holen zstd "$ZSTD_QUELLE" "$ZSTD_COMMIT"
bau="$arbeit/bau"
rm -rf -- "$bau"; mkdir -p "$bau/quelle" "$bau/zstd" "$bau/aus"
git -C "$arbeit/quelle" archive HEAD | tar -x -C "$bau/quelle"
git -C "$arbeit/zstd" archive HEAD | tar -x -C "$bau/zstd"
docker run --rm --network none -u "$(id -u):$(id -g)" -e HOME=/tmp -v "$bau:/src" -w /src "$IMAGE" bash -c "
  set -e
  cd /src/zstd/build/single_file_libs
  python3 combine.py -r ../../lib -x legacy/zstd_legacy.h -o /src/quelle/zstd/zstddeclib.c zstddeclib-in.c >/dev/null 2>&1
  cp ../../lib/zstd.h ../../lib/zstd_errors.h /src/quelle/zstd/
  cd /src/aus
  emcmake cmake ../quelle/webgl/transcoder -DCMAKE_EXE_LINKER_FLAGS='$FLAGS' >/dev/null
  emmake make -j8 >/dev/null"
if ! grep -q '#define ZSTD_VERSION_MINOR    5' "$bau/quelle/zstd/zstd.h" \
    || ! grep -q '#define ZSTD_VERSION_RELEASE  7' "$bau/quelle/zstd/zstd.h"; then
  echo "zstd.h ist nicht 1.5.7" >&2; exit 1
fi
bau="$bau/aus"
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
