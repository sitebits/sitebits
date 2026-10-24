#!/usr/bin/env bash
# Richtet scanview-server auf einem Debian-Host ein oder aktualisiert ihn (idempotent).
#
#   sudo einrichten.sh --release <release-verzeichnis>   # Standard: geprüftes Release
#   sudo einrichten.sh <pfad-zum-repo-stand>             # nur für lokale Tests (WARNUNG)
#
# --release: prüft zuerst Signatur und Prüfsummen gegen das signierte Manifest und den Inhalt der
# Tarballs (release_pruefen.py neben diesem Skript, Vertrauensdatei /etc/scanview/allowed_signers
# des Hosts — nie eine Datei aus dem Release) und entpackt web und server aus den geprüften
# Kopien in ein temporäres Verzeichnis. Bei jeder Abweichung Abbruch, bevor etwas verändert wird.
# Maßgeblich ist die Kopie dieses Skripts, die aufgerufen wird: nie das einrichten.sh aus einem
# noch ungeprüften Release starten (Vorgehen: docs/public/betrieb.md „Aktualisieren“).
#
# Vor jeder Änderung: Pflicht-Sicherung der Datenbank (`.backup`, Integritätsprüfung) nach
# /var/backups/scanview/update-sicherungen/ (root:root 0700 — außerhalb des vom Dienst
# beschreibbaren Baums, root folgt dort keinem Pfad des Dienstnutzers). Ein Update (Hinweise:
# /srv/scanview/state, /etc/scanview/scanview-server.env, /opt/scanview/server oder
# /var/lib/scanview-install vorhanden — Liste: SPUREN)
# ohne reguläre Datenbank bricht vor jeder Änderung ab — nur ein wirklich leerer Host gilt als
# Ersteinrichtung. Jeder Lauf, der den Host verändert, hängt eine Zeile an
# /var/log/scanview/updates.log (root:root 0640); das Protokollziel wird vor der ersten Änderung
# geprüft, und scheitert die Abschlusszeile, endet der Lauf mit Fehler:
#   <zeit> <version> <manifest-sha256> <quellcommit> sicherung=<pfad> artefakte=<name:sha256,…> ergebnis=ok|abgebrochen[ <zusätze>]
# Zusätze, in dieser Reihenfolge und jeweils optional: ` grund=<regel>` (nur abgebrochen, z. B.
# db-fehlt), ` WARNUNG: kein geprüftes Release quelle=<pfad>` (nur Repo-Modus, Version
# `ungeprüft`), ` ersteinrichtung=fortgesetzt`, ` warnung=<kennung>`.
# Ein abgelehntes Release (Prüfung gescheitert) schreibt ebenfalls eine Zeile, mit
# `ergebnis=abgelehnt grund=<regel>` (ohne weitere Zusätze) und `-` für alles noch nicht Geprüfte.
# Leser: `scanview-nachweise betrieb updates` (nachweise/src/scanview_nachweise/betrieb.py) —
# ein neuer Zusatz gehört dort und in dessen Formattest nachgezogen.
#
# Voraussetzungen (werden geprüft, nicht installiert — jede Installation ist ein bewusster
# eigener Schritt): Caddy als Nutzer `caddy` auf demselben Host, `setfacl` (Paket acl), rsync,
# curl, sqlite3 (Sicherung), git (Git-Export), uv; für --release zusätzlich python3 (≥ 3.11) und
# ssh-keygen. Beim Repo-Stand muss `web/dist` gebaut sein (npm ci && npm run build).
#
# Legt an: Nutzer scanview, Gruppe scanview-deploy, /srv/scanview/{data,state,eingang},
# /opt/scanview/{server,web}, /usr/local/sbin/scanview-paket-freischalten, die sudoers-Regel
# /etc/sudoers.d/scanview-deploy (nur das Freischalt-Skript; nur wenn sudo installiert ist),
# /etc/scanview/scanview-server.env (nur wenn fehlend), systemd-Units scanview-server,
# scanview-backup.timer und scanview-git-export.timer (Letzterer nur aktiv bei
# SCANVIEW_GIT_EXPORT=1 in der Umgebung; gepusht wird nur mit SCANVIEW_GIT_REMOTE und dem
# Netz-Drop-in aus docs/public/betrieb.md — nie von selbst). Pakete kommen getrennt per paket-bereitstellen.sh.
# JOBLAEUFER_BEGIN
#
# Jobläufer (optional, ab Werk aus): /etc/scanview/scanview-joblaeufer.env wird aus dem Beispiel
# angelegt, wenn sie fehlt. Mit SCANVIEW_JOBLAEUFER=1 darin prüft das Skript VOR jeder Änderung
# systemd ≥ 257, cgroup v2 mit memory, logind, dbus-user-session, Arbeitsspeicher und die
# Import-Umgebung und bricht sonst ab. Dann legt es an: Nutzer scanview-import (Systemnutzer, kein
# Login, Home /var/lib/scanview-import für den User-Manager, Linger an), Gruppe scanview-jobs,
# /srv/scanview/jobs (jobs.db) und /srv/scanview/berichte, die Unit scanview-import-worker und
# Drop-ins für scanview-server, scanview-backup und user@<uid>.service (RAM aller Aufträge).
# Mit 0 hält es einen vorhandenen Worker an und entfernt Unit, Drop-ins und Linger; Nutzer und
# Daten bleiben. Benannte Instanzen erhalten keinen Jobläufer (instanz_rendern.py entfernt die
# Abschnitte zwischen diesen Markierungen).
# JOBLAEUFER_END
set -euo pipefail

fehler() { echo "Fehler: $*" >&2; exit 1; }

# INSTANZ_DISPATCH_BEGIN
# Ohne diese Schalter läuft der bisherige Standard unverändert.
for argument in "$@"; do
    case $argument in
    --instanz|--port|--render-root)
        exec /usr/bin/python3 -I "$(dirname "${BASH_SOURCE[0]}")/instanz_rendern.py" "$@" ;;
    esac
done
# INSTANZ_DISPATCH_END

ALLOWED_SIGNERS=/etc/scanview/allowed_signers
PROTOKOLL=/var/log/scanview/updates.log
DB=/srv/scanview/state/scanview.db
STATE=/srv/scanview/state
# Root-eigener Marker: eine Ersteinrichtung läuft (bzw. brach ab, bevor es eine Datenbank gab).
# Nur mit ihm darf ein Host mit Installationsresten, aber ohne Datenbank, weiter eingerichtet
# werden; /etc/scanview gehört root, der Dienst kann ihn nicht anlegen.
MARKER=/etc/scanview/.ersteinrichtung-laeuft
# Irreversibler Installationsstatus (root, 0600, Ordner root 0700): wird vor dem ersten Start des
# Dienstes gesetzt — ab da kann es eine Datenbank geben — und nie entfernt. Mit ihm gilt ein Host
# nie wieder als Ersteinrichtung, auch wenn der Marker oben noch liegt.
INSTALL_STATUS_ORDNER=/var/lib/scanview-install
INSTALLIERT=$INSTALL_STATUS_ORDNER/installiert
# Reste einer Installation (eine Quelle für Erkennung, Fehlermeldung und Anleitung in betrieb.md).
SPUREN=("$STATE" /etc/scanview/scanview-server.env /opt/scanview/server "$INSTALL_STATUS_ORDNER")
SICHERUNGEN_WURZEL=/var/backups/scanview
SICHERUNGEN=$SICHERUNGEN_WURZEL/update-sicherungen
PYTHON=/usr/bin/python3
# Integritätsprüfung einer SQLite-Datei aus stdin, im Speicher (deserialize); eine WAL-Kennung
# im Kopf wird für die Prüfung auf Rollback gesetzt (in-memory kennt kein WAL).
SICHERUNG_PRUEFEN='import sqlite3, sys
d = bytearray(sys.stdin.buffer.read())
if len(d) >= 20 and d[18:20] == b"\x02\x02":
    d[18:20] = b"\x01\x01"
c = sqlite3.connect(":memory:")
c.deserialize(bytes(d))
print(c.execute("PRAGMA integrity_check").fetchone()[0])'
SSH_KEYGEN=/usr/bin/ssh-keygen   # derselbe feste Pfad wie in release_pruefen.py
SKRIPT_ORDNER=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

