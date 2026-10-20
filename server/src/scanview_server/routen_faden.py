"""/api/v1/projekte/{pid}/pois/{eid}/faden und /befunde: Befund-Fäden lesen und schreiben.

Lesen alle mit Recht im Projekt (interne Einträge nur die Bauherrenseite, nie per Lese-Token),
schreiben ab Rückmelder mit CSRF bzw. Schreib-Token; was genau, entscheidet `faden.rechte`.
Projekt-Schalter „Befund-Fäden“ aus → 404 auf allen Faden-Wegen. Vorrang 401 → 404 (fremdes
Projekt oder Schalter aus) → 403 → 409 → 422: Körper und Filter prüft die Route deshalb selbst,
erst nach der Projektprüfung (FastAPI würde sonst vorher mit 422 antworten). Einträge heißen nach
außen nur über ihren zufälligen `schluessel`.

KI-Vorschläge (G7): Status, Zuständigkeit oder Frist über einen Agent-Token antworten mit 202 und
einem Eintrag `vorschlag`; annehmen/verwerfen nur durch Menschen (403 KI_PRUEFT_NICHT)."""

from __future__ import annotations

import sqlite3
import unicodedata
from typing import Annotated, Any, Literal, get_args

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import faden, inhalte, lesezugriff
from .web import (
    KI_PRUEFT_NICHT,
    SQLITE_GANZZAHL_MAX,
    Ungueltig,
    api_token,
    csrf,
    db,
    kanal,
    ki,
    token_name,
)
from .zugriff import NICHT_GEFUNDEN, Zugriff, projekt

router = APIRouter(prefix="/api/v1/projekte/{pid}")
NUR_BETEILIGTE = "Schreiben im Faden ist Rückmeldern und Bearbeitern vorbehalten."
EINGABE_UNGUELTIG = "Eingabe ungültig."
Status = Literal["offen", "in_arbeit", "erledigt", "abgeschlossen", "abgelehnt"]
Zustaendig = Literal["alle", "gewerk", "ich"]
Frist = Literal["ueberfaellig", "woche", "ohne"]


class EintragIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    art: str = Field(max_length=20)
    # Ohne max_length: Länge und Kodierung prüft faden (sonst 422 ohne deutsche Meldung).
    text: str | None = None
    wert: Any = None
    intern: StrictBool = False
    version: int | None = Field(default=None, ge=1, le=SQLITE_GANZZAHL_MAX)


class AnnehmenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=SQLITE_GANZZAHL_MAX)


class VerwerfenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = None


def sicht(request: Request, conn: sqlite3.Connection, z: Zugriff) -> faden.Sicht:
    """Sicht der Anfrage (Lese-Token sieht nie interne Einträge); auch für Export und Verlauf."""
    t = api_token(request)
    return faden.sicht(conn, z.karte.id, z.nutzer.id, z.rolle,
                       lese_token=t is not None and t.umfang == "lesen")


def _poi_id(eid: str) -> str:
    if not inhalte.ID_RE.fullmatch(eid):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return eid


def _nicht_gefunden() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)


def _zugriff(request: Request, conn: sqlite3.Connection, pid: str, *,
             schreiben: bool = False) -> Zugriff:
    """Projektprüfung mit Schalter vor der Rolle: ein 403 (Rolle, Lese-Token) kommt erst, wenn
    das Projekt Fäden hat — bei ausgeschaltetem Schalter antwortet jeder Faden-Weg mit 404."""
    try:
        z = (projekt(request, conn, pid, mindestens="bearbeiter", rueckmelder=True,
                     text=NUR_BETEILIGTE) if schreiben else projekt(request, conn, pid))
    except StarletteHTTPException as e:
        # 403 gibt es erst nach bestandener 404-Prüfung des Projekts: kein Existenz-Leck.
        if e.status_code == 403 and not faden.aktiviert(conn, pid):
            raise _nicht_gefunden() from None
        raise
    if not faden.aktiviert(conn, pid):
        raise _nicht_gefunden()
    return z


def _auswahl(wert: str | None, erlaubt: Any) -> Any:
    if wert is not None and wert not in get_args(erlaubt):
        raise Ungueltig(EINGABE_UNGUELTIG)
    return wert


@router.get("/pois/{eid}/faden", operation_id="faden_lesen",
            summary="Befund-Faden: Stand, sichtbare Einträge und eigene Rechte")
def faden_lesen(pid: str, eid: str, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = _zugriff(request, conn, pid)
    antwort = faden.faden(conn, pid, _poi_id(eid), sicht(request, conn, z))
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "faden")
    return JSONResponse(antwort)


