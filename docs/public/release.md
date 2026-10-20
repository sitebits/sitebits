# Release-Prozess

Jedes scanview-Release entsteht aus genau einem eingefrorenen Commit über eine feste Kette von
Prüfungen. Die Kette ist **fail-closed**: Bricht eine Stufe ab, laufen die folgenden nicht.
Bricht sie vor der Stufe `archiv` ab, wird nichts archiviert, kein Tag gesetzt und der
veröffentlichbare Stand (`main` des Export-Repositorys) nicht verändert; ein neuer Lauf derselben
Version ist dann jederzeit möglich. Nach dem Archivieren fehlen höchstens noch die Freigabe des
Exports, der Tag und der Abschlussnachweis — dafür gibt es eine feste Wiederherstellung (unten).
Ohne Abschlussnachweis nimmt kein Zielhost das Release an. Werkzeug:
`scanview-nachweise` (Teilprojekt `nachweise/`).

## Checkliste je Release

1. **Schwachstellen-Datenbank aktualisieren** — `scanview-nachweise db-aktualisieren`. Der einzige
   Netzschritt des Scans; er wird mit Befehl und Zeit protokolliert. Eine Datenbank, die älter als
   24 Stunden ist, bricht das Release ab.
2. **Negativkontrollen** — `scanview-nachweise pruefen negativkontrollen`. Vierzehn feste Fälle, die
   nachweislich abbrechen müssen (siehe unten). Die Release-Kette führt sie selbst noch einmal im
   eingefrorenen Stand aus; der Vorablauf zeigt früh, ob die Werkzeuge bereit sind. Er setzt voraus,
   dass Bandit und Schemathesis im Arbeitsbaum schon bereitgestellt sind (einmal
   `scanview-nachweise pruefen sast` und `pruefen fuzzing`); die Release-Kette stellt beide im
   eingefrorenen Stand selbst bereit.
3. **CHANGELOG** — Abschnitt der Version inklusive „Sicherheit“ (behobene Schwachstellen)
   schreiben und **vor** dem Release committen: Er gehört zum geprüften Commit und ist ein
   Nachweis der Kontrollmatrix.
4. **Trockenlauf** (empfohlen) — `scanview-nachweise release <commit> --version X.Y.Z --trocken`.
   Alles außer Archiv, Tag und Abschluss; das Manifest trägt `"trockenlauf": true` und wird nie archiviert
   oder ausgeliefert. Der Lauf zeigt den Export-Diff und dessen Prüfsumme. Bewährt hat sich ein
   Wegwerf-Klon mit dem Versionssprung als Probe-Commit, damit der echte Release-Commit erst nach
   bestandenem Trockenlauf entsteht. Die Kette braucht dafür dieselbe Umgebung wie das Release:
   `SCANVIEW_BEGRIFFE` (Begriffsliste der Exportprüfung), `SCANVIEW_VAULT` (Ablage der
   organisatorischen Nachweise) und bei knappem `/tmp` ein `TMPDIR` mit genug Platz.
5. **Release** — `scanview-nachweise release <commit> --version X.Y.Z --bestaetigt <diff-hash>`.
   `<diff-hash>` ist die Prüfsumme des durchgesehenen Export-Diffs. Die Kette läuft in dieser
   Reihenfolge durch alle Stufen unten (einfrieren mit der Prüfung der Commit-Signaturen,
   register, qualitaetstor, `sast`, sbom mit den npm-Registry-Signaturen, `fuzzing`, `egress`,
   scan, negativkontrollen, lizenzen, matrix, export, manifest, archiv, tag, abschluss); bricht
   eine vor `archiv` ab, ist das Release nicht entstanden; bricht sie später ab, fehlen höchstens
   Freigabe, Tag und Abschluss (siehe „Abbruch nach dem Archivieren“).
6. **Bereitstellen auf der Testinstanz** — ausschließlich mit den Artefakten des Releases:
   `scanview-nachweise deploy-ordner --version X.Y.Z --ziel <ordner>` stellt den Release-Ordner
   aus dem committeten Archiv zusammen (und prüft ihn); das Bereitstellungsskript auf dem Host
   prüft vorher erneut Manifest- und Abschluss-Signatur und die Prüfsummen.
