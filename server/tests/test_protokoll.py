import json
import sqlite3
from pathlib import Path

import pytest

from scanview_server import db, konten, protokoll
from scanview_server.tokens import hash_token


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = db.connect(tmp_path / "p.db")
    db.migrate(c)
    return c


def test_ereignis_schreiben_und_lesen_neueste_zuerst(conn: sqlite3.Connection) -> None:
    a = protokoll.ereignis(conn, kanal="web", art="poi.angelegt", project_id="p1",
                           objekt_id="P-1", nachher={"title": "Tür", "note": "Ä ß"}, now=10)
    b = protokoll.ereignis(conn, kanal="web", art="poi.geloescht", project_id="p1",
                           objekt_id="P-1", vorher={"title": "Tür"}, now=11)
    liste = protokoll.lesen(conn, project_id="p1")
    assert [e.id for e in liste] == [b, a]
    assert liste[1].nachher == {"title": "Tür", "note": "Ä ß"}
    assert liste[1].vorher is None and liste[0].nachher is None
    roh = conn.execute("SELECT nachher FROM ereignisse WHERE id=?", (a,)).fetchone()[0]
    assert "Ä ß" in roh  # ensure_ascii=False: lesbar im Dump


def test_token_name_wird_gespeichert_und_ausgegeben(conn: sqlite3.Connection) -> None:
    """Schreibvorgänge per API-Token tragen den Token-Namen (nie Wert/Hash) mit."""
    eid = protokoll.ereignis(conn, kanal="api", art="poi.angelegt", project_id="p1",
                             token_name="Claude Desktop", now=1)
    ohne = protokoll.ereignis(conn, kanal="web", art="poi.angelegt", project_id="p1", now=2)
    [ohne_token, mit_token] = protokoll.lesen(conn, project_id="p1")  # neueste zuerst
    assert (mit_token.id, mit_token.token) == (eid, "Claude Desktop")
    assert mit_token.als_json()["token"] == "Claude Desktop"
    assert (ohne_token.id, ohne_token.token) == (ohne, None)
    assert ohne_token.als_json()["token"] is None


def test_lesen_seitenweise_und_filter(conn: sqlite3.Connection) -> None:
    ids = [protokoll.ereignis(conn, kanal="web", art="poi.angelegt", project_id="p1",
                              objekt_id=str(i)) for i in range(5)]
    protokoll.ereignis(conn, kanal="cli", art="recht.entzogen", project_id="p1", objekt_id="7")
    protokoll.ereignis(conn, kanal="web", art="poi.angelegt", project_id="p2", objekt_id="x")
    seite1 = protokoll.lesen(conn, project_id="p1", limit=2, arten=("poi",))
    assert [e.id for e in seite1] == [ids[4], ids[3]]
    seite2 = protokoll.lesen(conn, project_id="p1", limit=2, vor=seite1[-1].id, arten=("poi",))
    assert [e.id for e in seite2] == [ids[2], ids[1]]
    assert len(protokoll.lesen(conn, project_id="p1")) == 6
    assert len(protokoll.lesen(conn)) == 7
    assert len(protokoll.lesen(conn, limit=10_000)) == 7  # auf 200 begrenzt, hier egal
    assert protokoll.lesen(conn, limit=0)  # mindestens 1


def test_ereignisse_unveraenderlich(conn: sqlite3.Connection) -> None:
    eid = protokoll.ereignis(conn, kanal="cli", art="konto.gesperrt", objekt_id="1")
    with pytest.raises(sqlite3.DatabaseError, match="unveränderlich"):
        conn.execute("UPDATE ereignisse SET art='x.y' WHERE id=?", (eid,))
    with pytest.raises(sqlite3.DatabaseError, match="unveränderlich"):
        conn.execute("DELETE FROM ereignisse WHERE id=?", (eid,))
    with pytest.raises(sqlite3.DatabaseError, match="unveränderlich"):
        conn.execute("DELETE FROM ereignisse")
    assert conn.execute("SELECT art FROM ereignisse WHERE id=?", (eid,)).fetchone()[0] == (
        "konto.gesperrt"
    )