@router.post("/pois/{eid}/faden", status_code=201, dependencies=[Depends(csrf)],
             operation_id="faden_eintrag_anlegen",
             summary="Eintrag im Befund-Faden (kommentar, link, status, zustaendig, frist; "
             "version Pflicht bei status/zustaendig/frist)",
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": EintragIn.model_json_schema()}}}})
def eintrag_anlegen(pid: str, eid: str, request: Request,
                    roh: Annotated[Any, Body()] = None,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = _zugriff(request, conn, pid, schreiben=True)
    poi = _poi_id(eid)
    try:
        body = EintragIn.model_validate(roh)
    except ValidationError:
        raise Ungueltig(EINGABE_UNGUELTIG) from None
    e, st = faden.anlegen(conn, pid, poi, sicht(request, conn, z), art=body.art,
                          text=body.text, wert=body.wert, intern=body.intern,
                          version=body.version, kanal=kanal(request),
                          token_name=token_name(request), ki=ki(request))
    return JSONResponse({"eintrag": e, "stand": st},
                        status_code=202 if e["art"] == "vorschlag" else 201)


def _pruefer(request: Request, conn: sqlite3.Connection, pid: str, eid: str,
             schluessel: str) -> tuple[Zugriff, str]:
    """Vorrang für annehmen/verwerfen: Projekt (404/403), Agent-Token (403), Pfad (404)."""
    z = _zugriff(request, conn, pid, schreiben=True)
    if ki(request):
        raise StarletteHTTPException(status_code=403, detail=KI_PRUEFT_NICHT)
    if not faden.schluessel_gueltig(schluessel):
        raise _nicht_gefunden()
    return z, _poi_id(eid)


def _koerper(modell: type[BaseModel], roh: Any) -> Any:
    try:
        return modell.model_validate(roh if roh is not None else {})
    except ValidationError:
        raise Ungueltig(EINGABE_UNGUELTIG) from None


@router.post("/pois/{eid}/faden/{schluessel}/annehmen", status_code=201,
             dependencies=[Depends(csrf)], operation_id="faden_vorschlag_annehmen",
             summary="KI-Vorschlag im Faden annehmen (setzt den Stand; version = aktueller Stand;"
             " nicht per KI-Agent)",
             openapi_extra={"requestBody": {"required": True, "content": {
                 "application/json": {"schema": AnnehmenIn.model_json_schema()}}}})
def vorschlag_annehmen(pid: str, eid: str, schluessel: str, request: Request,
                       roh: Annotated[Any, Body()] = None,
                       conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z, poi = _pruefer(request, conn, pid, eid, schluessel)
    body = _koerper(AnnehmenIn, roh)
    e, st = faden.vorschlag_annehmen(conn, pid, poi, schluessel, sicht(request, conn, z),
                                     version=body.version, kanal=kanal(request),
                                     token_name=token_name(request))
    return JSONResponse({"eintrag": e, "stand": st}, status_code=201)


@router.post("/pois/{eid}/faden/{schluessel}/verwerfen", status_code=201,
             dependencies=[Depends(csrf)], operation_id="faden_vorschlag_verwerfen",
             summary="KI-Vorschlag im Faden verwerfen (Kommentar, Text optional; nicht per"
             " KI-Agent)",
             openapi_extra={"requestBody": {"required": False, "content": {
                 "application/json": {"schema": VerwerfenIn.model_json_schema()}}}})
def vorschlag_verwerfen(pid: str, eid: str, schluessel: str, request: Request,
                        roh: Annotated[Any, Body()] = None,
                        conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z, poi = _pruefer(request, conn, pid, eid, schluessel)
    body = _koerper(VerwerfenIn, roh)
    e = faden.vorschlag_verwerfen(conn, pid, poi, schluessel, sicht(request, conn, z),
                                  text=body.text, kanal=kanal(request),
                                  token_name=token_name(request))
    return JSONResponse({"eintrag": e}, status_code=201)


@router.post("/pois/{eid}/faden/{schluessel}/zurueckziehen", dependencies=[Depends(csrf)],
             operation_id="faden_eintrag_zurueckziehen",
             summary="Eigenen Kommentar oder Link zurückziehen (Text für alle ersetzt)")
def eintrag_zurueckziehen(pid: str, eid: str, schluessel: str, request: Request,
                          conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = _zugriff(request, conn, pid, schreiben=True)
    if not faden.schluessel_gueltig(schluessel):
        raise _nicht_gefunden()
    e = faden.zurueckziehen(conn, pid, _poi_id(eid), schluessel, sicht(request, conn, z),
                            kanal=kanal(request), token_name=token_name(request))
    return JSONResponse({"eintrag": e})


@router.get("/befunde", operation_id="befunde_lesen",
            summary="Liste „Fällig/überfällig“: Befunde mit Stand, überfällig zuerst "
            "(status: offen|in_arbeit|erledigt|abgeschlossen|abgelehnt; zustaendig: "
            "alle|gewerk|ich; frist: ueberfaellig|woche|ohne)")
def befunde_lesen(
    pid: str, request: Request, status: str | None = None, zustaendig: str = "alle",
    frist: str | None = None, kategorie: str | None = None,
    conn: sqlite3.Connection = Depends(db),
) -> JSONResponse:
    z = _zugriff(request, conn, pid)
    if kategorie is not None:
        kategorie = unicodedata.normalize("NFC", kategorie).strip()
        if len(kategorie) > inhalte.POI_LIMITS["category"]:
            raise Ungueltig(EINGABE_UNGUELTIG)
    f = faden.Filter(status=_auswahl(status, Status), zustaendig=_auswahl(zustaendig, Zustaendig),
                     frist=_auswahl(frist, Frist), kategorie=kategorie)
    antwort = faden.befunde(conn, pid, sicht(request, conn, z), f, faden.heute(),
                            inhalte.liste(conn, "pois", pid))
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "befunde")
    return JSONResponse(antwort)
