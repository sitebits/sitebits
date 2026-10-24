"""API v1: Projekt lesen, Metadaten ändern (Bearbeiter, version/409, Protokoll),
OpenAPI-Beschreibung nur für angemeldete Nutzer (keine Docs-UI, keine öffentliche Auslieferung).
"""

from __future__ import annotations

import sqlite3
import time
from importlib.metadata import version as paketversion
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import fotos_tor, konten, lesezugriff, paket, projektverwaltung, protokoll
from .api_modelle import Projekt, ProjektBasis, Version, antwort
from .db import transaktion
from .katalog import PaketFehler
from .openapi_v1 import sichtbare_pfade, token_pfade, v1_schemas
from .routen_api import karte_json
from .web import (
    COOKIE,
    SQLITE_GANZZAHL_MAX,
    Konflikt,
    Ungueltig,
    api_token,
    csrf,
    db,
    fehler,
    kanal,
    token_name,
)
from .zugriff import Zugriff, angemeldet, projekt

router = APIRouter(prefix="/api/v1")
TITEL_MAX = 120
BESCHREIBUNG_MAX = 2000
_ERLAUBT_IN_BESCHREIBUNG = {"\n", "\r", "\t"}


class MetaIn(BaseModel):
    titel: str | None = Field(default=None, max_length=TITEL_MAX)
    beschreibung: str | None = Field(default=None, max_length=BESCHREIBUNG_MAX)
    version: int = Field(ge=0, le=SQLITE_GANZZAHL_MAX)


def _steuerzeichen(text: str, erlaubt: set[str]) -> bool:
    return any((ord(c) < 32 or ord(c) == 127) and c not in erlaubt for c in text)


def _meta(conn: sqlite3.Connection, pid: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM project_meta WHERE project_id=?", (pid,),
    ).fetchone()
    return row


# Beide Anmeldewege beschreiben (neue Dicts — das von FastAPI zwischengespeicherte
# `app.openapi()` wird nie verändert). Global gilt „eins von beiden“.
_SICHERHEIT = {
    "bearerAuth": {"type": "http", "scheme": "bearer"},
    "cookieAuth": {"type": "apiKey", "in": "cookie", "name": COOKIE},
}


