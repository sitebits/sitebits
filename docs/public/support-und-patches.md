# Support und Sicherheitskorrekturen

Dieses Dokument legt fest, welche Versionen von scanview Sicherheitskorrekturen erhalten, in
welchen Fristen Schwachstellen behoben werden und wie Korrekturen zu den Betreibern kommen.

## Versionen und Wartungslinie

- scanview folgt der semantischen Versionierung `MAJOR.MINOR.PATCH`, beginnend mit `0.1.0`.
  Viewer/Portal, Plattform-Server und MCP-Server tragen je Release dieselbe Versionsnummer; die
  Release-Kette bricht ab, wenn sie voneinander abweichen.
- Es gibt **eine Wartungslinie**: die jeweils neueste Minor-Version. Sicherheitskorrekturen
  erscheinen als Patch-Release dieser Linie (z. B. `0.1.1`).
- **Sicherheitskorrekturen sind getrennt von Funktionsänderungen auslieferbar.** Ein Patch-Release
  enthält nur die Korrektur und das, was sie zwingend braucht; neue Funktionen kommen mit der
  nächsten Minor-Version.
- **Upgradepfad zwischen Minor-Versionen:** Jede Minor-Version lässt sich direkt aus der vorigen
  aktualisieren. Datenbankmigrationen laufen beim Dienststart automatisch und nur vorwärts; das
  CHANGELOG nennt je Version nötige Handgriffe (z. B. geänderte Caddy-Konfiguration). Vor jedem
  Update wird gesichert (siehe [`betrieb.md`](betrieb.md)).
- Die installierte Version zeigt die Plattform angemeldeten Nutzern unter `GET /api/v1/version`
  und im Portal über „Diagnose kopieren“.

## Supportzeitraum

Bis zur Anwendbarkeitsentscheidung „neueste Version, ohne Enddatum-Zusage“; Planung und Aufwand
werden auf den CRA-Regelfall ≥ 5 Jahre für eine Wartungslinie ausgelegt; das konkrete Enddatum
legt der Hersteller (Stiebitz Souverain Systems UG (haftungsbeschränkt)) mit dem Inverkehrbringen fest.

Das Enddatum wird dann hier, in der [`SECURITY.md`](../../SECURITY.md) und im README genannt. Zum
Stand der rechtlichen Einordnung siehe [`anwendbarkeit.md`](anwendbarkeit.md).

## Fristen für die Behebung

Die Frist beginnt mit der Bestätigung einer Schwachstelle (eigene Prüfung, Meldung nach
[`SECURITY.md`](../../SECURITY.md) oder Treffer des Schwachstellen-Scans einer Drittkomponente).

| Schwere | Korrektur verfügbar spätestens nach |
|---|---|
| kritisch | 7 Tagen |
| hoch | 30 Tagen |
| mittel | 60 Tagen |
| niedrig | spätestens mit der nächsten Minor-Version |

Kann eine Frist nicht gehalten werden, wird die Abweichung im Befundregister begründet und
befristet; Betreiber erhalten in der Zwischenzeit Hinweise zur Eindämmung. Kein Release wird mit
einem offenen Befund der Schwere „hoch“ oder „kritisch“ ausgeliefert (Release-Schwelle, siehe
[`befunde.md`](befunde.md)).

## Sicherheits-Hotfix-Pfad

Im Normalbetrieb gelten für Abhängigkeiten Mindestalter: npm installiert nur Pakete, die
mindestens 7 Tage öffentlich sind (`min-release-age`), und die Python-Teilprojekte lösen nur
gegen einen festen Stichtag auf (`exclude-newer`). Das schützt vor frisch kompromittierten
Paketversionen, würde aber eine dringende Korrektur einer Drittkomponente verzögern.

Für eine Sicherheitskorrektur wird dieses Mindestalter deshalb **ausdrücklich und protokolliert
umgangen**:

1. Nur das betroffene Paket wird angehoben, auf die kleinste Version mit der Korrektur.
2. Die Ausnahme steht im Commit (Paket, alte und neue Version, Kennung der Schwachstelle,
   Begründung) und im Befundregister beim zugehörigen Befund.
3. Die neue Version wird vor der Übernahme geprüft (Herkunft, Änderungen gegenüber der
   Vorversion, Signaturen bzw. Provenance-Angaben, soweit vorhanden).
4. Das Patch-Release durchläuft die vollständige Release-Kette ([`release.md`](release.md)) —
   der Hotfix-Pfad verkürzt nur die Wartezeit auf die Abhängigkeit, keine Prüfung.
5. Beim nächsten regulären Stichtag wird die Ausnahme wieder in die normale Regel überführt.

## Abhängigkeits-Updates übernehmen