AUFRUF="Aufruf: sudo $0 --release <release-verzeichnis>   (lokaler Test: sudo $0 <pfad-zum-repo-stand>)"
if [[ ${1:-} == --release ]]; then
	[[ $# -eq 2 ]] || fehler "$AUFRUF"
	MODUS=release
else
	[[ $# -eq 1 && ${1:-} != -* ]] || fehler "$AUFRUF"
	MODUS=repo
fi
[[ $EUID -eq 0 ]] || fehler "muss als root laufen (sudo)."

# Felder des Update-Protokolls; werden unten gefüllt.
VERSION=ungeprüft
MANIFEST_SHA=-
QUELLCOMMIT=-
ARTEFAKTE=-
SICHERUNG=-
HINWEIS=
ERGEBNIS=abgebrochen
VERAENDERT=0   # 1, sobald der Host verändert wird — ab dann schreibt jeder Ausgang eine Zeile
ARBEIT=
STUFE=

# Protokollziel anlegen/prüfen: root-eigener Ordner und Datei, kein Symlink, beschreibbar.
protokoll_bereit() {
	[[ ! -L /var/log/scanview && ! -L $PROTOKOLL ]] || { echo "Fehler: $PROTOKOLL ist ein Symlink" >&2; return 1; }
	install -d -o root -g root -m 0750 /var/log/scanview || return 1
	# Nur anlegen, wenn sie fehlt — `install /dev/null` würde eine vorhandene Datei leeren.
	[[ -e $PROTOKOLL ]] || (umask 0137 && : >"$PROTOKOLL") || return 1
	[[ -f $PROTOKOLL && ! -L $PROTOKOLL ]] || { echo "Fehler: $PROTOKOLL ist keine reguläre Datei" >&2; return 1; }
	chown root:root "$PROTOKOLL" && chmod 0640 "$PROTOKOLL" && : >>"$PROTOKOLL"
}

# shellcheck disable=SC2329  # aufgerufen aus beenden (EXIT-Trap)
protokollieren() {
	protokoll_bereit || return 1
	printf '%s %s %s %s sicherung=%s artefakte=%s ergebnis=%s%s\n' \
		"$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$VERSION" "$MANIFEST_SHA" "$QUELLCOMMIT" \
		"$SICHERUNG" "$ARTEFAKTE" "$ERGEBNIS" "$HINWEIS" >>"$PROTOKOLL"
}

# shellcheck disable=SC2329  # aufgerufen per trap … EXIT
beenden() {
	local rc=$?
	if [[ $VERAENDERT -eq 1 ]]; then
		if ! protokollieren; then
			echo "Fehler: Update-Protokoll $PROTOKOLL nicht geschrieben — Lauf gilt als gescheitert." >&2
			# Ohne Nachweiszeile kein Erfolg (Exit ≠ 0 auch nach sonst gelungenem Lauf).
			[[ $rc -ne 0 ]] || rc=1
		fi
	fi
	if [[ -n $STUFE ]]; then rm -rf -- "$STUFE"; fi
	if [[ -n $ARBEIT ]]; then rm -rf -- "$ARBEIT"; fi
	exit "$rc"
}
trap beenden EXIT

# Abgelehntes Release: eine Zeile `… sicherung=- artefakte=- ergebnis=abgelehnt grund=<regel>`
# (Version und Quellcommit nur, wenn die Signatur schon geprüft war), dann Abbruch. Außer dem
# Update-Protokoll ist nichts verändert.
ablehnen() {
	local grund=unbekannt
	[[ $1 =~ ^[a-z-]+$ ]] && grund=$1
	ERGEBNIS=abgelehnt
	SICHERUNG=-
	ARTEFAKTE=-
	HINWEIS=" grund=$grund"
	protokollieren || echo "Fehler: Update-Protokoll $PROTOKOLL nicht geschrieben" >&2
	fehler "Release abgelehnt ($grund, Meldung oben) — nichts verändert außer dem Update-Protokoll."
}

if [[ $MODUS == release ]]; then
	[[ -x $SSH_KEYGEN ]] || fehler "$SSH_KEYGEN fehlt (apt install openssh-client)."
	[[ -x $PYTHON ]] || fehler "$PYTHON fehlt."
	[[ -f $SKRIPT_ORDNER/release_pruefen.py ]] || fehler "$SKRIPT_ORDNER/release_pruefen.py fehlt."
	[[ -f $ALLOWED_SIGNERS && ! -L $ALLOWED_SIGNERS ]] ||
		fehler "$ALLOWED_SIGNERS fehlt — einmalig vom Betreiber installieren und den Fingerabdruck prüfen (docs/public/betrieb.md)."
	echo "== 0. Release prüfen und entpacken ($2)"
	ARBEIT=$(mktemp -d /var/tmp/scanview-release.XXXXXXXX)
	VERSION=-   # erst nach gültiger Signatur bekannt
	rc=0
	felder=$("$PYTHON" -I -B "$SKRIPT_ORDNER/release_pruefen.py" entpacken "$2" "$ALLOWED_SIGNERS" "$ARBEIT/ziel") || rc=$?
	REPO=
	GRUND=
	while IFS='=' read -r schluessel wert; do
		case $schluessel in
		version) VERSION=$wert ;;
		manifest_sha256) MANIFEST_SHA=$wert ;;
		quellcommit) QUELLCOMMIT=$wert ;;
		artefakte) ARTEFAKTE=$wert ;;
		baum) REPO=$wert ;;
		grund) GRUND=$wert ;;
		esac
	done <<<"$felder"
	[[ $rc -eq 0 ]] || ablehnen "${GRUND:-pruefung}"
	[[ -n $REPO && $VERSION != - && $MANIFEST_SHA != - && $QUELLCOMMIT != - && $ARTEFAKTE != - ]] ||
		ablehnen pruefausgabe
	echo "   Release $VERSION geprüft (Manifest $MANIFEST_SHA, Quellcommit $QUELLCOMMIT)."
else
	REPO=$(cd "$1" && pwd)
	echo "WARNUNG: kein geprüftes Release — $REPO wird ohne Signatur- und Prüfsummenprüfung installiert (nur für lokale Tests)." >&2
	HINWEIS=" WARNUNG: kein geprüftes Release quelle=$REPO"
	QUELLCOMMIT=$(git -C "$REPO" rev-parse HEAD 2>/dev/null || true)
	QUELLCOMMIT=${QUELLCOMMIT:--}
fi
HIER="$REPO/deploy/platform"

[[ -f $REPO/server/uv.lock ]] || fehler "$REPO/server/uv.lock fehlt — ist das der Repo-Stand?"
[[ -f $REPO/web/dist/index.html && -f $REPO/web/dist/portal.html ]] ||
	fehler "$REPO/web/dist ist nicht gebaut (cd web && npm ci && npm run build)."
for f in scanview-server.service scanview-backup.service scanview-backup.timer \
	scanview-git-export.service scanview-git-export.timer scanview-server.env.beispiel \
	scanview-paket-freischalten sudoers.d/scanview-deploy; do
	[[ -f $HIER/$f ]] || fehler "$HIER/$f fehlt."
done

[[ -x $PYTHON ]] || fehler "$PYTHON fehlt (Integritätsprüfung der Update-Sicherung)."
for cmd in setfacl rsync curl sqlite3 git runuser; do
	command -v "$cmd" >/dev/null || fehler "$cmd fehlt (apt install acl rsync curl sqlite3 git)."
done
command -v uv >/dev/null ||
	fehler "uv fehlt im PATH von root. uv bewusst installieren (gepinnte Version, Prüfsumme), dann erneut starten."
