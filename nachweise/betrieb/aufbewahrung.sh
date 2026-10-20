#!/usr/bin/env bash
# Jährliche Zweitablage des Nachweis-Archivs als git bundle (Aufbewahrung ≥ 10 Jahre).
#
#   aufbewahrung.sh
#
# Konfiguration in $SCANVIEW_WOCHENSCAN_KONF/umgebung (Standard:
# $HOME/.config/scanview-wochenscan/umgebung, dieselbe Datei wie beim Wochenscan): BETRIEB,
# TEXTFILE_DIR, TELEGRAM_TOKEN_DATEI, TELEGRAM_CHAT sowie ZIEL_SFTP (Benutzer@Host des
# sftp-Ziels), ZIEL_SSH_SCHLUESSEL, optional ZIEL_PORT (Standard 23) und ZIEL_VERZEICHNIS
# (Standard scanview-nachweise; nur ein einfacher Name aus ASCII-Buchstaben, Ziffern,
# . _ -). Das Skript ist zielneutral: jedes sftp-Ziel genügt.
#
# Ablauf: Archiv-Klon aktualisieren, Bundle (alle Refs) erzeugen und prüfen, SHA-256 festhalten,
# Ziel prüfen (existiert die Datei dort schon: Abbruch, nie überschreiben, nie löschen), hochladen,
# Rücklese-Probe (zurückholen, Hash vergleichen, Bundle prüfen, in ein Wegwerf-Repo klonen,
# fsck, Refs gleich denen des Klons), erst danach die Metrik. Telegram nur bei Fehler. Die
# lokale Kopie unter $BETRIEB/aufbewahrung/ bleibt liegen.
set -euo pipefail

KONF=${SCANVIEW_WOCHENSCAN_KONF:-$HOME/.config/scanview-wochenscan}
[[ -r $KONF/umgebung ]] || { echo "Aufbewahrung: $KONF/umgebung fehlt oder ist nicht lesbar" >&2; exit 2; }
# shellcheck source=/dev/null
. "$KONF/umgebung"
for v in BETRIEB TEXTFILE_DIR TELEGRAM_TOKEN_DATEI TELEGRAM_CHAT ZIEL_SFTP ZIEL_SSH_SCHLUESSEL; do
	[[ -n ${!v:-} ]] || { echo "Aufbewahrung: $v fehlt in $KONF/umgebung" >&2; exit 2; }
done
[[ $BETRIEB != *[[:space:]]* ]] || { echo "Aufbewahrung: BETRIEB darf kein Leerzeichen enthalten" >&2; exit 2; }
ZIEL_PORT=${ZIEL_PORT:-23}
ZIEL_VERZEICHNIS=${ZIEL_VERZEICHNIS:-scanview-nachweise}
# Steht ungeschützt in den sftp-Batchzeilen: nur ein einfacher ASCII-Name, kein Pfad, keine Option
# (ausgeschriebene Klassen: Bereiche wie A-Z hängen von der Locale ab).
ASCII_ANFANG='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
[[ $ZIEL_VERZEICHNIS =~ ^[$ASCII_ANFANG][$ASCII_ANFANG._-]*$ ]] || {
	echo "Aufbewahrung: ZIEL_VERZEICHNIS='$ZIEL_VERZEICHNIS' unzulässig (Name aus Buchstaben, Ziffern, . _ -)" >&2
	exit 2
}

ARCHIV=$BETRIEB/scanview-nachweise
LOKAL=$BETRIEB/aufbewahrung
METRIK=$TEXTFILE_DIR/scanview_aufbewahrung.prom
TMP=
GEMELDET=0
BATCH_NR=0

