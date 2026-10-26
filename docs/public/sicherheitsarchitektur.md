# Sicherheitsarchitektur und Risikobewertung

Dieses Dokument beschreibt die Bestandteile von scanview, die Datenflüsse zwischen ihnen, die
Vertrauensgrenzen und die Bedrohungen je Komponente (STRIDE) mit den getroffenen Maßnahmen. Es ist
die öffentliche Fassung der Risikobewertung nach Art. 13 Abs. 2 des Cyber Resilience Act.

Verweise auf Tests nennen die Kategorien der Sicherheits-Negativtests (`NT-…`), die vor jedem
Release einzeln grün sein müssen. Verweise auf Befunde (`B-…`) führen ins
[Befundregister](befunde.md); dort steht der Status. Offene Befunde werden hier nur genannt, wenn
ihr Inhalt ohnehin öffentlich dokumentiert ist oder sie nicht ausgelieferte Teile betreffen; alle
übrigen stehen mit Kennung und Status im Befundregister, Einzelheiten erst nach ihrer Behebung.

## Komponenten

| Komponente | Aufgabe | Läuft | Ausgeliefert |
|---|---|---|---|
| **Viewer** (`web/`, `index.html`) | Punktwolken, Panoramen, Messen, Merkpunkte (POIs) im Browser | im Browser der Nutzer | ja (Web-Bundle) |
| **Portal** (`web/`, `portal.html`) | Anmeldung, Projektkarten, Bearbeiten, Verlauf, Verwaltung, API-Zugänge | im Browser der Nutzer | ja (Web-Bundle) |
| **Plattform-Server** (`server/`) | Konten, Anmeldung, Sitzungen, Rechte je Projekt, REST-API v1, Bearbeitungsprotokoll, Zugriffsentscheidung für Paketdateien | auf dem Host, eigener Systemnutzer, nur Loopback | ja |
| **Caddy** mit `forward_auth` (`deploy/platform/`) | TLS, Sicherheits-Header, Weiterleitung an den Server, Auslieferung von Web-Bundle und Paketdateien nach bestandener Zugriffsprüfung | auf dem Host | Konfiguration ja, Caddy selbst vom Betreiber |
| **MCP-Server** (`mcp-server/`) | Werkzeuge für KI-Assistenten über die API v1 mit einem persönlichen API-Token | beim Nutzer (stdio) | ja |
| **Import-Worker** (`server/`, Konsole `scanview-import-worker`) | rechnet Aufträge aus der Auftragsdatenbank `jobs.db` (Prüfbericht; neue Projekte aus hochgeladenen Meshes oder Splats), je Auftrag in einer eigenen, isolierten systemd-Unit | auf dem Host, eigener Dienst und Systemnutzer, kein Netz | ja (zuerst nur auf freigegebenen Hosts) |
| **Übergabe** (`server/`, Konsole `scanview-uebergabe`, root-Skript `scanview-import-uebergabe`) | legt ein fertiges, geprüftes Importpaket in den Eingang und startet das unveränderte Freischalt-Skript | auf dem Host, eigener Systemnutzer ohne Anmeldung, per Timer | ja (nur mit eingeschaltetem Import) |
| **Import-Werkzeug** (`import/`) | erzeugt Projektpakete aus Scandaten (Punktwolken, Panoramen, Standpunkte, Meshes, Splats) | beim Datenaufbereiter ohne Netz; auf dem Host nur innerhalb der isolierten Aufträge des Workers | auf Hosts mit Jobläufer (Import-Umgebung) |
| **Postdienst** (`server/`, Konsole `scanview-post`) | versendet fertige E-Mail-Benachrichtigungen aus dem Spool per SMTP (ab Werk aus, ADR 0017) | auf dem Host, eigener Dienst und Systemnutzer, keine Datenbank, Netz nur zum SMTP-Server des Betreibers | ja (nur mit eingeschalteter Benachrichtigung) |
| **Projektpaket** | `project.json` (Schema 2), COPC-Punktwolken, Panoramen, Draufsicht | als Dateien unter der Paketwurzel des Hosts | Daten, kein Code |

## Datenflüsse

```
Browser ──HTTPS──> Caddy
                     ├─ /auth/*, /api/*        → Plattform-Server (127.0.0.1, nur Loopback)
                     ├─ /data/<projekt>/*      → forward_auth: Server entscheidet je Anfrage
                     │                             └─ erlaubt → Caddy liefert die Datei (Range)
                     ├─ /ansicht               → Viewer (Web-Bundle)
                     └─ /, /anmelden, …        → Portal (Web-Bundle)
Plattform-Server ── SQLite-Datenbank (Zustand, nur Dienstnutzer)
                 └─ liest Projektpakete nur lesend
                 └─ Lesezugriffe: /api/authz und die Leserouten unter /api/v1/projekte/<id>
                    schreiben nach erfolgreicher Rechteprüfung in die Tabelle lesezugriffe
                    (aggregiert je 15 Minuten, befristet; nur Admins lesen sie aus)
MCP-Server (beim Nutzer) ──HTTPS + API-Token──> /api/v1/* (wie ein Browser, gleiche Rechte)
Import-Werkzeug ── erzeugt Pakete offline ── Betreiber stellt sie auf dem Host bereit
Plattform-Server ── schreibt Aufträge in jobs.db, liest Stand und Berichte (nur lesend)
Import-Worker ── holt Aufträge aus jobs.db ── startet je Auftrag eine Unit (kein Netz, Paket nur
                 lesend, ein Arbeitsordner beschreibbar) ── übernimmt geprüfte Dateien nach
                 <berichte>/<projekt>/<auftrag>/
Browser/Skript ──HTTPS, Stücke à 64 MiB──> Plattform-Server ── schreibt nach <uploads>/<id>/
                 (außerhalb der Paketwurzel, nie ausgeliefert; liest davon nur Kopfbytes)
Import-Worker ── neues Projekt: Unit mit dem eigenen Upload nur lesend ── neu-ohne-wolke, verify
                 ── Paketbaum geprüft ── <übergabe>/<projekt>/
Timer (root) ── scanview-uebergabe (eigener Nutzer) kopiert in den Eingang ── Freischalt-Skript
                 (unverändert) ── Meldung (ein Wort) ── Import-Worker
Plattform-Server ── Faden-Eintrag: Mail vormerken (gleiche Transaktion) ── Hintergrund-Thread prüft
                 Rechte und Einwilligung, rendert ── <post>/ausgang/<id>.json
scanview-post ── liest <post>/ausgang ──SMTP+TLS──> SMTP-Server des Betreibers
                 └─ schreibt <post>/erledigt/<id>.json (Zustand) ── Plattform-Server liest zurück
Bildanhang (F7) ── Webdienst prüft Kopfbytes ── Import-Worker dekodiert und kodiert neu
                 in einer isolierten Unit ── Webdienst übernimmt die Ausgabe als fremde Eingabe
```

- Der Viewer lädt ausschließlich von der eigenen Origin (Content-Security-Policy
  `connect-src 'self'`); es gibt keine Verbindungen zu Kartendiensten, CDNs, Schriftanbietern oder
  Analysediensten.
- Paketdateien fließen nie durch den Plattform-Server: Er beantwortet nur „darf diese Sitzung
  diese Datei lesen?“, die Datei liefert Caddy.
- Der Plattform-Server baut keine Verbindungen nach außen auf (systemd `IPAddressDeny=any`, nur
  Loopback erlaubt). Einzige optionale Ausnahme ist der Push des Git-Exports (unten).

## Vertrauensgrenzen

