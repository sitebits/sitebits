#!/usr/bin/env bash
# Restore-Probe einer Instanz: die neueste (oder eine angegebene) Sicherung auf
# eine Wegwerf-Instanz zurückspielen, die Daten prüfen und einen Nachweis als JSON schreiben.
# Die laufende Instanz wird nur gelesen; die Wegwerf-Instanz lebt in einem temporären Ordner,
# hört nur auf 127.0.0.1 und wird am Ende samt Ordner entfernt.
#
#   sudo deploy/platform/test/restore-probe.sh [--instanz NAME] [--sicherung DATEI]
#                                               [--port PORT] [--ausgabe DATEI]
#
# Ohne --instanz: Standardinstanz (/srv/scanview, /etc/scanview, Nutzer scanview). Mit --instanz:
# /srv/scanview-NAME, /etc/scanview-NAME, Nutzer scanview-NAME (wie einrichten.sh). --sicherung
# nimmt eine bestimmte Datei, etwa aus einer Sicherung außer Haus; sonst die neueste unter
# <state>/backups. Für Tests lassen sich SV_STATE, SV_ENV, SV_NUTZER und SV_BIN vorgeben.
#
# Geprüft wird:
#   1  PRAGMA integrity_check = ok und keine Fremdschlüssel-Verletzung in der Sicherung
#   2  Schema der Sicherung ≤ Schema der laufenden Instanz
#   3  Bearbeitungsprotokoll: jedes Ereignis der Sicherung steht unverändert in der laufenden
#      Datenbank (das Protokoll ist nur anhängend — ein Unterschied hieße Beschädigung)
#   4  Wegwerf-Server startet auf der Sicherung, migriert auf den aktuellen Stand und antwortet
#      auf /api/gesundheit
#   5  Konten der Sicherung lassen sich mit `scanview-server nutzer` lesen
# Exit 0 nur, wenn alles bestanden ist; der Nachweis wird auch bei Fehlern geschrieben.
set -euo pipefail

INSTANZ="" SICHERUNG="" PORT=18899 AUSGABE=""
while [[ $# -gt 0 ]]; do
	case $1 in
	--instanz) INSTANZ=$2; shift 2 ;;
	--sicherung) SICHERUNG=$2; shift 2 ;;
	--port) PORT=$2; shift 2 ;;
	--ausgabe) AUSGABE=$2; shift 2 ;;
	*) echo "Unbekannte Option: $1" >&2; exit 2 ;;
	esac
done
if [[ -n $INSTANZ && ! $INSTANZ =~ ^[a-z][a-z0-9-]{1,19}$ ]]; then
	echo "Instanzname ungültig: $INSTANZ" >&2; exit 2
fi
[[ $PORT =~ ^[0-9]+$ ]] && ((PORT >= 1024 && PORT <= 65535)) || { echo "Port ungültig" >&2; exit 2; }

name=scanview${INSTANZ:+-$INSTANZ}
STATE=${SV_STATE:-/srv/$name/state}
ENVDATEI=${SV_ENV:-/etc/$name/scanview-server.env}
NUTZER=${SV_NUTZER:-$name}
BIN=${SV_BIN:-/opt/$name/server/.venv/bin/scanview-server}
LIVE=$STATE/scanview.db
for pfad in "$LIVE" "$ENVDATEI" "$BIN"; do
	[[ -e $pfad ]] || { echo "Fehlt: $pfad" >&2; exit 2; }