7. **Smoke-Test** der Testinstanz (Anmeldung, Projekttrennung, Viewer, Sicherheits-Header).
8. **DAST auf der Testinstanz — nach jedem Deploy auf L, vor dem Deploy auf S.** Ein
   Wegwerf-Konto ohne Projektrechte (Betrachter) anlegen (`sv einladen` ohne `--projekt`, siehe
   `betrieb.md`, Abschnitt „Konten“), den Einladungslink mit einem Passwort einlösen, den
   Sitzungswert in eine Datei mit Modus 0600 legen und
   `scanview-nachweise pruefen dast --ziel <adresse-von-L> --instanz L --archiv <pfad>
   --cookie-datei <datei>` ausführen. Der Befehl scannt passiv mit OWASP ZAP (gehärteter
   Container, Cookie nur in einer 0600-Datei, die danach gelöscht wird), schließt Anmelde-,
   Abmelde-, Konto-, Verwaltungs- und Statistikpfade aus, verlangt eine gültige Sitzung vor und
   nach dem Lauf sowie mindestens eine mit 200 beantwortete URL unter `/api/v1/`, entfernt das
   Cookie aus allen Berichten und archiviert das Ergebnis als
   `pruefungen/<datum>[.<lauf>]-dast-L/` (weitere Läufe desselben Tages mit Laufnummer ab 2). Jeder Alarm ab „mittel“ braucht je Pfadmuster einen Befund `B-DAST-<regelnummer>-<kennung>` im
   Befundregister (die Ausgabe nennt die ID; ein behobener Befund deckt einen erneuten Alarm nicht),
   jeder niedrigere einen Befund oder eine begründete Ausnahme in
   `docs/compliance/dast-ausnahmen.toml`; sonst endet der Befehl mit Exit 1 (der Nachweis ist
   dann trotzdem geschrieben). **Aufräumen — auch bei Abbruch:** das Konto auf dem Host sperren
   (`sv sperren --email <adresse>`), danach nach „Konto löschen (DSGVO, `anonymisieren`)“
   anonymisieren, die Cookie-Datei löschen, und mit `fail2ban-client status scanview`
   prüfen, dass die Adresse des Arbeitsplatzes nicht gesperrt ist (sonst
   `fail2ban-client set scanview unbanip <ip>`). Den Nachweis im Archiv pushen (Schritt 12). Gegen die
   Produktivinstanz läuft der Befehl nur mit `--instanz S --betreiber-freigabe <heutiges Datum, UTC>`
   und nur nach ausdrücklicher Freigabe durch den Betreiber; ohne sie verweigert er den Lauf.
9. **Update-Protokoll der Testinstanz auswerten** — `einrichten.sh --release` schreibt je Lauf
   eine Zeile in `/var/log/scanview/updates.log` (Zeit, Version, Manifest-Prüfsumme,
   Quellcommit, Sicherungspfad, geprüfte Artefakt-Prüfsummen, Ergebnis; siehe
   [`betrieb.md`](betrieb.md), „Aktualisieren“). Kopie davon holen (nur lesen) und
   `scanview-nachweise betrieb updates --instanz L --protokoll <datei> --archiv <pfad>`
   ausführen. Der Befehl gleicht jede `ok`-Zeile gegen das signierte Manifest im Archiv ab
   (Manifest-Prüfsumme, Quellcommit, Artefakte), meldet Läufe im Repo-Modus als Abweichung
   (Exit 1), archiviert das Ergebnis als `pruefungen/<datum>[.<lauf>]-updates-L/` und schreibt
   `betrieb/unterstuetzt.toml` (unterstützte Versionen, Stand je Instanz) fort. Eine nicht
   ausgewertete Instanz steht dort als „nicht ausgewertet“.
10. **Bereitstellen auf der Produktivinstanz (Betreiber)** — dieselben Artefakte, dieselbe
    Prüfung wie in Schritt 6; nur nach ausdrücklicher Freigabe durch den Betreiber.
11. **Update-Protokoll der Produktivinstanz auswerten** — wie Schritt 9, mit `--instanz S`.
12. **Archiv pushen** — die neuen Nachweise (`releases/`, `abschluesse/`, `pruefungen/`,
    `betrieb/`) stehen nur lokal im Archiv, bis sie gepusht sind; erst danach sind sie gesichert
    und für die Aufbewahrung (unten) und den wöchentlichen Scan sichtbar.

## Stufen der Release-Kette

