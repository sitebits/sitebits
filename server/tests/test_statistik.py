import hashlib
from pathlib import Path

from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import db, statistik

S = "/api/v1/statistik/ost"


def test_drossel_schluessel_gesalzen_nicht_ueber_sha256_reproduzierbar() -> None:
    """Ein ungesalzenes sha256(uid:pid) wäre trotz Hash zurückrechenbar
    (dieselbe uid+pid ergibt immer denselben Wert) — der Schlüssel muss mit einem Prozess-Salz
    (HMAC) gebildet werden, das die einfache Formel nicht mehr reproduziert."""
    unsalziert = "stat:" + hashlib.sha256(b"1:ost").hexdigest()[:32]
    assert statistik.drossel_schluessel(1, "ost") != unsalziert


def test_statistik_zaehlt_je_tag_ohne_personenbezug(mit_paketen: TestClient) -> None:  # noqa: F811
    n, c = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    assert c.post(S, headers=csrf(), json={"oeffnen": 1, "standpunkt": 3}).status_code == 204
    assert c.post(S, headers=csrf(), json={"messung": 2, "poi": 1, "oeffnen": 1}).status_code == 204
    assert c.post(S, headers=csrf(), json={}).status_code == 204  # nichts zu zählen
    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    row = conn.execute("SELECT * FROM statistik_tag").fetchone()
    assert (row["project_id"], row["oeffnen"], row["standpunkt"], row["messung"], row["poi"]) == (
        "ost", 2, 3, 2, 1,
    )
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(statistik_tag)")}
    assert spalten == {"project_id", "tag", "oeffnen", "standpunkt", "messung", "poi"}
    schluessel = [r[0] for r in conn.execute("SELECT key FROM throttle WHERE key LIKE 'stat:%'")]
    assert schluessel == [statistik.drossel_schluessel(n.id, "ost")]
    assert f"{n.id}:ost" not in schluessel[0]
    assert conn.execute("SELECT COUNT(*) FROM ereignisse WHERE art LIKE 'statistik%'"
                        ).fetchone()[0] == 0
    conn.close()


def test_statistik_rechte_und_grenzen(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    assert c.post("/api/v1/statistik/kirche", headers=csrf(),
                  json={"oeffnen": 1}).status_code == 404
    assert c.post(S, json={"oeffnen": 1}).status_code == 403  # ohne CSRF
    assert c.post(S, headers=csrf(), json={"oeffnen": 1001}).status_code == 422
    assert c.post(S, headers=csrf(), json={"oeffnen": -1}).status_code == 422
    assert mit_paketen.post(S, headers=csrf(), json={"oeffnen": 1}).status_code == 401


def test_drosselung_je_nutzer_und_projekt(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    for _ in range(statistik.MELDUNGEN_JE_FENSTER):
        assert c.post(S, headers=csrf(), json={"oeffnen": 1}).status_code == 204
    r = c.post(S, headers=csrf(), json={"oeffnen": 1})
    assert r.status_code == 429 and r.json()["dienst"] == "scanview"


def test_erlaubt_fenster(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    for _ in range(statistik.MELDUNGEN_JE_FENSTER):
        assert statistik.erlaubt(conn, 1, "p", now=1000)
    assert not statistik.erlaubt(conn, 1, "p", now=1059)
    assert statistik.erlaubt(conn, 2, "p", now=1059)  # anderer Nutzer: eigenes Fenster
    assert statistik.erlaubt(conn, 1, "p", now=1060)  # neues Fenster


def test_erlaubt_loescht_abgelaufene_stat_zeilen(tmp_path: Path) -> None:
    """Final-Fix C/D: abgelaufene `stat:`-Zeilen verschwinden schon bei der nächsten Meldung,
    nicht erst beim Dienststart; andere Drosselzeilen bleiben."""
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    conn.execute("INSERT INTO throttle (key, failures, locked_until, geaendert_am)"
                 " VALUES ('pw:x', 1, 0, 0)")
    assert statistik.erlaubt(conn, 1, "p", now=1000)
    assert statistik.erlaubt(conn, 2, "p", now=1030)

    def stat_zeilen() -> int:
        return int(conn.execute(
            "SELECT COUNT(*) FROM throttle WHERE key LIKE 'stat:%'").fetchone()[0])

    assert stat_zeilen() == 2
    assert statistik.erlaubt(conn, 3, "p", now=1060)  # Fenster von Nutzer 1 vorbei
    assert stat_zeilen() == 2  # Nutzer 1 weg, Nutzer 3 neu
    assert conn.execute("SELECT COUNT(*) FROM throttle WHERE key=?",
                        (statistik.drossel_schluessel(1, "p"),)).fetchone()[0] == 0
    assert statistik.erlaubt(conn, 3, "p", now=1200)
    assert stat_zeilen() == 1
    assert conn.execute("SELECT COUNT(*) FROM throttle WHERE key='pw:x'").fetchone()[0] == 1


def test_auswertung_30_tage(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "s.db")
    db.migrate(conn)
    heute = 100 * 86400 + 3600
    statistik.zaehlen(conn, "p", {"oeffnen": 2}, now=heute)
    statistik.zaehlen(conn, "p", {"oeffnen": 1, "poi": 4}, now=heute - 86400)
    statistik.zaehlen(conn, "p", {"oeffnen": 5}, now=heute - 40 * 86400)  # außerhalb
    [p] = statistik.auswertung(conn, [("p", "Projekt P")], tage=30, now=heute)
    assert len(p["verlauf"]) == 30 and p["verlauf"][-1]["tag"] == statistik.tag(heute)
    assert p["verlauf"][-2] == {"tag": statistik.tag(heute - 86400), "oeffnen": 1,
                                "standpunkt": 0, "messung": 0, "poi": 4}
    assert p["summen"] == {"oeffnen": 3, "standpunkt": 0, "messung": 0, "poi": 4}
    assert p["zuletztGeoeffnet"] == statistik.tag(heute) and p["titel"] == "Projekt P"
    [q] = statistik.auswertung(conn, [("q", "Leer")], now=heute)
    assert q["summen"]["oeffnen"] == 0 and q["zuletztGeoeffnet"] is None


def test_admin_statistik_route(mit_paketen: TestClient) -> None:  # noqa: F811
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    _, c = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    c.post(S, headers=csrf(), json={"oeffnen": 1})
    d = adm.get("/api/v1/admin/statistik").json()
    ost = next(p for p in d["projekte"] if p["id"] == "ost")
    assert ost["summen"]["oeffnen"] == 1 and len(ost["verlauf"]) == 30
    assert {n["name"] for n in d["nutzer"]} >= {"a", "t"}
    assert adm.get("/api/v1/admin/statistik", params={"tage": 7}).json()["tage"] == 7
    assert adm.get("/api/v1/admin/statistik", params={"tage": 0}).status_code == 422
    assert c.get("/api/v1/admin/statistik").status_code == 404