done
if [[ -z $SICHERUNG ]]; then
	SICHERUNG=$(ls -1t "$STATE"/backups/*.db 2>/dev/null | head -n1 || true)
	[[ -n $SICHERUNG ]] || { echo "Keine Sicherung unter $STATE/backups" >&2; exit 2; }
fi
[[ -f $SICHERUNG ]] || { echo "Sicherung nicht lesbar: $SICHERUNG" >&2; exit 2; }
AUSGABE=${AUSGABE:-restore-probe-${INSTANZ:-standard}-$(date -u +%Y-%m-%dT%H%M%SZ).json}

if [[ $(id -un) == "$NUTZER" ]]; then ALS=(); else ALS=(sudo -u "$NUTZER"); fi
als() { "${ALS[@]}" "$@"; }
live() { als sqlite3 -readonly "$LIVE" "$1"; } # laufende Instanz nur lesen (WAL bleibt unberührt)

wegwerf=$(als mktemp -d)
server_pid=""
aufraeumen() {
	[[ -n $server_pid ]] && kill "$server_pid" 2>/dev/null && wait "$server_pid" 2>/dev/null || true
	als rm -rf -- "$wegwerf"
}
trap aufraeumen EXIT
als cp -- "$SICHERUNG" "$wegwerf/scanview.db"
als chmod 0600 "$wegwerf/scanview.db"
probe=$wegwerf/scanview.db

declare -A ergebnis grund
pruefe() { # pruefe <nr> <ok|fehler> <grund>
	ergebnis[$1]=$2; grund[$1]=$3
	printf '%-7s %s %s\n' "$([[ $2 == ok ]] && echo OK || echo FEHLER)" "$1" "$3"
}

# 1 Integrität — eine unlesbare Sicherung bricht nicht ab: der Nachweis hält den Fehler fest.
ic=$(als sqlite3 "$probe" 'PRAGMA integrity_check' 2>&1 | tr '\n' ' ' | sed 's/ $//') || true
fk=$(als sqlite3 "$probe" 'PRAGMA foreign_key_check' 2>/dev/null | wc -l) || true
v_sich=0 v_live=0 v_nach=0 zahlen_sich='{}'
if [[ $ic == ok && $fk -eq 0 ]]; then
	pruefe 1 ok "integrity_check ok, 0 Fremdschlüssel-Fehler"
else
	pruefe 1 fehler "integrity_check '$ic', $fk Fremdschlüssel-Fehler"
	for nr in 2 3 4 5; do pruefe "$nr" fehler "übersprungen: Sicherung nicht lesbar"; done
fi
if [[ ${ergebnis[1]} == ok ]]; then

# 2 Schema
v_sich=$(als sqlite3 "$probe" 'SELECT MAX(version) FROM schema_version')
v_live=$(live 'SELECT MAX(version) FROM schema_version')
((v_sich <= v_live)) && pruefe 2 ok "Schema Sicherung $v_sich, laufend $v_live" \
	|| pruefe 2 fehler "Schema Sicherung $v_sich neuer als laufend $v_live"

# 3 Protokoll: gleiche Ereignisse bis zur höchsten ID der Sicherung
q_max='SELECT COALESCE(MAX(id), 0) FROM ereignisse'
max_sich=$(als sqlite3 "$probe" "$q_max")
q_hash="SELECT id, zeit, kanal, project_id, art, objekt_id, vorher, nachher FROM ereignisse WHERE id <= $max_sich ORDER BY id"
h_sich=$(als sqlite3 "$probe" "$q_hash" | sha256sum | cut -d' ' -f1)
h_live=$(live "$q_hash" | sha256sum | cut -d' ' -f1)
n_sich=$(als sqlite3 "$probe" 'SELECT COUNT(*) FROM ereignisse')
[[ $h_sich == "$h_live" ]] && pruefe 3 ok "$n_sich Ereignisse bis ID $max_sich gleich in Sicherung und laufender Instanz" \
	|| pruefe 3 fehler "Ereignisse bis ID $max_sich weichen ab (Sicherung $h_sich, laufend $h_live)"

# Zählung je Tabelle (Nachweis, keine eigene Prüfung: die laufende Instanz ist neuer)
zaehlung() {
	local db=$1 t aus=""
	for t in users project_roles api_tokens pois messungen befund_eintraege ereignisse; do
		if [[ -n $(als sqlite3 "$db" "SELECT name FROM sqlite_master WHERE type='table' AND name='$t'") ]]; then
			aus+="\"$t\": $(als sqlite3 "$db" "SELECT COUNT(*) FROM $t"), "
		fi
	done
	printf '{%s}' "${aus%, }"
}
zahlen_sich=$(zaehlung "$probe")

# 4 Wegwerf-Server auf der Sicherung (migriert beim Start). Der Port muss frei sein, sonst
# antwortete ein fremder Dienst. Kein Start über die Funktion `als`: im Hintergrund liefe sie in
# einer Subshell, `$!` wäre deren PID und der Server bliebe nach `kill` verwaist.
if curl -s -o /dev/null "http://127.0.0.1:$PORT/" 2>/dev/null; then
	pruefe 4 fehler "Port $PORT ist schon belegt — anderen Port mit --port wählen"
else
	"${ALS[@]}" env -C "$wegwerf" bash -c "set -a; . '$ENVDATEI'; set +a; SCANVIEW_STATE='$wegwerf' \
		SCANVIEW_GIT_EXPORT=0 exec '$BIN' serve --port $PORT" >"$wegwerf/server.log" 2>&1 &
	server_pid=$!
	gesund=""
	for _ in $(seq 1 60); do
		kill -0 "$server_pid" 2>/dev/null || break
		gesund=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/gesundheit" || true)
		[[ $gesund == 200 ]] && break
		sleep 0.5
	done
	v_nach=$(als sqlite3 "$probe" 'SELECT MAX(version) FROM schema_version')
	if [[ $gesund == 200 ]] && kill -0 "$server_pid" 2>/dev/null && ((v_nach == v_live)); then
		pruefe 4 ok "Wegwerf-Server antwortet, Schema nach Start $v_nach (wie laufend)"
	else
		pruefe 4 fehler "Wegwerf-Server: HTTP '$gesund', Schema nach Start $v_nach (laufend $v_live): $(tail -n3 "$wegwerf/server.log" | tr '\n' ' ')"
	fi
fi
kill "$server_pid" 2>/dev/null && wait "$server_pid" 2>/dev/null || true
server_pid=""

# 5 Konten lesbar
n_konten=$(als sqlite3 "$probe" 'SELECT COUNT(*) FROM users')
if als env -C "$wegwerf" bash -c "set -a; . '$ENVDATEI'; set +a; SCANVIEW_STATE='$wegwerf' exec '$BIN' nutzer" >"$wegwerf/nutzer.txt" 2>&1; then
	pruefe 5 ok "scanview-server nutzer liest die Sicherung ($n_konten Konten)"
else
	pruefe 5 fehler "scanview-server nutzer scheitert: $(tail -n2 "$wegwerf/nutzer.txt" | tr '\n' ' ')"
fi
fi # Sicherung lesbar

gesamt=ok
for nr in 1 2 3 4 5; do [[ ${ergebnis[$nr]} == ok ]] || gesamt=fehler; done
alter_h=$(( ($(date +%s) - $(stat -c %Y "$SICHERUNG")) / 3600 ))
{
	printf '{\n  "art": "restore-probe",\n  "instanz": "%s",\n  "host": "%s",\n' "${INSTANZ:-standard}" "$(hostname)"
	printf '  "zeit_utc": "%s",\n  "ergebnis": "%s",\n' "$(date -u +%FT%TZ)" "$gesamt"
	printf '  "sicherung": {"datei": "%s", "sha256": "%s", "alter_stunden": %d},\n' \
		"$SICHERUNG" "$(sha256sum "$SICHERUNG" | cut -d' ' -f1)" "$alter_h"
	printf '  "schema": {"sicherung": %s, "laufend": %s, "nach_start": %s},\n' "$v_sich" "$v_live" "$v_nach"
	printf '  "zaehlung_sicherung": %s,\n  "pruefungen": {\n' "$zahlen_sich"
	for nr in 1 2 3 4 5; do
		printf '    "%s": {"ergebnis": "%s", "grund": %s}%s\n' "$nr" "${ergebnis[$nr]}" \
			"$(printf '%s' "${grund[$nr]}" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')" \
			"$([[ $nr == 5 ]] || echo ,)"
	done
	printf '  }\n}\n'
} >"$AUSGABE"
echo "Nachweis: $AUSGABE"
echo "Ergebnis: $([[ $gesamt == ok ]] && echo 'alles OK' || echo FEHLER)"
[[ $gesamt == ok ]]
