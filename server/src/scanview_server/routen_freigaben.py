"""Freigabe von Panoramen und Fotos (F20) unter /api/v1/projekte/{pid}/freigaben.

Lesen: Bearbeiter, Projektleitung, Admin — auch per Token und MCP (Agenten sehen den Stand, z. B.
„3 freigegebene Dateien passen nicht mehr“). Freigeben und Zurücknehmen nur Projektleitung/Admin
per Browser-Sitzung (`zugriff.leitung`): Die Freigabe macht Personenbilder für Betrachter
sichtbar — die KI schlägt vor, ein Mensch entscheidet. „personenfrei“ nur Admin. Fachlogik in
`freigabe.py`."""

from __future__ import annotations

import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import freigabe, lesezugriff, projektverwaltung
from .web import csrf, db
from .zugriff import leitung, projekt

router = APIRouter(prefix="/api/v1/projekte/{pid}/freigaben")
Ebene = Literal["panos", "fotos"]


class FreigabeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    art: Literal["anonymisiert", "personenfrei"]


def _konflikt(e: freigabe.FreigabeFehler) -> StarletteHTTPException:
    return StarletteHTTPException(status_code=409, detail=str(e))


@router.get("", operation_id="freigaben_lesen",
            summary="Stand der Freigaben (Panoramen, Fotos): Anonymisierung im Paket, aktive "
            "Freigabe; mit pruefen=true zusätzlich abweichende Dateien (hasht alle, "
            "Sekunden je GB) (Bearbeiter+)")
def freigaben_lesen(pid: str, request: Request, pruefen: bool = False,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid, mindestens="bearbeiter")
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "freigaben")
    paket = request.app.state.katalog.data_dir / pid
    nur = projektverwaltung.einstellungen(conn, pid).panoramen_nur_freigegeben
    return JSONResponse({"ebenen": [freigabe.stand(conn, paket, pid, e, nur, pruefen=pruefen)
                                    for e in freigabe.EBENEN],
                         "panoramenNurFreigegeben": nur})


@router.post("/{ebene}", dependencies=[Depends(csrf)], status_code=201,
             summary="Ebene freigeben: Server hasht die Dateien und bindet die Freigabe daran "
             "(Projektleitung/Admin, nur Browser; „personenfrei“ nur Admin)")
def freigeben(pid: str, ebene: Ebene, body: FreigabeIn, request: Request,
              conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    paket = request.app.state.katalog.data_dir / pid
    try:
        freigabe.freigeben(conn, paket, pid, ebene, body.art, von=z.nutzer.id,
                           ist_admin=z.nutzer.is_admin)
    except freigabe.FreigabeFehler as e:
        raise _konflikt(e) from e
    nur = projektverwaltung.einstellungen(conn, pid).panoramen_nur_freigegeben
    return JSONResponse(freigabe.stand(conn, paket, pid, ebene, nur, pruefen=False),
                        status_code=201)


@router.delete("/{ebene}", dependencies=[Depends(csrf)],
               summary="Freigabe zurücknehmen (Projektleitung/Admin, nur Browser)")
def zuruecknehmen(pid: str, ebene: Ebene, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    paket = request.app.state.katalog.data_dir / pid
    try:
        freigabe.zuruecknehmen(conn, paket, pid, ebene, von=z.nutzer.id)
    except freigabe.FreigabeFehler as e:
        raise _konflikt(e) from e
    nur = projektverwaltung.einstellungen(conn, pid).panoramen_nur_freigegeben
    return JSONResponse(freigabe.stand(conn, paket, pid, ebene, nur, pruefen=False))
