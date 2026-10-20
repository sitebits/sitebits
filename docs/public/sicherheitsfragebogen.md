# Antwortvorlage für Sicherheitsfragebögen

Diese Seite beantwortet die Fragen, die Auftraggeber vor dem Einsatz von scanview typischerweise
stellen (Lieferanten-Sicherheitsfragebogen, Vendor Security Questionnaire). Jede Antwort verweist
auf ein Dokument, das sie belegt. Der Fragebogen nennt, was wir zusagen. Ob es stimmt, zeigen die
Belege und die Nachweise je Release.

**So verwenden:** Fragen eines Fragebogens dem passenden Abschnitt zuordnen, die Antwort übernehmen
und den Beleg mitgeben. Steht eine Frage in der Spalte „Betreiber“, beantwortet sie, wer die
Instanz betreibt (siehe unten). Lücken stehen bei der jeweiligen Frage und gesammelt am Ende. Sie
werden nicht weggelassen.

**Hersteller und Betreiber:** scanview ist Software zum Selbstbetrieb. Der **Hersteller**
verantwortet Entwicklung, Sicherheitsprüfung und Updates. Der **Betreiber** verantwortet Server,
Netz, Sicherung außer Haus, Plattenverschlüsselung und Zugriffe auf den Host. Betreibt der
Hersteller selbst eine Instanz für einen Auftraggeber, beantwortet er beide Spalten.

Stand der Belege: jeweils die verlinkte Fassung. Nachweise einer bestimmten Version (Manifest,
SBOM, Scan-, SAST- und DAST-Berichte) liegen im Nachweis-Archiv und werden Prüfern auf Anfrage
vorgelegt.

## 1. Governance und Organisation

