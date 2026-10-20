# Betriebsanleitung scanview-Plattform

Diese Anleitung beschreibt, wie eine scanview-Plattform eingerichtet, aktualisiert, gesichert und
gehärtet wird. Sie richtet sich an Administratoren eines eigenen Linux-Servers. Hersteller- und
Kontaktangaben, Zweck und Supportzeitraum stehen im [README](../../README.md).

## Aufbau

- **Caddy** nimmt HTTPS-Anfragen an, setzt die Sicherheits-Header, liefert das Web-Bundle
  (Portal und Viewer) und die Projektpakete aus und fragt vor jeder Paketdatei den Dienst.
- **scanview-server** (systemd-Dienst, Nutzer `scanview`) lauscht nur auf `127.0.0.1:8803` und
  verwaltet Konten, Sitzungen, Rechte, Bearbeitungen und das Bearbeitungsprotokoll in einer
  SQLite-Datenbank.
- **Projektpakete** liegen als Dateien unter `/srv/scanview/data/<projectId>/`; der Dienst liest
  sie nur.

| Pfad | Inhalt | Rechte |
|---|---|---|
| `/opt/scanview/server`, `/opt/scanview/web` | Programm und Web-Bundle | root, nur lesbar |
| `/etc/scanview/scanview-server.env` | Einstellungen (keine Geheimnisse) | root:scanview 0640 |
| `/srv/scanview/state` | Datenbank, Sicherungen, Git-Export | scanview 0700 |
| `/srv/scanview/data` | Projektpakete | root:scanview 0750, Caddy lesend per ACL |

Die Dateien für Einrichtung und Betrieb liegen in `deploy/platform/`:

| Datei | Zweck |
|---|---|
| `einrichten.sh` | Einrichten und Aktualisieren auf dem Host (root, wiederholbar); `--release <ordner>` spielt nur ein geprüftes Release ein |
| `release-pruefen.sh`, `release_pruefen.py` | Release-Ordner gegen das signierte Manifest prüfen (Signatur, Prüfsummen, Inhalt der Archive) |
| `scanview-server.service` | gehärtete systemd-Unit des Dienstes |
| `scanview-server.env.beispiel` | Vorlage der Einstellungen mit Erklärungen |
| `scanview.caddy` | Caddy-Konfiguration für die Plattform |
| `Caddyfile.lokal`, `docker-compose.caddy.yml` | vollständiges Caddy-Beispiel für einen lokalen Test |
| `caddy-haertung.conf` | systemd-Härtung für Caddy |
| `scanview-backup.{service,timer}` | tägliche Datenbanksicherung |
| `scanview-git-export.{service,timer}` | optionaler Git-Export der Bearbeitungen |
| `paket-bereitstellen.sh` | Projektpaket auf den Host bringen und freischalten |
| `fail2ban/` | Filter und Jail für fehlgeschlagene Passwort-Anmeldungen |
| `test/caddy-pruefen.sh` | Prüfung von Status, Headern und Zugriffsschutz von außen |

## Voraussetzungen

- Debian 12 oder vergleichbar, Python 3.11 oder 3.12.
- Caddy auf demselben Host (Nutzer `caddy`) mit gültigem TLS-Zertifikat für den Namen der
  Plattform. HTTPS ist Pflicht: Passkeys funktionieren nur in einem sicheren Kontext.
- Pakete `acl`, `rsync`, `curl`, `sqlite3`, `git`; `uv` in einer bewusst gewählten, geprüften
  Version, systemweit unter `/usr/local/bin/uv` (oder `/usr/bin/uv`) — das Bootstrap-Skript setzt
  den PATH fest auf Debians `secure_path`
  (`/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`), ein uv nur im
  Heimatverzeichnis wird dort nicht gefunden; für die Installation aus einem Release zusätzlich `python3` (ab 3.11) und
  `ssh-keygen` (`openssh-client`). Das Einrichtungsskript prüft diese Voraussetzungen und
  installiert selbst nichts.

## Einrichten

