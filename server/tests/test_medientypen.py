"""Gesendeter Content-Type passt zum beschriebenen — für die Routen mit eigener Antwortklasse
(CSV, XLSX, JSON Lines, HTML). Dieselbe Prüfung wie `content_type_conformance` im Fuzzing
(Schemathesis): Antwort zum Statuscode suchen (genau, dann Bereich wie `4XX`, dann `default`),
Haupt-/Untertyp ohne Parameter wie `charset` vergleichen. Geprüft wird die Laufzeit-OpenAPI
(`GET /api/v1/openapi.json`), denn die liest das Fuzzing. Erfolg und Fehler je Operation."""
from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient
from jobswelt import BASIS, Welt, welt  # noqa: F401
from uploadwelt import Welt as UploadWelt
from uploadwelt import welt as upload_welt

PROJEKT = "/api/v1/projekte/ost"
STAND = "/api/v1/uploads/{uid}/dateien/{nr}"
# Fixture unter zweitem Namen (beide Welten heißen `welt`).
uploads = upload_welt
BERICHT = BASIS + "/{aid}/bericht"
OPS = {
    "export": "/api/v1/projekte/{pid}/export.csv",
    "xlsx": "/api/v1/projekte/{pid}/maengelliste.xlsx",
    "verlauf": "/api/v1/projekte/{pid}/protokoll.jsonl",
    "lese_projekt": "/api/v1/projekte/{pid}/lesezugriffe.jsonl",
    "lese_alle": "/api/v1/lesezugriffe.jsonl",
    "protokoll": "/api/v1/protokoll.jsonl",
    "bericht": "/api/v1/projekte/{pid}/auswertungen/{aid}/bericht",
}


def _typ(wert: str) -> str:
    return wert.split(";", 1)[0].strip().lower()


def _beschrieben(schema: dict[str, Any], pfad: str, status: int) -> list[str]:
    antworten: dict[str, Any] = schema["paths"][pfad]["get"]["responses"]
    for schluessel in (str(status), f"{str(status)[0]}XX", "default"):
        if schluessel in antworten:
            return [_typ(t) for t in antworten[schluessel].get("content", {})]
    raise AssertionError(f"{pfad}: Status {status} nicht beschrieben")


def _pruefe(schema: dict[str, Any], op: str, r: httpx.Response, status: int) -> None:
    assert r.status_code == status, (op, r.status_code, r.text[:200])
    gesendet = _typ(r.headers["content-type"])
    erlaubt = _beschrieben(schema, OPS[op], status)
    assert gesendet in erlaubt, (OPS[op], status, gesendet, erlaubt)


def _schema(c: TestClient) -> dict[str, Any]:
    r = c.get("/api/v1/openapi.json")
    assert r.status_code == 200, r.text
    daten: dict[str, Any] = r.json()
    return daten


def test_dateirouten_typ_wie_beschrieben(welt: Welt) -> None:  # noqa: F811
    admin = welt.c["admin"]
    leitung = welt.c["leitung"]
    schema = _schema(admin)
    assert set(OPS.values()) <= set(schema["paths"])

    # Erfolg
    _pruefe(schema, "export", leitung.get(PROJEKT + "/export.csv"), 200)
    _pruefe(schema, "xlsx", leitung.get(PROJEKT + "/maengelliste.xlsx"), 200)
    _pruefe(schema, "verlauf", leitung.get(PROJEKT + "/protokoll.jsonl"), 200)
    _pruefe(schema, "lese_projekt", admin.get(PROJEKT + "/lesezugriffe.jsonl"), 200)
    _pruefe(schema, "lese_alle", admin.get("/api/v1/lesezugriffe.jsonl"), 200)
    _pruefe(schema, "protokoll", admin.get("/api/v1/protokoll.jsonl"), 200)

    # Fehler: anonym 401, Betrachter 403, fremdes Projekt 404, ungültige Eingabe 422.
    anonym, betr, fremd = welt.c["anonym"], welt.c["betr"], welt.c["fremd"]
    _pruefe(schema, "export", anonym.get(PROJEKT + "/export.csv"), 401)
    _pruefe(schema, "export", betr.get(PROJEKT + "/export.csv"), 403)
    _pruefe(schema, "xlsx", fremd.get(PROJEKT + "/maengelliste.xlsx"), 404)
    _pruefe(schema, "verlauf", betr.get(PROJEKT + "/protokoll.jsonl"), 403)
    _pruefe(schema, "lese_projekt", leitung.get(PROJEKT + "/lesezugriffe.jsonl"), 404)
    _pruefe(schema, "lese_alle", admin.get("/api/v1/lesezugriffe.jsonl?nutzer=0"), 422)
    _pruefe(schema, "protokoll", admin.get("/api/v1/protokoll.jsonl?projekt=..%2F"), 422)
    _pruefe(schema, "protokoll", leitung.get("/api/v1/protokoll.jsonl"), 404)


def test_bericht_typ_wie_beschrieben(welt: Welt) -> None:  # noqa: F811
    schema = _schema(welt.c["admin"])
    leitung = welt.c["leitung"]
    aid = welt.starten()
    # Noch nicht fertig: 409 als JSON.
    _pruefe(schema, "bericht", leitung.get(BERICHT.format(aid=aid)), 409)
    welt.rechnen()
    ok = leitung.get(BERICHT.format(aid=aid))
    _pruefe(schema, "bericht", ok, 200)
    assert ok.headers["content-type"] == "text/html; charset=utf-8"
    _pruefe(schema, "bericht", welt.c["fremd"].get(BERICHT.format(aid=aid)), 404)
    _pruefe(schema, "bericht", welt.c["anonym"].get(BERICHT.format(aid=aid)), 401)


def test_fehler_als_json_beschrieben(welt: Welt) -> None:  # noqa: F811
    """Die 4XX-Beschreibung dieser Routen verweist auf das Fehler-Modell, das im Dokument steht."""
    schema = _schema(welt.c["admin"])
    assert "Fehler" in schema["components"]["schemas"]
    for pfad in OPS.values():
        inhalt = schema["paths"][pfad]["get"]["responses"]["4XX"]["content"]
        assert inhalt == {"application/json": {"schema": {"$ref": "#/components/schemas/Fehler"}}}


def test_head_fehler_ohne_koerper_beschrieben(uploads: UploadWelt) -> None:
    """HEAD trägt nie einen Körper: 4XX ohne `content` beschrieben, Antwort tatsächlich leer —
    sonst erwartete das Fuzzing (response_schema_conformance) ein JSON-Fehlerobjekt."""
    admin = uploads.c["admin"]
    schema = _schema(admin)
    heads = {p: o["head"] for p, o in schema["paths"].items() if "head" in o}
    assert STAND in heads
    for pfad, op in heads.items():
        assert "content" not in op["responses"]["4XX"], pfad
    uid = uploads.anlegen(admin, {"a.ply": b"x" * 10}).json()["id"]
    ok = admin.head(STAND.format(uid=uid, nr=0))
    assert ok.status_code == 204 and ok.content == b""
    for c, pfad, status in ((admin, STAND.format(uid="0" * 32, nr=0), 404),
                            (admin, STAND.format(uid=uid, nr=99), 404),
                            (uploads.c["anonym"], STAND.format(uid=uid, nr=0), 401)):
        r = c.head(pfad)
        assert r.status_code == status and r.content == b"", (pfad, r.status_code)
