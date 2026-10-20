"""Mutationstests für Gewerke, Textprüfung und Hilfsfunktionen in projektverwaltung.py."""

from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from scanview_server import db, projektverwaltung
from scanview_server.web import Ungueltig


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    db_pfad = tmp_path / "test_gewerke.db"
    verbindung = db.connect(db_pfad)
    db.migrate(verbindung)
    verbindung.execute(
        "INSERT INTO users (id, name, email, is_admin, created_at) "
        "VALUES (1, 'Admin', 'a@x.de', 1, 100)"
    )
    yield verbindung
    verbindung.close()


def test_jetzt_feste_zeit_und_aktuelle_zeit() -> None:
    fester_wert = 1700000000
    assert projektverwaltung._jetzt(fester_wert) == fester_wert

    vorher = int(time.time())
    dynamischer_wert = projektverwaltung._jetzt(None)
    nachher = int(time.time())
    assert isinstance(dynamischer_wert, int)
    assert vorher <= dynamischer_wert <= nachher + 2


def test_text_pruefen_erfolg_und_normalisierung() -> None:
    zusammengesetzt = "e\u0301"
    ergebnis = projektverwaltung.text_pruefen(zusammengesetzt, "Name", 10)
    assert ergebnis == "\u00e9"
    assert unicodedata.is_normalized("NFC", ergebnis)

    mit_leerzeichen = "   Elektroinstallation   "
    ergebnis_strip = projektverwaltung.text_pruefen(mit_leerzeichen, "Name", 30)
    assert ergebnis_strip == "Elektroinstallation"

    genau_max = "x" * 80
    assert projektverwaltung.text_pruefen(genau_max, "Name", 80) == genau_max

    zeichen_erlaubt = "A B~C"
    assert projektverwaltung.text_pruefen(zeichen_erlaubt, "Name", 10) == zeichen_erlaubt


def test_text_pruefen_fehlerfaelle() -> None:
    with pytest.raises(Ungueltig) as exc_leer:
        projektverwaltung.text_pruefen("", "Name", 80)
    assert str(exc_leer.value) == "Name fehlt."

    with pytest.raises(Ungueltig) as exc_spaces:
        projektverwaltung.text_pruefen("     ", "Kurzzeichen", 4)
    assert str(exc_spaces.value) == "Kurzzeichen fehlt."

    with pytest.raises(Ungueltig) as exc_zu_lang:
        projektverwaltung.text_pruefen("x" * 81, "Name", 80)
    assert str(exc_zu_lang.value) == "Name länger als 80 Zeichen."

    with pytest.raises(Ungueltig) as exc_kurz_zu_lang:
        projektverwaltung.text_pruefen("ABCDE", "Kurzzeichen", 4)
    assert str(exc_kurz_zu_lang.value) == "Kurzzeichen länger als 4 Zeichen."

    for steuerzeichen in ("\x00", "\x1f", "\x7f", "\t", "\n", "\r"):
        with pytest.raises(Ungueltig) as exc_steuer:
            projektverwaltung.text_pruefen(f"Te{steuerzeichen}st", "Name", 80)
        assert str(exc_steuer.value) == "Name enthält Steuerzeichen."


def test_gewerk_dataclass_und_hilfsfunktionen() -> None:
    instanz = projektverwaltung.Gewerk(id=7, name="Maler", kurz="MAL", bauherrenseite=True)
    assert instanz.id == 7
    assert instanz.name == "Maler"
    assert instanz.kurz == "MAL"
    assert instanz.bauherrenseite is True

    erwartetes_json = {"id": 7, "name": "Maler", "kurz": "MAL", "bauherrenseite": True}
    assert instanz.als_json() == erwartetes_json

    erwartete_felder = {"name": "Maler", "kurz": "MAL", "bauherrenseite": True}
    assert projektverwaltung._felder(instanz) == erwartete_felder