telegram() {
	# Token und Text laufen als curl-Konfiguration über stdin, nie über argv.
	local text=$1 token antwort
	token=$(<"$TELEGRAM_TOKEN_DATEI")
	text=${text//\\/\\\\}
	text=${text//\"/\\\"}
	text=${text//$'\n'/\\n}
	antwort=$(printf 'url = "https://api.telegram.org/bot%s/sendMessage"\ndata-urlencode = "chat_id=%s"\ndata-urlencode = "text=%s"\n' \
		"$token" "$TELEGRAM_CHAT" "$text" | curl --silent --show-error --fail --max-time 30 -K -) || return 1
	[[ $antwort == *'"ok":true'* ]]
}

fehler() {
	local text="Aufbewahrung gescheitert: $1"
	GEMELDET=1
	echo "$text" >&2
	telegram "$text" || echo "Aufbewahrung: Telegram-Meldung nicht zugestellt" >&2
	exit 2
}

aufraeumen() {
	local rc=$?
	if [[ $rc -ne 0 && $GEMELDET -eq 0 ]]; then
		GEMELDET=1
		telegram "Aufbewahrung gescheitert: unerwarteter Abbruch (Exit $rc)" || true
	fi
	if [[ -n $TMP && -d $TMP ]]; then
		rm -r -- "$TMP"
	fi
}
trap aufraeumen EXIT

TMP=$(mktemp -d)

sftp_batch() {
	# $1: Batch-Inhalt; Ausgabe: stdout des Laufs ohne die Echozeilen von sftp.
	local datei
	BATCH_NR=$((BATCH_NR + 1))
	datei=$TMP/batch.$BATCH_NR
	printf '%s\n' "$1" >"$datei"
	sftp -P "$ZIEL_PORT" -i "$ZIEL_SSH_SCHLUESSEL" -o BatchMode=yes -o IdentitiesOnly=yes \
		-o StrictHostKeyChecking=yes -b "$datei" "$ZIEL_SFTP" | { grep -v '^sftp>' || true; }
	return "${PIPESTATUS[0]}"
}

namen() {
	# Nur die Dateinamen (letzte Pfadkomponente) aus einem sftp-Listing.
	sed 's|.*/||'
}

DATUM=$(date -u +%Y-%m-%d)
NAME=scanview-nachweise-$DATUM
BUNDLE=$NAME.bundle
SUMME=$NAME.sha256

# 1. Archiv aktualisieren; ein Fehler bricht ab, ein veralteter Stand wird nie gesichert.
git -C "$ARCHIV" fetch --prune --tags >&2 || fehler "git fetch im Archiv-Klon"

# 2. Bundle erzeugen, prüfen, Hash festhalten.
mkdir -p "$LOKAL"
git -C "$ARCHIV" bundle create "$LOKAL/$BUNDLE" --all >&2 || fehler "Bundle nicht erzeugbar"
git -C "$ARCHIV" bundle verify "$LOKAL/$BUNDLE" >&2 || fehler "Bundle nicht prüfbar"
(cd "$LOKAL" && sha256sum "$BUNDLE" >"$SUMME") || fehler "SHA-256 nicht berechenbar"

# 3. Ziel prüfen. Erst das Wurzelverzeichnis (nicht erreichbar ≠ „frei“), dann das Verzeichnis.
WURZEL=$(sftp_batch "ls -1") || fehler "Ziel nicht erreichbar"
if namen <<<"$WURZEL" | grep -Fxq "$ZIEL_VERZEICHNIS"; then
	INHALT=$(sftp_batch "ls -1 $ZIEL_VERZEICHNIS") || fehler "Zielverzeichnis nicht lesbar"
	for datei in "$BUNDLE" "$SUMME"; do
		if namen <<<"$INHALT" | grep -Fxq "$datei"; then
			fehler "$datei existiert schon am Ziel — nichts überschrieben, nichts hochgeladen"
		fi
	done
else
	sftp_batch "mkdir $ZIEL_VERZEICHNIS" >/dev/null || fehler "Zielverzeichnis nicht anlegbar"
fi

# 4. Hochladen.
sftp_batch "put $LOKAL/$BUNDLE $ZIEL_VERZEICHNIS/$BUNDLE
put $LOKAL/$SUMME $ZIEL_VERZEICHNIS/$SUMME" >/dev/null || fehler "Upload"

# 5. Rücklese-Probe.
mkdir "$TMP/rueck"
sftp_batch "get $ZIEL_VERZEICHNIS/$BUNDLE $TMP/rueck/$BUNDLE
get $ZIEL_VERZEICHNIS/$SUMME $TMP/rueck/$SUMME" >/dev/null || fehler "Rücklese: Dateien nicht zurückholbar"
cmp -s "$LOKAL/$SUMME" "$TMP/rueck/$SUMME" || fehler "Rücklese: Prüfsummendatei weicht ab"
(cd "$TMP/rueck" && sha256sum --check --status "$SUMME") || fehler "Rücklese: SHA-256 des Bundles weicht ab"
git -C "$ARCHIV" bundle verify "$TMP/rueck/$BUNDLE" >&2 || fehler "Rücklese: Bundle nicht prüfbar"
git clone --quiet "$TMP/rueck/$BUNDLE" "$TMP/probe" >&2 || fehler "Rücklese: Bundle nicht klonbar"
git -C "$TMP/probe" fsck --full >&2 || fehler "Rücklese: fsck des Klons"
SOLL=$(git -C "$ARCHIV" for-each-ref --format='%(objectname) %(refname)' | sort)
IST=$(git -C "$ARCHIV" bundle list-heads "$TMP/rueck/$BUNDLE" | { grep -v ' HEAD$' || true; } | sort)
[[ $SOLL == "$IST" ]] || fehler "Rücklese: Refs des Bundles weichen vom Archiv-Klon ab"

# 6. Metrik atomar, erst nach bestandener Probe.
JETZT=$(date +%s)
echo "scanview_aufbewahrung_last_success_timestamp $JETZT" >"$METRIK.tmp"
chmod 0644 "$METRIK.tmp"  # node_exporter liest unter eigenem Nutzer; die Unit setzt UMask=0077
mv "$METRIK.tmp" "$METRIK"
echo "Aufbewahrung: $BUNDLE am Ziel $ZIEL_VERZEICHNIS abgelegt, Rücklese-Probe bestanden"
