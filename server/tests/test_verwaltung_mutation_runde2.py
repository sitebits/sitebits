"""Gezielte Mutationstests (Runde 2) für verwaltung.py.

Tötet die verbliebenen 27 echten Mutanten aus UEBERLEBENDE-RUNDE2.md in den
Bereichen einladen, abmelden_ueberall, beteiligter_setzen und zugriff_erteilen
über exakte Verhaltensprüfungen (Zeitstempel, Nutzer-ID, Objekt-ID, Anonymisierungsschutz).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scanview_server import db, protokoll, verwaltung


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    datenbank = db.connect(tmp_path / "test_verwaltung_runde2.db")
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


def test_einladen_protokoll_erhaelt_festen_zeitstempel(conn: sqlite3.Connection) -> None:
    """Tötet x_einladen__mutmut_70 und x_einladen__mutmut_78:

    Das Ereignis einladung.angelegt muss exakt den übergebenen Zeitstempel now
    tragen statt der aktuellen Systemzeit.
    """
    _nutzer_erstellen(conn, 10, email="autor10@beispiel.de", ist_admin=True)
    _projekt_erstellen(conn, "proj_zeit")
    token = verwaltung.einladen(
        conn,
        email="zeit@beispiel.de",
        name="Zeit Nutzer",
        grants={},
        von=10,
        kanal="cli",
        now=1700000100,
        project_id="proj_zeit",
    )
    assert isinstance(token, str)

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "einladung.angelegt"
    assert ereignis.zeit == 1700000100
    assert ereignis.nutzer_id == 10
    assert ereignis.kanal == "cli"
    assert ereignis.project_id == "proj_zeit"


def test_abmelden_ueberall_protokoll_erhaelt_festen_zeitstempel(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_abmelden_ueberall__mutmut_20 und x_abmelden_ueberall__mutmut_27:

    Das Ereignis konto.abgemeldet muss exakt den übergebenen Zeitstempel now
    tragen statt der aktuellen Systemzeit.
    """
    _nutzer_erstellen(conn, 2, email="autor2@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 55, email="abmelden@beispiel.de")
    beendet = verwaltung.abmelden_ueberall(
        conn,
        55,
        von=2,
        kanal="web",
        now=1700000200,
    )
    assert beendet == 0

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "konto.abgemeldet"
    assert ereignis.zeit == 1700000200
    assert ereignis.nutzer_id == 2
    assert ereignis.objekt_id == "55"
    assert ereignis.kanal == "web"


def test_beteiligter_setzen_rolle_entziehen_vollstaendige_protokollierung(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_beteiligter_setzen__mutmut_5, 6, 46, 49, 51, 54, 57, 59, 62:

    Beim Entzug der Rolle (rolle=None) müssen Nutzer-ID (von), Objekt-ID (uid als String)
    und Zeitstempel vollständig und exakt im Ereignis recht.entzogen festgehalten werden.
    """
    _nutzer_erstellen(conn, 12, email="autor12@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 65, email="entzug@beispiel.de")
    _projekt_erstellen(conn, "proj_entzug")
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role) "
        "VALUES (65, 'proj_entzug', 'betrachter')"
    )

    alt = verwaltung.beteiligter_setzen(
        conn,
        65,
        "proj_entzug",
        None,
        gewerk_id=None,
        als_leitung=False,
        von=12,
        kanal="web",
        now=1700000300,
    )
    assert alt == "betrachter"
    anzahl = conn.execute(
        "SELECT COUNT(*) FROM project_roles WHERE user_id=65 AND project_id='proj_entzug'"
    ).fetchone()[0]
    assert anzahl == 0

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "recht.entzogen"
    assert ereignis.project_id == "proj_entzug"
    assert ereignis.objekt_id == "65"
    assert ereignis.nutzer_id == 12
    assert ereignis.zeit == 1700000300
    assert ereignis.vorher == "betrachter"
    assert ereignis.nachher is None
    assert ereignis.kanal == "web"


def test_beteiligter_setzen_anonymisiertes_konto_rollenwechsel_verhindert(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_beteiligter_setzen__mutmut_71:

    Bei einem Rollenwechsel (rolle != alt) muss _nicht_anonymisiert(conn, uid) mit
    der echten Nutzer-ID aufgerufen werden, damit anonymisierte Konten mit
    KontoAnonymisiert abgewiesen werden.
    """
    _nutzer_erstellen(conn, 1, email="autor1@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 75, email="geloescht-75@invalid", gesperrt=True)
    _projekt_erstellen(conn, "proj_anon")
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role) "
        "VALUES (75, 'proj_anon', 'betrachter')"
    )
    conn.execute(
        "INSERT INTO ereignisse (zeit, nutzer_id, kanal, art, objekt_id) "
        "VALUES (1000, 1, 'web', 'konto.anonymisiert', '75')"
    )
    assert verwaltung.ist_anonymisiert(conn, 75)

    with pytest.raises(verwaltung.KontoAnonymisiert):
        verwaltung.beteiligter_setzen(
            conn,
            75,
            "proj_anon",
            "bearbeiter",
            gewerk_id=None,
            als_leitung=False,
            von=1,
            kanal="cli",
            now=1700000400,
        )


def test_beteiligter_setzen_rolle_aendern_protokoll_vollstaendig(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_beteiligter_setzen__mutmut_89 und x_beteiligter_setzen__mutmut_98:

    Beim Rollenwechsel muss das Ereignis recht.vergeben exakt den übergebenen
    Zeitstempel now (über n) sowie Nutzer-ID und Objekt-ID tragen.
    """
    _nutzer_erstellen(conn, 14, email="autor14@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 85, email="aendern@beispiel.de")
    _projekt_erstellen(conn, "proj_aendern")
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role) "
        "VALUES (85, 'proj_aendern', 'betrachter')"
    )

    alt = verwaltung.beteiligter_setzen(
        conn,
        85,
        "proj_aendern",
        "bearbeiter",
        gewerk_id=None,
        als_leitung=False,
        von=14,
        kanal="cli",
        now=1700000500,
    )
    assert alt == "betrachter"

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "recht.vergeben"
    assert ereignis.project_id == "proj_aendern"
    assert ereignis.objekt_id == "85"
    assert ereignis.nutzer_id == 14
    assert ereignis.zeit == 1700000500
    assert ereignis.vorher == "betrachter"
    assert ereignis.nachher == "bearbeiter"
    assert ereignis.kanal == "cli"


def test_beteiligter_setzen_gewerk_aendern_protokoll_vollstaendig(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_beteiligter_setzen__mutmut_114, 117, 120, 123, 126, 129, 132:

    Bei einer reinen Gewerkänderung müssen Nutzer-ID (von), Objekt-ID (uid als String)
    und Zeitstempel vollständig und exakt im Ereignis beteiligter.gewerk festgehalten werden.
    """
    _nutzer_erstellen(conn, 19, email="autor19@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 95, email="gewerk@beispiel.de")
    _projekt_erstellen(conn, "proj_gw")
    _gewerk_erstellen(conn, 201, "proj_gw", "Elektro")
    _gewerk_erstellen(conn, 202, "proj_gw", "Sanitaer")
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) "
        "VALUES (95, 'proj_gw', 'bearbeiter', 201)"
    )

    alt = verwaltung.beteiligter_setzen(
        conn,
        95,
        "proj_gw",
        "bearbeiter",
        gewerk_id=202,
        als_leitung=False,
        von=19,
        kanal="web",
        now=1700000600,
    )
    assert alt == "bearbeiter"

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "beteiligter.gewerk"
    assert ereignis.project_id == "proj_gw"
    assert ereignis.objekt_id == "95"
    assert ereignis.nutzer_id == 19
    assert ereignis.zeit == 1700000600
    assert ereignis.vorher == 201
    assert ereignis.nachher == 202
    assert ereignis.kanal == "web"


def test_beteiligter_setzen_rolle_und_gewerk_gemeinsam_protokoll_vollstaendig(
    conn: sqlite3.Connection,
) -> None:
    """Prüft gleichzeitigen Wechsel von Rolle und Gewerk auf korrekte Protokollierung."""
    _nutzer_erstellen(conn, 45, email="autor45@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 125, email="beides@beispiel.de")
    _projekt_erstellen(conn, "proj_beides")
    _gewerk_erstellen(conn, 401, "proj_beides", "Rohbau")
    _gewerk_erstellen(conn, 402, "proj_beides", "Ausbau")
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role, gewerk_id) "
        "VALUES (125, 'proj_beides', 'betrachter', 401)"
    )

    alt = verwaltung.beteiligter_setzen(
        conn,
        125,
        "proj_beides",
        "bearbeiter",
        gewerk_id=402,
        als_leitung=False,
        von=45,
        kanal="cli",
        now=1700000850,
    )
    assert alt == "betrachter"

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 2

    ereignisse_nach_art = {e.art: e for e in ereignisse}
    ereignis_recht = ereignisse_nach_art["recht.vergeben"]
    assert ereignis_recht.nutzer_id == 45
    assert ereignis_recht.zeit == 1700000850
    assert ereignis_recht.objekt_id == "125"
    assert ereignis_recht.vorher == "betrachter"
    assert ereignis_recht.nachher == "bearbeiter"

    ereignis_gw = ereignisse_nach_art["beteiligter.gewerk"]
    assert ereignis_gw.nutzer_id == 45
    assert ereignis_gw.zeit == 1700000850
    assert ereignis_gw.objekt_id == "125"
    assert ereignis_gw.vorher == 401
    assert ereignis_gw.nachher == 402