def test_gewerk_anlegen_und_abfragen(conn: sqlite3.Connection) -> None:
    zeitstempel = 1700000000
    erstellt = projektverwaltung.gewerk_anlegen(
        conn,
        "projekt_alpha",
        name="Sanitär",
        kurz="SAN",
        bauherrenseite=False,
        von=1,
        now=zeitstempel,
    )
    assert isinstance(erstellt.id, int)
    assert erstellt.id > 0
    assert erstellt.name == "Sanitär"
    assert erstellt.kurz == "SAN"
    assert erstellt.bauherrenseite is False

    gelesen = projektverwaltung.gewerk(conn, "projekt_alpha", erstellt.id)
    assert gelesen is not None
    assert gelesen == erstellt

    gelesen_falsches_projekt = projektverwaltung.gewerk(conn, "projekt_beta", erstellt.id)
    assert gelesen_falsches_projekt is None

    gelesen_ungueltige_id = projektverwaltung.gewerk(conn, "projekt_alpha", 999999)
    assert gelesen_ungueltige_id is None

    ereignis = conn.execute(
        "SELECT art, objekt_id, nutzer_id, project_id, kanal, zeit, nachher "
        "FROM ereignisse WHERE art='gewerk.angelegt'"
    ).fetchone()
    assert ereignis is not None
    assert ereignis["art"] == "gewerk.angelegt"
    assert ereignis["objekt_id"] == str(erstellt.id)
    assert ereignis["nutzer_id"] == 1
    assert ereignis["project_id"] == "projekt_alpha"
    assert ereignis["kanal"] == "web"
    assert ereignis["zeit"] == zeitstempel
    nachher_daten = json.loads(ereignis["nachher"])
    assert nachher_daten == {"name": "Sanitär", "kurz": "SAN", "bauherrenseite": False}


def test_gewerke_liste_sortierung_und_filterung(conn: sqlite3.Connection) -> None:
    assert projektverwaltung.gewerke(conn, "projekt_leer") == []

    projektverwaltung.gewerk_anlegen(
        conn, "projekt_sort", name="Zimmerer", kurz="ZIM", bauherrenseite=False, von=1, now=100
    )
    projektverwaltung.gewerk_anlegen(
        conn, "projekt_sort", name="abbruch", kurz="ABB", bauherrenseite=True, von=1, now=101
    )
    projektverwaltung.gewerk_anlegen(
        conn, "projekt_sort", name="Abbruch 2", kurz="AB2", bauherrenseite=False, von=1, now=102
    )
    projektverwaltung.gewerk_anlegen(
        conn, "projekt_sort", name="Beton", kurz="BET", bauherrenseite=True, von=1, now=103
    )

    projektverwaltung.gewerk_anlegen(
        conn, "projekt_anderes", name="Dach", kurz="DAC", bauherrenseite=False, von=1, now=104
    )

    liste = projektverwaltung.gewerke(conn, "projekt_sort")
    assert len(liste) == 4
    namen = [item.name for item in liste]
    assert namen == ["abbruch", "Abbruch 2", "Beton", "Zimmerer"]

    kurz_liste = [item.kurz for item in liste]
    assert kurz_liste == ["ABB", "AB2", "BET", "ZIM"]

    bauherren_liste = [item.bauherrenseite for item in liste]
    assert bauherren_liste == [True, False, True, False]

    liste_anderes = projektverwaltung.gewerke(conn, "projekt_anderes")
    assert len(liste_anderes) == 1
    assert liste_anderes[0].name == "Dach"


