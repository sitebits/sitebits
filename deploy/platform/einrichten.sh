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
systemctl daemon-reload
systemctl enable scanview-server.service scanview-backup.timer
# restart statt `enable --now`: bei einem erneuten Lauf soll der neue Code auch geladen werden.
systemctl restart scanview-server.service
systemctl start scanview-backup.timer
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
		ERGEBNIS=ok
		rm -f -- "$MARKER"
		echo "Fertig ($VERSION). Protokoll: $PROTOKOLL. Nächste Schritte: docs/public/betrieb.md (Caddy, Pakete bereitstellen, Konten)."
		exit 0
	fi
	sleep 0.5
done
systemctl --no-pager status scanview-server.service || true
fehler "Dienst antwortet nicht auf 127.0.0.1:8803 — journalctl -u scanview-server prüfen."