Die Ersteinrichtung ist derselbe Weg wie ein Update: Vertrauensanker installieren, Release-Ordner
prüfen und `einrichten.sh --release <ordner>` aufrufen (Einzelheiten und die einmalige
Einrichtung je Host im Abschnitt [„Aktualisieren“](#aktualisieren)):

```bash
sudo deploy/platform/einrichten.sh --release <ordner>
```

Nur für einen lokalen Test aus einem ausgecheckten Quelltext-Stand (meldet
`WARNUNG: kein geprüftes Release`):

```bash
cd web && npm ci && npm run build && cd ..
sudo deploy/platform/einrichten.sh "$PWD"
```

Das Skript legt Nutzer, Verzeichnisse, Einstellungen (nur wenn sie fehlen) und die systemd-Units
an, installiert die Python-Abhängigkeiten ausschließlich aus dem Lockfile, startet den Dienst neu
und fragt `/api/gesundheit` ab. Danach:

1. `/etc/scanview/scanview-server.env` anpassen: `SCANVIEW_ORIGIN` (z. B.
   `https://scanview.example.org`) und `SCANVIEW_RP_ID` (Hostname). Die RP-ID **nie nachträglich
   ändern** — alle Passkeys hängen daran. `SCANVIEW_QUELLTEXT_URL` setzt den Link zum Quelltext
   (EUPL-1.2, Art. 5) in der Fußzeile des Portals; wer eine Instanz für andere betreibt, muss den
   Quelltext der laufenden Version anbieten. `SCANVIEW_BARRIEREFREIHEIT_KONTAKT` und
   `SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG` setzen den Link „Barriere melden“ und die Erklärung
   zur Barrierefreiheit ([`barrierefreiheit.md`](barrierefreiheit.md)); fehlt eine der beiden
   Angaben, schreibt `einrichten.sh` eine Warnung ins Update-Protokoll.
   `SCANVIEW_IMPRESSUM_URL` und `SCANVIEW_DATENSCHUTZ_URL` setzen die Links „Impressum“ und
   „Datenschutz“ in der Fußzeile jeder Portalseite, auch auf der Anmeldeseite. scanview liefert
   keinen Rechtstext; beide Seiten stellt der Betreiber. Erlaubt ist eine `https://`-Adresse ohne
   Zugangsdaten (`https://stelle.example.org/impressum`) oder ein Pfad auf derselben Origin
   (`/datenschutz.html`, nicht `//…`); einen solchen Pfad muss die Instanz selbst ausliefern, etwa
   als statische Datei über Caddy vor dem scanview-Zweig. Nur druckbare ASCII-Zeichen ohne
   Leerzeichen und Backslash, höchstens 2000 Zeichen; ein ungültiger Wert verhindert den Start.
   Die Datenschutzhinweise sollten alle Daten aus der Übersicht in der
   [Sicherheitsarchitektur](sicherheitsarchitektur.md#verarbeitete-daten-und-zweck) nennen, auch
   die im Browser gespeicherten: lokale Merkpunkte und den lokalen Befundentwurf (IndexedDB, je
   Konto und Projekt). Fehlt einer der beiden Links, fehlt er in der Fußzeile; der Dienst warnt beim
   Start, und `einrichten.sh` schreibt `warnung=impressum-datenschutz-fehlen` ins
   Update-Protokoll. Ein Abbruch ist das nicht.
2. `journalctl -u scanview-server -b` prüfen.
3. Caddy einbinden (nächster Abschnitt), dann das erste Admin-Konto einladen (Abschnitt
   „Konten“).

Für Kommandozeilenbefehle braucht der Dienst seine Umgebung und muss als `scanview` laufen:

```bash
sv() { sudo -u scanview bash -c 'set -a; . /etc/scanview/scanview-server.env; set +a; exec /opt/scanview/server/.venv/bin/scanview-server "$@"' _ "$@"; }
```

Für eine benannte Instanz einen eigenen Helfer verwenden. `sv-beispiel` lädt ausschließlich
deren Umgebung und startet deren Python-Umgebung; die bisherigen `sv`-Beispiele gelten weiter
für die Standardinstanz:

```bash
sv-beispiel() { sudo -u scanview-beispiel bash -c 'set -a; . /etc/scanview-beispiel/scanview-server.env; set +a; exec /opt/scanview-beispiel/server/.venv/bin/scanview-server "$@"' _ "$@"; }
```

### Caddy einbinden

`scanview.caddy` nach `/etc/caddy/scanview.caddy` kopieren und im Site-Block einbinden,
Beispiel für eine Wildcard-Domain:

```caddy
*.example.org {
	@scanview host scanview.example.org
	handle @scanview {
		import /etc/caddy/scanview.caddy
	}
	handle {
		abort
	}
}
```

Umgebung für Caddy: `SCANVIEW_WEB=/opt/scanview/web`, `SCANVIEW_DATA=/srv/scanview/data`
(`SCANVIEW_UPSTREAM` ist standardmäßig `127.0.0.1:8803`). `Caddyfile.lokal` zeigt einen
vollständigen Block mit allen Headern. Danach `caddy validate` und `systemctl reload caddy`.

Prüfen von außen (Cookie eines Testkontos mit Recht auf `PROJEKT`, ohne Recht auf `FREMD`):

```bash
PROJEKT=<sichtbares-projekt> FREMD=<nicht-sichtbares-projekt> \
  deploy/platform/test/caddy-pruefen.sh https://scanview.example.org '__Host-scanview=<wert>'
```

Alle Punkte müssen `OK` melden. Optional prüft `ABMELDEN_COOKIE=<Sitzung eines zweiten
Testkontos>`, dass Abmelden den Browser-Cache leert (`Clear-Site-Data`); diese Sitzung endet dabei.

## Weitere Instanz auf demselben Host

`einrichten.sh --instanz beispiel --port 8811 --release <release-ordner>` richtet eine getrennte
Instanz ein oder aktualisiert sie. Ohne Instanzangabe bleibt die Standardinstanz auf Port 8803
mit ihren bisherigen Dateien und Konten unverändert. Benannte Instanzen brauchen einen freien
Port von 1024 bis 65535 außer 8803; zulässig sind Namen nach `^[a-z][a-z0-9-]{1,19}$`, außer
`scanview` und Namen mit dem Ende `-deploy`.

Die Einrichtung verwendet `/opt/scanview-beispiel`, `/srv/scanview-beispiel` und
`/etc/scanview-beispiel`; Log, Sicherungen und Installstatus liegen unter
`/var/log/scanview-beispiel`, `/var/backups/scanview-beispiel` und
`/var/lib/scanview-install-beispiel`. Der Dienst heißt `scanview-beispiel-server`, die Sicherung
`scanview-beispiel-backup.{service,timer}`. Es werden keine Git-Export-Units angelegt.
Update-Erkennung, Datenbank, Sicherungen und `updates.log` betreffen nur diese Instanz.
Portkollisionen und Symlinks oder aufgelöste Pfade in fremde Instanzen führen vor jeder
Änderung zum Abbruch. Vorher je Instanz eine geprüfte reguläre Kopie von `allowed_signers`
installieren; fremde Vertrauensdateien werden nie still als Ersatz verwendet.

Der Betreiber setzt DNS und einen eigenen Host-Zweig im bestehenden Caddy-Wildcard-Block:

```caddyfile
@scanview_beispiel host beispiel.example.org
handle @scanview_beispiel {
    import /etc/caddy/scanview-beispiel.caddy
}
```

`python3 deploy/platform/instanz_rendern.py --instanz beispiel --port 8811 --render-root
<vorlagen-ziel>` erzeugt die Vorlagen mit dem eigenen Port und den eigenen Pfaden. Das Caddy-Snippet
sowie `fail2ban/filter.d/scanview-beispiel.conf` und `fail2ban/jail.d/scanview-beispiel.local`
aus diesem Ziel nach `/etc/` installieren (vollständige Befehle im
[Plattform-Runbook](../../deploy/platform/README.md#weitere-instanz-auf-demselben-host)).
Beim Rendern lautet der relative fail2ban-Pfad `etc/fail2ban/…`. Caddy vor dem Reload validieren;
fail2ban neu laden und das Jail `scanview-beispiel` prüfen. Beide Betreiberdateien werden
ebenso wie bei der Standardinstanz von Hand installiert. Das benannte Caddy-Snippet verwendet
feste Ziele, unabhängig von globalen `SCANVIEW_*`-Variablen im Caddy-Dienst.

Eigene Origin und RP-ID sowie Barrierefreiheitsangaben, Impressum und Datenschutz in
`/etc/scanview-beispiel/scanview-server.env` setzen und den eigenen Dienst neu starten.
Für CLI-Aufrufe den oben gezeigten Helfer `sv-beispiel` verwenden; er lädt die eigene Umgebung
und startet `/opt/scanview-beispiel/server/.venv/bin/scanview-server` als `scanview-beispiel`.

```bash
deploy/platform/paket-bereitstellen.sh --instanz beispiel <quellordner> <ssh-host>
sv-beispiel nutzer
sudo systemctl start scanview-beispiel-backup.service
```

Das eigene Deploy-Konto gehört ausschließlich zur Gruppe `scanview-beispiel-deploy`, nie zu
Dienstgruppen oder Deploy-Gruppen anderer Instanzen. Seine sudoers-Regel erlaubt nur
`/usr/local/sbin/scanview-beispiel-paket-freischalten`. Eingang, Datenwurzel und Reload-Unit sind
in diesem Skript fest eingebaut; es akzeptiert keine fremden Pfade oder Instanzschalter.
Bootstrap und Upload brauchen die begleitende `instanz_rendern.py`; das geprüfte Bootstrap
benötigt außerdem `release_pruefen.py`. Vor Go-live die vollständige Anmeldung einschließlich
Passkey, Einladungen, Upload/Freischaltung, Viewer, Export, Backup, sudo-Rechtegrenze und DAST
an der zweiten Instanz prüfen. Rootlose Hashlisten prüfen die Isolation, ersetzen diese Probe
auf dem Host aber nicht.

## Härtung

Zusätzlich zur gehärteten Unit des Dienstes (ohne Netz nach außen, nur Schreibrecht auf den
Zustand, Systemaufruf-Filter, keine Capabilities) werden für Caddy empfohlen:

1. **Header auch auf Fehlerantworten:** `handle_errors` mit denselben Sicherheits-Headern wie der
   Site-Block und ohne `Server`/`Via` (Wortlaut in `Caddyfile.lokal`). Sonst fehlen HSTS, CSP und
   `nosniff` auf 404/413/502.
2. **`-Server` und `-Via`** im äußeren `header`-Block; die strenge CSP des Site-Blocks mit `?`
   setzen, damit der Viewer seine eigene CSP behält.
3. **Port 80 ohne Banner:** eigener Block `http://*.example.org { header -Server; redir
   https://{host}{uri} 308 }`.
4. **Admin-Schnittstelle von Caddy auf einen Unix-Socket:** globale Option
   `admin unix//run/caddy/admin.sock|0600`. Beim ersten Umstellen `systemctl restart caddy`.
5. **systemd-Härtung für Caddy:** `caddy-haertung.conf` nach
   `/etc/systemd/system/caddy.service.d/haertung.conf`, `systemctl daemon-reload && systemctl
   restart caddy`, danach das Journal von Caddy auf Schreibfehler prüfen (bei weiteren Sites unter
   derselben Caddy-Instanz ggf. `ReadWritePaths` ergänzen).
6. **IP-Adressen im Zugriffslog kürzen:** im `log`-Block `request>remote_ip` und
   `request>client_ip` mit `ip_mask { ipv4 24  ipv6 48 }`.
7. **Automatische Sicherheitsupdates des Betriebssystems** (z. B. `unattended-upgrades`)
   einschalten.

### Anmeldeschutz

| Ebene | Wirkung | Einstellung |
|---|---|---|
| Sperre je Konto | 5 Fehlversuche → 15 min gesperrt (Passwort-Anmeldung dieses Kontos) | fest |
| Globale Bremse | mehr als N Fehlversuche je Minute über alle Konten → jede Passwort-Anmeldung 429 bis zur nächsten Minute | `SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE` (Standard 30) |
| fail2ban | 10 Fehlversuche einer IP in 10 min → IP 1 h gesperrt | `fail2ban/scanview.local` |

Passkey-Anmeldungen und bestehende Sitzungen sind von Sperre und Bremse nicht betroffen.

```bash
sudo install -m 0644 deploy/platform/fail2ban/scanview.conf /etc/fail2ban/filter.d/scanview.conf
sudo install -m 0644 deploy/platform/fail2ban/scanview.local /etc/fail2ban/jail.d/scanview.local
sudo systemctl reload fail2ban
sudo fail2ban-client status scanview
```

Voraussetzung: Caddy reicht die Client-Adresse per `X-Forwarded-For` durch; der Dienst vertraut
dem Header nur von `127.0.0.1`.

## Aktualisieren

Updates werden als signierte Release-Artefakte ausgeliefert und mit
`einrichten.sh --release <ordner>` eingespielt. Der Aufruf mit einem Quelltext-Stand
(`einrichten.sh <pfad>`) bleibt für lokale Tests erhalten, meldet aber
`WARNUNG: kein geprüftes Release` und vermerkt das im Update-Protokoll.

**Einmalige Einrichtung je Host (Bootstrap).** Zwei Dinge liegen danach dauerhaft auf dem Host
und stammen nie aus einem ungeprüften Release: der Vertrauensanker und eine geprüfte Kopie der
Prüfskripte.

1. **Vertrauensanker:** Das Skript prüft Signaturen ausschließlich gegen
   `/etc/scanview/allowed_signers` — nie gegen eine Datei aus dem Release, sonst könnte ein
   Release seinen eigenen Schlüssel mitbringen. Die Datei stammt aus `nachweise/allowed_signers`
   des Quelltexts (siehe [`release.md`](release.md)); den Fingerabdruck vor der Installation über
   einen zweiten Weg abgleichen:

   ```bash
   awk '!/^#/ {print $3" "$4}' allowed_signers | ssh-keygen -lf -
   sudo install -d -m 0755 /etc/scanview
   sudo install -o root -g root -m 0644 allowed_signers /etc/scanview/allowed_signers
   ```

   Ein Schlüsselwechsel wird als neue Zeile angehängt und auf jedem Host von Hand nachgetragen.
2. **Bootstrap-Skript anlegen — aus dieser Anleitung, nie aus dem Release.** Die Schritte laufen
   nicht als lose Befehle in einer interaktiven root-Shell (dort liefe nach einer gescheiterten
   Prüfung der nächste Befehl trotzdem), sondern als Skript mit `set -euo pipefail`: jede Prüfung
   muss gelingen, bevor irgendetwas aus dem Release entpackt oder ausgeführt wird. Den folgenden
   Block als `/root/scanview-bootstrap.sh` speichern (root, 0700):

   ```bash
   #!/bin/bash
   # scanview-bootstrap.sh <upload-ordner> <X.Y.Z> — erste Einrichtung je Host, als root.
   set -euo pipefail
   export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin   # secure_path, nie der PATH der Shell
   umask 077
   [[ $# -eq 2 ]] || { echo "Aufruf: $0 <upload-ordner> <X.Y.Z>" >&2; exit 2; }
   UPLOAD=$1
   V=$2
   [[ $V =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "Version X.Y.Z erwartet" >&2; exit 2; }
   ANKER=/etc/scanview/allowed_signers   # Vertrauensanker: fest, nie aus der Umgebung
   WURZEL=/root
   K=$WURZEL/scanview-release/$V   # root-eigene Kopie des Release-Ordners
   S=$WURZEL/scanview-deploy/$V    # geprüfte Kopie der Prüfskripte
   [[ -f $ANKER && ! -L $ANKER ]] || { echo "Vertrauensanker $ANKER fehlt" >&2; exit 1; }
   [[ ! -e $K && ! -e $S ]] || { echo "$K oder $S existiert schon" >&2; exit 1; }
   install -d -m 0700 "$WURZEL/scanview-release" "$WURZEL/scanview-deploy"
   # 1. Root-eigene Kopie: der Upload-Ordner kann sich sonst zwischen Prüfen und Verwenden
   #    ändern. Nur reguläre Dateien und Ordner (Links werden nicht aufgelöst).
   cp -R --no-preserve=all -- "$UPLOAD" "$K"
   [[ -z $(find "$K" ! -type f ! -type d) ]] || { echo "unzulässiger Eintrag" >&2; exit 1; }
   cd "$K"
   # 2. Signaturen über genau die Bytes dieser Kopie (Manifest und Abschlussnachweis).
   /usr/bin/ssh-keygen -Y verify -f "$ANKER" -I release@scanview -n scanview-release \
     -s manifest.json.sig <manifest.json
   /usr/bin/ssh-keygen -Y verify -f "$ANKER" -I release@scanview -n scanview-abschluss \
     -s abschluss.json.sig <abschluss.json
   # 3. Inhalt: Version, kein Trockenlauf, Abschluss gehört zum Manifest, Prüfsummen der
   #    drei ausgelieferten Archive.
   /usr/bin/python3 -I - "$V" <<'PY'
   import hashlib, json, sys
   def pruefe(ok, was):
       if not ok:
           sys.exit(f"Abbruch: {was}")
   v = sys.argv[1]
   roh = open("manifest.json", "rb").read()
   m = json.loads(roh)
   a = json.loads(open("abschluss.json", "rb").read())
   pruefe(m["version"] == v and m["trockenlauf"] is False, "Version oder Trockenlauf")
   pruefe(a["version"] == v and a["manifest_sha256"] == hashlib.sha256(roh).hexdigest(),
          "Abschluss gehört nicht zu diesem Manifest")
   geliefert = [x for x in m["artefakte"] if x["ausgeliefert"]]
   pruefe(sorted(x["name"] for x in geliefert) == ["mcp", "server", "web"], "Artefakte")
   for x in geliefert:
       pruefe("/" not in x["datei"], x["datei"])
       ist = hashlib.sha256(open("artefakte/" + x["datei"], "rb").read()).hexdigest()
       pruefe(ist == x["sha256"], "Prüfsumme " + x["datei"])
   print(f"Release {v}: Signaturen und Prüfsummen gültig")
   PY
   # 4. Erst jetzt: Prüfskripte aus dem geprüften Server-Archiv entpacken und mit dieser
   #    Kopie einrichten (einrichten.sh prüft alles noch einmal vollständig).
   install -d -m 0700 "$S"
   tar -xzf "artefakte/scanview-server-$V.tar.gz" -C "$S" --no-same-owner \
     --no-same-permissions deploy/platform
   exec "$S/deploy/platform/einrichten.sh" --release "$K"
   ```

3. **Ausführen:** `sudo bash /root/scanview-bootstrap.sh <upload-ordner> X.Y.Z`. Jede Abweichung
   (Signatur, Abschluss, Prüfsumme, Trockenlauf, unzulässiger Eintrag) beendet das Skript mit
   Fehler, bevor etwas entpackt wird. Danach liegen die geprüfte Release-Kopie unter
   `/root/scanview-release/X.Y.Z` und die geprüfte Skriptkopie unter
   `/root/scanview-deploy/X.Y.Z`.
4. **Skriptkopie behalten:** Diese Kopie (oder die eines später geprüften Releases) ruft künftig
   `release-pruefen.sh` bzw. `einrichten.sh --release` auf.
5. **Folgende Updates** laufen genauso über eine root-eigene Kopie des Release-Ordners (Skript, Teil 1)
   und mit der geprüften Skriptkopie oder der aus dem zuletzt geprüften Release — nie mit dem
   `einrichten.sh` eines noch ungeprüften Releases. Die geprüften Release-Ordner unter
   `/root/scanview-release/` bis zur Abnahme des Nachfolgers aufbewahren: sie sind der Weg
   zurück (siehe unten).
6. **Neues Manifest-Schema:** Ein Release kann ein neueres Manifest-Schema tragen, als die
   vorhandene geprüfte Skriptkopie kennt (Beispiel: 0.2.0 mit `scanview-release-manifest/2`,
   Skriptkopie aus 0.1.0 kennt nur `/1`). Dann lehnt die alte Kopie es ab
   (`NACHWEIS-FEHLER manifest: unbekanntes Schema …`) — gewollt, nichts wird verändert. Ein
   solches Release wird wie beim ersten Mal über das Bootstrap-Skript eingespielt (Schritt 3):
   es prüft Signaturen und Prüfsummen selbst, entpackt erst danach die Prüfskripte des neuen
   Releases und richtet mit ihnen ein. Nie ein Prüfskript aus dem noch ungeprüften Release direkt
   aufrufen.

**Release-Ordner** — genau diese Einträge, jede weitere oder fehlende Datei bricht ab. Auf der
Release-Workstation stellt `scanview-nachweise deploy-ordner --version X.Y.Z --ziel <ordner>` ihn
aus dem Nachweis-Archiv zusammen:

```
<ordner>/manifest.json
<ordner>/manifest.json.sig
<ordner>/abschluss.json
<ordner>/abschluss.json.sig
<ordner>/artefakte/scanview-web-X.Y.Z.tar.gz
<ordner>/artefakte/scanview-server-X.Y.Z.tar.gz
<ordner>/artefakte/scanview-mcp-X.Y.Z.tar.gz
```

Der MCP-Server wird mitgeprüft, aber nicht auf dem Host installiert (er läuft beim Nutzer).

**Ablauf:**

1. Hochgeladenen Ordner als root in einen nur für root zugänglichen Ordner kopieren (wie im
   Bootstrap-Skript, Teil 1); `<ordner>` ist im Folgenden immer diese Kopie.
2. Nur prüfen (verändert nichts):
   `<geprüfte Kopie>/deploy/platform/release-pruefen.sh <ordner> /etc/scanview/allowed_signers`
   → Exit 0 oder `NACHWEIS-FEHLER <regel>: …`.
3. `sudo <geprüfte Kopie>/deploy/platform/einrichten.sh --release <ordner>`:
   - prüft die Signatur über genau die gelesenen Manifest-Bytes, den signierten
     Abschlussnachweis (nur vorhanden, wenn Archivierung und Tag des Releases gelungen sind;
     Namensraum `scanview-abschluss`, gehört zu genau diesem Manifest), das Manifest (vollständig: Exportcommit, SBOMs der ausgelieferten Artefakte, Berichte,
     Werkzeuge, Datenbankstand und jede Pflichtprüfung „bestanden“; kein
     Probe-Manifest mit `"trockenlauf": true`, ausgeliefert genau web, server, mcp), SHA-256 und
     SHA-512 jedes Archivs und dessen Inhalt (nur Dateien und Ordner unter den erwarteten Pfaden,
     keine absoluten Pfade, kein `..`, keine Links oder Gerätedateien);
   - entpackt aus den geprüften Kopien in ein temporäres Verzeichnis — **bei jeder Abweichung
     bricht es ab, bevor etwas verändert wird**;
   - bricht ab, bevor etwas verändert wird, wenn Reste einer Installation da sind
     (`/srv/scanview/state`, `/etc/scanview/scanview-server.env`, `/opt/scanview/server`,
     `/var/lib/scanview-install`), die
     Datenbank aber fehlt (fehlender Mount, verlorener Bestand gilt nie als Ersteinrichtung),
     oder wenn das Update-Protokoll nicht schreibbar ist;
   - sichert die Datenbank (`.backup` nach `/var/backups/scanview/update-sicherungen/`, nur für
     root lesbar und außerhalb des Dienstverzeichnisses; übernommen wird eine neue, root-eigene
     Kopie, die ein offen gehaltener Schreibzugriff des Dienstes nicht erreicht, und genau diese
     prüft `PRAGMA integrity_check` als `nobody` im Arbeitsspeicher — dafür etwa das Dreifache der
     Datenbankgröße an freiem Arbeitsspeicher einplanen; eine fehlerhafte Sicherung bleibt nur als
     `*.ungueltig` liegen) — Pflichtschritt, ohne gültige Sicherung kein Update;
   - führt die übrigen Schritte wie beim Einrichten aus und fragt `/api/gesundheit` ab;
   - schreibt je Lauf eine Zeile nach `/var/log/scanview/updates.log` (root, 0640):
     `<zeit> <version> <manifest-sha256> <quellcommit> sicherung=<pfad> artefakte=<name:sha256,…> ergebnis=ok|abgebrochen`;
     ein abgelehntes Release ebenfalls, mit `sicherung=- artefakte=- ergebnis=abgelehnt grund=<regel>`
     (Version und Quellcommit nur, wenn die Signatur schon gültig war, sonst `-`). Scheitert
     diese Zeile, endet der Lauf mit Fehler, auch wenn alles andere gelungen ist. Fehlen Kontakt
     oder Erklärung der Barrierefreiheit, hängt die ok-Zeile `warnung=barrierefreiheit-angaben-fehlen`
     an, fehlt der Link zu Impressum oder Datenschutzhinweisen, `warnung=impressum-datenschutz-fehlen`
     (je fehlender Angabe eine Kennung, also auch beide hintereinander;
     `warnung=barrierefreiheit-nicht-pruefbar` allein, wenn `/api/barrierefreiheit` nicht lesbar
     antwortet) — ein Hinweis, kein Abbruch.
4. **Caddy-Zweig übernehmen.** `einrichten.sh` fasst Caddy nicht an, `scanview.caddy` ändert sich
   aber mit Releases. Die Datei aus der geprüften Skriptkopie dieses Releases nach
   `/etc/caddy/scanview.caddy` übernehmen, mit der Umgebung des Caddy-Dienstes validieren und
   **erst bei bestandener Validierung** neu laden (die alte Fassung vorher sichern, sonst fehlt der
   Rückweg):

   ```bash
   sudo cp -p /etc/caddy/scanview.caddy /etc/caddy/scanview.caddy.vorher
   sudo install -o root -g root -m 0644 <geprüfte Kopie>/deploy/platform/scanview.caddy /etc/caddy/scanview.caddy
   sudo sh -c 'set -a; . /etc/caddy/cloudflare.env; set +a; \
     SCANVIEW_WEB=/opt/scanview/web SCANVIEW_DATA=/srv/scanview/data \
     caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile' \
     && sudo systemctl reload caddy
   ```

   Validiert wird mit **derselben Umgebung wie der Caddy-Dienst** (`systemctl cat caddy` zeigt
   `Environment=` und `EnvironmentFile=`; im Beispiel die Datei mit dem Token des DNS-Anbieters,
   ohne die das Zertifikats-Modul ein leeres Token meldet). Ohne die Variablen scheitert
   `caddy validate` an den Platzhaltern der Datei (`SCANVIEW_UPSTREAM` hat einen Standardwert).
   Besteht die Prüfung nicht, die gesicherte Fassung zurückkopieren und nicht neu laden; einen
   Rückfall erst an die Prüfung hängen, nicht an Befehle danach (sonst stellt er nach einem
   gelungenen Reload die alte Datei wieder her). Rechte und Gruppe der vorhandenen Datei
   übernehmen, wenn sie abweichen. Unterschiede vorher mit
   `sudo diff /etc/caddy/scanview.caddy <geprüfte Kopie>/deploy/platform/scanview.caddy` ansehen
   (die geprüfte Kopie liegt unter `/root`).
5. `/api/gesundheit`, Journal und `deploy/platform/test/caddy-pruefen.sh` prüfen (aus einem
   Quell-Checkout starten — das Prüfskript ist nicht Teil des Server-Archivs); Hinweise im
   [`CHANGELOG.md`](../../CHANGELOG.md) der Version beachten. Die Update-Sicherungen werden nicht
   automatisch rotiert — nach erfolgreicher Abnahme von Hand aufräumen.

**Zurück zur vorigen Version.** Steht im Update-Protokoll `ergebnis=abgebrochen` und kam der Lauf
über den Schritt „Code kopieren“ hinaus (Ausgabe `== 3.`), ist der Host teilweise aktualisiert.
Dann die vorige Version auf demselben geprüften Weg wieder einspielen:

1. `systemctl stop scanview-server`.
2. Datenbank aus der Sicherung wiederherstellen, deren Pfad in derselben Protokollzeile steht
   (`sicherung=…`) — wie im Abschnitt [„Sicherung“](#sicherung) beschrieben (aktuelle
   `scanview.db` samt `-wal`/`-shm` beiseitelegen, Sicherung als `scanview.db`, Eigentümer
   `scanview`, 0600). Nötig, weil Migrationen nur vorwärts laufen und der Dienst der neuen
   Version sie beim Start schon ausgeführt haben kann.
3. `einrichten.sh --release` mit dem aufbewahrten, root-eigenen Ordner der vorigen Version
   (`/root/scanview-release/<vorige>`) und der dazugehörigen geprüften Skriptkopie aufrufen. Es
   prüft erneut Signatur und Prüfsummen und schreibt eine eigene Protokollzeile.

**Abgebrochene Ersteinrichtung.** Vor der ersten Änderung an einem leeren Host legt
`einrichten.sh` den root-eigenen Marker `/etc/scanview/.ersteinrichtung-laeuft` an und entfernt
ihn erst nach erfolgreichem Lauf. Bricht die Ersteinrichtung ab, **bevor der Dienst zum ersten
Mal gestartet wurde** (etwa `uv sync` ohne Netz), setzt derselbe Aufruf sie beim nächsten Mal
einfach fort. Unmittelbar vor dem ersten Dienststart legt `einrichten.sh` den root-eigenen
Installationsstatus `/var/lib/scanview-install/installiert` an, der nie entfernt wird: ab dann
kann es eine Datenbank geben, und ein Host mit diesem Status gilt nie wieder als Ersteinrichtung
(fehlt die Datenbank, folgt der Abbruch unten); das
Update-Protokoll vermerkt `ersteinrichtung=fortgesetzt`. Nichts ist von Hand zu löschen, eine
schon angepasste `/etc/scanview/scanview-server.env` bleibt erhalten.

**Installation ohne Datenbank.** Liegen Reste einer Installation vor (`/srv/scanview/state`,
`/etc/scanview/scanview-server.env`, `/opt/scanview/server` oder
`/var/lib/scanview-install`), fehlt aber
`/srv/scanview/state/scanview.db` und ist es keine vor dem ersten Dienststart abgebrochene
Ersteinrichtung, bricht `einrichten.sh` ab, bevor
etwas verändert wird, und schreibt `ergebnis=abgebrochen grund=db-fehlt` ins Update-Protokoll.
Das ist nie eine Ersteinrichtung: meist fehlt ein Mount oder der Datenbestand ging verloren.
Zuerst den Mount bzw. die Platte prüfen, sonst die Datenbank aus einer Sicherung zurückholen
(Abschnitt [„Sicherung“](#sicherung), Update-Sicherungen unter
`/var/backups/scanview/update-sicherungen/`). Nur wenn der Host wirklich neu aufgesetzt werden
soll: alle oben genannten Reste bewusst entfernen (vorher die env-Datei sichern); die
Fehlermeldung von `einrichten.sh` zählt dieselben Pfade auf.

Datenbankmigrationen laufen beim Dienststart automatisch und nur vorwärts. Die installierte
Version zeigt `GET /api/v1/version` (angemeldet). Fristen und Supportzeitraum:
[`support-und-patches.md`](support-und-patches.md).

## Projektpakete bereitstellen

`deploy/platform/paket-bereitstellen.sh <paketordner> <ssh-ziel>` überträgt ein Paket in einen
Eingangsordner auf dem Host, prüft es dort und schaltet es frei.

- **Rechte auf dem Host:** Der Deploy-Nutzer braucht keine root-Shell. Er ist Mitglied der Gruppe
  `scanview-deploy` (von `einrichten.sh` angelegt; Mitglied von Hand:
  `sudo usermod -aG scanview-deploy <deploy-nutzer>`; er darf weder root noch Mitglied der Gruppe
  `scanview` sein, sonst verweigern Freischalt-Skript und `einrichten.sh`), lädt ohne sudo nach
  `/srv/scanview/eingang/<projekt>/` und darf per sudo nur das root-eigene Freischalt-Skript
  `/usr/local/sbin/scanview-paket-freischalten` starten. Die Regel
  `/etc/sudoers.d/scanview-deploy` installiert `einrichten.sh` nach `visudo -cf`.
- **Ältere Installationen:** Eine frühere, breite sudo-Regel für den Deploy-Nutzer (`bash`,
  `rsync`, `test`, `du`, `true`) machte ihn faktisch zu root. Sie ist von Hand zu entfernen
  (`sudo visudo -c`, danach `sudo -l -U <deploy-nutzer>` zeigt nur noch das Freischalt-Skript).
- Das Freischalt-Skript nimmt den Upload zuerst weg (für den Deploy-Nutzer unerreichbar), sperrt
  ihn und prüft erst dann: nur reguläre Dateien und Ordner, keine Hardlinks, keine ACLs oder
  anderen erweiterten Attribute, alles vom Deploy-Nutzer hochgeladen. Dann kopiert es den Inhalt
  in neue, nur root zugängliche Dateien (ein vorher geöffneter Schreibzugriff des Hochladenden
  erreicht die Kopie nicht; die Upload-Dateien werden gelöscht) und prüft nur noch die Kopie:
  `project.json` im Schema 2 mit passender `projectId`. Danach Rechte für den Dienst und Caddy,
  Freischalten, Katalog neu laden. Das Paket braucht dafür zweimal seine Größe an freiem Platz
  (plus 10 GiB Reserve). Scheitert eine Prüfung, bleibt das Paket unsichtbar in
  `/srv/scanview/data/.freischalten-<projekt>` (bzw. `….kopie`) liegen; die Meldung nennt den
  Befehl zum Entfernen (root).

- Nur Pakete im Schema 2; Zielname ist die `projectId` aus `project.json`.
- Ein vorhandenes Paket wird nie überschrieben; Ersetzen heißt: altes Paket entfernen, dann neu
  bereitstellen.
- Symlinks werden beim Übertragen aufgelöst und auf dem Host abgelehnt.
- Ein abgebrochener Upload bleibt im Eingang (außerhalb der ausgelieferten Daten) liegen und wird
  beim nächsten Lauf fortgesetzt.
- Nach neuem oder entferntem Paket: `sudo systemctl reload scanview-server` (das Skript tut es
  selbst). Das Journal meldet `Katalog: N Pakete`; abgelehnte Pakete stehen dort als Warnung
  `Paket … übersprungen` mit Grund.

## Konten

```bash
sv einladen --name "Vorname Name" --email person@example.org --projekt <projekt>=betrachter
sv einladen --name "Admin" --email admin@example.org --admin
sv nutzer
```

Rollen je Projekt: `betrachter`, `rueckmelder`, `bearbeiter`, `projektleitung`; Admins sehen alle Projekte. Die
Ausgabe ist ein Einladungslink (einmalig, 7 Tage gültig), der nur über einen vertraulichen Kanal
weitergegeben werden darf. Die Verwaltung im Portal (`/verwaltung`, nur Admins) bietet dieselben
Funktionen.

### Rolle Rückmelder

Für Personen, die Befunde ansehen und dazu antworten sollen, ohne die Arbeit der Fachleute zu
verändern (etwa Auftraggeber oder Nutzer eines Gebäudes). Vergeben wird sie in der Verwaltung des
Portals oder bei der Einladung per `sv einladen … --projekt <projekt>=rueckmelder`.

Ein Rückmelder

- sieht alles, was ein Betrachter sieht, auch alle Befunde und Rückmeldungen mit dem Namen des
  Urhebers;
- legt eigene Einträge an und ändert oder löscht nur diese. Der Server erzwingt dabei die
  Kategorie „Rückmeldung“; ein Eintrag mit anderer Kategorie wird abgewiesen;
- hat höchstens 100 aktive Einträge je Projekt (zusätzlich zur Grenze des Projekts);
- misst nur lokal im Browser, ändert keine Projektbeschreibung und sieht weder Export noch
  Verlauf.

Ein API-Token eines Rückmelders hat nie mehr Rechte als die Rolle; ein Lese-Token schreibt gar
nicht.

Grenzen, die Betreuende eines Projekts kennen sollten:

- **Schutz eines Befunds hängt an der Kategorie.** Eine Rückmeldung bleibt für ihren Urheber
  änderbar, solange sie die Kategorie „Rückmeldung“ trägt — auch wenn ein Bearbeiter Titel oder
  Notiz inzwischen überarbeitet hat. Wer aus einer Rückmeldung einen Befund machen will, der nicht
  mehr überschrieben werden darf, ändert die Kategorie.
- **Eine so übernommene Rückmeldung kann ihr Urheber nur noch löschen**, nicht mehr ändern und
  nach dem Löschen nicht wiederherstellen.
- **Was die Betreuung gelöscht hat, bleibt gelöscht:** Eine von einem Bearbeiter gelöschte
  Rückmeldung kann der Rückmelder nicht wiederherstellen; selbst gelöschte eigene Rückmeldungen
  schon. Einen neuen Eintrag kann er jederzeit anlegen (innerhalb der Grenze).
- **Frühere Einträge zählen mit.** Wer vorher Bearbeiter war und zum Rückmelder herabgestuft
  wird, behält seine Einträge. Sie zählen gegen die Grenze von 100, und Einträge mit anderer
  Kategorie als „Rückmeldung“ kann er nur noch löschen, nicht mehr ändern.

Die Rolle kommt mit der Datenbankmigration 0009. Sie läuft beim ersten Start der neuen Version
automatisch und baut dabei die Tabelle der Projektrollen neu auf; die Rollen bleiben erhalten.
Zurück geht es wie bei jeder Migration nur über die Sicherung, die `einrichten.sh` vor dem Update
anlegt (Abschnitt „Aktualisieren“).

### Projektleitung

Für die Person, die ein Bauprojekt organisiert (etwa die Bauherrin). Die Instanzverwaltung macht sie
zur Projektleitung; danach pflegt sie ihr Projekt ohne Hilfe der Administration, auf der Portalseite
„Projekt-Einstellungen“ (Link auf der Projektkarte).

Eine Projektleitung

- darf alles, was ein Bearbeiter darf;
- lädt Beteiligte in ihr Projekt ein oder fügt bestehende Konten hinzu, vergibt und entzieht die
  Rollen Betrachter, Rückmelder und Bearbeiter, legt Gewerke an und ordnet sie zu, pflegt die
  Kategorien und die Schalter des Projekts und sieht im Verlauf zusätzlich die Rechte-,
  Einladungs- und Einstellungsereignisse ihres Projekts;
- vergibt, entzieht oder ändert **nie** die Rolle Projektleitung (auch nicht ihre eigene), ändert
  keine Admins und keine andere Projektleitung, setzt keine Konten zurück, sperrt oder
  anonymisiert keine, legt keine Projekte an und lädt keine Pakete hoch;
- verwaltet nur im Browser: Ein API-Token, auch mit Schreibrecht, verwaltet nichts.

Vergeben wird die Rolle in der Verwaltung des Portals (Einladung oder Rechte-Matrix, Rolle
„Projektleitung“) oder bei der Einladung per
`sv einladen … --projekt <projekt>=projektleitung`.

**Projekt einrichten.** Vor der Übergabe wählt ein Admin auf „Projekt-Einstellungen“ die Vorlage:
„Bestandsaufnahme“ (Kategorien Raum, Anlage, Zugang, Mangel, Sonstiges) oder „Bauprojekt“ (Mangel,
Frage, Hinweis, Entscheidung, Sonstiges). Ändert vorher jemand Kategorien oder Schalter, gilt
„Bestandsaufnahme“, und die Vorlage lässt sich nicht mehr wechseln. Die Kategorien sind die Startwerte;
danach pflegt sie die Projektleitung.

**Einladung durch die Projektleitung.** Ist die E-Mail-Adresse neu, entsteht ein Einladungslink
(7 Tage gültig, einmal verwendbar), den die Projektleitung selbst weitergibt; die Plattform verschickt
nichts. Hat die Adresse schon ein Konto, gilt der Zugriff sofort, ohne Link und ohne Zurücksetzen der
Anmeldung; eine vorhandene gleiche oder höhere Rolle wird nie herabgestuft, und ein gesperrtes Konto
bleibt gesperrt (Freischalten nur in der Verwaltung). Die Projektleitung erfährt auf diesem Weg, ob eine
E-Mail-Adresse schon ein Konto hat.

**Gewerke** sind Zuordnungen je Projekt (Name, Kurzzeichen bis 4 Zeichen, Bauherrenseite ja oder nein;
höchstens 50, eines je Person). Das Löschen eines Gewerks lässt Zugeordnete „ohne Gewerk“ zurück.
Solange das Gewerk an einem nicht gelöschten Befund zuständig ist (gleich welcher Status), lehnt der
Server das Löschen ab und nennt die Anzahl; erst dort die Zuständigkeit ändern. Nur an weich
gelöschten Befunden wird die Zuständigkeit beim Löschen des Gewerks leer.

**Offene Einladungen** sind je Projekt auf 200 begrenzt (noch nicht eingelöst, noch nicht abgelaufen).
Darüber weist der Server weitere Einladungen mit einer Meldung ab; Widerrufen oder Ablauf macht wieder
Platz.

**Kategorien** sind die Vorschläge im Viewer, in der Reihenfolge der Liste (höchstens 30). Es gibt nur
Anhängen, Ausschalten und Umordnen, kein Löschen und kein Umbenennen. Der Server nimmt weiterhin jede
Kategorie bis 60 Zeichen an (Pakete, Import, API); „Rückmeldung“ ist fest.

**Schalter.** „Rückmeldungen erlaubt“ aus: Rückmelder wirken überall wie Betrachter. „Export für
Bearbeiter“ aus: nur Projektleitung und Admins exportieren CSV, JSON und Mängellisten (auch per
Token). Der Export-Schalter ordnet den Ablauf, er ist keine Datenschutzgrenze: Bearbeiter lesen
dieselben Daten weiterhin über die Listen der Portal- und API-Ansichten (POIs, Messungen).
„Befund-Fäden“: ab Werk an bei der Vorlage „Bauprojekt“, aus bei „Bestandsaufnahme“ (Abschnitt
„Befund-Faden“); aus heißt: alles wie ohne Fäden, die Faden-Wege antworten mit 404, vorhandene
Einträge bleiben erhalten und erscheinen beim Wiedereinschalten wieder.

### Befund-Faden

Mit eingeschaltetem Schalter hat jeder Befund (POI) einen Faden: Kommentare, Links, Statuswechsel,
Zuständigkeit, Frist und automatisch jede Änderung an Titel, Kategorie, Notiz, Position oder
Standpunkt — in zeitlicher Reihenfolge, mit Person und Zeit. Einträge lassen sich weder ändern noch
löschen; wer einen Kommentar oder Link geschrieben hat, kann ihn **zurückziehen**: Der Text ist dann
für alle ersetzt, Person und Zeit bleiben sichtbar, der Ursprungstext bleibt im Bearbeitungsprotokoll.

**Status:** `offen` → `in_arbeit` → `erledigt` (von der Firma gemeldet) → `abgeschlossen` (von der
Bauherrenseite geprüft); daneben `abgelehnt` (kein Mangel, nicht zuständig). Abgelehnt und
Wiederöffnen (zurück nach `offen` aus erledigt, abgeschlossen oder abgelehnt) verlangen eine
Begründung. Jede Änderung an Status, Zuständigkeit oder Frist braucht die zuletzt gelesene Version
(sonst 409 mit dem aktuellen Stand); Kommentare nicht.

**Wer darf was:** Bauherrenseite sind Projektleitung, Admins und Bearbeiter in einem Gewerk mit
„Bauherrenseite“. Nur sie schließen ab, lehnen ab und öffnen wieder. Bearbeiter setzen Zuständigkeit
(ein Gewerk, optional eine Person daraus), Frist und die übrigen Status. Rückmelder kommentieren an
jedem Befund und setzen `in_arbeit`/`erledigt`, wenn sie selbst oder ihr Gewerk zuständig sind.
Betrachter lesen. Verlässt eine zuständige Person das Projekt, bleibt sie eingetragen.

**Interne Einträge** (nur Kommentar oder Link) schreiben und lesen nur die Bauherrenseite, nie per
Lese-Token. Alle anderen sehen sie nirgends: nicht im Faden, in der Befundliste, in der Mängelliste,
im CSV-/JSON-Export, im Verlauf oder über den MCP-Server.

**Frist** ist ein Kalendertag; überfällig ab dem Folgetag (Europe/Berlin), solange der Befund offen
oder in Arbeit ist. Die Liste „Fällig/überfällig“ (`GET /api/v1/projekte/{id}/befunde`) zeigt
überfällige Befunde zuerst; die Projektkarte zählt die überfälligen, für die man selbst oder das
eigene Gewerk zuständig ist.

Grenzen: Text bis 4000 Zeichen, höchstens 1000 eigene Einträge je Befund (öffentliche und interne
getrennt gezählt; automatische Markierungseinträge zählen nicht), Links nur mit `https://` oder als
Befundlink dieser Instanz. Nach außen trägt jeder Eintrag eine zufällige Kennung (`schluessel`),
keine fortlaufende Nummer.

Die Fäden kommen mit der Datenbankmigration 0011. Sie ergänzt die Befunde um Status, Zuständigkeit
und Frist (vorhandene Befunde: `offen`, ohne Zuständigkeit und Frist), legt die Tabelle der
Einträge an und schaltet den Schalter für vorhandene Projekte nach ihrer Vorlage. Vorher sichern;
zurück geht es nur über die Sicherung.

Die Rolle kommt mit der Datenbankmigration 0010. Sie läuft beim ersten Start der neuen Version
automatisch, baut die Tabelle der Projektrollen neu auf (die Rollen bleiben erhalten), ergänzt die
Einladungen um Projekt und Gewerk und legt die Tabellen für Gewerke, Projekt-Einstellungen und
Kategorien an. Vorher sichern; zurück geht es nur über die Sicherung, die `einrichten.sh` vor dem
Update anlegt (Abschnitt „Aktualisieren“).

```bash
sv widerrufen --email person@example.org          # offene Einladungen ungültig machen
sv sperren --email person@example.org             # Konto sperren, Sitzungen und Tokens beenden
sv entsperren --email person@example.org
sv entziehen --email person@example.org --projekt <projekt>
sv entziehen --email person@example.org --alle
sv abmelden-ueberall --email person@example.org   # Sitzungen und API-Tokens beenden
sv einladen --name "…" --email person@example.org --zuruecksetzen   # neuer Zugang
sv anonymisieren --email person@example.org       # Personendaten löschen (unwiderruflich)
```

Rechteänderungen wirken ab der nächsten Anfrage. Ein Zurücksetzen ersetzt alle Anmeldemittel
des Kontos; die Projektrechte bleiben.

## API, Tokens und MCP-Server

- Alle Funktionen von Portal und Viewer sind REST-Routen unter `/api/v1/…`; die
  OpenAPI-Beschreibung gibt es angemeldet unter `/api/v1/openapi.json`.
- Persönliche API-Tokens (Portal → Einstellungen → „API-Zugänge“): Umfang lesen bzw. lesen und
  schreiben, Gültigkeit bis 365 Tage, höchstens 10 je Konto, nur einmal sichtbar, in der
  Datenbank nur als Hash. Tokens gelten nur für `/api/v1/*` ohne Verwaltung, Statistik und Konto
  und nie für Paketdateien; 120 Anfragen je Minute und Token.
- Der MCP-Server läuft beim Nutzer, nicht auf dem Host. Einrichtung und Datenschutzhinweise:
  `mcp-server/README.md`.

## Aufzeichnung sicherheitsrelevanter Vorgänge

| Was | Wo | Aufbewahrung |
|---|---|---|
| Konto-, Rechte-, Einladungs- und Token-Änderungen (angelegt, gesperrt, zurückgesetzt, anonymisiert, Recht vergeben/entzogen, Token erstellt/widerrufen) | Bearbeitungsprotokoll (Datenbank) | unbegrenzt, unveränderlich |
| Änderungen an Metadaten, Merkpunkten und Messungen mit Vorher/Nachher | Bearbeitungsprotokoll | unbegrenzt, unveränderlich |
| schreibende Nutzung per API-Token (mit Token-Name) | Bearbeitungsprotokoll | unbegrenzt |
| fehlgeschlagene Passwort-Anmeldung (gekürzter Konto-Schlüssel, IP) | Systemjournal (WARNING) | nach Journal-Rotation des Hosts |
| erfolgreiche Anmeldung | Zeitpunkt der letzten Anmeldung am Konto | bis zum Anonymisieren |
| Zugriffsentscheidungen für Paketdateien (Status, Projekt, Dauer) | Systemjournal, nur bei `SCANVIEW_LOG_LEVEL=DEBUG` | nach Journal-Rotation |
| Lesezugriffe auf Projekte (Konto, Zeit, Projekt, Art), je 15 Minuten zusammengefasst | Lesezugriffsprotokoll (Datenbank) | `SCANVIEW_LESEZUGRIFFE_TAGE`, Standard 90 Tage, dann automatisch gelöscht |
| jede HTTP-Anfrage (IP-Adresse, Pfad, Status, Zeit) | Zugriffslog von Caddy — Log des Betreibers, nicht der Anwendung | nach Einstellung des Betreibers; IP-Adressen gekürzt nach Härtung 6 |

Das Bearbeitungsprotokoll enthält Nutzer-IDs, aber keine Namen, E-Mail-Adressen, IP-Adressen
oder Tokens. Lesen: Portal → „Verlauf“ je Projekt (Bearbeiter, Admin), gesamt per
`GET /api/v1/protokoll` bzw. `/api/v1/protokoll.jsonl` (Admin).

**Abschalten:** Das Bearbeitungsprotokoll lässt sich nicht abschalten. Es ist der Nachweis, wer
wann was geändert hat, gegenüber allen Beteiligten eines Projekts, und enthält nur das dafür
Nötige. Die Ausführlichkeit des Systemjournals steuert `SCANVIEW_LOG_LEVEL` (`DEBUG`, `INFO`,
`WARNING`; Standard `INFO`); die Zeile für fehlgeschlagene Passwort-Anmeldungen bleibt auf jeder
Stufe, weil fail2ban sie braucht. Wer auf fail2ban verzichtet, kann die IP-Angabe über das
Journal des Hosts begrenzen (Aufbewahrung, `journald`-Einstellungen).

## Lesezugriffsprotokoll — Datenschutz

Neben dem Bearbeitungsprotokoll führt der Dienst ein Lesezugriffsprotokoll: wer hat wann welches
Projekt gelesen. **Das Protokoll ist personenbezogen** und deshalb bewusst eng gefasst.

- **Zweck:** ausschließlich die Nachvollziehbarkeit des Need-to-know und die Aufklärung von
  Sicherheitsvorfällen. Es dient **nicht** der Leistungs- oder Verhaltenskontrolle und darf dafür
  nicht ausgewertet werden.
- **Rechtsgrundlage und Mitbestimmung:** Der Betreiber klärt sie für seinen Einsatz selbst, bei
  Beschäftigten gegebenenfalls einschließlich der Mitbestimmung des Betriebsrats. Die Plattform
  nimmt dem Betreiber diese Klärung nicht ab; wer sie nicht leisten will oder kann, schaltet das
  Protokoll ab (siehe unten).
- **Inhalt:** Konto, Zeit, Projekt und Art des Zugriffs (Projekt geöffnet, Projektdaten geladen,
  Export, Lesen von Merkpunkten, Messungen oder Verlauf), dazu der Kanal (Web oder API) und bei
  API-Zugriff der Name des Tokens. **Keine** IP-Adresse, **kein** Gerät oder User-Agent, **keine**
  E-Mail-Adresse, nie ein Sitzungs- oder Token-Wert. Die Zeit ist der erste Zugriff im
  15-Minuten-Fenster: Wer ein Projekt dauerhaft offen hat, erzeugt je Fenster eine Zeile, nicht
  eine je Anfrage. Protokolliert wird erst nach bestandener Rechteprüfung, nie bei abgewiesenen
  Anfragen.
- **Aufbewahrung:** `SCANVIEW_LESEZUGRIFFE_TAGE`, Standard **90 Tage**; erlaubt sind 7 bis 730
  Tage oder `0`. Abgelaufene Einträge löscht der Dienst selbst (beim Start, einmal je Stunde
  beim Erfassen und vor jeder Einsicht, auch vor dem Export); es gibt keinen anderen Löschweg und
  keine nachträgliche Änderung. Gelöschte Einträge stecken noch in den täglichen Sicherungen
  (bis sie nach 7 Tagen auslaufen), in den Update-Sicherungen (werden nicht automatisch rotiert,
  siehe „Aktualisieren“) und bis zu Checkpoint und `VACUUM` im WAL bzw. in freien Seiten der
  Datenbank; entfernen wie unter „Daten löschen und übertragen“, „Reste auf der Platte“. Bei `0`
  ist das Protokoll aus: es wird nichts mehr erfasst, und vorhandene Einträge werden beim nächsten
  Start gelöscht. Der Dienst schreibt beim Start eine
  Logzeile mit dem wirksamen Wert und der Zahl der gelöschten Einträge (bei `0` als WARNING).
  **Vor dem Verkürzen der Frist oder dem Abschalten den JSON-Lines-Export sichern**
  (`/api/v1/lesezugriffe.jsonl`), besonders während einer laufenden Vorfallaufklärung: Das
  Löschen ist endgültig.
- **Einsicht:** nur Admins, angemeldet per Browser-Sitzung (Portal → Verwaltung → „Lesezugriffe“,
  filterbar nach Projekt und Konto; geladen wird erst auf den Knopf „Anzeigen“, nicht beim Öffnen
  der Verwaltung). Mit einem API-Token ist das Protokoll auch für ein
  Admin-Konto nicht erreichbar. **Jede Einsicht wird selbst protokolliert** (je Projekt, das in
  der gezeigten Seite oder im Export vorkommt); sie erscheint beim nächsten Abruf in der Liste.
- **Auskunft nach Art. 15 DSGVO:** Der Admin-Export als JSON Lines
  (`/api/v1/lesezugriffe.jsonl?nutzer=<Konto-ID>`) liefert alle Einträge eines Kontos.
- **Anonymisieren eines Kontos** (Abschnitt „Daten löschen und übertragen“) lässt die Einträge
  stehen, sie zeigen danach aber nur „Gelöschtes Konto n“. Sie laufen wie alle anderen mit der
  Aufbewahrungsfrist ab.
- **Abgrenzung:** Das Bearbeitungsprotokoll wird nie gelöscht und geht in den Git-Export; das
  Lesezugriffsprotokoll wird nach der Frist gelöscht und gelangt nie in den Git-Export.
- **Nicht erfasst:** die Projektliste (nur Karten, kein Projektinhalt) und das globale
  Administrationsprotokoll.

Fällt die Erfassung aus (Datenbank nicht beschreibbar), schlägt die zugehörige Anfrage fehl,
statt unprotokolliert zu lesen.

## Integrität und Meldung von Beschädigungen

- **Programm:** Release-Artefakte tragen SHA-256-Prüfsummen in einem signierten Manifest.
  Vorgesehen ist, dass die Installation sie vor jeder Änderung prüft und mit Meldung abbricht
  (Abschnitt „Aktualisieren“, in Vorbereitung). Bis dahin prüft der Betreiber selbst:
  Signatur mit `ssh-keygen -Y verify`, Prüfsummen mit `scanview-nachweise manifest pruefen
  manifest.json --artefakte <ordner>` bzw. den signierten Tag mit `git tag -v`; jede Abweichung
  meldet das Werkzeug mit Exit ≠ 0.
- **Projektpakete:** Beim Start und nach jedem Neuladen prüft der Dienst jedes Paket (Schema,
  `projectId`, Ordnername, Symlinks, lesbares `project.json`). Beschädigte Pakete werden nicht
  angeboten und als Warnung `Paket … übersprungen` mit Grund ins Journal geschrieben.
- **Datenbank:** SQLite im WAL-Modus; Bearbeitungsprotokoll per Trigger unveränderlich. Der
  Restore-Test (unten) prüft jede Sicherung mit `PRAGMA integrity_check`; alles andere als `ok`
  ist eine Beschädigung — dann die nächstältere Sicherung prüfen und den Vorfall melden
  (Kontakt im README).

## Meldeweg: security.txt

Der vertrauliche Meldeweg steht in `SECURITY.md`; zusätzlich wird er maschinenlesbar nach
RFC 9116 unter `/.well-known/security.txt` ausgeliefert, samt Richtlinie
(`/.well-known/sicherheitsrichtlinie.txt`) und öffentlichem OpenPGP-Schlüssel
(`/.well-known/psirt-openpgp.asc`). Die drei Pfade bedient Caddy öffentlich und ohne Anmeldung
aus dem Verzeichnis `/etc/scanview/meldeweg` (Umgebung `SCANVIEW_MELDEWEG`); `einrichten.sh`
legt es leer an und schreibt nie hinein. **Ist das Verzeichnis leer, antworten die Pfade mit
404** — das ist der Sollzustand, solange kein Meldepostfach eingerichtet ist (eine genannte,
aber nicht erreichbare Adresse wäre eine falsche Zusage). Prüfen:
`curl -sI https://<host>/.well-known/security.txt` → `404`.

**Aktivieren** (erst wenn das Postfach eingerichtet und eine Testmeldung angekommen ist):

1. Postfach einrichten und eine Testmail von außen empfangen. OpenPGP-Schlüssel erzeugen; den
   öffentlichen Teil als `psirt-openpgp.asc` (`gpg --armor --export <fingerabdruck>`) bereitlegen.
   Der Export enthält den Signier-Unterschlüssel mit; der Schlüssel, mit dem `--clearsign` signiert,
   muss darin stehen, sonst scheitert die Prüfung in Schritt 4.
   Marker und Platzhalter in `SECURITY.md` durch Adresse und Fingerabdruck ersetzen.
2. Je Instanz eine eigene Datei erzeugen (die Adresse der einen Instanz steht nie in der Datei
   der anderen) und mit dem Schlüssel klar signieren:

   ```bash
   scanview-nachweise securitytxt erzeugen --kontakt <postfach> \
     --schluessel-url https://<host>/.well-known/psirt-openpgp.asc \
     --kanonisch https://<host>/.well-known/security.txt \
     --richtlinie-url https://<host>/.well-known/sicherheitsrichtlinie.txt > security.txt.roh
   gpg --clearsign --output security.txt security.txt.roh
   ```

   `erzeugen` verweigert, solange `SECURITY.md` den Postfach-Marker trägt. `Expires` liegt
   standardmäßig 180 Tage in der Zukunft (höchstens 364, `--tage`).
3. Dateien `security.txt`, `sicherheitsrichtlinie.txt` (Kopie von `SECURITY.md`) und
   `psirt-openpgp.asc` nach `/etc/scanview/meldeweg/` legen (root:root, 0644); Caddy muss nicht
   neu geladen werden.
4. Von außen prüfen — Abruf, Felder, `Expires` und Signatur gegen den veröffentlichten Schlüssel:

   ```bash
   scanview-nachweise securitytxt pruefen \
     --url https://<host>/.well-known/security.txt --schluessel psirt-openpgp.asc
   ```

   Mit `--archiv <pfad> --instanz L|S` wird das Ergebnis als Nachweis im Archiv abgelegt.
   `deploy/platform/test/caddy-pruefen.sh` erwartet danach `MELDEWEG_AKTIV=1`.

**Wartung (vierteljährlich):** `Expires` erneuern (Schritte 2 bis 4 wiederholen) und den Ablauf
des OpenPGP-Schlüssels prüfen. Bis zur Aktivierung: prüfen, ob Postfach und Schlüssel inzwischen
vorhanden sind.

## Sicherung

`scanview-backup.timer` (täglich 03:15) sichert die Datenbank per SQLite-Online-Backup nach
`/srv/scanview/state/backups/JJJJ-MM-TT.db` und behält die 7 neuesten.

```bash
systemctl list-timers scanview-backup.timer
sudo systemctl start scanview-backup.service     # sofort sichern
```

Die Sicherungen liegen auf derselben Platte wie das Original. Sie schützen vor Fehlbedienung,
nicht vor Plattenverlust — dafür zusätzlich eine Sicherung des ganzen Hosts oder des Verzeichnisses
`/srv/scanview/state` an einen anderen Ort einrichten. Projektpakete sind Kopien der beim
Aufbereiter liegenden Originale.

### Restore-Test (regelmäßig, mindestens nach jeder Schemaänderung)

```bash
t=$(sudo -u scanview mktemp -d)
neueste=$(sudo -u scanview bash -c 'ls -1t /srv/scanview/state/backups/*.db | head -1')
sudo -u scanview cp "$neueste" "$t/scanview.db"
sudo -u scanview sqlite3 "$t/scanview.db" 'PRAGMA integrity_check'      # erwartet: ok
sudo -u scanview bash -c 'set -a; . /etc/scanview/scanview-server.env; set +a; SCANVIEW_STATE='"$t"' exec /opt/scanview/server/.venv/bin/scanview-server nutzer'
sudo rm -r "$t"
```

Erwartet: `ok` und dieselben Nutzer wie `sv nutzer`.

**Restore-Probe mit Nachweis:** `sudo deploy/platform/test/restore-probe.sh [--instanz NAME]
[--sicherung DATEI] [--port PORT] [--ausgabe DATEI]` spielt die neueste (oder die angegebene)
Sicherung auf eine Wegwerf-Instanz zurück und schreibt das Ergebnis als JSON. Geprüft werden
Integrität, Schema, das Bearbeitungsprotokoll (jedes Ereignis der Sicherung muss unverändert in der
laufenden Datenbank stehen), der Start eines Wegwerf-Servers auf der Sicherung (nur `127.0.0.1`,
Standardport 18899, muss frei sein; migriert auf den aktuellen Stand) und das Lesen der Konten. Die
laufende Instanz wird nur gelesen. Für eine Sicherung außer Haus diese erst zurückholen und mit
`--sicherung` angeben.

**Wiederherstellen:** Dienst stoppen, aktuelle `scanview.db` (mit `-wal`, `-shm`) beiseitelegen,
Sicherung als `scanview.db` einsetzen (Eigentümer `scanview`, 0600), Dienst starten.

## Daten löschen und übertragen

- **Konto löschen:** `sv anonymisieren` entfernt in einer Transaktion Name, E-Mail, Sitzungen,
  Passkeys, Passwort, Tokens, Projektrechte und offene Einladungen; das Konto bleibt als
  gesperrte, namenlose Zeile, weil das Bearbeitungsprotokoll auf seine ID verweist.
  Freitexte in Merkpunkten, Messungen, Beschreibungen und Kommentaren der Befund-Fäden gehören zum
  unveränderlichen Verlauf und werden nicht geschwärzt; die Eingabedialoge weisen darauf hin, dort keine Personendaten
  einzutragen.
- **Reste auf der Platte** nach dem Anonymisieren entfernen:

  ```bash
  systemctl stop scanview-server
  sudo -u scanview sqlite3 /srv/scanview/state/scanview.db 'PRAGMA wal_checkpoint(TRUNCATE); VACUUM;'
  systemctl start scanview-server
  ```

  Die Sicherungen der letzten 7 Tage enthalten die Daten, bis sie auslaufen; muss es sofort sein,
  die betroffenen Sicherungen löschen.
- **Projekt entfernen:** Paketordner unter `/srv/scanview/data/` löschen und
  `systemctl reload scanview-server`. Metadaten, Merkpunkte und Messungen des Projekts bleiben in
  der Datenbank, bis die Installation entfernt wird.
- **Daten übertragen:** Bearbeiter exportieren Merkpunkte und Messungen je Projekt als CSV oder
  JSON (Portal-Karte „Export“ bzw. `GET /api/v1/projekte/{id}/export.csv|json`). Der Export ist
  nur angemeldet und mit Recht auf das Projekt möglich und läuft über HTTPS; CSV-Zellen, die als
  Formel gelesen würden, werden entschärft. Der optionale Git-Export überträgt per SSH mit
  geprüftem Host-Schlüssel.
- **Installation entfernen:**

  ```bash
  sudo systemctl disable --now scanview-server.service scanview-backup.timer scanview-git-export.timer
  sudo rm /etc/systemd/system/scanview-server.service \
    /etc/systemd/system/scanview-backup.{service,timer} /etc/systemd/system/scanview-git-export.{service,timer}
  sudo systemctl daemon-reload
  # Caddy: Einbindung von scanview.caddy entfernen, /etc/caddy/scanview.caddy löschen, caddy reload
  sudo rm -f /etc/fail2ban/filter.d/scanview.conf /etc/fail2ban/jail.d/scanview.local
  sudo rm -f /etc/sudoers.d/scanview-deploy /usr/local/sbin/scanview-paket-freischalten
  sudo visudo -c
  sudo rm -r /opt/scanview /etc/scanview /srv/scanview   # löscht Datenbank, Sicherungen und Pakete
  sudo userdel scanview
  sudo groupdel scanview-deploy
  ```

  Vorher sichern, falls Daten aufbewahrt werden müssen. Externe Sicherungen des Hosts separat
  löschen.

## Befund-/Mängelliste exportieren

Die Projektkarte im Portal bietet „Mängelliste XLSX“ zum Weiterbearbeiten in einer
Tabellenkalkulation und „Mängelliste drucken“ für eine Druckansicht. Dort öffnet
„Drucken / als PDF speichern“ den Druckdialog des Browsers; als Ziel einen Drucker oder
„Als PDF speichern“ wählen. Die Druckansicht verwendet A4 im Querformat.

Beide Ansichten enthalten die aktiven Befunde (POIs), nummeriert nach Erstellungszeit,
mit Titel, Kategorie, Notiz, Standpunkt, Weltkoordinaten in Metern (drei Nachkommastellen),
Urhebern und Erstellungs-/Änderungszeiten in UTC; mit eingeschalteten Befund-Fäden zusätzlich
Status, Zuständig (Gewerk-Kurzzeichen / Person) und Frist. Dieselben drei Spalten bekommt der
CSV-Export; der JSON-Export enthält je Befund außerdem den Faden (interne Einträge nur für die
Bauherrenseite). Projektname, Paket-Revision und Exportzeit
stehen im Kopf. Gelöschte Befunde und Messungen werden nicht aufgenommen.

Bearbeiter, Projektleitung und Admins dürfen exportieren. Ist „Export für Bearbeiter“
abgeschaltet, bleiben die Mängellisten der Projektleitung und Admins vorbehalten.
Die Server-Endpunkte `GET /api/v1/projekte/{id}/maengelliste.xlsx` (Download) und
`GET /api/v1/projekte/{id}/maengelliste` (Druckdaten) prüfen diese Rechte wie der CSV-Export.
Texte in der XLSX-Datei bleiben Text, auch wenn sie mit einem Formelzeichen beginnen.

## Git-Export der Bearbeitungen (optional)

Ab Werk aus. `SCANVIEW_GIT_EXPORT=1` in der Umgebungsdatei und erneutes `einrichten.sh` schalten
ihn ein (`scanview-git-export.timer`, alle 5 Minuten): Je Projekt entsteht unter
`/srv/scanview/state/git/<projekt>/` ein Repository mit Metadaten, Merkpunkten und Messungen,
jede Bearbeitung ein Commit. Autoren erscheinen pseudonym (`Nutzer <id>`), Klarnamen nur, wenn
die Person es in ihrem Konto eingeschaltet hat. Punktwolken und Pakete gehen nie ins Git.
Befund-Fäden stehen unter `faeden/<befund>/<schluessel>.json` — **vollständig**, auch interne
Einträge und (in der Historie) der Ursprungstext zurückgezogener Kommentare. Das Repository ist
Sache des Betreibers; wer darauf Zugriff bekommt, sieht alles.

```bash
sv git-export                     # alle Projekte, nur lokal
sv git-export --projekt <projekt> # ein Projekt
```

### Push an ein Git-Ziel

Die Unit `scanview-git-export.service` ist wie die Sicherung **ohne Netz** gehärtet
(`PrivateNetwork=yes`, nur `AF_UNIX`). Gepusht wird erst, wenn `SCANVIEW_GIT_REMOTE` gesetzt ist;
ohne die folgende Netzfreigabe scheitert der Push dann bei jedem Lauf laut (Unit „failed“),
statt still nichts zu tun.

1. **SSH-Schlüssel des Dienstes** anlegen — nie im Repository, nie in der Umgebungsdatei:

   ```bash
   sudo -u scanview install -d -m 0700 /srv/scanview/state/git-ssh
   sudo -u scanview ssh-keygen -t ed25519 -N '' -f /srv/scanview/state/git-ssh/id_ed25519
   ```

   `known_hosts` in demselben Ordner mit dem geprüften Host-Schlüssel des Git-Servers füllen und
   den öffentlichen Schlüssel dort als Deploy-Key mit Schreibrecht nur auf die Export-Repositorys
   hinterlegen. Der Dienst liest den Schlüssel von diesem Pfad (das Heimatverzeichnis des
   Dienstnutzers ist `/nonexistent`); die Unit darf nur unter `/srv/scanview/state` lesen und
   schreiben.
2. **Umgebungsdatei:** `SCANVIEW_GIT_REMOTE=ssh://git@git.example.org/scanview/{projekt}.git`
   und `GIT_SSH_COMMAND` mit `-i /srv/scanview/state/git-ssh/id_ed25519 -o IdentitiesOnly=yes
   -o UserKnownHostsFile=/srv/scanview/state/git-ssh/known_hosts -o StrictHostKeyChecking=yes
   -o BatchMode=yes` (Vorlage in `scanview-server.env.beispiel`). Zugangsdaten nie in die URL.
3. **Netz nur für diese Unit freigeben** — Drop-in per `sudo systemctl edit
   scanview-git-export.service`:

   ```ini
   [Service]
   PrivateNetwork=no
   RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
   # Nur der Git-Host, sonst nichts (der Dienst liest die ganze Datenbank):
   IPAddressDeny=any
   IPAddressAllow=<IP des Git-Hosts>
   ```

   **Namensauflösung:** `IPAddressDeny=any` sperrt auch DNS und Loopback. Mit einem Hostnamen in
   `SCANVIEW_GIT_REMOTE` muss deshalb zusätzlich der Resolver erlaubt werden
   (`IPAddressAllow=<IP des Git-Hosts> <IP des Resolvers>`; bei systemd-resolved `127.0.0.53`,
   also `… localhost`), **oder** der Remote wird per IP angegeben bzw. der Name über
   `/etc/hosts` aufgelöst (dann in `GIT_SSH_COMMAND` zusätzlich `-o HostKeyAlias=<name>`, damit
   die Host-Schlüssel-Prüfung weiter greift). Ohne eins davon scheitert der Push mit einem
   Namensauflösungsfehler.

Die Remote-URL landet nie in `.git/config`; Fehlermeldungen zeigen sie ohne Benutzer und
Passwort. Die Begrenzung auf den Git-Host ist eine Aufgabe des Betreibers: Die Unit öffnet das
Netz nur mit diesem Drop-in, und das Drop-in bestimmt, welche Adressen erreichbar sind.
