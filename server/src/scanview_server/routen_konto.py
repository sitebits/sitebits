"""/api/v1/konto/* — nur Cookie, nie Bearer (ADR 0008: der Pfad ist
nicht `bearer_pfad`, ein mitgeschickter Bearer-Header wird schlicht ignoriert, es zählt allein das
Sitzungs-Cookie). Eigene API-Tokens verwalten (anlegen/auflisten/widerrufen, apitoken.py) und die
Einwilligung zum Klarnamen in der Git-Historie (ADR 0011, users.git_name_zeigen)."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import apitoken, protokoll
from .db import transaktion
from .web import SQLITE_GANZZAHL_MAX, Ungueltig, csrf, db, frisch_angemeldet
from .zugriff import NICHT_GEFUNDEN, angemeldet

router = APIRouter(prefix="/api/v1")

Tid = Annotated[int, Path(ge=1, le=SQLITE_GANZZAHL_MAX)]

# Befund B-TE-M1: ein Token ist ein Zugangsmittel wie ein Passkey, die Einwilligung
# gibt den Klarnamen unwiderruflich in exportierte Commits frei — beides nur mit frischer
# Anmeldung (web.frisch_angemeldet, dieselbe Schwelle wie „weiteres Gerät hinzufügen"), damit ein
# erbeutetes Cookie allein weder einen langlebigen Token noch die Einwilligung hergibt.
# Auflisten, Widerrufen und Ausschalten bleiben ohne frische Anmeldung.
WOZU_TOKEN = "einen API-Zugang anzulegen"
WOZU_GIT_NAME = "die Einwilligung einzuschalten"


class TokenIn(BaseModel):
    name: str
    umfang: str
    tage: int = 90
    # G7: nur ein echter JSON-Wahrheitswert, Standard „kein Agent“.
    agent: StrictBool = False


class KontoIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    # Nur ein echter JSON-Wahrheitswert (Befund B-FUZZ-2): die Einwilligung ist unwiderruflich.
    git_name_zeigen: StrictBool = Field(alias="gitNameZeigen")


def _nicht_gefunden() -> StarletteHTTPException:
    return StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)


def _token_json(t: apitoken.TokenInfo) -> dict[str, Any]:
    return {
        "id": t.id, "name": t.name, "umfang": t.umfang, "erstelltAm": t.erstellt_am,
        "laeuftAb": t.laeuft_ab, "zuletztBenutzt": t.zuletzt_benutzt,
        "agent": t.agent,
    }


def _git_name_zeigen(conn: sqlite3.Connection, uid: int) -> bool:
    row = conn.execute("SELECT git_name_zeigen FROM users WHERE id=?", (uid,)).fetchone()
    return bool(row["git_name_zeigen"]) if row else False


@router.get("/konto/tokens", summary="Eigene API-Tokens (nie der Wert oder Hash)")
def tokens_liste(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = angemeldet(request, conn)
    return JSONResponse(
        {"tokens": [_token_json(t) for t in apitoken.liste(conn, n.id)]},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/konto/tokens", status_code=201, dependencies=[Depends(csrf)],
             summary="Neuen API-Token anlegen — der Wert steht nur in dieser Antwort")
def token_anlegen(body: TokenIn, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = frisch_angemeldet(request, conn, WOZU_TOKEN)
    try:
        roh, info = apitoken.erzeugen(conn, n.id, body.name, body.umfang, body.tage,
                                       agent=body.agent)
    except ValueError as e:
        raise Ungueltig(str(e)) from e
    return JSONResponse(
        {"token": roh, "info": _token_json(info)}, status_code=201,
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/konto/tokens/{tid}", status_code=204, dependencies=[Depends(csrf)],
               summary="Eigenen Token widerrufen (fremd/unbekannt/schon abgelaufen -> 404)")
def token_widerrufen(tid: Tid, request: Request,
                     conn: sqlite3.Connection = Depends(db)) -> Response:
    n = angemeldet(request, conn)
    # Nur widerrufbar, was `liste` noch zeigt — ein bereits abgelaufener
    # eigener Token ist wie ein fremder/unbekannter (404), nicht mehr eigens widerrufbar.
    sichtbar = {t.id for t in apitoken.liste(conn, n.id)}
    if tid not in sichtbar or not apitoken.widerrufen(conn, n.id, tid, von=n.id, kanal="web"):
        raise _nicht_gefunden()
    return Response(status_code=204)


@router.get("/konto", summary="Git-Namens-Einwilligung lesen (E-G1)")
def konto_lesen(request: Request, conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = angemeldet(request, conn)
    return JSONResponse({"gitNameZeigen": _git_name_zeigen(conn, n.id)})


@router.patch("/konto", dependencies=[Depends(csrf)],
              summary="Git-Namens-Einwilligung setzen (E-G1) — gilt ab dem nächsten Export "
                      "für alle noch nicht exportierten Bearbeitungen")
def konto_aendern(body: KontoIn, request: Request,
                  conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = angemeldet(request, conn)
    neu = body.git_name_zeigen
    if neu and not _git_name_zeigen(conn, n.id):
        frisch_angemeldet(request, conn, WOZU_GIT_NAME)
    with transaktion(conn):
        alt = _git_name_zeigen(conn, n.id)
        # Unveränderter Wert schreibt weder UPDATE noch Ereignis
        # (wie verwaltung.recht_setzen bei unveränderter Rolle) — sonst würde jedes erneute
        # Abspeichern desselben Schalters (z. B. ein Doppelklick im Portal) ins unveränderliche
        # Protokoll wachsen, ohne dass sich etwas geändert hätte. Die Antwort bleibt 200.
        if alt != neu:
            conn.execute("UPDATE users SET git_name_zeigen=? WHERE id=?", (int(neu), n.id))
            protokoll.ereignis(
                conn, kanal="web", nutzer_id=n.id, art="konto.git_name", objekt_id=str(n.id),
                vorher={"gitNameZeigen": alt}, nachher={"gitNameZeigen": neu},
            )
    return JSONResponse({"gitNameZeigen": neu})
