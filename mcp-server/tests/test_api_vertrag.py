"""Vertrag MCP ↔ OpenAPI aus einer Quelle (ADR 0018).

1. Abdeckung: Jede Operation der öffentlichen OpenAPI hat ein MCP-Werkzeug (endpunkte.json) oder
   eine Begründung (ohne_werkzeug.json), nie beides und nie keins.
2. Körper: Was ein Werkzeug an die API schickt (Beispielaufrufe aus test_werkzeuge, dort gegen die
   Leitung geprüft), ist gültig gegen das Request-Schema der Operation in der OpenAPI.
"""
from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource
from referencing.jsonschema import DRAFT202012
from test_werkzeuge import AUFRUFE, TABELLE

REPO = Path(__file__).resolve().parents[2]
OPENAPI: dict[str, Any] = json.loads(
    (REPO / "docs/public/api/openapi.json").read_text(encoding="utf-8"))
OHNE: list[dict[str, str]] = json.loads(
    resources.files("scanview_mcp").joinpath("ohne_werkzeug.json").read_text(encoding="utf-8"))
METHODEN = ("get", "post", "put", "patch", "delete", "head")
WURZEL = "urn:sitebits:openapi"


def _operationen() -> set[tuple[str, str]]:
    return {(m.upper(), p) for p, ms in OPENAPI["paths"].items() for m in ms if m in METHODEN}


def test_jede_operation_hat_werkzeug_oder_begruendung() -> None:
    mit = {(e["methode"], e["pfad"]) for e in TABELLE.values()}
    ohne = {(e["methode"], e["pfad"]) for e in OHNE}
    assert not mit & ohne, f"Werkzeug und Begründung zugleich: {sorted(mit & ohne)}"
    fehlt = _operationen() - mit - ohne
    assert not fehlt, f"ohne Werkzeug und ohne Begründung (ohne_werkzeug.json): {sorted(fehlt)}"
    veraltet = (mit | ohne) - _operationen()
    assert not veraltet, f"nicht (mehr) in der OpenAPI: {sorted(veraltet)}"
    assert all(e["grund"].strip() for e in OHNE)
    assert len(ohne) == len(OHNE), "doppelte Zeile in ohne_werkzeug.json"


def _validator(werkzeug: str) -> Draft202012Validator | None:
    methode, pfad = TABELLE[werkzeug]["methode"].lower(), TABELLE[werkzeug]["pfad"]
    inhalt = OPENAPI["paths"][pfad][methode].get("requestBody", {}).get("content", {})
    schema = inhalt.get("application/json", {}).get("schema")
    if schema is None:
        return None
    registry: Registry[Any] = Registry().with_resource(
        WURZEL, Resource(contents=OPENAPI, specification=DRAFT202012))
    return Draft202012Validator({"$ref": WURZEL + "#/" + "/".join(
        ["paths", pfad.replace("~", "~0").replace("/", "~1"), methode, "requestBody", "content",
         "application~1json", "schema"])}, registry=registry)


MIT_KOERPER = sorted(n for n, (_a, koerper, _q) in AUFRUFE.items() if koerper is not None)


@pytest.mark.parametrize("werkzeug", MIT_KOERPER)
def test_gesendeter_koerper_passt_zum_request_schema(werkzeug: str) -> None:
    v = _validator(werkzeug)
    if v is None:
        pytest.skip("Operation ohne JSON-Körper (z. B. Datei-Upload)")
    fehler = [f"{'/'.join(map(str, e.absolute_path))}: {e.message}"
              for e in v.iter_errors(AUFRUFE[werkzeug][1])]
    assert not fehler, f"{werkzeug} schickt, was die API nicht annimmt: {fehler}"


def test_validator_lehnt_falschen_koerper_ab() -> None:
    # Grenze: `daten` ist im Schema ein freies Objekt; geprüft wird die Hülle (Pflichtfelder,
    # Typen, version). Den Inhalt von `daten` prüfen die Server-Tests gegen die echte App.
    v = _validator("poi_anlegen")
    assert v is not None
    assert list(v.iter_errors({"id": 5}))
    w = _validator("poi_aendern")
    assert w is not None
    assert list(w.iter_errors({"daten": {"title": "x"}, "version": "zwei"}))
