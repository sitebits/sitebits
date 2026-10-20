"""Konten, Einladungen, Sitzungen, Rechte — reine DB-Funktionen, Zeit als Parameter testbar."""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from . import lesezugriff, protokoll, statistik
from .tokens import hash_token, new_token

EINLADUNG_TAGE = 7
LEERLAUF_S = 14 * 86400
ABSOLUT_S = 60 * 86400
ROLLEN: tuple[str, ...] = ("betrachter", "rueckmelder", "bearbeiter", "projektleitung")
RolleWert = Literal["betrachter", "rueckmelder", "bearbeiter", "projektleitung"]
# Rangfolge der Projektrollen; "admin" ist das Kontomerkmal, das rolle() als Rolle liefert.
# Liegt hier, weil web.py (Validierungs-Vorrang) zugriff nicht importieren kann.
RANG: dict[str, int] = {
    "betrachter": 1, "rueckmelder": 2, "bearbeiter": 3, "projektleitung": 4, "admin": 5,
}
# Rollen, die eine Projektleitung vergeben und entziehen darf (nie die eigene Stufe).
LEITUNG_VERGIBT: tuple[str, ...] = ("betrachter", "rueckmelder", "bearbeiter")

# WB-N8: höchstens so viele aktive Sitzungen je Konto; eine neue Anmeldung verdrängt die älteste.
MAX_SITZUNGEN = 10


def rang(rolle: str | None) -> int:
    """Rang einer Rolle; None und Unbekanntes haben Rang 0 und dürfen nichts."""
    if rolle is None:
        return 0
    return RANG.get(rolle, 0)


def mindestens(rolle: str | None, schwelle: str) -> bool:
    return rang(rolle) >= RANG[schwelle]


class EinladungUngueltig(Exception):
    """Ihre Nachricht ist nutzersichtbar (Deutsch) — Routen geben `str(e)` an den Client weiter.

    Darf deshalb nie Eingabewerte (Token, E-Mail, ...) enthalten, nur feste, generische Texte.
    """


@dataclass(frozen=True)
class Nutzer:
    id: int
    name: str
    email: str
    is_admin: bool


@dataclass(frozen=True)
class Einladung:
    token_hash: str
    email: str
    name: str
    purpose: str
    grants: dict[str, str]
    is_admin: bool
    project_id: str | None = None
    gewerk_id: int | None = None
    erstellt_von: int | None = None


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _hash_oder_none(token: str) -> str | None:
    """Wie hash_token, aber None statt Crash bei nicht-ASCII-Zeichen (verstümmeltes Cookie)."""
    try:
        return hash_token(token)
    except UnicodeEncodeError:
        return None


def _nutzer(row: sqlite3.Row) -> Nutzer:
    return Nutzer(
        id=row["id"], name=row["name"], email=row["email"], is_admin=bool(row["is_admin"]),
    )


def einladung_anlegen(
    conn: sqlite3.Connection, *, email: str, name: str, grants: dict[str, str],
    is_admin: bool = False, purpose: str = "neu", created_by: int | None = None,
    now: int | None = None, project_id: str | None = None, gewerk_id: int | None = None,
) -> str:
    email, name = email.strip(), name.strip()
    if "@" not in email or not name:
        raise ValueError("E-Mail und Name sind Pflicht.")
    for pid, r in grants.items():
        if r not in ROLLEN:
            raise ValueError(f"Rolle {r!r} für {pid!r} unbekannt (erlaubt: {', '.join(ROLLEN)}).")
    if purpose not in ("neu", "zuruecksetzen"):
        raise ValueError("purpose")
    vorhanden = conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
    if purpose == "zuruecksetzen" and vorhanden is None:
        raise ValueError(f"Kein Konto mit {email} zum Zurücksetzen.")
    if purpose == "neu" and vorhanden is not None:
        raise ValueError(
            f"Konto {email} existiert schon — zum Zurücksetzen --zuruecksetzen nutzen.",
        )
    t = new_token()
    n = _now(now)
    conn.execute(
        "INSERT INTO invites (token_hash, email, name, purpose, grants, is_admin, created_by,"
        " created_at, expires_at, project_id, gewerk_id) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (hash_token(t), email, name, purpose, json.dumps(grants), int(is_admin), created_by, n,
         n + EINLADUNG_TAGE * 86400, project_id, gewerk_id),
    )
    return t