def test_gewerk_anlegen_validierung_und_grenzwerte(conn: sqlite3.Connection) -> None:
    projektverwaltung.gewerk_anlegen(
        conn, "projekt_limit", name="Rohbau", kurz="ROH", bauherrenseite=True, von=1, now=10
    )

    with pytest.raises(Ungueltig) as exc_doppelt:
        projektverwaltung.gewerk_anlegen(
            conn, "projekt_limit", name="rohbau", kurz="RO2", bauherrenseite=False, von=1, now=11
        )
    assert str(exc_doppelt.value) == projektverwaltung.GEWERK_DOPPELT

    in_anderem_projekt = projektverwaltung.gewerk_anlegen(
        conn, "projekt_neu", name="Rohbau", kurz="ROH", bauherrenseite=False, von=1, now=12
    )
    assert in_anderem_projekt.name == "Rohbau"

    for index in range(2, projektverwaltung.GEWERKE_MAX + 1):
        name_wert = f"Gewerk {index:02d}"
        kurz_wert = f"G{index:02d}"
        projektverwaltung.gewerk_anlegen(
            conn,
            "projekt_limit",
            name=name_wert,
            kurz=kurz_wert,
            bauherrenseite=False,
            von=1,
            now=100 + index,
        )

    assert len(projektverwaltung.gewerke(conn, "projekt_limit")) == projektverwaltung.GEWERKE_MAX

    with pytest.raises(Ungueltig) as exc_zu_viele:
        projektverwaltung.gewerk_anlegen(
            conn, "projekt_limit", name="ZuViel", kurz="ZV", bauherrenseite=False, von=1, now=200
        )
    assert str(exc_zu_viele.value) == projektverwaltung.GEWERKE_ZU_VIELE
    assert str(exc_zu_viele.value) == "Höchstens 50 Gewerke je Projekt."


def test_gewerk_aendern_einzelne_felder_und_protokoll(conn: sqlite3.Connection) -> None:
    urspruenglich = projektverwaltung.gewerk_anlegen(
        conn, "projekt_edit", name="Tiefbau", kurz="TIE", bauherrenseite=True, von=1, now=50
    )
    gid = urspruenglich.id

    geaendert_name = projektverwaltung.gewerk_aendern(
        conn,
        "projekt_edit",
        gid,
        name="Tiefbau Spezial",
        kurz=None,
        bauherrenseite=None,
        von=1,
        now=60,
    )
    assert geaendert_name.id == gid
    assert geaendert_name.name == "Tiefbau Spezial"
    assert geaendert_name.kurz == "TIE"
    assert geaendert_name.bauherrenseite is True

    geaendert_kurz = projektverwaltung.gewerk_aendern(
        conn, "projekt_edit", gid, name=None, kurz="TBS", bauherrenseite=None, von=1, now=70
    )
    assert geaendert_kurz.name == "Tiefbau Spezial"
    assert geaendert_kurz.kurz == "TBS"
    assert geaendert_kurz.bauherrenseite is True

    geaendert_seite = projektverwaltung.gewerk_aendern(
        conn, "projekt_edit", gid, name=None, kurz=None, bauherrenseite=False, von=1, now=80
    )
    assert geaendert_seite.name == "Tiefbau Spezial"
    assert geaendert_seite.kurz == "TBS"
    assert geaendert_seite.bauherrenseite is False

    komplett = projektverwaltung.gewerk_aendern(
        conn, "projekt_edit", gid, name="Kanalbau", kurz="KAN", bauherrenseite=True, von=1, now=90
    )
    assert komplett.name == "Kanalbau"
    assert komplett.kurz == "KAN"
    assert komplett.bauherrenseite is True

    ereignisse = conn.execute(
        "SELECT art, objekt_id, vorher, nachher, zeit FROM ereignisse "
        "WHERE art='gewerk.geaendert' ORDER BY id"
    ).fetchall()
    assert len(ereignisse) == 4

    letztes = ereignisse[-1]
    assert letztes["objekt_id"] == str(gid)
    assert letztes["zeit"] == 90
    vorher_daten = json.loads(letztes["vorher"])
    nachher_daten = json.loads(letztes["nachher"])
    assert vorher_daten == {"name": "Tiefbau Spezial", "kurz": "TBS", "bauherrenseite": False}
    assert nachher_daten == {"name": "Kanalbau", "kurz": "KAN", "bauherrenseite": True}


