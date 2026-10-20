"""Bearer-Anmeldung für /api/v1 (ohne Verwaltung, Konto, Statistik, /data).

Bearer nie auf /api/authz bzw. Verwaltung/Konto; ungültiger Bearer
fällt nicht aufs Cookie zurück, gültiger Bearer schlägt ein fremdes Cookie; gesperrt/anonymisiert/
abgelaufen/widerrufen → sofort 401; Lese-Token schreibt nie (auch mit kaputtem Körper), Vorrang
401 → 404 → 403 → 422 bleibt.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from typing import Any

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import apitoken, db, konten, verwaltung, web
from scanview_server.web import COOKIE

POI: dict[str, Any] = {"title": "Tür Nord", "category": "Zugang", "note": "",
                       "position": [1.5, -2.25, 3.0], "station": "S001"}
BASIS = "/api/v1/projekte/ost/pois"


def auth(roh: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {roh}"}


def _conn(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def token(c: TestClient, n: konten.Nutzer, name: str, umfang: str,
          *, now: int | None = None, gueltig_tage: int = 90) -> str:
    conn = _conn(c)
    try:
        roh, _info = apitoken.erzeugen(conn, n.id, name, umfang, gueltig_tage, now=now)
    finally:
        conn.close()
    return roh


def zaehle_ereignisse(c: TestClient) -> int:
    conn = _conn(c)
    try:
        return int(conn.execute("SELECT count(*) FROM ereignisse").fetchone()[0])
    finally:
        conn.close()


def poi_anzahl(c: TestClient) -> int:
    conn = _conn(c)
    try:
        return int(conn.execute("SELECT count(*) FROM pois").fetchone()[0])
    finally:
        conn.close()


def anon(c: TestClient) -> TestClient:
    """Frischer Client ohne Cookie auf derselben App."""
    return TestClient(c.app, base_url=ORIGIN)


@pytest.fixture
def bearbeiter(mit_paketen: TestClient) -> tuple[konten.Nutzer, TestClient]:  # noqa: F811
    return person(mit_paketen, "bea@test", grants={"ost": "bearbeiter"})


@pytest.fixture
def token_lesen(mit_paketen: TestClient,  # noqa: F811
                bearbeiter: tuple[konten.Nutzer, TestClient]) -> str:
    # Konto ist Bearbeiter — der Umfang „lesen“ allein muss Schreiben verhindern.
    return token(mit_paketen, bearbeiter[0], "t-lesen", "lesen")


@pytest.fixture
def token_schreiben(mit_paketen: TestClient,  # noqa: F811
                    bearbeiter: tuple[konten.Nutzer, TestClient]) -> str:
    return token(mit_paketen, bearbeiter[0], "t-schreiben", "schreiben")


@pytest.fixture
def token_admin_konto(mit_paketen: TestClient) -> str:  # noqa: F811
    n, _c = person(mit_paketen, "adm@test", admin=True)
    return token(mit_paketen, n, "t-admin", "schreiben")


# --- Grundfunktion ------------------------------------------------------------------------------

def test_bearer_pfad_ausnahmen_feste_liste() -> None:
    """E-T1: neue v1-Pfade sind automatisch token-fähig — die Ausnahmen sind diese feste Liste."""
    for p in ("/api/v1/projekte", "/api/v1/sitzung", "/api/v1/openapi.json",
              "/api/v1/projekte/ost/pois", "/api/v1/projekte/ost/pois/P-1",
              "/api/v1/projekte/ost/export.csv", "/api/v1/protokoll"):
        assert web.bearer_pfad(p), p
    for p in ("/api/v1/admin/nutzer", "/api/v1/admin", "/api/v1/statistik/ost",
              "/api/v1/statistik", "/api/v1/konto/tokens", "/api/v1/konto",
              "/api/authz", "/api/sitzung", "/api/projekte", "/api/v1", "/auth/abmelden",
              "/data/ost/project.json", "/", "/verwaltung"):
        assert not web.bearer_pfad(p), p


def test_lesen_mit_token(mit_paketen: TestClient, token_lesen: str) -> None:  # noqa: F811
    r = anon(mit_paketen).get("/api/v1/projekte", headers=auth(token_lesen))
    assert r.status_code == 200
    assert [p["id"] for p in r.json()["projekte"]] == ["ost"]
    r = anon(mit_paketen).get(BASIS, headers=auth(token_lesen))
    assert r.status_code == 200 and r.json() == {"pois": []}
    r = anon(mit_paketen).get("/api/v1/sitzung", headers=auth(token_lesen))
    assert r.status_code == 200 and r.json()["nutzer"]["email"] == "bea@test"


def test_ohne_alles_401(mit_paketen: TestClient) -> None:  # noqa: F811
    assert anon(mit_paketen).get("/api/v1/projekte").status_code == 401


def test_schreiben_ohne_csrf_header_mit_schreib_token(mit_paketen: TestClient,  # noqa: F811
                                                      token_schreiben: str) -> None:
    c = anon(mit_paketen)
    r = c.post(BASIS, headers=auth(token_schreiben), json={"id": "P-1", "daten": POI})
    assert r.status_code == 201
    r = c.patch(f"{BASIS}/P-1", headers=auth(token_schreiben),
                json={"daten": {"note": "x"}, "version": 1})
    assert r.status_code == 200
    r = c.delete(f"{BASIS}/P-1", headers=auth(token_schreiben), params={"version": 2})
    assert r.status_code == 204
    r = c.patch("/api/v1/projekte/ost", headers=auth(token_schreiben),
                json={"titel": "Neu", "version": 0})
    assert r.status_code == 200
    conn = _conn(mit_paketen)
    try:
        zeilen = conn.execute(
            "SELECT art, kanal, token_name FROM ereignisse WHERE project_id='ost' ORDER BY id",
        ).fetchall()
    finally:
        conn.close()
    assert [tuple(z) for z in zeilen] == [
        ("poi.angelegt", "api", "t-schreiben"), ("poi.geaendert", "api", "t-schreiben"),
        ("poi.geloescht", "api", "t-schreiben"), ("meta.geaendert", "api", "t-schreiben"),
    ]


def test_cookie_schreiben_bleibt_kanal_web(mit_paketen: TestClient,  # noqa: F811
                                           bearbeiter: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = bearbeiter
    assert c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI}).status_code == 201
    conn = _conn(mit_paketen)
    try:
        z = conn.execute("SELECT kanal, token_name FROM ereignisse ORDER BY id DESC").fetchone()
    finally:
        conn.close()
    assert tuple(z) == ("web", None)


def test_cookie_ohne_csrf_bleibt_403(bearbeiter: tuple[konten.Nutzer, TestClient]) -> None:
    _n, c = bearbeiter
    assert c.post(BASIS, json={"id": "P-1", "daten": POI}).status_code == 403


def test_schreib_token_betrachter_konto_403(mit_paketen: TestClient) -> None:  # noqa: F811
    """Rechte eines Tokens nie über das Konto hinaus."""
    n, _c = person(mit_paketen, "bet@test", grants={"ost": "betrachter"})
    roh = token(mit_paketen, n, "t", "schreiben")
    vor = zaehle_ereignisse(mit_paketen)
    r = anon(mit_paketen).post(BASIS, headers=auth(roh), json={"id": "P-1", "daten": POI})
    assert r.status_code == 403 and zaehle_ereignisse(mit_paketen) == vor


# --- Lese-Token schreibt nie ----------------------------------------------------------------------

@pytest.mark.parametrize("koerper", ["kaputt", "gueltig"])
@pytest.mark.parametrize("methode", ["post", "patch", "delete"])
def test_lese_token_schreibt_nicht(mit_paketen: TestClient,  # noqa: F811
                                   bearbeiter: tuple[konten.Nutzer, TestClient],
                                   token_lesen: str, methode: str, koerper: str) -> None:
    _n, c = bearbeiter
    assert c.post(BASIS, headers=csrf(), json={"id": "p1", "daten": POI}).status_code == 201
    vor, pois_vor = zaehle_ereignisse(mit_paketen), poi_anzahl(mit_paketen)
    kw: dict[str, Any] = {"headers": auth(token_lesen)}
    if methode == "post":
        pfad = BASIS
        kw["json"] = {"kaputt": 1} if koerper == "kaputt" else {"id": "p2", "daten": POI}
    elif methode == "patch":
        pfad = f"{BASIS}/p1"
        kw["json"] = {"kaputt": 1} if koerper == "kaputt" else {"daten": {"note": "x"},
                                                                 "version": 1}
    else:
        pfad = f"{BASIS}/p1"
        if koerper == "gueltig":
            kw["params"] = {"version": 1}
    r = getattr(anon(mit_paketen), methode)(pfad, **kw)
    assert r.status_code == 403, r.text
    assert r.json()["fehler"] == "Dieses Token darf nur lesen."
    assert zaehle_ereignisse(mit_paketen) == vor and poi_anzahl(mit_paketen) == pois_vor
    assert c.get(BASIS).json()["pois"][0]["version"] == 1


def test_lese_token_meta_patch_403(mit_paketen: TestClient, token_lesen: str) -> None:  # noqa: F811
    vor = zaehle_ereignisse(mit_paketen)
    for body in ({"titel": "x", "version": 0}, {"version": -1}):
        r = anon(mit_paketen).patch("/api/v1/projekte/ost", headers=auth(token_lesen), json=body)
        assert r.status_code == 403, body
    assert zaehle_ereignisse(mit_paketen) == vor


@pytest.mark.parametrize("koerper", [{"x": 1}, {"id": "p", "daten": POI}])
def test_vorrang_bleibt(mit_paketen: TestClient, token_lesen: str,  # noqa: F811
                        koerper: dict[str, Any]) -> None:
    """404 vor 403 vor 422: fremdes Projekt → 404, gleich ob der Körper kaputt ist oder nicht."""
    c = anon(mit_paketen)
    for pid in ("kirche", "gibt-es-nicht", "UNGÜLTIG"):
        r = c.post(f"/api/v1/projekte/{pid}/pois", headers=auth(token_lesen), json=koerper)
        assert r.status_code == 404, (pid, r.text)


def test_vorrang_ungueltiger_token_401_vor_422(mit_paketen: TestClient) -> None:  # noqa: F811
    r = anon(mit_paketen).post(BASIS, headers=auth("sv_falsch"), json={"x": 1})
    assert r.status_code == 401


def test_schreib_token_kaputter_koerper_422(mit_paketen: TestClient,  # noqa: F811
                                            token_schreiben: str) -> None:
    r = anon(mit_paketen).post(BASIS, headers=auth(token_schreiben), json={"x": 1})
    assert r.status_code == 422


# --- E-T3: nie außerhalb der erlaubten Pfade ------------------------------------------------------

@pytest.mark.parametrize("methode,pfad", [
    ("get", "/api/authz"), ("get", "/api/v1/admin/nutzer"), ("get", "/api/v1/admin/statistik"),
    ("get", "/api/v1/konto/tokens"), ("get", "/api/sitzung"), ("get", "/api/projekte"),
    ("get", "/api/v1/protokoll"), ("get", "/api/v1/protokoll.jsonl"),
    ("get", "/api/v1/protokoll?limit=0"), ("post", "/api/v1/statistik/ost"),
    ("post", "/api/v1/admin/einladungen"),
])
def test_bearer_ausserhalb_erlaubter_pfade(mit_paketen: TestClient,  # noqa: F811
                                           token_admin_konto: str, methode: str,
                                           pfad: str) -> None:
    """Token eines Admin-Kontos — sonst bestünden die Verwaltungsfälle aus dem falschen Grund."""
    kw: dict[str, Any] = {"headers": {**auth(token_admin_konto),
                                      "X-Forwarded-Uri": "/data/ost/cloud.copc.laz",
                                      "X-Forwarded-Method": "GET"}}
    if methode == "post":
        # Mit CSRF-Kopfzeilen: geprüft wird die Anmeldung, nicht die CSRF-Sperre davor.
        kw["headers"].update(csrf())
        kw["json"] = {"oeffnen": 1}
    vor = zaehle_ereignisse(mit_paketen)
    r = getattr(anon(mit_paketen), methode)(pfad, **kw)
    assert r.status_code in (401, 404), (pfad, r.status_code)
    assert zaehle_ereignisse(mit_paketen) == vor


def test_authz_mit_bearer_und_cookie_bleibt_cookie(mit_paketen: TestClient) -> None:  # noqa: F811
    """/api/authz ignoriert Authorization: es gilt allein das Cookie (wie bisher)."""
    _n, c = person(mit_paketen, "x@test", grants={"ost": "betrachter"})
    roh = token(mit_paketen, person(mit_paketen, "y@test", admin=True)[0], "t", "lesen")
    h = {**auth(roh), "X-Forwarded-Uri": "/data/kirche/cloud.copc.laz",
         "X-Forwarded-Method": "GET"}
    assert c.get("/api/authz", headers=h).status_code == 404
    h["X-Forwarded-Uri"] = "/data/ost/cloud.copc.laz"
    assert c.get("/api/authz", headers=h).status_code == 200


# --- Kein Rückfall, Token-Konto gewinnt -----------------------------------------------------------

def test_ungueltiger_bearer_faellt_nicht_aufs_cookie_zurueck(
    bearbeiter: tuple[konten.Nutzer, TestClient],
) -> None:
    _n, c = bearbeiter
    assert c.get("/api/v1/projekte").status_code == 200  # Cookie allein gilt
    for wert in ("sv_falsch", "", "x" * 300):
        r = c.get("/api/v1/projekte", headers=auth(wert))
        assert r.status_code == 401, wert
    assert c.get(BASIS, headers=auth("sv_falsch")).status_code == 401


def test_ungueltiger_bearer_mit_cookie_kein_csrf_weg(mit_paketen: TestClient,  # noqa: F811
                                                     bearbeiter: tuple[konten.Nutzer,
                                                                       TestClient]) -> None:
    """CSRF übersprungen heißt immer auch: Cookie ignoriert."""
    _n, c = bearbeiter
    vor = zaehle_ereignisse(mit_paketen)
    for body in ({"id": "P-1", "daten": POI}, {"x": 1}):
        r = c.post(BASIS, headers=auth("sv_falsch"), json=body)
        assert r.status_code == 401, body
    assert zaehle_ereignisse(mit_paketen) == vor and poi_anzahl(mit_paketen) == 0


@pytest.mark.parametrize("kopf", ["bearer sv_x", "BEARER sv_x", "bEaReR "])
def test_schema_ohne_gross_klein_ist_token_versuch(
    mit_paketen: TestClient, bearbeiter: tuple[konten.Nutzer, TestClient], kopf: str,  # noqa: F811
) -> None:
    """RFC 7235: das Schema ist unabhängig von Groß-/Kleinschreibung — `bearer x` ist ein
    Token-Versuch (401 ohne Cookie-Rückfall), auch beim Schreiben ohne CSRF-Kopfzeilen."""
    _n, c = bearbeiter
    assert c.get("/api/v1/projekte", headers={"Authorization": kopf}).status_code == 401
    r = c.post(BASIS, headers={"Authorization": kopf}, json={"id": "P-1", "daten": POI})
    assert r.status_code == 401 and poi_anzahl(mit_paketen) == 0


def test_schema_kleingeschrieben_mit_gueltigem_token(mit_paketen: TestClient,  # noqa: F811
                                                    token_lesen: str) -> None:
    r = anon(mit_paketen).get("/api/v1/projekte",
                              headers={"Authorization": f"bearer {token_lesen}"})
    assert r.status_code == 200


@pytest.mark.parametrize("kopf", ["Basic dXNlcjpwdw==", "Bearer", "Bearerx sv_x"])
def test_anderes_schema_ist_kein_bearer(bearbeiter: tuple[konten.Nutzer, TestClient],
                                        kopf: str) -> None:
    """Nur `Bearer <wert>` schaltet auf den Token-Weg; sonst gilt das Cookie — und dann auch die
    CSRF-Prüfung (kein Überspringen)."""
    _n, c = bearbeiter
    assert c.get("/api/v1/projekte", headers={"Authorization": kopf}).status_code == 200
    r = c.post(BASIS, headers={"Authorization": kopf}, json={"id": "P-1", "daten": POI})
    assert r.status_code == 403 and r.json()["fehler"] == "CSRF"


def test_token_konto_gewinnt_gegen_cookie(mit_paketen: TestClient) -> None:  # noqa: F811
    _a, cookie_a = person(mit_paketen, "a@test", grants={"kirche": "bearbeiter"})
    b, _cb = person(mit_paketen, "b@test", grants={"ost": "betrachter"})
    roh = token(mit_paketen, b, "t-b", "schreiben")
    r = cookie_a.get("/api/v1/sitzung", headers=auth(roh))
    assert r.json()["nutzer"]["email"] == "b@test"
    r = cookie_a.get("/api/v1/projekte", headers=auth(roh))
    assert [p["id"] for p in r.json()["projekte"]] == ["ost"]
    # Schreiben mit dem Token: B ist nur Betrachter → 403, obwohl A's Cookie Bearbeiter wäre.
    r = cookie_a.post("/api/v1/projekte/kirche/pois", headers={**auth(roh), **csrf()},
                      json={"id": "P-1", "daten": POI})
    assert r.status_code == 404


# --- Sofort 401 -----------------------------------------------------------------------------------

@pytest.mark.parametrize("fall", ["gesperrt", "anonymisiert", "abgelaufen", "widerrufen",
                                  "abgemeldet"])
def test_ungueltig_geworden_sofort_401(mit_paketen: TestClient, fall: str) -> None:  # noqa: F811
    n, _c = person(mit_paketen, "k@test", grants={"ost": "bearbeiter"})
    adm, _ca = person(mit_paketen, "adm@test", admin=True)
    jetzt = int(time.time())
    roh = token(mit_paketen, n, "t", "schreiben",
                now=(jetzt - 86400 - 1) if fall == "abgelaufen" else None, gueltig_tage=1)
    c = anon(mit_paketen)
    if fall != "abgelaufen":
        assert c.get("/api/v1/projekte", headers=auth(roh)).status_code == 200
    conn = _conn(mit_paketen)
    try:
        aktion: dict[str, Callable[[], object]] = {
            "gesperrt": lambda: verwaltung.sperren(conn, n.id, von=adm.id, kanal="cli"),
            "anonymisiert": lambda: verwaltung.anonymisieren(conn, n.id, von=adm.id, kanal="cli"),
            "abgelaufen": lambda: None,
            "widerrufen": lambda: apitoken.widerrufen(
                conn, n.id, apitoken.liste(conn, n.id)[0].id, von=n.id, kanal="web"),
            "abgemeldet": lambda: verwaltung.abmelden_ueberall(conn, n.id, von=adm.id,
                                                              kanal="cli"),
        }
        aktion[fall]()
    finally:
        conn.close()
    assert c.get("/api/v1/projekte", headers=auth(roh)).status_code == 401
    r = c.post(BASIS, headers=auth(roh), json={"id": "P-1", "daten": POI})
    assert r.status_code == 401


# --- Drossel (E-T2) -----------------------------------------------------------------------------

def test_drossel(mit_paketen: TestClient, token_lesen: str,  # noqa: F811
                 monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web, "TOKEN_PRO_MINUTE", 3)
    c = anon(mit_paketen)
    rs = [c.get("/api/v1/projekte", headers=auth(token_lesen)) for _ in range(4)]
    assert [r.status_code for r in rs] == [200, 200, 200, 429]
    assert rs[-1].json()["fehler"] == "Zu viele Anfragen mit diesem Token."
    assert 1 <= int(rs[-1].headers["retry-after"]) <= 60
    # Gilt je Token, auf allen Token-Wegen (auch über den Validierungs-Handler) …
    assert c.get("/api/v1/sitzung", headers=auth(token_lesen)).status_code == 429
    assert c.post(BASIS, headers=auth(token_lesen), json={"x": 1}).status_code == 429
    # … aber nicht für ein anderes Token desselben Kontos und nicht fürs Cookie.
    conn = _conn(mit_paketen)
    try:
        uid = conn.execute("SELECT id FROM users WHERE email='bea@test'").fetchone()[0]
        roh2, _i = apitoken.erzeugen(conn, uid, "t2", "lesen")
    finally:
        conn.close()
    assert c.get("/api/v1/projekte", headers=auth(roh2)).status_code == 200


def test_drossel_gleitendes_fenster(monkeypatch: pytest.MonkeyPatch) -> None:
    uhr = [1000.0]
    monkeypatch.setattr(web.time, "monotonic", lambda: uhr[0])
    monkeypatch.setattr(web, "TOKEN_PRO_MINUTE", 2)
    d = web.TokenDrossel()
    assert d.erlaubt(1) is None and d.erlaubt(1) is None
    assert d.erlaubt(1) == 60
    uhr[0] += 30
    assert d.erlaubt(1) == 30
    assert d.erlaubt(2) is None
    uhr[0] += 30.5
    assert d.erlaubt(1) is None


# --- OpenAPI ------------------------------------------------------------------------------------

def test_openapi_mit_token_und_security_schemes(mit_paketen: TestClient,  # noqa: F811
                                                token_admin_konto: str) -> None:
    r = anon(mit_paketen).get("/api/v1/openapi.json", headers=auth(token_admin_konto))
    assert r.status_code == 200
    o = r.json()
    assert o["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer"},
        "cookieAuth": {"type": "apiKey", "in": "cookie", "name": COOKIE},
    }
    assert o["security"] == [{"bearerAuth": []}, {"cookieAuth": []}]
    # Admin-Konto, aber per Token: Verwaltung, globales Protokoll, Statistik ausgeblendet.
    assert all(web.bearer_pfad(p) for p in o["paths"]), list(o["paths"])
    assert not any(p.startswith(("/api/v1/admin", "/api/v1/statistik")) for p in o["paths"])
    assert "/api/v1/protokoll" not in o["paths"]
    assert "/api/v1/projekte/{pid}/pois" in o["paths"]


def test_openapi_admin_cookie_sieht_weiter_alles(mit_paketen: TestClient) -> None:  # noqa: F811
    _n, c = person(mit_paketen, "adm@test", admin=True)
    o = c.get("/api/v1/openapi.json").json()
    assert "/api/v1/admin/nutzer" in o["paths"] and "/api/v1/protokoll" in o["paths"]
    assert set(o["components"]["securitySchemes"]) == {"bearerAuth", "cookieAuth"}
    # Das App-Schema selbst wird nicht verändert (zwischengespeichert von FastAPI).
    assert "securitySchemes" not in mit_paketen.app.openapi().get("components", {})  # type: ignore[attr-defined]


# --- Secrets ------------------------------------------------------------------------------------

def test_token_nie_im_log(mit_paketen: TestClient, token_lesen: str,  # noqa: F811
                          caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    c = anon(mit_paketen)
    c.get("/api/v1/projekte/fremd", headers=auth(token_lesen))
    c.get("/api/v1/projekte/ost", headers=auth(token_lesen))
    c.post(BASIS, headers=auth(token_lesen), json={"x": 1})
    c.post(BASIS, headers=auth(token_lesen), json={"id": "p", "daten": POI})
    c.get("/api/authz", headers={**auth(token_lesen), "X-Forwarded-Uri": "/data/ost/x.laz",
                                 "X-Forwarded-Method": "GET"})
    c.get("/api/v1/projekte", headers=auth(token_lesen + "x"))
    assert token_lesen not in caplog.text
    assert token_lesen[3:20] not in caplog.text


def test_aktueller_nutzer_direkt_nie_cookie_bei_bearer(
    mit_paketen: TestClient, bearbeiter: tuple[konten.Nutzer, TestClient],  # noqa: F811
) -> None:
    """Keine Aufrufer-Option mehr — Bearer-Pfad plus Bearer-Kopf heißt immer
    Token-Weg, auch bei einem direkten Aufruf von `web.aktueller_nutzer`. Außerhalb der
    Bearer-Pfade (/api/authz, /auth/*) bleibt es beim Cookie."""
    from starlette.requests import Request

    _n, c = bearbeiter
    cookie = c.cookies[COOKIE]

    def anfrage(pfad: str, kopf: str | None) -> Request:
        k = [(b"cookie", f"{COOKIE}={cookie}".encode())]
        if kopf is not None:
            k.append((b"authorization", kopf.encode()))
        return Request({"type": "http", "method": "GET", "path": pfad, "query_string": b"",
                        "headers": k, "app": mit_paketen.app})

    conn = _conn(mit_paketen)
    try:
        assert web.aktueller_nutzer(anfrage("/api/v1/projekte", "Bearer sv_falsch"), conn) is None
        assert web.aktueller_nutzer(anfrage("/api/v1/projekte", "bearer sv_x"), conn) is None
        n = web.aktueller_nutzer(anfrage("/api/v1/projekte", None), conn)
        assert n is not None and n.email == "bea@test"
        for pfad in ("/api/authz", "/auth/passkey/hinzufuegen/optionen", "/api/v1/admin/nutzer"):
            n = web.aktueller_nutzer(anfrage(pfad, "Bearer sv_falsch"), conn)
            assert n is not None and n.email == "bea@test", pfad
    finally:
        conn.close()


@pytest.mark.parametrize("endung", ["", ".jsonl"])
def test_projektverlauf_admin_token_nur_bearbeitungen(mit_paketen: TestClient,  # noqa: F811
                                                      endung: str) -> None:
    """Ein Admin-Konto sieht per Token im Projektverlauf nur Bearbeitungen
    (meta/poi/messung), keine Konto-/Rechte-Ereignisse — per Cookie weiterhin alles."""
    adm, adm_c = person(mit_paketen, "adm@test", admin=True)
    n, _c = person(mit_paketen, "x@test")
    conn = _conn(mit_paketen)
    try:
        verwaltung.recht_setzen(conn, n.id, "ost", "betrachter", von=adm.id, kanal="web")
    finally:
        conn.close()
    assert adm_c.post(BASIS, headers=csrf(), json={"id": "P-1", "daten": POI}).status_code == 201
    roh = token(mit_paketen, adm, "t-adm", "lesen")
    pfad = f"/api/v1/projekte/ost/protokoll{endung}"

    def arten(r: Any) -> set[str]:
        assert r.status_code == 200, r.text
        if endung:
            import json
            return {json.loads(z)["art"] for z in r.text.splitlines() if z}
        return {e["art"] for e in r.json()["ereignisse"]}

    per_cookie = arten(adm_c.get(pfad))
    per_token = arten(anon(mit_paketen).get(pfad, headers=auth(roh)))
    assert {"recht.vergeben", "poi.angelegt"} <= per_cookie
    assert per_token == {"poi.angelegt"}
