#!/usr/bin/env bash
# Code-Karte (CodeCharta) eines Git-Stands als statische Datei — Repo rein, Karte raus.
#
#   codekarte.sh <repo> <ref> <ausgabe-ordner> [name]
#
# Für siteBits nur auf den ÖFFENTLICHEN Export anwenden (Klon von scanview-oeffentlich bzw. GitHub),
# nie auf das private Repo: die Karte enthält jeden Dateipfad und je Datei die Zahl der Commits.
#
# Ablauf: frischer Klon des Stands <ref> in einen Wegwerf-Ordner, Analyse im CodeCharta-Image
# (Digest fest, ohne Netz, als aufrufender Nutzer), keine Autoren (`--add-author` bleibt aus —
# Namen sind personenbezogen und die Co-Authored-By-Zeilen verfälschen die Zählung). Mit
# SCANVIEW_BEGRIFFE=<datei> bricht das Skript ab, wenn ein Begriff in der Karte steht — dieselbe Liste
# und dieselbe Prüffunktion wie die Export-Probe (nachweise, `pruefe_begriffe`).
#
# Ergebnis in <ausgabe-ordner>: <name>-<ref>.cc.json.gz, dazu .sha256 und meta.json (Stand, Commit,
# Werkzeug-Digest). Bestehende Dateien werden nie überschrieben.
set -euo pipefail

ANALYSE_IMAGE=${CODEKARTE_IMAGE:-codecharta/codecharta-analysis@sha256:ae97d9425ef81c00b52947a1d9d1c7cad1346204a3a288ef4fb90f07b7efd2b1}  # 2.0.2 (10.09.2026)

[[ $# -ge 3 ]] || { echo "Aufruf: codekarte.sh <repo> <ref> <ausgabe-ordner> [name]" >&2; exit 2; }
REPO=$1 REF=$2 AUS=$3 NAME=${4:-$(basename "$(realpath "$REPO")")}
[[ $NAME =~ ^[A-Za-z0-9._-]+$ && $REF =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "Name/Ref mit unerlaubten Zeichen" >&2; exit 2; }
COMMIT=$(git -C "$REPO" rev-parse --verify "$REF^{commit}")
DATEI="$NAME-${REF//\//-}.cc.json.gz"
mkdir -p "$AUS"
[[ ! -e $AUS/$DATEI ]] || { echo "$AUS/$DATEI gibt es schon — nicht überschrieben" >&2; exit 2; }

ARBEIT=$(mktemp -d "${TMPDIR:-/tmp}/codekarte.XXXXXX")
trap 'rm -rf -- "$ARBEIT"' EXIT
git clone -q --no-hardlinks "$REPO" "$ARBEIT/repo"
git -C "$ARBEIT/repo" -c advice.detachedHead=false checkout -q "$COMMIT"
mkdir -p "$ARBEIT/home"

docker run --rm --network=none --user "$(id -u):$(id -g)" -e HOME=/w/home \
	-e GIT_CONFIG_COUNT=1 -e GIT_CONFIG_KEY_0=safe.directory -e GIT_CONFIG_VALUE_0='*' \
	-e JAVA_TOOL_OPTIONS='-Duser.home=/w/home -Djava.io.tmpdir=/w/home' \
	-v "$ARBEIT:/w" "$ANALYSE_IMAGE" sh -euc '
		ccsh unifiedparser /w/repo -e "node_modules,\.venv,\.git" -o /w/code.cc.json.gz
		ccsh gitlogparser repo-scan --repo-path /w/repo -o /w/git.cc.json.gz --silent
		ccsh merge /w/code.cc.json.gz /w/git.cc.json.gz -o /w/karte.cc.json.gz'

# Prüfen, was rausgeht: keine Autoren, keine verbotenen Begriffe.
if gzip -dc "$ARBEIT/karte.cc.json.gz" | grep -q '"authors"'; then
	echo "Karte enthält Autoren — abgebrochen" >&2; exit 1
fi
if [[ -n ${SCANVIEW_BEGRIFFE:-} ]]; then
	# Dieselbe Prüfung wie die Export-Probe (Wortanfang, Groß/klein egal, Kodiertes dekodiert) —
	# eine zweite, einfachere Logik meldete „Charlottenhof“ als Treffer.
	mkdir "$ARBEIT/pruef" && gzip -dc "$ARBEIT/karte.cc.json.gz" > "$ARBEIT/pruef/karte.json"
	uv run --quiet --project "$(dirname "$0")/.." --frozen python - "$ARBEIT/pruef" <<'PY'
import sys
from pathlib import Path
from scanview_nachweise.export import lade_begriffe, pruefe_begriffe
treffer = pruefe_begriffe(Path(sys.argv[1]), lade_begriffe())
if treffer:  # Begriffe nie ausgeben: die Liste ist vertraulich
    sys.exit(f"Verbotene Begriffe in der Karte ({len(treffer)} Fundstellen) — abgebrochen")
PY
fi

cp "$ARBEIT/karte.cc.json.gz" "$AUS/$DATEI"
(cd "$AUS" && sha256sum "$DATEI" > "$DATEI.sha256")
cat > "$AUS/meta.json" <<EOF
{"datei": "$DATEI", "ref": "$REF", "commit": "$COMMIT", "erzeugt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
 "werkzeug": "$ANALYSE_IMAGE", "autoren": false,
 "begriffspruefung": $([[ -n ${SCANVIEW_BEGRIFFE:-} ]] && echo true || echo false)}
EOF
echo "Karte: $AUS/$DATEI ($(cut -c1-16 "$AUS/$DATEI.sha256")…)"
