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
| **Import-Werkzeug** (`import/`) | erzeugt Projektpakete aus Scandaten (Punktwolken, Panoramen, Standpunkte) | beim Hersteller bzw. Datenaufbereiter, ohne Netz | nein (internes Werkzeug) |
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
| Internet ↔ Caddy | TLS mit HSTS; Sicherheits-Header auch auf Fehlerantworten; Körpergrößen begrenzt (`/auth/*` 64 KB, `/api/*` 1 MB); `noindex` |
| Caddy ↔ Plattform-Server | nur über Loopback; der Dienst ist von außen nicht erreichbar; die interne Zugriffsprüfung `/api/authz` ist von außen gesperrt |
| Browser ↔ Plattform-Server (Sitzung) | Cookie `__Host-` mit `Secure`, `HttpOnly`, `SameSite=Lax`, ohne Domain; ändernde Anfragen nur mit eigener `Origin` und Header `X-Scanview` |
| Werkzeug/KI ↔ API (Token) | persönliches Token nur für `/api/v1/*`, nie für Paketdateien, Verwaltung, Statistik oder Konto; Umfang lesen bzw. lesen+schreiben; Rechte nie über das Konto hinaus; gedrosselt; widerrufbar |
| Nutzer ↔ Projekt | Rolle je Projekt (Betrachter, Rückmelder, Bearbeiter, Projektleitung), serverseitig bei jeder Anfrage geprüft; fremde Projekte verhalten sich wie nicht vorhandene (404) |
| Projektpaket ↔ Plattform | Paket nur lesend eingebunden; Pfade streng geprüft; Paketdateien als Daten ausgeliefert (`sandbox`-CSP, `nosniff`, `Cross-Origin-Resource-Policy`) |
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
| E | Werkzeuge der Paketaufbereitung mit Schwachstellen | Werkzeuge in Containern ohne Netz; nicht Teil der Auslieferung | B-SCAN-IMG-PDAL, B-SCAN-IMG-UNTWINE, B-LK-UNTWINE (offen, nur Import) |

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

Bewusst getragene Restrisiken der Rolle Projektleitung:

- Eine Projektleitung erfährt, ob es zu einer E-Mail schon ein Konto gibt, und sieht nach dem Hinzufügen Name und letzte Anmeldung dieses Kontos; auch „gesperrt“ ist erkennbar. Hingenommen, weil nur die Instanzverwaltung Projektleitungen bestimmt; jede Einladung und jede Vergabe wird protokolliert und steht im Verlauf.
- Bis zum E-Mail-Versand prüft niemand die E-Mail-Adresse einer Einladung: Eine Projektleitung kann per eigenem Link ein Konto für eine fremde Adresse anlegen; Einladungen anderer Projekte, die danach an diese Adresse gehen, erteilen dem Konto direkt Zugriff.

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
