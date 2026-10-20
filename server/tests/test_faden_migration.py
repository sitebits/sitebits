"""Migration und SQL-Schutz des Befund-Fadens (NT-FADEN-SQL)."""

import sqlite3
from pathlib import Path

import pytest
from test_db import _bis

from scanview_server import db


def test_migration_11_erhaelt_daten_und_setzt_defaults(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "alt.db")
    _bis(conn, 10)
    conn.execute("INSERT INTO projekte VALUES ('ost', 'bauprojekt', 1, 1, 0)")
    conn.execute("INSERT INTO projekte VALUES ('west', 'bestandsaufnahme', 1, 1, 0)")
    conn.execute(
        "INSERT INTO pois (project_id, id, daten, erstellt_am, geaendert_am, version)"
        " VALUES ('ost', 'punkt', '{}', 10, 20, 7)"
    )
    # Bis zum neuesten Stand und ein zweites Mal (idempotent); Migration 11 ist darin enthalten.
    neueste = db.migrate(conn)
    assert neueste >= 11
    assert db.migrate(conn) == neueste
    assert [tuple(r) for r in conn.execute(
        "SELECT project_id, befund_faeden FROM projekte ORDER BY project_id"
    )] == [("ost", 1), ("west", 0)]
    assert tuple(conn.execute(
        "SELECT daten, erstellt_am, geaendert_am, version, status,"
        " zustaendig_gewerk_id, zustaendig_nutzer_id, frist FROM pois"
    ).fetchone()) == ("{}", 10, 20, 7, "offen", None, None, None)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE pois SET status='unbekannt'")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.fixture
def eintrag(tmp_path: Path) -> sqlite3.Connection:
    conn = db.connect(tmp_path / "faden.db")
    db.migrate(conn)
    conn.execute(
        "INSERT INTO pois (project_id, id, daten, erstellt_am, geaendert_am)"
        " VALUES ('ost', 'punkt', '{}', 0, 0)"
    )
    conn.execute(
        "INSERT INTO befund_eintraege"
        " (id, schluessel, project_id, poi_id, art, text, wert, intern, erstellt_am, kanal)"
        " VALUES (1, '0123456789abcdef', 'ost', 'punkt', 'kommentar', 'Original', 'null', 0,"
        " 10, 'api')"
    )
    return conn


@pytest.mark.parametrize('sql', [
    "UPDATE befund_eintraege SET text='ersetzt'",
    "DELETE FROM befund_eintraege",
    "UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=20, intern=1",
    "UPDATE befund_eintraege SET zurueckgezogen_am=20",
    "UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=20, schluessel='ffffffffffffffff'",
    "INSERT OR REPLACE INTO befund_eintraege SELECT * FROM befund_eintraege",
    "INSERT OR REPLACE INTO befund_eintraege (schluessel, project_id, poi_id, art, text, wert,"
    " intern, erstellt_am, kanal) VALUES ('0123456789abcdef', 'ost', 'punkt', 'kommentar', 'neu',"
    " 'null', 0, 11, 'api')",
])
def test_unzulaessige_sql_mutation_scheitert(eintrag: sqlite3.Connection, sql: str) -> None:
    with pytest.raises(sqlite3.IntegrityError, match='unveränderlich'):
        eintrag.execute(sql)
    assert eintrag.execute("SELECT text FROM befund_eintraege").fetchone()[0] == 'Original'


def test_zurueckziehen_einmalig(eintrag: sqlite3.Connection) -> None:
    eintrag.execute("UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=20")
    assert tuple(eintrag.execute(
        "SELECT text, zurueckgezogen_am FROM befund_eintraege"
    ).fetchone()) == (None, 20)
    with pytest.raises(sqlite3.IntegrityError, match='unveränderlich'):
        eintrag.execute("UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=30")
