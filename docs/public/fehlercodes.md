# Fehlercodes der API

Jede Fehlerantwort der API v1 hat dieselbe Form:

```json
{"dienst": "scanview", "fehler": "Dieses Token darf nur lesen.", "code": "nur_lesen",
 "hinweis": "Für Änderungen ein Token mit Schreibrecht im Portal anlegen lassen."}
```

- `fehler` ist ein Satz für Menschen. Sein Wortlaut kann sich ändern.
- `code` ist fest. Programme und KI-Agenten verzweigen auf ihn, nie auf den Text. Codes kommen
  nur hinzu; keiner wird umbenannt oder entfernt.
- `hinweis` sagt, was stattdessen ginge, wenn es dazu etwas zu sagen gibt.
- Bei einem Bearbeitungskonflikt (409, `konflikt`) steht der aktuelle Stand in `aktuell`.

Hat ein Fehler keinen eigenen Code, gilt der Standard seines HTTP-Status. Ein 404 trägt immer
`nicht_gefunden`, auch wenn das Projekt existiert, aber der Zugriff fehlt: Die Antwort verrät
nicht, welche Projekte es gibt.

Antworten mit 202 sind keine Fehler. Legt ein Token für KI-Agenten etwas an, entsteht ein
Vorschlag (`pruefstand: "vorschlag"`), den ein Mensch annimmt oder verwirft.

## Liste

<!-- erzeugt aus server/src/scanview_server/fehlercodes.py (tabelle()); Drift-Test test_fehlercodes.py -->
| Code | Standard für HTTP | Hinweis in der Antwort |
|---|---|---|
| `anfrage_ungueltig` | 400 |  |
| `anmeldung_fehlgeschlagen` |  |  |
| `befund_ist_vorschlag` |  | Erst nimmt ein Mensch den Vorschlag an, dann geht der Faden. |
| `bereich_ungueltig` | 416 |  |
| `csrf_fehlt` |  | Browser-Sitzungen senden den CSRF-Kopf mit; Skripte melden sich mit einem Token an. |
| `eingabe_ungueltig` | 422 | Eingabe gegen das Schema in der OpenAPI-Datei prüfen. |
| `fehler` |  |  |
| `gesperrt` |  | Nach zu vielen Fehlversuchen gesperrt: erst nach 15 Minuten erneut versuchen. |
| `id_vergeben` |  | Eine andere ID wählen oder, wo erlaubt, ohne ID anlegen. |
| `keine_berechtigung` | 403 | Dafür fehlt das Recht: Rolle im Projekt, Umfang des Tokens oder Art der Anmeldung. |
| `konflikt` |  | Den aktuellen Stand aus „aktuell“ übernehmen und mit dessen „version“ erneut senden. |
| `link_ungueltig` |  | Den Link bei der Person erfragen, die ihn geteilt hat. |
| `mensch_prueft` |  | Vorschläge nimmt ein Mensch an oder verwirft sie (Liste „zu prüfen“ im Portal). Den Vorschlag dort vorlegen. |
| `methode_nicht_erlaubt` | 405 | Erlaubte Methoden stehen im Kopf „Allow“. |
| `neu_anmelden` |  | Erneut anmelden und den Vorgang wiederholen. |
| `nicht_angemeldet` | 401 | Anmelden oder ein gültiges Token im Kopf „Authorization: Bearer …“ mitsenden; ein abgelaufenes oder widerrufenes Token im Portal unter Einstellungen → API-Zugänge ersetzen. |
| `nicht_bereit` | 503 | Später erneut versuchen. |
| `nicht_gefunden` | 404 | ID prüfen. Auch ohne Zugriff auf das Projekt antwortet die Instanz mit 404. |
| `nicht_verfuegbar` |  | Diese Funktion ist auf dieser Instanz noch nicht freigeschaltet. |
| `nur_im_browser` |  | Das entscheidet ein Mensch im Browser; dafür gibt es bewusst keinen Weg per Token. |
| `nur_lesen` |  | Für Änderungen ein Token mit Schreibrecht im Portal anlegen lassen. |
| `nur_vorschlag` |  | Einen neuen Vorschlag anlegen; gültige Einträge ändert ein Mensch. |
| `passwort_falsch` |  |  |
| `passwort_noetig` |  | Das Passwort des Links mitsenden. |
| `schon_entschieden` |  | Den Eintrag neu lesen; er hat schon einen Stand. |
| `serverfehler` | 500 |  |
| `statuswechsel_unmoeglich` |  | Den aktuellen Status lesen und von dort aus wechseln. |
| `typ_nicht_erlaubt` | 415 |  |
| `ueberlastet` |  | Kurz warten, dann erneut versuchen. |
| `version_fehlt` |  | Den Eintrag lesen und dessen „version“ mitsenden. |
| `vorschlag_offen` |  | Die Entscheidung über den offenen Vorschlag abwarten. |
| `zu_gross` | 413 |  |
| `zu_viele_anfragen` | 429 | Kurz warten, dann erneut versuchen. |
| `zu_viele_auftraege` |  | Warten, bis ein laufender Auftrag fertig ist (Stand abfragen). |
| `zustand` | 409 | Der Vorgang passt nicht zum aktuellen Zustand. Den Stand lesen, dann entscheiden; nicht unverändert wiederholen. |