| Frage | Antwort | Beleg |
|---|---|---|
| Wer ist verantwortlich? | Hersteller ist die Stiebitz Souverain Systems UG (haftungsbeschränkt). Kontaktstelle für Schwachstellen siehe Abschnitt 6. | [README, Angaben nach Anhang II CRA](../../README.de.md#angaben-nach-anhang-ii-des-cyber-resilience-act) |
| Nach welchem Rahmen wird gearbeitet? | Cyber Resilience Act (CRA) Anhang I, behandelt, als gälte er; jede Anforderung mit Prüfhandlung, Sollwert und Nachweis je Release. Zuordnung zu ISO/IEC 27001:2022 Annex A als Orientierung. | [Kontrollmatrix](kontrollmatrix.md), [Anwendbarkeit](anwendbarkeit.md) |
| Zertifizierung (ISO 27001, BSI C5, SOC 2)? | **Keine.** Die ISO-Spalte der Kontrollmatrix ist nur eine Zuordnung, keine Prüfung. | [Kontrollmatrix](kontrollmatrix.md) |
| Gibt es eine Risikobewertung? | Ja: Datenflüsse, Vertrauensgrenzen, STRIDE je Komponente, bewusst getragene Restrisiken. | [Sicherheitsarchitektur](sicherheitsarchitektur.md) |
| Wie lange gibt es Sicherheitsupdates? | Eine Wartungslinie (neueste Minor-Version), Korrekturen kostenlos. Ein Supportende ist noch nicht festgelegt; geplant wird auf mindestens 5 Jahre je Wartungslinie. | [Support und Patches](support-und-patches.md#supportzeitraum) |
| Werden Unterauftragnehmer oder Fremddienste eingesetzt? | Die Plattform ruft keine Fremddienste auf: keine Tracker, keine externen Ressourcen, keine E-Mails, kein Netz nach außen. Ausnahme auf Wunsch: der MCP-Server für KI-Assistenten (Abschnitt 2). | [Sicherheitsarchitektur, Ausgehende Verbindungen](sicherheitsarchitektur.md#ausgehende-verbindungen) |

## 2. Verschlüsselung und Datenschutz

| Frage | Antwort | Beleg |
|---|---|---|
| Verschlüsselung bei der Übertragung? | TLS mit HSTS über Caddy; Sicherheits-Header auch auf Fehlerantworten. Betrieb nur über HTTPS. | [Sicherheitsarchitektur, Vertrauensgrenzen](sicherheitsarchitektur.md#vertrauensgrenzen) |
| Verschlüsselung im Ruhezustand? | Die Anwendung verschlüsselt ihre Datenbank nicht selbst. Dateien gehören dem Dienstkonto (Modus 0600, `UMask=0077`). Passwörter nur als argon2id, API-Tokens nur als SHA-256. **Betreiber:** Plattenverschlüsselung des Hosts. | [Sicherheitsarchitektur, Sichere Voreinstellungen](sicherheitsarchitektur.md#sichere-voreinstellungen), [Betrieb, Sicherung](betrieb.md#sicherung) |
| Welche Daten werden verarbeitet, wozu? | Liste je Datenkategorie mit Zweck, Ort und Aufbewahrung. | [Sicherheitsarchitektur, Verarbeitete Daten und Zweck](sicherheitsarchitektur.md#verarbeitete-daten-und-zweck) |
| Werden Daten an Dritte übermittelt? | Nein, außer der Auftraggeber setzt den MCP-Server ein: Werkzeugergebnisse gehen dann an den Anbieter des Sprachmodells. Ab Werk im datensparsamen Modus (Freitexte ausgeblendet, Namen als Pseudonym). | [MCP-Server, Datenschutz](../../mcp-server/README.md) |
| Löschung und Datenrückgabe? | Konten werden auf Anforderung anonymisiert; Export der Projektdaten als JSON/CSV/XLSX über HTTPS; Entfernen der Installation beschrieben. | [Betrieb, Daten löschen und übertragen](betrieb.md#daten-löschen-und-übertragen) |
| Wird protokolliert, wer was gesehen hat? | Ja: Lesezugriffsprotokoll je Projekt, nach 90 Tagen (einstellbar) automatisch gelöscht. | [Betrieb, Lesezugriffsprotokoll](betrieb.md#lesezugriffsprotokoll--datenschutz) |
| Standort der Daten? | **Betreiber:** wo die Instanz läuft. Die Software selbst überträgt keine Daten an andere Standorte. | — |

## 3. Zugriff und Authentifizierung

| Frage | Antwort | Beleg |
|---|---|---|
| Wie melden sich Nutzer an? | Passkey (WebAuthn mit Nutzerverifikation) zuerst; Passwort nur als Rückfallweg (argon2id, mindestens 12 Zeichen, Abgleich mit häufigen Passwörtern, Drosselung). | [Sicherheitsarchitektur, Sichere Voreinstellungen](sicherheitsarchitektur.md#sichere-voreinstellungen) |
| Selbstregistrierung? | Nein, nur Einladung (einmalig, 7 Tage gültig). | ebenda |
| Rollen und Rechte? | Rollen je Projekt (Betrachter, Rückmelder, Bearbeiter, Projektleitung, Admin). Ohne Recht antwortet die Plattform mit 404, als gäbe es das Projekt nicht. | [Betrieb, Konten](betrieb.md#konten) |
| Mandantentrennung? | Projekte mehrerer Auftraggeber auf einer Instanz sind getrennt; geprüft vor jedem Release mit den Negativtest-Kategorien `NT-TRENNUNG` und `NT-RECHTE`. | [Release-Prozess](release.md), [Pentest-Plan](pentest-plan.md#wann-geprüft-wird) |
| Maschinenzugriff (API)? | Persönliche API-Tokens mit Umfang „lesen“ oder „schreiben“, befristet (bis 365 Tage), widerrufbar, nur als Hash gespeichert, Nutzung protokolliert. | [Betrieb, API, Tokens und MCP-Server](betrieb.md#api-tokens-und-mcp-server) |
| Werden Änderungen nachvollziehbar protokolliert? | Ja: unveränderliches Bearbeitungsprotokoll für Konten, Rechte, Tokens und Inhalte (mit Vorher/Nachher); fehlgeschlagene Anmeldungen im Systemjournal. | [Betrieb, Aufzeichnung sicherheitsrelevanter Vorgänge](betrieb.md#aufzeichnung-sicherheitsrelevanter-vorgänge) |
| Administrative Zugänge zum Server? | **Betreiber.** Die Software braucht keinen Fernzugang des Herstellers. | — |

## 4. Infrastruktur und Betrieb

| Frage | Antwort | Beleg |
|---|---|---|
| Wie ist der Dienst gehärtet? | systemd-Unit mit `NoNewPrivileges`, `ProtectSystem=strict`, Systemaufruf-Filter, ohne Capabilities, `MemoryDenyWriteExecute`, ohne Netz nach außen; Härtungsschritte für den Host in der Betriebsanleitung. | [Sicherheitsarchitektur](sicherheitsarchitektur.md#sichere-voreinstellungen), [Betrieb, Härtung](betrieb.md#härtung) |
| Wie kommen Updates auf den Server? | Nur aus signierten Release-Artefakten; das Bereitstellungsskript prüft Signatur und Prüfsummen vor der Installation, jede Aktualisierung wird protokolliert. | [Release-Prozess, Was die Signatur belegt](release.md#was-die-signatur-belegt) |
| Datensicherung? | Tägliche Sicherung der Datenbank (7 Generationen) auf dem Host; Restore-Test beschrieben. **Betreiber:** Sicherung an einen anderen Ort. | [Betrieb, Sicherung](betrieb.md#sicherung) |
| Wird die Wiederherstellung geprüft? | Der Restore-Test ist beschrieben und nach jeder Schemaänderung vorgesehen. **Lücke:** eine protokollierte Wiederherstellungsprobe als Nachweis steht noch aus. | [Betrieb, Restore-Test](betrieb.md#restore-test-regelmäßig-mindestens-nach-jeder-schemaänderung) |
| Verfügbarkeit, Überlastschutz? | Drosselung von Anmeldung und API, Körpergrößen begrenzt; geprüft mit `NT-DROSSEL`. **Betreiber:** Verfügbarkeitszusagen (SLA) und Überwachung. | [Sicherheitsarchitektur](sicherheitsarchitektur.md), [Kontrollmatrix K-CRA-I-H](kontrollmatrix.md) |
| Lieferkette der Software? | SBOM je Artefakt (CycloneDX 1.6 nach BSI TR-03183-2), Lizenzabgleich, Mindestalter für neue Abhängigkeiten, signierte Commits, eingefrorener Release-Commit. | [README, SBOM](../../README.de.md#angaben-nach-anhang-ii-des-cyber-resilience-act), [Support und Patches, Hotfix-Pfad](support-und-patches.md#sicherheits-hotfix-pfad) |

## 5. Sicherheitstests, Schwachstellen- und Notfallmanagement

| Frage | Antwort | Beleg |
|---|---|---|
| Statische Codeanalyse (SAST)? | Ja, vor jedem Release: Semgrep (vendierter Regelsatz, ohne Netz) und Bandit; Funde ab „mittel“ blockieren ohne Eintrag im Befundregister. | [Release-Prozess](release.md#checkliste-je-release) |
| Dynamische Tests (DAST)? | Ja: passiver Scan mit OWASP ZAP gegen die Testinstanz nach jedem Deploy, vor dem Deploy auf die Produktivinstanz; dazu API-Fuzzing mit Schemathesis. | [Release-Prozess](release.md#checkliste-je-release) |
| Schwachstellen in Drittkomponenten? | trivy-Scan aller SBOMs gegen eine höchstens 24 h alte Datenbank je Release, Abgleich mit VEX; zusätzlich wöchentlicher Scan. | [Kontrollmatrix K-CRA-I-A](kontrollmatrix.md) |
| Sicherheitstests der eigenen Logik? | Feste Sicherheits-Negativtests je Kategorie (Rechte, Trennung, Tokens, Einladungen, Pfade, Zugriffsprüfung, Drosselung, Header …), vor jedem Release einzeln grün; Negativkontrollen belegen, dass die Prüfwerkzeuge anschlagen. | [Release-Prozess, Negativkontrollen](release.md#negativkontrollen), [Pentest-Plan](pentest-plan.md#wann-geprüft-wird) |
| Penetrationstest? | Prüfkatalog OWASP ASVS 5.0.0 Level 2, risikogestaffelt intern; **extern vor der ersten Installation bei einem Kunden**, Ausschreibungsvorlage liegt vor. **Lücke:** ein externer Pentest ist noch nicht durchgeführt. | [Pentest-Plan](pentest-plan.md) |
| Fristen für Korrekturen? | kritisch 7 Tage, hoch 30, mittel 60, niedrig mit der nächsten Minor-Version; kein Release mit offenem Befund „hoch“ oder höher. | [Support und Patches, Fristen](support-und-patches.md#fristen-für-die-behebung) |
| Notfallplan, Meldung von Vorfällen? | Meldeplaybook nach CRA Art. 14 (Frühwarnung 24 h, Meldung 72 h, Abschlussbericht, Nutzerwarnung, Vertretung), intern, Prüfern auf Anfrage. **Lücke:** ein geübter Ernstfall (Übung) ist noch nicht protokolliert. | [Kontrollmatrix K-CRA-14](kontrollmatrix.md) |
| Werden Betreiber über Sicherheitskorrekturen informiert? | Ja: Abschnitt „Sicherheit“ im CHANGELOG je Version (auch wenn „keine“), Hinweise zur Eindämmung, falls eine Frist nicht zu halten ist. | [CHANGELOG](../../CHANGELOG.md), [Support und Patches](support-und-patches.md) |

## 6. Historie und Meldeweg

| Frage | Antwort | Beleg |
|---|---|---|
| Wie werden Schwachstellen gemeldet? | Vertraulich nach der Richtlinie zur koordinierten Offenlegung (Offenlegung nach 90 Tagen); maschinenlesbar unter `/.well-known/security.txt`. **Lücke:** Das Meldepostfach wird eingerichtet; bis dahin antwortet `security.txt` absichtlich mit 404, statt eine nicht erreichbare Adresse zu nennen. | [SECURITY.md](../../SECURITY.md), [Betrieb, Meldeweg](betrieb.md#meldeweg-securitytxt) |
| Welche Schwachstellen gab es? | Jeder eigene und fremde Befund steht mit Kennung, Schwere, Kategorie und Status im Befundregister; Einzelheiten zu offenen Befunden erst nach der Korrektur. | [Befundregister](befunde.md) |
| Gab es Sicherheitsvorfälle? | Meldepflichtige Vorfälle stünden im Befundregister und im CHANGELOG. Der Betreiber ergänzt Vorfälle seiner Instanz. | [Befundregister](befunde.md), [CHANGELOG](../../CHANGELOG.md) |

## Offene Lücken (gesammelt)

| Lücke | Stand | Geplant |
|---|---|---|
| Externer Penetrationstest | nicht durchgeführt | vor der ersten Installation bei einem Kunden ([Pentest-Plan](pentest-plan.md)) |
| Protokollierte Wiederherstellungsprobe | Ablauf beschrieben, Nachweis fehlt | vor Version 1.0 |
| Geübter Notfallplan | Playbook vorhanden, Übung nicht protokolliert | Termin offen |
| Meldepostfach mit OpenPGP-Schlüssel | in Einrichtung | vor der Veröffentlichung |
| Supportende | nicht festgelegt | mit dem Inverkehrbringen |
| Zertifizierung | keine | nicht geplant; Kontrollmatrix und Nachweise je Release stattdessen |

## Was nur der Betreiber beantwortet

Rechenzentrum und Standort, physischer Zugang, Plattenverschlüsselung, Sicherung außer Haus,
Netz- und Firewallregeln außerhalb des Dienstes, Zugänge zum Host, Überwachung und
Verfügbarkeitszusagen, Personalsicherheit des Betriebsteams, Vorfälle der eigenen Instanz.
