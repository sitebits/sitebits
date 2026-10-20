"""Verwaltung unter /api/v1/admin/* (nur Admin, sonst 404; alles
protokolliert über verwaltung.py, kanal 'web') und die Statistik-Meldung des Viewers."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import konten, statistik, verwaltung
from .katalog import PROJECT_ID_RE, Katalog
from .tokens import hash_token
from .web import SQLITE_GANZZAHL_MAX, Ungueltig, csrf, db
from .zugriff import NICHT_GEFUNDEN, admin, projekt

router = APIRouter(prefix="/api/v1")
Rolle = konten.RolleWert
_EID_RE = re.compile(r"[0-9a-f]{16}")


Uid = Annotated[int, Path(ge=1, le=SQLITE_GANZZAHL_MAX)]


class EinladungIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: str = Field(min_length=3, max_length=320)
    # Nur echte JSON-Wahrheitswerte (Befund B-FUZZ-2): kein Admin-Recht aus 1 oder "true".
    admin: StrictBool = False
    rechte: dict[str, Rolle] = Field(default_factory=dict)
    zuruecksetzen: StrictBool = False


class RechtIn(BaseModel):
    rolle: Rolle | None


class StatistikIn(BaseModel):
    oeffnen: int = Field(default=0, ge=0, le=1000)
    standpunkt: int = Field(default=0, ge=0, le=1000)
    messung: int = Field(default=0, ge=0, le=1000)
    poi: int = Field(default=0, ge=0, le=1000)


def _nicht_gefunden() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)


def _konto(conn: sqlite3.Connection, uid: int) -> sqlite3.Row:
    row: sqlite3.Row | None = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        raise _nicht_gefunden()
    return row


def _projekte(conn: sqlite3.Connection, katalog: Katalog) -> list[tuple[str, str]]:
    titel = {r["project_id"]: r["title"] for r in conn.execute(
        "SELECT project_id, title FROM project_meta WHERE title IS NOT NULL")}
    return [(k.id, titel.get(k.id) or k.titel) for k in katalog.alle()]


def _einladung_json(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": verwaltung.einladung_id(r["token_hash"]), "name": r["name"], "email": r["email"],
        "zweck": r["purpose"], "admin": bool(r["is_admin"]), "rechte": json.loads(r["grants"]),
        "erstelltAm": r["created_at"], "ablauf": r["expires_at"], "projekt": r["project_id"],
    }


@router.get("/admin/nutzer", summary="Nutzer mit Status, letzter Anmeldung und Rechten (Admin)")
def nutzer_liste(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    admin(request, conn)
    rechte: dict[int, dict[str, str]] = {}
    anonym = verwaltung.anonymisierte_ids(conn)
    for r in conn.execute("SELECT user_id, project_id, role FROM project_roles"):
        rechte.setdefault(int(r["user_id"]), {})[r["project_id"]] = r["role"]
    nutzer = [
        {"id": u["id"], "name": u["name"], "email": u["email"], "admin": bool(u["is_admin"]),
         "gesperrt": bool(u["disabled"]), "letzteAnmeldung": u["last_login_at"],
         "rechte": rechte.get(int(u["id"]), {}), "anonymisiert": int(u["id"]) in anonym}
        for u in conn.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE, id")
    ]
    projekte = [{"id": pid, "titel": t} for pid, t in _projekte(conn, request.app.state.katalog)]
    return JSONResponse({"nutzer": nutzer, "projekte": projekte})


@router.get("/admin/einladungen", summary="Offene Einladungen (Admin, ohne Link)")
def einladungen(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    admin(request, conn)
    rows = conn.execute(
        "SELECT * FROM invites WHERE used_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
        (int(time.time()),),
    ).fetchall()
    return JSONResponse({"einladungen": [_einladung_json(r) for r in rows]})


@router.post("/admin/einladungen", status_code=201, dependencies=[Depends(csrf)],
             summary="Einladung anlegen (Admin) - der Link steht nur in dieser Antwort")
def einladung_anlegen(body: EinladungIn, request: Request,
                      conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    a = admin(request, conn)
    if body.admin and body.zuruecksetzen:
        # Eine Zurücksetzen-Einladung ändert den Admin-Status des
        # bestehenden Kontos nicht (konten.einladung_einloesen berührt users.is_admin dort gar
        # nicht) — admin=true wäre hier nur ein irreführender Protokolleintrag ohne Wirkung.
        raise Ungueltig(
            "Admin-Rechte lassen sich nicht per Zurücksetzen-Einladung vergeben.",
        )
    katalog: Katalog = request.app.state.katalog
    for pid in body.rechte:
        if not PROJECT_ID_RE.fullmatch(pid) or katalog.get(pid) is None:
            raise Ungueltig("Unbekanntes Projekt in den Rechten.")
    vorhanden = konten.konto_id(conn, body.email)
    if vorhanden is not None and verwaltung.ist_anonymisiert(conn, vorhanden):
        raise Ungueltig(verwaltung.ANONYMISIERT)
    if not body.zuruecksetzen and vorhanden is not None:
        raise Ungueltig(
            "Für diese E-Mail gibt es schon ein Konto. Dafür den Zurücksetzen-Link nutzen.",
        )
    try:
        token = verwaltung.einladen(
            conn, email=body.email, name=body.name, grants=dict(body.rechte), is_admin=body.admin,
            zuruecksetzen=body.zuruecksetzen, von=a.id, kanal="web",
        )
    except ValueError as e:
        raise Ungueltig(str(e)) from e
    row = conn.execute("SELECT * FROM invites WHERE token_hash=?", (hash_token(token),)).fetchone()
    return JSONResponse(
        {"link": f"{request.app.state.settings.origin}/einladung#t={token}",
         "einladung": _einladung_json(row)},
        status_code=201, headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/einladungen/{eid}/widerrufen", status_code=204,
             dependencies=[Depends(csrf)], summary="Offene Einladung widerrufen (Admin)")
def einladung_widerrufen(eid: str, request: Request,
                         conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    if not _EID_RE.fullmatch(eid) or not verwaltung.einladung_widerrufen(
        conn, eid, von=a.id, kanal="web", nur_projekt=None,
    ):
        raise _nicht_gefunden()
    return Response(status_code=204)


@router.post("/admin/nutzer/{uid}/sperren", status_code=204, dependencies=[Depends(csrf)],
             summary="Konto sperren und alle Sitzungen beenden (Admin)")
def sperren(uid: Uid, request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    _konto(conn, uid)
    if uid == a.id:
        raise Ungueltig("Das eigene Konto kann nicht gesperrt werden.")
    verwaltung.sperren(conn, uid, von=a.id, kanal="web")
    return Response(status_code=204)


@router.post("/admin/nutzer/{uid}/entsperren", status_code=204, dependencies=[Depends(csrf)],
             summary="Gesperrtes Konto freigeben (Admin)")
def entsperren(uid: Uid, request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    _konto(conn, uid)
    try:
        verwaltung.entsperren(conn, uid, von=a.id, kanal="web")
    except verwaltung.KontoAnonymisiert as e:
        raise Ungueltig(str(e)) from e
    return Response(status_code=204)


@router.post("/admin/nutzer/{uid}/abmelden-ueberall", status_code=204,
             dependencies=[Depends(csrf)], summary="Alle Sitzungen eines Kontos beenden (Admin)")
def abmelden_ueberall(uid: Uid, request: Request,
                      conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    _konto(conn, uid)
    verwaltung.abmelden_ueberall(conn, uid, von=a.id, kanal="web")
    return Response(status_code=204)


@router.put("/admin/nutzer/{uid}/rechte/{pid}", dependencies=[Depends(csrf)],
            summary="Rolle eines Nutzers für ein Projekt setzen oder entziehen (Admin)")
def recht(uid: Uid, pid: str, body: RechtIn, request: Request,
          conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    a = admin(request, conn)
    k = _konto(conn, uid)
    if not PROJECT_ID_RE.fullmatch(pid) or request.app.state.katalog.get(pid) is None:
        raise _nicht_gefunden()
    if k["is_admin"]:
        raise Ungueltig("Admins sehen ohnehin alle Projekte.")
    try:
        alt = verwaltung.recht_setzen(conn, uid, pid, body.rolle, von=a.id, kanal="web")
    except verwaltung.KontoAnonymisiert as e:
        raise Ungueltig(str(e)) from e
    return JSONResponse({"vorher": alt, "rolle": body.rolle})


@router.get("/admin/statistik", summary="Nutzung je Projekt und letzte Anmeldung je Nutzer (Admin)")
def statistik_lesen(request: Request, tage: Annotated[int, Query(ge=1, le=365)] = 30,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    admin(request, conn)
    nutzer = [
        {"id": r["id"], "name": r["name"], "letzteAnmeldung": r["last_login_at"]}
        for r in conn.execute(
            "SELECT id, name, last_login_at FROM users ORDER BY name COLLATE NOCASE",
        )
    ]
    projekte = statistik.auswertung(conn, _projekte(conn, request.app.state.katalog), tage)
    return JSONResponse({"tage": tage, "projekte": projekte, "nutzer": nutzer})


@router.post("/statistik/{pid}", status_code=204, dependencies=[Depends(csrf)],
             summary="Zähler des Viewers melden (gebündelt; je Nutzer und Projekt gedrosselt)")
def statistik_melden(pid: str, body: StatistikIn, request: Request,
                     conn: sqlite3.Connection = Depends(db)) -> Response:
    z = projekt(request, conn, pid)
    werte = body.model_dump()
    if not any(werte.values()):
        return Response(status_code=204)
    if not statistik.erlaubt(conn, z.nutzer.id, pid):
        raise StarletteHTTPException(status_code=429, detail="Zu viele Statistik-Meldungen.")
    statistik.zaehlen(conn, pid, werte)
    return Response(status_code=204)
