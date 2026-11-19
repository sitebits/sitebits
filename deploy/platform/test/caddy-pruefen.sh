#!/usr/bin/env bash
# shellcheck disable=SC2015 # `A && ok || nok` ist sicher: ok() ist ein printf, gibt immer 0 zurück.
# Prüft Caddy + scanview-server von außen: Status, Header, Pfadtricks. Eine Zeile je Prüfung,
# OK oder FEHLER; Exit 1, sobald eine Prüfung fehlschlägt.
#
#   deploy/platform/test/caddy-pruefen.sh <basis-url> <cookie>
#
# <cookie>: Wert des Sitzungs-Cookies (mit oder ohne Präfix "__Host-scanview="), z. B. aus dem
# Set-Cookie der Passwort-Einlösung. Der Nutzer muss PROJEKT sehen dürfen, FREMD aber nicht.
# PROJEKT und FREMD (je eine projectId) sind Pflicht und werden per Umgebung übergeben;
# FREMD sollte im Katalog stehen, sonst prüft "fremdes Projekt" nur "unbekanntes Projekt".
# Optional: ABMELDEN_COOKIE (Sitzung eines Wegwerf-Kontos, wird abgemeldet) für Prüfung 13;
# FADEN_POI (POI-ID in PROJEKT, an dessen Befund-Faden der Nutzer anhängen darf) für Prüfung 9g.
set -euo pipefail

