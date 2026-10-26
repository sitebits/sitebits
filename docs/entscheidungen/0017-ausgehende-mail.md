# 0017 Ausgehende E-Mail-Benachrichtigung über einen eigenen Postdienst

- **Status:** angenommen
- **Datum:** 2026-10-22
- **Ergänzt:** [0006](0006-anmeldung-einladung-passkey.md) (dort: „ein E-Mail-Versand soll nicht betrieben werden“)

## Kontext

Beteiligte an Befunden (Firmen, Rückmelder) öffnen die Plattform nur, wenn sie wissen, dass es Neues gibt.
ADR 0006 hat Mail ausgeschlossen, um keine Angriffsfläche über Mail-Anmeldung und Passwort-Reset zu öffnen.
Der Webdienst hat keine Netzverbindung nach außen und soll keine bekommen.

## Entscheidung

- **Nur ausgehend, nur Benachrichtigung.** Keine Anmeldung, kein Passwort-Reset, keine Einladung per Mail.
  Antworten auf Mails kommen nicht an; geantwortet wird im Browser. Eingehende Mail ist ein eigener,
  späterer Schritt mit eigener Entscheidung.
- **Eigener Dienst `scanview-post`.** Eigener Nutzer, keine Datenbank, nur Standardbibliothek
  (`smtplib`, `email`). Er liest fertige Nachrichten aus einem Spool-Verzeichnis und schreibt das Ergebnis
  zurück. Netz ab Werk aus, der Betreiber öffnet per Drop-in nur seinen SMTP-Server. Der Webdienst bleibt
  ohne Außenverbindung.
- **Herstellerneutral.** SMTP mit Anmeldung, implizites TLS oder STARTTLS mit Zertifikatsprüfung; ohne TLS
  nur zu localhost. Welcher Dienst, entscheidet der Betreiber. Das Passwort steht in einer eigenen Datei mit
  Rechten 0600.
- **Ab Werk aus**, auf drei Ebenen: Instanz (`SCANVIEW_POST`), Projekt (Schalter, Standard an) und Person
  (Einwilligung im Konto plus Bestätigung der Adresse per Link). Abbestellen je Befund und für alles, auch
  per Ein-Klick-Link ohne Anmeldung (RFC 8058).
- **Datensparsam.** Die Mail nennt nur die Art des Ereignisses und enthält Links — kein Projektname, kein
  Befundtitel, kein Text, keine Koordinaten. Die Links tragen nur eine zufällige Kennung und eine
  HMAC-Signatur mit Ablauf (14 Tage zum Befund, 90 Tage zum Abbestellen). Der Link zum Befund meldet
  niemanden an: Er verlangt die normale Anmeldung, und nur das Konto, an das die Mail ging, kommt weiter.
- **Rechte erst beim Rendern.** Beim Schreiben eines Faden-Eintrags wird nur vorgemerkt (dieselbe
  Transaktion). Ein Hintergrund-Thread im Webdienst prüft unmittelbar vor dem Rendern Rolle, interne Sicht,
  gelöschte Befunde, Einwilligung, bestätigte Adresse, Abbestellungen, Projektschalter und Drosselung
  (10 Mails je Person und Stunde, eine je Befund in 15 Minuten).
- **Zustellung höchstens einmal.** Vorübergehende Fehler werden mit Backoff wiederholt. Bricht die
  Verbindung nach dem Senden der Daten ab, gilt die Zustellung als unklar und wird nicht wiederholt.

## Konsequenzen

- Mailadressen werden zweckgebunden genutzt: Einwilligung und bestätigte Adresse stehen in der Datenbank.
  Der Betreiber ergänzt sein Verzeichnis der Verarbeitungstätigkeiten (betrieb.md).
- DKIM, SPF und DMARC der Absenderdomain sind Sache des Betreibers. Ohne sie landen Mails im Spam.
- Wer eine Mail weiterleitet, gibt nur einen Link weiter, der ohne das richtige Konto nichts öffnet.
- Bis zu einer Netzfreigabe per nftables-Set mit eigenem Resolver erfolgt die Freigabe per
  `IPAddressAllow` auf feste Adressen. Wechselt der Anbieter seine Adressen, stockt der Versand, bis der
  Betreiber nachzieht; der Ausgang bleibt erhalten.
- **KI-Nutzung:** Agenten können je Befund ab- oder anbestellen (API und MCP, nur für ihr eigenes Konto).
  Einwilligung und Bestätigung der Adresse gibt es nur im Browser.
