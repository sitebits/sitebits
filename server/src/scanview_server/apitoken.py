"""Persönliche API-Tokens: nur der SHA-256 des Tokens liegt in der DB.

Reine DB-Funktionen wie konten.py — Zeit als Parameter testbar. `erzeugen` und `widerrufen` sind
für sich abgeschlossene Aktionen (nicht Baustein einer größeren Transaktion wie in verwaltung.py)
und protokollieren deshalb selbst, in eigener Transaktion (db.transaktion). `alle_widerrufen`
schreibt bewusst kein eigenes Ereignis: sperren/abmelden_ueberall/anonymisieren (verwaltung.py)
rufen es innerhalb ihrer eigenen Transaktion auf (SQLite verschachtelt kein BEGIN) und
protokollieren dort selbst.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass

from . import konten, protokoll
from .db import transaktion

UMFAENGE = ("lesen", "schreiben")
MAX_GUELTIG = 10
PRAEFIX = "sv_"
_GUELTIG_TAGE_MIN, _GUELTIG_TAGE_MAX = 1, 365
_NAME_MAX = 60
# Befund B-TE-N2: nicht nur ASCII-Steuerzeichen, sondern alle Unicode-Steuer- und
# Formatzeichen (C0/C1, Bidi-Overrides/-Isolates, Nullbreiten) und Zeilen-/Absatztrenner — sonst
# ließen sich Namen in Portal, Verlauf und Terminal optisch verfälschen („Trojan Source“).
_VERBOTENE_KATEGORIEN = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _hat_steuerzeichen(text: str) -> bool:
    return any(unicodedata.category(z) in _VERBOTENE_KATEGORIEN for z in text)


@dataclass(frozen=True)
class ApiToken:
    id: int
    nutzer: konten.Nutzer
    name: str
    umfang: str
    # G7: Token „für einen KI-Agenten“ — was darüber geschrieben wird, ist ein Vorschlag.
    agent: bool = False


@dataclass(frozen=True)
class TokenInfo:
    id: int
    name: str
    umfang: str
    erstellt_am: int
    laeuft_ab: int
    zuletzt_benutzt: int | None
    agent: bool = False


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _hash(roh: str) -> str | None:
    """None statt Crash bei kaputten/übergroßen Werten (z. B. verstümmelter Bearer-Header)."""
    if not roh.startswith(PRAEFIX) or len(roh) > 200:
        return None
    return hashlib.sha256(roh.encode()).hexdigest()


def _info(row: sqlite3.Row) -> TokenInfo:
    return TokenInfo(
        id=row["id"], name=row["name"], umfang=row["umfang"], erstellt_am=row["erstellt_am"],
        laeuft_ab=row["laeuft_ab"], zuletzt_benutzt=row["zuletzt_benutzt"],
        agent=bool(row["agent"]),
    )


def erzeugen(
    conn: sqlite3.Connection, nutzer_id: int, name: str, umfang: str, gueltig_tage: int = 90,
    *, agent: bool = False, now: int | None = None,
) -> tuple[str, TokenInfo]:
    """Legt einen neuen Token an, gibt (Rohwert — nur hier lesbar, nie wieder —, Info) zurück."""
    name = name.strip()
    if not 1 <= len(name) <= _NAME_MAX or _hat_steuerzeichen(name):
        raise ValueError(f"Name muss 1 bis {_NAME_MAX} Zeichen ohne Steuerzeichen haben.")
    if umfang not in UMFAENGE:
        raise ValueError(f"Umfang {umfang!r} unbekannt (erlaubt: {', '.join(UMFAENGE)}).")
    if not _GUELTIG_TAGE_MIN <= gueltig_tage <= _GUELTIG_TAGE_MAX:
        raise ValueError(
            f"Gültigkeit muss {_GUELTIG_TAGE_MIN} bis {_GUELTIG_TAGE_MAX} Tage betragen.",
        )
    n = _jetzt(now)
    laeuft_ab = n + gueltig_tage * 86400
    roh = PRAEFIX + secrets.token_urlsafe(32)
    h = hashlib.sha256(roh.encode()).hexdigest()
    with transaktion(conn):
        anzahl = conn.execute(
            "SELECT COUNT(*) FROM api_tokens WHERE user_id=? AND widerrufen_am IS NULL"
            " AND laeuft_ab > ?", (nutzer_id, n),
        ).fetchone()[0]
        if anzahl >= MAX_GUELTIG:
            raise ValueError(f"Höchstens {MAX_GUELTIG} gültige Tokens je Konto.")
        cur = conn.execute(
            "INSERT INTO api_tokens (user_id, name, token_hash, umfang, erstellt_am, laeuft_ab,"
            " agent) VALUES (?,?,?,?,?,?,?)",
            (nutzer_id, name, h, umfang, n, laeuft_ab, int(agent)),
        )
        if cur.lastrowid is None:
            raise RuntimeError("Token ohne ID")
        tid = int(cur.lastrowid)
        protokoll.ereignis(
            conn, kanal="web", art="token.erstellt", nutzer_id=nutzer_id, objekt_id=str(tid),
            nachher={"name": name, "umfang": umfang, "laeuft_ab": laeuft_ab, "agent": agent},
            now=n,
        )
    return roh, TokenInfo(
        id=tid, name=name, umfang=umfang, erstellt_am=n, laeuft_ab=laeuft_ab,
        zuletzt_benutzt=None, agent=agent,
    )


def pruefen(conn: sqlite3.Connection, roh: str, *, now: int | None = None) -> ApiToken | None:
    """None bei unbekanntem, widerrufenem, abgelaufenem Token oder gesperrtem Konto.

    Aktualisiert `zuletzt_benutzt` höchstens alle 60 s (wie sessions.last_seen_at) — jede Prüfung
    einzeln zu schreiben wäre bei API-Verkehr unnötig viel Schreiblast.
    """
    h = _hash(roh)
    if h is None:
        return None
    n = _jetzt(now)
    row = conn.execute(
        "SELECT t.id AS tid, t.name AS tname, t.umfang, t.laeuft_ab, t.zuletzt_benutzt,"
        " t.widerrufen_am, t.agent AS tagent, u.*"
        " FROM api_tokens t JOIN users u ON u.id = t.user_id WHERE t.token_hash=?", (h,),
    ).fetchone()
    if row is None or row["widerrufen_am"] is not None or n >= row["laeuft_ab"] or row["disabled"]:
        return None
    if row["zuletzt_benutzt"] is None or n - row["zuletzt_benutzt"] >= 60:
        conn.execute("UPDATE api_tokens SET zuletzt_benutzt=? WHERE id=?", (n, row["tid"]))
    return ApiToken(id=row["tid"], nutzer=konten._nutzer(row), name=row["tname"],
                     umfang=row["umfang"], agent=bool(row["tagent"]))


def liste(conn: sqlite3.Connection, nutzer_id: int, *, now: int | None = None) -> list[TokenInfo]:
    """Nur (noch) gültige Tokens (nicht widerrufen, nicht abgelaufen), neueste zuerst."""
    n = _jetzt(now)
    rows = conn.execute(
        "SELECT id, name, umfang, erstellt_am, laeuft_ab, zuletzt_benutzt, agent FROM api_tokens"
        " WHERE user_id=? AND widerrufen_am IS NULL AND laeuft_ab > ?"
        " ORDER BY erstellt_am DESC, id DESC", (nutzer_id, n),
    ).fetchall()
    return [_info(r) for r in rows]


def widerrufen(
    conn: sqlite3.Connection, nutzer_id: int, tid: int, *, von: int, kanal: str,
) -> bool:
    """Widerruft genau einen (eigenen, noch nicht widerrufenen) Token; fremde/unbekannte ID/schon
    widerrufener Token → False, ohne zweites Ereignis (wie verwaltung.einladung_widerrufen)."""
    n = int(time.time())
    with transaktion(conn):
        row = conn.execute(
            "SELECT name, umfang, laeuft_ab FROM api_tokens WHERE id=? AND user_id=?"
            " AND widerrufen_am IS NULL", (tid, nutzer_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE api_tokens SET widerrufen_am=? WHERE id=?", (n, tid))
        protokoll.ereignis(
            conn, kanal=kanal, art="token.widerrufen", nutzer_id=von, objekt_id=str(tid),
            nachher={"name": row["name"], "umfang": row["umfang"], "laeuft_ab": row["laeuft_ab"]},
            now=n,
        )
    return True


def alle_widerrufen(conn: sqlite3.Connection, nutzer_id: int, *, now: int | None = None) -> int:
    """Widerruft alle noch gültigen (nicht bereits abgelaufenen) Tokens des Kontos, ohne eigenes
    Ereignis — läuft innerhalb der Transaktion des Aufrufers (sperren/abmelden_ueberall
    protokollieren dort selbst mit). Bereits abgelaufene Tokens werden nicht mitgezählt oder
    markiert — sie sind über `pruefen`/`liste` ohnehin schon unbrauchbar; der Rückgabewert
    (`tokensWiderrufen`) soll „waren gültig und sind es jetzt nicht mehr“ bedeuten."""
    n = _jetzt(now)
    return conn.execute(
        "UPDATE api_tokens SET widerrufen_am=? WHERE user_id=? AND widerrufen_am IS NULL"
        " AND laeuft_ab > ?",
        (n, nutzer_id, n),
    ).rowcount