if [[ $# -ne 2 ]]; then
	echo "Aufruf: $0 <basis-url> <cookie>" >&2
	exit 2
fi
BASIS=${1%/}
COOKIE=$2
[[ $COOKIE == *=* ]] || COOKIE="__Host-scanview=$COOKIE"
PROJEKT=${PROJEKT:?PROJEKT=<projectId, die der Nutzer sehen darf> setzen}
FREMD=${FREMD:?FREMD=<projectId, die der Nutzer nicht sehen darf> setzen}
LAZ="/data/$PROJEKT/cloud.copc.laz"

fehler=0
ok() { printf 'OK      %s\n' "$1"; }
nok() { printf 'FEHLER  %s — %s\n' "$1" "$2"; fehler=1; }

# Kopfzeilen einer Antwort (GET, Körper verworfen). --path-as-is: curl soll ../ nicht selbst
# auflösen, sonst prüfen wir den Pfadtrick gar nicht.
kopf() { curl -sS --path-as-is -D - -o /dev/null "$@"; }
# Statuscode aus der ersten Zeile, ohne Pipe: `printf | head -n1` konnte unter pipefail mit SIGPIPE (141)
# abbrechen, sobald head vor dem Ende der Kopfzeilen schloss (Wegwerf-E2E 17.10.).
status() { local z=${1%%$'\n'*}; z=${z#* }; printf '%s' "${z%%[ $'\r']*}"; }
feld() { # feld <kopfzeilen> <name> → Wert(e), eine Zeile je Vorkommen
	printf '%s' "$1" | tr -d '\r' | awk -v n="$2" 'tolower($0) ~ "^" tolower(n) ":" {sub(/^[^:]*:[ \t]*/, ""); print}'
}

# 1 Gesundheit
k=$(kopf "$BASIS/api/gesundheit")
[[ $(status "$k") == 200 ]] && ok "GET /api/gesundheit → 200" || nok "GET /api/gesundheit" "Status $(status "$k")"
# 1b kein Proxy-/Server-Banner (BB-N1/BB-I2), API nie im Cache (BB-I1)
v="$(feld "$k" Server)$(feld "$k" Via)"
[[ -z $v ]] && ok "/api/gesundheit ohne Server/Via" || nok "/api/gesundheit Banner" "'$v'"
cc=$(feld "$k" Cache-Control)
[[ $cc == no-store ]] && ok "/api/gesundheit Cache-Control: no-store" || nok "/api/gesundheit Cache-Control" "'$cc'"

# 2 Daten ohne Cookie
k=$(kopf "$BASIS$LAZ")
s=$(status "$k"); ct=$(feld "$k" Content-Type)
if [[ $s == 401 && $ct == application/json* ]]; then ok "ohne Cookie $LAZ → 401 JSON"
else nok "ohne Cookie $LAZ" "Status $s, Content-Type '$ct' (erwartet 401 application/json)"; fi
# Die 401 kommt von /api/authz und trägt no-store (BB-I1) — der Browser darf sie nicht aufheben
# (vorher: gar kein Cache-Control; ein max-age aus dem /data-Zweig wäre der Fehler).
cc=$(feld "$k" Cache-Control)
[[ $cc == no-store ]] && ok "401 mit Cache-Control: no-store" || nok "401 Cache-Control" "'$cc' (erwartet no-store)"

# 3 Range mit Cookie, Kompression angeboten
k=$(kopf -H "Cookie: $COOKIE" -H 'Range: bytes=0-15' -H 'Accept-Encoding: zstd, gzip' "$BASIS$LAZ")
s=$(status "$k"); cr=$(feld "$k" Content-Range); ce=$(feld "$k" Content-Encoding)
if [[ $s == 206 && $cr == "bytes 0-15/"* && -z $ce ]]; then ok "Range 0-15 → 206, $cr, kein Content-Encoding"
else nok "Range 0-15" "Status $s, Content-Range '$cr', Content-Encoding '$ce'"; fi

# 3b Range auf JSON (komprimierbar, über encode-Mindestlänge): darf ebenfalls nicht kodiert sein
k=$(kopf -H "Cookie: $COOKIE" -H 'Range: bytes=0-4095' -H 'Accept-Encoding: zstd, gzip' "$BASIS/data/$PROJEKT/project.json")
s=$(status "$k"); ce=$(feld "$k" Content-Encoding); cr=$(feld "$k" Content-Range)
if [[ $s == 206 && $cr == "bytes 0-"* && -z $ce ]]; then ok "Range auf project.json → 206, $cr, kein Content-Encoding"
else nok "Range auf project.json" "Status $s, Content-Range '$cr', Content-Encoding '$ce'"; fi

# 3c Cache-Control der Datenantworten
cc=$(feld "$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/project.json")" Cache-Control)
[[ $cc == "private, no-store"* ]] && ok "project.json Cache-Control: $cc" || nok "project.json Cache-Control" "'$cc'"
cc=$(feld "$(kopf -H "Cookie: $COOKIE" -H 'Range: bytes=0-15' "$BASIS$LAZ")" Cache-Control)
[[ $cc == "private, max-age=3600"* ]] && ok "COPC Cache-Control: $cc" || nok "COPC Cache-Control" "'$cc'"

# 3d fehlende Datei (authz ok, file_server 404): kein Cache-Control — der Browser soll die 404
# nicht aufheben, falls das Paket später vollständig ist.
k=$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/gibt-es-nicht.laz")
s=$(status "$k"); cc=$(feld "$k" Cache-Control)
if [[ $s == 404 && -z $cc ]]; then ok "fehlende Datei → 404 ohne Cache-Control"
else nok "fehlende Datei" "Status $s, Cache-Control '$cc'"; fi

# 3e Fehlerpfad (BB-N1): dieselbe 404 trägt die Sicherheits-Header und keinen Server-Banner
hsts=$(feld "$k" Strict-Transport-Security); nos=$(feld "$k" X-Content-Type-Options); srv=$(feld "$k" Server)
if [[ -n $hsts && $nos == nosniff && -z $srv ]]; then ok "Fehlerpfad-404: HSTS + nosniff, kein Server-Banner"
else nok "Fehlerpfad-404 Header" "HSTS '$hsts', nosniff '$nos', Server '$srv'"; fi

# 3f Datenantworten sind nie ein Dokument (WB-N5): CSP sandbox + nosniff auf /data
k=$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/project.json")
csp=$(feld "$k" Content-Security-Policy); nos=$(feld "$k" X-Content-Type-Options)
if [[ $csp == "sandbox; default-src 'none'" && $nos == nosniff ]]; then ok "/data: CSP '$csp', nosniff"
else nok "/data Header" "CSP '$csp', nosniff '$nos'"; fi

# 3g aktive Inhalte im Paket (WB-N5): authz lässt nur Paket-Dateiendungen durch → 404 JSON
for p in "/data/$PROJEKT/x.html" "/data/$PROJEKT/x.svg" "/data/$PROJEKT/x.js"; do
	k=$(kopf -H "Cookie: $COOKIE" "$BASIS$p"); s=$(status "$k"); ct=$(feld "$k" Content-Type)
	if [[ $s == 404 && $ct == application/json* ]]; then ok "$p → 404 JSON von authz"
	else nok "$p" "Status $s, Content-Type '$ct'"; fi
done

# 3h pois.json ist optional (der Import schreibt sie nie): vorhanden → 200, fehlt → 204 — nie 404,
# sonst meldet Chrome einen Ladefehler. Wie jede JSON-Datenantwort nie im Cache.
k=$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/pois.json")
s=$(status "$k"); cc=$(feld "$k" Cache-Control)
if [[ ($s == 200 || $s == 204) && $cc == "private, no-store"* ]]; then ok "pois.json → $s, Cache-Control: $cc"
else nok "pois.json" "Status $s, Cache-Control '$cc' (erwartet 200 oder 204 mit private, no-store)"; fi
# 3i die 204 kommt erst nach authz: pois.json eines fremden Projekts bleibt 404 (kein Existenz-Leck)
s=$(status "$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$FREMD/pois.json")")
[[ $s == 404 ]] && ok "pois.json fremdes Projekt $FREMD → 404" || nok "pois.json fremdes Projekt $FREMD" "Status $s (erwartet 404)"

# 4 fremdes Projekt
s=$(status "$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$FREMD/project.json")")
[[ $s == 404 ]] && ok "fremdes Projekt $FREMD → 404" || nok "fremdes Projekt $FREMD" "Status $s"

# 5 Pfadtricks (alle mit gültigem Cookie → 404)
for p in "/data/$PROJEKT/..%2f$FREMD/project.json" \
	"/data/$PROJEKT/%2e%2e/$FREMD/project.json" \
	"/data/$PROJEKT/../$FREMD/project.json" \
	"/data/$PROJEKT/.versteckt"; do
	s=$(status "$(kopf -H "Cookie: $COOKIE" "$BASIS$p")")
	[[ $s == 404 ]] && ok "Pfadtrick $p → 404" || nok "Pfadtrick $p" "Status $s"
done

# 5b X-Forwarded-Uri kommt roh (noch kodiert) beim Dienst an: dekodiert wäre
# cloud%2ecopc.laz erlaubt; nur die rohe Form trifft die %2e-Sperre in /api/authz.
# JSON-Pflicht: die 404 muss von authz kommen, nicht von file_server (dort wäre sie leer/HTML).
k=$(kopf -H "Cookie: $COOKIE" -H 'Range: bytes=0-15' "$BASIS/data/$PROJEKT/cloud%2ecopc.laz")
s=$(status "$k"); ct=$(feld "$k" Content-Type)
if [[ $s == 404 && $ct == application/json* ]]; then ok "roh weitergereicht: cloud%2ecopc.laz → 404 JSON von authz (Kontrolle oben 206)"
else nok "X-Forwarded-Uri nicht roh?" "cloud%2ecopc.laz → $s, Content-Type '$ct'"; fi

# 5c Paketdateien nur unter /data: außerhalb kommt das Portal (HTML), nie Paketinhalt
for p in "/$PROJEKT/project.json" "/wasm/../../$PROJEKT/project.json"; do
	k=$(kopf "$BASIS$p"); ct=$(feld "$k" Content-Type)
	[[ $ct == text/html* ]] && ok "außerhalb /data: $p → Portal ($ct)" || nok "außerhalb /data: $p" "Status $(status "$k"), Content-Type '$ct'"
done

# 6 Viewer-CSP
k=$(curl -sSI "$BASIS/ansicht")
n=$(feld "$k" Content-Security-Policy | wc -l)
csp=$(feld "$k" Content-Security-Policy)
if [[ $n -eq 1 && $csp == *"'wasm-unsafe-eval'"* ]]; then ok "/ansicht: genau eine CSP, mit wasm-unsafe-eval"
else nok "/ansicht CSP" "$n Header: $csp"; fi

cc=$(feld "$k" Cache-Control)
[[ $cc == no-cache ]] && ok "/ansicht Cache-Control: $cc" || nok "/ansicht Cache-Control" "'$cc' (erwartet no-cache)"

# 6a Web-App: Manifest und Symbole kommen als Datei (nicht als Portal-HTML), mit Medientyp und ohne immutable
k=$(kopf "$BASIS/manifest.webmanifest")
s=$(status "$k"); ct=$(feld "$k" Content-Type); cc=$(feld "$k" Cache-Control)
if [[ $s == 200 && $ct == application/manifest+json* && $cc != *immutable* ]]; then ok "/manifest.webmanifest → 200, $ct, $cc"
else nok "/manifest.webmanifest" "Status $s, Content-Type '$ct', Cache-Control '$cc'"; fi
k=$(kopf "$BASIS/icons/scanview-180.png")
s=$(status "$k"); ct=$(feld "$k" Content-Type)
[[ $s == 200 && $ct == image/png ]] && ok "/icons/scanview-180.png → 200 image/png" || nok "/icons/scanview-180.png" "Status $s, Content-Type '$ct'"

# 6b /api/authz nur intern (forward_auth); von außen 404 — auch mit gültigem Cookie.
# Dass forward_auth selbst weiter wirkt, belegen 2 (401 JSON) und 3 (206) oben.
k=$(kopf -H "Cookie: $COOKIE" -H "X-Forwarded-Uri: $LAZ" "$BASIS/api/authz")
s=$(status "$k")
[[ $s == 404 ]] && ok "/api/authz von außen → 404" || nok "/api/authz von außen" "Status $s"

# 6c Körpergrenze /auth/*: 64 KB → größere Anfragen weist Caddy mit 413 ab.
s=$(head -c 70000 /dev/zero | tr '\0' 'a' | curl -sS -o /dev/null -w '%{http_code}' -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H 'Content-Type: application/json' \
	--data-binary @- "$BASIS/auth/passwort/anmelden")
[[ $s == 413 ]] && ok "/auth/* mit 70 000 Byte Körper → 413" || nok "/auth/* Körpergrenze" "Status $s (erwartet 413)"
# 6d dieselbe 413 aus dem Fehlerpfad (BB-N1): Sicherheits-Header, kein Server-Banner
k=$(head -c 70000 /dev/zero | tr '\0' 'a' | curl -sS -D - -o /dev/null -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H 'Content-Type: application/json' \
	--data-binary @- "$BASIS/auth/passwort/anmelden")
hsts=$(feld "$k" Strict-Transport-Security); srv=$(feld "$k" Server); csp=$(feld "$k" Content-Security-Policy)
if [[ -n $hsts && -n $csp && -z $srv ]]; then ok "413: HSTS + CSP, kein Server-Banner"
else nok "413 Header" "HSTS '$hsts', CSP '$csp', Server '$srv'"; fi

# 7 Portal-CSP streng
k=$(curl -sSI "$BASIS/")
n=$(feld "$k" Content-Security-Policy | wc -l)
csp=$(feld "$k" Content-Security-Policy)
script_src=$(printf '%s' "$csp" | tr ';' '\n' | awk '$1=="script-src"')
if [[ $n -eq 1 && -n $script_src && $script_src != *unsafe-inline* && $script_src != *wasm-unsafe-eval* ]]; then
	ok "/: genau eine CSP, script-src streng ($(echo "$script_src" | xargs))"
else nok "/ CSP" "$n Header: $csp"; fi

# 8 X-Robots-Tag
xr=$(feld "$k" X-Robots-Tag)
[[ $xr == "noindex, nofollow" ]] && ok "X-Robots-Tag: $xr" || nok "X-Robots-Tag auf /" "'$xr'"

# 9 API v1
s=$(status "$(kopf "$BASIS/api/v1/openapi.json")")
[[ $s == 401 ]] && ok "/api/v1/openapi.json ohne Cookie → 401" || nok "/api/v1/openapi.json ohne Cookie" "Status $s"
k=$(kopf -H "Cookie: $COOKIE" "$BASIS/api/v1/openapi.json")
s=$(status "$k"); ct=$(feld "$k" Content-Type)
if [[ $s == 200 && $ct == application/json* ]]; then ok "/api/v1/openapi.json mit Cookie → 200 JSON"
else nok "/api/v1/openapi.json mit Cookie" "Status $s, Content-Type '$ct'"; fi
s=$(status "$(kopf -H "Cookie: $COOKIE" -H "X-Forwarded-Uri: $LAZ" -H 'X-Forwarded-Method: GET' "$BASIS/api/v1/authz")")
[[ $s == 404 ]] && ok "/api/v1/authz gibt es nicht → 404" || nok "/api/v1/authz" "Status $s (erwartet 404)"
cc=$(feld "$(kopf -H "Cookie: $COOKIE" "$BASIS/api/v1/projekte")" Cache-Control)
[[ $cc == *no-store* ]] && ok "/api/v1/projekte Cache-Control: $cc" || nok "/api/v1/projekte Cache-Control" "'$cc'"

# 9b Körpergrenze /api/*: 1 MB → 413 von Caddy, bevor der Dienst etwas liest. `|| true`: bricht Caddy
# die Verbindung beim großen Körper ab (curl-Fehler, Status 000), meldet das nok statt set -e das Skript beendet.
s=$(head -c 1100000 /dev/zero | tr '\0' 'a' | curl -sS -o /dev/null -w '%{http_code}' -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H 'Content-Type: application/json' -H "Cookie: $COOKIE" \
	--data-binary @- "$BASIS/api/v1/projekte/$PROJEKT/pois" || true)
s=${s:-000}
[[ $s == 413 ]] && ok "/api/* mit 1,1 MB Körper → 413" || nok "/api/* Körpergrenze" "Status $s (erwartet 413)"
# 9e Stücke eines Uploads: nur PUT …/uploads/<id>/dateien/<nr> darf über 1 MB (bis 65 MiB). 1,1 MB
# kommen dort beim Dienst an (er antwortet selbst, nie 413 von Caddy).
# Caddys request_body max_size greift erst beim Lesen. Die beiden Grenzfälle lehnt der Dienst ab,
# BEVOR er den Körper liest (gemessen nach dem Update auf 0.12.0): ohne Origin/X-Scanview mit 403
# (CSRF), POST auf den Stück-Pfad mit 405 (nur PUT/HEAD). Caddy kommt also nie zum 413; die Wand
# selbst (65 MiB bzw. 1 MB) sichert server/tests/test_caddy_grenzen.py gegen Drift. Geprüft wird
# hier, dass keiner der beiden Körper angenommen wird und die Verbindung sauber antwortet.
UPLOAD_STUECK="$BASIS/api/v1/uploads/00000000000000000000000000000000/dateien/1"
s=$(head -c 1100000 /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X PUT \
	-H "Cookie: $COOKIE" --data-binary @- "$UPLOAD_STUECK" || true)
s=${s:-000}
[[ $s != 413 && $s != 000 ]] && ok "Upload-Stück 1,1 MB erreicht den Dienst → $s" || nok "Upload-Stück 1,1 MB" "Status $s (erwartet ≠ 413)"
s=$(head -c $((65 * 1024 * 1024 + 1)) /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X PUT \
	-H "Cookie: $COOKIE" --data-binary @- "$UPLOAD_STUECK" || true)
s=${s:-000}
[[ $s == 403 ]] && ok "Upload-Stück über 65 MiB ohne CSRF-Kopf → 403, vom Dienst vor dem Lesen abgelehnt" \
	|| nok "Upload-Stück über 65 MiB" "Status $s (erwartet 403 vom Dienst vor dem Lesen)"
s=$(head -c 1100000 /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H "Cookie: $COOKIE" --data-binary @- "$UPLOAD_STUECK" || true)
s=${s:-000}
[[ $s == 405 ]] && ok "POST 1,1 MB auf den Stück-Pfad → 405, vom Dienst vor dem Lesen abgelehnt" \
	|| nok "POST auf Stück-Pfad" "Status $s (erwartet 405 vom Dienst vor dem Lesen)"
# 9f Ankündigung eines Uploads (POST /api/v1/uploads, Dateiliste als JSON): bis 13 MiB, Rechenweg
# in scanview.caddy (B-FUZZ-5). 1,1 MB kommen beim Dienst an (er antwortet selbst, je nach Import
# 404 oder 422, nie 413 von Caddy); über 13 MiB → 413.
UPLOAD_NEU="$BASIS/api/v1/uploads"
s=$(head -c 1100000 /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H 'Content-Type: application/json' -H "Cookie: $COOKIE" \
	--data-binary @- "$UPLOAD_NEU" || true)
s=${s:-000}
[[ $s != 413 && $s != 000 ]] && ok "Upload-Ankündigung 1,1 MB erreicht den Dienst → $s" || nok "Upload-Ankündigung 1,1 MB" "Status $s (erwartet ≠ 413)"
s=$(head -c $((13 * 1024 * 1024 + 1)) /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST \
	-H "Origin: $BASIS" -H 'X-Scanview: 1' -H 'Content-Type: application/json' -H "Cookie: $COOKIE" \
	--data-binary @- "$UPLOAD_NEU" || true)
s=${s:-000}
[[ $s == 413 ]] && ok "Upload-Ankündigung über 13 MiB → 413" || nok "Upload-Ankündigung Körpergrenze" "Status $s (erwartet 413)"
# 9g Foto/PDF am Befund-Faden (@faden_anhang): ein Stück, das genau 1 Byte über der Grenze aus
# scanview.caddy liegt (dort `request_body @faden_anhang { max_size … }`, nicht hier abgeschrieben).
# Von außen ist Caddys Grenze nicht erreichbar: curl sendet Content-Length, und der Dienst lehnt alles
# über 20 MiB (anhaenge.MAX_BYTES) schon vor dem Lesen mit 413 ab — unterhalb von Caddys max_size.
# Mit FADEN_POI (POI in PROJEKT, Recht zum Kommentieren) belegt der Punkt also die Dienst-Grenze; die
# Caddy-Wand ist nur Rückfall und wird offline von server/tests/test_caddy_grenzen.py geprüft.
# Ohne FADEN_POI geht das Stück ohne CSRF-Kopf an einen Platzhalter: das prüft nur die CSRF-Abweisung
# vor dem Lesen (403, wie 9e), keine Größengrenze.
CADDY_DATEI="$(dirname "${BASH_SOURCE[0]}")/../scanview.caddy"
grenze=$(awk '/request_body @faden_anhang \{/ {f=1; next} f && $1 == "max_size" {print $2; exit}' "$CADDY_DATEI" 2>/dev/null || true)
case $grenze in
	*MiB) bytes=$(( ${grenze%MiB} * 1024 * 1024 )) ;;
	*KiB) bytes=$(( ${grenze%KiB} * 1024 )) ;;
	*MB) bytes=$(( ${grenze%MB} * 1000 * 1000 )) ;;
	*) bytes= ;;
