"""Öffentlich, ohne Anmeldung (wer an der Anmeldung scheitert, muss eine
Barriere melden können; Impressum und Datenschutzhinweise gehören auf die Anmeldeseite). Keine
Eingabe, kein Personenbezug, außerhalb von /api/v1 (nicht Teil der OpenAPI und des
MCP-Vertrags)."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .barrierefreiheit import produkt_einschraenkungen
from .web import DIENST

router = APIRouter()
_PRODUKT = produkt_einschraenkungen()  # beim Import: eine kaputte Vorlage verhindert den Start


@router.get("/api/barrierefreiheit", include_in_schema=False)
async def barrierefreiheit(request: Request) -> JSONResponse:
    s = request.app.state.settings
    return JSONResponse({"dienst": DIENST, **s.barrierefreiheit.als_json(),
                         "produkt_einschraenkungen": _PRODUKT, **s.rechtliches.als_json()})
