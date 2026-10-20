import json
import sqlite3
from pathlib import Path

import pytest

from scanview_server import db


def test_migrate_legt_tabellen_an_und_ist_idempotent(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    assert db.migrate(conn) == 13
    assert db.migrate(conn) == 13
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {
        "users", "invites", "sessions", "project_roles", "passkeys", "challenges",
        "pois", "messungen", "ereignisse", "statistik_tag", "api_tokens", "git_export_stand",
        "lesezugriffe",
    } <= names
    trigger = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {"ereignisse_kein_update", "ereignisse_kein_delete", "lesezugriffe_kein_update",
            "lesezugriffe_kein_ersetzen"} <= trigger
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(project_meta)")}
    assert "version" in spalten
    ereignisse_spalten = {r[1] for r in conn.execute("PRAGMA table_info(ereignisse)")}
    assert "token_name" in ereignisse_spalten
    users_spalten = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    assert "git_name_zeigen" in users_spalten


def test_connect_setzt_pragmas(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 5000


def test_migration_0002_behaelt_drosselzeilen_und_setzt_zeitstempel(tmp_path: Path) -> None:
    """0002_sicherheit auf eine bestehende 0001-Datenbank: Drosselzeilen bleiben, bekommen den
    Migrationszeitpunkt als `geaendert_am` (altern ab jetzt), Index auf challenges.expires_at."""
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for stmt in db._statements(
        (Path(db.__file__).parent / "migrations" / "0001_konten.sql").read_text(encoding="utf-8"),
    ):
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version VALUES (1)")
    conn.execute("INSERT INTO throttle (key, failures) VALUES ('alt', 3)")
    assert db.migrate(conn) == 13
    row = conn.execute("SELECT failures, geaendert_am FROM throttle WHERE key='alt'").fetchone()
    assert row["failures"] == 3 and row["geaendert_am"] > 1_700_000_000
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "challenges_exp" in idx


def test_migration_0003_auf_bestehender_datenbank(tmp_path: Path) -> None:
    """Instanz L läuft mit Schema 1 — das Update (0002 Sicherheit + 0003) muss vorhandene Zeilen
    unverändert behalten; project_meta bekommt version = 0."""
    conn = db.connect(tmp_path / "alt.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for stmt in db._statements(
        (Path(db.__file__).parent / "migrations" / "0001_konten.sql").read_text(encoding="utf-8"),
    ):
        conn.execute(stmt)
    conn.execute("INSERT INTO schema_version (version) VALUES (1)")
    conn.execute(
        "INSERT INTO project_meta (project_id, title, description, updated_at)"
        " VALUES ('p', 'T', 'B', 1)",
    )
    assert db.migrate(conn) == 13
    row = conn.execute("SELECT title, description, version FROM project_meta").fetchone()
    assert tuple(row) == ("T", "B", 0)


def test_statements_trigger_einzeilig_bleibt_ganz() -> None:
    sql = (
        "CREATE TABLE t (a INT);\n"
        "CREATE TRIGGER x BEFORE DELETE ON t BEGIN SELECT RAISE(ABORT, 'nein'); END;\n"
    )
    teile = db._statements(sql)
    assert len(teile) == 2
    assert teile[1].endswith("END;")


def test_transaktion_rollt_bei_fehler_zurueck(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "x.db")
    db.migrate(conn)
    einfuegen = "INSERT INTO statistik_tag (project_id, tag, oeffnen) VALUES ('p', '2026-09-27', 1)"
    try:
        with db.transaktion(conn):
            conn.execute(einfuegen)
            raise RuntimeError("Abbruch")
    except RuntimeError:
        pass
    assert conn.execute("SELECT COUNT(*) FROM statistik_tag").fetchone()[0] == 0
    with db.transaktion(conn):
        conn.execute(einfuegen)
    assert conn.execute("SELECT COUNT(*) FROM statistik_tag").fetchone()[0] == 1


def test_migration_0004_zieht_fehlenden_ersetzen_trigger_nach(tmp_path: Path) -> None:
    """Datenbanken mit der alten 0003 (vor 944e246) hatten ereignisse_kein_ersetzen nicht —
    0004 legt ihn nach und den Index für die Anonymisierungs-Abfragen an.

    Aufgebaut wie eine echte Instanz auf Schema 3 (0001-0003 von der Platte, danach der Trigger
    entfernt — die alte 0003-Fassung kannte ihn noch nicht): ab hier läuft migrate() ganz normal
    bis zum aktuellen Kopf durch, ohne mit 0005 zu kollidieren (dessen Objekte existieren an
    dieser Stelle schlicht noch nicht).
    """
    conn = db.connect(tmp_path / "alt.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for name in ("0001_konten.sql", "0002_sicherheit.sql", "0003_bearbeiten.sql"):
        for stmt in db._statements(
            (Path(db.__file__).parent / "migrations" / name).read_text(encoding="utf-8"),
        ):
            conn.execute(stmt)
    conn.execute("DROP TRIGGER ereignisse_kein_ersetzen")
    conn.execute("INSERT INTO schema_version (version) VALUES (3)")
    assert db.migrate(conn) == 13
    trigger = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert "ereignisse_kein_ersetzen" in trigger
    idx = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert "ereignisse_art_objekt" in idx
    tabellen = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"api_tokens", "git_export_stand"} <= tabellen


def test_migration_0005_bestehende_ereignisse_bleiben_unveraendert(tmp_path: Path) -> None:
    """0005 fügt der Tabelle `ereignisse` nur eine NULL-Spalte hinzu (ADD COLUMN ändert keine
    Zeile) — ein Ereignis von vor der Migration bleibt inhaltlich unverändert, `token_name` ist
    für alte Zeilen NULL, und der Unveränderlichkeits-Trigger greift danach unverändert weiter."""
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for name in ("0001_konten.sql", "0002_sicherheit.sql", "0003_bearbeiten.sql",
                 "0004_protokoll_absichern.sql"):
        for stmt in db._statements(
            (Path(db.__file__).parent / "migrations" / name).read_text(encoding="utf-8"),
        ):
            conn.execute(stmt)
    conn.execute("INSERT INTO schema_version (version) VALUES (4)")
    conn.execute(
        "INSERT INTO ereignisse (zeit, kanal, art, objekt_id)"
        " VALUES (1000, 'cli', 'konto.gesperrt', '1')",
    )
    eid = conn.execute("SELECT id FROM ereignisse").fetchone()[0]
    assert db.migrate(conn) == 13
    row = conn.execute(
        "SELECT zeit, kanal, art, objekt_id, token_name FROM ereignisse WHERE id=?", (eid,),
    ).fetchone()
    assert tuple(row) == (1000, "cli", "konto.gesperrt", "1", None)
    with pytest.raises(sqlite3.DatabaseError, match="unveränderlich"):
        conn.execute("UPDATE ereignisse SET art='x.y' WHERE id=?", (eid,))


def test_migration_0006_bestehende_nutzer_bekommen_vorgabe_aus(tmp_path: Path) -> None:
    """0006 fügt `users.git_name_zeigen` als NOT NULL DEFAULT 0 hinzu (ADR 0011, Vorgabe aus) — ein
    Konto von vor der Migration bekommt automatisch 0 (kein Klarname), keine bestehende Zeile wird
    sonst verändert."""
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
    for name in ("0001_konten.sql", "0002_sicherheit.sql", "0003_bearbeiten.sql",
                 "0004_protokoll_absichern.sql", "0005_api_tokens.sql"):
        for stmt in db._statements(
            (Path(db.__file__).parent / "migrations" / name).read_text(encoding="utf-8"),
        ):
            conn.execute(stmt)
    conn.execute("INSERT INTO schema_version (version) VALUES (5)")
    conn.execute(
        "INSERT INTO users (name, email, is_admin, created_at) VALUES ('T', 't@b.de', 0, 1000)",
    )
    uid = conn.execute("SELECT id FROM users").fetchone()[0]
    assert db.migrate(conn) == 13
    row = conn.execute(
        "SELECT name, email, git_name_zeigen FROM users WHERE id=?", (uid,),
    ).fetchone()
    assert tuple(row) == ("T", "t@b.de", 0)


def _bis(conn: sqlite3.Connection, ziel: int) -> None:
    """Spielt die Migrationen 1..ziel von Hand ein (Datenbank auf einem älteren Stand)."""
    from importlib import resources
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    dateien = sorted(
        (f for f in resources.files("scanview_server.migrations").iterdir()
         if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    for f in dateien:
        v = int(f.name.split("_", 1)[0])
        if v > ziel:
            break
        conn.execute("BEGIN IMMEDIATE")
        for stmt in db._statements(f.read_text(encoding="utf-8")):
            conn.execute(stmt)
        conn.execute("INSERT INTO schema_version (version) VALUES (?)", (v,))
        conn.execute("COMMIT")


def test_migration_9_erhaelt_rollen_und_erweitert_check(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "alt.db")
    _bis(conn, 8)
    for uid, mail in ((1, "a@x.de"), (2, "b@x.de")):  # Pflichtspalten laut 0001_konten.sql
        conn.execute("INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)",
                     (uid, mail, mail))
    conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                 " VALUES (1, 'ost', 'bearbeiter')")
    conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                 " VALUES (2, 'ost', 'betrachter')")
    conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                 " VALUES (2, 'west', 'bearbeiter')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                     " VALUES (1, 'west', 'rueckmelder')")
    assert db.migrate(conn) == 13
    rollen = conn.execute(
        "SELECT user_id, project_id, role FROM project_roles ORDER BY 1, 2").fetchall()
    assert [tuple(r) for r in rollen] == [
        (1, "ost", "bearbeiter"), (2, "ost", "betrachter"), (2, "west", "bearbeiter")]
    conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                 " VALUES (1, 'west', 'rueckmelder')")
    with pytest.raises(sqlite3.IntegrityError):  # Primärschlüssel (Nutzer, Projekt) bleibt
        conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                     " VALUES (1, 'ost', 'betrachter')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO project_roles (user_id, project_id, role)"
                     " VALUES (1, 'sued', 'chef')")
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("DELETE FROM users WHERE id=2")  # ON DELETE CASCADE wirkt weiter
    assert conn.execute(
        "SELECT COUNT(*) FROM project_roles WHERE user_id=2").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM project_roles").fetchone()[0] == 2


