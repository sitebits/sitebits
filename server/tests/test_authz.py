import logging

import pytest
from fastapi.testclient import TestClient
from pakete import lege_paket
from test_sitzung import anmelden

from scanview_server import db


@pytest.fixture
def mit_paketen(client: TestClient) -> TestClient:
    d = client.app.state.settings.data_dir  # type: ignore[attr-defined]
    lege_paket(d, "kirche")
    lege_paket(d, "ost")
    client.app.state.katalog.laden()  # type: ignore[attr-defined]
    return client


def authz(c: TestClient, uri: str, method: str = "GET") -> int:
    r = c.get("/api/authz", headers={"X-Forwarded-Uri": uri, "X-Forwarded-Method": method})
    # Jede Nicht-200-Antwort muss JSON sein (Regel „nie HTML“ gilt für /api/authz
    # genau wie für 401/404 im Erfolgsfall).
    if r.status_code != 200:
        assert r.headers["content-type"].startswith("application/json")
    return r.status_code


def test_ohne_sitzung_401_json(mit_paketen: TestClient) -> None:
    r = mit_paketen.get(
        "/api/authz",
        headers={"X-Forwarded-Uri": "/data/kirche/project.json", "X-Forwarded-Method": "GET"},
    )
    assert r.status_code == 401 and r.headers["content-type"].startswith("application/json")


def test_ohne_forwarded_method_404_fail_closed(mit_paketen: TestClient) -> None:
    """Fehlt der Header ganz (kein regulärer Caddy-forward_auth-Aufruf) fail-closed statt GET zu
    unterstellen."""
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    r = mit_paketen.get(
        "/api/authz", headers={"X-Forwarded-Uri": "/data/kirche/cloud.copc.laz"},
    )
    assert r.status_code == 404
    assert r.headers["content-type"].startswith("application/json")


def test_eigenes_projekt_ok_fremdes_404(mit_paketen: TestClient) -> None:
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz", method="HEAD") == 200
    assert authz(mit_paketen, "/data/kirche/panos/S001/px/0.jpg?v=2") == 200  # echte Form
    assert authz(mit_paketen, "/data/ost/cloud.copc.laz") == 404
    assert authz(mit_paketen, "/data/gibtsnicht/project.json") == 404
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz", method="PUT") == 404


@pytest.mark.parametrize("uri", [
    "/data/kirche/../ost/cloud.copc.laz",
    "/data/kirche/%2e%2e/ost/cloud.copc.laz",
    "/data/kirche/%2E%2E%2Fost/cloud.copc.laz",
    "/data/kirche//cloud.copc.laz",
    "/data/kirche/..%5cost",
    "/data/kirche\\..\\ost/x",
    "/data/kirche/",
    "/data/kirche",
    "/data/Kirche/project.json",
    "/data/kirche/.hidden",
    "/daten/kirche/project.json",
    "/data/kirche/cloud.copc.laz%0a",
    "",
    # Ergänzend — bereits vorher 404 durch Zeichensatz/Anker in _ziel_aus_uri,
    # hier als Regressionstests festgehalten: doppelt kodiert, Matrix-Parameter (Tomcat/ältere
    # Proxys), eingebettetes Nullbyte, überlange UTF-8-Kodierung von „.“, kodierter Schrägstrich
    # (Großschreibung), doppelter Führungsslash.
    "/data/kirche/%252e%252e/ost/cloud.copc.laz",
    "/data/kirche;/../ost/cloud.copc.laz",
    "/data/kirche/cloud.copc.laz%00.jpg",
    "/data/kirche/%C0%AE%C0%AE/ost/cloud.copc.laz",
    "/data/kirche%2Fcloud.copc.laz",
    "//data/kirche/cloud.copc.laz",
])
def test_authz_pfadtricks(mit_paketen: TestClient, uri: str) -> None:
    anmelden(mit_paketen, grants={"kirche": "bearbeiter"})
    assert authz(mit_paketen, uri) == 404


def test_authz_rechteentzug_sofort(mit_paketen: TestClient) -> None:
    n = anmelden(mit_paketen, grants={"kirche": "betrachter"})
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 200
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    conn.execute("DELETE FROM project_roles WHERE user_id=?", (n.id,))
    conn.close()
    assert authz(mit_paketen, "/data/kirche/cloud.copc.laz") == 404


def test_admin_sieht_alles(mit_paketen: TestClient) -> None:
    anmelden(mit_paketen, admin=True)
    assert authz(mit_paketen, "/data/ost/project.json") == 200


def test_authz_query_mit_fragezeichen(mit_paketen: TestClient) -> None:
    # Abgeschnitten wird am ersten „?“: ein weiteres „?“ im Query-String gehört nicht zum Pfad.
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    assert authz(mit_paketen, "/data/kirche/project.json?v=1?x=2") == 200
    assert authz(mit_paketen, "/data/kirche/x.html?a=/data/kirche/project.json") == 404


def test_authz_protokoll_nur_bei_debug(
    mit_paketen: TestClient, caplog: pytest.LogCaptureFixture,
) -> None:
    anmelden(mit_paketen, grants={"kirche": "betrachter"})
    with caplog.at_level(logging.INFO, logger="scanview.api"):
        assert authz(mit_paketen, "/data/kirche/project.json") == 200
    assert not [r for r in caplog.records if r.name == "scanview.api"]
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="scanview.api"):
        assert authz(mit_paketen, "/data/kirche/project.json?geheim=1") == 200
        assert authz(mit_paketen, "/data/ost/project.json") == 404
        assert authz(mit_paketen, "/data/kirche/x.html") == 404
    zeilen = [r.getMessage() for r in caplog.records if r.name == "scanview.api"]
    assert len(zeilen) == 3
    assert [z.split()[:3] for z in zeilen] == [
        ["authz", "200", "kirche"], ["authz", "404", "ost"], ["authz", "404", "-"],
    ]
    assert all(z.endswith(" ms") for z in zeilen)
    # Dauer in Millisekunden: messbar größer als 0, aber keine Uhrzeit (Summe statt Differenz).
    assert all(0 < float(z.split()[3]) < 60_000 for z in zeilen)
    # Nie die volle URI (Query, Dateiname) — nur Status, projectId, Dauer.
    assert not any("geheim" in z or "project.json" in z or "x.html" in z for z in zeilen)
