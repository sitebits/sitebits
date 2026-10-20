from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import db, protokoll


def test_v1_aliase_liefern_dasselbe(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    assert c.get("/api/v1/sitzung").json() == c.get("/api/sitzung").json()
    alt = c.get("/api/projekte").json()
    assert c.get("/api/v1/projekte").json() == alt
    assert alt["projekte"][0]["id"] == "ost" and alt["projekte"][0]["version"] == 0


def test_openapi_nur_mit_sitzung_und_nur_v1(mit_paketen: TestClient) -> None:  # noqa: F811
    assert mit_paketen.get("/api/v1/openapi.json").status_code == 401
    _, c = person(mit_paketen, "b@b.de")
    r = c.get("/api/v1/openapi.json")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    pfade = r.json()["paths"]
    assert "/api/v1/projekte/{pid}" in pfade and "/api/v1/sitzung" in pfade
    assert all(p.startswith("/api/v1/") for p in pfade)
    assert "/api/v1/openapi.json" not in pfade
    assert mit_paketen.get("/openapi.json").status_code == 404
    assert mit_paketen.get("/docs").status_code == 404


def test_kein_authz_alias(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "betrachter"})
    r = c.get("/api/v1/authz", headers={"X-Forwarded-Uri": "/data/ost/project.json",
                                        "X-Forwarded-Method": "GET"})
    assert r.status_code == 404 and r.json()["dienst"] == "scanview"


def test_projekt_lesen_rolle_und_404(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "betrachter"})
    r = c.get("/api/v1/projekte/ost")
    assert r.status_code == 200 and r.json()["rolle"] == "betrachter"
    assert r.json()["version"] == 0
    for pid in ("kirche", "gibts-nicht", "Ost", "..%2Fost"):
        assert c.get(f"/api/v1/projekte/{pid}").status_code == 404, pid
    assert mit_paketen.get("/api/v1/projekte/ost").status_code == 401


def test_meta_aendern_mit_version_und_protokoll(mit_paketen: TestClient) -> None:  # noqa: F811
    n, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = c.patch("/api/v1/projekte/ost", headers=csrf(), json={
        "titel": "  Fassade Ost ", "beschreibung": "68 Standpunkte\nNordseite", "version": 0,
    })
    assert r.status_code == 200
    assert (r.json()["titel"], r.json()["beschreibung"], r.json()["version"]) == (
        "Fassade Ost", "68 Standpunkte\nNordseite", 1,
    )
    r = c.patch("/api/v1/projekte/ost", headers=csrf(), json={"beschreibung": "neu", "version": 1})
    assert (r.json()["titel"], r.json()["beschreibung"], r.json()["version"]) == (
        "Fassade Ost", "neu", 2,
    )
    assert c.get("/api/v1/projekte").json()["projekte"][0]["titel"] == "Fassade Ost"
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    [e2, e1] = protokoll.lesen(conn, project_id="ost")
    conn.close()
    assert (e1.art, e1.kanal, e1.nutzer_id, e1.objekt_id) == ("meta.geaendert", "web", n.id, "ost")
    assert e1.vorher == {"titel": None, "beschreibung": None}
    assert e2.vorher == {"titel": "Fassade Ost", "beschreibung": "68 Standpunkte\nNordseite"}
    assert e2.nachher == {"titel": "Fassade Ost", "beschreibung": "neu"}


def test_meta_konflikt_409_mit_aktuellem_stand(mit_paketen: TestClient) -> None:  # noqa: F811
    _, a = person(mit_paketen, "a@b.de", grants={"ost": "bearbeiter"})
    _, b = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    assert a.patch("/api/v1/projekte/ost", headers=csrf(),
                   json={"titel": "Von A", "version": 0}).status_code == 200
    r = b.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "Von B", "version": 0})
    assert r.status_code == 409
    assert r.json()["dienst"] == "scanview" and "geändert" in r.json()["fehler"]
    assert r.json()["aktuell"]["titel"] == "Von A" and r.json()["aktuell"]["version"] == 1
    assert a.get("/api/v1/projekte/ost").json()["titel"] == "Von A"


def test_meta_leerer_titel_setzt_paketnamen_zurueck(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    c.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "Anders", "version": 0})
    r = c.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "  ", "version": 1})
    assert r.json()["titel"] == "ost"  # lege_paket: title = projectId


def test_meta_titel_gleich_katalog_kein_override(mit_paketen: TestClient) -> None:  # noqa: F811
    """Ein Formular, das den (unangetasteten, aufgelösten) Titel trotzdem
    mitschickt, darf keinen Override festschreiben, der den Katalog-Titel einfriert."""
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = c.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "ost", "version": 0})
    assert r.status_code == 200
    assert r.json()["titel"] == "ost"
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    row = conn.execute("SELECT title FROM project_meta WHERE project_id=?", ("ost",)).fetchone()
    conn.close()
    assert row["title"] is None  # kein Override — folgt weiter dem Katalog-Titel


