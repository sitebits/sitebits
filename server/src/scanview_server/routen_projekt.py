"""Projektverwaltung unter /api/v1/projekte/{pid}/…: Beteiligte, Gewerke, projektgebundene
Einladungen, Kategorien, Schalter und Vorlage.

Schreiben und das Lesen offener Einladungen nur für Projektleitung und Admin per Browser-Sitzung
mit Herkunftsprüfung (zugriff.leitung), nie per API-Token. Beteiligte und Gewerke lesen alle
Projektbeteiligten, auch per Token — E-Mail, letzte Anmeldung und Sperre nur Leitung/Admin im
Browser. Die Projektleitung vergibt nur Betrachter, Rückmelder und Bearbeiter; die Prüfung gegen
den Datenbankstand läuft in verwaltung.py in derselben Transaktion wie das Schreiben."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import konten, lesezugriff, projektverwaltung, verwaltung
from .tokens import hash_token
from .web import SQLITE_GANZZAHL_MAX, Ungueltig, api_token, csrf, db
from .zugriff import NICHT_GEFUNDEN, leitung, projekt

router = APIRouter(prefix="/api/v1/projekte/{pid}")
Id = Annotated[int, Path(ge=1, le=SQLITE_GANZZAHL_MAX)]
GewerkId = Annotated[int, Field(ge=1, le=SQLITE_GANZZAHL_MAX)]


def _nicht_gefunden() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)


def _verboten(e: Exception) -> StarletteHTTPException:
    return StarletteHTTPException(status_code=403, detail=str(e))


class BeteiligterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Das Literal kennt auch "projektleitung": eine Projektleitung, die sie vergeben will, bekommt
    # so 403 aus der Rechteprüfung statt eines Formatfehlers.
    rolle: konten.RolleWert | None
    gewerk: GewerkId | None


class GewerkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=projektverwaltung.GEWERK_NAME_MAX)
    kurz: str = Field(min_length=1, max_length=projektverwaltung.GEWERK_KURZ_MAX)
    bauherrenseite: StrictBool = False


class GewerkAenderungIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1,
                             max_length=projektverwaltung.GEWERK_NAME_MAX)
    kurz: str | None = Field(default=None, min_length=1,
                             max_length=projektverwaltung.GEWERK_KURZ_MAX)
    bauherrenseite: StrictBool | None = None


@router.get("/beteiligte", summary="Beteiligte des Projekts mit Rolle und Gewerk (E-Mail, letzte "
            "Anmeldung und Sperre nur für Projektleitung/Admin im Browser)")
def beteiligte(pid: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid)
    voll = konten.mindestens(z.rolle, "projektleitung") and api_token(request) is None
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "beteiligte")
    je_id = {g.id: g for g in projektverwaltung.gewerke(conn, pid)}
    return JSONResponse({"beteiligte": [
        projektverwaltung.beteiligter_json(b, je_id, voll=voll)
        for b in projektverwaltung.beteiligte(conn, pid)
    ]}, headers={"Cache-Control": "no-store"})


@router.put("/beteiligte/{uid}", dependencies=[Depends(csrf)],
            summary="Rolle und Gewerk eines Beteiligten setzen oder die Rolle entziehen "
            "(Projektleitung: nur Betrachter, Rückmelder, Bearbeiter)")
def beteiligter_setzen(pid: str, uid: Id, body: BeteiligterIn, request: Request,
                       conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    try:
        alt = verwaltung.beteiligter_setzen(
            conn, uid, pid, body.rolle, body.gewerk, als_leitung=z.rolle != "admin",
            von=z.nutzer.id, kanal="web")
    except LookupError:
        raise _nicht_gefunden() from None
    except verwaltung.Verboten as e:
        raise _verboten(e) from e
    except ValueError as e:  # GewerkUnbekannt, KontoAnonymisiert
        raise Ungueltig(str(e)) from e
    return JSONResponse({"vorher": alt, "rolle": body.rolle,
                         "gewerk": body.gewerk if body.rolle is not None else None})


@router.get("/gewerke", summary="Gewerke des Projekts")
def gewerke_lesen(pid: str, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "gewerke")
    return JSONResponse({"gewerke": [g.als_json() for g in projektverwaltung.gewerke(conn, pid)]})


@router.post("/gewerke", status_code=201, dependencies=[Depends(csrf)],
             summary="Gewerk anlegen (Projektleitung/Admin)")
def gewerk_anlegen(pid: str, body: GewerkIn, request: Request,
                   conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    g = projektverwaltung.gewerk_anlegen(conn, pid, name=body.name, kurz=body.kurz,
                                         bauherrenseite=body.bauherrenseite, von=z.nutzer.id)
    return JSONResponse(g.als_json(), status_code=201)


@router.patch("/gewerke/{gid}", dependencies=[Depends(csrf)],
              summary="Gewerk ändern: Name, Kurzzeichen, Bauherrenseite (Projektleitung/Admin)")
def gewerk_aendern(pid: str, gid: Id, body: GewerkAenderungIn, request: Request,
                   conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    if not body.model_fields_set:
        raise Ungueltig("Name, Kurzzeichen oder Bauherrenseite angeben.")
    try:
        g = projektverwaltung.gewerk_aendern(conn, pid, gid, name=body.name, kurz=body.kurz,
                                             bauherrenseite=body.bauherrenseite, von=z.nutzer.id)
    except LookupError:
        raise _nicht_gefunden() from None
    return JSONResponse(g.als_json())


@router.delete("/gewerke/{gid}", status_code=204, dependencies=[Depends(csrf)],
               summary="Gewerk löschen; Beteiligte sind danach ohne Gewerk; 409, solange es für "
               "nicht abgeschlossene Befunde zuständig ist (Projektleitung/Admin)")
def gewerk_loeschen(pid: str, gid: Id, request: Request,
                    conn: sqlite3.Connection = Depends(db)) -> Response:
    z = leitung(request, conn, pid)
    try:
        projektverwaltung.gewerk_loeschen(conn, pid, gid, von=z.nutzer.id)
    except LookupError:
        raise _nicht_gefunden() from None
    except projektverwaltung.GewerkBelegt as e:
        raise StarletteHTTPException(status_code=409, detail=str(e)) from e
    return Response(status_code=204)


_EID_RE = re.compile(r"[0-9a-f]{16}")
_OHNE_SPEICHERN = {"Cache-Control": "no-store"}


class ProjektEinladungIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    rolle: konten.RolleWert
    gewerk: GewerkId | None = None


def _projekt_einladung_json(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": verwaltung.einladung_id(r["token_hash"]), "name": r["name"], "email": r["email"],
        "rolle": json.loads(r["grants"]).get(r["project_id"]), "gewerk": r["gewerk_id"],
        "erstelltAm": r["created_at"], "ablauf": r["expires_at"],
    }


@router.get("/einladungen", summary="Offene Einladungen dieses Projekts (Projektleitung/Admin)")
def einladungen(pid: str, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "einladungen")
    rows = conn.execute(
        "SELECT * FROM invites WHERE project_id=? AND used_at IS NULL AND expires_at > ?"
        " ORDER BY created_at DESC", (pid, int(time.time()))).fetchall()
    return JSONResponse({"einladungen": [_projekt_einladung_json(r) for r in rows]},
                        headers=_OHNE_SPEICHERN)


@router.post("/einladungen", dependencies=[Depends(csrf)],
             summary="In dieses Projekt einladen: neue E-Mail → Einladungslink (nur in dieser "
             "Antwort); bestehendes Konto → Zugriff direkt, ohne Zurücksetzen")
def einladen(pid: str, body: ProjektEinladungIn, request: Request,
             conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    als_leitung = z.rolle != "admin"
    if als_leitung and body.rolle not in konten.LEITUNG_VERGIBT:
        raise _verboten(verwaltung.Verboten(verwaltung.LEITUNG_ROLLE_VERBOTEN))
    # Vorprüfung für eine schnelle Antwort; maßgeblich ist die Prüfung in der Schreibtransaktion.
    if body.gewerk is not None and projektverwaltung.gewerk(conn, pid, body.gewerk) is None:
        raise Ungueltig(verwaltung.GEWERK_UNBEKANNT)
    vorhanden = konten.konto_id(conn, body.email)
    if vorhanden is not None:
        try:
            e = verwaltung.zugriff_erteilen(conn, vorhanden, pid, body.rolle, body.gewerk,
                                            als_leitung=als_leitung, von=z.nutzer.id, kanal="web")
        except (verwaltung.KontoAnonymisiert, verwaltung.GewerkUnbekannt) as ex:
            raise Ungueltig(str(ex)) from ex
        return JSONResponse({"ergebnis": e.art, "gesperrt": e.gesperrt,
                             "text": verwaltung.erteilt_text(e)}, headers=_OHNE_SPEICHERN)
    try:
        t = verwaltung.einladen(conn, email=body.email, name=body.name, grants={pid: body.rolle},
                                project_id=pid, gewerk_id=body.gewerk, von=z.nutzer.id, kanal="web")
    except ValueError as ex:
        raise Ungueltig(str(ex)) from ex
    row = conn.execute("SELECT * FROM invites WHERE token_hash=?", (hash_token(t),)).fetchone()
    return JSONResponse(
        {"ergebnis": "eingeladen", "link": f"{request.app.state.settings.origin}/einladung#t={t}",
         "einladung": _projekt_einladung_json(row)}, status_code=201, headers=_OHNE_SPEICHERN)


@router.post("/einladungen/{eid}/widerrufen", status_code=204, dependencies=[Depends(csrf)],
             summary="Offene Einladung dieses Projekts widerrufen (Projektleitung/Admin)")
def einladung_widerrufen(pid: str, eid: str, request: Request,
                         conn: sqlite3.Connection = Depends(db)) -> Response:
    z = leitung(request, conn, pid)
    if not _EID_RE.fullmatch(eid) or not verwaltung.einladung_widerrufen(
        conn, eid, von=z.nutzer.id, kanal="web", nur_projekt=pid,
    ):
        raise _nicht_gefunden()
    return Response(status_code=204)


class EinstellungenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rueckmeldungen: StrictBool | None = None
    export_bearbeiter: StrictBool | None = Field(default=None, alias="exportBearbeiter")
    befund_faeden: StrictBool | None = Field(default=None, alias="befundFaeden")
    nur_lese_links: StrictBool | None = Field(default=None, alias="nurLeseLinks")


class KategorieIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=projektverwaltung.KATEGORIE_MAX)
    aktiv: StrictBool = True


class KategorienIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kategorien: list[KategorieIn] = Field(max_length=projektverwaltung.KATEGORIEN_MAX + 1)


class EinrichtenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vorlage: Literal["bestandsaufnahme", "bauprojekt"]


NUR_ADMIN_VORLAGE = "Die Vorlage wählt die Instanzverwaltung beim Einrichten des Projekts."


@router.patch("/einstellungen", dependencies=[Depends(csrf)],
              summary="Schalter: Rückmeldungen erlaubt, Export für Bearbeiter, Befund-Fäden, "
              "Nur-Lese-Links (Projektleitung/Admin)")
def einstellungen_aendern(pid: str, body: EinstellungenIn, request: Request,
                          conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    if not body.model_fields_set:
        raise Ungueltig("Rückmeldungen, Export, Befund-Fäden oder Nur-Lese-Links angeben.")
    e = projektverwaltung.einstellungen_aendern(
        conn, pid, rueckmeldungen=body.rueckmeldungen, export_bearbeiter=body.export_bearbeiter,
        befund_faeden=body.befund_faeden, nur_lese_links=body.nur_lese_links, von=z.nutzer.id)
    return JSONResponse({"einstellungen": e.als_json()})


@router.put("/kategorien", dependencies=[Depends(csrf)],
            summary="Kategorien des Projekts: ganze geordnete Liste (anhängen, ausschalten, "
            "ordnen; nicht entfernen)")
def kategorien_setzen(pid: str, body: KategorienIn, request: Request,
                      conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    neu = projektverwaltung.kategorien_setzen(
        conn, pid, [(k.name, k.aktiv) for k in body.kategorien], von=z.nutzer.id)
    return JSONResponse({"kategorien": [k.als_json() for k in neu]})


@router.post("/einrichten", dependencies=[Depends(csrf)],
             summary="Projekt mit Vorlage einrichten (nur Admin, nur einmal)")
def einrichten(pid: str, body: EinrichtenIn, request: Request,
               conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    if z.rolle != "admin":
        raise StarletteHTTPException(status_code=403, detail=NUR_ADMIN_VORLAGE)
    try:
        e = projektverwaltung.einrichten(conn, pid, body.vorlage, von=z.nutzer.id)
    except projektverwaltung.SchonEingerichtet as ex:
        raise StarletteHTTPException(status_code=409, detail=str(ex)) from ex
    return JSONResponse({"einstellungen": e.als_json(),
                         "kategorien": [
                             k.als_json() for k in projektverwaltung.kategorien(conn, pid)]})