| Nr. | Stufe | Was geprüft wird |
|---|---|---|
| 1 | einfrieren | Version `X.Y.Z`, noch nicht archiviert, kein vorhandener Tag; web, server, mcp-server und nachweise tragen genau diese Version; Commit liegt auf einem Release-Zweig; frischer Worktree mit voller Historie, zunächst ohne Checkout angelegt, dann in seiner eigenen Konfiguration auf Filtertreiber und Attribute von außerhalb des Commits geprüft, ohne Hooks ausgecheckt und danach Datei für Datei gegen den Baum des Release-Commits geprüft (unabhängig vom Index); Stichtag-Commit und jeder seither hinzugekommene Commit gültig signiert (Vertrauensanker im Nachweis-Archiv, siehe „Signierte Commits“, auch im Trockenlauf); Abhängigkeiten nur gegen die Lockfiles, dazu die gepinnten Probe-Werkzeuge der Freischalt-Tests und die Prüfwerkzeuge Bandit (SAST) und Schemathesis (Fuzzing) |
| 2 | register | Register gültig, erzeugte Markdown-Fassungen ohne Abweichung, Befund-Gate (Release-Schwelle; Fix-Commit jedes behobenen Befunds im Release-Commit enthalten), Sicherheits-Negativtests je Kategorie grün, Retest jedes behobenen Befunds gelaufen und bestanden — pytest- und Vitest-Fälle je mit JUnit-Auswertung (übersprungen zählt als rot, eine unbekannte Retest-Art ebenso); Prüfpunkte gegen die Instanz beim Deploy |
| 3 | qualitaetstor | Web: Typprüfung, ESLint-Ratsche mit `--max-warnings=0`, Tests; jedes Python-Teilprojekt: Ruff-Ratsche, mypy (Import strict), pytest |
| 4 | sast | Statische Sicherheitsanalyse jeder versionierten Quelldatei (Python, TypeScript) der Quellbäume `server/src`, `mcp-server/src`, `nachweise/src`, `web/src`, `import/src` und der Host-Skripte unter `deploy/platform` (ohne Testordner) ohne Netz: Semgrep mit einem vendierten Regelsatz, dessen Dateien gegen ein Lock mit Prüfsummen geprüft werden, und Bandit aus einem gepinnten Lock; Inline-Unterdrückungen gelten nicht; jeder Fund ab „mittel“ steht per Fingerabdruck im Befundregister, jeder übrige dort oder als begründete Ausnahme (`docs/compliance/sast-ausnahmen.toml`); jede dieser Quelldateien muss im Ergebnis des jeweiligen Werkzeugs stehen |
| 5 | sbom | Artefakte deterministisch bauen (ausgeliefert wird nur, was auch die öffentliche Positivliste aufnimmt; der Web-Bau läuft mit einer festen, schmalen Umgebung); npm-Registry-Signaturen des Web-Baus geprüft (`npm audit signatures`, Abdeckung gegen das Lockfile, keine ungültige und keine unbegründet fehlende Signatur); je Artefakt ein SBOM (CycloneDX 1.6, BSI TR-03183-2), Abdeckung jeder ausgelieferten Datei, Schemaprüfung; jedes SBOM an sein Artefakt gebunden (Seriennummer, Prüfsummen) |
| 6 | fuzzing | API-Fuzzing mit Schemathesis gegen eine Wegwerf-Instanz aus dem Worktree (gebauter Web-Client, Server aus dem Commit, nur synthetische Pakete; Caddy auf `localhost:18090`, Server auf `127.0.0.1:18097`, beide Ports müssen frei sein), je als Betrachter (API-Token) und als Admin (Sitzung), fester Seed und Beispielzahl aus `nachweise/fuzzing.toml`; nur `/api/v1/`, die Anmelderouten nie; jeder Check aktiv oder begründet abgeschaltet (Serverfehler, Antwortschema und Zugriff ohne Anmeldung immer aktiv); jeder Fehlschlag blockiert, jede nicht begründet ausgeschlossene Operation muss geprüft sein, beide Anmeldungen müssen bis zum Ende gelten; Anmeldewerte nie auf der Befehlszeile und vor dem Ablegen aus den Berichten entfernt |
| 7 | egress | Keine ausgehenden Verbindungen von Portal und Viewer: jede absolute URL (`http`, `https`, `ws`, `wss`) in jeder Datei des gebauten Web-Bundles muss mit Art (Namensraum, Lizenzhinweis, Link ohne Abruf) und Grund in `docs/compliance/egress-ausnahmen.toml` stehen, eine unbekannte Dateiendung bricht ab; dazu ein Headless-Browser im gehärteten Container (Host-Netz, nur gegen `127.0.0.1`) gegen eine Wegwerf-Instanz aus dem Worktree mit einem synthetischen Testpaket (gebauter Web-Client, Server aus dem Commit; Caddy auf `localhost:18090`, Server auf `127.0.0.1:18097`, beide Ports müssen frei sein): Anmeldeseite, öffentliche Barrierefreiheitserklärung, Portal, Viewer mit Standpunkt-Sprung und Verlaufsseite; jede Anfrage auf jedem Weg (auch aus Workern, Web-Sockets, durch die Inhaltssicherheitsrichtlinie blockierte) wird erfasst und, wenn sie nicht zum eigenen Ursprung gehört, abgebrochen; eine Kontrollanfrage je Seite an eine nicht auflösbare Adresse muss erfasst sein, sonst ist das Ergebnis ungültig; Abdeckung (Punkte geladen, Paketdateien, Panoramakachel, Projekt-API) muss erreicht sein; jede Anfrage an einen fremden Ursprung blockiert; die Sitzung muss bis zum Ende gelten, Anmeldewerte stehen nie auf der Befehlszeile und nicht in den Berichten |
| 8 | scan | trivy ohne Netz, Datenbank höchstens 24 Stunden alt, jede Komponente geprüft; kein Treffer ab „hoch“ ohne freigebende VEX-Aussage in den ausgelieferten Artefakten |
| 9 | negativkontrollen | die vierzehn Negativkontrollen im eingefrorenen Stand |
| 10 | lizenzen | jede erklärte Lizenz auf der Positivliste (`nachweise/lizenzen.toml`), jede Komponente mit Name und Version in `docs/THIRD_PARTY.md` |
| 11 | matrix | jede Release-Voraussetzung der Kontrollmatrix hat ihren Nachweis (bestandene Prüfung oder vorhandenes Dokument, `nachweise/nachweisquellen.toml`) |
| 12 | export | öffentlicher Abzug nur aus den Git-Objekten des Commits über eine Positivliste; Binärdateien nur mit Freigabe; Begriffs-, Pfad- und Geheimnisprüfung; Meldeweg: trägt `SECURITY.md` den Marker „Postfach offen“, läuft die Stufe weiter, vermerkt aber `veroeffentlichbar: false` mit Grund im Export-Manifest und als `veroeffentlichung: {veroeffentlichbar, grund}` im Release-Manifest (die Veröffentlichung ist gesperrt, nicht der lokale Export); bestätigter Diff; Baubarkeit; öffentliche SBOMs ebenso geprüft; die ausgelieferten Artefakte (web, server, mcp) selbst — genau die Tarballs, die signiert werden — durchlaufen dieselbe Begriffs-, Pfad- und Geheimnisprüfung; lokaler Commit auf einen Kandidaten-Zweig |
| 13 | manifest | Manifest mit Quell- und Exportcommit, Prüfsummen aller Artefakte, SBOMs und Berichte, Werkzeug-Pins, Datenbankstand, Netzschritten, Toolchain-Versionen und Architektur; vor dem Signieren auf Vollständigkeit geprüft (jede Pflichtprüfung „bestanden“, SBOM je ausgeliefertem Artefakt, Berichte) — dieselbe Prüfung macht der Zielhost; signiert und sofort geprüft |
| 14 | archiv | Ablage in einem eigenen Repository außerhalb des Quellbaums, nur anhängend; die Signatur der abgelegten Kopie wird vor der Ablage geprüft |
| 15 | tag | Export freigeben (`main` des Export-Repositorys per Fast-Forward auf den Kandidaten, annotierter Tag `vX.Y.Z` mit Manifest-Prüfsumme), dann signierter Tag `vX.Y.Z` auf dem Quellcommit; beide werden vor der Erfolgsmeldung geprüft |
| 16 | abschluss | Abschlussnachweis (`abschluss.json`: Version, Manifest-Prüfsumme, Quell-, Export- und Archiv-Commit, Tag), signiert im eigenen Namensraum `scanview-abschluss` und im Archiv unter `abschluesse/X.Y.Z/` committet — nur, wenn das archivierte Manifest committet ist und seine Signatur besteht |