def test_ereignisse_insert_or_replace_scheitert_auch(conn: sqlite3.Connection) -> None:
    """`INSERT OR REPLACE` löscht intern erst die Konfliktzeile — ohne `recursive_triggers=ON`
    (db.connect) würde das den DELETE-Trigger umgehen und die Zeile unbemerkt überschreiben."""
    eid = protokoll.ereignis(conn, kanal="cli", art="konto.gesperrt", objekt_id="1")
    with pytest.raises(sqlite3.DatabaseError, match="unveränderlich"):
        conn.execute(
            "INSERT OR REPLACE INTO ereignisse (id, zeit, kanal, art) VALUES (?, 0, 'cli', 'x.y')",
            (eid,),
        )
    assert conn.execute("SELECT art FROM ereignisse WHERE id=?", (eid,)).fetchone()[0] == (
        "konto.gesperrt"
    )


def test_insert_or_replace_scheitert_ohne_pragma(tmp_path: Path) -> None:
    """Final-Fix C/D: auch ohne `recursive_triggers` (sqlite3-Shell, bloßes connect) lässt sich
    kein Ereignis per INSERT OR REPLACE überschreiben; normales Anhängen geht weiter."""
    c = db.connect(tmp_path / "roh.db")
    db.migrate(c)
    eid = protokoll.ereignis(c, kanal="cli", art="konto.gesperrt", objekt_id="1")
    c.close()
    roh = sqlite3.connect(tmp_path / "roh.db", isolation_level=None)
    try:
        assert roh.execute("PRAGMA recursive_triggers").fetchone()[0] == 0
        for sql in (
            "INSERT OR REPLACE INTO ereignisse (id, zeit, kanal, art) VALUES (?, 0, 'cli', 'x.y')",
            "REPLACE INTO ereignisse (id, zeit, kanal, art) VALUES (?, 0, 'cli', 'x.y')",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="Protokoll ist unveränderlich"):
                roh.execute(sql, (eid,))
        zeile = roh.execute("SELECT art, objekt_id FROM ereignisse WHERE id=?", (eid,)).fetchone()
        assert zeile == ("konto.gesperrt", "1")
        roh.execute("INSERT INTO ereignisse (zeit, kanal, art) VALUES (1, 'cli', 'konto.frei')")
        assert roh.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == 2
    finally:
        roh.close()


def test_ungueltiger_kanal_oder_art(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError):
        protokoll.ereignis(conn, kanal="mail", art="poi.angelegt")
    with pytest.raises(ValueError):
        protokoll.ereignis(conn, kanal="web", art="Poi angelegt")
    assert conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == 0


def test_jsonl_aelteste_zuerst(conn: sqlite3.Connection) -> None:
    protokoll.ereignis(conn, kanal="web", art="meta.geaendert", project_id="p1", now=1)
    protokoll.ereignis(conn, kanal="web", art="poi.angelegt", project_id="p1", now=2)
    zeilen = protokoll.jsonl(conn, project_id="p1").splitlines()
    assert [json.loads(z)["art"] for z in zeilen] == ["meta.geaendert", "poi.angelegt"]
    assert protokoll.jsonl(conn, project_id="p1").endswith("\n")
    assert protokoll.jsonl(conn, project_id="leer") == ""


def test_einloesen_protokolliert_konto_angelegt(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="Anna", grants={"p1": "betrachter"})
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    [e] = protokoll.lesen(conn)
    assert (e.art, e.kanal, e.nutzer_id, e.objekt_id) == ("konto.angelegt", "web", n.id, str(n.id))
    assert e.nachher == {"admin": False, "einladung": hash_token(t)[:16],
                         "rechte": {"p1": "betrachter"}}
    assert e.nutzer_name == "Anna"
    text = json.dumps(e.als_json(), ensure_ascii=False)
    assert "a@b.de" not in text and t not in text and hash_token(t) not in text


def test_zuruecksetzen_protokolliert(conn: sqlite3.Connection) -> None:
    t = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={})
    n = konten.einladung_einloesen(conn, t, lambda _c, _u: None)
    t2 = konten.einladung_anlegen(conn, email="a@b.de", name="A", grants={},
                                  purpose="zuruecksetzen")
    konten.einladung_einloesen(conn, t2, lambda _c, _u: None)
    assert protokoll.lesen(conn)[0].art == "konto.zurueckgesetzt"
    assert protokoll.lesen(conn)[0].objekt_id == str(n.id)
