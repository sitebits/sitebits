"""Mutationstests für Einstellungen, Vorlagen und wirksame Rollen in projektverwaltung.py."""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

from scanview_server import db, projektverwaltung


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_pfad = tmp_path / "test_einstellungen.db"
    verbindung = db.connect(db_pfad)
    db.migrate(verbindung)
    verbindung.execute(
        "INSERT INTO users (id, name, email, is_admin, created_at) "
        "VALUES (1, 'Admin', 'admin@example.com', 1, 100)"
    )
    yield verbindung
    verbindung.close()


def test_einstellungen_dataclass_und_json() -> None:
    standard = projektverwaltung.Einstellungen()
    assert standard.vorlage is None
    assert standard.rueckmeldungen is True
    assert standard.export_bearbeiter is True
    assert standard.als_json() == {
        "vorlage": None,
        "rueckmeldungen": True,
        "exportBearbeiter": True,
        "befundFaeden": False, "nurLeseLinks": False
    }

    benutzerdefiniert = projektverwaltung.Einstellungen(
        vorlage="bauprojekt",
        rueckmeldungen=False,
        export_bearbeiter=False,
        befund_faeden=True,
    )
    assert benutzerdefiniert.vorlage == "bauprojekt"
    assert benutzerdefiniert.rueckmeldungen is False
    assert benutzerdefiniert.export_bearbeiter is False
    assert benutzerdefiniert.als_json() == {
        "vorlage": "bauprojekt",
        "rueckmeldungen": False,
        "exportBearbeiter": False,
        "befundFaeden": True, "nurLeseLinks": False
    }
    assert projektverwaltung._schalter(benutzerdefiniert) == {
        "rueckmeldungen": False,
        "exportBearbeiter": False,
        "befundFaeden": True, "nurLeseLinks": False
    }


def test_wirksame_rolle_umschaltung() -> None:
    einstellungen_aktiv = projektverwaltung.Einstellungen(rueckmeldungen=True)
    assert projektverwaltung.wirksame_rolle("rueckmelder", einstellungen_aktiv) == "rueckmelder"
    assert projektverwaltung.wirksame_rolle("bearbeiter", einstellungen_aktiv) == "bearbeiter"
    assert projektverwaltung.wirksame_rolle("betrachter", einstellungen_aktiv) == "betrachter"
    assert (
        projektverwaltung.wirksame_rolle("projektleitung", einstellungen_aktiv) == "projektleitung"
    )
    assert projektverwaltung.wirksame_rolle(None, einstellungen_aktiv) is None

    einstellungen_inaktiv = projektverwaltung.Einstellungen(rueckmeldungen=False)
    assert projektverwaltung.wirksame_rolle("rueckmelder", einstellungen_inaktiv) == "betrachter"
    assert projektverwaltung.wirksame_rolle("bearbeiter", einstellungen_inaktiv) == "bearbeiter"
    assert projektverwaltung.wirksame_rolle("betrachter", einstellungen_inaktiv) == "betrachter"
    assert (
        projektverwaltung.wirksame_rolle("projektleitung", einstellungen_inaktiv)
        == "projektleitung"
    )
    assert projektverwaltung.wirksame_rolle(None, einstellungen_inaktiv) is None


def test_schon_eingerichtet_ausnahmetext() -> None:
    fehler_bau = projektverwaltung.SchonEingerichtet("bauprojekt")
    assert str(fehler_bau) == (
        "Das Projekt ist schon eingerichtet (Vorlage „Bauprojekt“). "
        "Kategorien und Schalter lassen sich weiter ändern."
    )

    fehler_bestand = projektverwaltung.SchonEingerichtet("bestandsaufnahme")
    assert str(fehler_bestand) == (
        "Das Projekt ist schon eingerichtet (Vorlage „Bestandsaufnahme“). "
        "Kategorien und Schalter lassen sich weiter ändern."
    )

    fehler_unbekannt = projektverwaltung.SchonEingerichtet("sonderfall")
    assert str(fehler_unbekannt) == (
        "Das Projekt ist schon eingerichtet (Vorlage „sonderfall“). "
        "Kategorien und Schalter lassen sich weiter ändern."
    )


