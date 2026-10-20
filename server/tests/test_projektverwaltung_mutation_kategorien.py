"""Mutationstests für Kategorien und Vorlageninhalte in projektverwaltung.py."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scanview_server import db, projektverwaltung
from scanview_server.web import Ungueltig


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_pfad = tmp_path / "test_kategorien.db"
    verbindung = db.connect(db_pfad)
    db.migrate(verbindung)
    verbindung.execute(
        "INSERT INTO users (id, name, email, is_admin, created_at) "
        "VALUES (1, 'Admin', 'admin@example.com', 1, 100)"
    )
    yield verbindung
    verbindung.close()


def test_kategorie_dataclass_und_json() -> None:
    kat = projektverwaltung.Kategorie(name="Prüfung", aktiv=True)
    assert kat.name == "Prüfung"
    assert kat.aktiv is True
    assert kat.als_json() == {"name": "Prüfung", "aktiv": True}

    inaktiv = projektverwaltung.Kategorie(name="Archiv", aktiv=False)
    assert inaktiv.als_json() == {"name": "Archiv", "aktiv": False}


def test_kategorien_nicht_eingerichtetes_projekt_gibt_standard(conn: sqlite3.Connection) -> None:
    liste = projektverwaltung.kategorien(conn, "noch_nicht_eingerichtet")
    erwartet = [
        projektverwaltung.Kategorie(name=n, aktiv=True)
        for n in projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE]
    ]
    assert liste == erwartet
    assert [k.name for k in liste] == ["Raum", "Anlage", "Zugang", "Mangel", "Sonstiges"]
    assert all(k.aktiv for k in liste)

    anzahl_projekte = conn.execute("SELECT COUNT(*) FROM projekte").fetchone()[0]
    assert anzahl_projekte == 0


def test_kategorien_setzen_validierung_laenge_und_zeichen(conn: sqlite3.Connection) -> None:
    standard_namen = list(projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE])
    basis_liste = [(name, True) for name in standard_namen]

    zu_viele = [(f"Kat {nummer}", True) for nummer in range(projektverwaltung.KATEGORIEN_MAX + 1)]
    with pytest.raises(Ungueltig) as exc_anzahl:
        projektverwaltung.kategorien_setzen(conn, "projekt_val", zu_viele, von=1, now=100)
    assert str(exc_anzahl.value) == projektverwaltung.KATEGORIEN_ZU_VIELE

    with pytest.raises(Ungueltig) as exc_leer:
        projektverwaltung.kategorien_setzen(
            conn, "projekt_val", [*basis_liste, ("  ", True)], von=1, now=101
        )
    assert str(exc_leer.value) == "Kategorie fehlt."

    with pytest.raises(Ungueltig) as exc_lang:
        projektverwaltung.kategorien_setzen(
            conn, "projekt_val", [*basis_liste, ("X" * 61, True)], von=1, now=102
        )
    assert str(exc_lang.value) == "Kategorie länger als 60 Zeichen."

    with pytest.raises(Ungueltig) as exc_steuer:
        projektverwaltung.kategorien_setzen(
            conn, "projekt_val", [*basis_liste, ("A\tB", True)], von=1, now=103
        )
    assert str(exc_steuer.value) == "Kategorie enthält Steuerzeichen."

    for reserviert in ("Rückmeldung", "rückmeldung", "RÜCKMELDUNG"):
        with pytest.raises(Ungueltig) as exc_res:
            projektverwaltung.kategorien_setzen(
                conn, "projekt_val", [*basis_liste, (reserviert, True)], von=1, now=104
            )
        assert str(exc_res.value) == projektverwaltung.KATEGORIE_RESERVIERT

    with pytest.raises(Ungueltig) as exc_doppelt:
        projektverwaltung.kategorien_setzen(
            conn, "projekt_val", [*basis_liste, ("raum", True)], von=1, now=105
        )
    assert str(exc_doppelt.value) == "Die Kategorie „raum“ steht doppelt in der Liste."


def test_kategorien_setzen_fehlt_in_liste(conn: sqlite3.Connection) -> None:
    standard_namen = list(projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE])
    ohne_raum = [(name, True) for name in standard_namen if name != "Raum"]

    with pytest.raises(Ungueltig) as exc_fehlt:
        projektverwaltung.kategorien_setzen(conn, "projekt_fehlt", ohne_raum, von=1, now=100)
    assert (
        str(exc_fehlt.value)
        == "Kategorien lassen sich nur ausschalten, nicht entfernen: „Raum“ fehlt in der Liste."
    )


def test_kategorien_setzen_hinzufuegen_umordnen_und_ausschalten(
    conn: sqlite3.Connection,
) -> None:
    zeitstempel_start = 1700005000
    eingabe = [
        ("Mangel", True),
        ("Raum", False),
        ("Anlage", True),
        ("Zugang", True),
        ("Sonstiges", True),
        ("Sonderbefund", True),
    ]

    ergebnis = projektverwaltung.kategorien_setzen(
        conn, "projekt_kats", eingabe, von=1, now=zeitstempel_start
    )
    assert len(ergebnis) == 6
    assert [k.name for k in ergebnis] == [
        "Mangel",
        "Raum",
        "Anlage",
        "Zugang",
        "Sonstiges",
        "Sonderbefund",
    ]
    assert [k.aktiv for k in ergebnis] == [True, False, True, True, True, True]

    ereignisse_angelegt = conn.execute(
        "SELECT art, nachher, zeit FROM ereignisse WHERE art='kategorie.angelegt'"
    ).fetchall()
    assert len(ereignisse_angelegt) == 1
    nachher_angelegt = json.loads(ereignisse_angelegt[0]["nachher"])
    assert nachher_angelegt == {"name": "Sonderbefund", "aktiv": True, "reihenfolge": 5}
    assert ereignisse_angelegt[0]["zeit"] == zeitstempel_start

    ereignisse_geaendert = conn.execute(
        "SELECT art, objekt_id, vorher, nachher, zeit FROM ereignisse "
        "WHERE art='kategorie.geaendert' ORDER BY id"
    ).fetchall()
    assert len(ereignisse_geaendert) > 0

    raum_wechsel = next(
        e for e in ereignisse_geaendert if json.loads(e["vorher"])["name"] == "Raum"
    )
    assert json.loads(raum_wechsel["vorher"]) == {"name": "Raum", "aktiv": True, "reihenfolge": 0}
    assert json.loads(raum_wechsel["nachher"]) == {"name": "Raum", "aktiv": False, "reihenfolge": 1}

    mangel_wechsel = next(
        e for e in ereignisse_geaendert if json.loads(e["vorher"])["name"] == "Mangel"
    )
    assert json.loads(mangel_wechsel["vorher"]) == {
        "name": "Mangel",
        "aktiv": True,
        "reihenfolge": 3,
    }
    assert json.loads(mangel_wechsel["nachher"]) == {
        "name": "Mangel",
        "aktiv": True,
        "reihenfolge": 0,
    }


def test_kategorien_setzen_unveraendert_schreibt_kein_ereignis(conn: sqlite3.Connection) -> None:
    projektverwaltung.einrichten(conn, "projekt_ident", "bauprojekt", von=1, now=100)
    start_kategorien = [
        (k.name, k.aktiv) for k in projektverwaltung.kategorien(conn, "projekt_ident")
    ]

    ereignisse_vorher = conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0]

    ergebnis_ident = projektverwaltung.kategorien_setzen(
        conn, "projekt_ident", start_kategorien, von=1, now=200
    )
    assert [(k.name, k.aktiv) for k in ergebnis_ident] == start_kategorien

    ereignisse_nachher = conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0]
    assert ereignisse_nachher == ereignisse_vorher


def test_kategorien_grenzwert_genau_dreissig(conn: sqlite3.Connection) -> None:
    standard_namen = list(projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE])
    liste_30 = [(name, True) for name in standard_namen]
    for nummer in range(len(standard_namen), projektverwaltung.KATEGORIEN_MAX):
        liste_30.append((f"Kategorie {nummer:02d}", True))

    assert len(liste_30) == 30
    ergebnis = projektverwaltung.kategorien_setzen(conn, "projekt_max", liste_30, von=1, now=100)
    assert len(ergebnis) == 30
    assert ergebnis[-1].name == "Kategorie 29"


def test_kategorien_setzen_laenge_60_erlaubt_und_zeit_default(conn: sqlite3.Connection) -> None:
    standard_namen = list(projektverwaltung.VORLAGEN[projektverwaltung.STANDARD_VORLAGE])
    basis_liste = [(name, True) for name in standard_namen]
    basis_liste.append(("K" * 60, True))

    vorher = int(time.time())
    ergebnis = projektverwaltung.kategorien_setzen(conn, "projekt_len60", basis_liste, von=1)
    nachher = int(time.time())
    assert ergebnis[-1].name == "K" * 60

    letztes_ereignis = conn.execute(
        "SELECT zeit FROM ereignisse WHERE art='kategorie.angelegt' ORDER BY id DESC"
    ).fetchone()[0]
    assert vorher <= letztes_ereignis <= nachher + 2


def test_kategorien_setzen_nur_aktiv_geaendert_und_neue_inaktiv(conn: sqlite3.Connection) -> None:
    projektverwaltung.einrichten(conn, "projekt_schalter_kat", "bauprojekt", von=1, now=100)
    # Bauprojekt: "Mangel", "Frage", "Hinweis", "Entscheidung", "Sonstiges"
    # Ändere NUR "Frage" von aktiv=True auf aktiv=False, Reihenfolge bleibt exakt unverändert
    nur_aktiv_wechsel = [
        ("Mangel", True),
        ("Frage", False),
        ("Hinweis", True),
        ("Entscheidung", True),
        ("Sonstiges", True),
        ("NeuInaktiv", False),
    ]
    ergebnis = projektverwaltung.kategorien_setzen(
        conn, "projekt_schalter_kat", nur_aktiv_wechsel, von=1, now=150
    )
    assert [k.aktiv for k in ergebnis] == [True, False, True, True, True, False]
    assert [k.name for k in ergebnis] == [
        "Mangel",
        "Frage",
        "Hinweis",
        "Entscheidung",
        "Sonstiges",
        "NeuInaktiv",
    ]

    ereignis_frage = conn.execute(
        "SELECT vorher, nachher FROM ereignisse WHERE art='kategorie.geaendert'"
    ).fetchone()
    assert ereignis_frage is not None
    assert json.loads(ereignis_frage["vorher"]) == {
        "name": "Frage",
        "aktiv": True,
        "reihenfolge": 1,
    }
    assert json.loads(ereignis_frage["nachher"]) == {
        "name": "Frage",
        "aktiv": False,
        "reihenfolge": 1,
    }

    ereignis_neu = conn.execute(
        "SELECT nachher FROM ereignisse WHERE art='kategorie.angelegt'"
    ).fetchone()
    assert ereignis_neu is not None
    assert json.loads(ereignis_neu["nachher"]) == {
        "name": "NeuInaktiv",
        "aktiv": False,
        "reihenfolge": 5,
    }