def test_zugriff_erteilen_protokoll_vollstaendig_mit_zeitstempel_und_autor(
    conn: sqlite3.Connection,
) -> None:
    """Tötet x_zugriff_erteilen__mutmut_4, 5, 45, 47:

    Bei Zugriffserteilung (recht_anheben) müssen autor (von=von) und Zeitstempel (now=n)
    vollständig an konten.recht_anheben übergeben und im Ereignis recht.vergeben
    protokolliert werden.
    """
    _nutzer_erstellen(conn, 33, email="autor33@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 105, email="zugriff@beispiel.de")
    _projekt_erstellen(conn, "proj_zugriff")

    erteilt = verwaltung.zugriff_erteilen(
        conn,
        105,
        "proj_zugriff",
        "bearbeiter",
        gewerk_id=None,
        als_leitung=False,
        von=33,
        kanal="cli",
        now=1700000700,
    )
    assert erteilt.art == "zugriff-erteilt"
    assert not erteilt.gesperrt

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "recht.vergeben"
    assert ereignis.project_id == "proj_zugriff"
    assert ereignis.objekt_id == "105"
    assert ereignis.nutzer_id == 33
    assert ereignis.zeit == 1700000700
    assert ereignis.vorher is None
    assert ereignis.nachher == "bearbeiter"
    assert ereignis.kanal == "cli"


def test_zugriff_erteilen_mit_gewerk_protokoll_vollstaendig(
    conn: sqlite3.Connection,
) -> None:
    """Prüft Protokollierung von recht.vergeben und beteiligter.gewerk bei zugriff_erteilen."""
    _nutzer_erstellen(conn, 34, email="autor34@beispiel.de", ist_admin=True)
    _nutzer_erstellen(conn, 115, email="zugriff_gw@beispiel.de")
    _projekt_erstellen(conn, "proj_zugriff_gw")
    _gewerk_erstellen(conn, 301, "proj_zugriff_gw", "Maler")

    erteilt = verwaltung.zugriff_erteilen(
        conn,
        115,
        "proj_zugriff_gw",
        "rueckmelder",
        gewerk_id=301,
        als_leitung=False,
        von=34,
        kanal="web",
        now=1700000750,
    )
    assert erteilt.art == "zugriff-erteilt"
    assert not erteilt.gesperrt

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 2

    ereignisse_nach_art = {e.art: e for e in ereignisse}
    ereignis_recht = ereignisse_nach_art["recht.vergeben"]
    assert ereignis_recht.nutzer_id == 34
    assert ereignis_recht.zeit == 1700000750
    assert ereignis_recht.objekt_id == "115"
    assert ereignis_recht.project_id == "proj_zugriff_gw"
    assert ereignis_recht.nachher == "rueckmelder"

    ereignis_gw = ereignisse_nach_art["beteiligter.gewerk"]
    assert ereignis_gw.nutzer_id == 34
    assert ereignis_gw.zeit == 1700000750
    assert ereignis_gw.objekt_id == "115"
    assert ereignis_gw.project_id == "proj_zugriff_gw"
    assert ereignis_gw.nachher == 301
