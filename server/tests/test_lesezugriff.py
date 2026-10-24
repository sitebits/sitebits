"""Lesezugriffsprotokoll: wer hat wann welches Projekt gelesen — datensparsam, aggregiert je
Zeitfenster, unveränderlich, mit Aufbewahrungsfrist, nur für Admins (per Cookie) einsehbar."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from conftest import ORIGIN, csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import apitoken, db, konten, lesezugriff, projektverwaltung, verwaltung
from scanview_server.config import Settings
from scanview_server.web import COOKIE, create_app

PID = "ost"
DATA = {"X-Forwarded-Method": "GET", "X-Forwarded-Uri": f"/data/{PID}/project.json"}
LESE_ROUTEN = (
    f"/api/v1/projekte/{PID}/lesezugriffe", f"/api/v1/projekte/{PID}/lesezugriffe.jsonl",
    "/api/v1/lesezugriffe", "/api/v1/lesezugriffe.jsonl",
)


def _conn(c: TestClient) -> sqlite3.Connection:
    return db.connect(c.app.state.settings.db_path)  # type: ignore[attr-defined]


def zeilen(c: TestClient) -> list[sqlite3.Row]:
    conn = _conn(c)
    try:
        return conn.execute("SELECT * FROM lesezugriffe ORDER BY id").fetchall()
    finally:
        conn.close()


def eintraege(c: TestClient) -> list[tuple[int, str, str, str, str, str]]:
    return [(int(r["nutzer_id"]), r["project_id"], r["art"], r["objekt"], r["kanal"],
             r["token_name"]) for r in zeilen(c)]


def lese_token(c: TestClient, n: konten.Nutzer, name: str = "Skript") -> str:
    conn = _conn(c)
    try:
        roh, _info = apitoken.erzeugen(conn, n.id, name, "lesen", 90)
    finally:
        conn.close()
    return roh


def admin_person(c: TestClient) -> tuple[konten.Nutzer, TestClient]:
    return person(c, "chef@b.de", admin=True, name="Chef")


# --- je Zugriffsart genau ein Eintrag ---------------------------------------------------------

def test_authz_200_erfasst_paket(mit_paketen: TestClient) -> None:  # noqa: F811
    n, p = person(mit_paketen, "b@b.de", grants={PID: "betrachter"})
    assert p.get("/api/authz", headers=DATA).status_code == 200
    assert eintraege(mit_paketen) == [(n.id, PID, "paket", "", "web", "")]
    r = zeilen(mit_paketen)[0]
    assert r["zeit"] >= r["fenster"] and r["fenster"] % lesezugriff.FENSTER_S == 0


@pytest.mark.parametrize(("pfad", "art", "objekt"), [
    (f"/api/v1/projekte/{PID}", "oeffnen", ""),
    (f"/api/v1/projekte/{PID}/export.csv", "export", "csv"),
    (f"/api/v1/projekte/{PID}/export.json", "export", "json"),
    (f"/api/v1/projekte/{PID}/protokoll.jsonl", "export", "verlauf"),
    (f"/api/v1/projekte/{PID}/pois", "api", "pois"),
    (f"/api/v1/projekte/{PID}/messungen", "api", "messungen"),
    (f"/api/v1/projekte/{PID}/protokoll", "api", "verlauf"),
])
def test_je_zugriffsart_genau_ein_eintrag(mit_paketen: TestClient, pfad: str,  # noqa: F811
                                          art: str, objekt: str) -> None:
    n, p = person(mit_paketen, "e@b.de", grants={PID: "bearbeiter"})
    assert p.get(pfad).status_code == 200
    assert eintraege(mit_paketen) == [(n.id, PID, art, objekt, "web", "")]


def test_lese_token_erfasst_kanal_api_mit_namen(mit_paketen: TestClient) -> None:  # noqa: F811
    n, _p = person(mit_paketen, "t@b.de", grants={PID: "betrachter"})
    roh = lese_token(mit_paketen, n, "Auswertung")
    r = mit_paketen.get(f"/api/v1/projekte/{PID}/pois", headers={"Authorization": f"Bearer {roh}"})
    assert r.status_code == 200
    assert eintraege(mit_paketen) == [(n.id, PID, "api", "pois", "api", "Auswertung")]


# --- Aggregation ------------------------------------------------------------------------------

def test_fenster_aggregiert(mit_paketen: TestClient,  # noqa: F811
                            monkeypatch: pytest.MonkeyPatch) -> None:
    _n, p = person(mit_paketen, "a@b.de", grants={PID: "betrachter"})
    start = 1_800_000_000 // lesezugriff.FENSTER_S * lesezugriff.FENSTER_S
    monkeypatch.setattr(lesezugriff, "_jetzt", lambda now=None: start + 5 if now is None else now)
    for _ in range(50):
        assert p.get("/api/authz", headers=DATA).status_code == 200
    assert len(zeilen(mit_paketen)) == 1
    monkeypatch.setattr(lesezugriff, "_jetzt",
                        lambda now=None: start + lesezugriff.FENSTER_S if now is None else now)
    for _ in range(3):
        assert p.get("/api/authz", headers=DATA).status_code == 200
    assert len(zeilen(mit_paketen)) == 2


def test_schreibt_je_fenster_nur_einmal(mit_paketen: TestClient) -> None:  # noqa: F811
    """Der prozessweite Merker hält den heißen Pfad frei von Schreibversuchen: ein Trigger
    zählt jeden INSERT-Versuch (auch den von `INSERT OR IGNORE` abgewiesenen)."""
    _n, p = person(mit_paketen, "m@b.de", grants={PID: "betrachter"})
    conn = _conn(mit_paketen)
    try:
        conn.execute("CREATE TABLE versuche (x INTEGER)")
        conn.execute("CREATE TRIGGER zaehlen BEFORE INSERT ON lesezugriffe"
                     " BEGIN INSERT INTO versuche VALUES (1); END")
        for _ in range(20):
            assert p.get("/api/authz", headers=DATA).status_code == 200
        assert conn.execute("SELECT COUNT(*) FROM versuche").fetchone()[0] == 1
    finally:
        conn.close()


# --- kein Eintrag ohne erfolgreiche Rechteprüfung ---------------------------------------------

def test_kein_eintrag_bei_401_404_403(mit_paketen: TestClient) -> None:  # noqa: F811
    c = mit_paketen
    assert c.get("/api/authz", headers=DATA).status_code == 401
    assert c.get(f"/api/v1/projekte/{PID}").status_code == 401
    _n, p = person(c, "b@b.de", grants={"kirche": "betrachter"})
    assert p.get("/api/authz", headers=DATA).status_code == 404
    for pfad in (f"/api/v1/projekte/{PID}", f"/api/v1/projekte/{PID}/pois",
                 f"/api/v1/projekte/{PID}/messungen", f"/api/v1/projekte/{PID}/export.csv",
                 f"/api/v1/projekte/{PID}/protokoll"):
        assert p.get(pfad).status_code == 404
    # 403: Betrachter auf Export und Verlauf des eigenen Projekts
    assert p.get("/api/v1/projekte/kirche/export.csv").status_code == 403
    assert p.get("/api/v1/projekte/kirche/export.json").status_code == 403
    assert p.get("/api/v1/projekte/kirche/protokoll").status_code == 403
    assert p.get("/api/v1/projekte/kirche/protokoll.jsonl").status_code == 403
    assert zeilen(c) == []


def test_projektliste_und_globales_protokoll_nicht_erfasst(
        mit_paketen: TestClient) -> None:  # noqa: F811
    _a, admin = admin_person(mit_paketen)
    assert admin.get("/api/v1/projekte").status_code == 200
    assert admin.get("/api/v1/protokoll").status_code == 200
    assert zeilen(mit_paketen) == []


# --- Sichtbarkeit -----------------------------------------------------------------------------

@pytest.mark.parametrize("rolle", ["betrachter", "bearbeiter"])
@pytest.mark.parametrize("pfad", [*LESE_ROUTEN, *(p + "?limit=abc&vor=-1&nutzer=x&projekt=../"
                                                  for p in LESE_ROUTEN)])
def test_nur_admin_sieht_das_protokoll(mit_paketen: TestClient, rolle: str,  # noqa: F811
                                       pfad: str) -> None:
    _n, p = person(mit_paketen, f"{rolle}@b.de", grants={PID: rolle})
    p.get(f"/api/v1/projekte/{PID}")
    r = p.get(pfad)
    assert r.status_code == 404 and r.json() == {"dienst": "scanview", "fehler": "nicht gefunden"}


@pytest.mark.parametrize("pfad", LESE_ROUTEN)
def test_admin_per_token_bekommt_404(mit_paketen: TestClient, pfad: str) -> None:  # noqa: F811
    a, _c = admin_person(mit_paketen)
    roh = lese_token(mit_paketen, a)
    r = mit_paketen.get(pfad, headers={"Authorization": f"Bearer {roh}"})
    assert r.status_code == 404
    r = mit_paketen.get(pfad + "?limit=abc", headers={"Authorization": f"Bearer {roh}"})
    assert r.status_code == 404


@pytest.mark.parametrize("pfad", LESE_ROUTEN)
def test_ohne_anmeldung_401(mit_paketen: TestClient, pfad: str) -> None:  # noqa: F811
    assert mit_paketen.get(pfad).status_code == 401


def test_openapi_nennt_die_routen_nur_admins(mit_paketen: TestClient) -> None:  # noqa: F811
    _n, p = person(mit_paketen, "b@b.de", grants={PID: "betrachter"})
    _a, admin = admin_person(mit_paketen)
    offen = "".join(p.get("/api/v1/openapi.json").json()["paths"])
    assert "lesezugriffe" not in offen
    assert "/api/v1/lesezugriffe" in admin.get("/api/v1/openapi.json").json()["paths"]


def test_admin_sieht_eintraege_aller_konten(mit_paketen: TestClient) -> None:  # noqa: F811
    n1, p1 = person(mit_paketen, "eins@b.de", grants={PID: "betrachter"}, name="Eins")
    n2, p2 = person(mit_paketen, "zwei@b.de", grants={PID: "bearbeiter"}, name="Zwei")
    p1.get(f"/api/v1/projekte/{PID}")
    p2.get(f"/api/v1/projekte/{PID}/pois")
    _a, admin = admin_person(mit_paketen)
    r = admin.get(f"/api/v1/projekte/{PID}/lesezugriffe")
    assert r.status_code == 200
    d = r.json()
    assert [(e["nutzer"], e["art"], e["objekt"]) for e in d["lesezugriffe"]] == [
        ("Zwei", "api", "pois"), ("Eins", "oeffnen", ""),
    ]
    assert {e["nutzerId"] for e in d["lesezugriffe"]} == {n1.id, n2.id}
    assert d["weiter"] is None
    assert {"zeit", "kanal", "projekt", "token", "id"} <= set(d["lesezugriffe"][0])
    g = admin.get("/api/v1/lesezugriffe", params={"nutzer": n1.id}).json()["lesezugriffe"]
    assert [e["nutzer"] for e in g] == ["Eins"]
    g = admin.get("/api/v1/lesezugriffe", params={"projekt": "kirche"}).json()["lesezugriffe"]
    assert g == []
    assert admin.get("/api/v1/lesezugriffe", params={"projekt": "../x"}).status_code == 422


def test_seitenweise_nachladen(mit_paketen: TestClient) -> None:  # noqa: F811
    _n, p = person(mit_paketen, "e@b.de", grants={PID: "bearbeiter"})
    for pfad in ("", "/pois", "/messungen", "/export.csv", "/export.json"):
        p.get(f"/api/v1/projekte/{PID}{pfad}")
    _a, admin = admin_person(mit_paketen)
    erste = admin.get("/api/v1/lesezugriffe", params={"limit": 2}).json()
    assert len(erste["lesezugriffe"]) == 2 and erste["weiter"] is not None
    zweite = admin.get("/api/v1/lesezugriffe",
                       params={"limit": 10, "vor": erste["weiter"]}).json()
    assert len(zweite["lesezugriffe"]) == 3 and zweite["weiter"] is None
    ids = [e["id"] for e in erste["lesezugriffe"] + zweite["lesezugriffe"]]
    assert ids == sorted(ids, reverse=True) and len(set(ids)) == 5


def test_jsonl_export(mit_paketen: TestClient) -> None:  # noqa: F811
    n, p = person(mit_paketen, "e@b.de", grants={PID: "bearbeiter"}, name="Erika")
    p.get(f"/api/v1/projekte/{PID}")
    _a, admin = admin_person(mit_paketen)
    r = admin.get("/api/v1/lesezugriffe.jsonl")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    assert 'filename="lesezugriffe.jsonl"' in r.headers["content-disposition"]
    zeile = r.text.splitlines()[0]
    assert '"nutzer": "Erika"' in zeile and f'"nutzerId": {n.id}' in zeile
    r = admin.get(f"/api/v1/projekte/{PID}/lesezugriffe.jsonl")
    assert r.status_code == 200 and "Erika" in r.text


def test_betrachter_und_bearbeiter_sehen_keine_fremden_eintraege(
        mit_paketen: TestClient) -> None:  # noqa: F811
    _f, fremd = person(mit_paketen, "fremd@b.de", grants={PID: "betrachter"}, name="Fremdling")
    fremd.get(f"/api/v1/projekte/{PID}")
    _e, bearbeiter = person(mit_paketen, "bea@b.de", grants={PID: "bearbeiter"})
    r = bearbeiter.get(f"/api/v1/projekte/{PID}/protokoll")
    assert r.status_code == 200 and set(r.json()) == {"ereignisse", "weiter"}
    assert "Fremdling" not in r.text and "lesezugriff" not in r.text.lower()
    assert "Fremdling" not in bearbeiter.get(f"/api/v1/projekte/{PID}/protokoll.jsonl").text


def test_lesen_des_protokolls_wird_selbst_erfasst(mit_paketen: TestClient) -> None:  # noqa: F811
    _n, p = person(mit_paketen, "b@b.de", grants={PID: "betrachter"})
    _a, admin = admin_person(mit_paketen)
    leer = admin.get("/api/v1/lesezugriffe").json()
    assert leer["lesezugriffe"] == [] and zeilen(mit_paketen) == []  # leere Seite: kein Eintrag
    p.get(f"/api/v1/projekte/{PID}")
    p.get("/api/v1/projekte/kirche")  # 404, kein Eintrag
    a_id = konten.konto_id(_conn(mit_paketen), "chef@b.de")
    assert admin.get("/api/v1/lesezugriffe").status_code == 200
    assert admin.get(f"/api/v1/projekte/{PID}/lesezugriffe.jsonl").status_code == 200
    assert (a_id, PID, "api", "lesezugriffe", "web", "") in eintraege(mit_paketen)
    assert (a_id, PID, "export", "lesezugriffe", "web", "") in eintraege(mit_paketen)
    # Die Einsicht erscheint beim nächsten Lesen selbst im Protokoll.
    namen = [(e["nutzer"], e["objekt"])
             for e in admin.get("/api/v1/lesezugriffe").json()["lesezugriffe"]]
    assert ("Chef", "lesezugriffe") in namen


# --- Unveränderlich ---------------------------------------------------------------------------

def _roh(tmp_path: Path) -> sqlite3.Connection:
    """Bloße Verbindung ohne `recursive_triggers` — so wie eine sqlite3-Shell."""
    db.migrate(db.connect(tmp_path / "x.db"))
    conn = sqlite3.connect(tmp_path / "x.db", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("INSERT INTO users (id, email, name, is_admin, created_at) "
                 "VALUES (1, 'a@b.de', 'A', 0, 1)")
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="p", art="oeffnen", kanal="web",
                         token_name=None, now=1000)
    return conn


def test_update_ist_verboten(tmp_path: Path) -> None:
    conn = _roh(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="unveränderlich"):
        conn.execute("UPDATE lesezugriffe SET project_id='q'")


def test_insert_or_replace_ist_verboten(tmp_path: Path) -> None:
    conn = _roh(tmp_path)
    with pytest.raises(sqlite3.IntegrityError, match="unveränderlich"):
        conn.execute("INSERT OR REPLACE INTO lesezugriffe (id, zeit, fenster, nutzer_id, kanal,"
                     " project_id, art) VALUES (1, 2, 0, 1, 'web', 'p', 'oeffnen')")
    assert conn.execute("SELECT COUNT(*) FROM lesezugriffe").fetchone()[0] == 1


def test_replace_ueber_den_eindeutigen_schluessel_ersetzt_keine_zeile(tmp_path: Path) -> None:
    """Auch ohne id darf `INSERT OR REPLACE` mit gleichem Schlüssel und Fenster nichts umschreiben
    (sonst ließe sich `zeit` nachträglich ändern)."""
    conn = _roh(tmp_path)
    vorher = tuple(conn.execute("SELECT * FROM lesezugriffe").fetchone())
    conn.execute("INSERT OR REPLACE INTO lesezugriffe (zeit, fenster, nutzer_id, kanal,"
                 " token_name, project_id, art, objekt) VALUES (?,?,1,'web','','p','oeffnen','')",
                 (vorher[1] + 5, vorher[2]))
    assert [tuple(r) for r in conn.execute("SELECT * FROM lesezugriffe")] == [vorher]
    # Ein doppeltes `INSERT OR IGNORE` bleibt folgenlos und wirft nicht (authz ist fail-closed).
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="p", art="oeffnen", kanal="web",
                         token_name=None, now=vorher[1] + 5)
    assert conn.execute("SELECT COUNT(*) FROM lesezugriffe").fetchone()[0] == 1


def test_einziger_loeschweg_ist_aufraeumen() -> None:
    quelle = Path(lesezugriff.__file__).parent
    treffer = [p.name for p in quelle.glob("*.py")
               if re.search(r"DELETE\s+FROM\s+lesezugriffe", p.read_text(encoding="utf-8"), re.I)]
    assert treffer == ["lesezugriff.py"]
    text = (quelle / "lesezugriff.py").read_text(encoding="utf-8")
    assert len(re.findall(r"DELETE\s+FROM\s+lesezugriffe", text, re.I)) == 1


# --- Aufbewahrung -----------------------------------------------------------------------------

def test_aufraeumen_loescht_nur_abgelaufene(tmp_path: Path) -> None:
    conn = _roh(tmp_path)
    jetzt = 1_800_000_000
    tage = 90
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="alt", art="oeffnen", kanal="web",
                         token_name=None, now=jetzt - tage * 86400 - 1)
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="jung", art="oeffnen", kanal="web",
                         token_name=None, now=jetzt - tage * 86400 + 60)
    assert lesezugriff.aufraeumen(conn, tage, now=jetzt) == 2  # auch der Eintrag aus now=1000
    assert [r["project_id"] for r in conn.execute("SELECT project_id FROM lesezugriffe")] == [
        "jung"]


def test_konten_aufraeumen_raeumt_lesezugriffe_mit(tmp_path: Path) -> None:
    conn = _roh(tmp_path)
    out = konten.aufraeumen(conn, now=1_800_000_000, lesezugriffe_tage=30)
    assert out["lesezugriffe"] == 1


def test_erfassen_raeumt_hoechstens_einmal_je_stunde_auf(tmp_path: Path) -> None:
    conn = _roh(tmp_path)
    m = lesezugriff.Merker()
    jetzt = 1_800_000_000
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="a", art="oeffnen", kanal="web",
                         token_name=None, now=jetzt, merker=m, tage=90)
    assert conn.execute("SELECT COUNT(*) FROM lesezugriffe").fetchone()[0] == 1  # alt gelöscht
    conn.execute("INSERT INTO lesezugriffe (zeit, fenster, nutzer_id, kanal, project_id, art)"
                 " VALUES (5, 0, 1, 'web', 'alt', 'oeffnen')")
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="b", art="oeffnen", kanal="web",
                         token_name=None, now=jetzt + 600, merker=m, tage=90)
    assert conn.execute("SELECT COUNT(*) FROM lesezugriffe WHERE project_id='alt'"
                        ).fetchone()[0] == 1  # noch keine Stunde vergangen
    lesezugriff.erfassen(conn, nutzer_id=1, project_id="c", art="oeffnen", kanal="web",
                         token_name=None, now=jetzt + 3700, merker=m, tage=90)
    assert conn.execute("SELECT COUNT(*) FROM lesezugriffe WHERE project_id='alt'"
                        ).fetchone()[0] == 0


def _umgebung(monkeypatch: pytest.MonkeyPatch, tage: str | None) -> None:
    for k, v in {"SCANVIEW_DATA": "/d", "SCANVIEW_STATE": "/s", "SCANVIEW_ORIGIN": ORIGIN,
                 "SCANVIEW_RP_ID": "x"}.items():
        monkeypatch.setenv(k, v)
    if tage is None:
        monkeypatch.delenv("SCANVIEW_LESEZUGRIFFE_TAGE", raising=False)
    else:
        monkeypatch.setenv("SCANVIEW_LESEZUGRIFFE_TAGE", tage)


@pytest.mark.parametrize(("roh", "erwartet"), [
    (None, 90), ("0", 0), ("7", 7), ("730", 730), (" 30 ", 30),
])
def test_tage_aus_umgebung(monkeypatch: pytest.MonkeyPatch, roh: str | None,
                           erwartet: int) -> None:
    _umgebung(monkeypatch, roh)
    assert Settings.from_env().lesezugriffe_tage == erwartet


@pytest.mark.parametrize("roh", ["5", "abc", "6", "731", "-1", "", "9.5", "١٢"])
def test_ungueltige_tage_brechen_den_start_ab(monkeypatch: pytest.MonkeyPatch,
                                              roh: str) -> None:
    _umgebung(monkeypatch, roh)
    if roh == "":
        assert Settings.from_env().lesezugriffe_tage == 90  # leer = Standard
        return
    with pytest.raises(SystemExit, match="SCANVIEW_LESEZUGRIFFE_TAGE"):
        Settings.from_env()


def test_abgeschaltet_erfasst_nichts(settings: Settings) -> None:
    import dataclasses
    app = create_app(dataclasses.replace(settings, lesezugriffe_tage=0))
    from pakete import lege_paket
    lege_paket(settings.data_dir, PID)
    app.state.katalog.laden()
    with TestClient(app, base_url=ORIGIN) as c:
        _n, p = person(c, "b@b.de", grants={PID: "betrachter"})
        assert p.get(f"/api/v1/projekte/{PID}").status_code == 200
        assert p.get("/api/authz", headers=DATA).status_code == 200
        assert zeilen(c) == []


def test_abgeschaltet_loescht_beim_start_vorhandene_eintraege(settings: Settings) -> None:
    import dataclasses
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, is_admin=False)
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    lesezugriff.erfassen(conn, nutzer_id=n.id, project_id="p", art="oeffnen", kanal="web",
                         token_name=None, now=1000)
    conn.close()
    app = create_app(dataclasses.replace(settings, lesezugriffe_tage=0))
    with TestClient(app, base_url=ORIGIN) as c:
        assert zeilen(c) == []


def test_logzeile_beim_start(settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    import dataclasses
    import logging
    with caplog.at_level(logging.INFO, logger="scanview.lesezugriff"):
        create_app(settings)
    zeilen_an = [(r.levelno, r.getMessage()) for r in caplog.records
                 if r.name == "scanview.lesezugriff"]
    assert len(zeilen_an) == 1
    assert zeilen_an[0][0] == logging.INFO
    assert "90 Tage" in zeilen_an[0][1] and "0 abgelaufene Einträge gelöscht" in zeilen_an[0][1]
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="scanview.lesezugriff"):
        create_app(dataclasses.replace(settings, lesezugriffe_tage=0))
    aus = [(r.levelno, r.getMessage()) for r in caplog.records if r.name == "scanview.lesezugriff"]
    assert len(aus) == 1 and aus[0][0] == logging.WARNING
    assert "abgeschaltet" in aus[0][1] and "gelöscht" in aus[0][1]


def test_start_loggt_die_zahl_geloeschter_eintraege(
        settings: Settings, caplog: pytest.LogCaptureFixture) -> None:
    import dataclasses
    import logging
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={}, is_admin=False)
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    for i, p in enumerate(("a", "b", "c")):
        lesezugriff.erfassen(conn, nutzer_id=n.id, project_id=p, art="oeffnen", kanal="web",
                             token_name=None, now=1000 + i)
    conn.close()
    with caplog.at_level(logging.INFO, logger="scanview.lesezugriff"):
        create_app(dataclasses.replace(settings, lesezugriffe_tage=0))
    texte = [r.getMessage() for r in caplog.records if r.name == "scanview.lesezugriff"]
    assert len(texte) == 1 and "3 vorhandene Einträge" in texte[0]


def test_abgelaufene_eintraege_werden_nie_ausgeliefert(
        mit_paketen: TestClient) -> None:  # noqa: F811
    """Ruhige Instanz: seit Stunden nichts erfasst, also kein stündliches Aufräumen —
    die Einsicht selbst räumt vorher auf."""
    n, _p = person(mit_paketen, "alt@b.de", grants={PID: "betrachter"}, name="Alt")
    _a, admin = admin_person(mit_paketen)
    conn = _conn(mit_paketen)
    try:
        lesezugriff.erfassen(conn, nutzer_id=n.id, project_id=PID, art="oeffnen", kanal="web",
                             token_name=None, now=1000)
    finally:
        conn.close()
    assert len(zeilen(mit_paketen)) == 1
    for pfad in LESE_ROUTEN:
        r = admin.get(pfad)
        assert r.status_code == 200 and "Alt" not in r.text, pfad
    assert zeilen(mit_paketen) == []


# --- Ausfall: fail-closed ---------------------------------------------------------------------

def test_nicht_beschreibbares_protokoll_liefert_nichts_aus(
        mit_paketen: TestClient) -> None:  # noqa: F811
    _n, p = person(mit_paketen, "f@b.de", grants={PID: "betrachter"})
    conn = _conn(mit_paketen)
    try:
        conn.execute("CREATE TRIGGER ausfall BEFORE INSERT ON lesezugriffe"
                     " BEGIN SELECT RAISE(ABORT, 'Protokoll nicht beschreibbar'); END")
    finally:
        conn.close()
    roh = TestClient(mit_paketen.app, base_url=ORIGIN, raise_server_exceptions=False,
                     cookies={COOKIE: p.cookies.get(COOKIE) or ""})
    for pfad, koerper in ((f"/api/v1/projekte/{PID}", '"titel"'),
                          (f"/api/v1/projekte/{PID}/pois", '"pois"')):
        r = roh.get(pfad)
        assert r.status_code == 500 and koerper not in r.text, pfad
    r = roh.get("/api/authz", headers=DATA)
    assert r.status_code == 500


# --- Datensparsamkeit -------------------------------------------------------------------------

def test_keine_geheimnisse_im_protokoll(mit_paketen: TestClient) -> None:  # noqa: F811
    n, p = person(mit_paketen, "geheim@privat.example", grants={PID: "bearbeiter"},
                  name="Geheim")
    roh = lese_token(mit_paketen, n, "Skript")
    sitzung = p.cookies.get(COOKIE) or ""
    p.get(f"/api/v1/projekte/{PID}", headers={"User-Agent": "Mozilla/9 Sonderbar"})
    p.get("/api/authz", headers={**DATA, "X-Forwarded-For": "203.0.113.77"})
    mit_paketen.get(f"/api/v1/projekte/{PID}/pois", headers={"Authorization": f"Bearer {roh}"})
    conn = _conn(mit_paketen)
    try:
        spalten = {r[1] for r in conn.execute("PRAGMA table_info(lesezugriffe)")}
        alles = "\n".join(
            "|".join(str(w) for w in r) for r in conn.execute("SELECT * FROM lesezugriffe")
        )
    finally:
        conn.close()
    assert spalten == {"id", "zeit", "fenster", "nutzer_id", "kanal", "token_name", "project_id",
                       "art", "objekt"}
    assert len(zeilen(mit_paketen)) == 3
    for verboten in (roh, hashlib.sha256(roh.encode()).hexdigest(), sitzung,
                     hashlib.sha256(sitzung.encode()).hexdigest(), "203.0.113.77", "Mozilla",
                     "Sonderbar", "geheim@privat.example", "testclient", "privat.example"):
        assert verboten not in alles


def test_anonymisiertes_konto_erscheint_als_geloeschtes_konto(
        mit_paketen: TestClient) -> None:  # noqa: F811
    n, p = person(mit_paketen, "weg@b.de", grants={PID: "betrachter"}, name="Erika Muster")
    a, admin = admin_person(mit_paketen)
    p.get(f"/api/v1/projekte/{PID}")
    conn = _conn(mit_paketen)
    try:
        verwaltung.anonymisieren(conn, n.id, von=a.id, kanal="web")
    finally:
        conn.close()
    d = admin.get("/api/v1/lesezugriffe").json()["lesezugriffe"]
    assert [e["nutzer"] for e in d] == [f"Gelöschtes Konto {n.id}"]
    assert "Erika" not in admin.get("/api/v1/lesezugriffe.jsonl").text


# --- Vollständigkeit --------------------------------------------------------------------------

def _alle_routen(routen: Any) -> list[tuple[str, set[str]]]:
    """Pfad und Methoden aller Routen; eingebundene Router (`include_router`) sind in dieser
    FastAPI-Version eigene Routenobjekte, deren Kinder unter `original_router` liegen."""
    out: list[tuple[str, set[str]]] = []
    for r in routen:
        kinder = getattr(r, "original_router", None)
        if kinder is not None:
            out.extend(_alle_routen(kinder.routes))
        elif hasattr(r, "methods") and r.methods:
            out.append((r.path, set(r.methods)))
    return out


# Leserouten unter /api/v1/projekte/{pid}, die bewusst keinen Eintrag erzeugen, mit Begründung.
AUSNAHMEN: dict[str, str] = {
    "/api/v1/projekte/{pid}/auswertungen":
        "nur Auftragsstatus, kein Inhalt; braucht jobs.db (Inhalte siehe test_auswertungen)",
    "/api/v1/projekte/{pid}/auswertungen/{aid}":
        "nur Status und Urteil des Auftrags, kein Inhalt aus dem Paket",
    "/api/v1/projekte/{pid}/auswertungen/{aid}/ergebnis":
        "wird erfasst (art api, objekt auswertung/<id>) — geprüft in test_auswertungen.py",
    "/api/v1/projekte/{pid}/auswertungen/{aid}/bericht":
        "wird erfasst (art api, objekt auswertung/<id>) — geprüft in test_auswertungen.py",
}


def test_jede_leseroute_erzeugt_einen_eintrag_oder_ist_begruendet_ausgenommen(
        mit_paketen: TestClient) -> None:  # noqa: F811
    app: Any = mit_paketen.app
    routen = sorted(
        pfad for pfad, methoden in _alle_routen(app.routes)
        if "GET" in methoden and pfad.startswith("/api/v1/projekte/{pid}")
    )
    # Auch Routen außerhalb der OpenAPI-Beschreibung (include_in_schema=False) zählen.
    sichtbar = {p for p, ops in app.openapi()["paths"].items()
                if "get" in ops and p.startswith("/api/v1/projekte/{pid}")}
    assert sichtbar <= set(routen)
    assert routen, "keine Leserouten gefunden"
    # Die Routen, die das Protokoll selbst lesen, zuletzt — erst dann gibt es etwas anzuzeigen.
    routen.sort(key=lambda p: "lesezugriffe" in p)
    a, admin = admin_person(mit_paketen)
    # Befund-Fäden: Projekt als Bauprojekt (Schalter an) mit einem POI für die Faden-Route.
    conn = _conn(mit_paketen)
    try:
        projektverwaltung.einrichten(conn, PID, "bauprojekt", von=a.id)
    finally:
        conn.close()
    assert admin.post(f"/api/v1/projekte/{PID}/pois", headers=csrf(), json={
        "id": "P-1", "daten": {"title": "T", "position": [0, 0, 0]}}).status_code == 201
    ohne: list[str] = []
    for pfad in routen:
        if pfad in AUSNAHMEN:
            assert AUSNAHMEN[pfad].strip(), "Ausnahme ohne Begründung"
            continue
        assert re.fullmatch(r"/api/v1/projekte/\{pid\}(/[a-z.-]+|/pois/\{eid\}/faden)?", pfad), (
            f"{pfad}: weitere Pfadparameter — Test ergänzen oder Ausnahme begründen")
        vorher = len(zeilen(mit_paketen))
        r = admin.get(pfad.replace("{pid}", PID).replace("{eid}", "P-1"))
        assert r.status_code == 200, (pfad, r.status_code)
        if len(zeilen(mit_paketen)) != vorher + 1:
            ohne.append(pfad)
    assert ohne == [], f"Leseroute ohne Protokolleintrag und ohne begründete Ausnahme: {ohne}"
