"""Mutationstests für verwaltung.py: Rollen, Gewerke und Beteiligte.

Deckt rechte_entziehen, recht_setzen, beteiligter_setzen, zugriff_erteilen,
GewerkUnbekannt, Erteilt und erteilt_text sowie Rechte-Eskalationsschutz ab.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scanview_server import db, protokoll, verwaltung


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    datenbank = db.connect(tmp_path / "test_verwaltung_rollen.db")
    db.migrate(datenbank)
    return datenbank


def _nutzer_erstellen(
    conn: sqlite3.Connection,
    nutzer_id: int,
    email: str = "nutzer@beispiel.de",
    name: str = "Test Nutzer",
    ist_admin: bool = False,
    gesperrt: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, 1000)",
        (nutzer_id, name, email, int(ist_admin), int(gesperrt)),
    )


def _projekt_erstellen(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        "INSERT INTO projekte (project_id, vorlage, rueckmeldungen, export_bearbeiter, "
        "angelegt_am) VALUES (?, 'bestandsaufnahme', 1, 1, 1000)",
        (project_id,),
    )


def _gewerk_erstellen(
    conn: sqlite3.Connection, gewerk_id: int, project_id: str, name: str = "Gewerk"
) -> None:
    conn.execute(
        "INSERT INTO gewerke (id, project_id, name, kurz, bauherrenseite, angelegt_am) "
        "VALUES (?, ?, ?, 'GW', 0, 1000)",
        (gewerk_id, project_id, name),
    )


def test_gewerk_unbekannt_ausnahme_und_pruefung(conn: sqlite3.Connection) -> None:
    assert issubclass(verwaltung.GewerkUnbekannt, ValueError)
    ausnahme = verwaltung.GewerkUnbekannt()
    assert str(ausnahme) == "Dieses Gewerk gibt es in diesem Projekt nicht."

    _projekt_erstellen(conn, "proj_x")
    _projekt_erstellen(conn, "proj_y")
    _gewerk_erstellen(conn, 11, "proj_x", "Elektro")

    verwaltung._gewerk_im_projekt(conn, "proj_x", None)
    verwaltung._gewerk_im_projekt(conn, "proj_x", 11)

    with pytest.raises(
        verwaltung.GewerkUnbekannt,
        match=r"^Dieses Gewerk gibt es in diesem Projekt nicht\.$",
    ):
        verwaltung._gewerk_im_projekt(conn, "proj_x", 999)

    with pytest.raises(
        verwaltung.GewerkUnbekannt,
        match=r"^Dieses Gewerk gibt es in diesem Projekt nicht\.$",
    ):
        verwaltung._gewerk_im_projekt(conn, "proj_y", 11)


def test_erteilt_dataclass_und_text() -> None:
    erteilt_ok = verwaltung.Erteilt("zugriff-erteilt", False)
    assert erteilt_ok.art == "zugriff-erteilt"
    assert erteilt_ok.gesperrt is False
    assert verwaltung.erteilt_text(erteilt_ok) == "Konto vorhanden — Zugriff erteilt."

    erteilt_gesperrt = verwaltung.Erteilt("zugriff-erteilt", True)
    assert (
        verwaltung.erteilt_text(erteilt_gesperrt)
        == "Konto vorhanden — Zugriff erteilt. Das Konto ist gesperrt; "
        "freischalten kann nur die Instanzverwaltung."
    )

    besteht_ok = verwaltung.Erteilt("zugriff-besteht", False)
    assert (
        verwaltung.erteilt_text(besteht_ok)
        == "Konto vorhanden — Zugriff besteht bereits (eine gleiche oder höhere Rolle bleibt)."
    )

    besteht_gesperrt = verwaltung.Erteilt("zugriff-besteht", True)
    assert (
        verwaltung.erteilt_text(besteht_gesperrt)
        == "Konto vorhanden — Zugriff besteht bereits (eine gleiche oder höhere Rolle bleibt). "
        "Das Konto ist gesperrt; freischalten kann nur die Instanzverwaltung."
    )


def test_rechte_entziehen_alle_sortiert_nach_projekt(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 3, email="admin3@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 10, email="u10@b.de")
    _nutzer_erstellen(conn, 20, email="u20@b.de")
    _projekt_erstellen(conn, "proj_z")
    _projekt_erstellen(conn, "proj_a")
    _projekt_erstellen(conn, "proj_m")

    sql_rolle = "INSERT INTO project_roles (user_id, project_id, role) VALUES (?, ?, ?)"
    conn.execute(sql_rolle, (10, "proj_z", "betrachter"))
    conn.execute(sql_rolle, (10, "proj_a", "bearbeiter"))
    conn.execute(sql_rolle, (10, "proj_m", "rueckmelder"))
    conn.execute(sql_rolle, (20, "proj_z", "projektleitung"))

    entzogen = verwaltung.rechte_entziehen(
        conn,
        10,
        None,
        von=3,
        kanal="cli",
        now=1700000600,
    )
    assert entzogen == 3

    assert conn.execute("SELECT COUNT(*) FROM project_roles WHERE user_id=10").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM project_roles WHERE user_id=20").fetchone()[0] == 1

    ereignisse = [
        ereignis for ereignis in reversed(protokoll.lesen(conn)) if ereignis.art == "recht.entzogen"
    ]
    assert len(ereignisse) == 3

    assert (ereignisse[0].project_id, ereignisse[0].vorher) == ("proj_a", "bearbeiter")
    assert (ereignisse[1].project_id, ereignisse[1].vorher) == ("proj_m", "rueckmelder")
    assert (ereignisse[2].project_id, ereignisse[2].vorher) == ("proj_z", "betrachter")

    for ereignis in ereignisse:
        assert ereignis.objekt_id == "10"
        assert ereignis.nutzer_id == 3
        assert ereignis.kanal == "cli"
        assert ereignis.zeit == 1700000600


def test_rechte_entziehen_einzelnes_projekt(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 4, email="admin4@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 30, email="u30@b.de")
    _projekt_erstellen(conn, "proj_1")
    _projekt_erstellen(conn, "proj_2")

    sql_rolle = "INSERT INTO project_roles (user_id, project_id, role) VALUES (?, ?, ?)"
    conn.execute(sql_rolle, (30, "proj_1", "bearbeiter"))
    conn.execute(sql_rolle, (30, "proj_2", "betrachter"))

    entzogen = verwaltung.rechte_entziehen(
        conn,
        30,
        "proj_1",
        von=4,
        kanal="web",
        now=1700000610,
    )
    assert entzogen == 1

    rollen = conn.execute("SELECT project_id, role FROM project_roles WHERE user_id=30").fetchall()
    assert len(rollen) == 1
    assert (rollen[0]["project_id"], rollen[0]["role"]) == ("proj_2", "betrachter")

    kein_entzug = verwaltung.rechte_entziehen(
        conn,
        30,
        "proj_unbekannt",
        von=4,
        kanal="web",
        now=1700000611,
    )
    assert kein_entzug == 0


def test_recht_setzen_validierung_und_anonymisiert(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 1, email="admin1@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 40, email="u40@b.de")
    _projekt_erstellen(conn, "proj_p")

    erwartete_meldung = r"^Rolle 'unbekannt' unbekannt\.$"
    with pytest.raises(ValueError, match=erwartete_meldung):
        verwaltung.recht_setzen(
            conn,
            40,
            "proj_p",
            "unbekannt",
            von=1,
            kanal="cli",
        )

    _nutzer_erstellen(conn, 50, email="geloescht-50@invalid")
    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="50")

    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung.recht_setzen(
            conn,
            50,
            "proj_p",
            "betrachter",
            von=1,
            kanal="cli",
        )

    entzug_anonym = verwaltung.recht_setzen(
        conn,
        50,
        "proj_p",
        None,
        von=1,
        kanal="cli",
    )
    assert entzug_anonym is None


def test_recht_setzen_aenderungen_und_keine_ereignisse_bei_gleichheit(
    conn: sqlite3.Connection,
) -> None:
    _nutzer_erstellen(conn, 7, email="admin7@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 60, email="u60@b.de")
    _projekt_erstellen(conn, "proj_fest")

    vorher_1 = verwaltung.recht_setzen(
        conn,
        60,
        "proj_fest",
        "betrachter",
        von=7,
        kanal="cli",
        now=1700000700,
    )
    assert vorher_1 is None

    zeile_1 = conn.execute(
        "SELECT role FROM project_roles WHERE user_id=60 AND project_id='proj_fest'"
    ).fetchone()
    assert zeile_1["role"] == "betrachter"

    ereignis_1 = protokoll.lesen(conn)[0]
    assert ereignis_1.art == "recht.vergeben"
    assert ereignis_1.vorher is None
    assert ereignis_1.nachher == "betrachter"
    assert ereignis_1.objekt_id == "60"
    assert ereignis_1.project_id == "proj_fest"
    assert ereignis_1.nutzer_id == 7
    assert ereignis_1.zeit == 1700000700

    vorher_gleich = verwaltung.recht_setzen(
        conn,
        60,
        "proj_fest",
        "betrachter",
        von=7,
        kanal="cli",
        now=1700000701,
    )
    assert vorher_gleich == "betrachter"
    assert len(protokoll.lesen(conn)) == 1

    vorher_2 = verwaltung.recht_setzen(
        conn,
        60,
        "proj_fest",
        "bearbeiter",
        von=7,
        kanal="web",
        now=1700000702,
    )
    assert vorher_2 == "betrachter"

    ereignis_2 = protokoll.lesen(conn)[0]
    assert ereignis_2.art == "recht.vergeben"
    assert ereignis_2.vorher == "betrachter"
    assert ereignis_2.nachher == "bearbeiter"

    vorher_3 = verwaltung.recht_setzen(
        conn,
        60,
        "proj_fest",
        None,
        von=7,
        kanal="cli",
        now=1700000703,
    )
    assert vorher_3 == "bearbeiter"

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM project_roles WHERE user_id=60 AND project_id='proj_fest'"
        ).fetchone()[0]
        == 0
    )

    ereignis_3 = protokoll.lesen(conn)[0]
    assert ereignis_3.art == "recht.entzogen"
    assert ereignis_3.vorher == "bearbeiter"
    assert ereignis_3.nachher is None

    vorher_bleibt_none = verwaltung.recht_setzen(
        conn,
        60,
        "proj_fest",
        None,
        von=7,
        kanal="cli",
    )
    assert vorher_bleibt_none is None
    assert len(protokoll.lesen(conn)) == 3


def test_beteiligter_setzen_eskalationsschutz_leitung(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 99, email="admin99@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 70, email="admin_ziel@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 71, email="leitung_ziel@b.de", ist_admin=False)
    _nutzer_erstellen(conn, 72, email="normal_ziel@b.de", ist_admin=False)
    _projekt_erstellen(conn, "proj_leitung")

    sql_rolle = "INSERT INTO project_roles (user_id, project_id, role) VALUES (?, ?, ?)"
    conn.execute(sql_rolle, (70, "proj_leitung", "bearbeiter"))
    conn.execute(sql_rolle, (71, "proj_leitung", "projektleitung"))
    conn.execute(sql_rolle, (72, "proj_leitung", "betrachter"))

    with pytest.raises(
        verwaltung.Verboten,
        match=r"^Rollen von Admins und Projektleitungen ändert nur die Instanzverwaltung\.$",
    ):
        verwaltung.beteiligter_setzen(
            conn,
            70,
            "proj_leitung",
            "bearbeiter",
            None,
            als_leitung=True,
            von=99,
            kanal="web",
        )

    with pytest.raises(
        verwaltung.Verboten,
        match=r"^Rollen von Admins und Projektleitungen ändert nur die Instanzverwaltung\.$",
    ):
        verwaltung.beteiligter_setzen(
            conn,
            71,
            "proj_leitung",
            "bearbeiter",
            None,
            als_leitung=True,
            von=99,
            kanal="web",
        )

    with pytest.raises(
        verwaltung.Verboten,
        match=r"^Die Projektleitung vergibt nur Betrachter, Rückmelder und Bearbeiter\.$",
    ):
        verwaltung.beteiligter_setzen(
            conn,
            72,
            "proj_leitung",
            "projektleitung",
            None,
            als_leitung=True,
            von=99,
            kanal="web",
        )

    vorher_admin = verwaltung.beteiligter_setzen(
        conn,
        70,
        "proj_leitung",
        "rueckmelder",
        None,
        als_leitung=False,
        von=99,
        kanal="web",
        now=1700000800,
    )
    assert vorher_admin == "bearbeiter"


def test_beteiligter_setzen_aenderungen_rollen_und_gewerke(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 1, email="admin1@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 15, email="admin15@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 80, email="u80@b.de")
    _nutzer_erstellen(conn, 81, email="unbeteiligt@b.de")
    _projekt_erstellen(conn, "proj_bet")
    _gewerk_erstellen(conn, 201, "proj_bet", "Rohbau")
    _gewerk_erstellen(conn, 202, "proj_bet", "Ausbau")

    with pytest.raises(LookupError, match=r"^nicht beteiligt$"):
        verwaltung.beteiligter_setzen(
            conn,
            81,
            "proj_bet",
            "bearbeiter",
            None,
            als_leitung=False,
            von=1,
            kanal="cli",
        )

    with pytest.raises(ValueError, match=r"^Rolle 'unbekannt' unbekannt\.$"):
        verwaltung.beteiligter_setzen(
            conn,
            80,
            "proj_bet",
            "unbekannt",
            None,
            als_leitung=False,
            von=1,
            kanal="cli",
        )

    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) "
        "VALUES (80, 'proj_bet', 'betrachter', NULL)"
    )

    vorher = verwaltung.beteiligter_setzen(
        conn,
        80,
        "proj_bet",
        "bearbeiter",
        201,
        als_leitung=True,
        von=15,
        kanal="web",
        now=1700000850,
    )
    assert vorher == "betrachter"

    zeile_1 = conn.execute(
        "SELECT role, gewerk_id FROM project_roles WHERE user_id=80 AND project_id='proj_bet'"
    ).fetchone()
    assert (zeile_1["role"], zeile_1["gewerk_id"]) == ("bearbeiter", 201)

    ereignisse_1 = protokoll.lesen(conn)
    assert len(ereignisse_1) == 2
    assert ereignisse_1[1].art == "recht.vergeben"
    assert ereignisse_1[1].vorher == "betrachter"
    assert ereignisse_1[1].nachher == "bearbeiter"
    assert ereignisse_1[0].art == "beteiligter.gewerk"
    assert ereignisse_1[0].vorher is None
    assert ereignisse_1[0].nachher == 201

    vorher_nur_gewerk = verwaltung.beteiligter_setzen(
        conn,
        80,
        "proj_bet",
        "bearbeiter",
        202,
        als_leitung=True,
        von=15,
        kanal="web",
        now=1700000851,
    )
    assert vorher_nur_gewerk == "bearbeiter"

    zeile_2 = conn.execute(
        "SELECT role, gewerk_id FROM project_roles WHERE user_id=80 AND project_id='proj_bet'"
    ).fetchone()
    assert (zeile_2["role"], zeile_2["gewerk_id"]) == ("bearbeiter", 202)

    ereignis_gewerk = protokoll.lesen(conn)[0]
    assert ereignis_gewerk.art == "beteiligter.gewerk"
    assert ereignis_gewerk.vorher == 201
    assert ereignis_gewerk.nachher == 202

    vorher_unveraendert = verwaltung.beteiligter_setzen(
        conn,
        80,
        "proj_bet",
        "bearbeiter",
        202,
        als_leitung=True,
        von=15,
        kanal="web",
        now=1700000852,
    )
    assert vorher_unveraendert == "bearbeiter"
    assert len(protokoll.lesen(conn)) == 3

    vorher_entzug = verwaltung.beteiligter_setzen(
        conn,
        80,
        "proj_bet",
        None,
        None,
        als_leitung=True,
        von=15,
        kanal="cli",
        now=1700000853,
    )
    assert vorher_entzug == "bearbeiter"

    assert (
        conn.execute(
            "SELECT COUNT(*) FROM project_roles WHERE user_id=80 AND project_id='proj_bet'"
        ).fetchone()[0]
        == 0
    )

    ereignis_entzug = protokoll.lesen(conn)[0]
    assert ereignis_entzug.art == "recht.entzogen"
    assert ereignis_entzug.vorher == "bearbeiter"


def test_zugriff_erteilen_regeln_und_admin(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 1, email="admin1@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 90, email="u90@b.de", ist_admin=False, gesperrt=False)
    _nutzer_erstellen(conn, 91, email="u91@b.de", ist_admin=True, gesperrt=False)
    _nutzer_erstellen(conn, 92, email="u92@b.de", ist_admin=True, gesperrt=True)
    _nutzer_erstellen(conn, 93, email="geloescht-93@invalid")
    _projekt_erstellen(conn, "proj_zugriff")
    _gewerk_erstellen(conn, 301, "proj_zugriff", "Statik")

    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="93")

    with pytest.raises(
        verwaltung.Verboten,
        match=r"^Die Projektleitung vergibt nur Betrachter, Rückmelder und Bearbeiter\.$",
    ):
        verwaltung.zugriff_erteilen(
            conn,
            90,
            "proj_zugriff",
            "projektleitung",
            None,
            als_leitung=True,
            von=1,
            kanal="web",
        )

    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung.zugriff_erteilen(
            conn,
            93,
            "proj_zugriff",
            "betrachter",
            None,
            als_leitung=False,
            von=1,
            kanal="cli",
        )

    with pytest.raises(
        verwaltung.GewerkUnbekannt,
        match=r"^Dieses Gewerk gibt es in diesem Projekt nicht\.$",
    ):
        verwaltung.zugriff_erteilen(
            conn,
            90,
            "proj_zugriff",
            "betrachter",
            999,
            als_leitung=False,
            von=1,
            kanal="cli",
        )

    with pytest.raises(LookupError, match=r"^nicht beteiligt$"):
        verwaltung.zugriff_erteilen(
            conn,
            9999,
            "proj_zugriff",
            "betrachter",
            None,
            als_leitung=False,
            von=1,
            kanal="cli",
        )

    status_admin_aktiv = verwaltung.zugriff_erteilen(
        conn,
        91,
        "proj_zugriff",
        "bearbeiter",
        None,
        als_leitung=False,
        von=1,
        kanal="cli",
    )
    assert status_admin_aktiv == verwaltung.Erteilt("zugriff-besteht", False)

    status_admin_gesperrt = verwaltung.zugriff_erteilen(
        conn,
        92,
        "proj_zugriff",
        "bearbeiter",
        None,
        als_leitung=False,
        von=1,
        kanal="cli",
    )
    assert status_admin_gesperrt == verwaltung.Erteilt("zugriff-besteht", True)

    status_neu = verwaltung.zugriff_erteilen(
        conn,
        90,
        "proj_zugriff",
        "bearbeiter",
        301,
        als_leitung=False,
        von=1,
        kanal="cli",
        now=1700000900,
    )
    assert status_neu == verwaltung.Erteilt("zugriff-erteilt", False)

    zeile_zugriff = conn.execute(
        "SELECT role, gewerk_id FROM project_roles WHERE user_id=90 AND project_id='proj_zugriff'"
    ).fetchone()
    assert (zeile_zugriff["role"], zeile_zugriff["gewerk_id"]) == ("bearbeiter", 301)

    status_niedriger = verwaltung.zugriff_erteilen(
        conn,
        90,
        "proj_zugriff",
        "betrachter",
        301,
        als_leitung=False,
        von=1,
        kanal="cli",
        now=1700000901,
    )
    assert status_niedriger == verwaltung.Erteilt("zugriff-besteht", False)
