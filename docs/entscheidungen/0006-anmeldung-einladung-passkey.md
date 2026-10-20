# 0006 Anmeldung per Einladung und Passkey

- **Status:** angenommen
- **Datum:** 2026-09-27

## Kontext

Nutzer sind wenige, namentlich bekannte Personen. Eine Selbstregistrierung ist nicht gewollt, ein
E-Mail-Versand soll nicht betrieben werden, und Passwörter sind der häufigste Angriffsweg.

## Entscheidung

- Konten entstehen **nur per Einladung**: einmaliger Link, 7 Tage gültig, Token im URL-Fragment
  (erscheint in keinem Log), in der Datenbank nur als Hash; verbraucht erst beim erfolgreichen
  Anlegen des Zugangs.
- Anmeldung standardmäßig per **Passkey** (WebAuthn, Nutzerverifikation Pflicht, mehrere Passkeys je
  Konto).
- **Passwort als Rückfallweg** (argon2id, mindestens 12 Zeichen, Abgleich gegen häufige Passwörter,
  Drosselung je Konto und global, Fehlversuche für fail2ban protokolliert).
- Keine Wiederherstellung per E-Mail: Ein Admin erzeugt eine Zurücksetzen-Einladung.
- Sitzung als `__Host-`-Cookie (Secure, HttpOnly, SameSite=Lax, ohne Domain), serverseitig als
  Hash, Leerlauf 14 Tage, absolut 60 Tage. Ändernde Anfragen nur mit eigener `Origin` und Header
  `X-Scanview`.

## Konsequenzen

- Kein Mailserver, keine Selbstregistrierung, kein Passwort-Reset-Link als Angriffsfläche.
- WebAuthn verlangt HTTPS und bindet Konten an den Hostnamen der Instanz; Konten sind zwischen
  Instanzen nicht übertragbar.
- Der Passwort-Rückfallweg bleibt, bis entschieden wird, ihn zugunsten mehrerer Passkeys zu
  streichen.