Jede Stufe schreibt ihren Bericht (`berichte/<stufe>.json`), auch die abbrechende.

Die Kette läuft bewusst lokal auf der Release-Workstation und nicht in einem CI-Runner; warum, steht in [Entscheidung 0013](../entscheidungen/0013-kein-ci-runner.md).

## Was die Signatur belegt

Das Manifest wird mit einem SSH-Schlüssel signiert (`ssh-keygen -Y sign`, Namensraum
`scanview-release`). Diese Signatur belegt, dass die Kette bis einschließlich der Stufe
`manifest` auf der Release-Workstation lief. Dass danach auch Archivierung und Tag gelungen
sind, belegt erst der **Abschlussnachweis** (`abschluss.json`, Namensraum `scanview-abschluss`,
getrennt vom Manifest, damit keine der beiden Signaturen für die andere gelten kann). Ein
Zielhost nimmt ein Release nur mit beiden an. Keine der Signaturen belegt, dass der Autor das
Release freigegeben hat.

Prüfen:

```sh
ssh-keygen -Y verify -f nachweise/allowed_signers -I release@scanview -n scanview-release \
  -s manifest.json.sig < manifest.json
```

Danach die SHA-256-Prüfsummen der Artefakte gegen `artefakte[].sha256` im Manifest vergleichen
(`scanview-nachweise manifest pruefen manifest.json --artefakte <ordner>`). Jedes Release
archiviert die zum Zeitpunkt gültige `allowed_signers`-Datei mit; ein Schlüsselwechsel wird
angehängt, nie ersetzt.

## Signierte Commits

