"""Projektleitung über Bearbeiter, Admin darüber; Unbekanntes darf nichts."""

from __future__ import annotations

import typing

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_api_bearer import anon, auth, token
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import konten


def test_rollen_und_rang() -> None:
    assert konten.ROLLEN == ("betrachter", "rueckmelder", "bearbeiter", "projektleitung")
    assert typing.get_args(konten.RolleWert) == konten.ROLLEN
    assert [konten.rang(r) for r in (*konten.ROLLEN, "admin")] == [1, 2, 3, 4, 5]
    assert konten.rang(None) == 0 and konten.rang("chef") == 0 and konten.rang("__proto__") == 0
    assert konten.mindestens("projektleitung", "bearbeiter")
    assert not konten.mindestens("bearbeiter", "projektleitung")
    assert not konten.mindestens(None, "betrachter")
    assert konten.LEITUNG_VERGIBT == ("betrachter", "rueckmelder", "bearbeiter")


def test_projektleitung_darf_was_bearbeiter_duerfen(mit_paketen: TestClient) -> None:  # noqa: F811
    _, lei = person(mit_paketen, "l@x.de", grants={"ost": "projektleitung"})
    r = lei.get("/api/v1/projekte/ost")
    assert r.status_code == 200 and r.json()["rolle"] == "projektleitung"
    r = lei.patch("/api/v1/projekte/ost", headers=csrf(), json={"titel": "Neu", "version": 0})
    assert r.status_code == 200
    poi = {
        "title": "Riss",
        "category": "Mangel",
        "note": "",
        "position": [1, 2, 3],
        "station": None,
    }
    assert (
        lei.post("/api/v1/projekte/ost/pois", headers=csrf(), json={"daten": poi}).status_code
        == 201
    )
    assert lei.get("/api/v1/projekte/ost/export.json").status_code == 200
    assert lei.get("/api/v1/projekte/ost/protokoll").status_code == 200
    assert lei.get("/api/v1/projekte/kirche").status_code == 404


def test_admin_vergibt_projektleitung(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "adm@x.de", admin=True)
    n, _ = person(mit_paketen, "l@x.de", grants={})
    r = adm.put(
        f"/api/v1/admin/nutzer/{n.id}/rechte/ost", headers=csrf(), json={"rolle": "projektleitung"}
    )
    assert (r.status_code, r.json()) == (200, {"vorher": None, "rolle": "projektleitung"})
    r = adm.post(
        "/api/v1/admin/einladungen",
        headers=csrf(),
        json={"name": "B", "email": "b@x.de", "rechte": {"ost": "projektleitung"}},
    )
    assert r.status_code == 201 and r.json()["einladung"]["rechte"] == {"ost": "projektleitung"}


@pytest.mark.parametrize("rolle", ["chef", "admin"])
def test_unbekannte_rolle_422(mit_paketen: TestClient, rolle: str) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "adm@x.de", admin=True)
    n, _ = person(mit_paketen, "l@x.de", grants={})
    r = adm.put(f"/api/v1/admin/nutzer/{n.id}/rechte/ost", headers=csrf(), json={"rolle": rolle})
    assert r.status_code == 422


def test_verlauf_vorrang_kaputter_parameter(mit_paketen: TestClient) -> None:  # noqa: F811
    """Kaputter Parameter am Projektverlauf: Projektleitung 422, Betrachter weiter 403."""
    _, lei = person(mit_paketen, "l@x.de", grants={"ost": "projektleitung"})
    _, bet = person(mit_paketen, "t@x.de", grants={"ost": "betrachter"})
    assert lei.get("/api/v1/projekte/ost/protokoll?limit=0").status_code == 422
    assert bet.get("/api/v1/projekte/ost/protokoll?limit=0").status_code == 403


def test_projektleitung_lesetoken_schreibt_nicht(mit_paketen: TestClient) -> None:  # noqa: F811
    n, _ = person(mit_paketen, "l@x.de", grants={"ost": "projektleitung"})
    c = anon(mit_paketen)
    kopf = auth(token(mit_paketen, n, "lesen", "lesen"))
    assert c.get("/api/v1/projekte/ost", headers=kopf).status_code == 200
    assert (
        c.patch(
            "/api/v1/projekte/ost", headers=kopf, json={"titel": "Neu", "version": 0}
        ).status_code
        == 403
    )
    assert (
        c.patch(
            "/api/v1/projekte/kirche", headers=kopf, json={"titel": "Neu", "version": 0}
        ).status_code
        == 404
    )
