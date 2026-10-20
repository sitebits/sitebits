#!/usr/bin/env bash
# Lädt ein scanview-Paket (Schema 2) auf den Plattform-Host und macht es sichtbar.
#
#   deploy/platform/paket-bereitstellen.sh <quellordner> <ssh-host>
#
# - Zielname kommt aus project.json (projectId), nicht aus dem Ordnernamen: ein lokaler
#   `…-schema2`-Ordner landet als /srv/scanview/data/<projectId>/.
# - Symlinks werden beim Hochladen aufgelöst (rsync -L): ein `-schema2`-Ordner aus Symlinks auf
#   die Originaldateien plus echter, migrierter project.json reicht, keine lokale Vollkopie.
# - Ein bestehendes Ziel wird NIE überschrieben — erst von Hand wegräumen.
# - Rechte auf dem Ziel (Befund B-WB-M4): Der Deploy-Nutzer ist Mitglied der Gruppe
#   scanview-deploy und lädt OHNE sudo nach /srv/scanview/eingang/<projectId>/ hoch. sudo braucht
#   er nur für /usr/local/sbin/scanview-paket-freischalten (sudoers-Vorlage
#   sudoers.d/scanview-deploy, installiert von einrichten.sh) — keine Shell und kein rsync mit
#   root-Rechten. Fehlt die Regel, bricht das Skript vor jeder Änderung ab.
set -euo pipefail

fehler() { echo "Fehler: $*" >&2; exit 1; }

# INSTANZ_DISPATCH_BEGIN
for argument in "$@"; do
    case $argument in
    --instanz)
        exec python3 -I "$(dirname "${BASH_SOURCE[0]}")/instanz_rendern.py" --bereitstellen "$@" ;;
    esac
done
# INSTANZ_DISPATCH_END