Jeder Commit im Repository wird mit einem eigenen SSH-Schlüssel signiert (Git, `gpg.format
ssh`) — nicht mit dem Release-Schlüssel und nicht mit einem Anmeldeschlüssel. Der
Vertrauensanker liegt im Nachweis-Archiv unter `vertrauensanker/`: die zugelassenen Schlüssel
(`commit-signers`, Format `allowed_signers`, Namensraum `git`), eine Sperrliste
(`commit-revoked`) und in `commit-signatur.toml` der Stichtag, die Release-Zweige und die
**Altstände** (die Zweigspitzen, die zum Stichtag schon existierten). Der Anker liegt bewusst
**außerhalb** des geprüften Quellbaums: Ein Commit kann die Schlüssel, gegen die er geprüft
wird, nicht selbst mitbringen. Gelesen wird er aus den Git-Objekten des Archiv-Zweigs `main`;
weicht die Datei im Arbeitsbaum davon ab, bricht das Release ab. Vorher prüft die Kette die
Geschichte des Ankers: Ab einem im Werkzeug fest eingetragenen ersten Anker-Commit (Genesis) ist
die Archiv-Historie linear, jeder Archiv-Commit ist mit einem Schlüssel aus dem Anker **seines
Vorgängers** signiert, und die Anker-Dateien wachsen nur (neue Schlüssel- und Sperrzeilen, neue
Altstände; Stichtag und Zweige bleiben). Ein Commit kann sich also nicht mit einem Schlüssel
legitimieren, den er selbst erst einträgt.

Die Release-Kette prüft in der Stufe `einfrieren` den Stichtag-Commit und **jeden Commit, der
vom Release-Commit aus erreichbar ist, aber weder vom Stichtag noch von einem Altstand**
(`git rev-list <release> --not <stichtag> <altstände…>`). Die Historie vor dem Stichtag ist
damit ausgenommen; jeder spätere Commit — auf welchem Zweig auch immer er entstand, auch über
Merges hereingekommen, auch der Merge-Commit selbst — wird geprüft. Es zählt nur eine gültige
Signatur eines nicht gesperrten Schlüssels aus dem Anker; unsigniert, ungültig, mit unbekanntem
oder gesperrtem Schlüssel bricht das Release ab. Geprüft wird mit festen Programmen
(`/usr/bin/ssh-keygen`; OpenPGP- und X.509-Signaturen gelten nie) und mit fest vorgegebener
Sicht auf die Historie: ohne Git-Ersatzobjekte (`git replace`), ohne grafts und ohne
commit-graph-Datei; ein flaches Repository (`shallow`) wird abgelehnt. Jeder gelesene Commit,
Baum und Blob wird gegen seine Objekt-ID nachgerechnet (git selbst prüft das beim Lesen nicht),
die Prüfmenge wird aus diesen geprüften Objekten berechnet und mit `git rev-list` abgeglichen;
Repositorys mit fremden Objektspeichern (`objects/info/alternates`) werden abgelehnt. Die
Signatur jedes geprüften Commits wird zweimal bewertet: von git und zusätzlich direkt mit
`ssh-keygen -Y verify` über den signierten Inhalt der hashgeprüften Commit-Bytes (Namensraum
`git`, Sperrliste, Prüfzeit = Committer-Zeit); beide müssen denselben Schlüssel als gültig
melden. Diese Einstellungen setzt
die Kette selbst, Konfiguration des Repositorys oder der Umgebung überschreibt sie nicht. Allgemein
läuft **jeder** git-Aufruf der Kette über einen einzigen Aufrufpunkt mit festem
Konfigurationssatz: keine Hooks, kein fsmonitor, kein Pager, kein externes Diff, keine
Credential-Helfer, kein ssh-Befehl, keine Signaturanzeige, feste Signaturprogramme, keine
System- und Nutzerkonfiguration (`GIT_CONFIG_NOSYSTEM`, `GIT_CONFIG_GLOBAL=/dev/null`) — kein
Programm aus einer Git-Konfiguration läuft während eines Releases, auch nicht beim Aufräumen
nach einem Abbruch. Daraus
folgt: Zweige werden nur **lokal mit signiertem Merge-Commit** zusammengeführt, nie über die
Oberfläche des Git-Servers — dessen Merge-Commit wäre nicht mit einem Schlüssel aus dem Anker
signiert und blockierte das nächste Release. Der Git-Server selbst erzwingt keine Signaturen;
die Durchsetzung ist die Release-Kette.

Was die Signatur belegt: Der Commit entstand mit einem Schlüssel aus dem Anker, also auf der
Entwicklungs-Workstation. Sie belegt **nicht**, dass ein Mensch die Änderung durchgesehen hat,
und bindet den Schlüssel nicht an die im Commit genannte Adresse. Wer auf der Workstation unter
dem Entwicklerkonto Code ausführen kann, kann auch signieren.