id caddy >/dev/null 2>&1 || fehler "Nutzer caddy fehlt — Caddy muss auf diesem Host laufen (liest Pakete und web/dist)."
# JOBLAEUFER_BEGIN
# Jobläufer: Schalter und Vorbedingungen — nur lesen, nichts verändern. Ist der Schalter an und
# fehlt etwas, bricht der ganze Lauf hier ab (laut, vor der Sicherung): lieber kein Update als ein
# Host, auf dem Aufträge still liegen bleiben oder ohne Grenzen rechnen.
JL_ENV=/etc/scanview/scanview-joblaeufer.env
JL_NUTZER=scanview-import
JL_GRUPPE=scanview-jobs
JL_HOME=/var/lib/scanview-import
JL_JOBS=/srv/scanview/jobs
JL_BERICHTE=/srv/scanview/berichte
JL_UNITS=/etc/systemd/system
JL_UNIT=$JL_UNITS/scanview-import-worker.service
JL_SYSTEMD_MIN=257   # PrivatePIDs in den Auftrags-Units
JL_CGROUP=/sys/fs/cgroup
JL_MEMINFO=/proc/meminfo
JL_DBUS_SOCKET=/usr/lib/systemd/user/dbus.socket   # Paket dbus-user-session
JL_LINGER=/var/lib/systemd/linger/$JL_NUTZER
# Import-Umgebung (legt dieses Skript selbst an): uv-verwaltetes Python in fester Version unter
# /opt/scanview/python, Umgebung aus dem gelockten import/uv.lock unter /opt/scanview/import.
JL_IMPORT=/opt/scanview/import
JL_IMPORT_BIN=$JL_IMPORT/.venv/bin
JL_PYTHON_DIR=/opt/scanview/python
JL_PYTHON_VERSION=3.12.13
JL_BAU_NUTZER=nobody
# Import neuer Projekte (Upload im Browser): zweiter Schalter, nur zusammen mit dem Jobläufer.
JL_UPLOAD_GRUPPE=scanview-upload
JL_UEBERGABE_NUTZER=scanview-uebergabe
JL_UPLOADS=/srv/scanview/uploads
JL_UEBERGABE=/srv/scanview/uebergabe
JL_MELDUNGEN=/srv/scanview/uebergabe-meldungen
JL_UEBERGABE_SKRIPT=/usr/local/sbin/scanview-import-uebergabe
for f in scanview-import-worker.service scanview-joblaeufer.env.beispiel \
	scanview-server.service.d/joblaeufer.conf scanview-backup.service.d/joblaeufer.conf \
	import-bau-einschraenkungen.txt scanview-import-uebergabe scanview-import-uebergabe.service \
	scanview-import-uebergabe.timer scanview-server.service.d/import.conf \
	scanview-import-worker.service.d/import.conf; do
	[[ -f $HIER/$f ]] || fehler "$HIER/$f fehlt."
