import re
import sqlite3
import threading
from typing import Any

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import db, inhalte, protokoll
from scanview_server.web import Konflikt, Ungueltig

POI: dict[str, Any] = {"title": "Tür Nord", "category": "Zugang", "note": "",
                       "position": [1.5, -2.25, 3.0], "station": "S001"}
STRECKE: dict[str, Any] = {"kind": "distance", "points": [[0, 0, 0], [3, 4, 0]]}
FLAECHE: dict[str, Any] = {"kind": "area", "points": [[0, 0, 0], [4, 0, 0], [4, 3, 0]],
                           "areaM2": 6.0, "planarityM": 0.0}
BASIS = "/api/v1/projekte/ost/pois"
MESS = "/api/v1/projekte/ost/messungen"


def _conn(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def test_poi_anlegen_lesen_aendern_loeschen(mit_paketen: TestClient) -> None:  # noqa: F811
    n, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI})
    assert r.status_code == 201
    e = r.json()
    assert (e["id"], e["version"], e["paketRevision"], e["erstelltVon"]) == ("P-1", 1, 1, n.id)
    assert e["daten"] == POI
    assert c.get(BASIS).json()["pois"] == [e]
    r = c.patch(f"{BASIS}/P-1", headers=csrf(), json={"daten": {"note": " innen "}, "version": 1})
    assert r.status_code == 200
    assert (r.json()["version"], r.json()["daten"]["note"]) == (2, "innen")
    assert r.json()["daten"]["title"] == "Tür Nord" and r.json()["geaendertVonName"] == "b"
    assert c.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 2}).status_code == 204
    assert c.get(BASIS).json()["pois"] == []
    conn = _conn(c)
    liste = protokoll.lesen(conn, project_id="ost")
    conn.close()
    assert [(x.art, x.objekt_id) for x in liste] == [
        ("poi.geloescht", "P-1"), ("poi.geaendert", "P-1"), ("poi.angelegt", "P-1"),
    ]
    assert liste[0].vorher["note"] == "innen" and liste[0].nachher is None
    assert liste[1].vorher["note"] == "" and liste[1].nachher["note"] == "innen"


def test_gleichzeitig_aendern_409_mit_aktuellem_stand(mit_paketen: TestClient) -> None:  # noqa: F811
    _, a = person(mit_paketen, "a@b.de", grants={"ost": "bearbeiter"})
    _, b = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    a.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI})
    stand = b.get(BASIS).json()["pois"][0]
    r = a.patch(f"{BASIS}/P-1", headers=csrf(),
                json={"daten": {"title": "Von A"}, "version": stand["version"]})
    assert r.status_code == 200
    r = b.patch(f"{BASIS}/P-1", headers=csrf(),
                json={"daten": {"title": "Von B"}, "version": stand["version"]})
    assert r.status_code == 409
    assert r.json()["aktuell"]["version"] == 2
    assert r.json()["aktuell"]["daten"]["title"] == "Von A"
    assert b.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 1}).status_code == 409
    assert b.get(BASIS).json()["pois"][0]["daten"]["title"] == "Von A"