esac
if [[ -z $bytes ]]; then
	nok "Faden-Anhang Körpergrenze" "max_size für @faden_anhang in $CADDY_DATEI nicht gefunden ('$grenze')"
elif [[ -n ${FADEN_POI:-} ]]; then
	s=$(head -c $((bytes + 1)) /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST \
		-H "Origin: $BASIS" -H 'X-Scanview: 1' -H "Cookie: $COOKIE" -H 'Content-Type: image/jpeg' \
		--data-binary @- "$BASIS/api/v1/projekte/$PROJEKT/pois/$FADEN_POI/faden/anhaenge" || true)
	s=${s:-000}
	[[ $s == 413 ]] && ok "Faden-Anhang $grenze + 1 Byte → 413 (Dienst-Grenze 20 MiB vor dem Lesen)" || nok "Faden-Anhang $grenze + 1 Byte" "Status $s (erwartet 413)"
else
	s=$(head -c $((bytes + 1)) /dev/zero | curl -sS -o /dev/null -w '%{http_code}' -X POST \
		-H "Cookie: $COOKIE" -H 'Content-Type: image/jpeg' --data-binary @- \
		"$BASIS/api/v1/projekte/$PROJEKT/pois/P-0/faden/anhaenge" || true)
	s=${s:-000}
	[[ $s == 403 ]] && ok "Faden-Anhang ohne CSRF-Kopf → 403 vor dem Lesen (nur CSRF, keine Größenprüfung; dafür FADEN_POI setzen)" \
		|| nok "Faden-Anhang $grenze + 1 Byte" "Status $s (erwartet 403 vom Dienst vor dem Lesen)"