| Grenze | Schutzmaßnahme |
|---|---|
| Internet ↔ Caddy | TLS mit HSTS; Sicherheits-Header auch auf Fehlerantworten; Körpergrößen begrenzt (`/auth/*` 64 KB, `/api/*` 1 MB, nur Upload-Stücke `PUT /api/v1/uploads/*/dateien/*` 65 MiB und die Upload-Ankündigung `POST /api/v1/uploads` 13 MiB); `noindex` |
| Caddy ↔ Plattform-Server | nur über Loopback; der Dienst ist von außen nicht erreichbar; die interne Zugriffsprüfung `/api/authz` ist von außen gesperrt |
| Browser ↔ Plattform-Server (Sitzung) | Cookie `__Host-` mit `Secure`, `HttpOnly`, `SameSite=Lax`, ohne Domain; ändernde Anfragen nur mit eigener `Origin` und Header `X-Scanview` |
| Werkzeug/KI ↔ API (Token) | persönliches Token nur für `/api/v1/*`, nie für Paketdateien, Verwaltung, Statistik oder Konto; Umfang lesen bzw. lesen+schreiben; Rechte nie über das Konto hinaus; gedrosselt; widerrufbar |
| Nutzer ↔ Projekt | Rolle je Projekt (Betrachter, Rückmelder, Bearbeiter, Projektleitung), serverseitig bei jeder Anfrage geprüft; fremde Projekte verhalten sich wie nicht vorhandene (404) |
| Projektpaket ↔ Plattform | Paket nur lesend eingebunden; Pfade streng geprüft; Paketdateien als Daten ausgeliefert (`sandbox`-CSP, `nosniff`, `Cross-Origin-Resource-Policy`) |
| Plattform-Server ↔ Import-Worker | nur über die eigene SQLite-Datei `jobs.db` (keine Konten, Sitzungen, Geheimnisse) und die Berichtsablage; der Webdienst startet keine Prozesse (statischer Test), der Worker lädt vom Server nur das Auftragsmodul (Importtest). Auftragstypen und Optionen sind feste Aufzählungen, die Argumentliste des Prüfers enthält nur geprüfte IDs und Profilnamen; der Worker prüft eingetragene Aufträge erneut. Was der Worker zurückschreibt, liest der Webdienst als fremde Eingabe (Urteil, Zählung, Dateien ohne Symlinks, Größe begrenzt). Die Freigabe eines Berichts für Bearbeiter liegt in `scanview.db`, außer Reichweite des Workers |
| Uploader (Admin, Browser oder Skript) ↔ Plattform-Server | nur Admins der Instanz, nur mit `SCANVIEW_IMPORT=1` (sonst 404); Quote ohne Standardwert vor jeder Annahme, Plattenreserve; Pfade bis Tiefe 6 nach Positivliste, ohne `.`/`..`, ohne Groß/Klein-Dubletten, höchstens 10 000 Dateien; angelegt Segment für Segment über Ordner-Deskriptoren mit `O_NOFOLLOW`; Stücke nur an der angekündigten Stelle in fester Größe mit SHA-256 je Stück (sonst zurückgenommen), SHA-256 je Datei nachgerechnet; Endung und Kopfbytes müssen passen, Archive abgelehnt; halbe Uploads nach 24 h ohne Fortschritt entfernt. Der Webdienst liest vom Inhalt nur Kopfbytes |
| Import-Worker ↔ Übergabe ↔ Freischalten | Der Worker schreibt fertige Pakete nur in die Übergabe, nie in Eingang oder Paketwurzel, und hat weder sudo noch Rechte am Eingang. Ein root-Timer startet `scanview-uebergabe` als eigenen Nutzer (nur Gruppe `scanview-deploy`, nie `scanview`): er kopiert das Paket mit neuen Inodes in den Eingang (nur Ordner und reguläre Dateien, ein Name je Datei, Grenzen); danach ruft root das unveränderte Freischalt-Skript, das alles erneut prüft. root liest dabei keine Dateiinhalte aus der Übergabe, nur geprüfte Ordnernamen. Das Ergebnis geht als ein Wort an den Worker zurück |
| Import-Worker ↔ Auftrag (fremde Dateien) | Annahme: jeder Parser ist angreifbar. Je Auftrag eine transiente systemd-Unit: `PrivateNetwork`, Adressfamilien nur IP (kein Unix-Socket — sonst erreicht ein Job den Bus des User-Managers und startet eine Unit ohne Grenzen), `/run/user`, `/run/dbus`, `/run/systemd` unsichtbar, Pakete und Berichte anderer Projekte unsichtbar, `ProtectSystem=strict`, nur der Arbeitsordner beschreibbar, `NoNewPrivileges`, eigener PID-Namensraum, `RestrictNamespaces`, RAM ohne Swap, Laufzeit, Prozesse und Dateigröße begrenzt; Parallelität 1. Selbsttest beim Start (kein Netz, kein DNS, kein Unix-Socket, nichts außerhalb beschreibbar, RAM-Grenze greift), sonst startet der Worker nicht. Ausgabe nur nach Positivliste (reguläre Dateien, keine Links, Namen und Größe begrenzt) |
| Plattform-Server ↔ Postdienst | nur über das Spool-Verzeichnis: der Webdienst schreibt fertige Nachrichten (Empfänger und RFC-5322-Bytes, gebaut mit `email.message.EmailMessage`, Adressen strukturiert geprüft), der Postdienst schreibt nur einen Zustand aus fester Liste zurück, den der Webdienst als fremde Eingabe liest. Der Postdienst hat keine Datenbank, keine Konten, keine Pakete (Import- und Bibliothekstest) und prüft jede Nachricht erneut (genau ein Empfänger, kein Cc/Bcc, To = Envelope). Der Webdienst öffnet keine Verbindung nach außen (statischer Test: kein `smtplib`/`socket` im Server) |
| Postdienst ↔ SMTP-Server | TLS mit Zertifikats- und Hostnamenprüfung (implizit oder STARTTLS), ohne TLS nur localhost; Zugang aus einer Datei mit Rechten 0600; Netz der Unit ab Werk gesperrt, Freigabe per Drop-in nur für die Adressen des SMTP-Servers und den Resolver |
| Mail-Empfänger ↔ Plattform (Links aus der Mail) | Links tragen nur eine zufällige Kennung, Ablauf und eine HMAC-Signatur (Schlüssel 0600 im Zustandsverzeichnis); kein Projekt, kein Befund, kein Konto im Link. Der Link zum Befund meldet nicht an: ohne Sitzung zur Anmeldung, mit fremdem Konto 404. Abbestellen ohne Anmeldung nur per POST (Ein-Klick nach RFC 8058), GET zeigt nur die Bestätigungsseite (Mail-Scanner) |
| Dienst ↔ Betriebssystem | eigener Systemnutzer ohne Anmeldung, gehärtete systemd-Unit (unten), Schreibrecht nur auf das Zustandsverzeichnis |
| MCP-Server ↔ KI-Anbieter | Werkzeugergebnisse gehen an den Anbieter des Sprachmodells; ab Werk datensparsam (Namen anderer Konten und Token-Namen pseudonymisiert, Freitexte ausgeblendet), Originaldaten nur mit `SCANVIEW_MCP_PERSONENDATEN=voll` als Entscheidung des Verantwortlichen; Hinweise in `mcp-server/README.md`, Lese-Token empfohlen |

## Sichere Voreinstellungen

- **Registrierung nur per Einladung.** Es gibt keine Selbstregistrierung. Einladungslinks sind
  einmalig, 7 Tage gültig und stehen im URL-Fragment, damit sie in keinem Serverlog landen.
- **Passkey zuerst.** Anmeldung per Passkey (WebAuthn mit Nutzerverifikation); ein Passwort ist
  nur Rückfallweg (argon2id, mindestens 12 Zeichen, Abgleich mit häufigen Passwörtern,
  Drosselung je Konto und global).
- **Keine E-Mails.** Die Plattform verschickt keine E-Mails; Einladungen und Rücksetz-Links gibt
  der Administrator selbst über einen vertraulichen Kanal weiter. Es gibt keinen Mail-Reset.
- **Git-Export aus.** Der Export der Bearbeitungen in Git-Repositorys ist ab Werk abgeschaltet
  und pusht auch eingeschaltet nur, wenn ein Ziel und eine eigene Netzfreigabe für genau diese
  Unit konfiguriert sind. Autoren erscheinen dort pseudonym, Klarnamen nur mit Einwilligung.
- **Keine Tracker, keine externen Ressourcen.** Keine Analyse- oder Werbedienste, keine
  Einbindung fremder Server. Die Nutzungsstatistik zählt nur je Projekt und Tag, ohne
  Personenbezug.
- **Keine öffentliche API-Beschreibung.** Die OpenAPI-Beschreibung gibt es nur angemeldet, eine
  Dokumentationsoberfläche gar nicht. Die Produktversion liefert die API wie alle ihre Routen nur
  angemeldeten Nutzern (`GET /api/v1/version`); das ausgelieferte Web-Bundle enthält sie für die
  Diagnose. Das Verbergen der Version ist ausdrücklich keine Schutzmaßnahme — gegen bekannte
  Schwachstellen schützen die Fristen für Sicherheitskorrekturen
  ([`support-und-patches.md`](support-und-patches.md)).
- **Browser-Cache beim Sitzungswechsel geleert.** Paketdateien darf der Browser bis zu einer
  Stunde privat zwischenspeichern; Anmelden und Abmelden senden deshalb
  `Clear-Site-Data: "cache"`, damit nach einem Wechsel der Person im selben Browser nichts ohne
  Rechteprüfung aus dem Cache kommt. Browser ohne Unterstützung des Headers behalten den Cache
  bis zum Ablauf (Restrisiko bei gemeinsam genutzten Geräten: eigenes Browserprofil oder privates
  Fenster).
