"""Die Werkzeuge als einfache Funktionen über `api.Client` (siehe ADR 0008: von Hand).

Methode und Pfad jedes Werkzeugs stammen aus `endpunkte.json` — dieselbe Tabelle prüft der
Server-Test `server/tests/test_mcp_vertrag.py` gegen die echte OpenAPI. Jeder erwartbare Fehler
wird zu `ToolError` mit einem Text, den die KI verstehen und an den Menschen weitergeben kann.
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from importlib import resources
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.mcpserver.exceptions import ToolError

from .api import ApiFehler, Client
from .datenschutz import AUSGEBLENDET, ist_platzhalter

# Wie server/src/scanview_server/katalog.py (PROJECT_ID_RE) und inhalte.py (ID_RE, STATION_ID_RE).
PROJEKT_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
STATION_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,32}")
# Wie server/src/scanview_server/auftraege.py (AUFTRAG_ID_RE, UPLOAD_ID_RE, IMPORTE).
AUSWERTUNG_ID_RE = re.compile(r"[0-9a-f]{16}")
UPLOAD_ID_RE = re.compile(r"[0-9a-f]{32}")
IMPORTTYPEN = ("neu_mesh", "neu_splats")
IMPORT_OPTIONEN = ("projekt", "titel", "crs", "quellHoehe", "zielHoehe")

Messart = Literal["distance", "polyline", "area", "height"]
Status = Literal["offen", "in_arbeit", "erledigt", "abgeschlossen", "abgelehnt"]

KONFLIKT = "Jemand anderes hat inzwischen geändert – bitte neu lesen."  # noqa: RUF001
# beschreibung_setzen liest die Version selbst — ein erneuter Aufruf genügt.
KONFLIKT_ERNEUT = "Jemand anderes hat inzwischen geändert – bitte erneut aufrufen."  # noqa: RUF001


def _tabelle() -> dict[str, tuple[str, str]]:
    roh = json.loads(
        resources.files(__package__).joinpath("endpunkte.json").read_text(encoding="utf-8")
    )
    return {e["werkzeug"]: (e["methode"], e["pfad"]) for e in roh}


ENDPUNKTE = _tabelle()


PLATZHALTER = (f"„{AUSGEBLENDET}“ ist nur ein Platzhalter für einen ausgeblendeten Text, kein "
               "Inhalt – nicht zurückschreiben. Das Feld weglassen, um es unverändert zu lassen.")  # noqa: RUF001


def _kein_platzhalter(*texte: str | None) -> None:
    """Der datensparsame Modus zeigt Freitexte als Platzhalter; schriebe die KI ihn zurück, wäre
    der echte Text überschrieben."""
    if any(ist_platzhalter(t) for t in texte):
        raise ToolError(PLATZHALTER)


def _id(wert: str, muster: re.Pattern[str], was: str) -> str:
    if not isinstance(wert, str) or not muster.fullmatch(wert):
        raise ToolError(f"{was} ungültig: {wert!r}.")
    return quote(wert, safe="")


def _pfad(werkzeug: str, projekt_id: str | None = None, eintrag_id: str | None = None) -> str:
    _methode, vorlage = ENDPUNKTE[werkzeug]
    if "{pid}" in vorlage:
        vorlage = vorlage.replace("{pid}", _id(projekt_id or "", PROJEKT_ID_RE, "Projekt-ID"))
    if "{eid}" in vorlage:
        vorlage = vorlage.replace("{eid}", _id(eintrag_id or "", ID_RE, "Eintrags-ID"))
    if "{aid}" in vorlage:
        vorlage = vorlage.replace("{aid}", _id(eintrag_id or "", AUSWERTUNG_ID_RE,
                                              "Auswertungs-ID"))
    return vorlage


def _punkt(wert: Any, wo: str) -> list[float]:
    if (not isinstance(wert, list | tuple) or len(wert) != 3
            or not all(isinstance(v, int | float) and not isinstance(v, bool)
                       and math.isfinite(v) for v in wert)):
        raise ToolError(f"{wo} braucht genau drei endliche Zahlen [x, y, z] in Metern.")
    return [float(v) for v in wert]


def _meldung(e: ApiFehler, *, konflikt: str | None) -> str:
    s = e.status
    if s == 0:
        return e.text
    if 300 <= s < 400:
        return f"{e.text} (HTTP {s})"
    if s == 409:
        version = e.aktuell.get("version") if isinstance(e.aktuell, dict) else None
        # Ohne aktuellen Stand im Körper ist es kein Versionskonflikt (z. B. G7: Vorschlag schon
        # offen oder entschieden) — dann zählt der Text des Servers.
        if konflikt is not None and e.aktuell is not None:
            text = f"Konflikt (409): {konflikt}"
            if version is not None:
                text += f" Aktuelle Version: {version}."
            return text
        text = f"Konflikt (409): {e.text}"
        if e.aktuell is not None:
            text += f" Vorhandener Eintrag: {json.dumps(e.aktuell, ensure_ascii=False)}"
        return text
    if s == 401:
        return ("Nicht angemeldet (401): Token ungültig, abgelaufen oder widerrufen – im Portal "  # noqa: RUF001
                "unter Einstellungen → API-Zugänge einen neuen anlegen.")
    if s == 403:
        return (f"Keine Berechtigung (403): {e.text} Ein Lese-Token darf nichts ändern; Ändern, "
                "Export und Verlauf brauchen die Rolle Bearbeiter im Projekt; Rückmelder ändern "
                "nur eigene Rückmeldungen.")
    if s == 404:
        return "Nicht gefunden (404): Projekt oder Eintrag gibt es nicht, oder kein Zugriff darauf."
    if s == 422:
        return f"Eingabe ungültig (422): {e.text}"
    if s == 429:
        return f"Zu viele Anfragen (429): {e.text} Bitte eine Minute warten."
    return f"Serverfehler ({s}): {e.text}"


def _aufruf(c: Client, werkzeug: str, pfad: str, *, json_koerper: Any = None,
            konflikt: str | None = None, **params: Any) -> Any:
    methode = ENDPUNKTE[werkzeug][0]
    try:
        if methode == "GET":
            return c.get(pfad, **params)
        if methode == "POST":
            return c.post(pfad, json_koerper)
        if methode == "PATCH":
            return c.patch(pfad, json_koerper)
        return c.delete(pfad, json_koerper, **params)
    except ApiFehler as e:
        raise ToolError(_meldung(e, konflikt=konflikt)) from None


def projekte_auflisten(c: Client) -> Any:
    return _aufruf(c, "projekte_auflisten", _pfad("projekte_auflisten"))


def projekt_lesen(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "projekt_lesen", _pfad("projekt_lesen", projekt_id))


def beteiligte_lesen(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "beteiligte_lesen", _pfad("beteiligte_lesen", projekt_id))


def beschreibung_setzen(c: Client, projekt_id: str, beschreibung: str) -> Any:
    pfad = _pfad("beschreibung_setzen", projekt_id)
    _kein_platzhalter(beschreibung)
    karte = _aufruf(c, "projekt_lesen", _pfad("projekt_lesen", projekt_id))
    version = karte.get("version") if isinstance(karte, dict) else None
    if not isinstance(version, int):
        raise ToolError("Projekt ohne lesbare Version – bitte projekt_lesen prüfen.")  # noqa: RUF001
    return _aufruf(c, "beschreibung_setzen", pfad, konflikt=KONFLIKT_ERNEUT,
                   json_koerper={"beschreibung": beschreibung, "version": version})


def pois_lesen(c: Client, projekt_id: str) -> Any:
    # G7: offene KI-Vorschläge mitlesen (der Server zeigt fremde nur Rollen ab Bearbeiter).
    return _aufruf(c, "pois_lesen", _pfad("pois_lesen", projekt_id), vorschlaege=1)


def _poi_daten(titel: str | None, position: Any, kategorie: str | None, notiz: str | None,
               standpunkt: str | None) -> dict[str, Any]:
    _kein_platzhalter(notiz)
    daten: dict[str, Any] = {}
    if titel is not None:
        daten["title"] = titel
    if position is not None:
        daten["position"] = _punkt(position, "position")
    if kategorie is not None:
        daten["category"] = kategorie
    if notiz is not None:
        daten["note"] = notiz
    if standpunkt is not None:
        if not STATION_ID_RE.fullmatch(standpunkt):
            raise ToolError(f"Standpunkt ungültig: {standpunkt!r}.")
        daten["station"] = standpunkt
    return daten


def poi_anlegen(c: Client, projekt_id: str, titel: str, position: list[float],
                kategorie: str | None = None, notiz: str | None = None,
                standpunkt: str | None = None, poi_id: str | None = None,
                position_source: dict[str, str] | None = None) -> Any:
    pfad = _pfad("poi_anlegen", projekt_id)
    koerper: dict[str, Any] = {}
    if poi_id is not None:
        _id(poi_id, ID_RE, "POI-ID")
        koerper["id"] = poi_id
    koerper["daten"] = _poi_daten(titel, position, kategorie, notiz, standpunkt)
    if position_source is not None:
        koerper["daten"]["positionSource"] = position_source
    return _aufruf(c, "poi_anlegen", pfad, json_koerper=koerper)


def poi_aendern(c: Client, projekt_id: str, poi_id: str, version: int,
                titel: str | None = None, kategorie: str | None = None,
                notiz: str | None = None, position: list[float] | None = None,
                standpunkt: str | None = None, standpunkt_entfernen: bool = False,
                position_source: dict[str, str] | None = None) -> Any:
    pfad = _pfad("poi_aendern", projekt_id, poi_id)
    if standpunkt_entfernen and standpunkt is not None:
        raise ToolError("Entweder standpunkt setzen oder standpunkt_entfernen – nicht beides.")  # noqa: RUF001
    daten = _poi_daten(titel, position, kategorie, notiz, standpunkt)
    if position_source is not None:
        daten["positionSource"] = position_source
    if standpunkt_entfernen:
        daten["station"] = None  # der Server erlaubt station: null (inhalte.poi_pruefen)
    if not daten:
        raise ToolError("Keine Änderung angegeben – mindestens ein Feld setzen.")  # noqa: RUF001
    return _aufruf(c, "poi_aendern", pfad, konflikt=KONFLIKT,
                   json_koerper={"daten": daten, "version": version})


def messungen_lesen(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "messungen_lesen", _pfad("messungen_lesen", projekt_id), vorschlaege=1)


def messung_anlegen(c: Client, projekt_id: str, art: Messart, punkte: list[list[float]],
                    messung_id: str | None = None,
                    point_sources: list[dict[str, str] | None] | None = None) -> Any:
    pfad = _pfad("messung_anlegen", projekt_id)
    if not isinstance(punkte, list):
        raise ToolError("punkte muss eine Liste von [x, y, z] sein.")
    koerper: dict[str, Any] = {}
    if messung_id is not None:
        _id(messung_id, ID_RE, "Mess-ID")
        koerper["id"] = messung_id
    koerper["daten"] = {
        "kind": art,
        "points": [_punkt(p, f"Eckpunkt {i + 1}") for i, p in enumerate(punkte)],
    }
    if point_sources is not None:
        koerper["daten"]["pointSources"] = point_sources
    return _aufruf(c, "messung_anlegen", pfad, json_koerper=koerper)


def protokoll_lesen(c: Client, projekt_id: str, vor: int | None = None,
                    limit: int | None = None) -> Any:
    return _aufruf(c, "protokoll_lesen", _pfad("protokoll_lesen", projekt_id),
                   vor=vor, limit=limit)


def export(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "export", _pfad("export", projekt_id))


def faden_lesen(c: Client, projekt_id: str, poi_id: str) -> Any:
    return _aufruf(c, "faden_lesen", _pfad("faden_lesen", projekt_id, poi_id))


def kommentar_anlegen(c: Client, projekt_id: str, poi_id: str, text: str,
                      intern: bool = False) -> Any:
    pfad = _pfad("kommentar_anlegen", projekt_id, poi_id)
    _kein_platzhalter(text)
    return _aufruf(c, "kommentar_anlegen", pfad,
                   json_koerper={"art": "kommentar", "text": text, "intern": intern})


def status_setzen(c: Client, projekt_id: str, poi_id: str, status: Status, version: int,
                  begruendung: str | None = None) -> Any:
    pfad = _pfad("status_setzen", projekt_id, poi_id)
    _kein_platzhalter(begruendung)
    koerper: dict[str, Any] = {"art": "status", "wert": status, "version": version}
    if begruendung is not None:
        koerper["text"] = begruendung
    return _aufruf(c, "status_setzen", pfad, konflikt=KONFLIKT, json_koerper=koerper)


def _loeschen(c: Client, werkzeug: str, projekt_id: str, eintrag_id: str, version: int) -> Any:
    pfad = _pfad(werkzeug, projekt_id, eintrag_id)
    antwort = _aufruf(c, werkzeug, pfad, konflikt=KONFLIKT, version=version)
    if antwort is not None:
        # 202 mit Eintrag (G7): über einen Token für KI-Agenten nur zum Löschen vorgeschlagen;
        # ein Mensch entscheidet. Nie als „gelöscht“ melden.
        return {"loeschenVorgeschlagen": eintrag_id, "eintrag": antwort}
    # 204 ohne Körper: das Ergebnis bestätigen; Wiederherstellen bleibt im Protokoll möglich.
    return {"geloescht": eintrag_id}


def poi_loeschen(c: Client, projekt_id: str, poi_id: str, version: int) -> Any:
    return _loeschen(c, "poi_loeschen", projekt_id, poi_id, version)


def messung_loeschen(c: Client, projekt_id: str, messung_id: str, version: int) -> Any:
    return _loeschen(c, "messung_loeschen", projekt_id, messung_id, version)


def link_anlegen(c: Client, projekt_id: str, poi_id: str, url: str, titel: str,
                 intern: bool = False) -> Any:
    pfad = _pfad("link_anlegen", projekt_id, poi_id)
    _kein_platzhalter(url, titel)
    return _aufruf(c, "link_anlegen", pfad, json_koerper={
        "art": "link", "wert": {"url": url, "titel": titel}, "intern": intern})


def zustaendig_setzen(c: Client, projekt_id: str, poi_id: str, version: int,
                      gewerk_id: int | None, nutzer_id: int | None = None) -> Any:
    pfad = _pfad("zustaendig_setzen", projekt_id, poi_id)
    if gewerk_id is None and nutzer_id is not None:
        raise ToolError("Eine Person nur zusammen mit ihrem Gewerk (gewerk_id) angeben.")
    wert = None if gewerk_id is None else {"gewerk_id": gewerk_id, "nutzer_id": nutzer_id}
    return _aufruf(c, "zustaendig_setzen", pfad, konflikt=KONFLIKT,
                   json_koerper={"art": "zustaendig", "wert": wert, "version": version})


def frist_setzen(c: Client, projekt_id: str, poi_id: str, version: int,
                 frist: str | None) -> Any:
    pfad = _pfad("frist_setzen", projekt_id, poi_id)
    return _aufruf(c, "frist_setzen", pfad, konflikt=KONFLIKT,
                   json_koerper={"art": "frist", "wert": frist, "version": version})


def paket_lesen(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "paket_lesen", _pfad("paket_lesen", projekt_id))


def gewerke_lesen(c: Client, projekt_id: str) -> Any:
    return _aufruf(c, "gewerke_lesen", _pfad("gewerke_lesen", projekt_id))


def auswertung_starten(c: Client, projekt_id: str, typ: str,
                       optionen: dict[str, Any] | None = None) -> Any:
    """Reiht eine Auswertung ein; Typ und Optionen prüft der Server gegen seine feste Liste."""
    pfad = _pfad("auswertung_starten", projekt_id)
    if not isinstance(typ, str) or not re.fullmatch(r"[a-z_]{1,40}", typ):
        raise ToolError(f"Auswertungstyp ungültig: {typ!r}.")
    if optionen is not None and not isinstance(optionen, dict):
        raise ToolError("optionen muss ein Objekt sein, z. B. {'profil': 'bestandserfassung'}.")
    return _aufruf(c, "auswertung_starten", pfad,
                   json_koerper={"typ": typ, "optionen": optionen or {}})


def auswertung_status(c: Client, projekt_id: str, auswertung_id: str | None = None) -> Any:
    """Alle sichtbaren Auswertungen des Projekts, oder nur die eine (aus derselben Liste)."""
    pfad = _pfad("auswertung_status", projekt_id)
    if auswertung_id is not None:
        _id(auswertung_id, AUSWERTUNG_ID_RE, "Auswertungs-ID")
    antwort = _aufruf(c, "auswertung_status", pfad)
    if auswertung_id is None or not isinstance(antwort, dict):
        return antwort
    treffer = [a for a in antwort.get("auswertungen", [])
               if isinstance(a, dict) and a.get("id") == auswertung_id]
    if not treffer:
        raise ToolError("Nicht gefunden (404): Auswertung gibt es nicht, oder kein Zugriff "
                        "darauf.")
    return {**treffer[0], "worker": antwort.get("worker")}


# Aus ergebnis.json nur das Urteil je Anforderung — keine Prüfsummenlisten der Eingänge, keine
# Messwerttabellen, keine Karten (nie Rohdaten).
_ZEILE = ("id", "anforderung", "pruefart", "parameter", "urteil", "grund", "belege")


def auswertung_lesen(c: Client, projekt_id: str, auswertung_id: str) -> Any:
    pfad = _pfad("auswertung_lesen", projekt_id, auswertung_id)
    e = _aufruf(c, "auswertung_lesen", pfad)
    if not isinstance(e, dict):
        raise ToolError("Unerwartete Antwort vom Server (kein JSON-Objekt).")
    roh_zeilen, roh_katalog = e.get("zeilen"), e.get("katalog")
    zeilen: list[Any] = roh_zeilen if isinstance(roh_zeilen, list) else []
    katalog: dict[str, Any] = roh_katalog if isinstance(roh_katalog, dict) else {}
    return {
        "katalog": {k: katalog.get(k) for k in ("titel", "sha256")},
        "gesamt": e.get("gesamt"),
        "zeilen": [{k: z.get(k) for k in _ZEILE} for z in zeilen if isinstance(z, dict)],
    }


def import_starten(c: Client, upload_id: str, auftragstyp: str,
                   optionen: dict[str, Any]) -> Any:
    """Neues Projekt aus einem fertig hochgeladenen Upload. Hochladen selbst geht nicht über das
    Sprachmodell (große Dateien): das tut ein Mensch im Portal oder ein Skript über die API."""
    _id(upload_id, UPLOAD_ID_RE, "Upload-ID")
    if auftragstyp not in IMPORTTYPEN:
        raise ToolError(f"Importtyp ungültig: {auftragstyp!r}. Erlaubt: "
                        f"{', '.join(IMPORTTYPEN)}.")
    if not isinstance(optionen, dict) or set(optionen) - set(IMPORT_OPTIONEN) or not all(
            isinstance(v, str) for v in optionen.values()):
        raise ToolError("optionen: nur Texte für " + ", ".join(IMPORT_OPTIONEN) + "; "
                        "projekt ist Pflicht.")
    if "projekt" not in optionen:
        raise ToolError("optionen.projekt fehlt (ID des neuen Projekts).")
    _id(optionen["projekt"], PROJEKT_ID_RE, "Projekt-ID")
    return _aufruf(c, "import_starten", _pfad("import_starten"),
                   json_koerper={"upload": upload_id, "typ": auftragstyp, "optionen": optionen})


def import_status(c: Client, projekt_id: str | None = None) -> Any:
    """Alle Importe der Instanz, oder nur die für ein (neues) Projekt."""
    if projekt_id is not None:
        _id(projekt_id, PROJEKT_ID_RE, "Projekt-ID")
    antwort = _aufruf(c, "import_status", _pfad("import_status"))
    if projekt_id is None or not isinstance(antwort, dict):
        return antwort
    return {"importe": [a for a in antwort.get("importe", [])
                        if isinstance(a, dict) and a.get("projekt") == projekt_id],
            "worker": antwort.get("worker")}


WERKZEUGE: dict[str, Callable[..., Any]] = {
    "projekte_auflisten": projekte_auflisten,
    "projekt_lesen": projekt_lesen,
    "beteiligte_lesen": beteiligte_lesen,
    "beschreibung_setzen": beschreibung_setzen,
    "pois_lesen": pois_lesen,
    "poi_anlegen": poi_anlegen,
    "poi_aendern": poi_aendern,
    "messungen_lesen": messungen_lesen,
    "messung_anlegen": messung_anlegen,
    "protokoll_lesen": protokoll_lesen,
    "export": export,
    "faden_lesen": faden_lesen,
    "poi_loeschen": poi_loeschen,
    "messung_loeschen": messung_loeschen,
    "link_anlegen": link_anlegen,
    "zustaendig_setzen": zustaendig_setzen,
    "frist_setzen": frist_setzen,
    "gewerke_lesen": gewerke_lesen,
    "paket_lesen": paket_lesen,
    "kommentar_anlegen": kommentar_anlegen,
    "status_setzen": status_setzen,
    "auswertung_starten": auswertung_starten,
    "auswertung_status": auswertung_status,
    "auswertung_lesen": auswertung_lesen,
    "import_starten": import_starten,
    "import_status": import_status,
}
