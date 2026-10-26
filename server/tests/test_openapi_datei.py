"""Öffentliche OpenAPI-Datei (`docs/public/api/openapi.json`) ↔ App.

- Drift: Die versionierte Datei ist byte-gleich mit einer frischen Erzeugung aus der App.
- Abgrenzung: Sie enthält nur, was ein API-Token erreicht — jede Operation steht auch in der
  Laufzeit-Sicht eines Tokens (`GET /api/v1/openapi.json`), keine ist per Token strukturell
  gesperrt, und jede bewusst ausgelassene Verwaltungsoperation (`NUR_BROWSER`) wird per Token
  wirklich abgelehnt.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jobswelt import Welt, welt  # noqa: F401

from scanview_server import openapi_v1
from scanview_server.zugriff import NICHT_PER_TOKEN

WURZEL = Path(__file__).resolve().parent.parent.parent
DATEI = WURZEL / "docs" / "public" / "api" / "openapi.json"
ENDPUNKTE = WURZEL / "mcp-server" / "src" / "scanview_mcp" / "endpunkte.json"
WERTE = {"pid": "ost", "eid": "P-gibtesnicht", "aid": "0123456789abcdef", "uid": "999",
         "gid": "999", "lid": "gibtesnicht", "ebene": "panos", "schluessel": "gibtesnicht",
         "nr": "0"}


def _datei() -> dict[str, Any]:
    daten: dict[str, Any] = json.loads(DATEI.read_text(encoding="utf-8"))
    return daten


def _ops(pfade: dict[str, Any]) -> set[tuple[str, str]]:
    return {(m, p) for p, ops in pfade.items() for m in ops}


def _konkret(pfad: str) -> str:
    for k, v in WERTE.items():
        pfad = pfad.replace("{" + k + "}", v)
    assert "{" not in pfad, pfad
    return pfad


def test_datei_passt_zur_app() -> None:
    assert DATEI.read_text(encoding="utf-8") == openapi_v1.erzeugen(), (
        f"{DATEI} passt nicht mehr zur App. Neu erzeugen (Repo-Wurzel): {openapi_v1.ERZEUGER}"
        " — danach den Client an neue Operationen anpassen (OPERATIONEN in src/index.ts)")


def test_nur_v1_nur_token_ohne_cookie() -> None:
    d = _datei()
    assert d["paths"] and all(p.startswith("/api/v1/") for p in d["paths"])
    assert d["components"]["securitySchemes"] == {"bearerAuth": {"type": "http",
                                                                 "scheme": "bearer"}}
    assert d["security"] == [{"bearerAuth": []}]
    for verboten in ("/api/v1/admin", "/api/v1/konto", "/api/v1/statistik", "/api/v1/protokoll",
                     "/api/v1/lesezugriffe", "/api/v1/openapi.json"):
        assert not any(p.startswith(verboten) for p in d["paths"]), verboten
    assert not any("lesezugriffe" in p for p in d["paths"])
    assert not _ops(d["paths"]) & openapi_v1.NUR_BROWSER


def test_teilmenge_der_laufzeit_sicht_eines_tokens(welt: Welt) -> None:  # noqa: F811
    laufzeit = welt.token("leitung").get("/api/v1/openapi.json").json()["paths"]
    datei = _ops(_datei()["paths"])
    assert datei <= _ops(laufzeit)
    # Was die Laufzeit-Sicht mehr hat, ist genau die Verwaltung nur im Browser.
    assert _ops(laufzeit) - datei == openapi_v1.NUR_BROWSER


def test_mcp_werkzeuge_liegen_in_der_datei() -> None:
    ops = _ops(_datei()["paths"])
    for e in json.loads(ENDPUNKTE.read_text(encoding="utf-8")):
        assert (e["methode"].lower(), e["pfad"]) in ops, e["werkzeug"]


@pytest.mark.parametrize(("methode", "pfad"), sorted(openapi_v1.NUR_BROWSER))
def test_ausgelassene_verwaltung_ist_per_token_gesperrt(welt: Welt, methode: str,  # noqa: F811
                                                        pfad: str) -> None:
    # Projektleitung mit Schreib-Token: die stärkste Token-Rolle im Projekt.
    r = welt.token("leitung").request(methode.upper(), _konkret(pfad),
                                      json={} if methode != "get" else None)
    assert r.status_code == 403 and r.json()["fehler"] == NICHT_PER_TOKEN, (pfad, r.text)


def test_keine_aufgenommene_operation_ist_per_token_gesperrt(welt: Welt) -> None:  # noqa: F811
    c = welt.token("leitung")
    for methode, pfad in sorted(_ops(_datei()["paths"])):
        r = c.request(methode.upper(), _konkret(pfad), json={} if methode != "get" else None)
        if r.headers.get("content-type", "").startswith("application/json") and r.content:
            fehler = r.json().get("fehler") if isinstance(r.json(), dict) else None
            assert fehler != NICHT_PER_TOKEN, (methode, pfad)
        # 401 hieße: der Pfad nimmt keinen Bearer an.
        assert r.status_code != 401, (methode, pfad, r.text)