def test_gewerk_aendern_unveraendert_ohne_protokoll(conn: sqlite3.Connection) -> None:
    start = projektverwaltung.gewerk_anlegen(
        conn, "projekt_noop", name="Gartenbau", kurz="GAR", bauherrenseite=False, von=1, now=100
    )
    gid = start.id

    unveraendert = projektverwaltung.gewerk_aendern(
        conn, "projekt_noop", gid, name=None, kurz=None, bauherrenseite=None, von=1, now=110
    )
    assert unveraendert == start

    unveraendert_explizit = projektverwaltung.gewerk_aendern(
        conn,
        "projekt_noop",
        gid,
        name="Gartenbau",
        kurz="GAR",
        bauherrenseite=False,
        von=1,
        now=120,
    )
    assert unveraendert_explizit == start

    ereignisse = conn.execute(
        "SELECT COUNT(*) FROM ereignisse WHERE art='gewerk.geaendert'"
    ).fetchone()[0]
    assert ereignisse == 0


def test_gewerk_aendern_fehlerfaelle(conn: sqlite3.Connection) -> None:
    g1 = projektverwaltung.gewerk_anlegen(
        conn, "projekt_err", name="Mauerwerk", kurz="MAU", bauherrenseite=False, von=1, now=10
    )
    g2 = projektverwaltung.gewerk_anlegen(
        conn, "projekt_err", name="Pflaster", kurz="PFL", bauherrenseite=True, von=1, now=11
    )

    with pytest.raises(LookupError) as exc_nicht_da:
        projektverwaltung.gewerk_aendern(
            conn, "projekt_err", 999999, name="Neu", kurz=None, bauherrenseite=None, von=1, now=12
        )
    assert exc_nicht_da.value.args[0] == 999999

    with pytest.raises(LookupError):
        projektverwaltung.gewerk_aendern(
            conn,
            "falsches_projekt",
            g1.id,
            name="Neu",
            kurz=None,
            bauherrenseite=None,
            von=1,
            now=13,
        )

    with pytest.raises(Ungueltig) as exc_kollision:
        projektverwaltung.gewerk_aendern(
            conn,
            "projekt_err",
            g2.id,
            name="mauerwerk",
            kurz=None,
            bauherrenseite=None,
            von=1,
            now=14,
        )
    assert str(exc_kollision.value) == projektverwaltung.GEWERK_DOPPELT

    selbst = projektverwaltung.gewerk_aendern(
        conn, "projekt_err", g1.id, name="mauerwerk", kurz="MAU", bauherrenseite=None, von=1, now=15
    )
    assert selbst.name == "mauerwerk"