def test_meta_rechte_und_eingaben(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    r = bet.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "x", "version": 0})
    assert r.status_code == 403 and r.json()["fehler"] == "Nur Bearbeiter dürfen hier ändern."
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    assert bea.patch("/api/v1/projekte/kirche", headers=csrf(),
                     json={"titel": "x", "version": 0}).status_code == 404
    assert bea.patch("/api/v1/projekte/ost", json={"titel": "x", "version": 0}).status_code == 403
    for body in (
        {"version": 0},
        {"titel": "x" * 121, "version": 0},
        {"titel": "a\nb", "version": 0},
        {"beschreibung": "x" * 2001, "version": 0},
        {"beschreibung": "a\x00b", "version": 0},
        {"titel": "x", "version": -1},
        {"titel": "x"},
    ):
        r = bea.patch("/api/v1/projekte/ost", headers=csrf(), json=body)
        assert r.status_code == 422, body
        assert r.json()["dienst"] == "scanview"
    admin = person(mit_paketen, "adm@b.de", admin=True)[1]
    assert admin.patch("/api/v1/projekte/ost", headers=csrf(),
                       json={"titel": "Admin darf", "version": 0}).status_code == 200


def test_kaputter_body_ohne_sitzung_401_mit_sitzung_422(mit_paketen: TestClient) -> None:  # noqa: F811
    """Unter /api/v1/ hat „keine Sitzung“ Vorrang vor 422 (RequestValidat-
    ionError). Mit Sitzung bleibt ein kaputter Body 422 — auch für Betrachter: die Body-Validierung
    (Pydantic) läuft vor der Rollenprüfung im Funktionskörper, ein Betrachter bekommt also bei
    kaputtem Body 422 statt 403 (dokumentiertes Verhalten)."""
    kaputt = {"titel": "x", "version": -1}
    r = mit_paketen.patch("/api/v1/projekte/ost", headers=csrf(), json=kaputt)
    assert r.status_code == 401 and r.json()["fehler"] == "nicht angemeldet"

    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    r2 = bet.patch("/api/v1/projekte/ost", headers=csrf(), json=kaputt)
    assert r2.status_code == 422

    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r3 = bea.patch("/api/v1/projekte/ost", headers=csrf(), json=kaputt)
    assert r3.status_code == 422


def test_kaputter_body_ohne_csrf_bleibt_403(mit_paketen: TestClient) -> None:  # noqa: F811
    """CSRF bleibt vorrangig vor der (neuen) 401-Prüfung und vor 422."""
    r = mit_paketen.patch("/api/v1/projekte/ost", json={"titel": "x", "version": -1})
    assert r.status_code == 403


def test_openapi_schemas_nur_von_v1_referenziert(mit_paketen: TestClient) -> None:  # noqa: F811
    """components.schemas nur die von v1-Pfaden erreichbaren Schemas."""
    _, c = person(mit_paketen, "b@b.de")
    schemas = c.get("/api/v1/openapi.json").json()["components"]["schemas"]
    assert "MetaIn" in schemas
    assert "AnmeldenIn" not in schemas
    assert "TokenIn" not in schemas


def test_version_endpunkt(mit_paketen: TestClient) -> None:  # noqa: F811
    """Produktversion nur für Angemeldete (keine Versionspreisgabe an Anonyme), Wert = Paket."""
    import tomllib
    from pathlib import Path

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    soll = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]["version"]
    assert mit_paketen.get("/api/v1/version").status_code == 401
    _, c = person(mit_paketen, "b@b.de")
    r = c.get("/api/v1/version")
    assert r.status_code == 200 and "no-store" in r.headers["cache-control"]
    assert r.json() == {"version": soll}
    assert "/api/v1/version" in c.get("/api/v1/openapi.json").json()["paths"]


def test_openapi_nennt_die_echten_inhaltstypen(mit_paketen: TestClient) -> None:  # noqa: F811
    """Befund B-FUZZ-1 (API-Fuzzing): CSV und JSON Lines stehen mit ihrem tatsächlichen
    Inhaltstyp im Schema, nicht als application/json."""
    _, c = person(mit_paketen, "a@b.de", admin=True)
    pfade = c.get("/api/v1/openapi.json").json()["paths"]
    for pfad, typ in (("/api/v1/projekte/{pid}/export.csv", "text/csv"),
                      ("/api/v1/projekte/{pid}/protokoll.jsonl", "application/x-ndjson"),
                      ("/api/v1/protokoll.jsonl", "application/x-ndjson")):
        assert list(pfade[pfad]["get"]["responses"]["200"]["content"]) == [typ], pfad
