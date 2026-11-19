"""Feste Fehlercodes für jede Fehlerantwort (nach dem Muster „Ablehnungen sind Antworten“).

Jede Fehlerantwort trägt neben dem Satz für Menschen (`fehler`) einen stabilen `code`, auf den
ein Programm oder ein KI-Agent verzweigen kann, und, wo es etwas zu sagen gibt, einen `hinweis`,
was stattdessen ginge. Der Text darf sich ändern, der Code nicht: Codes werden nur ergänzt,
nie umbenannt oder entfernt (öffentliche Liste: `docs/public/fehlercodes.md`).

Bestimmt wird der Code an einer Stelle (`web.fehler`, Konflikt-Handler): zuerst über den genauen
Fehlertext aus dem Register unten, sonst über den HTTP-Status. So hat jede Antwort einen Code,
ohne dass jede Fehlerstelle ihn selbst setzen muss.

Grenze (wie `web._vorrang`): Ein 404 verrät nie, ob es das Projekt gibt oder nur der Zugriff
fehlt. Deshalb gibt es für 404 genau einen Code und einen festen Hinweis ohne Rolle oder Projekt.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache


@dataclass(frozen=True, slots=True)
class Code:
    name: str
    hinweis: str | None = None


# --- Codes ----------------------------------------------------------------------------------

ANFRAGE_UNGUELTIG = Code("anfrage_ungueltig")
NICHT_ANGEMELDET = Code(
    "nicht_angemeldet",
    "Anmelden oder ein gültiges Token im Kopf „Authorization: Bearer …“ mitsenden; ein "
    "abgelaufenes oder widerrufenes Token im Portal unter Einstellungen → API-Zugänge ersetzen.",
)
KEINE_BERECHTIGUNG = Code(
    "keine_berechtigung",
    "Dafür fehlt das Recht: Rolle im Projekt, Umfang des Tokens oder Art der Anmeldung.",
)
NICHT_GEFUNDEN = Code(
    "nicht_gefunden",
    "ID prüfen. Auch ohne Zugriff auf das Projekt antwortet die Instanz mit 404.",
)
METHODE_NICHT_ERLAUBT = Code("methode_nicht_erlaubt", "Erlaubte Methoden stehen im Kopf „Allow“.")
KONFLIKT = Code(
    "konflikt",
    "Den aktuellen Stand aus „aktuell“ übernehmen und mit dessen „version“ erneut senden.",
)
ID_VERGEBEN = Code(
    "id_vergeben",
    "Eine andere ID wählen oder, wo erlaubt, ohne ID anlegen.",
)
ZUSTAND = Code(
    "zustand",
    "Der Vorgang passt nicht zum aktuellen Zustand. Den Stand lesen, dann entscheiden; nicht "
    "unverändert wiederholen.",
)
ZU_GROSS = Code("zu_gross")
TYP_NICHT_ERLAUBT = Code("typ_nicht_erlaubt")
BEREICH_UNGUELTIG = Code("bereich_ungueltig")
EINGABE_UNGUELTIG = Code(
    "eingabe_ungueltig", "Eingabe gegen das Schema in der OpenAPI-Datei prüfen."
)
ZU_VIELE_ANFRAGEN = Code("zu_viele_anfragen", "Kurz warten, dann erneut versuchen.")
GESPERRT = Code(
    "gesperrt", "Nach zu vielen Fehlversuchen gesperrt: erst nach 15 Minuten erneut versuchen."
)
SERVERFEHLER = Code("serverfehler")
NICHT_BEREIT = Code("nicht_bereit", "Später erneut versuchen.")
FEHLER = Code("fehler")

NUR_LESEN = Code(
    "nur_lesen", "Für Änderungen ein Token mit Schreibrecht im Portal anlegen lassen."
)
NUR_IM_BROWSER = Code(
    "nur_im_browser",
    "Das entscheidet ein Mensch im Browser; dafür gibt es bewusst keinen Weg per Token.",
)
MENSCH_PRUEFT = Code(
    "mensch_prueft",
    "Vorschläge nimmt ein Mensch an oder verwirft sie (Liste „zu prüfen“ im Portal). Den "
    "Vorschlag dort vorlegen.",
)
NUR_VORSCHLAG = Code(
    "nur_vorschlag",
    "Einen neuen Vorschlag anlegen; gültige Einträge ändert ein Mensch.",
)
VERSION_FEHLT = Code("version_fehlt", "Den Eintrag lesen und dessen „version“ mitsenden.")
VORSCHLAG_OFFEN = Code(
    "vorschlag_offen", "Die Entscheidung über den offenen Vorschlag abwarten."
)
SCHON_ENTSCHIEDEN = Code("schon_entschieden", "Den Eintrag neu lesen; er hat schon einen Stand.")
STATUSWECHSEL_UNMOEGLICH = Code(
    "statuswechsel_unmoeglich", "Den aktuellen Status lesen und von dort aus wechseln."
)
UEBERLASTET = Code("ueberlastet", "Kurz warten, dann erneut versuchen.")
ZU_VIELE_AUFTRAEGE = Code(
    "zu_viele_auftraege", "Warten, bis ein laufender Auftrag fertig ist (Stand abfragen)."
)
NICHT_VERFUEGBAR = Code(
    "nicht_verfuegbar", "Diese Funktion ist auf dieser Instanz noch nicht freigeschaltet."
)
PASSWORT_NOETIG = Code("passwort_noetig", "Das Passwort des Links mitsenden.")
PASSWORT_FALSCH = Code("passwort_falsch")
ANMELDUNG_FEHLGESCHLAGEN = Code("anmeldung_fehlgeschlagen")
LINK_UNGUELTIG = Code("link_ungueltig", "Den Link bei der Person erfragen, die ihn geteilt hat.")
CSRF_FEHLT = Code(
    "csrf_fehlt",
    "Browser-Sitzungen senden den CSRF-Kopf mit; Skripte melden sich mit einem Token an.",
)
NEU_ANMELDEN = Code("neu_anmelden", "Erneut anmelden und den Vorgang wiederholen.")
BEFUND_IST_VORSCHLAG = Code(
    "befund_ist_vorschlag", "Erst nimmt ein Mensch den Vorschlag an, dann geht der Faden."
)

#: Standard-Code je HTTP-Status, wenn der Text keinen eigenen hat.
NACH_STATUS: dict[int, Code] = {
    400: ANFRAGE_UNGUELTIG,
    401: NICHT_ANGEMELDET,
    403: KEINE_BERECHTIGUNG,
    404: NICHT_GEFUNDEN,
    405: METHODE_NICHT_ERLAUBT,
    409: ZUSTAND,
    411: ANFRAGE_UNGUELTIG,
    413: ZU_GROSS,
    415: TYP_NICHT_ERLAUBT,
    416: BEREICH_UNGUELTIG,
    422: EINGABE_UNGUELTIG,
    429: ZU_VIELE_ANFRAGEN,
    500: SERVERFEHLER,
    503: NICHT_BEREIT,
}

#: Texte mit eingesetzten Werten: Ende → eigener Code (Anfänge: `_anfaenge`).
_ENDUNGEN: tuple[tuple[str, Code], ...] = (
    ("noch nicht verfügbar.", NICHT_VERFUEGBAR),
)


@cache
def _nach_text() -> dict[str, Code]:
    """Genaue Fehlertexte → eigener Code. Erst beim ersten Fehler gebaut: die Module importieren
    `web`, und `web` braucht dieses Modul — ein Import oben im Modul wäre ein Kreis."""
    from . import (
        faden,
        inhalte,
        routen_auswertungen,
        routen_auth,
        routen_teilen,
        routen_uploads,
        teilen,
        web,
        zugriff,
    )

    return {
        web.NUR_LESEN: NUR_LESEN,
        zugriff.NICHT_PER_TOKEN: NUR_IM_BROWSER,
        web._NICHT_PER_TOKEN: NUR_IM_BROWSER,  # Abschrift in web.py (Test: beide gleich)
        web.KONFLIKT: KONFLIKT,
        inhalte.ID_VERGEBEN: ID_VERGEBEN,
        routen_uploads.VERGEBEN: ID_VERGEBEN,
        faden.BEFUND_IST_VORSCHLAG: BEFUND_IST_VORSCHLAG,
        web.CSRF_FEHLT: CSRF_FEHLT,
        routen_auth.FALSCH: ANMELDUNG_FEHLGESCHLAGEN,
        routen_auth.PASSKEY_FEHLGESCHLAGEN: ANMELDUNG_FEHLGESCHLAGEN,
        routen_auth.GESPERRT: GESPERRT,
        routen_teilen.GESPERRT: GESPERRT,
        teilen.UNGUELTIG: LINK_UNGUELTIG,
        web.KI_PRUEFT_NICHT: MENSCH_PRUEFT,
        inhalte.KI_NUR_VORSCHLAG: NUR_VORSCHLAG,
        faden.VERSION_FEHLT: VERSION_FEHLT,
        faden.VORSCHLAG_DOPPELT: VORSCHLAG_OFFEN,
        faden.VORSCHLAG_ERLEDIGT: SCHON_ENTSCHIEDEN,
        faden.WECHSEL_UNMOEGLICH: STATUSWECHSEL_UNMOEGLICH,
        web.UEBERLASTET: UEBERLASTET,
        web.ZU_VIELE_TOKEN_ANFRAGEN: ZU_VIELE_ANFRAGEN,
        routen_uploads.ZU_VIELE: ZU_VIELE_AUFTRAEGE,
        routen_auswertungen.ZU_VIELE: ZU_VIELE_AUFTRAEGE,
        routen_teilen.PASSWORT_NOETIG: PASSWORT_NOETIG,
        routen_teilen.FALSCHES_PASSWORT: PASSWORT_FALSCH,
    }


@cache
def _anfaenge() -> tuple[tuple[str, Code], ...]:
    from . import web

    return ((web.NEU_ANMELDEN_PRAEFIX, NEU_ANMELDEN),)


def bestimme(status: int, text: str) -> Code:
    """Code einer Fehlerantwort: eigener Code des Texts, sonst der Standard des Status.
    Ein 404 bekommt immer `nicht_gefunden` (verrät nichts über Projekt oder Rolle)."""
    if status == 404:
        return NICHT_GEFUNDEN
    eigen = _nach_text().get(text)
    if eigen is None:
        eigen = next((c for endung, c in _ENDUNGEN if text.endswith(endung)), None)
    if eigen is None:
        eigen = next((c for anfang, c in _anfaenge() if text.startswith(anfang)), None)
    if eigen is not None:
        return eigen
    return NACH_STATUS.get(status, SERVERFEHLER if status >= 500 else FEHLER)


def alle() -> tuple[Code, ...]:
    """Alle Codes, die eine Antwort tragen kann (für Doku-Abgleich und OpenAPI)."""
    return tuple(sorted({c.name: c for c in (*globals().values(),) if isinstance(c, Code)}
                        .values(), key=lambda c: c.name))


def tabelle() -> str:
    """Markdown-Tabelle aller Codes für `docs/public/fehlercodes.md` (Drift-Test vergleicht)."""
    status = {c.name: s for s, c in sorted(NACH_STATUS.items(), reverse=True)}
    zeilen = ["| Code | Standard für HTTP | Hinweis in der Antwort |", "|---|---|---|"]
    zeilen += [f"| `{c.name}` | {status.get(c.name, '')} | {c.hinweis or ''} |" for c in alle()]
    return "\n".join(zeilen)