def test_gewerk_loeschen_kaskade_und_protokoll(conn: sqlite3.Connection) -> None:
    g_loesch = projektverwaltung.gewerk_anlegen(
        conn, "projekt_del", name="Abbruch", kurz="ABB", bauherrenseite=False, von=1, now=200
    )
    g_anderes = projektverwaltung.gewerk_anlegen(
        conn, "projekt_del", name="Gerüstbau", kurz="GER", bauherrenseite=True, von=1, now=201
    )
    gid = g_loesch.id

    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, created_at) "
        "VALUES (2, 'Beteiligter 1', 'b1@x.de', 0, 100), (3, 'Beteiligter 2', 'b2@x.de', 0, 100)"
    )
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) "
        "VALUES (2, 'projekt_del', 'bearbeiter', ?), (3, 'projekt_del', 'bearbeiter', ?)",
        (gid, g_anderes.id),
    )
    conn.execute(
        "INSERT INTO invites ("
        "token_hash, email, name, purpose, created_at, expires_at, project_id, gewerk_id) "
        "VALUES ('hash_1', 'inv1@x.de', 'Inv 1', 'neu', 100, 200, 'projekt_del', ?), "
        "       ('hash_2', 'inv2@x.de', 'Inv 2', 'neu', 100, 200, 'projekt_del', ?)",
        (gid, g_anderes.id),
    )

    zeitstempel_del = 1700000500
    entfernte_zuordnungen = projektverwaltung.gewerk_loeschen(
        conn, "projekt_del", gid, von=1, now=zeitstempel_del
    )
    assert entfernte_zuordnungen == 1

    assert projektverwaltung.gewerk(conn, "projekt_del", gid) is None

    rolle_1 = conn.execute(
        "SELECT gewerk_id FROM project_roles WHERE user_id=2 AND project_id='projekt_del'"
    ).fetchone()
    assert rolle_1["gewerk_id"] is None

    rolle_2 = conn.execute(
        "SELECT gewerk_id FROM project_roles WHERE user_id=3 AND project_id='projekt_del'"
    ).fetchone()
    assert rolle_2["gewerk_id"] == g_anderes.id

    einladung_1 = conn.execute("SELECT gewerk_id FROM invites WHERE token_hash='hash_1'").fetchone()
    assert einladung_1["gewerk_id"] is None

    einladung_2 = conn.execute("SELECT gewerk_id FROM invites WHERE token_hash='hash_2'").fetchone()
    assert einladung_2["gewerk_id"] == g_anderes.id

    ereignis = conn.execute(
        "SELECT art, objekt_id, vorher, nachher, zeit FROM ereignisse WHERE art='gewerk.geloescht'"
    ).fetchone()
    assert ereignis is not None
    assert ereignis["objekt_id"] == str(gid)
    assert ereignis["zeit"] == zeitstempel_del
    vorher_daten = json.loads(ereignis["vorher"])
    nachher_daten = json.loads(ereignis["nachher"])
    assert vorher_daten == {"name": "Abbruch", "kurz": "ABB", "bauherrenseite": False}
    assert nachher_daten == {"ohneGewerk": 1}

    with pytest.raises(LookupError) as exc_del_wiederholt:
        projektverwaltung.gewerk_loeschen(conn, "projekt_del", gid, von=1, now=210)
    assert exc_del_wiederholt.value.args[0] == gid


def test_text_pruefen_strip_und_zeichen_details() -> None:
    assert projektverwaltung.text_pruefen("   Vorne", "Name", 10) == "Vorne"
    assert projektverwaltung.text_pruefen("Hinten   ", "Name", 10) == "Hinten"
    assert projektverwaltung.text_pruefen("  Beidseits  ", "Name", 20) == "Beidseits"
    assert projektverwaltung.text_pruefen("A B", "Name", 10) == "A B"
    assert projektverwaltung.text_pruefen("~", "Name", 10) == "~"


def test_gewerk_anlegen_laengen_und_zeit_default(conn: sqlite3.Connection) -> None:
    name_80 = "N" * 80
    kurz_4 = "K" * 4
    g_grenze = projektverwaltung.gewerk_anlegen(
        conn,
        "projekt_grenzen",
        name=name_80,
        kurz=kurz_4,
        bauherrenseite=True,
        von=1,
        now=1000,
    )
    assert g_grenze.name == name_80
    assert g_grenze.kurz == kurz_4

    with pytest.raises(Ungueltig) as exc_name_81:
        projektverwaltung.gewerk_anlegen(
            conn,
            "projekt_grenzen",
            name="N" * 81,
            kurz="K4",
            bauherrenseite=True,
            von=1,
            now=1001,
        )
    assert str(exc_name_81.value) == "Name länger als 80 Zeichen."

    with pytest.raises(Ungueltig) as exc_kurz_5:
        projektverwaltung.gewerk_anlegen(
            conn,
            "projekt_grenzen",
            name="Normal",
            kurz="K" * 5,
            bauherrenseite=True,
            von=1,
            now=1002,
        )
    assert str(exc_kurz_5.value) == "Kurzzeichen länger als 4 Zeichen."

    vorher = int(time.time())
    g_default_now = projektverwaltung.gewerk_anlegen(
        conn,
        "projekt_grenzen",
        name="OhneNow",
        kurz="ONOW",
        bauherrenseite=False,
        von=1,
    )
    nachher = int(time.time())
    db_zeit = conn.execute(
        "SELECT angelegt_am FROM gewerke WHERE id=?", (g_default_now.id,)
    ).fetchone()[0]
    assert vorher <= db_zeit <= nachher + 2

    ereignis_zeit = conn.execute(
        "SELECT zeit FROM ereignisse WHERE art='gewerk.angelegt' AND objekt_id=?",
        (str(g_default_now.id),),
    ).fetchone()[0]
    assert vorher <= ereignis_zeit <= nachher + 2


