#!/usr/bin/env bash
# Wochenscan aller unterstützten Versionen auf dem Betriebshost (systemd-Benutzer-Timer).
#
#   wochenscan.sh
#
# Alles Hostspezifische steht in $SCANVIEW_WOCHENSCAN_KONF/umgebung (Standard:
# $HOME/.config/scanview-wochenscan/umgebung): BETRIEB, TEXTFILE_DIR, TELEGRAM_TOKEN_DATEI,
# TELEGRAM_CHAT, SCANVIEW_CONTAINER, PYTHON. Daneben liegen die Vertrauensanker
# allowed_signers_release (Manifest-Signaturen) und allowed_signers_git (Release-Tag).
#
# Ablauf: beide Klone aktualisieren, neuesten Release-Tag v<X.Y.Z> prüfen (Signatur gegen den
# Anker), frischen Worktree genau dieses Tags anlegen und NUR dessen Code ausführen, Metrik
# atomar schreiben, bei Fehler oder neuem Befund per Telegram melden. Der Token steht nur in
# einer Datei und läuft über stdin an curl, nie über argv.
#
# Aufbewahrung: Der Worktree jedes Laufs (`lauf-<Zeit>/`) wird am Ende des Laufs entfernt, auch
# bei Fehler oder Abbruch. Die Ergebnisse bleiben dagegen liegen und werden nie automatisch
# gelöscht: `wochenscans/<Zeit>/` (je Lauf ein Ordner mit ergebnis.json) wächst unbegrenzt, ein
# Lauf pro Woche und Ordner; es gibt keine Rotation, weil das Löschen von Nachweisen eine
# bewusste Entscheidung des Betreibers sein soll. `bekannt.txt` ist keine Historie, sondern der
# Stand des letzten Laufs (die aktuell blockierenden IDs; jeder Lauf ersetzt sie). Wer
# aufräumen will, löscht alte `wochenscans/<Zeit>/`-Ordner von Hand — das Archiv der
# Release-Nachweise ist davon nicht berührt.
#
# Exit 0: vollständiger Lauf (grün oder blockierend; Befunde werden gemeldet, nicht als
# Skriptfehler gewertet). Exit ≠ 0: der Lauf ist gescheitert; die Metrik bleibt stehen, damit der
# Alarm für ausbleibende Läufe greift.
set -euo pipefail

KONF=${SCANVIEW_WOCHENSCAN_KONF:-$HOME/.config/scanview-wochenscan}
[[ -r $KONF/umgebung ]] || { echo "Wochenscan: $KONF/umgebung fehlt oder ist nicht lesbar" >&2; exit 2; }
# shellcheck source=/dev/null
. "$KONF/umgebung"
for v in BETRIEB TEXTFILE_DIR TELEGRAM_TOKEN_DATEI TELEGRAM_CHAT; do
	[[ -n ${!v:-} ]] || { echo "Wochenscan: $v fehlt in $KONF/umgebung" >&2; exit 2; }
done
PYTHON=${PYTHON:-python3}
if [[ -n ${SCANVIEW_CONTAINER:-} ]]; then
	export SCANVIEW_CONTAINER
fi

# Nur ein Lauf zur Zeit (Timer und Handaufruf teilen bekannt.txt und cache/). flock hält die Sperre
# selbst und schließt sie vor dem Start (-o): Nachfahren, die den Lauf überleben (Container,
# git gc), halten sie nicht fest.
if [[ -z ${SCANVIEW_WOCHENSCAN_GESPERRT:-} ]]; then
	RC=0
	SCANVIEW_WOCHENSCAN_GESPERRT=1 flock -n -E 75 -o "$BETRIEB/wochenscan.lock" \
		bash "${BASH_SOURCE[0]}" "$@" || RC=$?
	if [[ $RC -eq 75 ]]; then
		echo "Wochenscan: läuft bereits (Sperre $BETRIEB/wochenscan.lock)" >&2
		exit 2
	fi
	exit "$RC"
fi

PRODUKT=$BETRIEB/scanview
ARCHIV=$BETRIEB/scanview-nachweise
METRIK=$TEXTFILE_DIR/scanview_wochenscan.prom
LAUF=
WORKTREE=
TMP=
GEMELDET=0
# Signaturen nur gegen den SSH-Anker: Eine OpenPGP- oder X.509-Signatur prüfte git sonst gegen den
# Schlüsselbund des Betriebskontos. Erste Sperre: der Typ genau der Signatur, die git prüft
# (ssh_signiert), zweite: gpg und gpgsm sind für diese Aufrufe abgeschaltet.
PRUEF=(-c "gpg.ssh.allowedSignersFile=$KONF/allowed_signers_git"
	-c gpg.openpgp.program=false -c gpg.x509.program=false)