def test_migration_10_erhaelt_rollen_und_einladungen(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "alt.db")
    _bis(conn, 9)
    for uid, mail in (
        (1, "a@x.de"),
        (2, "b@x.de"),
        (3, "c@x.de"),
    ):  # Pflichtspalten laut 0001_konten.sql
        conn.execute(
            "INSERT INTO users (id, email, name, created_at) VALUES (?,?,?,0)", (uid, mail, mail)
        )
    conn.execute("INSERT INTO project_roles VALUES (1, 'ost', 'bearbeiter')")
    conn.execute("INSERT INTO project_roles VALUES (2, 'ost', 'rueckmelder')")
    conn.execute("INSERT INTO project_roles VALUES (2, 'west', 'betrachter')")
    grants = json.dumps({"ost": "betrachter"})
    conn.execute(
        "INSERT INTO invites (token_hash, email, name, purpose, grants, created_at, expires_at)"
        " VALUES ('h1', 'd@x.de', 'D', 'neu', ?, 0, 99)",
        (grants,),
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO project_roles VALUES (3, 'ost', 'projektleitung')")
    assert db.migrate(conn) == 13
    rollen = conn.execute(
        "SELECT user_id, project_id, role, gewerk_id FROM project_roles ORDER BY 1, 2"
    ).fetchall()
    assert [tuple(r) for r in rollen] == [
        (1, "ost", "bearbeiter", None),
        (2, "ost", "rueckmelder", None),
        (2, "west", "betrachter", None),
    ]
    einladung = conn.execute("SELECT grants, project_id, gewerk_id FROM invites").fetchone()
    assert tuple(einladung) == (grants, None, None)
    conn.execute(
        "INSERT INTO project_roles (user_id, project_id, role) VALUES (3, 'ost', 'projektleitung')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO project_roles (user_id, project_id, role) VALUES (3, 'sued', 'chef')"
        )
    gid = conn.execute(
        "INSERT INTO gewerke (project_id, name, kurz, angelegt_am)"
            " VALUES ('ost', 'Elektro', 'EL', 0)"
    ).lastrowid
    conn.execute(
        "UPDATE project_roles SET gewerk_id=? WHERE user_id=1 AND project_id='ost'", (gid,)
    )
    with pytest.raises(sqlite3.IntegrityError):  # Name je Projekt eindeutig, ohne Groß/Klein
        conn.execute(
            "INSERT INTO gewerke (project_id, name, kurz, angelegt_am)"
                " VALUES ('ost', 'elektro', 'E2', 0)"
        )
    with pytest.raises(sqlite3.IntegrityError):  # unbekanntes Gewerk
        conn.execute("UPDATE project_roles SET gewerk_id=999 WHERE user_id=2 AND project_id='ost'")
    with pytest.raises(sqlite3.IntegrityError):  # unbekannte Vorlage
        conn.execute(
            "INSERT INTO projekte (project_id, vorlage, angelegt_am) VALUES ('ost', 'frei', 0)"
        )
    conn.execute("UPDATE invites SET gewerk_id=?", (gid,))
    conn.execute("DELETE FROM gewerke WHERE id=?", (gid,))
    assert conn.execute("SELECT gewerk_id FROM invites").fetchone()[0] is None
    assert (
        conn.execute(
            "SELECT gewerk_id FROM project_roles WHERE user_id=1 AND project_id='ost'"
        ).fetchone()[0]
        is None
    )
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute("DELETE FROM users WHERE id=2")  # ON DELETE CASCADE wirkt weiter
    assert conn.execute("SELECT COUNT(*) FROM project_roles WHERE user_id=2").fetchone()[0] == 0


def test_migrate_bricht_bei_uebersprungener_niedrigerer_migration_ab(tmp_path: Path) -> None:
    """Eine Datei mit kleinerer Nummer als die höchste eingespielte, die nie lief (z. B. zwei Zweige
    vergeben 0012/0013 und 0013 erscheint zuerst), darf nicht still übersprungen werden."""
    conn = db.connect(tmp_path / "x.db")
    hoechste = db.migrate(conn)
    conn.execute("DELETE FROM schema_version WHERE version = ?", (hoechste - 1,))
    conn.commit()
    with pytest.raises(db.MigrationsLuecke, match=f"{hoechste - 1:04d}"):
        db.migrate(conn)