- **Kein Zugriff auf fremde Projekte sichtbar.** Ohne Recht antwortet die Plattform mit 404, als
  gäbe es das Projekt nicht.
- **Gehärteter Dienst.** systemd-Unit mit `NoNewPrivileges`, `ProtectSystem=strict`,
  `ProtectHome`, `PrivateTmp`, `PrivateDevices`, Systemaufruf-Filter, keinen Capabilities,
  `MemoryDenyWriteExecute`, `UMask=0077`, Speichergrenze und ohne Netz nach außen.
- **Ursprungszustand:** Die Umgebungsdatei wird bei der Einrichtung nur angelegt, wenn sie fehlt,
  aus der Vorlage `deploy/platform/scanview-server.env.beispiel` mit den obigen Werten. Zurück auf
  den Ursprungszustand führt: Dienst stoppen, Umgebungsdatei durch die Vorlage ersetzen und
  anpassen, Zustandsverzeichnis leeren (entfernt Konten, Sitzungen, Bearbeitungen und Protokoll —
  vorher sichern), Einrichtung erneut ausführen. Einzelheiten in [`betrieb.md`](betrieb.md).

## Verarbeitete Daten und Zweck

| Daten | Zweck | Wo | Aufbewahrung |
|---|---|---|---|
| Name, E-Mail des Kontos | Einladung, Anzeige im Verlauf, Zuordnung von Rechten | Datenbank | bis zum Anonymisieren des Kontos |
| Passkey (öffentlicher Schlüssel), Passwort-Hash (argon2id) | Anmeldung | Datenbank | bis zum Zurücksetzen oder Anonymisieren |
| Sitzungs-, Einladungs- und Token-Werte | Anmeldung, Einladung, API-Zugang | Datenbank nur als SHA-256 | Ablauf bzw. Widerruf |
| Rollen je Projekt (Betrachter, Rückmelder, Bearbeiter, Projektleitung) | Autorisierung | Datenbank | bis zum Entzug |
| Projektmetadaten, Merkpunkte, Messungen | Zusammenarbeit im Projekt | Datenbank | solange das Projekt besteht; Löschen ist weich (Verlauf) |
| Bearbeitungsprotokoll (Nutzer-ID, Zeit, Art, Vorher/Nachher) | Nachvollziehbarkeit von Änderungen | Datenbank, unveränderlich | unbegrenzt; enthält keine Namen, E-Mails, IP-Adressen |
| Lesezugriffsprotokoll (Konto-ID, Zeit, Projekt, Art, Kanal, Token-Name) | Nachvollziehbarkeit des Need-to-know, Aufklärung von Sicherheitsvorfällen; keine Leistungs- oder Verhaltenskontrolle | Datenbank, unveränderlich, nur Admins per Browser-Sitzung | `SCANVIEW_LESEZUGRIFFE_TAGE`, Standard 90 Tage, dann automatisch gelöscht; personenbezogen, keine IP, kein Gerät, keine E-Mail |
| Beteiligte eines Projekts (Name, Rolle, Gewerk) | Zusammenarbeit, Zuordnung im Projekt | Datenbank | solange die Rolle besteht; sichtbar für alle Projektbeteiligten, E-Mail, letzte Anmeldung und Sperrstatus nur für Projektleitung und Admins im Browser |
| Gewerke je Projekt (Name, Kurzzeichen, Bauherrenseite) | Zuordnung der Beteiligten | Datenbank | solange das Projekt besteht; Sachbezeichnung, ein Personenbezug entsteht nur über die Zuordnung |
| Projektgebundene Einladungen (Name, E-Mail, Rolle, Gewerk, Projekt) | Einladen durch die Projektleitung | Datenbank | wie jede Einladung: nie eingelöste 30 Tage nach Ablauf gelöscht, eingelöste bleiben als Nachweis |
| Projekt-Einstellungen und Kategorien (Vorlage, Schalter, Kategorienliste) | Projektkonfiguration | Datenbank | solange das Projekt besteht; keine Personendaten |
| Befund-Faden: Kommentare, Links, Status, Zuständigkeit (Gewerk, optional Person), Frist, Urheber und Zeit je Eintrag | Abstimmung über einen Befund zwischen Bauherrenseite und Firmen | Datenbank, nur anhängend; Text zusätzlich im Bearbeitungsprotokoll | solange das Projekt besteht; Zurückziehen ersetzt den Text für alle Leser, der Ursprungstext bleibt im Protokoll und im Git-Export; Anonymisieren eines Kontos ersetzt den Namen, nicht die Texte. Interne Einträge nur für die Bauherrenseite |
| Zeitpunkt der letzten Anmeldung | Verwaltung von Konten | Datenbank | bis zum Anonymisieren |
| Nutzungszähler je Projekt und Tag | Überblick für den Administrator | Datenbank | ohne Personenbezug |
| IP-Adresse fehlgeschlagener Passwort-Anmeldungen | Abwehr von Passwort-Raten (fail2ban) | nur Systemjournal, nie Datenbank | nach Journal-Rotation des Hosts |
| Zugriffslog von Caddy (IP-Adresse, Pfad, Status, Zeit je Anfrage) | Betrieb und Fehlersuche; Log des Betreibers, nicht der Anwendung | Log bzw. Journal von Caddy | nach Einstellung des Betreibers; IP-Adressen gekürzt, wenn die Härtung (Betriebsanleitung, Punkt 6) umgesetzt ist |
| Projektpakete (Punktwolken, Panoramen) | Anzeige | Paketwurzel, nur lesend | vom Betreiber verwaltet |
| E-Mail-Benachrichtigung: Einwilligung, SHA-256 der bestätigten Adresse (keine Klartext-Kopie) und Zeitpunkt, Abbestellungen je Befund | Benachrichtigung über Neues an eigenen Befunden, nur nach Einwilligung | Datenbank | bis zum Anonymisieren des Kontos (löscht alle drei Tabellen des Kontos); Ausschalten beendet den Versand sofort; Bestätigungs-Token nur als SHA-256, 2 Tage gültig |
| Mail-Ausgang (Empfänger-Konto, Projekt, Befund, Eintrag, Ereignisart, Zustand, Zeiten — kein Text) | Zustellung, Drosselung, Nachweis ohne Inhalt | Datenbank | 91 Tage, dann automatisch gelöscht (so lange gelten die Abbestell-Links) |
| Gerenderte Mail im Spool (Empfängeradresse, Ereignisart, Links) | Übergabe an den Postdienst | Spool-Verzeichnis des Hosts | bis der Postdienst das Ergebnis gemeldet hat (Sekunden bis Stunden bei Störung); Ergebnisdateien ohne Inhalt nach 30 Tagen |
| Freigaben von Panoramen und Fotos (Ebene, Art, Werkzeug, Manifest-SHA-256, Pfad und SHA-256 je Datei, freigebendes Konto, Zeiten) | Nachweis, welche Personenbilder wer für wen sichtbar gemacht hat | Datenbank, Dateiliste unveränderlich | solange das Projekt besteht; Zurücknehmen markiert, löscht nicht. Unanonymisierte Originale liegen nie in der Datenbank und sollen außerhalb der Paketwurzel bleiben (Löschfrist legt der Betreiber fest) |
| Uploads (Dateien mit relativen Pfaden, Größe, SHA-256, hochladendes Konto, Zeiten) | Neues Projekt aus hochgeladenen Daten | Upload-Ordner des Hosts (außerhalb der Paketwurzel), Angaben in der Datenbank | bis zum Löschen durch einen Admin, spätestens 24 h nach dem letzten Fortschritt (außer ein offener Import liest sie); nicht in der Sicherung |
| Auswertungen: Auftrag (Typ, Optionen, startendes Konto, Zeiten, Urteil) und Bericht (`ergebnis.json`, HTML, Karten) | Prüfbericht auf Anforderung der Projektleitung | `jobs.db` und Berichtsablage des Hosts; Freigabe für Bearbeiter in der Datenbank | bis die Projektleitung löscht; Löschen entfernt Ordner, Auftrag und Freigabe, im Protokoll bleibt nur, dass gelöscht wurde |
| Lokaler Befundentwurf (Formularinhalt, Zielpunkt, Blickposition, verkleinertes Vorschaubild) | ein angefangener Befund übersteht einen Verbindungsabbruch | IndexedDB des Browsers, je Konto und Projekt; geht nicht an den Server | bis zur Bestätigung durch den Server (ohne Speicherung am Server: bis zum Speichern im Browser), zum Verwerfen oder zum Abmelden im Portal (löscht die Entwürfe aller Konten dieses Browsers); ohne diese Anlässe bleibt er im Browser liegen, bis dasselbe Konto das Projekt dort wieder öffnet — ist er dann älter als 30 Tage, trägt er einen Zeitpunkt mehr als einen Tag in der Zukunft oder ist er unlesbar, wird er gelöscht statt angeboten. Das Ablaufen der Sitzung allein löscht nicht. |
| Lokale Merkpunkte ohne Schreibrecht am Server (Titel, Kategorie, Notiz, Position), etwa eines Betrachters | wer nicht ins Projekt schreiben darf, kann sich trotzdem Stellen im Viewer merken | `localStorage` des Browsers, je Projekt, **nicht** je Konto; geht nicht an den Server | bis zum Löschen im Viewer oder der Websitedaten des Browsers; Abmelden löscht sie nicht. Ein anderes Konto im selben Browser ohne Schreibrecht sieht sie; ein Konto mit Bearbeiterrecht bekommt angeboten, sie ins Projekt zu übernehmen (dann unter seinem Namen). Auf gemeinsam genutzten Geräten nach der Arbeit die Websitedaten löschen. |