[[ $# -eq 2 ]] || fehler "Aufruf: $0 <quellordner> <ssh-host>"
QUELLE=${1%/}
HOST=$2
# Ein Ziel mit führendem „-“ nähmen ssh und rsync als Option (z. B. -oProxyCommand=…).
[[ -n $HOST && $HOST != -* ]] || fehler "ssh-Ziel '$HOST' ist leer oder beginnt mit '-'."
# Feste Pfade auf dem Ziel (einrichten.sh); das Freischalt-Skript kennt sie selbst.
WURZEL=/srv/scanview/data
EINGANG=/srv/scanview/eingang
FREISCHALTEN=/usr/local/sbin/scanview-paket-freischalten
RESERVE=$((10 * 1024 * 1024 * 1024)) # nach Hochladen und Freischalt-Kopie ≥ 10 GiB frei

[[ -d $QUELLE ]] || fehler "$QUELLE ist kein Ordner."
[[ -f $QUELLE/project.json ]] || fehler "$QUELLE/project.json fehlt."

# projectId und schemaVersion lokal lesen (python3 statt jq: auf jedem Host vorhanden).
read -r ID SCHEMA < <(python3 -c '
import json, sys
d = json.load(open(sys.argv[1]))
print(d.get("projectId", ""), d.get("schemaVersion", ""))
' "$QUELLE/project.json")

# Die ID geht unten in Pfade und entfernte Befehle — nur die erlaubte Form durchlassen.
[[ $ID =~ ^[a-z0-9][a-z0-9-]{0,63}$ ]] || fehler "projectId '$ID' hat nicht die Form ^[a-z0-9][a-z0-9-]{0,63}\$."

# Schema lokal prüfen, BEVOR Gigabytes übertragen werden (Abweichung von der Plan-Reihenfolge:
# dort erst nach dem rsync; der Dienst liest nur Schema 2, Migration geht nur lokal).
if [[ $SCHEMA != 2 ]]; then
	cat >&2 <<EOF
Fehler: $QUELLE/project.json hat schemaVersion '$SCHEMA', der Dienst liest nur Schema 2.
Migration lokal auf einer KOPIE (der Import-Code liegt nicht auf dem Server, das Original bleibt
unverändert):
  mkdir <paket>-schema2
  for f in <paket>/*; do [ "\$(basename "\$f")" = project.json ] || ln -s "../\$f" <paket>-schema2/; done
  cp <paket>/project.json <paket>-schema2/project.json      # echte Datei, KEIN Symlink
  (cd import && uv run --locked scanview-import migrate ../<paket>-schema2)
Danach: $0 <paket>-schema2 $HOST
EOF
	exit 1
fi

# Kaputte Symlinks (Ziel fehlt) würden rsync -L mitten im Lauf abbrechen lassen.
kaputt=$(find -L "$QUELLE" -type l -print -quit)
[[ -z $kaputt ]] || fehler "kaputter Symlink in der Quelle: $kaputt"

echo "== Paket $ID (Schema $SCHEMA) aus $QUELLE nach $HOST:$WURZEL/$ID"

# Eine entfernte Abfrage: sudo für das Freischalt-Skript erlaubt? Ziel vorhanden (das weiß nur
# root: die Datenwurzel ist root:scanview 0750)? Wie viel liegt schon im Eingang (fortgesetzter
# Lauf)? Wie viel ist frei? Eingang und Platz liest der Deploy-Nutzer selbst, ohne sudo.
# `sudo -n`: nie auf ein Passwort warten.
set +e
# shellcheck disable=SC2029 # Absicht: lokal einsetzen; ID ist oben per Regex geprüft, der Rest fest.
PROBE=$(ssh "$HOST" "if ! sudo -n -l $FREISCHALTEN >/dev/null 2>&1; then echo SUDO; exit 0; fi
  a=\$(sudo -n $FREISCHALTEN pruefen $ID) || { echo \"PRUEFEN \$a\"; exit 0; }
  if [ \"\$a\" != OK ]; then echo \"\$a\"; exit 0; fi
  if [ ! -w $EINGANG ]; then echo EINGANG_NICHT_BESCHREIBBAR; exit 0; fi
  if [ -d $EINGANG/$ID ]; then v=\$(du -sB1 $EINGANG/$ID | cut -f1); else v=0; fi
  echo FREI \$v \$(df -B1 --output=avail $EINGANG | tail -n1)")
rc=$?
set -e
((rc != 255)) || fehler "ssh zu $HOST fehlgeschlagen (Exit 255: Verbindung/Anmeldung) — nichts geändert."
((rc == 0)) || fehler "Abfrage auf $HOST fehlgeschlagen (Exit $rc): '$PROBE'"
case $PROBE in
SUDO) fehler "sudo -n $FREISCHALTEN ist auf $HOST nicht erlaubt (sudoers-Regel für die Gruppe scanview-deploy fehlt, siehe README) — nichts geändert." ;;
WURZEL) fehler "$HOST:$WURZEL ist ein Symlink oder kein Ordner — Abbruch vor jeder Übertragung." ;;
EINGANG_WURZEL) fehler "$HOST:$EINGANG fehlt, ist ein Symlink oder kein Ordner — einrichten.sh auf dem Host laufen lassen." ;;
EINGANG_NICHT_BESCHREIBBAR) fehler "$HOST:$EINGANG ist für den Deploy-Nutzer nicht beschreibbar — Mitglied der Gruppe scanview-deploy? (neu anmelden nach usermod)." ;;
DATEISYSTEM) fehler "$HOST:$EINGANG und $WURZEL liegen auf verschiedenen Dateisystemen — Freischalten wäre nicht atomar." ;;
RESTE) fehler "$HOST:$WURZEL/.freischalten-$ID existiert (abgebrochenes Freischalten) — als root prüfen und entfernen." ;;
EINGANG_SYMLINK) fehler "$HOST:$EINGANG/$ID ist ein Symlink — Abbruch vor der Übertragung; von Hand prüfen und entfernen." ;;
EINGANG_KEIN_ORDNER) fehler "$HOST:$EINGANG/$ID existiert, ist aber kein Ordner — Abbruch vor der Übertragung, von Hand prüfen." ;;
VORHANDEN) fehler "$HOST:$WURZEL/$ID existiert schon — nie überschreiben. Erst von Hand wegräumen." ;;
FREI\ *) read -r _ VORHANDEN_EINGANG FREI <<<"$PROBE" ;;
*) fehler "unerwartete Antwort von $HOST: '$PROBE'" ;;
esac
[[ $VORHANDEN_EINGANG =~ ^[0-9]+$ && $FREI =~ ^[0-9]+$ ]] || fehler "Platzangaben von $HOST nicht lesbar: '$PROBE'"

GROESSE=$(du -sLB1 "$QUELLE" | cut -f1)
# Fortgesetzter Lauf: was schon im Eingang liegt, belegt den Platz bereits. Das Freischalten
# kopiert das Paket einmal in neue root-Dateien (offene Deskriptoren des Uploads erreichen die
# Kopie nicht) — dafür noch einmal die Paketgröße.
NOCH=$((GROESSE > VORHANDEN_EINGANG ? GROESSE - VORHANDEN_EINGANG : 0))
if ((FREI - NOCH - GROESSE < RESERVE)); then
	fehler "zu wenig Platz: noch $((NOCH / 1024 / 1024)) MiB zu übertragen, $((GROESSE / 1024 / 1024)) MiB für die Freischalt-Kopie, frei $((FREI / 1024 / 1024)) MiB, Reserve 10 GiB."
fi
echo "   Größe $((GROESSE / 1024 / 1024)) MiB, schon im Eingang $((VORHANDEN_EINGANG / 1024 / 1024)) MiB, frei $((FREI / 1024 / 1024)) MiB"

# Der Eingang liegt außerhalb der Datenwurzel (Caddy und Katalog sehen ihn nicht) und gehört dem
# Deploy-Nutzer; ein abgebrochener Lauf setzt hier fort.
echo "== Hochladen (Symlinks aufgelöst, ohne sudo)"
# --no-D: keine Geräte/FIFOs übertragen (-a würde sie mitnehmen). --delete wirkt nur im
# Eingang: Reste eines früheren, abgebrochenen Laufs, die in der Quelle fehlen, fliegen raus.
# PAKET_BWLIMIT (KiB/s, optional mit K/M): Drossel für Anschlüsse, die ungedrosselt andere stören.
BWLIMIT=()
if [[ -n ${PAKET_BWLIMIT:-} ]]; then
    [[ $PAKET_BWLIMIT =~ ^[0-9]+[KkMm]?$ ]] || fehler "PAKET_BWLIMIT '$PAKET_BWLIMIT' ist keine Rate (z. B. 3000 oder 3M)."
    BWLIMIT=(--bwlimit="$PAKET_BWLIMIT")
fi
rsync -aL --no-D --delete "${BWLIMIT[@]}" --info=progress2 -- "$QUELLE/" "$HOST:$EINGANG/$ID/"

echo "== Prüfen und freischalten auf $HOST"
# Das Freischalt-Skript nimmt den Eingang weg, prüft (nur reguläre Dateien/Ordner, project.json),
# setzt Eigentümer/Rechte/ACL, macht das Paket sichtbar und lädt den Katalog neu.
# shellcheck disable=SC2029 # ID ist oben per Regex geprüft.
ssh "$HOST" "sudo -n $FREISCHALTEN $ID"

echo "Fertig. Rechte vergeben: scanview-server einladen --projekt $ID=betrachter (README.md)."