@router.get("/openapi.json", include_in_schema=False)
def openapi(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = angemeldet(request, conn)
    schema: dict[str, Any] = request.app.openapi()
    pfade = {k: v for k, v in schema.get("paths", {}).items() if k.startswith("/api/v1/")}
    per_token = api_token(request) is not None
    if per_token:
        # E-T3: per Token nur, was per Token erreichbar ist (keine Verwaltung, Statistik, Konto).
        pfade = token_pfade(schema)
    pfade = sichtbare_pfade(pfade, n.is_admin and not per_token)
    komponenten = {
        **schema.get("components", {}),
        "schemas": v1_schemas(pfade, schema.get("components", {}).get("schemas", {})),
        "securitySchemes": dict(_SICHERHEIT),
    }
    return JSONResponse(
        {**schema, "paths": pfade, "components": komponenten,
         "security": [{"bearerAuth": []}, {"cookieAuth": []}]},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/version", summary="Produktversion (nur angemeldet)", responses=antwort(Version))
def version(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    """Version des installierten Pakets `scanview-server`, wie alle API-Routen nur für
    Angemeldete (401 sonst). Das ausgelieferte Web-Bundle trägt die Version ohnehin für die
    Diagnose; das Verbergen der Version ist keine Schutzmaßnahme — die schützen die
    Patch-Fristen (docs/public/support-und-patches.md)."""
    angemeldet(request, conn)
    return JSONResponse({"version": paketversion("scanview-server")})


def _karte(conn: sqlite3.Connection, z: Zugriff, meta: sqlite3.Row | None) -> dict[str, Any]:
    return karte_json(z.karte, meta, z.rolle, projektverwaltung.einstellungen(conn, z.karte.id))


@router.get("/projekte/{pid}", summary="Ein Projekt: Karte, eigene Rolle, Metadaten-Version",
            responses=antwort(Projekt))
def projekt_lesen(pid: str, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid)
    lesezugriff.erfasse_anfrage(request, conn, z, "oeffnen")
    karte = _karte(conn, z, _meta(conn, pid))
    karte["kategorien"] = [k.als_json() for k in projektverwaltung.kategorien(conn, pid)]
    return JSONResponse(karte)


@router.get("/projekte/{pid}/paket", summary="Paketinhalt: Ebenen, Koordinaten, Höhe, Klassen")
def paket_lesen(pid: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    """Paketinhalt für Agenten. Fotos-Ebene nur, wenn die Rolle die Fotos laden dürfte."""
    z = projekt(request, conn, pid)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "paket")
    d = request.app.state.katalog.data_dir / pid
    sichtbar = konten.mindestens(z.rolle, fotos_tor.MINDESTROLLE) or fotos_tor.freigegeben(d)
    try:
        return JSONResponse(paket.uebersicht(d, fotos_sichtbar=sichtbar))
    except (OSError, PaketFehler):
        return fehler(404, "nicht gefunden")


@router.patch("/projekte/{pid}", dependencies=[Depends(csrf)],
              summary="Titel/Beschreibung ändern (Bearbeiter; version = zuletzt gelesene)",
              responses=antwort(ProjektBasis))
def projekt_aendern(pid: str, body: MetaIn, request: Request,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid, mindestens="bearbeiter")
    gesetzt = body.model_fields_set - {"version"}
    if not gesetzt:
        raise Ungueltig("Titel oder Beschreibung angeben.")
    if body.titel is not None and _steuerzeichen(body.titel, set()):
        raise Ungueltig("Der Titel darf keine Zeilenumbrüche oder Steuerzeichen enthalten.")
    if body.beschreibung is not None and _steuerzeichen(body.beschreibung,
                                                         _ERLAUBT_IN_BESCHREIBUNG):
        raise Ungueltig("Die Beschreibung enthält unzulässige Steuerzeichen.")
    jetzt = int(time.time())
    with transaktion(conn):
        alt = _meta(conn, pid)
        if body.version != (int(alt["version"]) if alt else 0):
            raise Konflikt(_karte(conn, z, alt))
        vorher = {"titel": alt["title"] if alt else None,
                  "beschreibung": alt["description"] if alt else None}
        titel = ((body.titel or "").strip() or None) if "titel" in gesetzt else vorher["titel"]
        beschreibung = (
            ((body.beschreibung or "").strip() or None) if "beschreibung" in gesetzt
            else vorher["beschreibung"]
        )
        # Stimmt der gesendete (explizit gesetzte) Titel mit dem Katalog-Titel
        # des Pakets überein, wird kein Override gespeichert (NULL) — sonst friert ein Formular, das
        # nur die Beschreibung ändern wollte aber den unangetasteten, aufgelösten Titel mitschickt,
        # den heutigen Katalog-Titel dauerhaft ein und folgt einer späteren Umbenennung im Paket
        # nicht mehr.
        if "titel" in gesetzt and titel is not None and titel == z.karte.titel:
            titel = None
        if alt is None:
            conn.execute(
                "INSERT INTO project_meta (project_id, title, description, updated_by,"
                " updated_at, version) VALUES (?,?,?,?,?,1)",
                (pid, titel, beschreibung, z.nutzer.id, jetzt),
            )
        else:
            conn.execute(
                "UPDATE project_meta SET title=?, description=?, updated_by=?, updated_at=?,"
                " version=version+1 WHERE project_id=?",
                (titel, beschreibung, z.nutzer.id, jetzt, pid),
            )
        protokoll.ereignis(
            conn, kanal=kanal(request), nutzer_id=z.nutzer.id, project_id=pid,
            art="meta.geaendert", objekt_id=pid, vorher=vorher,
            nachher={"titel": titel, "beschreibung": beschreibung},
            token_name=token_name(request), now=jetzt,
        )
        neu = _meta(conn, pid)
    return JSONResponse(_karte(conn, z, neu))