# ssh_tag <objekt-text>: git prüft den letzten Signaturblock eines Tags — der muss SSH sein.
ssh_tag() {
	local letzte
	letzte=$(grep -- '^-----BEGIN ' <<<"$1" | tail -n 1) || true
	[[ $letzte == '-----BEGIN SSH SIGNATURE-----' ]]
}

# ssh_commit <objekt-text>: git prüft die erste gpgsig-Zeile im Kopf (bis zur ersten Leerzeile).
ssh_commit() {
	local erste
	erste=$(sed '/^$/q' <<<"$1" | grep -m 1 -- '^gpgsig ') || true
	[[ $erste == 'gpgsig -----BEGIN SSH SIGNATURE-----' ]]
}

telegram() {
	# Text als curl-Konfiguration über stdin: weder Token noch Text erscheinen in argv.
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
	local text="Wochenscan gescheitert: $1"
	GEMELDET=1
	echo "$text" >&2
	telegram "$text" || echo "Wochenscan: Telegram-Meldung nicht zugestellt" >&2
	exit 2
}

aufraeumen() {
	local rc=$?
	if [[ $rc -ne 0 && $GEMELDET -eq 0 ]]; then
		GEMELDET=1
		telegram "Wochenscan gescheitert: unerwarteter Abbruch (Exit $rc)" || true
	fi
	if [[ -n $WORKTREE && -d $WORKTREE ]]; then
		git -C "$PRODUKT" worktree remove "$WORKTREE" >&2 || echo "Wochenscan: Worktree $WORKTREE nicht entfernt" >&2
	fi
	if [[ -n $LAUF && -d $LAUF ]]; then
		rmdir "$LAUF" 2>/dev/null || true
	fi
	if [[ -n $TMP && -d $TMP ]]; then
		rm -f "$TMP"/stderr.txt "$TMP"/auswertung.py
		rmdir "$TMP" 2>/dev/null || true
	fi
}
trap aufraeumen EXIT

TMP=$(mktemp -d)

# 1. Beide Klone aktualisieren. Der Archiv-Klon wird nur vorgespult (nie lokal verändert), damit
#    neue Releases im Arbeitsverzeichnis stehen.
git -C "$PRODUKT" fetch --prune --tags >&2 || fehler "git fetch im Produkt-Klon"
git -C "$ARCHIV" fetch --prune --tags >&2 || fehler "git fetch im Archiv-Klon"
git -C "$ARCHIV" merge --ff-only '@{upstream}' >&2 || fehler "Archiv-Klon nicht vorspulbar"
# Der Archiv-Stand (unterstuetzt.toml, releases/) muss vom Release-Schlüssel signiert sein.
ARCHIV_OBJEKT=$(git -C "$ARCHIV" cat-file commit HEAD) || fehler "Archiv-Stand nicht lesbar"
ssh_commit "$ARCHIV_OBJEKT" || fehler "Archiv-Stand nicht signiert (keine SSH-Signatur)"
git -C "$ARCHIV" "${PRUEF[@]}" verify-commit HEAD >&2 || fehler "Archiv-Stand nicht signiert"

# 2. Neuester Release-Tag, SemVer-sortiert; Vorabversionen und fremde Tags zählen nicht.
TAGS=$(git -C "$PRODUKT" tag --list 'v*' | { grep -E '^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$' || true; } | sort -V)
TAG=$(tail -n 1 <<<"$TAGS")
[[ -n $TAG ]] || fehler "kein Release-Tag v<X.Y.Z> im Produkt-Klon"
TAG_OBJEKT=$(git -C "$PRODUKT" cat-file tag "$TAG") || fehler "Tag-Objekt $TAG nicht lesbar"
ssh_tag "$TAG_OBJEKT" || fehler "Tag nicht mit dem Release-Schlüssel signiert (keine SSH-Signatur)"
git -C "$PRODUKT" "${PRUEF[@]}" tag -v "$TAG" >&2 || fehler "Tag nicht mit dem Release-Schlüssel signiert"
# Der Name im Tag-Objekt muss dem Ref-Namen entsprechen (kein altes signiertes Tag unter neuem Namen).
TAG_NAME=$(sed -n 's/^tag //p' <<<"$TAG_OBJEKT")
[[ $TAG_NAME == "$TAG" ]] || fehler "Tag nicht mit dem Release-Schlüssel signiert (Name im Tag-Objekt: ${TAG_NAME:-keiner})"