def einladung_pruefen(
    conn: sqlite3.Connection, token: str, now: int | None = None,
) -> Einladung | None:
    h = _hash_oder_none(token)
    if h is None:
        return None
    row = conn.execute(
        "SELECT * FROM invites WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
        (h, _now(now)),
    ).fetchone()
    if row is None:
        return None
    return Einladung(
        token_hash=row["token_hash"], email=row["email"], name=row["name"],
        purpose=row["purpose"], grants=json.loads(row["grants"]), is_admin=bool(row["is_admin"]),
        project_id=row["project_id"], gewerk_id=row["gewerk_id"], erstellt_von=row["created_by"],
    )


def recht_anheben(
    conn: sqlite3.Connection, uid: int, pid: str, rolle: str, *, gewerk_id: int | None,
    von: int | None, kanal: str, now: int, protokollieren: bool = True,
) -> bool:
    """Nur innerhalb einer laufenden Transaktion. Setzt `rolle`, wenn das Konto im Projekt noch
    keine oder eine niedrigere Rolle hat — stuft nie herab —, und `gewerk_id`, wenn es noch keinem
    Gewerk angehört und das Gewerk zu diesem Projekt gehört. Protokolliert `recht.vergeben` bzw.
    `beteiligter.gewerk` mit project_id (außer `protokollieren=False`). True = Rolle geändert."""
    if rolle not in ROLLEN:
        raise ValueError(f"Rolle {rolle!r} unbekannt.")
    row = conn.execute(
        "SELECT role, gewerk_id FROM project_roles WHERE user_id=? AND project_id=?", (uid, pid),
    ).fetchone()
    alt = None if row is None else str(row["role"])
    geaendert = rang(rolle) > rang(alt)
    if geaendert:
        conn.execute(
            "INSERT INTO project_roles (user_id, project_id, role) VALUES (?,?,?)"
            " ON CONFLICT(user_id, project_id) DO UPDATE SET role=excluded.role",
            (uid, pid, rolle),
        )
        if protokollieren:
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="recht.vergeben",
                               project_id=pid, objekt_id=str(uid), vorher=alt, nachher=rolle,
                               now=now)
    ohne_gewerk = row is None or row["gewerk_id"] is None
    if gewerk_id is not None and ohne_gewerk and conn.execute(
        "SELECT 1 FROM gewerke WHERE id=? AND project_id=?", (gewerk_id, pid),
    ).fetchone() is not None:
        conn.execute("UPDATE project_roles SET gewerk_id=? WHERE user_id=? AND project_id=?",
                     (gewerk_id, uid, pid))
        if protokollieren:
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="beteiligter.gewerk",
                               project_id=pid, objekt_id=str(uid), vorher=None, nachher=gewerk_id,
                               now=now)
    return geaendert


