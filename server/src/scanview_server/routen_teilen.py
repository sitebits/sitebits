"""Nur-Lese-Links (F28): Verwaltung unter /api/v1/projekte/{pid}/teilen (Projektleitung/Admin, nur
Browser), Austausch Token → Gast-Cookie unter /auth/teilen und das bereinigte Gast-Manifest unter
/api/teilen/manifest. Fachlogik und Positivliste in `teilen.py`."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import db as dbmod
from . import lesezugriff, passwort, projektverwaltung, teilen
from .web import (
    DIENST,
    SQLITE_GANZZAHL_MAX,
    UEBERLASTET,
    Ungueltig,
    csrf,
    db,
    fehler,
    frisch_angemeldet,
)
from .zugriff import NICHT_GEFUNDEN, leitung

router = APIRouter()
Id = Annotated[int, Path(ge=1, le=SQLITE_GANZZAHL_MAX)]
_PFAD = "/api/v1/projekte/{pid}/teilen"
FALSCHES_PASSWORT = "Das Passwort stimmt nicht."
PASSWORT_NOETIG = "Für diesen Link braucht es ein Passwort."
GESPERRT = "Zu viele falsche Passwörter – bitte in 15 Minuten noch einmal versuchen."  # noqa: RUF001


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=teilen.NAME_MAX)
    tage: int = Field(default=teilen.TAGE_STANDARD, ge=1, le=teilen.TAGE_MAX)
    passwort: str | None = Field(default=None, min_length=teilen.PASSWORT_MIN,
                                 max_length=teilen.PASSWORT_MAX)


class AustauschIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=1, max_length=128)
    passwort: str | None = Field(default=None, max_length=teilen.PASSWORT_MAX)


@router.get(_PFAD, summary="Nur-Lese-Links des Projekts (Projektleitung/Admin, nur Browser)")
def links(pid: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "teilen")
    return JSONResponse({
        "erlaubt": projektverwaltung.einstellungen(conn, pid).nur_lese_links,
        "links": [x.als_json() for x in teilen.liste(conn, pid)],
    })


@router.post(_PFAD, dependencies=[Depends(csrf)], status_code=201,
             summary="Nur-Lese-Link anlegen (Projektleitung/Admin, frische Sitzung)")
def link_anlegen(pid: str, body: LinkIn, request: Request,
                 conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    # Ein Link ist ein neues Zugangsmittel — wie Passkey oder API-Token nur mit frischer Sitzung.
    frisch_angemeldet(request, conn, "einen Nur-Lese-Link anzulegen")
    if not projektverwaltung.einstellungen(conn, pid).nur_lese_links:
        raise StarletteHTTPException(status_code=403, detail=teilen.AUSGESCHALTET)
    name = projektverwaltung.text_pruefen(body.name, "Name", teilen.NAME_MAX)
    pw_hash = None
    if body.passwort is not None:
        pw = body.passwort
        if pw != pw.strip() or not pw.isprintable():
            raise Ungueltig("Das Passwort darf keine Leer- oder Steuerzeichen am Rand enthalten.")
        pw_hash = passwort.hash_pw(pw)
    link, token = teilen.anlegen(conn, pid, name=name, tage=body.tage, passwort_hash=pw_hash,
                                 von=z.nutzer.id)
    # Das Token steht nur in dieser Antwort; das Portal baut daraus <origin>/teilen#t=<token>.
    return JSONResponse({"link": link.als_json(), "token": token}, status_code=201)


@router.delete(_PFAD + "/{lid}", dependencies=[Depends(csrf)],
               summary="Nur-Lese-Link widerrufen (sofort wirksam)")
def link_widerrufen(pid: str, lid: Id, request: Request,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = leitung(request, conn, pid)
    link = teilen.widerrufen(conn, pid, lid, von=z.nutzer.id)
    if link is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return JSONResponse({"link": link.als_json()})


@router.post("/auth/teilen", dependencies=[Depends(csrf)], include_in_schema=False)
async def austausch(body: AustauschIn, request: Request) -> JSONResponse:
    d: teilen.Drosseln = request.app.state.teilen_drosseln
    client_ip = request.client.host if request.client else None
    # Erst je IP, dann global: eine einzelne IP belegt so höchstens ihr eigenes Kontingent im
    # globalen Fenster und kann den Austausch nicht für alle anderen Gäste sperren.
    if not d.austausch_ip.erlaubt(client_ip) or not d.austausch_global.erlaubt("alle"):
        return fehler(429, UEBERLASTET)
    # argon2 (Link-Passwort) läuft wie die Anmeldung in den eigenen Rechenplätzen.
    antwort: JSONResponse = await request.app.state.rechenplaetze.ausfuehren(
        lambda: _austausch(body, request, client_ip))
    return antwort


def _austausch(body: AustauschIn, request: Request, client_ip: str | None) -> JSONResponse:
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        link = teilen.link_zum_token(conn, body.token)
        if link is None or request.app.state.katalog.get(link.project_id) is None:
            return fehler(404, teilen.UNGUELTIG)
        if link.passwort_hash is not None:
            if not body.passwort:
                return JSONResponse({"dienst": DIENST, "fehler": PASSWORT_NOETIG,
                                     "passwort": True}, status_code=401)
            # Hex-Schlüssel wie bei der Konto-Anmeldung: das fail2ban-Muster erwartet [0-9a-f]+.
            key = hashlib.sha256(f"teilen:{link.id}".encode()).hexdigest()
            ergebnis = passwort.anmeldung_pruefen(conn, key, link.passwort_hash, body.passwort)
            if ergebnis == "gesperrt":
                return fehler(429, GESPERRT)
            if ergebnis != "ok":
                passwort.fehlversuch_melden(key, client_ip)
                return JSONResponse({"dienst": DIENST, "fehler": FALSCHES_PASSWORT,
                                     "passwort": True}, status_code=401)
        alt = request.cookies.get(teilen.GAST_COOKIE)
        if alt:
            teilen.gast_beenden(conn, alt)
        token = teilen.gast_anlegen(conn, link)
        # Abgelaufene Gast-Sitzungen nicht erst beim Neustart wegräumen (ein indizierter DELETE).
        teilen.aufraeumen(conn)
        r = JSONResponse({"projekt": link.project_id})
        s = request.app.state.settings
        r.set_cookie(teilen.GAST_COOKIE, token, max_age=max(1, link.ablauf - int(time.time())),
                     path="/", secure=s.cookie_secure, httponly=True, samesite="lax")
        return r
    finally:
        conn.close()


@router.get("/api/teilen/manifest", include_in_schema=False)
def gast_manifest(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    """Bereinigtes Manifest (nur Wolke und Meshes) für die Gast-Ansicht; das rohe project.json
    bleibt für Gäste gesperrt."""
    g = teilen.gast(conn, request.cookies.get(teilen.GAST_COOKIE))
    if g is None:
        return fehler(401, teilen.UNGUELTIG)
    katalog = request.app.state.katalog
    gp = teilen.gast_paket(katalog.data_dir / g.project_id) if katalog.get(g.project_id) else None
    if gp is None:
        return fehler(404, NICHT_GEFUNDEN)
    return JSONResponse(gp.manifest)