def test_einstellungen_abfragen_standard_und_vorhanden(conn: sqlite3.Connection) -> None:
    stand_neu = projektverwaltung.einstellungen(conn, "unbekanntes_projekt")
    assert stand_neu == projektverwaltung.VORGABE
    assert stand_neu.vorlage is None
    assert stand_neu.rueckmeldungen is True
    assert stand_neu.export_bearbeiter is True

    alle_leer = projektverwaltung.alle_einstellungen(conn)
    assert alle_leer == {}

    conn.execute(
        "INSERT INTO projekte "
        "(project_id, vorlage, rueckmeldungen, export_bearbeiter, angelegt_am) "
        "VALUES ('projekt_1', 'bestandsaufnahme', 1, 0, 1000), "
        "       ('projekt_2', 'bauprojekt', 0, 1, 2000)"
    )

    p1 = projektverwaltung.einstellungen(conn, "projekt_1")
    assert p1.vorlage == "bestandsaufnahme"
    assert p1.rueckmeldungen is True
    assert p1.export_bearbeiter is False

    p2 = projektverwaltung.einstellungen(conn, "projekt_2")
    assert p2.vorlage == "bauprojekt"
    assert p2.rueckmeldungen is False
    assert p2.export_bearbeiter is True

    alle = projektverwaltung.alle_einstellungen(conn)
    assert set(alle.keys()) == {"projekt_1", "projekt_2"}
    assert alle["projekt_1"] == p1
    assert alle["projekt_2"] == p2


def test_einrichten_erfolg_und_kategorien(conn: sqlite3.Connection) -> None:
    zeitstempel = 1700003000
    ergebnis = projektverwaltung.einrichten(
        conn, "projekt_neu", "bauprojekt", von=1, now=zeitstempel
    )
    assert ergebnis.vorlage == "bauprojekt"
    assert ergebnis.rueckmeldungen is True
    assert ergebnis.export_bearbeiter is True

    zeile = conn.execute(
        "SELECT vorlage, rueckmeldungen, export_bearbeiter, angelegt_am FROM projekte "
        "WHERE project_id='projekt_neu'"
    ).fetchone()
    assert zeile is not None
    assert zeile["vorlage"] == "bauprojekt"
    assert zeile["rueckmeldungen"] == 1
    assert zeile["export_bearbeiter"] == 1
    assert zeile["angelegt_am"] == zeitstempel

    kategorien_zeilen = conn.execute(
        "SELECT name, reihenfolge, aktiv FROM projekt_kategorien "
        "WHERE project_id='projekt_neu' ORDER BY reihenfolge"
    ).fetchall()
    erwartete_namen = ["Mangel", "Frage", "Hinweis", "Entscheidung", "Sonstiges"]
    assert [k["name"] for k in kategorien_zeilen] == erwartete_namen
    assert [k["reihenfolge"] for k in kategorien_zeilen] == [0, 1, 2, 3, 4]
    assert all(k["aktiv"] == 1 for k in kategorien_zeilen)

    ereignis = conn.execute(
        "SELECT art, objekt_id, nutzer_id, project_id, kanal, zeit, nachher "
        "FROM ereignisse WHERE art='projekt.eingerichtet'"
    ).fetchone()
    assert ereignis is not None
    assert ereignis["objekt_id"] == "projekt_neu"
    assert ereignis["nutzer_id"] == 1
    assert ereignis["project_id"] == "projekt_neu"
    assert ereignis["kanal"] == "web"
    assert ereignis["zeit"] == zeitstempel
    nachher_daten = json.loads(ereignis["nachher"])
    assert nachher_daten == {
        "vorlage": "bauprojekt",
        "kategorien": erwartete_namen,
    }


