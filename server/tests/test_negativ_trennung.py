"""Negativtests Projekttrennung (IDOR, Kategorie NT-TRENNUNG).

Ein fremdes, existierendes Projekt muss auf jeder projektbezogenen Route genau so antworten wie
ein Projekt, das es gar nicht gibt: gleicher Status, gleicher Körper, gleicher Content-Type — sonst
verrät der Unterschied, welche Projekt-IDs existieren. Geprüft mit Cookie (Bearbeiter und
Betrachter) und mit Schreib-Token; ein Betrachter bekommt auf fremde Projekte 404, nicht 403.
Nichts davon darf die Datenbank verändern."""

from __future__ import annotations

import sqlite3
from typing import Any

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import apitoken, db

FREMD = "kirche"      # existiert im Katalog, aber ohne Recht
GIBTS_NICHT = "gibts-nicht"

POI: dict[str, Any] = {"title": "Tür Nord", "category": "Zugang", "note": "",
                       "position": [1.5, -2.25, 3.0], "station": "S001"}

# (Methode, Pfad mit {pid}, JSON-Körper) — jede projektbezogene Route der API v1.
ROUTEN: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/v1/projekte/{pid}", None),
    ("PATCH", "/api/v1/projekte/{pid}", {"titel": "neu", "version": 0}),
    ("GET", "/api/v1/projekte/{pid}/pois", None),
    ("POST", "/api/v1/projekte/{pid}/pois", {"id": "P-1", "daten": POI}),
    ("PATCH", "/api/v1/projekte/{pid}/pois/P-1", {"daten": POI, "version": 1}),
    ("DELETE", "/api/v1/projekte/{pid}/pois/P-1?version=1", None),
    ("GET", "/api/v1/projekte/{pid}/messungen", None),
    ("GET", "/api/v1/projekte/{pid}/export.csv", None),
    ("GET", "/api/v1/projekte/{pid}/export.json", None),
    ("GET", "/api/v1/projekte/{pid}/protokoll", None),
    ("GET", "/api/v1/projekte/{pid}/protokoll.jsonl", None),
    ("GET", "/api/v1/projekte/{pid}/pois/P-1/faden", None),
    ("POST", "/api/v1/projekte/{pid}/pois/P-1/faden", {"art": "kommentar", "text": "x"}),
    ("POST", "/api/v1/projekte/{pid}/pois/P-1/faden/0123456789abcdef/zurueckziehen", None),
    ("GET", "/api/v1/projekte/{pid}/befunde", None),
    ("POST", "/api/v1/statistik/{pid}", {"oeffnen": 1}),
]


def _zustand(c: TestClient) -> tuple[int, ...]:
    conn: sqlite3.Connection = db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        return tuple(int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
                     for t in ("pois", "messungen", "ereignisse", "project_meta", "statistik_tag",
                               "befund_eintraege"))
    finally:
        conn.close()


def _antwort(c: TestClient, methode: str, pfad: str, koerper: dict[str, Any] | None,
             kopf: dict[str, str]) -> tuple[int, bytes, str]:
    r = c.request(methode, pfad, headers=kopf, json=koerper)
    return r.status_code, r.content, r.headers.get("content-type", "")


def _gleich_wie_nicht_vorhanden(c: TestClient, kopf: dict[str, str]) -> None:
    vorher = _zustand(c)
    for methode, pfad, koerper in ROUTEN:
        fremd = _antwort(c, methode, pfad.format(pid=FREMD), koerper, kopf)
        nicht_da = _antwort(c, methode, pfad.format(pid=GIBTS_NICHT), koerper, kopf)
        assert fremd[0] == 404, (methode, pfad, fremd)
        assert fremd == nicht_da, (methode, pfad, fremd, nicht_da)
    assert _zustand(c) == vorher


@pytest.mark.parametrize("rolle", ["bearbeiter", "betrachter"])
def test_fremdes_projekt_wie_nicht_vorhanden_mit_cookie(
    mit_paketen: TestClient, rolle: str,  # noqa: F811
) -> None:
    _, c = person(mit_paketen, f"{rolle}@test", grants={"ost": rolle})
    # Gegenprobe: das eigene Projekt ist erreichbar, der Test misst also nicht nur 404 überall.
    assert c.get("/api/v1/projekte/ost").status_code == 200
    _gleich_wie_nicht_vorhanden(c, csrf())


def test_fremdes_projekt_wie_nicht_vorhanden_mit_schreib_token(
    mit_paketen: TestClient,  # noqa: F811
) -> None:
    n, _ = person(mit_paketen, "tok@test", grants={"ost": "bearbeiter"})
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    try:
        roh, _info = apitoken.erzeugen(conn, n.id, "t", "schreiben", 30)
    finally:
        conn.close()
    c = TestClient(mit_paketen.app, base_url=ORIGIN)
    kopf = {"Authorization": f"Bearer {roh}"}
    assert c.get("/api/v1/projekte/ost/pois", headers=kopf).status_code == 200
    # Statistik ist kein Bearer-Pfad (web.bearer_pfad): dort zählt ein Token nie, die Route bleibt
    # hier außen vor (ihr Verhalten mit Token prüft test_api_bearer).
    vorher = _zustand(c)
    for methode, pfad, koerper in ROUTEN:
        if "/statistik/" in pfad:
            continue
        fremd = _antwort(c, methode, pfad.format(pid=FREMD), koerper, kopf)
        nicht_da = _antwort(c, methode, pfad.format(pid=GIBTS_NICHT), koerper, kopf)
        assert fremd[0] == 404, (methode, pfad, fremd)
        assert fremd == nicht_da, (methode, pfad, fremd, nicht_da)
    assert _zustand(c) == vorher
