from fastapi.testclient import TestClient
from test_authz import mit_paketen  # noqa: F401  (Fixture)
from test_sitzung import anmelden

from scanview_server import db


def test_liste_nur_berechtigte(mit_paketen: TestClient) -> None:  # noqa: F811
    anmelden(mit_paketen, grants={"ost": "bearbeiter"})
    r = mit_paketen.get("/api/projekte")
    assert r.status_code == 200
    p = r.json()["projekte"]
    assert [x["id"] for x in p] == ["ost"]
    assert p[0]["rolle"] == "bearbeiter" and p[0]["crs"] == "lokal" and p[0]["punkte"] == 1234
    assert p[0]["ausdehnung"] == [40.5, 30.25, 12.0]


def test_meta_ueberlagert_titel(mit_paketen: TestClient) -> None:  # noqa: F811
    anmelden(mit_paketen, admin=True)
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    conn.execute(
        "INSERT INTO project_meta (project_id, title, description, updated_at) VALUES (?,?,?,0)",
        ("ost", "Fassade Ost", "68 Standpunkte"),
    )
    conn.close()
    p = {x["id"]: x for x in mit_paketen.get("/api/projekte").json()["projekte"]}
    assert p["ost"]["titel"] == "Fassade Ost" and p["ost"]["beschreibung"] == "68 Standpunkte"


def test_ohne_sitzung_401(mit_paketen: TestClient) -> None:  # noqa: F811
    assert mit_paketen.get("/api/projekte").status_code == 401