def test_einrichten_fehlerfaelle(conn: sqlite3.Connection) -> None:
    with pytest.raises(ValueError) as exc_wert:
        projektverwaltung.einrichten(
            conn, "projekt_ungueltig", "ungueltige_vorlage", von=1, now=100
        )
    assert exc_wert.value.args[0] == "ungueltige_vorlage"

    projektverwaltung.einrichten(conn, "projekt_doppelt", "bestandsaufnahme", von=1, now=101)

    with pytest.raises(projektverwaltung.SchonEingerichtet) as exc_schon:
        projektverwaltung.einrichten(conn, "projekt_doppelt", "bauprojekt", von=1, now=102)
    assert "Bestandsaufnahme" in str(exc_schon.value)


def test_einstellungen_aendern_initialisiert_nicht_eingerichtetes_projekt(
    conn: sqlite3.Connection,
) -> None:
    zeitstempel = 1700004000
    geaendert = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_auto",
        rueckmeldungen=False,
        export_bearbeiter=None,
        von=1,
        now=zeitstempel,
    )
    assert geaendert.vorlage == "bestandsaufnahme"
    assert geaendert.rueckmeldungen is False
    assert geaendert.export_bearbeiter is True

    zeile = conn.execute(
        "SELECT vorlage, rueckmeldungen, export_bearbeiter FROM projekte "
        "WHERE project_id='projekt_auto'"
    ).fetchone()
    assert zeile is not None
    assert zeile["vorlage"] == "bestandsaufnahme"
    assert zeile["rueckmeldungen"] == 0
    assert zeile["export_bearbeiter"] == 1

    ereignis_eingerichtet = conn.execute(
        "SELECT art, nachher FROM ereignisse WHERE art='projekt.eingerichtet'"
    ).fetchone()
    assert ereignis_eingerichtet is not None
    nachher_eingerichtet = json.loads(ereignis_eingerichtet["nachher"])
    assert nachher_eingerichtet["vorlage"] == "bestandsaufnahme"

    ereignis_geaendert = conn.execute(
        "SELECT art, objekt_id, vorher, nachher, zeit FROM ereignisse "
        "WHERE art='einstellung.geaendert'"
    ).fetchone()
    assert ereignis_geaendert is not None
    assert ereignis_geaendert["objekt_id"] == "projekt_auto"
    assert ereignis_geaendert["zeit"] == zeitstempel
    vorher_daten = json.loads(ereignis_geaendert["vorher"])
    nachher_daten = json.loads(ereignis_geaendert["nachher"])
    assert vorher_daten == {"rueckmeldungen": True, "exportBearbeiter": True,
                            "befundFaeden": False, "nurLeseLinks": False}
    assert nachher_daten == {"rueckmeldungen": False, "exportBearbeiter": True,
                             "befundFaeden": False, "nurLeseLinks": False}


def test_einstellungen_aendern_unveraendert_schreibt_kein_ereignis(
    conn: sqlite3.Connection,
) -> None:
    vorher_einstellungen = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_unveraendert",
        rueckmeldungen=True,
        export_bearbeiter=True,
        von=1,
        now=100,
    )
    assert vorher_einstellungen == projektverwaltung.VORGABE

    projekte_zeilen = conn.execute("SELECT COUNT(*) FROM projekte").fetchone()[0]
    assert projekte_zeilen == 0

    ereignisse_anzahl = conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0]
    assert ereignisse_anzahl == 0