def test_parallel_nur_einer_gewinnt(mit_paketen: TestClient) -> None:  # noqa: F811
    _, a = person(mit_paketen, "a@b.de", grants={"ost": "bearbeiter"})
    version = a.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI}).json()["version"]
    ergebnisse: list[str] = []

    def lauf(titel: str) -> None:
        conn = _conn(a)
        try:
            inhalte.aendern(conn, "pois", "ost", "P-1", {"title": titel}, version, nutzer_id=None)
            ergebnisse.append("ok")
        except Konflikt:
            ergebnisse.append("konflikt")
        finally:
            conn.close()

    threads = [threading.Thread(target=lauf, args=(f"T{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(ergebnisse) == ["konflikt"] * 3 + ["ok"]


def test_betrachter_darf_nicht_schreiben(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    bea.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI})
    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    assert bet.get(BASIS).status_code == 200 and len(bet.get(BASIS).json()["pois"]) == 1
    for r in (
        bet.post(BASIS, headers=csrf(), json={"daten": POI}),
        bet.patch(f"{BASIS}/P-1", headers=csrf(), json={"daten": {"title": "x"}, "version": 1}),
        bet.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 1}),
        bet.post(MESS, headers=csrf(), json={"daten": STRECKE}),
    ):
        assert r.status_code == 403
        assert r.json()["fehler"] == "Nur Bearbeiter dürfen hier ändern."
    _, fremd = person(mit_paketen, "f@b.de", grants={"kirche": "bearbeiter"})
    assert fremd.get(BASIS).status_code == 404
    assert fremd.post(BASIS, headers=csrf(), json={"daten": POI}).status_code == 404
    assert mit_paketen.get(BASIS).status_code == 401
    assert bea.post(BASIS, json={"daten": POI}).status_code == 403  # ohne CSRF-Header
    assert bea.get(BASIS).json()["pois"][0]["daten"]["title"] == "Tür Nord"


def test_id_vergeben_409_und_wiederherstellen(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI})
    r = c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": {**POI, "title": "Doppelt"}})
    assert r.status_code == 409 and r.json()["aktuell"]["daten"]["title"] == "Tür Nord"
    assert c.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 1}).status_code == 204
    assert c.patch(f"{BASIS}/P-1", headers=csrf(),
                   json={"daten": {"title": "x"}, "version": 2}).status_code == 404
    assert c.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 2}).status_code == 404
    r = c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": {**POI, "title": "Wieder da"}})
    assert r.status_code == 201 and r.json()["version"] == 3
    conn = _conn(c)
    e = protokoll.lesen(conn, project_id="ost")[0]
    conn.close()
    assert e.art == "poi.wiederhergestellt"
    assert e.vorher["title"] == "Tür Nord" and e.nachher["title"] == "Wieder da"
    assert c.post(BASIS, headers=csrf(), json={"id": "../x", "daten": POI}).status_code == 422
    r = c.post(BASIS, headers=csrf(), json={"daten": POI})
    assert r.status_code == 201 and re.fullmatch(r"P-[0-9a-f]{12}", r.json()["id"])


def test_hoechstzahl_je_projekt(
    mit_paketen: TestClient, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.setattr(inhalte, "ANZAHL_MAX", 2)
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    for i in (1, 2):
        assert c.post(BASIS, headers=csrf(), json={"id": f"P-{i}", "daten": POI}).status_code == 201
    r = c.post(BASIS, headers=csrf(), json={"id": "P-3", "daten": POI})
    assert r.status_code == 422 and r.json()["fehler"] == "Höchstens 2 POIs je Projekt."
    # An der Grenze: eine vorhandene ID (wiederholte Anfrage) ergibt 409 mit dem Eintrag, nicht 422.
    r = c.post(BASIS, headers=csrf(), json={"id": "P-2", "daten": POI})
    assert r.status_code == 409 and r.json()["aktuell"]["id"] == "P-2"
    c.delete(f"{BASIS}/P-1", headers=csrf(), params={"version": 1})
    assert c.post(BASIS, headers=csrf(), json={"id": "P-3", "daten": POI}).status_code == 201


@pytest.mark.parametrize("aenderung", [
    {"title": ""}, {"title": "   "}, {"title": 5}, {"title": "x" * 201},
    {"category": "x" * 61}, {"note": "x" * 2001},
    {"position": [1, 2]}, {"position": [1, 2, float("nan")]}, {"position": [1, 2, float("inf")]},
    {"position": [1, True, 3]}, {"position": "1,2,3"},
    {"station": "S 1"}, {"station": "x" * 33}, {"farbe": "rot"},
])
def test_poi_pruefen_lehnt_ab(aenderung: dict[str, Any]) -> None:
    with pytest.raises(Ungueltig):
        inhalte.poi_pruefen({**POI, **aenderung})


def test_poi_pruefen_normalisiert() -> None:
    assert inhalte.poi_pruefen({"title": "  A ", "position": [1, 2, 3]}) == {
        "title": "A", "category": "", "note": "", "position": [1.0, 2.0, 3.0], "station": None,
    }


@pytest.mark.parametrize("daten", [
    {"kind": "kreis", "points": [[0, 0, 0], [1, 1, 1]]},
    {"kind": "distance", "points": [[0, 0, 0]]},
    {"kind": "distance", "points": [[0, 0, 0], [1, 1, 1], [2, 2, 2]]},
    {"kind": "polyline", "points": [[0, 0, 0]]},
    {"kind": "area", "points": [[0, 0, 0], [1, 0, 0]]},
    {"kind": "polyline", "points": [[i, 0, 0] for i in range(1001)]},
    {"kind": "height", "points": [[0, 0, 0], [0, 0, "1"]]},
    {"kind": "area", "points": [[0, 0, 0], [1, 0, 0], [1, 1, 0]], "areaM2": -1},
    {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]], "areaM2": 1},
    {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]], "farbe": "rot"},
    ["kein", "objekt"],
])
def test_messung_pruefen_lehnt_ab(daten: Any) -> None:
    with pytest.raises(Ungueltig):
        inhalte.messung_pruefen(daten)