Die Datenbank speichert keine IP-Adressen und keine User-Agents. Der Git-Export enthält nur
Merkpunkte, Messungen und Metadaten, keine Punktwolken.

Die Datenschutzhinweise der Instanz schreibt der Betreiber als Verantwortlicher; scanview liefert
keinen Rechtstext. `SCANVIEW_DATENSCHUTZ_URL` und `SCANVIEW_IMPRESSUM_URL` verlinken sie in der
Fußzeile jeder Portalseite, auch auf der Anmeldeseite (Einrichtung in [`betrieb.md`](betrieb.md)).
Die Hinweise sollten die Daten dieser Tabelle abdecken, auch die im Browser gespeicherten
(lokale Merkpunkte, lokaler Befundentwurf). Die Werte sind Links (https ohne Zugangsdaten oder ein
Pfad derselben Origin); das Portal lädt dafür nichts nach, die Content-Security-Policy bleibt
unverändert.

## Ausgehende Verbindungen

| Von | Wohin | Wann | Begrenzung |
|---|---|---|---|
| Plattform-Server | — | nie | systemd: nur Loopback |
| Git-Export (optional) | konfiguriertes Git-Ziel per SSH | nur wenn eingeschaltet und ein Ziel gesetzt ist | eigene Unit ohne Netz, bis der Betreiber es per Drop-in öffnet; per Drop-in auf die Adresse des Git-Hosts beschränken ([Betriebsanleitung](betrieb.md#push-an-ein-git-ziel)); Host-Schlüssel geprüft |
| Viewer/Portal | eigene Origin | immer | CSP `connect-src 'self'` |
| MCP-Server | die konfigurierte scanview-Instanz | bei jedem Werkzeugaufruf | nur `/api/v1/*`, gedrosselt je Token |
| Import-Werkzeug | — | nie | läuft offline |
| Übergabe und Freischalten | — | nie | eigene Unit ohne Netz |
| Postdienst (optional) | SMTP-Server des Betreibers, Port 465 oder 587, TLS | nur bei eingeschalteter Benachrichtigung, je Nachricht | Unit ab Werk ohne Netz; Drop-in gibt nur die Adressen des SMTP-Servers und den Resolver frei ([Betriebsanleitung](betrieb.md#e-mail-benachrichtigung-optional)); TLS-Zertifikat geprüft; höchstens `SCANVIEW_POST_PRO_MINUTE` Versuche je Minute |
| Import-Worker und seine Aufträge | — | nie | je Auftrag `PrivateNetwork=yes`, nur IP-Adressfamilien (im leeren Netz-Namensraum); geplante Ausnahme PVGIS-Abruf für Sonnenberichte als eigener Schritt ohne fremde Dateien, nur zum JRC-Host (noch nicht gebaut) |

## STRIDE je Komponente

S = Spoofing (Identität vortäuschen), T = Tampering (Manipulation), R = Repudiation (Abstreiten),
I = Information Disclosure (Preisgabe), D = Denial of Service, E = Elevation of Privilege (Rechte
ausweiten).

### Plattform-Server und Anmeldung

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| S | Anmeldung mit fremder Identität, Passwort-Raten | Passkey mit Nutzerverifikation; Passwort nur als Rückfall mit argon2id, Kontosperre, globaler Bremse und fail2ban; Einladungen einmalig und befristet, atomar verbraucht | NT-EINLADUNG, NT-DROSSEL; B-WB-M1, B-WB-M2 (behoben) |
| S | Sitzung übernehmen | 32 Byte Zufall, in der Datenbank nur als Hash, `__Host-`-Cookie, Leerlauf 14 und absolut 60 Tage, höchstens 10 Sitzungen je Konto, Rotation bei Neuanmeldung, serverseitiges Abmelden | NT-TOKEN, NT-HEADER; B-WB-N8 (behoben) |
| T | Anfragen fremder Seiten im Namen des Nutzers (CSRF) | eigene `Origin` und Header `X-Scanview` für jede ändernde Anfrage, `SameSite=Lax` | NT-HEADER |
| T | Überschreiben fremder Änderungen | optimistische Sperre je Datensatz (`version`, Konflikt 409) | Server-Tests |
| R | Änderungen abstreiten | unveränderliches Bearbeitungsprotokoll (Datenbank-Trigger verbieten Ändern und Löschen) mit Kanal Web, Kommandozeile oder API | Server-Tests |
| I | Daten fremder Projekte lesen (IDOR) | Rolle je Projekt bei jeder Anfrage; fremde Projekte wie nicht vorhandene; Projektliste nur mit Recht | NT-TRENNUNG, NT-RECHTE |
| I | Preisgabe über API-Beschreibung oder Fehlermeldungen | OpenAPI nur angemeldet und nur mit den für den Nutzer erreichbaren Pfaden; `Cache-Control: no-store` auf API- und Anmeldeantworten | NT-HEADER; B-WB-I2 (behoben) |
| D | Anmeldeflut blockiert den Dienst | eigener Rechenpool für argon2 mit Warteschlangengrenze; Grenzen für Challenges und Drosseltabellen; robuster Katalog bei defekten Paketen | NT-DROSSEL; B-WB-M3, B-WB-N1, B-WB-N2, B-WB-N4 (behoben) |
| E | Betrachter schreibt, Nicht-Admin verwaltet | Rolle serverseitig je Route; Verwaltungsrouten für Nicht-Admins 404; frische Anmeldung für Token-Anlage und Einwilligungen | NT-RECHTE; B-TE-M1 (behoben) |
| T | Rückmelder überschreibt oder löscht fremde Befunde | Rolle Rückmelder darf POIs nur anlegen und eigene ändern oder löschen; Prüfung des Urhebers in derselben Datenbank-Transaktion wie das Schreiben, auch beim Wiederherstellen einer gelöschten ID; Messungen, Projektbeschreibung, Export und Verlauf bleiben Bearbeitern vorbehalten | Server-Tests (Rolle Rückmelder, Sitzung und API-Token) |
| S | Rückmeldung gibt sich als Befund der Fachperson aus | Einträge eines Rückmelders tragen serverseitig erzwungen die Kategorie „Rückmeldung“ und den Namen des Urhebers; Bearbeiter können sie löschen | Server-Tests |
| D | Massenhaft Einträge eines Rückmelders füllen das Projekt | höchstens 100 aktive Einträge je Rückmelder und Projekt zusätzlich zur Projektgrenze | Server-Tests |
| E | Rückmelder-Token schreibt mehr als die Rolle | ein API-Token hat nie mehr Rechte als die Rolle seines Kontos; ein Lese-Token schreibt gar nicht | Server-Tests (API-Token) |
| E | Projektleitung erweitert eigene oder fremde Rechte | Die Projektleitung vergibt und entzieht nur Betrachter, Rückmelder und Bearbeiter, nie die Projektleitung und nie bei Admins oder anderen Projektleitungen (auch nicht bei sich selbst); geprüft gegen den Stand in derselben Datenbank-Transaktion wie das Schreiben | Server-Tests (Negativtests Projektleitung) |
| I | Firma liest interne Einträge der Bauherrenseite im Befund-Faden | Interne Einträge (nur Kommentar oder Link) lesen und schreiben nur Projektleitung, Admin und Bearbeiter in einem Gewerk mit Bauherrenseite, nie per Lese-Token; serverseitig gefiltert in Faden, Befundliste, POIs, Mängelliste, CSV/JSON-Export, Verlauf/JSONL und damit im MCP-Server; der Zeitpunkt des letzten Eintrags zählt sie für andere nicht mit | NT-FADEN |
| T | Firma schließt eigenen Mangel selbst ab oder ändert Frist und Zuständigkeit | Rückmelder setzen nur „in Arbeit“/„erledigt“ und nur, wenn sie (oder ihr Gewerk) zuständig sind; Abschließen, Ablehnen und Wiederöffnen nur die Bauherrenseite; Statuswechsel mit Versionsprüfung (409); Einträge per Trigger unveränderlich, Zurückziehen nur durch den Verfasser | NT-FADEN |
| E | Erbeutetes API-Token einer Projektleitung verwaltet das Projekt | Projektverwaltung nur mit Browser-Sitzung und Herkunftsprüfung; jedes API-Token (auch mit Schreibumfang) bekommt auf Verwaltungswegen 403 | Server-Tests (aus den Routen erzeugt) |
| I | Projektleitung sieht Konten anderer Projekte | Beteiligtenliste und offene Einladungen nur des eigenen Projekts; fremdes Projekt wie nicht vorhanden (404); E-Mail-Adressen nur für Projektleitung und Admin im Browser | Server-Tests |
| T | Einladung an ein bestehendes Konto übernimmt das Konto | Eine Einladung an eine bestehende E-Mail setzt nur das Projektrecht (nie herabstufend) und setzt nichts zurück — Passkeys, Passwort, Sitzungen und API-Tokens bleiben; Zurücksetzen bleibt der Instanzverwaltung vorbehalten | Server-Tests |
| E | Einladung oder Gewerk über Projektgrenzen | Projektgebundene Einladungen vergeben nur das Recht ihres Projekts; Gewerke werden nur innerhalb ihres Projekts zugeordnet | Server-Tests |
| S | Projektleitung legt per eigenem Einladungslink ein Konto für eine fremde E-Mail an; spätere Einladungen anderer Projekte an diese E-Mail gehen an sie | Einlösen übernimmt keine offenen Einladungen anderer Projekte; Restrisiko für Einladungen, die erst nach der Kontoanlage kommen (Zugriff direkt), bis zum E-Mail-Versand | Server-Tests; Restrisiko |

### Caddy und Auslieferung der Paketdateien (`forward_auth`)

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| S | Paketdateien ohne Anmeldung abrufen | jede Anfrage unter `/data/` fragt vorher den Server; ohne Sitzung 401, API-Tokens werden dort nicht angenommen | NT-FORWARD-AUTH |
| T | Pfadtricks (`..`, kodierte Varianten, Backslash, leere Segmente) | strenge Prüfung von Projektkennung und Pfad vor der Auslieferung; Pakete ohne Symlinks | NT-PFADE |
| I | Header verraten Server und Proxy | `Server`- und `Via`-Header entfernt, auch auf Fehlerantworten | NT-HEADER; B-BB-N1, B-BB-I2 (behoben) |
| I | Zwischenspeicher geben private Daten weiter | `Cache-Control: private` bzw. `no-store` je Dateiart | Instanzprüfung |
| D | große Anfragekörper | Körpergrenzen je Bereich, Range-Auslieferung ohne Dienstbeteiligung | Instanzprüfung |
| I | Personen auf Panoramen und Fotos erreichen Betrachter, Rückmelder oder Gäste | Freigabe an Dateien gebunden: unter Bearbeiter nur Dateien einer aktiven Freigabe mit passendem SHA-256 (Hash-Zwischenspeicher nach Inode, Größe, mtime und ctime); jede Datei unter `pano…`/`fotos…`/`images…`, die keine Freigabe nennt (Original, Sicherung, ausgetauschtes Bild), 404; Freigeben nur Projektleitung/Admin im Browser, „personenfrei“ nur Admin, beides im Protokoll; Gast-Links nie. Übergang: Panoramen älterer Pakete ohne Anonymisierungsblock bleiben sichtbar, bis die Projektleitung den Schalter einschaltet (akzeptiertes Risiko, im Portal gekennzeichnet) | NT-FREIGABE |
| E | eingeschleuste aktive Inhalte im Paket laufen in der Origin | Paketdateien mit `sandbox`-CSP, `nosniff` und `Cross-Origin-Resource-Policy`; nur Paket-Dateiendungen | NT-PFADE; B-WB-N5 (behoben) |
| E | Übernahme der Proxy-Konfiguration | empfohlen: Admin-Schnittstelle von Caddy auf einem Unix-Socket, systemd-Härtung für Caddy (Vorlagen in `deploy/platform/`) | Betriebsanleitung, Härtung 4/5 |

### Viewer und Portal (Browser)

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| S | untergeschobene Anmeldeseite | Anmeldung nur über die eigene Origin, Passkeys an die Relying-Party-ID gebunden (phishing-resistent) | E2E-Tests |
| T | Skriptinjektion aus Paket- oder Nutzerdaten (XSS) | Daten nur über `textContent`, keine HTML-Einfügung; strenge CSP für das Portal ohne Inline-Skripte, eigene CSP für den Viewer | NT-HEADER |
| I | Einbettung in fremde Seiten (Clickjacking) | `frame-ancestors 'none'`, `X-Frame-Options: DENY` | NT-HEADER |
| I | Abfluss an Dritte | keine externen Ressourcen, `connect-src 'self'`, `Referrer-Policy: same-origin` | Web-Tests |
| D | übergroße Pakete oder Eingaben | Grenzen für Merkpunkte und Messungen, Kacheln werden nach Sichtbarkeit geladen | Web-Tests |
| I | Befundlink verrät Inhalte | der Link enthält wie jeder Ansichtslink die Ansicht (Blickposition, sichtbare Ebenen, noch nicht am Server gespeicherte Messungen des Absenders), dazu Befund-ID und Datensatzstand — kein Geheimnis, kein Token; Öffnen verlangt Anmeldung und Projektrecht wie jede andere Ansicht | E2E-Tests |

### MCP-Server

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| S | Token gelangt an Dritte | Token wird beim Start aus einer Datei mit Rechten 600 in die Umgebung des Prozesses gelesen; Anleitung vermeidet Kommandozeile und Projektkonfiguration; Widerruf wirkt sofort | NT-TOKEN |
| T | fremde Texte im Projekt steuern die KI (Prompt Injection) | gemindert, nicht ausgeschlossen: Notizen und Beschreibungen anderer erreichen die KI ab Werk nicht, Titel und Kategorien (kurze Freitexte) aber weiterhin — darüber bleibt Injection möglich; Hinweis im README, Lese-Token empfohlen, Schreibaufträge bestätigen lassen, schreibende Werkzeuge mit Versionsprüfung; alle Änderungen im Protokoll mit Kanal `api` | `mcp-server/tests/test_datenschutz.py`, `mcp-server/README.md` |
| R | KI-Änderungen nicht zuordenbar | jede schreibende Token-Nutzung im Protokoll mit Token-Name | NT-TOKEN |
| I | Personendaten Dritter gehen an den KI-Anbieter | datensparsam ab Werk: Namen und Konto-IDs anderer als Pseudonym (HMAC mit geheimem Salz), Token-Namen ebenso, Freitexte ausgeblendet, auch im Verlauf und im Konfliktstand; `voll` nur als Betreiberentscheidung mit Warnung beim Start | `mcp-server/tests/test_datenschutz.py`; B-TE-M3 (behoben) |
| I | Kommentare im Befund-Faden erreichen den KI-Anbieter | Kommentar- und Linktexte im datensparsamen Modus ausgeblendet, Verfasser und zuständige Person als Pseudonym; Art, Status, Frist und Zeiten bleiben; interne Einträge liefert der Server nur der Bauherrenseite | `mcp-server/tests/test_datenschutz.py`, NT-FADEN |
| I | Personendaten der Beteiligten erreichen die KI | Das Werkzeug `beteiligte_lesen` liefert keine E-Mail-Adressen; im datensparsamen Modus laufen Name und Konto-ID durch die Pseudonymisierung | `mcp-server/tests/test_datenschutz.py` |
| E | Token kann mehr als das Konto | Rechte nie über das Konto hinaus; kein Zugriff auf Verwaltung, Konto und Paketdateien; Endpunkt-Tabelle gegen die OpenAPI geprüft | NT-RECHTE, NT-TOKEN |

### Import-Werkzeug und Projektpakete

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| T | manipuliertes oder fehlerhaftes Paket | Plattform liest nur Schema 2, prüft Projektkennung und Ordnernamen, lehnt Symlinks und defekte `project.json` mit Journal-Eintrag ab; Pakete werden nie überschrieben | NT-PFADE; B-WB-N4 (behoben) |
| E | Zugang zum Bereitstellen von Paketen wird zu root auf dem Host | Upload ohne sudo in einen Eingang außerhalb der ausgelieferten Daten; sudo nur für ein root-eigenes Freischalt-Skript (feste Pfade, keine Optionen, Prüfung der Projektkennung); es nimmt den Upload vor jeder Prüfung weg, prüft über Datei-Deskriptoren ohne Symlink-Folgen (nur Einträge des Hochladenden, keine ACLs oder anderen erweiterten Attribute, keine Hardlinks) und schaltet eine Kopie in neuen root-Dateien frei (offen gehaltene Schreibzugriffe des Hochladenden erreichen sie nicht); Deploy-Nutzer weder root noch in der Dienstgruppe | NT-FREISCHALTEN (Tests des Freischalt-Skripts als root im Wegwerf-Container); B-WB-M4 (behoben) |
| E | Werkzeuge der Paketaufbereitung mit Schwachstellen | Container-Werkzeuge (PDAL, untwine) laufen nur beim Datenaufbereiter, ohne Netz; auf den Host kommt ab Manifest-Schema /3 der Import-Code mit einer Umgebung aus `import/uv.lock`, die Images werden dort weder gebaut noch geladen und kein Auftrag erreicht Docker oder Podman. Ihr Inhalt steht im eigenen, nicht ausgelieferten Nachweis-SBOM `importbilder` (archiviert, informativ gescannt); das ausgelieferte Import-SBOM enthält nur die uv.lock-Umgebung | B-SCAN-IMG-PDAL, B-SCAN-IMG-UNTWINE, B-LK-UNTWINE (offen, beim Datenaufbereiter); B-SBOM-IMAGES behoben; `server/tests/test_isolation_import.py` |

### Import-Worker und Auswertungsjobs

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| E | Parser-Schwachstelle in einem Auftrag führt zu Code im Job | Job ohne Netz, ohne Unix-Sockets, ohne neue Rechte, Dateisystem nur lesend bis auf den eigenen Arbeitsordner, eigener PID-Namensraum; ein Ausbruch träfe den Worker-Nutzer (Auftragsdatenbank, Berichte), nicht Konten oder Sitzungen | `server/tests/test_isolation.py` (echtes `systemd-run`: Netz, DNS, Bus, Schreiben außerhalb, unsichtbare Pfade, Gegenprobe) |
| E | Webdienst wird übernommen und startet Prozesse oder Container | Webdienst ohne Prozessstarts außer `git` (Git-Export), kein Docker-Socket; Aufträge nur als Zeilen in `jobs.db` mit fester Typ- und Optionsliste | `server/tests/test_trennung_worker.py`, `server/tests/test_auswertungen.py` |
| T | Job fälscht Ergebnis oder Status | Status aus der Zusammenfassung von systemd, nicht aus der Job-Ausgabe (die geht in eine Datei); Ergebnis nur mit gültigem Gesamturteil, Dateien nach Positivliste; Freigabe für Bearbeiter nur in `scanview.db` | `server/tests/test_worker.py`, `test_isolation.py::test_job_kann_zusammenfassung_nicht_faelschen` |
| I | Bericht erreicht Unbefugte oder wird als Seite mit Skript ausgeliefert | Berichte nur über den Webdienst mit Projektrechten (Projektleitung und Admin, Bearbeiter nach Freigabe), als Download mit `sandbox`-CSP ohne Skripte, `nosniff`; Lesezugriff protokolliert; Löschen entfernt Ordner, Auftrag und Freigabe, das Protokoll behält nur „gelöscht“ | `server/tests/test_auswertungen.py` |
| D | Aufträge füllen Speicher, Platte oder Warteschlange | RAM je Typ (Prüfen 2 GB) ohne Swap, Laufzeit, Prozesse, Dateigröße, Plattenreserve vor dem Start, Parallelität 1, höchstens 3 offene Aufträge je Projekt | `test_isolation.py` (RAM, Zeit), `test_worker.py`, `test_auswertungen.py` |
| R | Wer hat gerechnet, freigegeben, gelöscht? | Bearbeitungsprotokoll `auswertung.gestartet`/`.freigabe`/`.geloescht` mit Kanal und Token-Name; Start per Token für KI-Agenten erlaubt, Freigabe und Löschen nicht | `test_auswertungen.py` |
| D | Worker stirbt mitten im Auftrag | Beim Start: verwaiste Units stoppen, „laufend“ wird „abgebrochen“, Arbeitsordner leer; Herzschlag in `jobs.db`, `/api/gesundheit` meldet „still“ | `test_isolation.py::test_neustart_stoppt_verwaiste_unit`, `test_worker.py` |

Grenzen: Pakete und Berichte anderer Projekte sieht der Job nicht (Paketwurzel und Berichtsablage als leeres
`tmpfs`, darin nur das eigene Paket lesend und der eigene Arbeitsordner), Prozesse außerhalb seines PID-Namensraums
auch nicht. Das übrige Dateisystem bleibt lesbar (Systemdateien, Import-Umgebung; am Arbeitsplatz `/home` nur
lesend, auf dem Server `SCANVIEW_JOBS_PROTECT_HOME=tmpfs`); weitere Pfade wie der Zustand des Webdienstes kommen
über `SCANVIEW_JOBS_UNSICHTBAR` hinzu. Podman rootless folgt nach einem Spike; bis dahin ist
`systemd-run` der Weg.

Gemessen im User-Manager (`systemd-run --user`, systemd 262, Arbeitsplatz, 21.10.2026):

| Property | Ergebnis |
|---|---|
| `PrivateNetwork=yes` | wirkt: nur `lo`, Verbindung zu 1.1.1.1:443 „Network is unreachable“ |
| `RestrictAddressFamilies=AF_INET` + `=AF_INET6` | wirkt: kein `AF_UNIX`/`AF_NETLINK`; damit keine Namensauflösung über systemd-resolved und kein Bus. Je Familie ein eigenes `-p` (Liste in einem Wert lehnt `systemd-run` ab). **Ohne** diese Sperre erreicht ein Job mit `PrivateNetwork` den Bus des User-Managers, startet per `systemd-run --user` eine Unit ohne Grenzen und hat dort volles Netz (nachgewiesen, Gegenprobe im Test) |
| `InaccessiblePaths=` | wirkt (Datei unlesbar) |
| `TemporaryFileSystem=…:ro`, `BindReadOnlyPaths=`, `BindPaths=` | wirken (fremde Projekte unsichtbar, eigenes Paket nur lesend) |
| `ProtectSystem=strict` + `ReadWritePaths=` | wirken: außerhalb des Arbeitsordners „Read-only file system“, auch `/tmp` und `/var/tmp` |
| `ProtectHome=read-only` / `tmpfs` | wirken (`tmpfs`: `/home` leer) |
| `NoNewPrivileges=yes` | wirkt (`NoNewPrivs: 1`) |
| `PrivateDevices=yes` | wirkt (`/dev` 18 statt 218 Einträge) |
| `RestrictNamespaces=yes` | wirkt (`unshare(CLONE_NEWUSER)` scheitert) |
| `PrivatePIDs=yes` | wirkt (nur PID 1 sichtbar; braucht systemd ≥ 257). Folge: der Job ist PID 1 und ignoriert SIGTERM ohne eigenen Handler, deshalb `TimeoutStopSec=10` |
| `MemoryMax=` + `MemorySwapMax=0` | wirken (`oom-kill`), sofern `memory` an `user@<uid>.service` delegiert ist; der Selbsttest prüft das: ein Kindprozess fordert 256 MB unter 64 MB Grenze, die Probe-Unit trägt als einzige `OOMPolicy=continue` (endet also nicht als `oom-kill`) und gilt nur als bestanden, wenn das Kind per SIGKILL starb und der Zähler `oom` der eigenen `memory.events` stieg — lesbar trotz `ProtectControlGroups=yes`, gemessen 22.10. mit systemd 262. Gegenprobe ohne `MemoryMax` und mit 1024 MB Grenze: „offen“ (`test_isolation.py`) |
| `RuntimeMaxSec=`, `TasksMax=`, `LimitFSIZE=` | `RuntimeMaxSec` wirkt (`timeout`); die anderen angenommen, nicht einzeln gemessen |
| `ProtectKernel*`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname`, `LockPersonality`, `RestrictRealtime`, `RestrictSUIDSGID`, `SystemCallArchitectures`, `PrivateIPC` | angenommen, Wirkung nicht einzeln gemessen |
| `ProtectProc=invisible` | ohne Wirkung im User-Manager (nicht gesetzt; `PrivatePIDs` deckt es ab) |
| `PrivateTmp=yes` | nicht gesetzt: scheitert mit 226/NAMESPACE, wenn der Arbeitsordner unter `/tmp` liegt; `TMPDIR` zeigt stattdessen in den Arbeitsordner |

Voraussetzungen auf dem Host: systemd ≥ 257, cgroup v2 mit `memory` in `cgroup.subtree_control` von
`user@<uid>.service`, Linger für den Dienstnutzer (oder `SCANVIEW_JOBS_SYSTEMD=system`), dazu
`dbus-user-session`: `systemd-run --user --wait` braucht den Bus des User-Managers, der private
Socket allein reicht nicht. `einrichten.sh` prüft das vor jeder Änderung, legt Nutzer, Rechte und
Units an und wartet auf den bestandenen Selbsttest ([Betriebsanleitung](betrieb.md), Abschnitt
„Jobläufer“). Die Worker-Unit selbst hat kein IP-Netz und erreicht ihren User-Manager nur
über `/run/user/<uid>`; die RAM-Grenze aller Aufträge zusammen liegt auf `user@<uid>.service`, weil
die Aufträge dort laufen und nicht in der Worker-Unit.

#### Uploads und neue Projekte

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| E | Nicht-Admin oder Lese-Token legt Uploads oder Projekte an | nur Admins (Nicht-Admins 404 wie Verwaltungswege, auch bei kaputtem Körper), Lese-Token 403; Schalter `SCANVIEW_IMPORT` aus → 404 | `server/tests/test_uploads_negativ.py` |
| T | Pfadtricks im Upload (`..`, `./`, absolute Pfade, Steuerzeichen, Dubletten, Symlink-Tausch) | Positivliste je Segment mit `fullmatch`, Tiefe und Anzahl begrenzt, Ablage über Ordner-Deskriptoren ohne Symlink-Folgen; ein getauschter Ordner führt zu Absage, nie zum Schreiben außerhalb | `test_uploads_negativ.py`, `test_uploads.py::test_kein_pfad_ausserhalb_ueber_symlink` |
| T | verfälschte oder doppelt gesendete Stücke | `Content-Range` an der Stückgrenze in fester Größe, `Content-Digest` je Stück, Stand per Vergleich weitergeschoben (doppeltes PUT schreibt nicht zweimal), eine Sperre je Datei; SHA-256 je Datei nachgerechnet | `test_uploads.py` |
| D | Uploads füllen die Platte | Quote vor der Annahme (angekündigte Größen zählen, auch halber Uploads), Plattenreserve, Aufräumen nach 24 h | `test_uploads.py` |
| E | Parser-Schwachstelle im Import (Mesh-, Splat-Leser) | Auftrag isoliert wie oben, nur der eigene Upload lesend sichtbar, andere Uploads, Pakete, Berichte und Übergabe unsichtbar; kein Container: kein Docker-Socket, kein `docker`/`podman`, keine Namensräume; texturierte Meshes (bräuchten einen Container für die Texturkodierung) und Punktwolken werden noch nicht angenommen | `server/tests/test_isolation_import.py` (echtes `systemd-run`, mit Gegenprobe) |
| T | Auftrag legt Links, Hardlinks oder Sonderdateien ins Paket | Worker prüft den Paketbaum vor der Übergabe, `scanview-uebergabe` beim Kopieren erneut, das Freischalt-Skript ein drittes Mal | `test_worker_import.py`, `test_uebergabe.py`, NT-FREISCHALTEN |
| E | übernommener Worker schaltet frei | Worker hat kein sudo und keinen Zugang zum Eingang; freigeschaltet wird nur über den Nutzer `scanview-uebergabe` und das unveränderte Skript (prüft Projektkennung, `VORHANDEN`, Eigentümer, Rechte) | `test_uebergabe.py`; Deploy-Probe |
| R | Wer hat hochgeladen, gestartet, abgebrochen? | Protokoll `upload.angelegt`/`.abgeschlossen`/`.geloescht`/`.abgelaufen`, `import.gestartet`/`.abgebrochen` mit Kanal und Token-Name | Server-Tests |

Projektordner unter `/srv/scanview/berichte` gehören dem Worker-Nutzer; ein übernommener Worker
könnte einen davon gegen einen Symlink tauschen. Deshalb öffnet der Webdienst jeden Pfadteil relativ
zum vorigen mit `O_NOFOLLOW` (Ablage, Projekt, Auftrag, Datei — ohne Wettlauf zwischen Prüfung und
Öffnen) und liefert keine Datei mit mehreren harten Links aus. Nachweis: Tests mit getauschtem
Projektordner (auch bei bestandener Pfadprüfung) und hart verlinkter Datei.

### E-Mail-Benachrichtigung und Postdienst

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| S | Mail-Link als Anmeldung missbraucht (weitergeleitete Mail) | Link meldet nicht an; nur das Konto, an das die Mail ging, kommt nach normaler Anmeldung weiter, sonst 404 | `server/tests/test_post.py` (NT-POST) |
| S | Fremde schalten Mails für ein Konto ein | Einwilligung nur in der eigenen Sitzung, Adresse erst nach Bestätigungslink im selben Konto; geänderte Konto-Adresse gilt als unbestätigt | NT-POST |
| T | Kopfzeilen-Injektion (CRLF, zusätzliche Empfänger) über Titel, Texte oder Adressen | Mail enthält keine Nutzertexte; Adressen über `email.headerregistry` geprüft, Steuerzeichen und Kommas abgelehnt; Nachricht mit `EmailMessage`, Envelope getrennt; der Postdienst prüft genau einen Empfänger, kein Cc/Bcc | NT-POST, `server/tests/test_trennung_post.py` |
| T | Link manipuliert (anderer Befund, anderes Konto, abgelaufen) | HMAC-SHA-256 über Art, Kennung und Ablauf; Schlüssel 0600; Ziel aus der Datenbank, nicht aus dem Link | NT-POST |
| I | Projektinhalte erreichen Postfächer, Anbieter und Mitleser | Mail nennt nur die Ereignisart; kein Projektname, Titel, Text, Koordinate; Link ohne lesbare Kennungen | NT-POST |
| I | Interne Einträge oder entzogene Rechte lösen Mails aus | Rechte, interne Sicht, Löschung, Einwilligung und Abbestellung werden unmittelbar vor dem Rendern geprüft, nicht beim Vormerken | NT-POST |
| D | Mailflut (Spam über Kommentare, Schleifen) | höchstens 10 Mails je Person und Stunde, eine je Befund in 15 Minuten, Versuche je Minute begrenzt; keine Antworten auf Mails (kein Eingang) | NT-POST |
| D | SMTP-Ausfall verliert Mails oder schickt sie doppelt | Ausgang in der Datenbank, Backoff bis 6 Versuche; Abbruch nach den Daten = „unklar“ ohne neuen Versuch | NT-POST |
| E | Postdienst übernommen → Datenbank oder Konten | eigener Nutzer, keine Datenbank, keine Pakete, nur Standardbibliothek, Netz ab Werk aus; liefert dem Webdienst nur einen Zustand aus fester Liste | `server/tests/test_trennung_post.py` |
| E | Webdienst bekommt Netz für Mail | Webdienst ohne `smtplib`/`socket` (statischer Test), Unit unverändert `IPAddressDeny=any` | `server/tests/test_trennung_post.py` |

### Fotos und PDFs am Befund-Faden

JPEG, PNG und PDF werden bis **20 MB** angenommen. Der Webdienst prüft Content-Type gegen
Kopfbytes und zählt die Größe beim Empfang. Bilder dekodiert und kodiert ausschließlich der
Jobläufer im isolierten Auftrag `anhang_bereinigen` neu: kein Netz, nur die Eingabedatei lesbar,
ein eigener Arbeitsordner beschreibbar, **512 MB** RAM, 60 Sekunden und höchstens 40 Megapixel.
Der isolierte Lauf mit 40 Megapixeln erreichte laut Messprotokoll im Commit `5bea336`
**324,5 MB**, gedreht 353,6 MB. Das sind Einzelmessungen, keine Obergrenze für alle Eingaben.
Maßgeblich bleibt das durchgesetzte Budget.

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| T | Bilddecoder angreifbar (Dekompressionsbombe, Polyglott, Parser-Lücke) | isolierte Neukodierung mit Pixel-, Größen-, RAM- und Zeitgrenzen; der Webdienst dekodiert keine Bilder | NT-ANHANG, `import/tests/test_anhang.py`, `server/tests/test_worker_anhang.py` |
| I | GPS, Gerät und Namen in Bildmetadaten | neues JPEG aus den Pixeln, ohne EXIF, GPS, XMP, IPTC, Kommentare, Vorschaubilder und angehängte Bytes; nur das Farbprofil bleibt im Bild; Aufnahmezeit separat als Kamerazeit ohne Zeitzone | NT-ANHANG |
| T | Ausgabe des Decoders manipuliert | Worker und Webdienst prüfen die Ausgabe erneut als fremde Eingabe: reguläre Dateien, keine Symlinks oder Hardlinks, JPEG-Kopf, Maße und Größe; Ablage unter eigenem SHA-256 | NT-ANHANG |
| T | aktive Inhalte im PDF | PDF wird nicht gerendert oder bereinigt, nur unverändert als Download mit Hinweis „PDF aus externer Quelle“ ausgeliefert; `attachment`, `nosniff`, CSP `sandbox`, `private, no-store` | NT-ANHANG |
| I | fremdes Projekt, Gast oder gelöschter Befund liest Anhang | eigenes Zugriffstor prüft Projektrecht und sichtbaren, nicht zurückgezogenen Faden-Eintrag bei jedem Datei- und Statusabruf; kein Zugriff über Gast-Links | NT-ANHANG |
| E | unbefugtes Hochladen oder Zurückziehen | Hochladen nur mit Recht zum Kommentieren und CSRF-Schutz in der Sitzung bzw. Schreib-Token; Zurückziehen nur durch den Verfasser | NT-ANHANG |
| D | Uploadflut und volle Platte | höchstens 50 Anhänge je Befund, Projektquote (Standard 2048 MB), Platzreserve und begrenzte Warteschlange | NT-ANHANG |

Bilder erscheinen bis zur erfolgreichen Übernahme als „in Prüfung“. Dateinamen sind nur bereinigte
Anzeigenamen; Ablage und Downloadname werden aus Hash bzw. Anhang-ID gebildet. Die Bereinigung
entfernt keine Personen aus den Bildpixeln und keine personenbezogenen Inhalte aus PDFs.

### Panoramen und Fotos: Freigabe an Dateien gebunden

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| I | Bild mit Personen nach der Freigabe ausgetauscht | Freigabe speichert Pfad und SHA-256 jeder Datei; das Tor vergleicht bei jeder Anfrage den Hash (Zwischenspeicher nach Inode, Größe, mtime, ctime) | NT-FREIGABE |
| I | Original oder Sicherungskopie im Paketordner | alles unter `pano…`, `fotos…`, `images…`, das keine Freigabe nennt, ist für Betrachter und Rückmelder 404 | NT-FREIGABE |
| E | Unbefugte geben frei | nur Projektleitung und Admin im Browser, „personenfrei“ nur Admin, beides im Protokoll; per Token nur lesen | NT-FREIGABE |
| T | Anonymisierung im Import umgangen | `panos-kameras --anonymisiert` prüft Entscheidung, Revision, Quell-Hash und Verifikation selbst nach, verwendet nur die Ausgaben und prüft nach dem Schreiben erneut | `import/tests/test_anonymisiert.py` |

### Lieferkette und Auslieferung

| | Bedrohung | Maßnahme | Nachweis |
|---|---|---|---|
| T | kompromittierte Abhängigkeit | Laufzeitabhängigkeiten mit exakten Versionen und Hashes in Lockfiles; Mindestalter für neue Versionen; keine Install-Skripte von Abhängigkeiten; SBOM je Artefakt; Schwachstellen-Scan vor jedem Release | Release-Kette ([`release.md`](release.md)) |
| T | manipuliertes Release | Artefakte nur aus einem eingefrorenen Commit; signiertes Manifest mit SHA-256 aller Artefakte und signierter Tag; `einrichten.sh --release` prüft Signatur, Prüfsummen und Archivinhalt vor jeder Änderung gegen den Vertrauensanker des Hosts | Negativkontrolle „Manifest verändert“; Tests des Prüfskripts (manipuliertes Archiv, Manifest, fremder Schlüssel, Probe-Manifest, unsichere Archivpfade) |
| I | Geheimnisse im veröffentlichten Quelltext | Geheimnissuche (gitleaks) im Export und im Bauergebnis | Negativkontrolle „Testsecret im Export“ |

## Restrisiken

Offene Befunde, ihre Schwere und ihr Status stehen im [Befundregister](befunde.md). Kein Release
wird mit einem offenen Befund der Schwere „hoch“ oder höher in einem ausgelieferten Artefakt
veröffentlicht; offene mittlere Befunde zu Authentifizierung, Rechten, Projekttrennung oder
Datenabfluss blockieren ebenfalls. Die öffentliche Fassung nennt zu offenen Befunden keine
Einzelheiten, bis eine Korrektur verfügbar ist.

Bewusst getragene Restrisiken des Imports:

- Ein übernommener Import-Worker kann unter jeder noch freien Projektkennung ein Paket freischalten
  lassen. Rechte hängen daran nicht (neue Projekte sehen zuerst nur Admins), und das Freischalt-Skript
  überschreibt nie ein vorhandenes Paket.
- Ein Paket ohne Punktwolke trägt keinen Lage- oder Maßstabsnachweis; der Viewer weist darauf hin
  („Modellmaß“), sperrt aber nichts.
- Wird eine Übergabe nicht bestätigt (Zeitüberschreitung), meldet der Auftrag einen Fehler, das
  Paket kann aber später noch erscheinen.

Bewusst getragene Restrisiken der Rolle Projektleitung:

- Eine Projektleitung erfährt, ob es zu einer E-Mail schon ein Konto gibt, und sieht nach dem Hinzufügen Name und letzte Anmeldung dieses Kontos; auch „gesperrt“ ist erkennbar. Hingenommen, weil nur die Instanzverwaltung Projektleitungen bestimmt; jede Einladung und jede Vergabe wird protokolliert und steht im Verlauf.
- Bis zum E-Mail-Versand prüft niemand die E-Mail-Adresse einer Einladung: Eine Projektleitung kann per eigenem Link ein Konto für eine fremde Adresse anlegen; Einladungen anderer Projekte, die danach an diese Adresse gehen, erteilen dem Konto direkt Zugriff.

Bewusst getragene Restrisiken von Freigabe und Benachrichtigung:

- Panoramen älterer Pakete ohne Anonymisierungsblock bleiben für alle Beteiligten sichtbar, bis die
  Projektleitung „Panoramen nur freigegeben zeigen“ einschaltet (Übergang; im Portal gekennzeichnet).
- „Personenfrei“ ist eine Erklärung eines Admins ohne technische Prüfung; sie wird protokolliert.
- Eine zurückgenommene Freigabe sperrt jede neue Anfrage sofort; Bilder, die ein Betrachter schon
  geladen hat, kann sein Browser bis zu einer Stunde aus dem Zwischenspeicher zeigen
  (`Cache-Control: private, max-age=3600` für Paketdateien).
- Rahmenfotos haben noch keinen Anonymisierungsschritt; freigeben lassen sie sich nur als
  „personenfrei“.
- Die Mail-Adresse wird per Bestätigungslink geprüft, nicht die Person: wer das Postfach liest,
  bekommt die (inhaltsfreien) Benachrichtigungen. Der Link zum Befund verlangt trotzdem das Konto.
- Netzfreigabe des Postdienstes auf feste Adressen: wechselt der Anbieter seine Adressen, stockt der
  Versand, bis der Betreiber nachzieht (nftables-Set mit eigenem Resolver ist noch offen).

Bewusst getragene Restrisiken des Befund-Fadens:

- Der Git-Export (Betreiber) enthält alle Einträge, auch interne und den Ursprungstext
  zurückgezogener Kommentare; wer Zugriff auf das Export-Repository hat, sieht sie.
- Einträge erscheinen nach außen nur mit einer zufälligen Kennung (`schluessel`), nie mit der
  fortlaufenden Nummer der Datenbank; die Obergrenze zählt interne und öffentliche Einträge
  getrennt. Die Ereignisnummern im Verlauf zählen dagegen instanzweit über alle Projekte, Konten-
  und Verwaltungsvorgänge; Lücken sind dort der Normalfall und lassen sich keinem internen Eintrag
  zuordnen.
- Kommentartexte bleiben nach dem Anonymisieren eines Kontos stehen (nur der Name wird ersetzt);
  ein späterer Löschweg ist im Datenmodell vorbereitet, aber nicht gebaut.

Bewusst getragene Restrisiken der Rolle Rückmelder:

- Ein Rückmelder sieht alle Befunde und Rückmeldungen des Projekts samt Urhebernamen (wie ein
  Betrachter).
- Anlegen und Löschen in Schleife lässt weich gelöschte Einträge und Protokollereignisse wachsen
  (wie bei Bearbeitern).
- Ob es eine gelöschte fremde ID gab, ist erkennbar: Anlegen mit dieser ID wird abgewiesen
  (403), eine unbenutzte ID nicht. Der Inhalt bleibt verborgen. Vom Server vergebene IDs sind
  zufällig und nicht ratbar; betroffen sind nur IDs, die ein Client selbst wählt.

Wie diese Bewertung geprüft wird, steht im [Pentest-Plan](pentest-plan.md); die Zuordnung zu den
Anforderungen des Cyber Resilience Act in der [Kontrollmatrix](kontrollmatrix.md).
