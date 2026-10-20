"""Mutationstests für verwaltung.py: Kontenverwaltung, Sitzungen und DSGVO-Anonymisierung.

Deckt sperren, entsperren, abmelden_ueberall und anonymisieren ab,
inklusive Tabellenbereinigung (sessions, passkeys, passwords, api_tokens,
project_roles, challenges, throttle, invites) und Protokollierung.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from scanview_server import apitoken, db, passwort, protokoll, verwaltung


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    datenbank = db.connect(tmp_path / "test_verwaltung_konten.db")
    db.migrate(datenbank)
    return datenbank


def _nutzer_erstellen(
    conn: sqlite3.Connection,
    nutzer_id: int,
    email: str = "konto@beispiel.de",
    name: str = "Test Konto",
    ist_admin: bool = False,
    gesperrt: bool = False,
    git_name: bool = False,
) -> None:
    conn.execute(
        "INSERT INTO users (id, name, email, is_admin, disabled, created_at, git_name_zeigen) "
        "VALUES (?, ?, ?, ?, ?, 1000, ?)",
        (nutzer_id, name, email, int(ist_admin), int(gesperrt), int(git_name)),
    )


def test_sperren_ohne_sitzungen_und_ohne_tokens(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 1, email="admin1@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 10, email="u10@b.de", gesperrt=False)

    beendete_sitzungen = verwaltung.sperren(
        conn,
        10,
        von=1,
        kanal="cli",
        now=1700000100,
    )
    assert beendete_sitzungen == 0

    zeile_nutzer = conn.execute("SELECT disabled FROM users WHERE id=10").fetchone()
    assert zeile_nutzer["disabled"] == 1

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "konto.gesperrt"
    assert ereignis.objekt_id == "10"
    assert ereignis.kanal == "cli"
    assert ereignis.nutzer_id == 1
    assert ereignis.zeit == 1700000100
    assert ereignis.nachher == {"sitzungenBeendet": 0, "tokensWiderrufen": 0}


def test_sperren_mit_sitzungen_und_tokens(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 5, email="admin5@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 20, email="u20@b.de")
    _nutzer_erstellen(conn, 30, email="u30@b.de")

    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at) "
        "VALUES ('ses_20_1', 20, 100, 100, 500), ('ses_20_2', 20, 101, 101, 501), "
        "('ses_30_1', 30, 102, 102, 502)"
    )

    roh_token_1, _ = apitoken.erzeugen(conn, 20, "Token1", "lesen", now=1700000000)
    roh_token_2, _ = apitoken.erzeugen(conn, 20, "Token2", "schreiben", now=1700000000)
    roh_token_3, _ = apitoken.erzeugen(conn, 30, "Token3", "lesen", now=1700000000)

    beendet = verwaltung.sperren(
        conn,
        20,
        von=5,
        kanal="web",
        now=1700000150,
    )
    assert beendet == 2

    zeile_20 = conn.execute("SELECT disabled FROM users WHERE id=20").fetchone()
    assert zeile_20["disabled"] == 1

    sitzungen_20 = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=20").fetchone()[0]
    sitzungen_30 = conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=30").fetchone()[0]
    assert sitzungen_20 == 0
    assert sitzungen_30 == 1

    assert apitoken.pruefen(conn, roh_token_1, now=1700000150) is None
    assert apitoken.pruefen(conn, roh_token_2, now=1700000150) is None
    assert apitoken.pruefen(conn, roh_token_3, now=1700000150) is not None

    ereignis = protokoll.lesen(conn)[0]
    assert ereignis.art == "konto.gesperrt"
    assert ereignis.objekt_id == "20"
    assert ereignis.kanal == "web"
    assert ereignis.nutzer_id == 5
    assert ereignis.zeit == 1700000150
    assert ereignis.nachher == {"sitzungenBeendet": 2, "tokensWiderrufen": 2}


def test_entsperren_erfolg_und_protokoll(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 9, email="admin9@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 40, email="u40@b.de", gesperrt=True)

    rueckgabe = verwaltung.entsperren(
        conn,
        40,
        von=9,
        kanal="cli",
        now=1700000200,
    )
    assert rueckgabe is None

    zeile_nutzer = conn.execute("SELECT disabled FROM users WHERE id=40").fetchone()
    assert zeile_nutzer["disabled"] == 0

    ereignisse = protokoll.lesen(conn)
    assert len(ereignisse) == 1
    ereignis = ereignisse[0]
    assert ereignis.art == "konto.entsperrt"
    assert ereignis.objekt_id == "40"
    assert ereignis.kanal == "cli"
    assert ereignis.nutzer_id == 9
    assert ereignis.zeit == 1700000200


def test_entsperren_anonymisiertes_konto_abgelehnt(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 50, email="geloescht-50@invalid", gesperrt=True)
    protokoll.ereignis(conn, kanal="cli", art="konto.anonymisiert", objekt_id="50")

    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung.entsperren(
            conn,
            50,
            von=1,
            kanal="cli",
            now=1700000210,
        )

    zeile_nutzer = conn.execute("SELECT disabled FROM users WHERE id=50").fetchone()
    assert zeile_nutzer["disabled"] == 1


def test_abmelden_ueberall_beendet_sitzungen_und_widerruft_tokens(
    conn: sqlite3.Connection,
) -> None:
    _nutzer_erstellen(conn, 2, email="admin2@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 60, email="u60@b.de", gesperrt=False)
    _nutzer_erstellen(conn, 70, email="u70@b.de", gesperrt=True)

    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at) "
        "VALUES ('ses_60_1', 60, 100, 100, 500), ('ses_60_2', 60, 101, 101, 501), "
        "('ses_70_1', 70, 102, 102, 502)"
    )

    roh_token_60, _ = apitoken.erzeugen(conn, 60, "Token60", "lesen", now=1700000000)
    roh_token_70, _ = apitoken.erzeugen(conn, 70, "Token70", "lesen", now=1700000000)

    beendet_60 = verwaltung.abmelden_ueberall(
        conn,
        60,
        von=2,
        kanal="cli",
        now=1700000250,
    )
    assert beendet_60 == 2

    beendet_70 = verwaltung.abmelden_ueberall(
        conn,
        70,
        von=2,
        kanal="web",
        now=1700000251,
    )
    assert beendet_70 == 1

    zeile_60 = conn.execute("SELECT disabled FROM users WHERE id=60").fetchone()
    zeile_70 = conn.execute("SELECT disabled FROM users WHERE id=70").fetchone()
    assert zeile_60["disabled"] == 0
    assert zeile_70["disabled"] == 1

    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=60").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=70").fetchone()[0] == 0

    assert apitoken.pruefen(conn, roh_token_60, now=1700000250) is None
    assert apitoken.pruefen(conn, roh_token_70, now=1700000251) is None

    ereignisse = protokoll.lesen(conn)
    ereignis_70 = ereignisse[0]
    ereignis_60 = ereignisse[1]

    assert ereignis_60.art == "konto.abgemeldet"
    assert ereignis_60.objekt_id == "60"
    assert ereignis_60.kanal == "cli"
    assert ereignis_60.nutzer_id == 2
    assert ereignis_60.nachher == {"sitzungenBeendet": 2, "tokensWiderrufen": 1}

    assert ereignis_70.art == "konto.abgemeldet"
    assert ereignis_70.objekt_id == "70"
    assert ereignis_70.kanal == "web"
    assert ereignis_70.nutzer_id == 2
    assert ereignis_70.nachher == {"sitzungenBeendet": 1, "tokensWiderrufen": 1}


def test_anonymisieren_unbekannte_id_schlaegt_fehl(conn: sqlite3.Connection) -> None:
    erwartete_meldung = r"^Kein Konto mit der ID 999\.$"
    with pytest.raises(LookupError, match=erwartete_meldung):
        verwaltung.anonymisieren(
            conn,
            999,
            von=1,
            kanal="cli",
            now=1700000300,
        )
    assert conn.execute("SELECT COUNT(*) FROM ereignisse").fetchone()[0] == 0


def test_anonymisieren_bereinigt_vollstaendig_und_isoliert(conn: sqlite3.Connection) -> None:
    ziel_id = 80
    fremd_id = 81
    ziel_mail = "  Max.Mustermann@beispiel.de  "
    fremd_mail = "andere.person@beispiel.de"

    _nutzer_erstellen(
        conn,
        77,
        email="admin77@b.de",
        name="Admin Siebenundsiebzig",
        ist_admin=True,
    )
    _nutzer_erstellen(
        conn,
        ziel_id,
        email="Max.Mustermann@beispiel.de",
        name="Max Mustermann",
        ist_admin=True,
        gesperrt=False,
        git_name=True,
    )
    _nutzer_erstellen(
        conn,
        fremd_id,
        email=fremd_mail,
        name="Andere Person",
        ist_admin=False,
        gesperrt=False,
        git_name=False,
    )

    sql_projekte = (
        "INSERT INTO projekte (project_id, vorlage, rueckmeldungen, export_bearbeiter, "
        "angelegt_am) VALUES ('p1', 'bestandsaufnahme', 1, 1, 1000), "
        "('p2', 'bestandsaufnahme', 1, 1, 1000)"
    )
    conn.execute(sql_projekte)

    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at) "
        "VALUES ('s_z1', 80, 10, 10, 20), ('s_z2', 80, 11, 11, 21), ('s_f1', 81, 12, 12, 22)"
    )

    conn.execute(
        "INSERT INTO passkeys (id, user_id, public_key, sign_count, label, created_at) "
        "VALUES ('pk_z1', 80, x'01', 0, 'K1', 10), ('pk_z2', 80, x'02', 0, 'K2', 11), "
        "('pk_f1', 81, x'03', 0, 'KF', 12)"
    )

    conn.execute(
        "INSERT INTO passwords (user_id, hash, updated_at) "
        "VALUES (80, 'hash_z', 10), (81, 'hash_f', 12)"
    )

    apitoken.erzeugen(conn, ziel_id, "TokenZ1", "lesen", now=10)
    apitoken.erzeugen(conn, ziel_id, "TokenZ2", "schreiben", now=11)
    apitoken.erzeugen(conn, fremd_id, "TokenF1", "lesen", now=12)

    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role) "
        "VALUES (80, 'p1', 'bearbeiter'), (80, 'p2', 'betrachter'), (81, 'p1', 'rueckmelder')"
    )

    conn.execute(
        "INSERT INTO challenges (id, challenge, purpose, user_id, expires_at) "
        "VALUES ('c_z1', x'aa', 'anmelden', 80, 2000), ('c_f1', x'bb', 'anmelden', 81, 2000)"
    )

    drossel_ziel = passwort.schluessel(ziel_mail.strip().lower())
    drossel_fremd = passwort.schluessel(fremd_mail.strip().lower())
    conn.execute(
        "INSERT INTO throttle (key, failures, locked_until, geaendert_am) "
        "VALUES (?, 3, 0, 10), (?, 2, 0, 12)",
        (drossel_ziel, drossel_fremd),
    )

    sql_inv = (
        "INSERT INTO invites (token_hash, email, name, purpose, created_at, expires_at, used_at) "
        "VALUES (?, ?, ?, 'neu', 100, 2000, ?)"
    )
    conn.execute(sql_inv, ("inv_z_offen_1", "max.mustermann@beispiel.de", "Max", None))
    conn.execute(sql_inv, ("inv_z_offen_2", "max.mustermann@beispiel.de", "Max", None))
    conn.execute(sql_inv, ("inv_z_geschw_1", "max.mustermann@beispiel.de", "Max", 500))
    conn.execute(sql_inv, ("inv_z_geschw_2", "max.mustermann@beispiel.de", "Max", 600))
    conn.execute(sql_inv, ("inv_f_offen_1", fremd_mail, "Andere", None))

    ergebnis = verwaltung.anonymisieren(
        conn,
        ziel_id,
        von=77,
        kanal="web",
        now=1700000400,
    )

    assert ergebnis == {
        "sitzungen": 2,
        "passkeys": 2,
        "passwort": 1,
        "tokens": 2,
        "rechte": 2,
        "einladungenGeloescht": 2,
        "einladungenGeschwaerzt": 2,
    }

    zeile_user_80 = conn.execute(
        "SELECT name, email, disabled, is_admin, git_name_zeigen FROM users WHERE id=80"
    ).fetchone()
    assert (
        zeile_user_80["name"],
        zeile_user_80["email"],
        zeile_user_80["disabled"],
        zeile_user_80["is_admin"],
        zeile_user_80["git_name_zeigen"],
    ) == ("Gelöschtes Konto 80", "geloescht-80@invalid", 1, 0, 0)

    zeile_user_81 = conn.execute(
        "SELECT name, email, disabled, is_admin, git_name_zeigen FROM users WHERE id=81"
    ).fetchone()
    assert (
        zeile_user_81["name"],
        zeile_user_81["email"],
        zeile_user_81["disabled"],
        zeile_user_81["is_admin"],
        zeile_user_81["git_name_zeigen"],
    ) == ("Andere Person", fremd_mail, 0, 0, 0)

    tabellen_bereinigt = (
        "sessions",
        "passkeys",
        "passwords",
        "api_tokens",
        "project_roles",
        "challenges",
    )
    for tabelle in tabellen_bereinigt:
        anzahl_80 = conn.execute(f"SELECT COUNT(*) FROM {tabelle} WHERE user_id=80").fetchone()[0]
        assert anzahl_80 == 0

    assert conn.execute("SELECT COUNT(*) FROM sessions WHERE user_id=81").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM passkeys WHERE user_id=81").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM passwords WHERE user_id=81").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id=81").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM project_roles WHERE user_id=81").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM challenges WHERE user_id=81").fetchone()[0] == 1

    anzahl_drossel_ziel = conn.execute(
        "SELECT COUNT(*) FROM throttle WHERE key=?", (drossel_ziel,)
    ).fetchone()[0]
    anzahl_drossel_fremd = conn.execute(
        "SELECT COUNT(*) FROM throttle WHERE key=?", (drossel_fremd,)
    ).fetchone()[0]
    assert anzahl_drossel_ziel == 0
    assert anzahl_drossel_fremd == 1

    sql_geschwaerzt = (
        "SELECT email, name, used_at FROM invites "
        "WHERE token_hash IN ('inv_z_geschw_1', 'inv_z_geschw_2')"
    )
    geschwaerzt = conn.execute(sql_geschwaerzt).fetchall()
    assert len(geschwaerzt) == 2
    for eintrag in geschwaerzt:
        assert (eintrag["email"], eintrag["name"]) == (
            "geloescht-80@invalid",
            "Gelöschtes Konto 80",
        )
        assert eintrag["used_at"] in (500, 600)

    geloescht = conn.execute(
        "SELECT COUNT(*) FROM invites WHERE token_hash IN ('inv_z_offen_1', 'inv_z_offen_2')"
    ).fetchone()[0]
    assert geloescht == 0

    fremd_invite = conn.execute(
        "SELECT email, name FROM invites WHERE token_hash='inv_f_offen_1'"
    ).fetchone()
    assert (fremd_invite["email"], fremd_invite["name"]) == (fremd_mail, "Andere")

    ereignis = protokoll.lesen(conn)[0]
    assert ereignis.art == "konto.anonymisiert"
    assert ereignis.objekt_id == "80"
    assert ereignis.kanal == "web"
    assert ereignis.nutzer_id == 77
    assert ereignis.zeit == 1700000400
    assert ereignis.nachher == ergebnis


def test_anonymisieren_erneuter_aufruf_scheitert(conn: sqlite3.Connection) -> None:
    _nutzer_erstellen(conn, 1, email="admin1@b.de", ist_admin=True)
    _nutzer_erstellen(conn, 90, email="u90@b.de")
    verwaltung.anonymisieren(conn, 90, von=1, kanal="cli", now=1700000500)

    with pytest.raises(verwaltung.KontoAnonymisiert, match=r"^Konto wurde anonymisiert\.$"):
        verwaltung.anonymisieren(conn, 90, von=1, kanal="cli", now=1700000501)