def test_messung_pruefen_nimmt_1000_punkte() -> None:
    d = inhalte.messung_pruefen({"kind": "polyline", "points": [[i, 0, 0] for i in range(1000)]})
    assert len(d["points"]) == 1000


def test_messung_server_id_und_flaechenwerte(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = c.post(MESS, headers=csrf(), json={"daten": FLAECHE})
    assert r.status_code == 201
    e = r.json()
    assert re.fullmatch(r"M-[0-9a-f]{12}", e["id"]) and e["daten"]["areaM2"] == 6.0
    r = c.patch(f"{MESS}/{e['id']}", headers=csrf(), json={
        "daten": {"points": [[0, 0, 0], [2, 0, 0], [2, 2, 0]]}, "version": 1,
    })
    assert r.status_code == 200 and "areaM2" not in r.json()["daten"]
    r = c.patch(f"{MESS}/{e['id']}", headers=csrf(), json={
        "daten": {"points": [[0, 0, 0], [2, 0, 0], [2, 2, 0]], "areaM2": 2.0, "planarityM": 0.0},
        "version": 2,
    })
    assert r.json()["daten"]["areaM2"] == 2.0
    # Neue Eckpunkte mit nur einem der beiden Flächenwerte: der andere, veraltete fällt weg.
    r = c.patch(f"{MESS}/{e['id']}", headers=csrf(), json={
        "daten": {"points": [[0, 0, 0], [3, 0, 0], [3, 3, 0]], "areaM2": 4.5}, "version": 3,
    })
    assert r.status_code == 200
    assert r.json()["daten"]["areaM2"] == 4.5 and "planarityM" not in r.json()["daten"]
    # Nur ein Flächenwert ohne neue Eckpunkte: der andere bleibt.
    r = c.patch(f"{MESS}/{e['id']}", headers=csrf(), json={"daten": {"planarityM": 0.01},
                                                             "version": 4})
    assert r.json()["daten"] == {"kind": "area", "points": [[0, 0, 0], [3, 0, 0], [3, 3, 0]],
                                 "areaM2": 4.5, "planarityM": 0.01}
    assert c.patch(f"{MESS}/{e['id']}", headers=csrf(),
                   json={"daten": {"kind": "polyline"}, "version": 5}).status_code == 422
    assert c.post(MESS, headers=csrf(), json={"daten": STRECKE}).status_code == 201
    assert len(c.get(MESS).json()["messungen"]) == 2
    conn = _conn(c)
    assert protokoll.lesen(conn, project_id="ost")[0].art == "messung.angelegt"
    conn.close()
