"""Lesezugriffsprotokoll einsehen — nur Admins per Cookie (`zugriff.admin`: Nicht-Admins und
jedes API-Token bekommen 404, ADR 0008; `web._vorrang` hält das auch bei ungültigen Parametern).

Das Lesen des Protokolls wird selbst protokolliert (Kontrolle der Kontrolleure): je Projekt, das
in der ausgelieferten Seite oder im Export vorkommt, ein Eintrag (`api` bzw. `export` mit Objekt
`lesezugriffe`). Eine leere Antwort erzeugt keinen Eintrag. Der Eintrag entsteht nach dem Lesen
und erscheint deshalb erst beim nächsten Abruf in der Liste.
"""

from __future__ import annotations

import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from . import lesezugriff
from .api_modelle import FEHLER_ALS_JSON
from .katalog import PROJECT_ID_RE
from .routen_export import JsonLinesAntwort, Limit, Vor, _jsonl
from .web import SQLITE_GANZZAHL_MAX, Ungueltig, db
from .zugriff import admin

router = APIRouter(prefix="/api/v1")
Konto = Annotated[int | None, Query(alias="nutzer", ge=1, le=SQLITE_GANZZAHL_MAX)]


def _frisch(request: Request, conn: sqlite3.Connection) -> None:
    """Abgelaufene Einträge nie ausliefern: vor jedem Lesen aufräumen (auf einer ruhigen Instanz
    käme das stündliche Aufräumen beim Erfassen sonst nicht zum Zug)."""
    lesezugriff.aufraeumen(conn, request.app.state.settings.lesezugriffe_tage)


def _pid(pid: str | None) -> str | None:
    if pid is not None and not PROJECT_ID_RE.fullmatch(pid):
        raise Ungueltig("Projekt-ID ungültig.")
    return pid


def _selbst_erfassen(
    request: Request, conn: sqlite3.Connection, admin_id: int, projekte: set[str], art: str,
) -> None:
    for pid in sorted(projekte):
        lesezugriff.erfasse_nutzer(request, conn, admin_id, pid, art, "lesezugriffe")


def _seite(request: Request, conn: sqlite3.Connection, admin_id: int,
           liste: list[lesezugriff.Lesezugriff], limit: int) -> JSONResponse:
    _selbst_erfassen(request, conn, admin_id, {e.project_id for e in liste}, "api")
    return JSONResponse({
        "lesezugriffe": [e.als_json() for e in liste],
        "weiter": liste[-1].id if len(liste) == limit else None,
    })


def _export(request: Request, conn: sqlite3.Connection, admin_id: int, pid: str | None,
            konto: int | None, name: str) -> Response:
    text = lesezugriff.jsonl(conn, project_id=pid, nutzer_id=konto)
    _selbst_erfassen(request, conn, admin_id,
                     lesezugriff.projekte(conn, project_id=pid, nutzer_id=konto), "export")
    return _jsonl(text, name)


@router.get("/projekte/{pid}/lesezugriffe",
            summary="Lesezugriffe eines Projekts (neueste zuerst, Admin)")
def projekt_lesezugriffe(pid: str, request: Request, vor: Vor = None, limit: Limit = 50,
                         konto: Konto = None,
                         conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    a = admin(request, conn)
    _frisch(request, conn)
    liste = lesezugriff.lesen(conn, project_id=_pid(pid), nutzer_id=konto, vor=vor, limit=limit)
    return _seite(request, conn, a.id, liste, limit)


@router.get("/projekte/{pid}/lesezugriffe.jsonl",
            summary="Lesezugriffe eines Projekts als JSON Lines (Admin)",
            response_class=JsonLinesAntwort, responses=FEHLER_ALS_JSON)
def projekt_lesezugriffe_jsonl(pid: str, request: Request, konto: Konto = None,
                               conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    _frisch(request, conn)
    return _export(request, conn, a.id, _pid(pid), konto, f"lesezugriffe-{pid}")


@router.get("/lesezugriffe", summary="Lesezugriffe aller Projekte (neueste zuerst, Admin)")
def lesezugriffe_alles(request: Request, vor: Vor = None, limit: Limit = 50,
                       projekt_id: Annotated[str | None, Query(alias="projekt")] = None,
                       konto: Konto = None,
                       conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    a = admin(request, conn)
    _frisch(request, conn)
    liste = lesezugriff.lesen(conn, project_id=_pid(projekt_id), nutzer_id=konto, vor=vor,
                              limit=limit)
    return _seite(request, conn, a.id, liste, limit)


@router.get("/lesezugriffe.jsonl", summary="Lesezugriffe aller Projekte als JSON Lines (Admin)",
            response_class=JsonLinesAntwort, responses=FEHLER_ALS_JSON)
def lesezugriffe_alles_jsonl(request: Request,
                             projekt_id: Annotated[str | None, Query(alias="projekt")] = None,
                             konto: Konto = None,
                             conn: sqlite3.Connection = Depends(db)) -> Response:
    a = admin(request, conn)
    _frisch(request, conn)
    return _export(request, conn, a.id, _pid(projekt_id), konto, "lesezugriffe")
