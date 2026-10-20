"""Mutationstests für verwaltung.py: Basisfunktionen und Einladungsverwaltung.

Deckt _jetzt, einladung_id, _sitzungen_beenden, _nicht_anonymisiert,
ist_anonymisiert, anonymisierte_ids, KontoAnonymisiert, einladen,
einladungen_widerrufen und einladung_widerrufen ab.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest

from scanview_server import db, protokoll, verwaltung
from scanview_server.tokens import hash_token


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    datenbank = db.connect(tmp_path / "test_verwaltung_probe.db")
    db.migrate(datenbank)
    return datenbank


def _nutzer_anlegen(
    conn: sqlite3.Connection,
    nutzer_id: int,
    email: str = "nutzer@beispiel.de",
    name: str = "Test Nutzer",
    ist_admin: bool = False,
    gesperrt: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (nutzer_id, name, email, int(ist_admin), int(gesperrt), 1000),
    )


def _projekt_anlegen(conn: sqlite3.Connection, project_id: str) -> None:
    conn.execute(
        "INSERT INTO projekte (project_id, vorlage, rueckmeldungen, export_bearbeiter, "
        "angelegt_am) VALUES (?, 'bestandsaufnahme', 1, 1, 1000)",
        (project_id,),
    )


def _gewerk_anlegen(
    conn: sqlite3.Connection, gewerk_id: int, project_id: str, name: str = "Gewerk"
) -> None:
    conn.execute(
        "INSERT INTO gewerke (id, project_id, name, kurz, bauherrenseite, angelegt_am) "
        "VALUES (?, ?, ?, 'GW', 0, 1000)",
        (gewerk_id, project_id, name),
    )


def test_konto_anonymisiert_ausnahme_eigenschaften() -> None:
    assert issubclass(verwaltung.KontoAnonymisiert, ValueError)
    ausnahme = verwaltung.KontoAnonymisiert()
    assert str(ausnahme) == "Konto wurde anonymisiert."
    assert ausnahme.args == ("Konto wurde anonymisiert.",)


def test_ist_anonymisiert_und_anonymisierte_ids(conn: sqlite3.Connection) -> None:
    assert verwaltung.ist_anonymisiert(conn, 1) is False
    assert verwaltung.anonymisierte_ids(conn) == set()

    protokoll.ereignis(conn, kanal="cli", art="konto.gesperrt", objekt_id="1")
    assert verwaltung.ist_anonymisiert(conn, 1) is False

    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="2")
    assert verwaltung.ist_anonymisiert(conn, 2) is True
    assert verwaltung.ist_anonymisiert(conn, 1) is False

    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="30")
    assert verwaltung.ist_anonymisiert(conn, 30) is True

    ergebnis = verwaltung.anonymisierte_ids(conn)
    assert ergebnis == {2, 30}
    assert all(isinstance(kennug, int) for kennug in ergebnis)


def test_nicht_anonymisiert_hilfsfunktion(conn: sqlite3.Connection) -> None:
    verwaltung._nicht_anonymisiert(conn, None)
    verwaltung._nicht_anonymisiert(conn, 999)

    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="55")
    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung._nicht_anonymisiert(conn, 55)


def test_einladung_id_zeichenketten_kuerzung() -> None:
    hash_wert = "0123456789abcdefghij"
    gekuerzt = verwaltung.einladung_id(hash_wert)
    assert gekuerzt == "0123456789abcdef"
    assert len(gekuerzt) == 16
    assert verwaltung.einladung_id("abcdefghijklmnop") == "abcdefghijklmnop"
    assert verwaltung.einladung_id("kurz") == "kurz"
    assert verwaltung.einladung_id("") == ""


def test_jetzt_zeit_parameter_und_standard() -> None:
    assert verwaltung._jetzt(1700000000) == 1700000000
    assert verwaltung._jetzt(0) == 0

    start_zeit = int(time.time())
    jetzt_zeit = verwaltung._jetzt(None)
    assert start_zeit <= jetzt_zeit <= int(time.time()) + 1


def test_sitzungen_beenden_zeilenanzahl_und_isolierung(conn: sqlite3.Connection) -> None:
    _nutzer_anlegen(conn, 10, email="u10@b.de")
    _nutzer_anlegen(conn, 20, email="u20@b.de")

    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at) "
        "VALUES ('s1', 10, 100, 100, 200), ('s2', 10, 101, 101, 201), ('s3', 10, 102, 102, 202), "
        "('s4', 20, 103, 103, 203), ('s5', 20, 104, 104, 204)"
    )

    beendet = verwaltung._sitzungen_beenden(conn, 10)
    assert beendet == 3

    verbleibend_10 = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=10").fetchone()[0]
    verbleibend_20 = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=20").fetchone()[0]
    assert verbleibend_10 == 0
    assert verbleibend_20 == 2

    nochmals = verwaltung._sitzungen_beenden(conn, 10)
    assert nochmals == 0


def test_einladen_zweck_und_admin_schalter(conn: sqlite3.Connection) -> None:
    _nutzer_anlegen(conn, 10, email="admin10@b.de", ist_admin=True)

    token_neu = verwaltung.einladen(
        conn,
        email="neu@b.de",
        name="Neu Nutzer",
        grants={"p1": "bearbeiter"},
        is_admin=False,
        zuruecksetzen=False,
        von=10,
        kanal="cli",
        now=1700000001,
    )
    assert isinstance(token_neu, str)

    zeile_neu = conn.execute(
        "SELECT purpose, is_admin, created_by, created_at FROM invites WHERE email='neu@b.de'"
    ).fetchone()
    assert (zeile_neu["purpose"], zeile_neu["is_admin"]) == ("neu", 0)
    assert (zeile_neu["created_by"], zeile_neu["created_at"]) == (10, 1700000001)

    ereignis_neu = protokoll.lesen(conn)[0]
    assert ereignis_neu.art == "einladung.angelegt"
    assert ereignis_neu.kanal == "cli"
    assert ereignis_neu.nutzer_id == 10
    assert ereignis_neu.project_id is None
    assert ereignis_neu.objekt_id == verwaltung.einladung_id(hash_token(token_neu))
    assert ereignis_neu.nachher == {
        "zweck": "neu",
        "admin": False,
        "rechte": {"p1": "bearbeiter"},
    }

    _nutzer_anlegen(conn, 11, email="reset@b.de", name="Reset Nutzer")

    token_reset = verwaltung.einladen(
        conn,
        email="reset@b.de",
        name="Reset Nutzer",
        grants={},
        is_admin=True,
        zuruecksetzen=True,
        von=None,
        kanal="web",
        now=1700000002,
    )
    assert isinstance(token_reset, str)

    zeile_reset = conn.execute(
        "SELECT purpose, is_admin, created_by, created_at FROM invites WHERE email='reset@b.de'"
    ).fetchone()
    assert (zeile_reset["purpose"], zeile_reset["is_admin"]) == ("zuruecksetzen", 1)
    assert zeile_reset["created_by"] is None
    assert zeile_reset["created_at"] == 1700000002

    ereignis_reset = protokoll.lesen(conn)[0]
    assert ereignis_reset.art == "einladung.angelegt"
    assert ereignis_reset.kanal == "web"
    assert ereignis_reset.nutzer_id is None
    assert ereignis_reset.nachher == {
        "zweck": "zuruecksetzen",
        "admin": True,
        "rechte": {},
    }


def test_einladen_mit_projekt_und_gewerk(conn: sqlite3.Connection) -> None:
    _nutzer_anlegen(conn, 1, email="admin1@b.de", ist_admin=True)
    _projekt_anlegen(conn, "proj1")
    _projekt_anlegen(conn, "proj2")
    _gewerk_anlegen(conn, 101, "proj1", "Architektur")
    _gewerk_anlegen(conn, 202, "proj2", "Statik")

    token = verwaltung.einladen(
        conn,
        email="gewerk@b.de",
        name="Gewerk Nutzer",
        grants={"proj1": "bearbeiter"},
        is_admin=False,
        von=1,
        kanal="cli",
        now=1700000010,
        project_id="proj1",
        gewerk_id=101,
    )
    zeile = conn.execute(
        "SELECT project_id, gewerk_id FROM invites WHERE email='gewerk@b.de'"
    ).fetchone()
    assert (zeile["project_id"], zeile["gewerk_id"]) == ("proj1", 101)

    ereignis = protokoll.lesen(conn)[0]
    assert ereignis.project_id == "proj1"
    assert ereignis.objekt_id == verwaltung.einladung_id(hash_token(token))
    assert ereignis.nachher == {
        "zweck": "neu",
        "admin": False,
        "rechte": {"proj1": "bearbeiter"},
        "gewerk": 101,
    }

    token_ohne = verwaltung.einladen(
        conn,
        email="ohne_gewerk@b.de",
        name="Ohne Gewerk",
        grants={},
        von=1,
        kanal="cli",
        now=1700000011,
        project_id="proj1",
        gewerk_id=None,
    )
    assert isinstance(token_ohne, str)
    ereignis_ohne = protokoll.lesen(conn)[0]
    assert ereignis_ohne.nachher == {
        "zweck": "neu",
        "admin": False,
        "rechte": {},
        "gewerk": None,
    }

    with pytest.raises(
        verwaltung.GewerkUnbekannt,
        match=r"^Dieses Gewerk gibt es in diesem Projekt nicht\.$",
    ):
        verwaltung.einladen(
            conn,
            email="falsch1@b.de",
            name="Falsch",
            grants={},
            von=1,
            kanal="cli",
            project_id="proj1",
            gewerk_id=999,
        )

    with pytest.raises(
        verwaltung.GewerkUnbekannt,
        match=r"^Dieses Gewerk gibt es in diesem Projekt nicht\.$",
    ):
        verwaltung.einladen(
            conn,
            email="falsch2@b.de",
            name="Falsch",
            grants={},
            von=1,
            kanal="cli",
            project_id="proj1",
            gewerk_id=202,
        )


def test_einladen_verhindert_einladung_anonymisierter_adresse(
    conn: sqlite3.Connection,
) -> None:
    _nutzer_anlegen(conn, 1, email="geloescht-1@invalid", name="Gelöschtes Konto 1")
    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="1")

    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung.einladen(
            conn,
            email="geloescht-1@invalid",
            name="Neu",
            grants={},
            von=None,
            kanal="cli",
        )


def test_offene_einladungen_kontingent_grenzpruefung(conn: sqlite3.Connection) -> None:
    _projekt_anlegen(conn, "limit_proj")
    _projekt_anlegen(conn, "andere_proj")
    jetzt = 1000

    sql_offen = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, "
        "project_id) VALUES (?, ?, 'Name', 'neu', 500, 2000, 'limit_proj')"
    )
    for index in range(199):
        conn.execute(sql_offen, (f"token_hash_{index:04d}", f"offen_{index}@b.de"))

    sql_abgelaufen = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, "
        "project_id) VALUES (?, ?, 'Name', 'neu', 500, 900, 'limit_proj')"
    )
    sql_eingeloest = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, "
        "used_at, project_id) VALUES (?, ?, 'Name', 'neu', 500, 2000, 800, 'limit_proj')"
    )
    sql_andere = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, "
        "project_id) VALUES (?, ?, 'Name', 'neu', 500, 2000, 'andere_proj')"
    )
    for index in range(5):
        conn.execute(sql_abgelaufen, (f"abgelaufen_hash_{index}", f"abgelaufen_{index}@b.de"))
        conn.execute(sql_eingeloest, (f"eingeloest_hash_{index}", f"eingeloest_{index}@b.de"))
        conn.execute(sql_andere, (f"andere_hash_{index}", f"andere_{index}@b.de"))

    token_200 = verwaltung.einladen(
        conn,
        email="einladung_200@b.de",
        name="Nummer 200",
        grants={},
        von=None,
        kanal="cli",
        now=jetzt,
        project_id="limit_proj",
    )
    assert isinstance(token_200, str)

    sql_zaehlung = (
        "SELECT COUNT(*) FROM invites WHERE project_id='limit_proj' AND used_at IS NULL "
        "AND expires_at > ?"
    )
    anzahl_offen = conn.execute(sql_zaehlung, (jetzt,)).fetchone()[0]
    assert anzahl_offen == 200

    erwartete_meldung = (
        r"^Dieses Projekt hat schon 200 offene Einladungen\. Widerrufen Sie "
        r"nicht mehr benötigte Einladungen, bevor Sie weitere anlegen\.$"
    )
    with pytest.raises(ValueError, match=erwartete_meldung):
        verwaltung.einladen(
            conn,
            email="einladung_201@b.de",
            name="Nummer 201",
            grants={},
            von=None,
            kanal="cli",
            now=jetzt,
            project_id="limit_proj",
        )

    token_global = verwaltung.einladen(
        conn,
        email="global_einladung@b.de",
        name="Global",
        grants={},
        von=None,
        kanal="cli",
        now=jetzt,
        project_id=None,
    )
    assert isinstance(token_global, str)


def test_einladungen_widerrufen_mehrere_und_leerzeichen(conn: sqlite3.Connection) -> None:
    _nutzer_anlegen(conn, 12, email="admin12@b.de", ist_admin=True)
    _projekt_anlegen(conn, "proj_a")
    _projekt_anlegen(conn, "proj_b")

    token_a = verwaltung.einladen(
        conn,
        email="multi@b.de",
        name="Multi",
        grants={},
        von=None,
        kanal="cli",
        project_id="proj_a",
    )
    token_b = verwaltung.einladen(
        conn,
        email="multi@b.de",
        name="Multi",
        grants={},
        von=None,
        kanal="cli",
        project_id="proj_b",
    )
    conn.execute(
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, used_at) "
        "VALUES ('alt_used_hash', 'multi@b.de', 'Multi', 'neu', 100, 500, 300)"
    )

    widerrufen_anzahl = verwaltung.einladungen_widerrufen(
        conn,
        "  multi@b.de  ",
        von=12,
        kanal="cli",
        now=1700000050,
    )
    assert widerrufen_anzahl == 2

    zeile_a = conn.execute(
        "SELECT used_at FROM invites WHERE token_hash=?", (hash_token(token_a),)
    ).fetchone()
    zeile_b = conn.execute(
        "SELECT used_at FROM invites WHERE token_hash=?", (hash_token(token_b),)
    ).fetchone()
    zeile_alt = conn.execute(
        "SELECT used_at FROM invites WHERE token_hash='alt_used_hash'"
    ).fetchone()
    assert zeile_a["used_at"] == 1700000050
    assert zeile_b["used_at"] == 1700000050
    assert zeile_alt["used_at"] == 300

    ereignisse = [
        ereignis for ereignis in protokoll.lesen(conn) if ereignis.art == "einladung.widerrufen"
    ]
    assert len(ereignisse) == 2
    projekte_im_protokoll = {ereignis.project_id for ereignis in ereignisse}
    assert projekte_im_protokoll == {"proj_a", "proj_b"}
    assert all(ereignis.zeit == 1700000050 for ereignis in ereignisse)
    assert all(ereignis.nutzer_id == 12 for ereignis in ereignisse)
    assert all(ereignis.kanal == "cli" for ereignis in ereignisse)

    keine_weiteren = verwaltung.einladungen_widerrufen(
        conn, "multi@b.de", von=12, kanal="cli", now=1700000051
    )
    assert keine_weiteren == 0


def test_einladung_widerrufen_einzeln_filter_und_ablauf(conn: sqlite3.Connection) -> None:
    _nutzer_anlegen(conn, 10, email="admin10@b.de", ist_admin=True)
    _projekt_anlegen(conn, "p_ziel")
    _projekt_anlegen(conn, "p_fremd")
    jetzt = 1000

    hash_ziel = "hash_ziel_1234567890"
    hash_fremd = "hash_fremd_123456789"
    hash_global = "hash_global_12345678"
    hash_genau_abgelaufen = "hash_genau_abgelaufen"
    hash_noch_gueltig = "hash_noch_gueltig_12"
    hash_bereits_eingeloest = "hash_bereits_eingelo"

    sql_einladungen = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, "
        "project_id) VALUES (?, ?, ?, 'neu', 500, ?, ?)"
    )
    eintraege = [
        (hash_ziel, "z1@b.de", "Z1", 2000, "p_ziel"),
        (hash_fremd, "f1@b.de", "F1", 2000, "p_fremd"),
        (hash_global, "g1@b.de", "G1", 2000, None),
        (hash_genau_abgelaufen, "a1@b.de", "A1", 1000, "p_ziel"),
        (hash_noch_gueltig, "a2@b.de", "A2", 1001, "p_ziel"),
        (hash_bereits_eingeloest, "e1@b.de", "E1", 2000, "p_ziel"),
    ]
    for datensatz in eintraege:
        conn.execute(sql_einladungen, datensatz)
    conn.execute("UPDATE invites SET used_at=700 WHERE token_hash=?", (hash_bereits_eingeloest,))

    eid_ziel = hash_ziel[:16]
    eid_fremd = hash_fremd[:16]
    eid_global = hash_global[:16]
    eid_genau_abgelaufen = hash_genau_abgelaufen[:16]
    eid_noch_gueltig = hash_noch_gueltig[:16]
    eid_bereits_eingeloest = hash_bereits_eingeloest[:16]

    assert (
        verwaltung.einladung_widerrufen(
            conn,
            "unbekannt_123456",
            von=10,
            kanal="cli",
            nur_projekt=None,
            now=jetzt,
        )
        is False
    )

    assert (
        verwaltung.einladung_widerrufen(
            conn,
            eid_bereits_eingeloest,
            von=10,
            kanal="cli",
            nur_projekt=None,
            now=jetzt,
        )
        is False
    )

    assert (
        verwaltung.einladung_widerrufen(
            conn,
            eid_genau_abgelaufen,
            von=10,
            kanal="cli",
            nur_projekt=None,
            now=jetzt,
        )
        is False
    )

    assert (
        verwaltung.einladung_widerrufen(
            conn,
            eid_fremd,
            von=10,
            kanal="cli",
            nur_projekt="p_ziel",
            now=jetzt,
        )
        is False
    )

    assert (
        verwaltung.einladung_widerrufen(
            conn,
            eid_global,
            von=10,
            kanal="cli",
            nur_projekt="p_ziel",
            now=jetzt,
        )
        is False
    )

    erfolg_gueltig = verwaltung.einladung_widerrufen(
        conn,
        eid_noch_gueltig,
        von=10,
        kanal="cli",
        nur_projekt="p_ziel",
        now=jetzt,
    )
    assert erfolg_gueltig is True
    zeile_gueltig = conn.execute(
        "SELECT used_at FROM invites WHERE substr(token_hash, 1, 16)=?",
        (eid_noch_gueltig,),
    ).fetchone()
    assert zeile_gueltig["used_at"] == jetzt

    ereignis = protokoll.lesen(conn)[0]
    assert ereignis.art == "einladung.widerrufen"
    assert ereignis.project_id == "p_ziel"
    assert ereignis.objekt_id == eid_noch_gueltig
    assert ereignis.nutzer_id == 10
    assert ereignis.kanal == "cli"
    assert ereignis.zeit == jetzt

    erfolg_ziel = verwaltung.einladung_widerrufen(
        conn,
        eid_ziel,
        von=10,
        kanal="cli",
        nur_projekt="p_ziel",
        now=jetzt,
    )
    assert erfolg_ziel is True

    erfolg_global = verwaltung.einladung_widerrufen(
        conn,
        eid_global,
        von=10,
        kanal="cli",
        nur_projekt=None,
        now=jetzt,
    )
    assert erfolg_global is True