def test_gewerk_aendern_ungueltige_eingaben_und_zeit_default(conn: sqlite3.Connection) -> None:
    g = projektverwaltung.gewerk_anlegen(
        conn, "projekt_aendern", name="Basis", kurz="BAS", bauherrenseite=False, von=1, now=100
    )

    with pytest.raises(Ungueltig) as exc_leer_name:
        projektverwaltung.gewerk_aendern(
            conn, "projekt_aendern", g.id, name="   ", kurz=None, bauherrenseite=None, von=1
        )
    assert str(exc_leer_name.value) == "Name fehlt."

    with pytest.raises(Ungueltig) as exc_lang_name:
        projektverwaltung.gewerk_aendern(
            conn, "projekt_aendern", g.id, name="x" * 81, kurz=None, bauherrenseite=None, von=1
        )
    assert str(exc_lang_name.value) == "Name länger als 80 Zeichen."

    with pytest.raises(Ungueltig) as exc_leer_kurz:
        projektverwaltung.gewerk_aendern(
            conn, "projekt_aendern", g.id, name=None, kurz="   ", bauherrenseite=None, von=1
        )
    assert str(exc_leer_kurz.value) == "Kurzzeichen fehlt."

    with pytest.raises(Ungueltig) as exc_lang_kurz:
        projektverwaltung.gewerk_aendern(
            conn, "projekt_aendern", g.id, name=None, kurz="ABCDE", bauherrenseite=None, von=1
        )
    assert str(exc_lang_kurz.value) == "Kurzzeichen länger als 4 Zeichen."

    g_max = projektverwaltung.gewerk_aendern(
        conn,
        "projekt_aendern",
        g.id,
        name="y" * 80,
        kurz="z" * 4,
        bauherrenseite=True,
        von=1,
        now=200,
    )
    assert g_max.name == "y" * 80
    assert g_max.kurz == "z" * 4
    assert g_max.bauherrenseite is True

    vorher = int(time.time())
    g_now = projektverwaltung.gewerk_aendern(
        conn,
        "projekt_aendern",
        g.id,
        name="NeuNow",
        kurz="NNOW",
        bauherrenseite=False,
        von=1,
    )
    nachher = int(time.time())
    assert g_now.name == "NeuNow"
    assert g_now.bauherrenseite is False

    letztes_ereignis = conn.execute(
        "SELECT zeit FROM ereignisse WHERE art='gewerk.geaendert' ORDER BY id DESC"
    ).fetchone()[0]
    assert vorher <= letztes_ereignis <= nachher + 2


def test_gewerk_loeschen_ohne_beteiligte_und_zeit_default(conn: sqlite3.Connection) -> None:
    g = projektverwaltung.gewerk_anlegen(
        conn, "projekt_del_solo", name="Solo", kurz="SOL", bauherrenseite=True, von=1, now=100
    )

    vorher = int(time.time())
    entfernt = projektverwaltung.gewerk_loeschen(conn, "projekt_del_solo", g.id, von=1)
    nachher = int(time.time())
    assert entfernt == 0

    ereignis = conn.execute(
        "SELECT vorher, nachher, zeit FROM ereignisse WHERE art='gewerk.geloescht' AND objekt_id=?",
        (str(g.id),),
    ).fetchone()
    assert ereignis is not None
    assert json.loads(ereignis["vorher"]) == {"name": "Solo", "kurz": "SOL", "bauherrenseite": True}
    assert json.loads(ereignis["nachher"]) == {"ohneGewerk": 0}
    assert vorher <= ereignis["zeit"] <= nachher + 2
