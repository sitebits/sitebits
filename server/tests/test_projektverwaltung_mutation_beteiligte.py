"""Mutationstests für Beteiligte und deren JSON-Darstellung in projektverwaltung.py."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from scanview_server import db, projektverwaltung


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_pfad = tmp_path / "test_beteiligte.db"
    verbindung = db.connect(db_pfad)
    db.migrate(verbindung)
    yield verbindung
    verbindung.close()


def test_beteiligter_dataclass() -> None:
    person = projektverwaltung.Beteiligter(
        nutzer_id=10,
        name="Clara Tester",
        email="clara@example.com",
        rolle="bearbeiter",
        gewerk_id=4,
        letzte_anmeldung=1700001000,
        gesperrt=False,
        admin=True,
    )
    assert person.nutzer_id == 10
    assert person.name == "Clara Tester"
    assert person.email == "clara@example.com"
    assert person.rolle == "bearbeiter"
    assert person.gewerk_id == 4
    assert person.letzte_anmeldung == 1700001000
    assert person.gesperrt is False
    assert person.admin is True


def test_beteiligte_leer_wenn_keine_rollen(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at, last_login_at) "
        "VALUES (1, 'Super Admin', 'admin@example.com', 1, 0, 100, 200)"
    )
    ergebnis = projektverwaltung.beteiligte(conn, "projekt_leer")
    assert ergebnis == []


def test_beteiligte_filterung_und_sortierung(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at, last_login_at) VALUES "
        "(10, 'berta', 'berta@example.com', 0, 0, 100, 1500), "
        "(20, 'Adam', 'adam2@example.com', 1, 0, 101, 1600), "
        "(30, 'adam', 'adam1@example.com', 0, 1, 102, NULL), "
        "(40, 'Charly', 'charly@example.com', 0, 0, 103, 1700), "
        "(50, 'Aussenseiter', 'aussen@example.com', 1, 0, 104, 1800)"
    )

    gid = projektverwaltung.gewerk_anlegen(
        conn, "projekt_target", name="Elektro", kurz="ELEK", bauherrenseite=True, von=10, now=100
    ).id

    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) VALUES "
        "(10, 'projekt_target', 'rueckmelder', ?), "
        "(20, 'projekt_target', 'projektleitung', NULL), "
        "(30, 'projekt_target', 'betrachter', NULL), "
        "(40, 'projekt_target', 'bearbeiter', ?), "
        "(50, 'anderes_projekt', 'bearbeiter', NULL)",
        (gid, gid),
    )

    liste = projektverwaltung.beteiligte(conn, "projekt_target")
    assert len(liste) == 4

    assert [eintrag.name for eintrag in liste] == ["Adam", "adam", "berta", "Charly"]
    assert [eintrag.nutzer_id for eintrag in liste] == [20, 30, 10, 40]

    erster = liste[0]
    assert erster.nutzer_id == 20
    assert erster.name == "Adam"
    assert erster.email == "adam2@example.com"
    assert erster.rolle == "projektleitung"
    assert erster.gewerk_id is None
    assert erster.letzte_anmeldung == 1600
    assert erster.gesperrt is False
    assert erster.admin is True

    zweiter = liste[1]
    assert zweiter.nutzer_id == 30
    assert zweiter.name == "adam"
    assert zweiter.email == "adam1@example.com"
    assert zweiter.rolle == "betrachter"
    assert zweiter.gewerk_id is None
    assert zweiter.letzte_anmeldung is None
    assert zweiter.gesperrt is True
    assert zweiter.admin is False

    dritter = liste[2]
    assert dritter.nutzer_id == 10
    assert dritter.rolle == "rueckmelder"
    assert dritter.gewerk_id == gid
    assert dritter.letzte_anmeldung == 1500
    assert dritter.gesperrt is False
    assert dritter.admin is False

    vierter = liste[3]
    assert vierter.nutzer_id == 40
    assert vierter.rolle == "bearbeiter"
    assert vierter.gewerk_id == gid
    assert vierter.letzte_anmeldung == 1700


def test_beteiligter_json_kompakt_und_voll() -> None:
    gewerk_objekt = projektverwaltung.Gewerk(id=5, name="Sanitär", kurz="SAN", bauherrenseite=True)
    gewerke_tabelle = {5: gewerk_objekt}

    person_mit_gewerk = projektverwaltung.Beteiligter(
        nutzer_id=101,
        name="Tina Techniker",
        email="tina@example.com",
        rolle="bearbeiter",
        gewerk_id=5,
        letzte_anmeldung=1700002000,
        gesperrt=False,
        admin=True,
    )

    json_kompakt = projektverwaltung.beteiligter_json(
        person_mit_gewerk, gewerke_tabelle, voll=False
    )
    assert json_kompakt == {
        "nutzerId": 101,
        "nutzer": "Tina Techniker",
        "rolle": "bearbeiter",
        "gewerk": {"id": 5, "name": "Sanitär", "kurz": "SAN"},
    }
    assert len(json_kompakt) == 4
    assert "email" not in json_kompakt
    assert "letzteAnmeldung" not in json_kompakt
    assert "gesperrt" not in json_kompakt
    assert "admin" not in json_kompakt

    json_voll = projektverwaltung.beteiligter_json(person_mit_gewerk, gewerke_tabelle, voll=True)
    assert json_voll == {
        "nutzerId": 101,
        "nutzer": "Tina Techniker",
        "rolle": "bearbeiter",
        "gewerk": {"id": 5, "name": "Sanitär", "kurz": "SAN"},
        "email": "tina@example.com",
        "letzteAnmeldung": 1700002000,
        "gesperrt": False,
        "admin": True,
    }
    assert len(json_voll) == 8


def test_beteiligter_json_ohne_oder_unbekanntes_gewerk() -> None:
    gewerke_tabelle = {
        1: projektverwaltung.Gewerk(id=1, name="Bau", kurz="BAU", bauherrenseite=False)
    }

    person_ohne_gewerk = projektverwaltung.Beteiligter(
        nutzer_id=102,
        name="Olaf Ohne",
        email="olaf@example.com",
        rolle="betrachter",
        gewerk_id=None,
        letzte_anmeldung=None,
        gesperrt=True,
        admin=False,
    )
    ausgabe_ohne = projektverwaltung.beteiligter_json(
        person_ohne_gewerk, gewerke_tabelle, voll=False
    )
    assert ausgabe_ohne["gewerk"] is None

    person_unbekanntes_gewerk = projektverwaltung.Beteiligter(
        nutzer_id=103,
        name="Uwe Unbekannt",
        email="uwe@example.com",
        rolle="rueckmelder",
        gewerk_id=999,
        letzte_anmeldung=None,
        gesperrt=False,
        admin=False,
    )
    ausgabe_unbekannt = projektverwaltung.beteiligter_json(
        person_unbekanntes_gewerk, gewerke_tabelle, voll=False
    )
    assert ausgabe_unbekannt["gewerk"] is None


def test_beteiligte_sortierung_gleicher_name_und_status_flags(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at, last_login_at) VALUES "
        "(55, 'Doppelgänger', 'd2@example.com', 0, 1, 100, NULL), "
        "(22, 'Doppelgänger', 'd1@example.com', 1, 0, 100, 12345)"
    )
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) VALUES "
        "(55, 'projekt_doppel', 'betrachter', NULL), "
        "(22, 'projekt_doppel', 'projektleitung', NULL)"
    )

    liste = projektverwaltung.beteiligte(conn, "projekt_doppel")
    assert len(liste) == 2
    assert [e.nutzer_id for e in liste] == [22, 55]

    erster = liste[0]
    assert erster.nutzer_id == 22
    assert erster.admin is True
    assert erster.gesperrt is False
    assert erster.letzte_anmeldung == 12345

    zweiter = liste[1]
    assert zweiter.nutzer_id == 55
    assert zweiter.admin is False
    assert zweiter.gesperrt is True
    assert zweiter.letzte_anmeldung is None


def test_beteiligter_json_voll_mit_none_anmeldung_und_flags() -> None:
    person = projektverwaltung.Beteiligter(
        nutzer_id=105,
        name="Lara Leer",
        email="lara@example.com",
        rolle="betrachter",
        gewerk_id=None,
        letzte_anmeldung=None,
        gesperrt=True,
        admin=False,
    )
    daten = projektverwaltung.beteiligter_json(person, {}, voll=True)
    assert daten == {
        "nutzerId": 105,
        "nutzer": "Lara Leer",
        "rolle": "betrachter",
        "gewerk": None,
        "email": "lara@example.com",
        "letzteAnmeldung": None,
        "gesperrt": True,
        "admin": False,
    }