Ihre eigenen Commits ins Nachweis-Archiv signiert die Kette mit dem Schlüssel aus
`user.signingkey` des Archivs, als **Schlüsseldatei ohne Passphrase**: ohne ssh-agent
(`SSH_AUTH_SOCK` wird nicht durchgereicht) und ohne Terminal. Vor jedem solchen Commit signiert
sie probeweise und prüft die Probe gegen Anker und Sperrdatei; ein Schlüssel mit Passphrase, ein
Schlüssel-Literal oder ein Schlüssel außerhalb des Ankers bricht dort sofort ab, bevor etwas
geschrieben wird.

Schlüsselwechsel: Ein neuer Schlüssel wird als neue Zeile angehängt; die abgelöste Zeile erhält
`valid-before="JJJJMMTT"` in Komma-Form vor den übrigen Optionen
(`<principal> valid-before="20270101",namespaces="git" ssh-ed25519 …`). `valid-before` gilt
gegen die Commit-Zeit, die der Signierer selbst setzt, und ist deshalb **kein** Schutz gegen
einen kompromittierten Schlüssel: der wird in `commit-revoked` gesperrt (gilt dann unabhängig
von der Commit-Zeit; ältere Commits dieses Schlüssels verlieren ihre Gültigkeit). Jede dieser
Änderungen muss mit einem noch gültigen Schlüssel des bisherigen Ankers signiert sein. Ist das
nicht mehr möglich oder soll ein neuer Stichtag gelten, braucht es einen neuen Genesis — eine
bewusste, signierte Änderung des Werkzeugs selbst. Zeilen werden nie gelöscht.

Weil Zeilen nie gelöscht werden, lässt sich eine fehlerhafte Zeile nach dem Push nicht mehr
korrigieren. Eine Zeile in `commit-revoked`, die kein bloßer öffentlicher Schlüssel ist (etwa
eine aus `commit-signers` kopierte Zeile mit Principal oder Optionen), ließe ssh-keygen jede
Signatur ablehnen — das Archiv wäre gesperrt, bis ein neuer Genesis gesetzt ist. Die Kette
meldet einen solchen Stand beim Laden des Ankers mit dem Archiv-Commit, der ihn einträgt;
verhindern lässt er sich nur vor dem Commit. Ablauf für jede Ankeränderung von Hand:

1. Die Dateien im Arbeitsbaum des Archivs nur anhängend ändern. In `commit-revoked` steht je
   Zeile `<typ> <base64> [kommentar]`, wie in einer `.pub`-Datei, sonst nur Leer- und
   Kommentarzeilen, eingerückt höchstens mit Leerzeichen und Tab.
2. Vor dem Commit die Probesignatur gegen den **neuen** Stand im Arbeitsbaum, mit dem Schlüssel,
   der künftig signiert:

   ```sh
   A=<archiv>/vertrauensanker
   printf probe > /tmp/probe && ssh-keygen -Y sign -n git -f <signierschlüssel> /tmp/probe
   ssh-keygen -Y verify -n git -f $A/commit-signers -I <principal> -s /tmp/probe.sig \
     -r $A/commit-revoked < /tmp/probe
   ```

   Nur „Good "git" signature“ (Exit 0) erlaubt den Commit. Der Commit selbst muss mit einem
   Schlüssel aus dem **bisherigen** Anker signiert sein: dieselbe Prüfung gegen
   `git -C <archiv> show main:vertrauensanker/commit-signers` bzw. `…/commit-revoked`.
3. Signiert committen, aber noch nicht pushen. Der nächste Lauf, der den Anker lädt (spätestens
   der Trockenlauf), prüft die ganze Ankerkette. Meldet er einen Ankerfehler mit diesem
   Archiv-Commit, wird `main` lokal zurückgesetzt (`git reset --keep HEAD~1`) und die Änderung
   neu geschrieben. Erst danach pushen.

Prüfen:

```sh
A=<archiv>/vertrauensanker
export GIT_NO_REPLACE_OBJECTS=1 GIT_GRAFT_FILE=/dev/null
test "$(git rev-parse --is-shallow-repository)" = false
git -c core.commitGraph=false -c gpg.ssh.allowedSignersFile=$A/commit-signers \
  -c gpg.ssh.revocationFile=$A/commit-revoked -c gpg.ssh.program=/usr/bin/ssh-keygen \
  -c gpg.program=/usr/bin/false -c gpg.openpgp.program=/usr/bin/false \
  -c gpg.x509.program=/usr/bin/false \
  log --no-walk --format='%H %G? %GF' <stichtag> \
  $(git -c core.commitGraph=false rev-list <release-commit> --not <stichtag> <altstände…>)
```