fi

# 9d Ein Bearer-Token öffnet nie /data (forward_auth reicht Authorization durch, /api/authz
# wertet nur das Cookie aus) — auch ein syntaktisch passender Wert → 401.
s=$(status "$(kopf -H 'Authorization: Bearer sv_x' "$BASIS/data/$PROJEKT/project.json")")
[[ $s == 401 ]] && ok "Bearer auf /data → 401" || nok "Bearer auf /data" "Status $s (erwartet 401)"

# 9c Portal-Seiten: genau eine, strenge Portal-CSP (wie der Wurzel-Check unter 7)
for p in /verwaltung "/verlauf?p=$PROJEKT"; do
	k=$(curl -sSI "$BASIS$p")
	n=$(feld "$k" Content-Security-Policy | wc -l)
	csp=$(feld "$k" Content-Security-Policy)
	script_src=$(printf '%s' "$csp" | tr ';' '\n' | awk '$1=="script-src"')
	if [[ $n -eq 1 && -n $script_src && $script_src != *unsafe-inline* && $script_src != *wasm-unsafe-eval* ]]; then
		ok "$p: genau eine CSP, Portal-CSP streng"
	else nok "$p CSP" "$n Header: $csp"; fi
done

# 10 Meldeweg (RFC 9116): öffentlich, ohne forward_auth. Solange kein Postfach eingerichtet ist,
# liegt nichts im Meldeweg-Verzeichnis → 404 (nicht 401, nicht die Anmeldung). Ist der Meldeweg
# aktiviert (MELDEWEG_AKTIV=1), müssen security.txt und Schlüssel als text/plain kommen.
for p in /.well-known/security.txt /.well-known/sicherheitsrichtlinie.txt /.well-known/psirt-openpgp.asc; do
	k=$(kopf "$BASIS$p")
	s=$(status "$k"); ct=$(feld "$k" Content-Type); loc=$(feld "$k" Location)
	if [[ ${MELDEWEG_AKTIV:-0} == 1 ]]; then
		if [[ $s == 200 && $ct == "text/plain; charset=utf-8" && -z $loc ]]; then ok "$p → 200 text/plain"
		else nok "$p" "Status $s, Content-Type '$ct', Location '$loc' (erwartet 200 text/plain)"; fi
	else
		if [[ $s == 404 && -z $loc ]]; then ok "$p: leeres Meldeweg-Verzeichnis → 404, kein forward_auth-Redirect"
		else nok "$p" "Status $s, Location '$loc' (erwartet 404 ohne Weiterleitung)"; fi
	fi
