#!/usr/bin/env bash
# Prüft ein Release-Verzeichnis gegen das signierte Manifest, ohne etwas zu verändern.
#
#   deploy/platform/release-pruefen.sh <release-verzeichnis> <allowed_signers>
#
# Release-Verzeichnis: manifest.json, manifest.json.sig, abschluss.json, abschluss.json.sig,
# artefakte/scanview-{web,server,mcp}-<v>.tar.gz (genau diese Einträge; scanview-nachweise
# deploy-ordner stellt es aus dem Archiv zusammen). allowed_signers: die Vertrauensdatei des Hosts
# (/etc/scanview/allowed_signers), nie eine Datei aus dem Release.
#
# Die Prüfung selbst (ssh-keygen -Y verify über die einmal gelesenen Manifest-Bytes, SHA-256 und
# SHA-512 jedes Tarballs, Tar-Inhalt) steht in release_pruefen.py daneben — nur Standardbibliothek,
# kein jq. Exit 0 = gültig; jede Abweichung: Exit ≠ 0 und `NACHWEIS-FEHLER <regel>: …`.
# einrichten.sh --release prüft und entpackt in einem Schritt (release_pruefen.py entpacken).
set -euo pipefail

if [[ $# -ne 2 ]]; then
	echo "Aufruf: $0 <release-verzeichnis> <allowed_signers>" >&2
	exit 2
fi
HIER=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# -I: keine Umgebungsvariablen/Benutzerpfade für Python, -B: keine .pyc-Dateien.
# SCANVIEW_PYTHON ist nur für Tests und Gegenprüfungen gedacht (z. B. unter Python 3.11); dieses
# Skript verändert nichts. einrichten.sh wertet die Variable nicht aus und ruft immer
# /usr/bin/python3 auf.
exec "${SCANVIEW_PYTHON:-/usr/bin/python3}" -I -B "$HIER/release_pruefen.py" pruefen "$1" "$2"