def einladung_einloesen(
    conn: sqlite3.Connection, token: str,
    speichern: Callable[[sqlite3.Connection, int], None],
    now: int | None = None,
) -> Nutzer:
    """Löst eine Einladung atomar ein (BEGIN IMMEDIATE) und legt bei Bedarf den Nutzer an.

    `speichern` läuft vor dem COMMIT in derselben Transaktion (z. B. Passkey/Passwort
    speichern) und bekommt die `user_id`. Der Callback darf weder committen noch
    zurückrollen — das übernimmt diese Funktion.
    """
    h = _hash_oder_none(token)
    if h is None:
        raise EinladungUngueltig("Einladung ungültig, abgelaufen oder bereits eingelöst.")
    n = _now(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        e = einladung_pruefen(conn, token, now=n)
        cur = conn.execute(
            "UPDATE invites SET used_at=? WHERE token_hash=? AND used_at IS NULL AND expires_at>?",
            (n, h, n),
        )
        if e is None or cur.rowcount != 1:
            raise EinladungUngueltig("Einladung ungültig, abgelaufen oder bereits eingelöst.")
        uid: int | None
        if e.purpose == "neu":
            # COLLATE NOCASE auf users.email greift auch hier: eine zweite offene
            # "neu"-Einladung für dieselbe (auch anders geschriebene) E-Mail-Adresse
            # würde sonst am UNIQUE-Constraint mit sqlite3.IntegrityError scheitern.
            if conn.execute("SELECT id FROM users WHERE email=?", (e.email,)).fetchone():
                raise EinladungUngueltig(
                    "Für diese E-Mail gibt es schon ein Konto — bitte normal anmelden.",
                )
            uid = conn.execute(
                "INSERT INTO users (name, email, is_admin, created_at) VALUES (?,?,?,?)",
                (e.name, e.email, int(e.is_admin), n),
            ).lastrowid
        else:
            besteht = conn.execute(
                "SELECT id FROM users WHERE email=?", (e.email,),
            ).fetchone()
            if besteht is None:
                raise EinladungUngueltig("Konto wurde zwischenzeitlich gelöscht.")
            uid = besteht["id"]
            for tab in ("sessions", "passkeys", "passwords"):
                conn.execute(f"DELETE FROM {tab} WHERE user_id=?", (uid,))
            # Auch API-Tokens widerrufen (kompromittiertes Konto darf nach
            # dem Zurücksetzen keine alten Tokens behalten). Inline statt
            # apitoken.alle_widerrufen(...): apitoken importiert konten (Nutzer/_nutzer), ein
            # Import hier schlösse den Kreis.
            conn.execute(
                "UPDATE api_tokens SET widerrufen_am=? WHERE user_id=? AND widerrufen_am IS NULL",
                (n, uid),
            )
        assert uid is not None
        if e.project_id is not None:
            # Projektgebunden: nur das Recht dieses Projekts, auch wenn grants mehr nennt.
            r = e.grants.get(e.project_id)
            if r in ROLLEN:
                recht_anheben(conn, uid, e.project_id, r, gewerk_id=e.gewerk_id,
                              von=e.erstellt_von, kanal="web", now=n)
        else:
            for pid, r in e.grants.items():
                if r in ROLLEN:
                    recht_anheben(conn, uid, pid, r, gewerk_id=None, von=e.erstellt_von,
                                  kanal="web", now=n, protokollieren=False)
        protokoll.ereignis(
            conn, kanal="web", nutzer_id=uid,
            art="konto.angelegt" if e.purpose == "neu" else "konto.zurueckgesetzt",
            objekt_id=str(uid),
            nachher={"admin": e.is_admin, "einladung": h[:16], "rechte": e.grants}, now=n,
        )
        speichern(conn, uid)
        row = conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise EinladungUngueltig("Konto wurde zwischenzeitlich gelöscht.")
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return _nutzer(row)


def sitzung_anlegen(conn: sqlite3.Connection, user_id: int, now: int | None = None) -> str:
    t = new_token()
    n = _now(now)
    conn.execute(
        "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at)"
        " VALUES (?,?,?,?,?)",
        (hash_token(t), user_id, n, n, n + ABSOLUT_S),
    )
    # Älteste Sitzungen über MAX_SITZUNGEN hinaus verwerfen (WB-N8): verwaiste, noch gültige
    # Tokens (alte Browser, Backups) sammeln sich so nicht unbegrenzt an.
    conn.execute(
        "DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN ("
        " SELECT token_hash FROM sessions WHERE user_id=?"
        " ORDER BY created_at DESC, rowid DESC LIMIT ?)",
        (user_id, user_id, MAX_SITZUNGEN),
    )
    conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (n, user_id))
    return t


def sitzung_nutzer(conn: sqlite3.Connection, token: str, now: int | None = None) -> Nutzer | None:
    n = _now(now)
    h = _hash_oder_none(token)
    if h is None:
        return None
    row = conn.execute(
        "SELECT u.*, s.last_seen_at AS seen, s.expires_at AS exp FROM sessions s"
        " JOIN users u ON u.id = s.user_id WHERE s.token_hash=?",
        (h,),
    ).fetchone()
    if row is None or row["disabled"] or n > row["exp"] or n - row["seen"] > LEERLAUF_S:
        return None
    if n - row["seen"] >= 60:
        conn.execute("UPDATE sessions SET last_seen_at=? WHERE token_hash=?", (n, h))
    return _nutzer(row)


def sitzung_beenden(conn: sqlite3.Connection, token: str) -> None:
    h = _hash_oder_none(token)
    if h is not None:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (h,))


def rolle(conn: sqlite3.Connection, user: Nutzer, project_id: str) -> str | None:
    if user.is_admin:
        return "admin"
    row = conn.execute(
        "SELECT role FROM project_roles WHERE user_id=? AND project_id=?", (user.id, project_id),
    ).fetchone()
    return None if row is None else str(row["role"])