done

# 11 Angaben zur Barrierefreiheit (Kontakt, Erklärung): öffentlich, ohne Cookie, nie im Cache.
k=$(kopf "$BASIS/api/barrierefreiheit")
s=$(status "$k"); cc=$(feld "$k" Cache-Control)
[[ $s == 200 ]] && ok "GET /api/barrierefreiheit ohne Cookie → 200" || nok "GET /api/barrierefreiheit ohne Cookie" "Status $s (erwartet 200)"
[[ $cc == no-store ]] && ok "/api/barrierefreiheit Cache-Control: no-store" || nok "/api/barrierefreiheit Cache-Control" "'$cc' (erwartet no-store)"

# 12 Orientierte Fotos (optional, nur wenn PROJEKT eine Ebene fotos hat): FOTO=<Pfad einer Stufe,
# z. B. /data/<p>/fotos/F0001/512.jpg>. COOKIE gehört dann einem Bearbeiter+ (sieht den Prüfstand),
# BETRACHTER_COOKIE einem Betrachter desselben Projekts. Ohne freigegebene Anonymisierung bekommt
# der Betrachter für Foto und images.json 404; der Rest des Pakets bleibt erreichbar.
if [[ -n ${FOTO:-} ]]; then
	k=$(kopf -H "Cookie: $COOKIE" "$BASIS$FOTO")
	s=$(status "$k"); cc=$(feld "$k" Cache-Control)
	[[ $s == 200 && $cc == "private, max-age=3600"* ]] && ok "Foto (Bearbeiter) → 200, $cc" \
		|| nok "Foto (Bearbeiter)" "Status $s, Cache-Control '$cc'"
	cc=$(feld "$(kopf -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/images.json")" Cache-Control)
	[[ $cc == "private, no-store"* ]] && ok "images.json Cache-Control: $cc" || nok "images.json Cache-Control" "'$cc'"
	if [[ -n ${BETRACHTER_COOKIE:-} ]]; then
		bc=$BETRACHTER_COOKIE
		[[ $bc == *=* ]] || bc="__Host-scanview=$bc"
		freigabe=$(curl -sS -H "Cookie: $COOKIE" "$BASIS/data/$PROJEKT/images.json" \
			| python3 -c 'import json,sys; print(json.load(sys.stdin)["anonymization"]["status"])')
		erwartet=404
		[[ $freigabe == released ]] && erwartet=200
		for p in "$FOTO" "/data/$PROJEKT/images.json"; do
			s=$(status "$(kopf -H "Cookie: $bc" "$BASIS$p")")
			[[ $s == "$erwartet" ]] && ok "$p (Betrachter, Anonymisierung $freigabe) → $s" \
				|| nok "$p (Betrachter)" "Status $s (erwartet $erwartet)"
		done
		s=$(status "$(kopf -H "Cookie: $bc" "$BASIS$LAZ")")
		[[ $s == 200 ]] && ok "Punktwolke (Betrachter) → 200" || nok "Punktwolke (Betrachter)" "Status $s"
	fi
