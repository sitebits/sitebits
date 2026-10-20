"""SQLite: eine Verbindung je Anfrage, WAL, nummerierte Migrationen aus migrations/*.sql."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from importlib import resources
from pathlib import Path


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    # Ohne dies feuert SQLite den DELETE-Trigger nicht, wenn REPLACE-Konfliktbehandlung
    # (z. B. `INSERT OR REPLACE`) intern eine Zeile löscht — die Ereignisprotokoll-Trigger
    # (Migration 0003, ereignisse_kein_delete) sollen auch diesen Weg versperren.
    conn.execute("PRAGMA recursive_triggers=ON")
    return conn


class MigrationsLuecke(RuntimeError):
    """Eine Migration mit kleinerer Nummer als die höchste eingespielte lief nie."""


def migrate(conn: sqlite3.Connection) -> int:
    """Spielt fehlende Migrationen ein, gibt die erreichte Version zurück.

    Jede Datei trägt sich einzeln in `schema_version` ein (seit dem ersten Schema). Fehlt eine
    Nummer zwischen der niedrigsten und der höchsten eingetragenen, bricht der Start ab, statt sie
    still zu überspringen — etwa wenn zwei Zweige 0012/0013 vergeben und 0013 zuerst ausgeliefert
    wurde. Unterhalb der niedrigsten wird nicht geprüft (nachgebaute Altstände in Tests)."""
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()
    current = row[0] or 0
    eingespielt = {r[0] for r in conn.execute("SELECT version FROM schema_version")}
    migrations_dir = resources.files("scanview_server.migrations")
    files = sorted(
        (f for f in migrations_dir.iterdir() if f.name.endswith(".sql")),
        key=lambda f: f.name,
    )
    for f in files:
        version = int(f.name.split("_", 1)[0])
        if version <= current:
            if version not in eingespielt and version > min(eingespielt):
                raise MigrationsLuecke(
                    f"Migration {f.name} wurde nie eingespielt, die Datenbank steht schon auf "
                    f"{current:04d}. Nummern neu vergeben, nicht überspringen.")
            continue
        # Anweisungen einzeln in EINER Transaktion (executescript würde vorher committen).
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _statements(f.read_text(encoding="utf-8")):
                conn.execute(stmt)
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        current = version
    return current


def _statements(sql: str) -> list[str]:
    """Teilt ein Migrationsskript an ';' am Zeilenende.

    Trigger müssen deshalb einzeilig stehen (`CREATE TRIGGER … BEGIN …; END;` in einer Zeile) —
    das innere ';' steht dann nicht am Zeilenende (Test in test_db.py).
    """
    out: list[str] = []
    buf: list[str] = []
    for line in sql.splitlines():
        stripped = line.split("--", 1)[0].rstrip()
        if not stripped:
            continue
        buf.append(stripped)
        if stripped.endswith(";"):
            out.append("\n".join(buf))
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


@contextmanager
def transaktion(conn: sqlite3.Connection) -> Iterator[None]:
    """Schreibtransaktion (BEGIN IMMEDIATE): Änderung und Protokoll-Ereignis gelingen nur gemeinsam.

    IMMEDIATE nimmt die Schreibsperre sofort — Lesen-Prüfen-Schreiben (z. B. Versionsvergleich)
    ist damit gegen parallele Anfragen serialisiert. Nicht verschachteln: SQLite kennt kein
    geschachteltes BEGIN.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
