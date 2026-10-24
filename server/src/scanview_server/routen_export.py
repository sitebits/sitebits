"""Export (CSV/JSON, Bearbeiter) und Bearbeitungsprotokoll lesen."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import export, faden, inhalte, lesezugriff, maengelliste, projektverwaltung, protokoll
from .api_modelle import FEHLER_ALS_JSON
from .katalog import PROJECT_ID_RE, Karte
from .routen_api import karte_json
from .routen_faden import sicht as _sicht
from .routen_v1 import _meta
from .web import SQLITE_GANZZAHL_MAX, Ungueltig, api_token, db
from .zugriff import Zugriff, admin, projekt

router = APIRouter(prefix="/api/v1")
NUR_EXPORT = "Export ist Bearbeitern vorbehalten."
NUR_VERLAUF = "Den Verlauf sehen Bearbeiter, Projektleitung und Admins."
BEARBEITUNGEN = ("meta", "poi", "messung", "eintrag")
VERWALTUNG_ARTEN = ("einladung", "recht", "beteiligter", "gewerk", "kategorie", "einstellung",
                    "projekt")


NUR_EXPORT_LEITUNG = "Export ist in diesem Projekt der Projektleitung vorbehalten."


def _export_erlaubt(conn: sqlite3.Connection, z: Zugriff) -> None:
    erlaubt = projektverwaltung.einstellungen(conn, z.karte.id).export_bearbeiter
    if z.rolle == "bearbeiter" and not erlaubt:
        raise StarletteHTTPException(status_code=403, detail=NUR_EXPORT_LEITUNG)


# Eigene Antwortklassen, damit die OpenAPI den tatsächlichen Inhaltstyp nennt statt
# application/json (Befund B-FUZZ-1).
class CsvAntwort(Response):
    media_type = "text/csv"


class XlsxAntwort(Response):
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class JsonLinesAntwort(Response):
    media_type = "application/x-ndjson"
Vor = Annotated[int | None, Query(ge=1, le=SQLITE_GANZZAHL_MAX)]
Limit = Annotated[int, Query(ge=1, le=protokoll.SEITE_MAX)]


def _datei(pid: str, endung: str) -> dict[str, str]:
    tag = datetime.now(UTC).date().isoformat()
    return {"Content-Disposition": f'attachment; filename="scanview-{pid}-{tag}.{endung}"'}


def _genauigkeit_kurz(k: Karte) -> str:
    """Mängelliste Zeile 1: Lage und Maßstab (F15); bei verschiedenen Ebenen je Ebene mit Titel."""
    texte = {g.titel: " · ".join(g.zeilen[:2]) for g in k.genauigkeit}
    if len(set(texte.values())) <= 1:
        return next(iter(texte.values()), "")
    return " | ".join(f"{t}: {x}" for t, x in texte.items())


@router.get("/projekte/{pid}/export.csv", summary="POIs und Messungen als CSV (Bearbeiter)",
            response_class=CsvAntwort, responses=FEHLER_ALS_JSON)
def export_csv(pid: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_EXPORT)
    _export_erlaubt(conn, z)
    lesezugriff.erfasse_anfrage(request, conn, z, "export", "csv")
    text = export.csv_text(inhalte.liste(conn, "pois", pid), inhalte.liste(conn, "messungen", pid),
                           _faden_spalten(conn, pid), einheit=z.karte.einheit,
                           hinweise={g.id: g.hinweise for g in z.karte.genauigkeit})
    return CsvAntwort(text.encode("utf-8"), media_type="text/csv; charset=utf-8",
                    headers=_datei(pid, "csv"))


@router.get("/projekte/{pid}/export.json", summary="POIs und Messungen als JSON (Bearbeiter)")
def export_json(pid: str, request: Request, conn: sqlite3.Connection = Depends(db)) -> Response:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_EXPORT)
    _export_erlaubt(conn, z)
    lesezugriff.erfasse_anfrage(request, conn, z, "export", "json")
    karte = karte_json(z.karte, _meta(conn, pid), z.rolle)
    daten = export.json_export(
        pid, z.karte.revision, str(karte["titel"]), str(karte["beschreibung"]),
        inhalte.liste(conn, "pois", pid), inhalte.liste(conn, "messungen", pid), int(time.time()),
        *_faden_json(request, conn, z), ebenen=z.karte.genauigkeit,
    )
    return Response(json.dumps(daten, ensure_ascii=False, indent=2).encode("utf-8"),
                    media_type="application/json", headers=_datei(pid, "json"))


@router.get("/projekte/{pid}/maengelliste.xlsx", summary="Befundliste als XLSX (Bearbeiter)",
            response_class=XlsxAntwort, responses=FEHLER_ALS_JSON)
def maengelliste_xlsx(pid: str, request: Request,
                      conn: sqlite3.Connection = Depends(db)) -> Response:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_EXPORT)
    _export_erlaubt(conn, z)
    lesezugriff.erfasse_anfrage(request, conn, z, "export", "xlsx")
    karte = karte_json(z.karte, _meta(conn, pid), z.rolle)
    return XlsxAntwort(maengelliste.xlsx(inhalte.liste(conn, "pois", pid), str(karte["titel"]),
                                        z.karte.revision, int(time.time()),
                                        _faden_spalten(conn, pid), einheit=z.karte.einheit,
                                        genauigkeit=_genauigkeit_kurz(z.karte)),
                       headers=_datei(pid, "xlsx"))


@router.get("/projekte/{pid}/maengelliste", summary="Befundliste für die Druckansicht (Bearbeiter)")
def maengelliste_druck(pid: str, request: Request,
                       conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_EXPORT)
    _export_erlaubt(conn, z)
    lesezugriff.erfasse_anfrage(request, conn, z, "export", "maengelliste")
    karte = karte_json(z.karte, _meta(conn, pid), z.rolle)
    pois = [p.als_json() for p in inhalte.liste(conn, "pois", pid)]
    if faden.aktiviert(conn, pid):
        # Befund-Fäden an: je POI der Stand wie bei GET …/pois (Status, Zuständig, Frist).
        staende = faden.staende(conn, pid)
        pois = [{**p, "stand": staende[p["id"]]} for p in pois if p["id"] in staende]
    return JSONResponse({"titel": karte["titel"], "revision": z.karte.revision,
                         "stand": int(time.time()), "pois": pois})


def _faden_spalten(conn: sqlite3.Connection,
                   pid: str) -> tuple[tuple[str, ...], dict[str, list[str]]] | None:
    """Zusatzspalten Status/Zuständig/Frist für CSV und XLSX — nur bei eingeschalteten Fäden."""
    return (faden.SPALTEN, faden.spalten(conn, pid)) if faden.aktiviert(conn, pid) else None


def _faden_json(request: Request, conn: sqlite3.Connection, z: Zugriff
                ) -> tuple[dict[str, Any] | None, dict[str, list[Any]] | None]:
    """Stand und sichtbarer Faden je POI für den JSON-Export (interne nur Bauherrenseite)."""
    if not faden.aktiviert(conn, z.karte.id):
        return None, None
    return (faden.staende(conn, z.karte.id),
            faden.alle_faeden(conn, z.karte.id, intern=_sicht(request, conn, z).intern))


def _seite(liste: list[protokoll.Ereignis], limit: int) -> JSONResponse:
    return JSONResponse({
        "ereignisse": [e.als_json() for e in liste],
        "weiter": liste[-1].id if len(liste) == limit else None,
    })


def _jsonl(text: str, name: str) -> Response:
    return JsonLinesAntwort(text.encode("utf-8"),
                    headers={"Content-Disposition": f'attachment; filename="{name}.jsonl"'})


def _arten(request: Request, rolle: str) -> tuple[str, ...] | None:
    """Per Token nur Bearbeitungen (kein Konto-/Rechte-Verlauf per Token, wie das globale
    Protokoll). Per Sitzung: Admin alles, Projektleitung zusätzlich die Verwaltung ihres Projekts,
    alle anderen nur Bearbeitungen."""
    if api_token(request) is not None:
        return BEARBEITUNGEN
    if rolle == "admin":
        return None
    if rolle == "projektleitung":
        return BEARBEITUNGEN + VERWALTUNG_ARTEN
    return BEARBEITUNGEN


@router.get("/projekte/{pid}/protokoll", summary="Verlauf eines Projekts (neueste zuerst)")
def projekt_protokoll(pid: str, request: Request, vor: Vor = None, limit: Limit = 50,
                      conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_VERLAUF)
    arten = _arten(request, z.rolle)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "verlauf")
    return _seite(protokoll.lesen(conn, project_id=pid, vor=vor, limit=limit, arten=arten,
                                  ohne_intern=not _sicht(request, conn, z).intern), limit)


@router.get("/projekte/{pid}/protokoll.jsonl", summary="Verlauf eines Projekts als JSON Lines",
            response_class=JsonLinesAntwort, responses=FEHLER_ALS_JSON)
def projekt_protokoll_jsonl(pid: str, request: Request,
                            conn: sqlite3.Connection = Depends(db)) -> Response:
    z = projekt(request, conn, pid, mindestens="bearbeiter", text=NUR_VERLAUF)
    arten = _arten(request, z.rolle)
    lesezugriff.erfasse_anfrage(request, conn, z, "export", "verlauf")
    return _jsonl(protokoll.jsonl(conn, project_id=pid, arten=arten,
                                  ohne_intern=not _sicht(request, conn, z).intern),
                  f"verlauf-{pid}")


def _projekt_filter(projekt_id: str | None) -> str | None:
    if projekt_id is not None and not PROJECT_ID_RE.fullmatch(projekt_id):
        raise Ungueltig("Projekt-ID ungültig.")
    return projekt_id


@router.get("/protokoll", summary="Gesamtes Protokoll inkl. Konten und Rechte (Admin)")
def protokoll_alles(request: Request, vor: Vor = None, limit: Limit = 50,
                    projekt_id: Annotated[str | None, Query(alias="projekt")] = None,
                    conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    admin(request, conn)
    pid = _projekt_filter(projekt_id)
    return _seite(protokoll.lesen(conn, project_id=pid, vor=vor, limit=limit), limit)


@router.get("/protokoll.jsonl", summary="Gesamtes Protokoll als JSON Lines (Admin)",
            response_class=JsonLinesAntwort, responses=FEHLER_ALS_JSON)
def protokoll_alles_jsonl(request: Request,
                          projekt_id: Annotated[str | None, Query(alias="projekt")] = None,
                          conn: sqlite3.Connection = Depends(db)) -> Response:
    admin(request, conn)
    return _jsonl(protokoll.jsonl(conn, project_id=_projekt_filter(projekt_id)), "protokoll")