fi

# 13 Abmelden leert den Browser-Cache (B-RL-N1): Caddy reicht Clear-Site-Data von /auth/abmelden
# durch. Optional mit ABMELDEN_COOKIE=<Sitzung eines Wegwerf-Kontos> — die Sitzung endet dabei,
# deshalb nie das Cookie der übrigen Prüfungen.
if [[ -n ${ABMELDEN_COOKIE:-} ]]; then
	ac=$ABMELDEN_COOKIE
	[[ $ac == *=* ]] || ac="__Host-scanview=$ac"
	k=$(kopf -X POST -H "Cookie: $ac" -H "Origin: $BASIS" -H 'X-Scanview: 1' "$BASIS/auth/abmelden")
	s=$(status "$k"); csd=$(feld "$k" Clear-Site-Data)
	[[ $s == 200 && $csd == '"cache"' ]] && ok "Abmelden → 200, Clear-Site-Data: $csd" \
		|| nok "Abmelden" "Status $s, Clear-Site-Data '$csd' (erwartet 200 und \"cache\")"
fi

# 14 Teilen-Seite (Nur-Lese-Link, F28) ohne Cookie: genau eine Referrer-Policy, genau same-origin —
# der Host-Block der Instanz (Produktions-Caddyfile) setzt sie; doppelt gesetzt wäre sie mehrdeutig.
k=$(kopf "$BASIS/teilen")
n=$(feld "$k" Referrer-Policy | wc -l); rp=$(feld "$k" Referrer-Policy)
if [[ $n -eq 1 && $rp == same-origin ]]; then ok "/teilen: genau eine Referrer-Policy: $rp"
else nok "/teilen Referrer-Policy" "$n Header: '$rp' (erwartet genau einmal same-origin)"; fi

if [[ $fehler -ne 0 ]]; then
	echo "Ergebnis: FEHLER"
	exit 1
fi
echo "Ergebnis: alles OK"