Die Konfiguration `renovate.json` im Repo legt fest, wie Renovate Updates für npm-, Python- und
Container-Abhängigkeiten sowie für die gepinnten Werkzeug-Images (`nachweise/werkzeuge.json`) als
Pull Requests vorschlägt. Der regelmäßige Lauf in der Entwicklungsumgebung ist aktiv, sobald das
Repo dort registriert ist. Renovate schlägt nur vor: nichts wird automatisch
übernommen, und die Pull Requests verlangen ein Mindestalter von 7 Tagen für jede neue Version
(`minimumReleaseAge`) — das entspricht der Regel hinter dem festen `exclude-newer`-Stichtag
(Stichtag = Freigabedatum − 7 Tage). Basis-Images mit Werkzeugordner (Eintrag ohne `tag` in
`werkzeuge.json`) aktualisiert Renovate nicht; sie stehen in der vierteljährlichen Werkzeugpflege.

Ein Vorschlag wird **lokal nachvollzogen**, nicht über die Schaltfläche des Pull Requests
gemergt:

1. Branch des Pull Requests holen und die Änderung ansehen (Paket, alte und neue Version,
   Änderungsprotokoll der Drittkomponente).
2. Die Änderung selbst ausführen:
   - **Python (uv):** den Stichtag `exclude-newer` auf „heute − 7 Tage, 00:00 UTC“ setzen,
     `uv lock --upgrade-package <paket>`, die Liste `no-build-package` an den neuen Lock
     anpassen (der Abgleichtest `test_lieferkette.py` schlägt bei Abweichung fehl) und den
     `hatchling`-Pin prüfen.
   - **npm:** `npm install --ignore-scripts`, danach `allowScripts` prüfen (neue Pakete mit
     Installationsskripten bleiben gesperrt, bis sie ausdrücklich zugelassen sind).
   - **Container-Images (Digest):** das neue Image wie bei der Erstaufnahme prüfen — ziehen,
     Digest aus `RepoDigests` festhalten, Version im Container gegenprüfen — und `image`,
     `version` und `tag` in `werkzeuge.json` gemeinsam anpassen.
3. Das Qualitätstor der Nachweiskette laufen lassen (Tests, Linter, Typprüfung); bei
   Container-Images zusätzlich die Prüfung, für die das Werkzeug zuständig ist.
4. Mit einem **signierten Commit** übernehmen, der auf den Pull Request verweist, und den Pull
   Request schließen. Ein Vorschlag, der nicht übernommen wird, wird mit Begründung geschlossen.

Der Sicherheits-Hotfix-Pfad (siehe oben) bleibt davon unberührt: Er umgeht das Mindestalter
ausdrücklich und protokolliert; Renovate-Vorschläge halten es ein.

## Auslieferung von Updates

- **Kostenlos:** Sicherheitskorrekturen sind für alle Nutzer und Betreiber einer unterstützten
  Version kostenlos.
- **Mit Hinweisen:** Jedes Release mit Sicherheitskorrekturen nennt im Abschnitt „Sicherheit“ des
  [`CHANGELOG.md`](../../CHANGELOG.md) die behobenen Schwachstellen mit Schwere, betroffenen
  Versionen und den nötigen Maßnahmen der Betreiber.
- **Signiert:** Updates werden als Release-Artefakte mit signiertem Manifest und signiertem Tag
  ausgeliefert. Die Installation (`einrichten.sh --release`) prüft Signatur und Prüfsummen vor
  jeder Änderung, bricht bei Abweichung ab, sichert die Datenbank und protokolliert jedes Update
  (siehe [`betrieb.md`](betrieb.md), „Aktualisieren“).
- **Keine automatische Installation ab Werk.** scanview ist ein selbst betriebener Server; das
  Einspielen eines Updates greift in den laufenden Dienst ein (Neustart, Datenbankmigration,
  gegebenenfalls Caddy-Konfiguration) und ist deshalb eine Entscheidung des Betreibers, die er
  mit Sicherung und anschließender Prüfung verbindet. Die automatischen Sicherheitsupdates des
  Betriebssystems (z. B. `unattended-upgrades`) bleiben davon unberührt und werden empfohlen.
  Über verfügbare Sicherheitsupdates informieren das CHANGELOG und Sicherheitshinweise; wer eine
  Installation betreibt, sollte sie verfolgen.
- **Viewer im Browser:** Das Web-Bundle wird vom Server ausgeliefert. Mit dem Update des Servers
  erhalten alle Nutzer beim nächsten Laden automatisch den neuen Stand; auf den Endgeräten ist
  nichts zu installieren.
- **MCP-Server:** läuft beim Nutzer und wird von ihm aktualisiert (neue Version aus dem Release
  beziehen, Anleitung in `mcp-server/README.md`).
