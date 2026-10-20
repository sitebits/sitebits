import json
import sqlite3

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import db, konten, protokoll, verwaltung, zugriff
from scanview_server.routen_export import NUR_VERLAUF
from scanview_server.tokens import hash_token


def _conn(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def test_admin_routen_fuer_nicht_admin_404(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    for methode, pfad, body in (
        ("GET", "/api/v1/admin/nutzer", None),
        ("GET", "/api/v1/admin/einladungen", None),
        ("GET", "/api/v1/admin/statistik", None),
        ("POST", "/api/v1/admin/einladungen", {"name": "X", "email": "x@b.de"}),
        ("POST", "/api/v1/admin/einladungen/0123456789abcdef/widerrufen", None),
        ("POST", "/api/v1/admin/nutzer/1/sperren", None),
        ("POST", "/api/v1/admin/nutzer/1/entsperren", None),
        ("POST", "/api/v1/admin/nutzer/1/abmelden-ueberall", None),
        ("PUT", "/api/v1/admin/nutzer/1/rechte/ost", {"rolle": "bearbeiter"}),
    ):
        r = bea.request(methode, pfad, json=body, headers=csrf())
        assert r.status_code == 404, pfad
        assert r.json()["dienst"] == "scanview"
    assert mit_paketen.get("/api/v1/admin/nutzer").status_code == 401


def test_nutzerliste_mit_status_und_rechten(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "a@b.de", admin=True, name="Andre")
    t, _ = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"}, name="Tina")
    conn = _conn(mit_paketen)
    verwaltung.sperren(conn, t.id, von=None, kanal="cli")
    conn.close()
    d = adm.get("/api/v1/admin/nutzer").json()
    nutzer = {n["email"]: n for n in d["nutzer"]}
    assert nutzer["t@b.de"]["gesperrt"] is True
    assert nutzer["t@b.de"]["rechte"] == {"ost": "betrachter"}
    assert nutzer["a@b.de"]["admin"] is True and nutzer["a@b.de"]["letzteAnmeldung"] is not None
    assert [p["id"] for p in d["projekte"]] == ["kirche", "ost"]


def test_einladung_link_nur_einmal_ohne_geheimnis_im_protokoll(
    mit_paketen: TestClient,  # noqa: F811
) -> None:
    a, adm = person(mit_paketen, "a@b.de", admin=True)
    r = adm.post("/api/v1/admin/einladungen", headers=csrf(),
                 json={"name": "Anna", "email": "anna@b.de", "rechte": {"ost": "bearbeiter"}})
    assert r.status_code == 201 and "no-store" in r.headers["cache-control"]
    link = r.json()["link"]
    assert link.startswith(f"{ORIGIN}/einladung#t=")
    token = link.split("#t=", 1)[1]
    offen = adm.get("/api/v1/admin/einladungen").json()["einladungen"]
    assert [(e["email"], e["zweck"], e["rechte"]) for e in offen] == [
        ("anna@b.de", "neu", {"ost": "bearbeiter"}),
    ]
    assert "link" not in offen[0] and token not in json.dumps(offen)
    conn = _conn(mit_paketen)
    roh = " ".join(str(v) for z in conn.execute("SELECT * FROM ereignisse") for v in tuple(z))
    for geheim in (token, hash_token(token), "anna@b.de", "Anna"):
        assert geheim not in roh
    e = protokoll.lesen(conn)[0]
    conn.close()
    assert (e.art, e.kanal, e.nutzer_id, e.objekt_id) == (
        "einladung.angelegt", "web", a.id, offen[0]["id"],
    )
    pfad = f"/api/v1/admin/einladungen/{offen[0]['id']}/widerrufen"
    assert adm.post(pfad, headers=csrf()).status_code == 204
    assert adm.get("/api/v1/admin/einladungen").json()["einladungen"] == []
    conn2 = _conn(mit_paketen)
    ev = protokoll.lesen(conn2)[0]
    conn2.close()
    assert (ev.art, ev.objekt_id, ev.nutzer_id) == ("einladung.widerrufen", offen[0]["id"], a.id)
    assert adm.post(pfad, headers=csrf()).status_code == 404
    assert adm.post("/api/v1/admin/einladungen/zzzz/widerrufen", headers=csrf()).status_code == 404


def test_einladung_widerruf_nur_nicht_abgelaufen(mit_paketen: TestClient) -> None:  # noqa: F811
    """Einheitlich mit `GET /admin/einladungen` (zeigt nur offene,
    gültige Einladungen) — eine abgelaufene, nie eingelöste Einladung gilt auch beim Widerruf
    als nicht (mehr) vorhanden."""
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    conn = _conn(mit_paketen)
    token = konten.einladung_anlegen(conn, email="alt@b.de", name="Alt", grants={}, now=0)
    conn.close()
    eid = verwaltung.einladung_id(hash_token(token))
    assert adm.post(f"/api/v1/admin/einladungen/{eid}/widerrufen",
                    headers=csrf()).status_code == 404


def test_einladung_eingaben(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    person(mit_paketen, "da@b.de")
    for body in (
        {"name": "X", "email": "x@b.de", "rechte": {"gibts-nicht": "betrachter"}},
        {"name": "X", "email": "x@b.de", "rechte": {"ost": "admin"}},
        {"name": "", "email": "x@b.de"},
        {"name": "X", "email": "kein-at"},
        {"name": "X", "email": "da@b.de"},
        {"name": "X", "email": "neu@b.de", "zuruecksetzen": True},
        {"name": "X", "email": "z@b.de", "admin": True, "zuruecksetzen": True},
    ):
        r = adm.post("/api/v1/admin/einladungen", headers=csrf(), json=body)
        assert r.status_code == 422, body
    r = adm.post("/api/v1/admin/einladungen", headers=csrf(),
                 json={"name": "Da", "email": "da@b.de", "zuruecksetzen": True})
    assert r.status_code == 201 and r.json()["einladung"]["zweck"] == "zuruecksetzen"
    assert adm.post("/api/v1/admin/einladungen",
                    json={"name": "Y", "email": "y@b.de"}).status_code == 403  # ohne CSRF


def test_einladung_admin_und_zuruecksetzen_zusammen_422(mit_paketen: TestClient) -> None:  # noqa: F811
    """admin=true wirkt bei purpose=zuruecksetzen nicht (das bestehende
    Konto behält seinen is_admin-Stand) — die Kombination wäre nur ein irreführender Protokoll-
    eintrag und ist deshalb ungültig statt still ignoriert."""
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    person(mit_paketen, "da@b.de")
    r = adm.post("/api/v1/admin/einladungen", headers=csrf(),
                json={"name": "Da", "email": "da@b.de", "admin": True, "zuruecksetzen": True})
    assert r.status_code == 422
    assert r.json()["fehler"] == (
        "Admin-Rechte lassen sich nicht per Zurücksetzen-Einladung vergeben."
    )


def test_sperren_entsperren_abmelden(mit_paketen: TestClient) -> None:  # noqa: F811
    a, adm = person(mit_paketen, "a@b.de", admin=True)
    t, tc = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    u, uc = person(mit_paketen, "u@b.de")
    assert tc.get("/api/v1/sitzung").status_code == 200
    assert adm.post(f"/api/v1/admin/nutzer/{t.id}/sperren", headers=csrf()).status_code == 204
    assert tc.get("/api/v1/sitzung").status_code == 401
    assert adm.post(f"/api/v1/admin/nutzer/{t.id}/entsperren", headers=csrf()).status_code == 204
    assert adm.post(f"/api/v1/admin/nutzer/{u.id}/abmelden-ueberall",
                    headers=csrf()).status_code == 204
    assert uc.get("/api/v1/sitzung").status_code == 401
    r = adm.post(f"/api/v1/admin/nutzer/{a.id}/sperren", headers=csrf())
    assert r.status_code == 422
    assert r.json()["fehler"] == "Das eigene Konto kann nicht gesperrt werden."
    assert adm.post("/api/v1/admin/nutzer/9999/sperren", headers=csrf()).status_code == 404
    assert adm.post(f"/api/v1/admin/nutzer/{t.id}/sperren").status_code == 403  # ohne CSRF
    conn = _conn(mit_paketen)
    folge = [(e.art, e.objekt_id, e.kanal, e.nutzer_id) for e in protokoll.lesen(conn)
             if e.art in ("konto.gesperrt", "konto.entsperrt", "konto.abgemeldet")]
    conn.close()
    assert folge == [
        ("konto.abgemeldet", str(u.id), "web", a.id),
        ("konto.entsperrt", str(t.id), "web", a.id),
        ("konto.gesperrt", str(t.id), "web", a.id),
    ]


def test_admin_kann_anderen_admin_sperren(mit_paketen: TestClient) -> None:  # noqa: F811
    """Nur das EIGENE Konto ist geschützt (422) — ein anderes
    Admin-Konto darf gesperrt werden, wie jedes andere, und wird ganz normal protokolliert."""
    a, adm = person(mit_paketen, "a@b.de", admin=True)
    b, bc = person(mit_paketen, "b@b.de", admin=True)
    assert bc.get("/api/v1/sitzung").status_code == 200
    assert adm.post(f"/api/v1/admin/nutzer/{b.id}/sperren", headers=csrf()).status_code == 204
    assert bc.get("/api/v1/sitzung").status_code == 401
    conn = _conn(mit_paketen)
    e = protokoll.lesen(conn)[0]
    conn.close()
    assert (e.art, e.objekt_id, e.nutzer_id) == ("konto.gesperrt", str(b.id), a.id)


def test_rechte_matrix_setzen(mit_paketen: TestClient) -> None:  # noqa: F811
    a, adm = person(mit_paketen, "a@b.de", admin=True)
    t, tc = person(mit_paketen, "t@b.de")
    pfad = f"/api/v1/admin/nutzer/{t.id}/rechte/ost"
    assert tc.get("/api/v1/projekte/ost").status_code == 404
    r = adm.put(pfad, headers=csrf(), json={"rolle": "betrachter"})
    assert r.json() == {"vorher": None, "rolle": "betrachter"}
    assert tc.get("/api/v1/projekte/ost").json()["rolle"] == "betrachter"
    adm.put(pfad, headers=csrf(), json={"rolle": "bearbeiter"})
    adm.put(pfad, headers=csrf(), json={"rolle": "bearbeiter"})  # unverändert: kein Ereignis
    adm.put(pfad, headers=csrf(), json={"rolle": None})
    assert tc.get("/api/v1/projekte/ost").status_code == 404
    conn = _conn(mit_paketen)
    folge = [(e.art, e.vorher, e.nachher, e.nutzer_id)
             for e in reversed(protokoll.lesen(conn, project_id="ost"))]
    conn.close()
    assert folge == [
        ("recht.vergeben", None, "betrachter", a.id),
        ("recht.vergeben", "betrachter", "bearbeiter", a.id),
        ("recht.entzogen", "bearbeiter", None, a.id),
    ]
    assert adm.put(f"/api/v1/admin/nutzer/{t.id}/rechte/gibts-nicht", headers=csrf(),
                   json={"rolle": "betrachter"}).status_code == 404
    r = adm.put(f"/api/v1/admin/nutzer/{a.id}/rechte/ost", headers=csrf(),
                json={"rolle": "betrachter"})
    assert r.status_code == 422 and r.json()["fehler"] == "Admins sehen ohnehin alle Projekte."
    assert adm.put(pfad, headers=csrf(), json={"rolle": "admin"}).status_code == 422
    assert adm.put("/api/v1/admin/nutzer/9999/rechte/ost", headers=csrf(),
                   json={"rolle": "betrachter"}).status_code == 404


# --- Admin-Prüfung im RequestValidationError-Handler geht dem
# 422 auch bei ungültigen Query-/Body-Parametern voraus (web.py, zentral statt je Route). ------

def test_protokoll_ungueltiger_limit_vor_422(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = bea.get("/api/v1/protokoll", params={"limit": 0})
    assert r.status_code == 404 and r.json()["fehler"] == zugriff.NICHT_GEFUNDEN
    assert mit_paketen.get("/api/v1/protokoll", params={"limit": 0}).status_code == 401
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    assert adm.get("/api/v1/protokoll", params={"limit": 0}).status_code == 422


def test_admin_statistik_ungueltige_tage_nicht_admin_404(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = bea.get("/api/v1/admin/statistik", params={"tage": 0})
    assert r.status_code == 404 and r.json()["fehler"] == zugriff.NICHT_GEFUNDEN


def test_admin_ungueltiger_body_ohne_csrf_bleibt_403(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r = bea.put("/api/v1/admin/nutzer/1/rechte/ost", json={"rolle": "admin"})  # kaputt, ohne CSRF
    assert r.status_code == 403


def test_projekt_protokoll_ungueltiger_limit_rollen(mit_paketen: TestClient) -> None:  # noqa: F811
    """Dieselbe Vorrangfolge gilt für die projektbezogene Verlaufsroute —
    Rolle entscheidet zwischen 404 (kein Zugriff), 403 (Betrachter) und 422 (Bearbeiter/Admin),
    auch wenn `limit` selbst ungültig ist."""
    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    r = bet.get("/api/v1/projekte/ost/protokoll", params={"limit": 0})
    assert r.status_code == 403 and r.json()["fehler"] == NUR_VERLAUF
    r2 = bet.get("/api/v1/projekte/kirche/protokoll", params={"limit": 0})  # fremdes Projekt
    assert r2.status_code == 404 and r2.json()["fehler"] == zugriff.NICHT_GEFUNDEN
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    r3 = bea.get("/api/v1/projekte/ost/protokoll", params={"limit": 0})
    assert r3.status_code == 422
    assert mit_paketen.get("/api/v1/projekte/ost/protokoll", params={"limit": 0}).status_code == 401


def test_openapi_verbirgt_admin_und_protokoll_fuer_nicht_admin(mit_paketen: TestClient) -> None:  # noqa: F811
    """Das OpenAPI-Dokument soll
    Nicht-Admins nicht verraten, dass es /api/v1/admin/* oder das globale Protokoll überhaupt
    gibt — die 404-Antwort dieser Routen verbirgt es sonst nur scheinbar."""
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    d = bea.get("/api/v1/openapi.json").json()
    pfade = d["paths"]
    assert not any(p.startswith("/api/v1/admin/") for p in pfade)
    assert "/api/v1/protokoll" not in pfade and "/api/v1/protokoll.jsonl" not in pfade
    assert "/api/v1/projekte/{pid}/protokoll" in pfade  # projektbezogen bleibt sichtbar
    schemas = d["components"]["schemas"]
    assert "EinladungIn" not in schemas and "RechtIn" not in schemas
    assert "StatistikIn" in schemas  # /api/v1/statistik/{pid} bleibt für alle Rollen sichtbar

    _, adm = person(mit_paketen, "a@b.de", admin=True)
    d_adm = adm.get("/api/v1/openapi.json").json()
    assert any(p.startswith("/api/v1/admin/") for p in d_adm["paths"])
    assert "/api/v1/protokoll" in d_adm["paths"]
    assert "EinladungIn" in d_adm["components"]["schemas"]


def test_anonymisiertes_konto_ueber_api_endgueltig(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "a@b.de", admin=True, name="Andre")
    t, _ = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"}, name="Tina")
    conn = _conn(mit_paketen)
    verwaltung.anonymisieren(conn, t.id, von=None, kanal="cli")
    conn.close()
    nutzer = {n["id"]: n for n in adm.get("/api/v1/admin/nutzer").json()["nutzer"]}
    assert nutzer[t.id]["anonymisiert"] is True and nutzer[t.id]["gesperrt"] is True
    assert nutzer[t.id]["name"] == f"Gelöschtes Konto {t.id}"
    assert all(not n["anonymisiert"] for i, n in nutzer.items() if i != t.id)
    for methode, pfad, body in (
        ("POST", f"/api/v1/admin/nutzer/{t.id}/entsperren", None),
        ("PUT", f"/api/v1/admin/nutzer/{t.id}/rechte/ost", {"rolle": "bearbeiter"}),
        ("POST", "/api/v1/admin/einladungen",
         {"name": "X", "email": f"geloescht-{t.id}@invalid", "zuruecksetzen": True}),
        ("POST", "/api/v1/admin/einladungen", {"name": "X", "email": f"geloescht-{t.id}@invalid"}),
    ):
        r = adm.request(methode, pfad, json=body, headers=csrf())
        assert (r.status_code, r.json()["fehler"]) == (422, "Konto wurde anonymisiert."), pfad
    # Die frühere Adresse bekommt auf Wunsch ein neues, unabhängiges Konto.
    r = adm.post("/api/v1/admin/einladungen", json={"name": "Tina", "email": "t@b.de"},
                 headers=csrf())
    assert r.status_code == 201


@pytest.mark.parametrize("feld", ["admin", "zuruecksetzen"])
@pytest.mark.parametrize("wert", [1, "true"])
def test_einladung_schalter_nur_echter_wahrheitswert(
    mit_paketen: TestClient, feld: str, wert: object,  # noqa: F811
) -> None:
    """Befund B-FUZZ-2 (API-Fuzzing): Admin-Recht und Zurücksetzen nur per JSON-Wahrheitswert.
    Zurücksetzen gilt nur für ein bestehendes Konto — sonst lehnte schon die Fachprüfung ab."""
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    person(mit_paketen, "da@b.de")
    email = "da@b.de" if feld == "zuruecksetzen" else "x@b.de"
    r = adm.post("/api/v1/admin/einladungen", headers=csrf(),
                 json={"name": "X", "email": email, feld: wert})
    assert r.status_code == 422
    assert adm.get("/api/v1/admin/einladungen").json()["einladungen"] == []