done
# Letzter Wert NAME=… aus $JL_ENV; Anführungszeichen und Leerraum ringsum wie systemd entfernt.
jl_wert() {
	local zeile
	zeile=$(grep -E "^[[:space:]]*$1=" "$JL_ENV" 2>/dev/null | tail -n 1) || true
	zeile=${zeile#*=}
	printf '%s' "$zeile" | sed -E "s/^[[:space:]]+//; s/[[:space:]]+\$//; s/^\"(.*)\"\$/\\1/; s/^'(.*)'\$/\\1/"
}
[[ ! -L $JL_ENV ]] || fehler "$JL_ENV ist ein Symlink — nichts verändert."
JOBLAEUFER=0
JL_IMPORT_AN=0
if [[ -f $JL_ENV && $(jl_wert SCANVIEW_JOBLAEUFER) == 1 ]]; then
	JOBLAEUFER=1
fi
if [[ $JOBLAEUFER -eq 1 ]]; then
	echo "== 0. Jobläufer: Vorbedingungen"
	jl_systemd=$(systemctl --version 2>/dev/null | sed -nE '1s/^systemd ([0-9]+)([^0-9].*)?$/\1/p') || true
	if [[ ! $jl_systemd =~ ^[0-9]+$ ]] || ((10#$jl_systemd < JL_SYSTEMD_MIN)); then
		fehler "Jobläufer: systemd ${jl_systemd:-unbekannt} — nötig ≥ $JL_SYSTEMD_MIN (Debian 13). Host aktualisieren oder SCANVIEW_JOBLAEUFER=0 in $JL_ENV. Nichts verändert."
	fi
	# cgroup v2: nur die vereinheitlichte Hierarchie hat cgroup.controllers in der Wurzel.
	if [[ ! -f $JL_CGROUP/cgroup.controllers ]] || ! grep -qw memory "$JL_CGROUP/cgroup.controllers"; then
		fehler "Jobläufer: cgroup v2 mit Controller memory fehlt ($JL_CGROUP/cgroup.controllers) — ohne ihn greift keine RAM-Grenze. Nichts verändert."
	fi
	command -v loginctl >/dev/null ||
		fehler "Jobläufer: loginctl fehlt (systemd-logind, für Linger von $JL_NUTZER). Nichts verändert."
	[[ -e $JL_DBUS_SOCKET ]] ||
		fehler "Jobläufer: $JL_DBUS_SOCKET fehlt (apt install dbus-user-session) — systemd-run --user --wait braucht den Bus des User-Managers. Nichts verändert."
	jl_ram=$(jl_wert SCANVIEW_JOBS_RAM_GB)
	jl_ram=${jl_ram:-2}
	if [[ ! $jl_ram =~ ^[0-9]{1,2}$ ]] || ((10#$jl_ram < 2 || 10#$jl_ram > 40)); then
		fehler "Jobläufer: SCANVIEW_JOBS_RAM_GB muss eine ganze Zahl von 2 bis 40 sein (Prüfen braucht 2). Nichts verändert."
	fi
	jl_ram=$((10#$jl_ram))
	jl_reserve=$(jl_wert SCANVIEW_JOBS_RESERVE_GB)
	[[ -z $jl_reserve || $jl_reserve =~ ^[0-9]{1,4}$ ]] ||
		fehler "Jobläufer: SCANVIEW_JOBS_RESERVE_GB muss eine ganze Zahl ab 0 sein. Nichts verändert."
	# Gesamtspeicher statt „frei“: beim Update rechnet womöglich gerade ein Auftrag. Budget plus
	# 1 GB für Webdienst (512 MB), Worker, User-Manager und System. Im LXC zeigt /proc/meminfo die
	# Grenze des Containers.
	jl_kb=$(awk '$1 == "MemTotal:" {print $2}' "$JL_MEMINFO" 2>/dev/null) || true
	if [[ ! $jl_kb =~ ^[0-9]+$ ]] || ((jl_kb < (jl_ram + 1) * 1024 * 1024)); then
		fehler "Jobläufer: zu wenig Arbeitsspeicher — MemTotal ${jl_kb:-?} kB, nötig $((jl_ram + 1)) GB (SCANVIEW_JOBS_RAM_GB=$jl_ram plus 1 GB). Nichts verändert."
	fi
	# Die Units geben genau diese Pfade frei; andere Werte liefen in „Read-only file system“.
	[[ $(jl_wert SCANVIEW_JOBS_DB) == "$JL_JOBS/jobs.db" && $(jl_wert SCANVIEW_BERICHTE) == "$JL_BERICHTE" ]] ||
		fehler "Jobläufer: in $JL_ENV muss SCANVIEW_JOBS_DB=$JL_JOBS/jobs.db und SCANVIEW_BERICHTE=$JL_BERICHTE stehen. Nichts verändert."
	# Import-Umgebung: legt dieses Skript an (Schritt 7a); hier nur, ob alles dafür da ist.
	[[ $(jl_wert SCANVIEW_IMPORT_BIN) == "$JL_IMPORT_BIN" ]] ||
		fehler "Jobläufer: in $JL_ENV muss SCANVIEW_IMPORT_BIN=$JL_IMPORT_BIN stehen (einrichten.sh legt die Import-Umgebung dort an). Nichts verändert."
	[[ -f $REPO/import/uv.lock && -f $REPO/import/pyproject.toml ]] ||
		fehler "Jobläufer: $REPO/import/uv.lock bzw. pyproject.toml fehlt — ohne Import-Code keine Import-Umgebung. Nichts verändert."
	id "$JL_BAU_NUTZER" >/dev/null 2>&1 ||
		fehler "Jobläufer: Nutzer $JL_BAU_NUTZER fehlt (baut die Import-Umgebung ohne root-Rechte). Nichts verändert."
	JL_RAM_MB=$((jl_ram * 1024 + 256))
	# Import neuer Projekte: Pflichtwert Quote (kein Standard — der Betreiber entscheidet), feste
	# Pfade (die Units geben genau sie frei).
	jl_import=$(jl_wert SCANVIEW_IMPORT)
	[[ -z $jl_import || $jl_import == 0 || $jl_import == 1 ]] ||
		fehler "Jobläufer: SCANVIEW_IMPORT muss 0 oder 1 sein. Nichts verändert."
	if [[ $jl_import == 1 ]]; then
		JL_IMPORT_AN=1
		jl_quote=$(jl_wert SCANVIEW_UPLOAD_QUOTE_GB)
		if [[ ! $jl_quote =~ ^[0-9]{1,6}$ ]] || ((10#$jl_quote < 1)); then
			fehler "Import: SCANVIEW_UPLOAD_QUOTE_GB fehlt oder ist keine ganze Zahl ab 1 — Pflicht bei SCANVIEW_IMPORT=1, ohne Standard (Platz für Uploads, GB). Nichts verändert."
		fi
		# Die Pfade setzen die Drop-ins fest; ein Wert in der Datei hätte Vorrang (EnvironmentFile).
		for v in SCANVIEW_UPLOADS SCANVIEW_UEBERGABE SCANVIEW_UEBERGABE_MELDUNGEN; do
			! grep -qE "^[[:space:]]*$v=" "$JL_ENV" ||
				fehler "Import: $v nicht in $JL_ENV setzen (fester Pfad aus den Drop-ins). Nichts verändert."
		done
		# Der Übergabe-Nutzer ist für das Freischalt-Skript der „Hochladende“: nie in einer Gruppe,
		# die Pakete, Uploads oder Aufträge erreicht.
		if id "$JL_UEBERGABE_NUTZER" >/dev/null 2>&1; then
			for g in $(id -nG -- "$JL_UEBERGABE_NUTZER"); do
				case $g in
				scanview | "$JL_UPLOAD_GRUPPE" | "$JL_GRUPPE" | "$JL_NUTZER")
					fehler "Import: $JL_UEBERGABE_NUTZER ist Mitglied von $g — nicht erlaubt (nur $JL_UEBERGABE_NUTZER und scanview-deploy; gpasswd -d $JL_UEBERGABE_NUTZER $g). Nichts verändert." ;;
				esac
			done
		fi
		echo "   Import an: Quote $jl_quote GB."
	fi
	[[ $JL_IMPORT_AN -eq 1 ]] || echo "   Import neuer Projekte aus (SCANVIEW_IMPORT=1 in $JL_ENV schaltet ihn ein)."
	echo "   systemd $jl_systemd, cgroup v2 mit memory, ${jl_kb} kB Arbeitsspeicher, Budget $jl_ram GB."
fi
# Nach dem Start des Webdienstes: meldet der Worker sich in /api/gesundheit als „aktiv“? Er läuft
# zuerst seinen Selbsttest (Netz, Bus, Schreiben außerhalb, RAM-Grenze); scheitert der, endet er
# mit Exit 3 und startet nicht neu.
joblaeufer_aktiv() {
	local antwort
	for _ in $(seq 1 60); do
		antwort=$(curl -fsS --max-time 5 http://127.0.0.1:8803/api/gesundheit 2>/dev/null) || antwort=
		if grep -qE '"worker"[[:space:]]*:[[:space:]]*"aktiv"' <<<"$antwort"; then
			echo "   Jobläufer aktiv (Selbsttest bestanden)."
			return 0
		fi
		sleep 1
	done
	systemctl --no-pager status scanview-import-worker.service || true
	fehler "Jobläufer meldet sich nicht als aktiv — journalctl -u scanview-import-worker prüfen (Selbsttest der Isolation?)."
}
# JOBLAEUFER_END

echo "== 0. Sicherung vor dem Update"
# Update oder Ersteinrichtung? Ein fehlender Mount oder verlorener Datenbestand darf nie als
# „Ersteinrichtung“ durchgehen: sobald irgendein Rest einer Installation da ist, ist das ein
# Update — und ein Update braucht die Datenbank (regulär, kein Symlink).
UPDATE=0
for spur in "${SPUREN[@]}"; do
	if [[ -e $spur || -L $spur ]]; then UPDATE=1; fi
done
FORTSETZUNG=0
if [[ $UPDATE -eq 1 && ! -e $DB && ! -L $DB && -f $MARKER && ! -L $MARKER &&
	$(stat -c %u -- "$MARKER") == 0 && ! -e $INSTALLIERT && ! -L $INSTALLIERT &&
	! -e $INSTALL_STATUS_ORDNER && ! -L $INSTALL_STATUS_ORDNER ]]; then
	# Abgebrochene Ersteinrichtung (Marker von root, noch keine Datenbank): fortsetzen.
	UPDATE=0
	FORTSETZUNG=1
	HINWEIS="$HINWEIS ersteinrichtung=fortgesetzt"
	echo "   abgebrochene Ersteinrichtung wird fortgesetzt ($MARKER)."
fi
if [[ $UPDATE -eq 1 ]]; then
	if [[ ! -f $DB || -L $DB ]]; then
		# Nachweisrelevant (fehlender Mount, verlorener Bestand): eine Zeile ins Update-Protokoll
		# (liegt außerhalb des Dienstbaums), dann Abbruch — sonst nichts verändert.
		ERGEBNIS=abgebrochen
		HINWEIS=" grund=db-fehlt$HINWEIS"
		protokollieren || echo "Fehler: Update-Protokoll $PROTOKOLL nicht geschrieben" >&2
		fehler "vorhandene Installation, aber $DB fehlt oder ist keine reguläre Datei — Update abgebrochen, nichts verändert außer dem Update-Protokoll. Mount und Datenbestand prüfen (docs/public/betrieb.md, Abschnitt Installation ohne Datenbank); eine bewusste Neuinstallation erst nach Entfernen aller Reste: ${SPUREN[*]}."
	fi
	id scanview >/dev/null 2>&1 || fehler "$DB existiert, aber Nutzer scanview fehlt."
fi
# Protokollziel vor der ersten Änderung prüfen: ohne schreibbares Protokoll kein Update.
protokoll_bereit || fehler "Update-Protokoll $PROTOKOLL nicht schreibbar — nichts verändert."
VERAENDERT=1
if [[ $UPDATE -eq 1 ]]; then
	# Ablage außerhalb des Dienstbaums: /var/backups/scanview (root, 0711 — der Dienstnutzer darf
	# nur hindurch) und darin update-sicherungen (root, 0700). root legt nur hier an und folgt
	# keinem Pfad unter $STATE. sqlite3 läuft als scanview (Online-Backup, auch bei laufendem
	# Dienst) und schreibt in einen frischen Stufenordner, den nur scanview beschreiben kann; root
	# holt die Datei per rename in die root-Ablage und prüft sie DORT (regulär, ein Name).
	for d in "$SICHERUNGEN_WURZEL" "$SICHERUNGEN"; do
		[[ ! -L $d ]] || fehler "$d ist ein Symlink."
	done
	install -d -o root -g root -m 0711 "$SICHERUNGEN_WURZEL"
	install -d -o root -g root -m 0700 "$SICHERUNGEN"
	ziel="$SICHERUNGEN/$(date -u +%Y%m%dT%H%M%SZ)-${VERSION/ungeprüft/repo}.db"
	[[ ! -e $ziel && ! -L $ziel ]] || fehler "$ziel existiert schon."
	STUFE=$(mktemp -d "$SICHERUNGEN_WURZEL/.stufe.XXXXXXXX")
	chown scanview:scanview "$STUFE"
	chmod 0700 "$STUFE"
	(umask 0077 && runuser -u scanview -- sqlite3 "$DB" ".backup '$STUFE/sicherung.db'")
	# rename(2) verschiebt den Eintrag selbst (auch einen untergeschobenen Symlink) in die
	# root-Ablage; dort nur Typ und Namenszahl (nichts folgt einem Link).
	roh="$ziel.roh"
	mv -T -- "$STUFE/sicherung.db" "$roh"
	if [[ ! -f $roh || -L $roh || $(stat -c %h -- "$roh") != 1 ]]; then
		rm -rf -- "$roh"
		fehler "Sicherung ist keine reguläre Datei mit genau einem Namen — Update abgebrochen."
	fi
	# Neue, root-eigene Inode (nur Bytes kopiert, nichts geparst): ein Schreib-Deskriptor, den der
	# Dienst beim Backup offen hielt, zeigt auf die alte Inode und erreicht die Sicherung nie.
	(umask 0077 && cp --no-preserve=all -- "$roh" "$ziel")
	rm -f -- "$roh"
	chown root:root "$ziel"
	chmod 0600 "$ziel"
	# GENAU diese Kopie prüfen — nie als root und nicht als Dienst (ein kompromittierter Dienst
	# könnte einen Prüfprozess seiner eigenen uid beeinflussen), sondern als nobody: root reicht
	# nur die Bytes über stdin weiter, python3 prüft sie im Speicher (braucht etwa das Dreifache
	# der Datenbankgröße an freiem Arbeitsspeicher).
	pruefung=$(runuser -u nobody -- "$PYTHON" -I -c "$SICHERUNG_PRUEFEN" <"$ziel") || pruefung="nicht ausführbar"
	if [[ $pruefung != ok ]]; then
		# Nie unter dem regulären Sicherungsnamen liegen lassen (Rückweg würde sie sonst anbieten).
		mv -T -- "$ziel" "$ziel.ungueltig"
		fehler "Sicherung fehlerhaft ($pruefung) — als $ziel.ungueltig abgelegt, Update abgebrochen."
	fi
	rm -rf -- "$STUFE"
	STUFE=
	SICHERUNG=$ziel
	echo "   $SICHERUNG"
else
	SICHERUNG=keine
	[[ $FORTSETZUNG -eq 1 ]] || echo "   kein Rest einer Installation (Ersteinrichtung)."
	# Marker vor der ersten Änderung an der Installation setzen (root, 0600); er verschwindet
	# erst nach erfolgreichem Lauf.
	install -d -o root -g root -m 0755 /etc/scanview
	[[ ! -L $MARKER ]] || fehler "$MARKER ist ein Symlink."
	(umask 0077 && date -u +%Y-%m-%dT%H:%M:%SZ >"$MARKER")
fi

echo "== 1. Nutzer"
id scanview >/dev/null 2>&1 || useradd --system --user-group --home /nonexistent --shell /usr/sbin/nologin scanview
# Deploy-Gruppe: darf in den Eingang hochladen und per sudo nur das Freischalt-Skript starten.
# Mitglieder trägt der Betreiber von Hand ein (usermod -aG scanview-deploy <deploy-nutzer>).
getent group scanview-deploy >/dev/null || groupadd --system scanview-deploy
# Deploy-Nutzer nie zugleich in der Gruppe scanview: die erreicht die Datenwurzel (0750) und damit
# den Quarantäneordner des Freischalt-Skripts (das prüft es beim Freischalten ebenfalls).
for mitglied in $(getent group scanview-deploy | cut -d: -f4 | tr ',' ' '); do
	if id -nG -- "$mitglied" 2>/dev/null | tr ' ' '\n' | grep -qx scanview; then
		fehler "$mitglied ist in scanview-deploy UND scanview — aus scanview entfernen (gpasswd -d $mitglied scanview)."
	fi
done

echo "== 2. Verzeichnisse"
install -d -o root -g scanview -m 0750 /srv/scanview/data
install -d -o scanview -g scanview -m 0700 /srv/scanview/state
# Eingang für Paket-Uploads: außerhalb der Datenwurzel (Caddy und Katalog sehen ihn nicht), auf
# demselben Dateisystem (das Freischalt-Skript verschiebt per rename). 1770: die Gruppe lädt hoch,
# das Sticky-Bit hindert jeden Deploy-Nutzer daran, den Upload eines anderen umzubenennen oder
# zu löschen (mehrere Deploy-Nutzer je Host).
[[ ! -L /srv/scanview/eingang ]] || fehler "/srv/scanview/eingang ist ein Symlink."
install -d -o root -g scanview-deploy -m 1770 /srv/scanview/eingang
install -d -m 0755 /opt/scanview /opt/scanview/web /opt/scanview/server
install -d -m 0755 /etc/scanview
# Meldeweg (security.txt, Richtlinie, öffentlicher Schlüssel): Caddy liefert daraus öffentlich aus.
# Wird leer angelegt und hier NIE befüllt — die Dateien kommen erst mit eingerichtetem Postfach
# und von Hand des Betreibers (docs/public/betrieb.md, „Meldeweg: security.txt“). Leer → 404.
install -d -o root -g root -m 0755 /etc/scanview/meldeweg

echo "== 3. Code kopieren"
# .venv bleibt stehen (--exclude schützt es vor --delete); Caches aus dem Arbeitsstand nicht mitnehmen.
rsync -a --delete \
	--exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache \
	"$REPO/server/" /opt/scanview/server/
rsync -a --delete "$REPO/web/dist/" /opt/scanview/web/
chown -R root:root /opt/scanview/server /opt/scanview/web
chmod -R u+rwX,go=rX /opt/scanview/server /opt/scanview/web

echo "== 4. Caddy darf lesen (ACL statt Gruppe)"
# Kein `usermod -aG scanview caddy`: neue Gruppen wirken erst nach einem Caddy-NEUSTART, der auf
# diesem Host die übrigen Portale kurz unterbricht. ACLs wirken sofort. Nach dem Kopieren, damit
# rsync die Maske nicht wieder verengt.
setfacl -R -m u:caddy:rX -m d:u:caddy:rX /srv/scanview/data /opt/scanview/web

echo "== 5. Python-Umgebung (Debian-Python, gesperrte Abhängigkeiten)"
(cd /opt/scanview/server && UV_PYTHON_PREFERENCE=only-system uv sync --locked --no-dev)
chmod -R go=rX /opt/scanview/server/.venv

echo "== 6. Umgebung"
if [[ ! -e /etc/scanview/scanview-server.env ]]; then
	install -o root -g scanview -m 0640 "$HIER/scanview-server.env.beispiel" /etc/scanview/scanview-server.env
	echo "   /etc/scanview/scanview-server.env aus Beispiel angelegt — SCANVIEW_ORIGIN/SCANVIEW_RP_ID prüfen."
else
	echo "   /etc/scanview/scanview-server.env existiert, bleibt unverändert."
fi

echo "== 7. systemd"
install -m 0644 "$HIER/scanview-server.service" /etc/systemd/system/scanview-server.service
install -m 0644 "$HIER/scanview-backup.service" /etc/systemd/system/scanview-backup.service
install -m 0644 "$HIER/scanview-backup.timer" /etc/systemd/system/scanview-backup.timer
install -m 0644 "$HIER/scanview-git-export.service" /etc/systemd/system/scanview-git-export.service
install -m 0644 "$HIER/scanview-git-export.timer" /etc/systemd/system/scanview-git-export.timer
# Ab dem ersten Dienststart kann es eine Datenbank geben: Installationsstatus setzen (nie wieder
# Ersteinrichtung). Symlinks an Ordner oder Datei → Abbruch.
[[ ! -L $INSTALL_STATUS_ORDNER && ! -L $INSTALLIERT ]] || fehler "$INSTALL_STATUS_ORDNER bzw. $INSTALLIERT ist ein Symlink."
install -d -o root -g root -m 0700 "$INSTALL_STATUS_ORDNER"
if [[ ! -e $INSTALLIERT ]]; then
	(umask 0077 && date -u +%Y-%m-%dT%H:%M:%SZ >"$INSTALLIERT")
fi
[[ -f $INSTALLIERT && ! -L $INSTALLIERT && $(stat -c %u -- "$INSTALLIERT") == 0 ]] ||
	fehler "$INSTALLIERT ist keine root-eigene reguläre Datei."
# JOBLAEUFER_BEGIN
echo "== 7a. Jobläufer"
if [[ ! -e $JL_ENV && ! -L $JL_ENV ]]; then
	# root:root 0640: nur systemd (EnvironmentFile) und dieses Skript lesen sie.
	install -o root -g root -m 0640 "$HIER/scanview-joblaeufer.env.beispiel" "$JL_ENV"
	echo "   $JL_ENV aus Beispiel angelegt (SCANVIEW_JOBLAEUFER=0)."
fi
JL_DROPINS=("$JL_UNITS/scanview-server.service.d/joblaeufer.conf"
	"$JL_UNITS/scanview-backup.service.d/joblaeufer.conf"
	"$JL_UNITS/scanview-import-worker.service.d/laufzeit.conf")
# Import neuer Projekte: Units und Drop-ins, die nur mit SCANVIEW_IMPORT=1 liegen.
JL_IMPORT_UNITS=("$JL_UNITS/scanview-import-uebergabe.service" "$JL_UNITS/scanview-import-uebergabe.timer"
	"$JL_UNITS/scanview-server.service.d/import.conf" "$JL_UNITS/scanview-import-worker.service.d/import.conf")
# Import ausschalten: Timer anhalten, Units, Drop-ins und Root-Skript weg, Gruppe des Workers
# entziehen; Uploads, Übergabe und Nutzer bleiben (Daten löscht nur der Betreiber).
import_ausschalten() {
	if [[ -e ${JL_IMPORT_UNITS[0]} || -e ${JL_IMPORT_UNITS[1]} || -e $JL_UEBERGABE_SKRIPT ]]; then
		systemctl disable --now scanview-import-uebergabe.timer 2>/dev/null || true
		systemctl stop scanview-import-uebergabe.service 2>/dev/null || true
		rm -f -- "${JL_IMPORT_UNITS[@]}" "$JL_UEBERGABE_SKRIPT"
		echo "   Import ausgeschaltet: Übergabe-Timer, Units und Drop-ins entfernt (Uploads bleiben)."
	fi
	if id -nG -- "$JL_NUTZER" 2>/dev/null | tr ' ' '\n' | grep -qx "$JL_UPLOAD_GRUPPE"; then
		gpasswd -d "$JL_NUTZER" "$JL_UPLOAD_GRUPPE" >/dev/null
	fi
}
if [[ $JOBLAEUFER -eq 1 ]]; then
	# Rechte (so eng wie möglich; Tabelle auch in deploy/platform/README.md „Jobläufer“):
	# - scanview-import: Systemnutzer ohne Login, eigene Gruppe; Home nur für den User-Manager.
	# - Mitglied in scanview: liest die Pakete wie der Webdienst (/srv/scanview/data root:scanview
	#   0750, Dateien 0640) — schreiben kann dort nur root (Freischalt-Skript unverändert).
	# - Mitglied in scanview-jobs: gemeinsam mit dem Webdienst (der die Gruppe nur per Drop-in
	#   bekommt, nicht in /etc/group) auf jobs.db und die Berichte.
	getent group "$JL_GRUPPE" >/dev/null || groupadd --system "$JL_GRUPPE"
	if ! id "$JL_NUTZER" >/dev/null 2>&1; then
		useradd --system --user-group --home-dir "$JL_HOME" --no-create-home \
			--shell /usr/sbin/nologin "$JL_NUTZER"
	fi
	usermod -a -G "scanview,$JL_GRUPPE" "$JL_NUTZER"
	JL_UID=$(id -u "$JL_NUTZER")
	[[ $JL_UID =~ ^[0-9]+$ && $JL_UID -ne 0 ]] || fehler "$JL_NUTZER hat keine gültige uid ($JL_UID)."
	# Nie Deploy-Nutzer: der erreicht sonst über scanview die Datenwurzel (wie oben bei Schritt 1).
	if id -nG -- "$JL_NUTZER" 2>/dev/null | tr ' ' '\n' | grep -qx scanview-deploy; then
		fehler "$JL_NUTZER ist in scanview-deploy UND scanview — aus scanview-deploy entfernen."
	fi
	for d in "$JL_HOME" "$JL_JOBS" "$JL_BERICHTE" "$JL_JOBS/jobs.db"; do
		[[ ! -L $d ]] || fehler "$d ist ein Symlink."
	done
	# Home 0700: Zustand des User-Managers, sonst niemand.
	install -d -o "$JL_NUTZER" -g "$JL_NUTZER" -m 0700 "$JL_HOME"
	# jobs/ 3770 root:scanview-jobs: beide Dienste legen dort -wal/-shm an (setgid hält die
	# Gruppe), Fremde nichts. Sticky und root als Eigentümer von Ordner und jobs.db: keiner der
	# beiden Dienste kann jobs.db umbenennen oder durch einen Symlink ersetzen (SQLite folgt einem
	# Symlink an der Datenbankdatei — gemessen; so könnte der Worker die Schreibzugriffe des
	# Webdienstes auf dessen scanview.db umlenken).
	install -d -o root -g "$JL_GRUPPE" -m 3770 "$JL_JOBS"
	# berichte/ 2750 scanview-import:scanview-jobs: nur der Worker schreibt (Ordner 0750, Dateien
	# 0640 dank UMask 0027), der Webdienst liest über die Gruppe; setgid vererbt sie an alles darin.
	install -d -o "$JL_NUTZER" -g "$JL_GRUPPE" -m 2750 "$JL_BERICHTE"
	# jobs.db root:scanview-jobs 0660 (Gruppe aus dem setgid-Ordner). Ohne Vorab-Anlage hätte sie
	# 0600 von dem, der zuerst öffnet. noclobber öffnet mit O_EXCL: ein untergeschobener Symlink
	# lässt das Anlegen scheitern, statt dass root ihm folgt.
	if [[ ! -e $JL_JOBS/jobs.db && ! -L $JL_JOBS/jobs.db ]]; then
		(umask 0117 && set -C && : >"$JL_JOBS/jobs.db")
	fi
	[[ -f $JL_JOBS/jobs.db && ! -L $JL_JOBS/jobs.db &&
		$(stat -c '%U:%G %a %h' -- "$JL_JOBS/jobs.db") == "root:$JL_GRUPPE 660 1" ]] ||
		fehler "$JL_JOBS/jobs.db: erwartet root:$JL_GRUPPE 0660, ein Name — ist $(stat -c '%U:%G %a %h' -- "$JL_JOBS/jobs.db" 2>/dev/null || echo fehlt). Von Hand prüfen, nichts geändert."
	install -d -m 0755 "$JL_UNITS/scanview-server.service.d" "$JL_UNITS/scanview-backup.service.d" \
		"$JL_UNITS/scanview-import-worker.service.d" "$JL_UNITS/user@$JL_UID.service.d"
	install -m 0644 "$HIER/scanview-server.service.d/joblaeufer.conf" "${JL_DROPINS[0]}"
	install -m 0644 "$HIER/scanview-backup.service.d/joblaeufer.conf" "${JL_DROPINS[1]}"
	install -m 0644 "$HIER/scanview-import-worker.service" "$JL_UNIT"
	# uid-abhängig, darum hier geschrieben (%U wäre in einer System-Unit 0): Weg zum User-Manager.
	(umask 0022 && printf '%s\n' \
		'# Von einrichten.sh geschrieben: Weg des Workers zu seinem User-Manager (Linger).' \
		'[Unit]' "Requires=user@$JL_UID.service" "After=user@$JL_UID.service" \
		'[Service]' "Environment=XDG_RUNTIME_DIR=/run/user/$JL_UID" \
		"BindReadOnlyPaths=/run/user/$JL_UID" >"${JL_DROPINS[2]}")
	# RAM aller Aufträge zusammen: sie laufen im User-Manager, nicht in der Worker-Unit.
	(umask 0022 && printf '%s\n' \
		'# Von einrichten.sh geschrieben: RAM aller Aufträge des Jobläufers (SCANVIEW_JOBS_RAM_GB + 256 MB).' \
		'[Service]' "MemoryMax=${JL_RAM_MB}M" 'MemorySwapMax=0' \
		>"$JL_UNITS/user@$JL_UID.service.d/scanview-joblaeufer.conf")
	echo "   Nutzer $JL_NUTZER (uid $JL_UID), $JL_JOBS, $JL_BERICHTE, Unit und Drop-ins installiert."

	# Import-Umgebung: Code root-eigen nach /opt/scanview/import, dann baut uv als
	# $JL_BAU_NUTZER (ohne root-Rechte: Quellpakete führen beim Bauen fremden Code aus) eine frische
	# Umgebung mit uv-verwaltetem Python $JL_PYTHON_VERSION aus dem gelockten uv.lock (inkl. Extra
	# sonne, ohne Entwicklungswerkzeuge). Fremdpakete nur als Wheel (import/pyproject.toml
	# no-build-package), Bauabhängigkeiten fest (import-bau-einschraenkungen.txt). Geladen wird nur
	# hier; Aufträge laufen ohne Netz. Danach gehört alles root und ist für niemanden sonst
	# beschreibbar — der Worker startet daraus Prozesse und darf sie nicht verändern können.
	echo "   Import-Umgebung (Python $JL_PYTHON_VERSION, import/uv.lock)"
	for d in "$JL_IMPORT" "$JL_IMPORT/.venv" "$JL_PYTHON_DIR"; do
		[[ ! -L $d ]] || fehler "$d ist ein Symlink."
	done
	# Kein Auftrag soll aus einer halb gebauten Umgebung starten (der Worker startet unten neu).
	systemctl stop scanview-import-worker.service 2>/dev/null || true
	install -d -o root -g root -m 0755 "$JL_IMPORT"
	rsync -a --delete \
		--exclude .venv --exclude __pycache__ --exclude .pytest_cache --exclude .mypy_cache --exclude .ruff_cache \
		"$REPO/import/" "$JL_IMPORT/"
	chown -R root:root "$JL_IMPORT"
	chmod -R u+rwX,go=rX "$JL_IMPORT"
	rm -rf -- "$JL_IMPORT/.venv" "$JL_PYTHON_DIR"
	jl_bau=$(mktemp -d /var/tmp/scanview-import-bau.XXXXXXXX)
	chmod 0711 "$jl_bau"  # $JL_BAU_NUTZER darf hindurch (nicht auflisten)
	install -m 0644 "$HIER/import-bau-einschraenkungen.txt" "$jl_bau/einschraenkungen.txt"
	# uv selbst als Kopie: root findet es womöglich unter /root, das $JL_BAU_NUTZER nicht erreicht.
	install -m 0755 "$(command -v uv)" "$jl_bau/uv"
	install -d -o "$JL_BAU_NUTZER" -m 0700 "$jl_bau/cache" "$jl_bau/home"
	install -d -o "$JL_BAU_NUTZER" -m 0755 "$JL_IMPORT/.venv" "$JL_PYTHON_DIR"
	if ! (cd "$JL_IMPORT" && runuser -u "$JL_BAU_NUTZER" -- env -i PATH=/usr/bin:/bin \
		HOME="$jl_bau/home" UV_CACHE_DIR="$jl_bau/cache" UV_PYTHON_INSTALL_DIR="$JL_PYTHON_DIR" \
		UV_PYTHON_PREFERENCE=only-managed UV_PYTHON="$JL_PYTHON_VERSION" \
		UV_BUILD_CONSTRAINT="$jl_bau/einschraenkungen.txt" \
		"$jl_bau/uv" sync --locked --no-dev --extra sonne); then
		rm -rf -- "$jl_bau"
		fehler "Import-Umgebung: uv sync gescheitert (Netz beim Einrichten, uv.lock, Bau von meshoptimizer?) — Jobläufer nicht gestartet."
	fi
	rm -rf -- "$jl_bau"
	# chown/chmod folgen keinem Symlink (-R ohne -L); chown löscht setuid/setgid-Bits.
	chown -R root:root "$JL_IMPORT/.venv" "$JL_PYTHON_DIR"
	chmod -R u+rwX,go=rX "$JL_IMPORT/.venv" "$JL_PYTHON_DIR"
	# Prüfung danach: root-eigen, für niemanden sonst beschreibbar, ausführbarer Prüfer; der
	# Interpreter der Umgebung liegt im root-eigenen Python-Ordner.
	jl_py=$(readlink -f -- "$JL_IMPORT_BIN/python" 2>/dev/null) || jl_py=
	[[ -d $JL_IMPORT_BIN && ! -L $JL_IMPORT_BIN && -f $JL_IMPORT_BIN/scanview-pruefen && ! -L $JL_IMPORT_BIN/scanview-pruefen &&
		-x $JL_IMPORT_BIN/scanview-pruefen && $jl_py == "$JL_PYTHON_DIR"/* &&
		$(find "$JL_IMPORT" "$JL_IMPORT_BIN" "$JL_IMPORT_BIN/scanview-pruefen" "$JL_PYTHON_DIR" -maxdepth 0 -user 0 ! -perm /0022 -printf x 2>/dev/null) == xxxx &&
		-z $(find "$JL_IMPORT" "$JL_PYTHON_DIR" \( ! -user 0 -o -perm /0022 \) ! -type l -print -quit 2>/dev/null) ]] ||
		fehler "Jobläufer: SCANVIEW_IMPORT_BIN ($JL_IMPORT_BIN) muss ein root-eigener, nur für root beschreibbarer Ordner mit ausführbarem scanview-pruefen sein, Python unter $JL_PYTHON_DIR (Import-Umgebung, docs/public/betrieb.md, Abschnitt Jobläufer)."

	if [[ $JL_IMPORT_AN -eq 1 ]]; then
		# Rechte (Tabelle in deploy/platform/README.md „Import neuer Projekte“):
		# - scanview-upload: Webdienst (nur per Drop-in) schreibt Uploads, Worker liest sie.
		# - scanview-uebergabe: Systemnutzer ohne Login, nur in scanview-deploy. Er kopiert das
		#   fertige Paket aus der Übergabe in den Eingang und ist damit für das unveränderte
		#   Freischalt-Skript der Hochladende (nie in scanview: sonst bricht es ab).
		getent group "$JL_UPLOAD_GRUPPE" >/dev/null || groupadd --system "$JL_UPLOAD_GRUPPE"
		if ! id "$JL_UEBERGABE_NUTZER" >/dev/null 2>&1; then
			useradd --system --user-group --home-dir /nonexistent --no-create-home \
				--shell /usr/sbin/nologin "$JL_UEBERGABE_NUTZER"
		fi
		usermod -a -G scanview-deploy "$JL_UEBERGABE_NUTZER"
		JL_GRUPPEN_NEU=0
		if ! id -nG -- "$JL_NUTZER" | tr ' ' '\n' | grep -qx "$JL_UPLOAD_GRUPPE"; then
			usermod -a -G "$JL_UPLOAD_GRUPPE" "$JL_NUTZER"
			JL_GRUPPEN_NEU=1
		fi
		# Gegenprobe nach dem Anlegen (der Betreiber könnte Gruppen von Hand ergänzt haben).
		for g in $(id -nG -- "$JL_UEBERGABE_NUTZER"); do
			[[ $g == "$JL_UEBERGABE_NUTZER" || $g == scanview-deploy ]] ||
				fehler "$JL_UEBERGABE_NUTZER ist Mitglied von $g — erlaubt sind nur $JL_UEBERGABE_NUTZER und scanview-deploy."
		done
		for g in $(id -nG -- "$JL_NUTZER"); do
			[[ $g != "$JL_UEBERGABE_NUTZER" && $g != scanview-deploy ]] ||
				fehler "$JL_NUTZER ist Mitglied von $g — nicht erlaubt (der Worker schaltet nie selbst frei)."
		done
		for d in "$JL_UPLOADS" "$JL_UEBERGABE" "$JL_MELDUNGEN"; do
			[[ ! -L $d ]] || fehler "$d ist ein Symlink."
		done
		# uploads/ 2750 scanview:scanview-upload: der Webdienst schreibt (Ordner 2750, Dateien
		# 0640), der Worker liest über die Gruppe, sonst niemand. Außerhalb der Paketwurzel.
		install -d -o scanview -g "$JL_UPLOAD_GRUPPE" -m 2750 "$JL_UPLOADS"
		# uebergabe/ 2750 scanview-import:scanview-uebergabe: Worker und Aufträge schreiben, der
		# Übergabe-Nutzer liest über die Gruppe (setgid vererbt sie an alles darin).
		install -d -o "$JL_NUTZER" -g "$JL_UEBERGABE_NUTZER" -m 2750 "$JL_UEBERGABE"
		# uebergabe-meldungen/ 2750 scanview-uebergabe:scanview-import: ein Wort je Paket zurück.
		install -d -o "$JL_UEBERGABE_NUTZER" -g "$JL_NUTZER" -m 2750 "$JL_MELDUNGEN"
		install -o root -g root -m 0755 "$HIER/scanview-import-uebergabe" "$JL_UEBERGABE_SKRIPT"
		install -m 0644 "$HIER/scanview-import-uebergabe.service" "${JL_IMPORT_UNITS[0]}"
		install -m 0644 "$HIER/scanview-import-uebergabe.timer" "${JL_IMPORT_UNITS[1]}"
		install -m 0644 "$HIER/scanview-server.service.d/import.conf" "${JL_IMPORT_UNITS[2]}"
		install -m 0644 "$HIER/scanview-import-worker.service.d/import.conf" "${JL_IMPORT_UNITS[3]}"
		echo "   Import: $JL_UPLOADS, $JL_UEBERGABE, $JL_MELDUNGEN, Nutzer $JL_UEBERGABE_NUTZER, Übergabe-Timer installiert."
	else
		import_ausschalten
	fi
elif [[ -e $JL_UNIT || -e $JL_LINGER ]]; then
	# Ausschalten: Dienst anhalten, Unit, Drop-ins und Linger weg; Nutzer, jobs.db und Berichte
	# bleiben (Daten löscht nur der Betreiber).
	systemctl disable --now scanview-import-worker.service 2>/dev/null || true
	rm -f -- "$JL_UNIT" "${JL_DROPINS[@]}"
	import_ausschalten
	if id "$JL_NUTZER" >/dev/null 2>&1; then
		rm -f -- "$JL_UNITS/user@$(id -u "$JL_NUTZER").service.d/scanview-joblaeufer.conf"
		loginctl disable-linger "$JL_NUTZER" || true
	fi
	echo "   Jobläufer ausgeschaltet: Dienst, Unit, Drop-ins und Linger entfernt (Daten bleiben)."
else
	echo "   Jobläufer aus (SCANVIEW_JOBLAEUFER=1 in $JL_ENV schaltet ihn ein)."
fi
# JOBLAEUFER_END
systemctl daemon-reload
systemctl enable scanview-server.service scanview-backup.timer
# restart statt `enable --now`: bei einem erneuten Lauf soll der neue Code auch geladen werden.
systemctl restart scanview-server.service
systemctl start scanview-backup.timer
# JOBLAEUFER_BEGIN
if [[ $JOBLAEUFER -eq 1 ]]; then
	# Linger: der User-Manager von scanview-import läuft ohne Anmeldung, ab dem Systemstart.
	loginctl enable-linger "$JL_NUTZER"
	systemctl start "user@$JL_UID.service"
	# Neue Gruppe (scanview-upload) sieht der User-Manager erst nach einem Neustart — sonst lesen
	# die Aufträge die Uploads nicht.
	if [[ ${JL_GRUPPEN_NEU:-0} -eq 1 ]]; then systemctl restart "user@$JL_UID.service"; fi
	jl_bus=/run/user/$JL_UID/bus
	for _ in $(seq 1 20); do
		[[ -S $jl_bus ]] && break
		sleep 0.5
	done
	[[ -S $jl_bus ]] ||
		fehler "Jobläufer: $jl_bus fehlt — der User-Manager von $JL_NUTZER hat keinen Bus (dbus-user-session, systemctl status user@$JL_UID)."
	jl_cg=$JL_CGROUP/user.slice/user-$JL_UID.slice/user@$JL_UID.service
	grep -qw memory "$jl_cg/cgroup.controllers" 2>/dev/null ||
		fehler "Jobläufer: memory ist nicht an user@$JL_UID.service delegiert ($jl_cg/cgroup.controllers) — RAM-Grenzen der Aufträge würden nicht greifen (Delegate=, Container-Konfiguration prüfen)."
	[[ $(cat "$jl_cg/memory.max" 2>/dev/null) == $((JL_RAM_MB * 1024 * 1024)) ]] ||
		fehler "Jobläufer: RAM-Grenze von user@$JL_UID.service nicht wirksam ($jl_cg/memory.max ≠ ${JL_RAM_MB} MB)."
	systemctl enable scanview-import-worker.service
	# restart: ein erneuter Lauf lädt neuen Code; der Worker bricht einen laufenden Auftrag ab.
	systemctl restart scanview-import-worker.service
	if [[ $JL_IMPORT_AN -eq 1 ]]; then
		systemctl enable --now scanview-import-uebergabe.timer
	fi
fi
# JOBLAEUFER_END
# Git-Export: aus, bis SCANVIEW_GIT_EXPORT=1 in der Umgebung steht. Ein erneuter
# Lauf nach dem Ausschalten hält den Timer wieder an. Ob gepusht wird, entscheidet allein
# SCANVIEW_GIT_REMOTE (plus Netz-Drop-in, docs/public/betrieb.md) — dieses Skript pusht nie.
# Tolerant wie systemd beim Lesen der Datei: 1, "1" oder '1', Leerraum ringsum. Alles andere
# (auch fehlende Zeile) = aus (fail-closed).
if grep -qE "^[[:space:]]*SCANVIEW_GIT_EXPORT=(1|\"1\"|'1')[[:space:]]*$" /etc/scanview/scanview-server.env; then
	systemctl enable --now scanview-git-export.timer
	echo "   Git-Export aktiv (alle 5 min nach /srv/scanview/state/git)."
else
	systemctl disable --now scanview-git-export.timer 2>/dev/null || true
	echo "   Git-Export aus (SCANVIEW_GIT_EXPORT=1 in /etc/scanview/scanview-server.env schaltet ihn ein)."
fi

echo "== 8. Paket-Freischaltung (Deploy-Nutzer ohne root-Shell)"
install -o root -g root -m 0755 "$HIER/scanview-paket-freischalten" /usr/local/sbin/scanview-paket-freischalten
if [[ -x /usr/sbin/visudo && -d /etc/sudoers.d && ! -L /etc/sudoers.d ]]; then
	# Erst unter einem Namen mit Punkt ablegen (sudo liest solche Dateien nicht), prüfen, dann per
	# rename aktivieren — eine fehlerhafte Datei in sudoers.d legt sonst sudo lahm.
	install -o root -g root -m 0440 "$HIER/sudoers.d/scanview-deploy" /etc/sudoers.d/.scanview-deploy.neu
	if ! /usr/sbin/visudo -cf /etc/sudoers.d/.scanview-deploy.neu >/dev/null; then
		rm -f /etc/sudoers.d/.scanview-deploy.neu
		fehler "sudoers-Vorlage besteht visudo -c nicht — nicht aktiviert."
	fi
	mv -f /etc/sudoers.d/.scanview-deploy.neu /etc/sudoers.d/scanview-deploy
	echo "   /etc/sudoers.d/scanview-deploy: Gruppe scanview-deploy darf nur das Freischalt-Skript."
	echo "   Alte, breite Regeln für den Deploy-Nutzer (bash, rsync, test, du, true) von Hand entfernen."
else
	echo "   sudo/visudo fehlt — keine sudoers-Regel installiert; Pakete dann als root bereitstellen."
fi

# Angaben der laufenden Instanz lesen (eine Antwort von /api/barrierefreiheit): Barrierefreiheit
# (Kontakt, Erklärung) und die Links zu Impressum und Datenschutzhinweisen. Fehlt etwas, steht eine
# Warnung im Update-Protokoll (Kennung `warnung=…` hinter der Ergebniszeile, je Prüfung eine) und
# auf stderr. Ist die Antwort unlesbar, steht nur `warnung=barrierefreiheit-nicht-pruefbar` — sie
# gilt für beide Prüfungen. Nie ein Abbruch — die Angaben setzt der Betreiber in
# scanview-server.env (docs/public/betrieb.md).
barrierefreiheit_pruefen() {
	local antwort urteil ba rl
	antwort=$(curl -fsS --max-time 5 http://127.0.0.1:8803/api/barrierefreiheit 2>/dev/null) || antwort=
	urteil=$("$PYTHON" -I -c '
import json, sys
try:
    d = json.load(sys.stdin)
    ba = "fehlt" if d.get("kontakt") is None or d.get("erklaerung") is None else "ok"
    rl = "fehlt" if d.get("impressum") is None or d.get("datenschutz") is None else "ok"
    print(ba, rl)
except (ValueError, AttributeError):
    print("unlesbar unlesbar")
' <<<"$antwort" 2>/dev/null) || urteil="unlesbar unlesbar"
	read -r ba rl <<<"$urteil"
	case $ba in
	ok) ;;
	fehlt)
		echo "WARNUNG: Barrierefreiheit — Kontakt oder Erklärung der Instanz nicht gesetzt (SCANVIEW_BARRIEREFREIHEIT_KONTAKT / _ERKLAERUNG, docs/public/barrierefreiheit.md)." >&2
		HINWEIS="$HINWEIS warnung=barrierefreiheit-angaben-fehlen" ;;
	*)
		echo "WARNUNG: Barrierefreiheit — /api/barrierefreiheit nicht lesbar, Angaben (auch Impressum/Datenschutz) nicht geprüft." >&2
		HINWEIS="$HINWEIS warnung=barrierefreiheit-nicht-pruefbar"
		return ;;
	esac
	case $rl in
	ok) ;;
	*)
		echo "WARNUNG: Impressum/Datenschutz — Link zu Impressum oder Datenschutzhinweisen nicht gesetzt (SCANVIEW_IMPRESSUM_URL / SCANVIEW_DATENSCHUTZ_URL, docs/public/betrieb.md)." >&2
		HINWEIS="$HINWEIS warnung=impressum-datenschutz-fehlen" ;;
	esac
}

echo "== 9. Gesundheit"
for _ in $(seq 1 20); do
	if curl -fsS http://127.0.0.1:8803/api/gesundheit; then
		echo
		barrierefreiheit_pruefen
		# JOBLAEUFER_BEGIN
		if [[ $JOBLAEUFER -eq 1 ]]; then joblaeufer_aktiv; fi
		# JOBLAEUFER_END
		ERGEBNIS=ok
		rm -f -- "$MARKER"
		echo "Fertig ($VERSION). Protokoll: $PROTOKOLL. Nächste Schritte: docs/public/betrieb.md (Caddy, Pakete bereitstellen, Konten)."
		exit 0
	fi
	sleep 0.5
done
systemctl --no-pager status scanview-server.service || true
fehler "Dienst antwortet nicht auf 127.0.0.1:8803 — journalctl -u scanview-server prüfen."