def test_einstellungen_aendern_auf_bestehendem_projekt(conn: sqlite3.Connection) -> None:
    projektverwaltung.einrichten(conn, "projekt_bestehend", "bauprojekt", von=1, now=100)

    nach_export = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_bestehend",
        rueckmeldungen=None,
        export_bearbeiter=False,
        von=1,
        now=110,
    )
    assert nach_export.vorlage == "bauprojekt"
    assert nach_export.rueckmeldungen is True
    assert nach_export.export_bearbeiter is False

    nach_beidem = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_bestehend",
        rueckmeldungen=False,
        export_bearbeiter=True,
        von=1,
        now=120,
    )
    assert nach_beidem.vorlage == "bauprojekt"
    assert nach_beidem.rueckmeldungen is False
    assert nach_beidem.export_bearbeiter is True

    ereignisse = conn.execute(
        "SELECT vorher, nachher, zeit FROM ereignisse WHERE art='einstellung.geaendert' ORDER BY id"
    ).fetchall()
    assert len(ereignisse) == 2

    erster_wechsel = ereignisse[0]
    assert json.loads(erster_wechsel["vorher"]) == {
        "rueckmeldungen": True,
        "exportBearbeiter": True,
        "befundFaeden": True, "nurLeseLinks": False
    }
    assert json.loads(erster_wechsel["nachher"]) == {
        "rueckmeldungen": True,
        "exportBearbeiter": False,
        "befundFaeden": True, "nurLeseLinks": False
    }

    zweiter_wechsel = ereignisse[1]
    assert json.loads(zweiter_wechsel["vorher"]) == {
        "rueckmeldungen": True,
        "exportBearbeiter": False,
        "befundFaeden": True, "nurLeseLinks": False
    }
    assert json.loads(zweiter_wechsel["nachher"]) == {
        "rueckmeldungen": False,
        "exportBearbeiter": True,
        "befundFaeden": True, "nurLeseLinks": False
    }


def test_einrichten_bestandsaufnahme_und_zeit_default(conn: sqlite3.Connection) -> None:
    vorher = int(time.time())
    e = projektverwaltung.einrichten(conn, "projekt_def_zeit", "bestandsaufnahme", von=1)
    nachher = int(time.time())
    assert e.vorlage == "bestandsaufnahme"
    assert e.rueckmeldungen is True
    assert e.export_bearbeiter is True

    kats = conn.execute(
        "SELECT name, reihenfolge, aktiv FROM projekt_kategorien "
        "WHERE project_id='projekt_def_zeit' ORDER BY reihenfolge"
    ).fetchall()
    assert [k["name"] for k in kats] == ["Raum", "Anlage", "Zugang", "Mangel", "Sonstiges"]
    assert [k["reihenfolge"] for k in kats] == [0, 1, 2, 3, 4]
    assert all(k["aktiv"] == 1 for k in kats)

    row = conn.execute(
        "SELECT angelegt_am FROM projekte WHERE project_id='projekt_def_zeit'"
    ).fetchone()
    assert vorher <= row["angelegt_am"] <= nachher + 2


def test_einstellungen_aendern_rueckkehr_auf_true_und_zeit_default(
    conn: sqlite3.Connection,
) -> None:
    projektverwaltung.einrichten(conn, "projekt_schalter", "bauprojekt", von=1, now=100)
    projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_schalter",
        rueckmeldungen=False,
        export_bearbeiter=False,
        von=1,
        now=105,
    )

    rueckkehr_rm = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_schalter",
        rueckmeldungen=True,
        export_bearbeiter=None,
        von=1,
        now=110,
    )
    assert rueckkehr_rm.rueckmeldungen is True
    assert rueckkehr_rm.export_bearbeiter is False

    rueckkehr_exp = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_schalter",
        rueckmeldungen=None,
        export_bearbeiter=True,
        von=1,
        now=120,
    )
    assert rueckkehr_exp.rueckmeldungen is True
    assert rueckkehr_exp.export_bearbeiter is True

    unveraendert = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_schalter",
        rueckmeldungen=None,
        export_bearbeiter=None,
        von=1,
        now=130,
    )
    assert unveraendert == rueckkehr_exp

    anzahl_ereignisse = conn.execute(
        "SELECT COUNT(*) FROM ereignisse WHERE art='einstellung.geaendert'"
    ).fetchone()[0]
    assert anzahl_ereignisse == 3

    vorher = int(time.time())
    wieder_aus = projektverwaltung.einstellungen_aendern(
        conn,
        "projekt_schalter",
        rueckmeldungen=False,
        export_bearbeiter=None,
        von=1,
    )
    nachher = int(time.time())
    assert wieder_aus.rueckmeldungen is False

    letztes = conn.execute(
        "SELECT zeit FROM ereignisse WHERE art='einstellung.geaendert' ORDER BY id DESC"
    ).fetchone()[0]
    assert vorher <= letztes <= nachher + 2