Jede Zeile muss `G` zeigen, mit einem Fingerabdruck aus `commit-signers`.

## Abbruch nach dem Archivieren

Scheitert eine der letzten Stufen (Export-Freigabe, Tag oder Abschluss), sind die Nachweise schon
archiviert. Die
Version lässt sich dann nicht erneut ausführen (das Archiv hängt nur an); die Fehlermeldung nennt
die fehlenden Schritte mit den konkreten Pfaden:

1. Manifest prüfen: `scanview-nachweise manifest pruefen <archiv>/releases/X.Y.Z/manifest.json
   --artefakte <archiv>/releases/X.Y.Z/artefakte`.
2. Export freigeben, falls `main` des Export-Repositorys noch nicht auf `release-kandidat/X.Y.Z`
   steht: `git merge --ff-only release-kandidat/X.Y.Z`, dann ein annotierter Tag `vX.Y.Z` mit der
   Nachricht „scanview X.Y.Z“ und „Manifest-SHA-256 <prüfsumme>“ (bei gesperrter
   Veröffentlichung zusätzlich die Zeile „Veröffentlichung gesperrt: <grund>“ aus dem Manifest)
   unter der neutralen Identität.
3. Einen vorhandenen, ungültigen Tag im privaten Repository löschen (`git tag -d vX.Y.Z`). Die
   Kette erkennt solche Reste: Ein vorhandener Tag gilt nur, wenn Signatur, Zielcommit und
   Manifest-Prüfsumme stimmen.
4. Tag setzen: `git -c gpg.format=ssh -c user.signingkey=<release-schlüssel> tag -s
   -m "scanview X.Y.Z" -m "Manifest-SHA-256 <prüfsumme>" vX.Y.Z <quellcommit>`.
5. Prüfen: `git -c gpg.ssh.allowedSignersFile=<datei> tag -v vX.Y.Z`; die Datei enthält die
   Zeile `release@scanview namespaces="git" <inhalt von nachweise/signierschluessel.pub>`.
6. Abschluss nachholen: `scanview-nachweise abschluss --version X.Y.Z` — prüft beide Tags gegen
   das archivierte Manifest und schreibt erst dann den signierten Abschlussnachweis.

## Ratschen im Qualitätstor

ESLint prüft typgestützte Sicherheitsregeln für `web/src/` und `web/tests/` und die empfohlenen
Playwright-Regeln für `web/e2e/`. `npm run lint` verwendet `--max-warnings=0`. Die empfohlene
Playwright-Konfiguration läuft vollständig als Fehler, damit Bulk Suppressions auch deren
Altbestand erfasst. Fokus-Tests und unbegründete Skips sind nicht toleriert; bedingte Skips
brauchen Bedingung und Grund. Die öffentliche Fassung enthält keine E2E-Dateien und bleibt
mit derselben Konfiguration baubar.

`web/eslint-suppressions.json` zählt ausschließlich den gemessenen Altbestand je Datei/Regel.
Neue Funde brechen das Gate. Nach Behebungen senkt `npx eslint . --prune-suppressions` die Basis.
`--suppress-all` gehört nur zur einmaligen Erfassung des Startstands, niemals zur Freigabe neuer
Funde.

Für alle vier Python-Teile prüft `scanview-nachweise pruefen ratsche` zuerst die bisherigen
Ruff-Regeln ohne tolerierte Funde und dann `BLE`, `C90` (maximale Komplexität 15) und `ASYNC`
gegen `<teil>/ruff-basis.json`. Bandit deckt die Sicherheitsregeln bereits ab. Eine erhöhte
Zählung oder ein neues Datei/Regel-Paar bricht ab; eine Senkung meldet „Basis senken“.
`scanview-nachweise pruefen ratsche --basis-aktualisieren` schreibt ausschließlich niedrigere
Werte. Fehlende oder kaputte Basisdateien und Werkzeugfehler brechen ebenfalls ab.
Die Python-Einzelprüfung für die bisherigen Regeln lautet
`uv run --locked ruff check --ignore BLE,C90,ASYNC .`; sie ersetzt die zusätzliche Ratsche nicht.

## Negativkontrollen