# 3. Frischer Worktree des Tags; ausgeführt wird nur dieser Code, nie der Klon.
ZEIT=$(date -u +%Y-%m-%dT%H%M%SZ)
LAUF=$BETRIEB/lauf-$ZEIT
mkdir -p "$LAUF"
git -C "$PRODUKT" worktree add --detach "$LAUF/code" "$TAG^{commit}" >&2 || fehler "Worktree von $TAG nicht anlegbar"
WORKTREE=$LAUF/code
AUSGABE=$BETRIEB/wochenscans/$ZEIT

# Erstlauf: ohne bekannt.txt bricht der Scan ab; leer = noch nichts gemeldet.
[[ -e $BETRIEB/bekannt.txt ]] || : >"$BETRIEB/bekannt.txt"

RC=0
(
	cd "$WORKTREE"
	PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$WORKTREE/nachweise/src" "$PYTHON" -m scanview_nachweise wochenscan \
		--archiv "$ARCHIV" --repo "$PRODUKT" \
		--vex "$WORKTREE/docs/compliance/vex.openvex.json" \
		--allowed-signers "$KONF/allowed_signers_release" \
		--ausgabe "$AUSGABE" --bekannt "$BETRIEB/bekannt.txt" \
		--cache "$BETRIEB/cache" --db-aktualisieren
) 2>"$TMP/stderr.txt" || RC=$?
cat "$TMP/stderr.txt" >&2

if [[ $RC -ne 0 && $RC -ne 1 ]]; then
	REGEL=$(grep -m1 -o 'NACHWEIS-FEHLER [a-z0-9-]*' "$TMP/stderr.txt" | cut -d' ' -f2 || true)
	fehler "${REGEL:-Exit $RC ohne Regelkennung}"
fi

# Auswertung des Ergebnisses: Zahl blockierender IDs, alle IDs, neue IDs, betroffene Versionen.
cat >"$TMP/auswertung.py" <<'PY'
import json
import sys

d = json.load(open(sys.argv[1], encoding="utf-8"))
zeilen = d["blockierend"]
ids = sorted({z.split(" ", 1)[0] for zs in zeilen.values() for z in zs})
neu = sorted(d["neu"])
versionen = sorted(v for v, zs in zeilen.items() if any(z.split(" ", 1)[0] in neu for z in zs))
print(len(ids))
print(" ".join(ids))
print(" ".join(neu))
print(", ".join(versionen))
PY
mapfile -t AUSW < <("$PYTHON" "$TMP/auswertung.py" "$AUSGABE/ergebnis.json") || fehler "ergebnis.json nicht auswertbar"
[[ ${#AUSW[@]} -eq 4 ]] || fehler "ergebnis.json nicht auswertbar"
ANZAHL=${AUSW[0]}
IDS=${AUSW[1]}
NEU=${AUSW[2]}
VERSIONEN=${AUSW[3]}

# 4. Metrik atomar; last_success nur bei Exit 0, sonst der alte Wert bleibt stehen.
JETZT=$(date +%s)
ALT_ERFOLG=
if [[ -r $METRIK ]]; then
	ALT_ERFOLG=$(sed -n 's/^scanview_wochenscan_last_success_timestamp \([0-9][0-9]*\)$/\1/p' "$METRIK" | tail -n 1)
fi
{
	echo "scanview_wochenscan_last_run_timestamp $JETZT"
	if [[ $RC -eq 0 ]]; then
		echo "scanview_wochenscan_last_success_timestamp $JETZT"
	elif [[ -n $ALT_ERFOLG ]]; then
		echo "scanview_wochenscan_last_success_timestamp $ALT_ERFOLG"
	fi
	echo "scanview_wochenscan_blockierend $ANZAHL"
} >"$METRIK.tmp"
chmod 0644 "$METRIK.tmp"  # node_exporter liest unter eigenem Nutzer; die Unit setzt UMask=0077
mv "$METRIK.tmp" "$METRIK"

# 5. Telegram nur bei einem neuen Befund; bekannt.txt wird erst nach erfolgreicher Meldung
#    fortgeschrieben, sonst kommt die Meldung beim nächsten Lauf noch einmal.
if [[ -n $NEU ]]; then
	telegram "Neuer Befund ≥ hoch: ${NEU} (Version ${VERSIONEN})" \
		|| { GEMELDET=1; echo "Wochenscan: Telegram-Meldung nicht zugestellt" >&2; exit 3; }
fi

# 6. Bekannte IDs = aktuell blockierende IDs.
if [[ -n $IDS ]]; then
	tr ' ' '\n' <<<"$IDS" >"$BETRIEB/bekannt.txt.tmp"
else
	: >"$BETRIEB/bekannt.txt.tmp"
fi
mv "$BETRIEB/bekannt.txt.tmp" "$BETRIEB/bekannt.txt"
echo "Wochenscan $TAG: Exit $RC, $ANZAHL blockierende IDs, $(wc -w <<<"$NEU") neu"