def sitzung_alter(conn: sqlite3.Connection, token: str, now: int | None = None) -> int | None:
    """Sekunden seit dem Anlegen der Sitzung (sessions.created_at), None ohne passende Zeile.

    Für Schritte, die eine frische Anmeldung verlangen (weiteren Passkey hinzufügen): ein
    liegengelassenes, noch gültiges Cookie allein soll dafür nicht reichen.
    """
    h = _hash_oder_none(token)
    if h is None:
        return None
    row = conn.execute("SELECT created_at FROM sessions WHERE token_hash=?", (h,)).fetchone()
    return None if row is None else _now(now) - int(row["created_at"])


# --- Aufräumen beim Start -------------------------------------------------------------------

EINLADUNG_AUFBEWAHREN_S = 30 * 86400
DROSSEL_AUFBEWAHREN_S = 86400
# WB-N2/BB-I6: Drosselzeilen mit offenen Fehlversuchen (1 bis 4), seit so langer Zeit unverändert.
DROSSEL_OFFEN_AUFBEWAHREN_S = 30 * 86400


def aufraeumen(
    conn: sqlite3.Connection, now: int | None = None, *, lesezugriffe_tage: int,
) -> dict[str, int]:
    """Löscht veraltete Zeilen (beim Dienststart aus `create_app`), gibt die Anzahl je Tabelle
    zurück.

    - Sitzungen: absolut abgelaufen oder länger als LEERLAUF_S unbenutzt (wären ohnehin ungültig).
    - Einladungen: nie eingelöst und seit mehr als 30 Tagen abgelaufen (eingelöste bleiben als
      Nachweis stehen).
    - Challenges: abgelaufen.
    - Drosselung: Zeilen ohne offene Fehlversuche, deren Sperre seit über einem Tag vorbei ist,
      und Zeilen ohne aktive Sperre, die seit über 30 Tagen unverändert sind (auch mit 1 bis 4
      offenen Fehlversuchen; Spalte `geaendert_am` seit Migration 0002).
    - Statistik-Drosselzeilen (Präfix `stat:`): schon sobald ihr Fenster
      (`statistik.FENSTER_S`) vorbei ist — nicht erst nach `DROSSEL_OFFEN_AUFBEWAHREN_S` wie
      normale Drosselzeilen (Datensparsamkeit: der Schlüssel ist zwar gesalzen, soll
      aber nicht unnötig lange bestehen bleiben).
    - Lesezugriffsprotokoll: Einträge älter als `lesezugriffe_tage` (bei 0 alle, das Protokoll ist
      dann aus).
    """
    n = _now(now)
    out: dict[str, int] = {}
    out["sessions"] = conn.execute(
        "DELETE FROM sessions WHERE expires_at < ? OR last_seen_at < ?", (n, n - LEERLAUF_S),
    ).rowcount
    out["invites"] = conn.execute(
        "DELETE FROM invites WHERE used_at IS NULL AND expires_at < ?",
        (n - EINLADUNG_AUFBEWAHREN_S,),
    ).rowcount
    out["challenges"] = conn.execute(
        "DELETE FROM challenges WHERE expires_at < ?", (n,),
    ).rowcount
    out["throttle_statistik"] = conn.execute(
        "DELETE FROM throttle WHERE key LIKE 'stat:%' AND geaendert_am < ?",
        (n - statistik.FENSTER_S,),
    ).rowcount
    out["throttle"] = conn.execute(
        "DELETE FROM throttle WHERE (failures = 0 AND locked_until < ?)"
        " OR (locked_until <= ? AND geaendert_am < ?)",
        (n - DROSSEL_AUFBEWAHREN_S, n, n - DROSSEL_OFFEN_AUFBEWAHREN_S),
    ).rowcount
    out["lesezugriffe"] = lesezugriff.aufraeumen(conn, lesezugriffe_tage, now=n)
    return out


# --- Zugänge verwalten (CLI) ----------------------------------------------------------------

def konto_id(conn: sqlite3.Connection, email: str) -> int | None:
    row = conn.execute("SELECT id FROM users WHERE email=?", (email.strip(),)).fetchone()
    return None if row is None else int(row["id"])


def einladung_bekannt(conn: sqlite3.Connection, email: str) -> bool:
    """Gibt es (in irgendeinem Zustand) eine Einladung für diese E-Mail?"""
    return conn.execute(
        "SELECT 1 FROM invites WHERE email=? LIMIT 1", (email.strip(),),
    ).fetchone() is not None


def ist_admin(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT is_admin FROM users WHERE id=?", (user_id,)).fetchone()
    return row is not None and bool(row["is_admin"])
