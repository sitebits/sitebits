"""/api/v1/projekte/{pid}/pois|messungen: Lesen für alle mit Recht, Schreiben für
Bearbeiter und Admin (CSRF), optimistische Sperre über `version`, DELETE mit ?version=N.

POIs zusätzlich für die Rolle Rückmelder, eingeschränkt auf eigene Einträge der Kategorie
„Rückmeldung“ (geprüft in `inhalte` innerhalb der Schreib-Transaktion). Messungen bleiben
Bearbeitern vorbehalten.

KI-Vorschläge (G7): Über einen Agent-Token Geschriebenes wird Vorschlag; `annehmen`/`verwerfen`
nur durch Menschen (Sitzung oder normaler Token). Lesen zeigt offene Vorschläge anderer erst mit
`?vorschlaege=1` und nur Rollen ab Bearbeiter; ohne Parameter wie bisher (eigene sichtbar)."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import faden, inhalte, konten, lesezugriff
from .api_modelle import Messung, MessungListe, Poi, PoiListe, antwort
from .web import KI_PRUEFT_NICHT, SQLITE_GANZZAHL_MAX, csrf, db, kanal, ki, token_name
from .zugriff import RUECKMELDER, Zugriff, projekt

router = APIRouter(prefix="/api/v1/projekte/{pid}")


class NeuIn(BaseModel):
    id: str | None = Field(default=None, max_length=64)
    daten: dict[str, Any]


class AenderungIn(BaseModel):
    daten: dict[str, Any]
    version: int = Field(ge=1, le=SQLITE_GANZZAHL_MAX)


class PruefIn(BaseModel):
    version: int = Field(ge=1, le=SQLITE_GANZZAHL_MAX)


def _eigen(z: Zugriff) -> inhalte.Eigentum | None:
    return inhalte.Eigentum(z.nutzer.id) if z.rolle == RUECKMELDER else None


def _routen(tabelle: inhalte.Tabelle) -> None:
    # Nur die POI-Routen öffnen sich für Rückmelder; Messungen nie.
    rueckmelder = tabelle == "pois"
    wer = "Bearbeiter; Rückmelder nur eigene Rückmeldungen" if rueckmelder else "Bearbeiter"
    # Nur Beschreibung der Antworten (api_modelle), kein Verhalten.
    einer = Poi if rueckmelder else Messung
    liste = PoiListe if rueckmelder else MessungListe

    @router.get(f"/{tabelle}", operation_id=f"{tabelle}_lesen",
                summary=f"{tabelle}: alle nicht gelöschten Einträge", responses=antwort(liste))
    def lesen(pid: str, request: Request,
              vorschlaege: Annotated[bool, Query()] = False,
              conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
        z = projekt(request, conn, pid)
        lesezugriff.erfasse_anfrage(request, conn, z, "api", tabelle)
        sicht = inhalte.Vorschlagssicht(
            alle=vorschlaege and konten.mindestens(z.rolle, "bearbeiter"), nutzer_id=z.nutzer.id)
        eintraege = [e.als_json() for e in inhalte.liste(conn, tabelle, pid, sicht=sicht)]
        if tabelle == "pois" and faden.aktiviert(conn, pid):
            # Befund-Fäden an: je POI zusätzlich der Stand (Status, Zuständig, Frist). Zwei
            # Lesevorgänge: ein dazwischen gelöschter POI fällt weg statt eines KeyError.
            st = faden.staende(conn, pid)
            eintraege = [{**e, "stand": st[e["id"]]} for e in eintraege if e["id"] in st]
        return JSONResponse({tabelle: eintraege})

    @router.post(f"/{tabelle}", status_code=201, dependencies=[Depends(csrf)],
                 operation_id=f"{tabelle}_anlegen", summary=f"{tabelle}: anlegen ({wer})",
                 responses=antwort(einer, 201))
    def anlegen(pid: str, body: NeuIn, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
        z = projekt(request, conn, pid, mindestens="bearbeiter", rueckmelder=rueckmelder)
        e = inhalte.anlegen(conn, tabelle, pid, body.id, inhalte.pruefen(tabelle, body.daten),
                            nutzer_id=z.nutzer.id, revision=z.karte.revision,
                            kanal=kanal(request), token_name=token_name(request),
                            eigen=_eigen(z), ki=ki(request))
        return JSONResponse(e.als_json(), status_code=201)

    @router.patch(f"/{tabelle}/{{eid}}", dependencies=[Depends(csrf)],
                  operation_id=f"{tabelle}_aendern",
                  summary=f"{tabelle}: ändern ({wer}; version = zuletzt gelesene)",
                  responses=antwort(einer))
    def aendern(pid: str, eid: str, body: AenderungIn, request: Request,
                conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
        z = projekt(request, conn, pid, mindestens="bearbeiter", rueckmelder=rueckmelder)
        e = inhalte.aendern(conn, tabelle, pid, eid, body.daten, body.version,
                            nutzer_id=z.nutzer.id, kanal=kanal(request),
                            token_name=token_name(request), eigen=_eigen(z), ki=ki(request))
        return JSONResponse(e.als_json())

    @router.delete(f"/{tabelle}/{{eid}}", status_code=204, dependencies=[Depends(csrf)],
                   operation_id=f"{tabelle}_loeschen",
                   summary=f"{tabelle}: löschen (weich, {wer}; ?version=N; 202 = per"
                           " KI-Agent nur vorgeschlagen)",
                   responses=antwort(einer, 202))
    def loeschen(pid: str, eid: str, request: Request,
                 version: Annotated[int, Query(ge=1, le=SQLITE_GANZZAHL_MAX)],
                 conn: sqlite3.Connection = Depends(db)) -> Response:
        z = projekt(request, conn, pid, mindestens="bearbeiter", rueckmelder=rueckmelder)
        e = inhalte.loeschen(conn, tabelle, pid, eid, version, nutzer_id=z.nutzer.id,
                             kanal=kanal(request), token_name=token_name(request),
                             eigen=_eigen(z), ki=ki(request))
        if e is not None:
            return JSONResponse(e.als_json(), status_code=202)
        return Response(status_code=204)

    def _pruefen(aktion: str, pid: str, eid: str, body: PruefIn, request: Request,
                 conn: sqlite3.Connection) -> Response:
        z = projekt(request, conn, pid, mindestens="bearbeiter", rueckmelder=rueckmelder)
        if ki(request):
            raise StarletteHTTPException(status_code=403, detail=KI_PRUEFT_NICHT)
        f = inhalte.annehmen if aktion == "annehmen" else inhalte.verwerfen
        e = f(conn, tabelle, pid, eid, body.version, nutzer_id=z.nutzer.id,
              kanal=kanal(request), token_name=token_name(request), eigen=_eigen(z))
        return Response(status_code=204) if e is None else JSONResponse(e.als_json())

    @router.post(f"/{tabelle}/{{eid}}/annehmen", dependencies=[Depends(csrf)],
                 operation_id=f"{tabelle}_annehmen",
                 summary=f"{tabelle}: KI-Vorschlag annehmen ({wer}; nicht per KI-Agent;"
                         " 204 = Löschvorschlag ausgeführt)",
                 responses=antwort(einer))
    def annehmen(pid: str, eid: str, body: PruefIn, request: Request,
                 conn: sqlite3.Connection = Depends(db)) -> Response:
        return _pruefen("annehmen", pid, eid, body, request, conn)

    @router.post(f"/{tabelle}/{{eid}}/verwerfen", dependencies=[Depends(csrf)],
                 operation_id=f"{tabelle}_verwerfen",
                 summary=f"{tabelle}: KI-Vorschlag verwerfen ({wer}; nicht per KI-Agent;"
                         " 204 = Vorschlag verworfen)",
                 responses=antwort(einer))
    def verwerfen(pid: str, eid: str, body: PruefIn, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> Response:
        return _pruefen("verwerfen", pid, eid, body, request, conn)


for _tabelle in inhalte.TABELLEN:
    _routen(_tabelle)


@router.get("/zu-pruefen", operation_id="zu_pruefen",
            summary="Offene KI-Vorschläge: POIs, Messungen (auch Löschvorschläge) und Faden"
            " (ab Bearbeiter)")
def zu_pruefen(pid: str, request: Request,
               conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    z = projekt(request, conn, pid, mindestens="bearbeiter")
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "zu_pruefen")
    return JSONResponse({
        **{t: [e.als_json() for e in inhalte.offene_vorschlaege(conn, t, pid)]
           for t in inhalte.TABELLEN},
        "faden": faden.offene_vorschlaege(conn, pid),
    })