| Kontrolle | Erwarteter Abbruch |
|---|---|
| bekannt verwundbare Testkomponente (nur Test-Fixture) | Scan-Gate blockiert |
| Schwachstellen-Datenbank 25 Stunden alt | Abbruch „Datenbank zu alt“ |
| SBOM eines ausgelieferten Artefakts fehlt | Abbruch |
| zusätzliche, keiner Komponente zugeordnete Datei im Artefakt | Abbruch |
| VEX-Aussage `under_investigation` | Scan-Gate blockiert |
| Testgeheimnis im Export | Abbruch (gitleaks meldet einen Fund der Token-Regel; ein Fehler des Werkzeugs zählt nicht) |
| Manifest nach der Signatur verändert bzw. ohne Signatur | Signaturprüfung scheitert |
| unsignierter Commit nach dem Stichtag (Wegwerf-Repository mit eigenem Vertrauensanker) | Prüfung der Commit-Signaturen scheitert |
| ungültige npm-Registry-Signatur (Testmeldung) | Prüfung der npm-Signaturen scheitert |
| absichtlich verwundbare Test-Quelldateien, Funde nicht im Befundregister | SAST-Gate blockiert (Funde ab „mittel“ von Semgrep und Bandit) |
| Testdienst mit einer Operation, die immer mit 500 antwortet | Fuzzing-Gate blockiert, und zwar nur an dieser Operation |
| aufgezeichnete Browser-Anfragen mit einer Anfrage an ein fremdes Ziel und einem Richtlinien-Verstoß (Testaufzeichnung) | Egress-Gate blockiert beide |
| Test-Bundle mit absoluten URLs in Skript und Stylesheet, ohne Ausnahme | Egress-Gate blockiert beide |
| Neuer blinder `except Exception` | Ruff-Ratsche bricht mit `BLE001` ab |

Eine Kontrolle gilt nur als bestanden, wenn sie **aus genau ihrem Grund** abbricht; ein Abbruch
aus anderem Grund (etwa ein fehlendes Werkzeug) zählt als gescheitert.

## Öffentlicher Export

Der öffentliche Stand ist ein Abzug des privaten Repositorys, erzeugt nur aus den Git-Objekten
des Release-Commits. Die Kette legt ihn als **lokalen** Commit im Export-Repository ab — zunächst
auf dem Zweig `release-kandidat/X.Y.Z`. Erst wenn die Nachweise archiviert sind, rückt `main` per
Fast-Forward nach und erhält einen annotierten Tag `vX.Y.Z`, dessen Nachricht Version und
Manifest-Prüfsumme nennt. Ein abgebrochener Lauf lässt `main` unverändert; ein neuer Lauf
derselben Version ersetzt den übrig gebliebenen Kandidaten-Zweig. Ist die Veröffentlichung
gesperrt (Manifestfeld `veroeffentlichung`, z. B. solange das Meldepostfach nicht geprüft ist),
geschieht das alles trotzdem lokal; der Tag im Export-Repository trägt dann den Vermerk
„Veröffentlichung gesperrt: …“, die Kette meldet ihn am Ende, und ein Veröffentlichungsschritt
wird verweigert. Der eigenständige Export (`scanview-nachweise export`) ist der
Veröffentlichungsweg und bricht bei gesperrter Veröffentlichung ab. Autor, Committer und Tagger
sind die neutrale Release-Identität `scanview release <security@sitebits.de>`, die
Commit-Nachricht nennt Version und Diff-Prüfsumme. Die Veröffentlichung selbst ist ein
eigener, bewusster Schritt außerhalb der Kette.

## Tests und Qualität

- Neue Funktionen und Fehlerbehebungen bringen automatisierte Tests mit; eine behobene
  Sicherheitslücke bekommt einen Test, der sie belegt (Retest im Befundregister).
- Das Qualitätstor (Typprüfung, Linter, Tests aller Teilprojekte), die statische
  Sicherheitsanalyse (Semgrep, Bandit), das API-Fuzzing (Schemathesis) und der Egress-Test
  (keine ausgehenden Verbindungen) sind Teil jedes Releases; was sie nicht besteht, wird
  nicht veröffentlicht.
- Sicherheits-Negativtests laufen je Kategorie und müssen einzeln grün sein, nicht nur in der
  Summe.

## Aufbewahrung

Manifest, Signatur, SBOMs, Scan-Ergebnis, Berichte, Exportmanifest und Artefakte jedes Releases
werden mindestens zehn Jahre aufbewahrt (Maßstab: Dokumentationsfrist des Cyber Resilience Act).

Ein zweiter Ort sichert das Archiv unabhängig vom Git-Server: einmal im Jahr entsteht ein
vollständiges git bundle des Nachweis-Archivs, das mit SHA-256-Prüfsumme an einen zweiten Ort
(Speicher bei einem anderen Anbieter) übertragen wird. Eine bestehende Datei wird dort nie
überschrieben und nie gelöscht. Danach wird das Bundle zurückgeholt, gegen die Prüfsumme
verglichen, in ein leeres Repository geklont und mit `git fsck --full` geprüft; seine Refs müssen
denen des Archivs entsprechen. Erst dann gilt die Ablage als erfolgreich. Bis zum ersten Lauf ist
dieses Ergebnis offen; das Ergebnis jedes Laufs wird im Archiv unter `pruefungen/` festgehalten.
