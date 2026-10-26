"""E-Mail-Benachrichtigung (F8): Einwilligung im Konto, Abmelden je Faden, Links aus der Mail.

Ohne eingerichteten Postdienst (SCANVIEW_POST) antworten alle Wege mit 404.

- `/api/v1/konto/benachrichtigung` (nur Sitzung, nie Token — wie alle Kontowege): Einwilligung
  an/aus, Bestätigung der Adresse.
- `/api/v1/projekte/{pid}/pois/{eid}/faden/benachrichtigung` (Sitzung oder Token, wie der Faden):
  Mails zu diesem Befund an/aus — nur für das eigene Konto.
- `/api/mail/{token}`: Link aus der Mail. Meldet niemanden an: ohne Sitzung zur Anmeldung, mit
  fremder Sitzung 404, sonst weiter zum Faden.
- `/api/mail/abmelden/{token}`: GET leitet zur Bestätigungsseite (Mail-Scanner rufen Links ab),
  POST meldet ab (One-Click nach RFC 8058, ohne Anmeldung — kann nur Mail verringern).
"""

from __future__ import annotations

import sqlite3
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import lesezugriff, post
from .routen_faden import _poi_id, _zugriff, sicht
from .web import aktueller_nutzer, csrf, db
from .zugriff import NICHT_GEFUNDEN, angemeldet

router = APIRouter()
LINK_UNGUELTIG = "Der Link ist ungültig oder abgelaufen."
BESTAETIGUNG_UNGUELTIG = "Der Bestätigungslink ist ungültig oder abgelaufen."


def _dienst(request: Request) -> post.Postdienst:
    s = request.app.state.settings
    if s.post_dir is None or s.post_absender is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return post.Postdienst(spool=s.post_dir, absender=s.post_absender, origin=s.origin,
                           state_dir=s.state_dir)


class EinwilligungIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    eingeschaltet: StrictBool


class BestaetigenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=10, max_length=200)


class FadenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    aus: StrictBool


@router.get("/api/v1/konto/benachrichtigung", include_in_schema=False)
def einwilligung_lesen(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _dienst(request)
    n = angemeldet(request, conn)
    return JSONResponse({**post.zustand(conn, n.id), "email": n.email})


@router.put("/api/v1/konto/benachrichtigung", dependencies=[Depends(csrf)],
            include_in_schema=False)
def einwilligung_setzen(body: EinwilligungIn, request: Request,
                        conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    d = _dienst(request)
    n = angemeldet(request, conn)
    return JSONResponse({**post.einschalten(conn, d, n.id, body.eingeschaltet), "email": n.email})


@router.post("/api/v1/konto/benachrichtigung/bestaetigen", dependencies=[Depends(csrf)],
             include_in_schema=False)
def bestaetigen(body: BestaetigenIn, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _dienst(request)
    n = angemeldet(request, conn)
    if not post.bestaetigen(conn, n.id, body.token):
        raise StarletteHTTPException(status_code=422, detail=BESTAETIGUNG_UNGUELTIG)
    return JSONResponse({**post.zustand(conn, n.id), "email": n.email})


@router.get("/api/v1/projekte/{pid}/pois/{eid}/faden/benachrichtigung",
            operation_id="faden_benachrichtigung_lesen",
            summary="Mails zu diesem Befund für das eigene Konto: abbestellt? (nur mit "
            "eingerichtetem Postdienst)")
def faden_lesen(pid: str, eid: str, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _dienst(request)
    z = _zugriff(request, conn, pid)
    poi = _poi_id(eid)
    from . import faden

    faden.stand(conn, pid, poi, sicht(request, conn, z))
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "faden")
    return JSONResponse({"aus": post.ist_faden_aus(conn, z.nutzer.id, pid, poi),
                         "konto": post.zustand(conn, z.nutzer.id)})


@router.put("/api/v1/projekte/{pid}/pois/{eid}/faden/benachrichtigung",
            dependencies=[Depends(csrf)], operation_id="faden_benachrichtigung_setzen",
            summary="Mails zu diesem Befund für das eigene Konto ab- oder wieder anbestellen")
def faden_setzen(pid: str, eid: str, body: FadenIn, request: Request,
                 conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _dienst(request)
    z = _zugriff(request, conn, pid)
    poi = _poi_id(eid)
    from . import faden

    faden.stand(conn, pid, poi, sicht(request, conn, z))
    post.faden_aus(conn, z.nutzer.id, pid, poi, body.aus)
    return JSONResponse({"aus": body.aus, "konto": post.zustand(conn, z.nutzer.id)})


@router.get("/api/mail/abmelden/{token}", include_in_schema=False)
def abmelden_seite(token: str, request: Request) -> Response:
    _dienst(request)
    return RedirectResponse(f"/abmelden#t={quote(token, safe='')}", status_code=303)


@router.post("/api/mail/abmelden/{token}", include_in_schema=False)
def abmelden(token: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    d = _dienst(request)
    try:
        link = post.link_pruefen(d.state_dir, token, conn)
        post.abmelden(conn, link)
    except post.LinkUngueltig:
        raise StarletteHTTPException(status_code=404, detail=LINK_UNGUELTIG) from None
    return JSONResponse({"abgemeldet": "faden" if link.art == "f" else "alle"})


@router.get("/api/mail/{token}", include_in_schema=False)
def zum_faden(token: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    d = _dienst(request)
    try:
        link = post.link_pruefen(d.state_dir, token, conn)
    except post.LinkUngueltig:
        raise StarletteHTTPException(status_code=404, detail=LINK_UNGUELTIG) from None
    if link.art != "l":
        raise StarletteHTTPException(status_code=404, detail=LINK_UNGUELTIG)
    n = aktueller_nutzer(request, conn)
    if n is None:
        weiter = quote(f"/api/mail/{token}", safe="")
        return RedirectResponse(f"/anmelden?weiter={weiter}", status_code=303)
    if n.id != link.uid:
        # Weitergeleitete Mail: der Link gehört einem anderen Konto.
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return RedirectResponse(
        f"/befunde?p={quote(link.pid, safe='')}&b={quote(link.poi, safe='')}", status_code=303)
