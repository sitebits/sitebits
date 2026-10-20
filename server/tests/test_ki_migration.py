"""Migration 0013 (G7 Vorschlag-Zustand): Daten bleiben, Faden bleibt append-only."""

import sqlite3
from pathlib import Path

import pytest
from test_db import _bis

from scanview_server import db

ZEILEN = [
    # id, schluessel, art, text, wert, intern, zurueckgezogen_am
    (1, "0000000000000001", "kommentar", "öffentlich", "null", 0, None),
    (2, "0000000000000002", "kommentar", None, "null", 1, 30),
    (3, "0000000000000003", "status", None, '"erledigt"', 0, None),
    (5, "0000000000000005", "markierung", None, '{"vorher":{},"nachher":{}}', 0, None),
]


def _alt(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "alt.db")
    _bis(conn, 11)
    conn.execute("INSERT INTO projekte (project_id, vorlage, angelegt_am, befund_faeden)"
                 " VALUES ('ost', 'bauprojekt', 0, 1)")
    conn.execute("INSERT INTO pois (project_id, id, daten, erstellt_am, geaendert_am, version)"
                 " VALUES ('ost', 'P-1', '{}', 10, 20, 3)")
    conn.execute("INSERT INTO messungen (project_id, id, daten, erstellt_am, geaendert_am)"
                 " VALUES ('ost', 'M-1', '{}', 10, 20)")
    conn.execute("INSERT INTO users (id, email, name, created_at) VALUES (7, 'a@b.de', 'A', 0)")
    conn.execute("INSERT INTO api_tokens (user_id, name, token_hash, umfang, erstellt_am,"
                 " laeuft_ab) VALUES (7, 't', 'h', 'schreiben', 0, 99)")
    for i, s, art, text, wert, intern, zur in ZEILEN:
        conn.execute(
            "INSERT INTO befund_eintraege (id, schluessel, project_id, poi_id, art, text, wert,"
            " intern, erstellt_am, kanal, zurueckgezogen_am) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (i, s, "ost", "P-1", art, text, wert, intern, 10 + i, "web", zur))
    return conn


def test_migration_13_erhaelt_daten_und_setzt_defaults(tmp_path: Path) -> None:
    conn = _alt(tmp_path)
    assert db.migrate(conn) == 13
    assert db.migrate(conn) == 13
    for t in ("pois", "messungen"):
        assert tuple(conn.execute(
            f"SELECT herkunft, pruefstand, loeschen_vorgeschlagen FROM {t}").fetchone()
        ) == ("mensch", "gueltig", None)
    assert conn.execute("SELECT agent FROM api_tokens").fetchone()[0] == 0
    rows = conn.execute("SELECT id, schluessel, art, text, wert, intern, zurueckgezogen_am,"
                        " herkunft, bezug FROM befund_eintraege ORDER BY id").fetchall()
    assert [tuple(r)[:7] for r in rows] == ZEILEN
    assert {(r["herkunft"], r["bezug"]) for r in rows} == {("mensch", None)}
    # AUTOINCREMENT: neue Einträge liegen hinter der höchsten alten id (5), nie in der Lücke 4.
    conn.execute("INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, wert,"
                 " erstellt_am, kanal) VALUES ('00000000000000ff', 'ost', 'P-1', 'vorschlag',"
                 " '{}', 99, 'api')")
    assert conn.execute("SELECT MAX(id) FROM befund_eintraege").fetchone()[0] == 6
    assert {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE tbl_name='befund_eintraege' AND type IN"
        " ('index', 'trigger') AND sql IS NOT NULL")} == {
        "befund_eintraege_poi", "befund_eintraege_bezug", "befund_eintraege_kein_update",
        "befund_eintraege_kein_delete", "befund_eintraege_kein_ersetzen"}
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("sql", [
    "UPDATE pois SET herkunft='roboter'",
    "UPDATE pois SET pruefstand='offen'",
    "UPDATE messungen SET pruefstand='gueltig ' ",
    "UPDATE messungen SET herkunft='KI'",
    "UPDATE api_tokens SET agent=2",
    "UPDATE befund_eintraege SET text='neu' WHERE id=1",
    "UPDATE befund_eintraege SET herkunft='ki' WHERE id=1",
    "UPDATE befund_eintraege SET bezug='0000000000000003' WHERE id=1",
    "DELETE FROM befund_eintraege WHERE id=1",
    "INSERT OR REPLACE INTO befund_eintraege SELECT * FROM befund_eintraege WHERE id=1",
    "INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, wert, erstellt_am, kanal,"
    " herkunft) VALUES ('00000000000000aa', 'ost', 'P-1', 'kommentar', 'null', 1, 'web', 'x')",
    "INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, wert, intern,"
    " erstellt_am, kanal) VALUES ('00000000000000ab', 'ost', 'P-1', 'vorschlag', '{}', 1, 1,"
    " 'web')",
    "INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, wert, erstellt_am, kanal,"
    " bezug) VALUES ('00000000000000ac', 'ost', 'P-1', 'kommentar', 'null', 1, 'web', 'kurz')",
])
def test_migration_13_sperrt(tmp_path: Path, sql: str) -> None:
    conn = _alt(tmp_path)
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql)


def test_migration_13_zurueckziehen_bleibt_moeglich(tmp_path: Path) -> None:
    conn = _alt(tmp_path)
    db.migrate(conn)
    conn.execute("UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=50 WHERE id=1")
    assert conn.execute("SELECT zurueckgezogen_am FROM befund_eintraege WHERE id=1"
                        ).fetchone()[0] == 50
