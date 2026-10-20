"""Nutzungsstatistik: Tageszähler je Projekt, keine Einzelereignisse mit Personenbezug.

Der Viewer meldet gebündelt (höchstens alle 10 s, beim Verlassen/Verbergen mit keepalive).
Serverseitig höchstens MELDUNGEN_JE_FENSTER Meldungen je Nutzer und Projekt in FENSTER_S (Tabelle
throttle; der Schlüssel ist HMAC-SHA-256 mit einem Zufallsschlüssel je Prozess, die Zuordnung
Nutzer/Projekt steht damit nirgends im Klartext und lässt sich auch nicht aus uid+pid zurückrechnen
— anders als ein ungesalzenes sha256(uid:pid) wäre das kein dauerhaft reproduzierbares
Personenmerkmal). Zähler je Meldung 0 bis 1000 (Pydantic in routen_admin). Tage in UTC.
`stat:`-Drosselzeilen verschwinden nach Ablauf ihres FENSTER_S (nicht erst nach 30 Tagen wie
normale Drosselzeilen): `erlaubt()` löscht bei jeder Meldung alle abgelaufenen
`stat:`-Zeilen (Final-Fix C/D — vorher nur `konten.aufraeumen` beim Dienststart), Datensparsamkeit
auch bei der Aufbewahrung.
"""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import time
from datetime import UTC, datetime
from typing import Any

from .db import transaktion

ZAEHLER = ("oeffnen", "standpunkt", "messung", "poi")
FENSTER_S = 60
MELDUNGEN_JE_FENSTER = 12
# Prozessweiter Zufallsschlüssel: bewusst nicht in der DB persistiert — der
# Schlüssel muss nur innerhalb eines FENSTER_S stabil bleiben, danach ist die Zeile ohnehin weg
# (siehe konten.aufraeumen). Ein Neustart des Dienstes setzt laufende Fenster zurück, das ist
# hier hinnehmbar (höchstens eine neue volle Quote statt einer Drosselung).
_SALZ = secrets.token_bytes(32)


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def tag(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).date().isoformat()


def drossel_schluessel(uid: int, pid: str) -> str:
    return "stat:" + hmac.new(_SALZ, f"{uid}:{pid}".encode(), "sha256").hexdigest()[:32]


def erlaubt(conn: sqlite3.Connection, uid: int, pid: str, now: int | None = None) -> bool:
    n = _jetzt(now)
    key = drossel_schluessel(uid, pid)
    with transaktion(conn):
        # Bereich statt LIKE: nutzt den Primärschlüssel-Index (LIKE ist case-insensitiv).
        conn.execute(
            "DELETE FROM throttle WHERE key >= 'stat:' AND key < 'stat;' AND geaendert_am <= ?",
            (n - FENSTER_S,),
        )
        row = conn.execute(
            "SELECT failures, geaendert_am FROM throttle WHERE key=?", (key,),
        ).fetchone()
        if row is None or n - int(row["geaendert_am"]) >= FENSTER_S:
            conn.execute(
                "INSERT INTO throttle (key, failures, locked_until, geaendert_am) VALUES (?,1,0,?)"
                " ON CONFLICT(key) DO UPDATE SET failures=1, geaendert_am=excluded.geaendert_am",
                (key, n),
            )
            return True
        if int(row["failures"]) >= MELDUNGEN_JE_FENSTER:
            return False
        conn.execute("UPDATE throttle SET failures = failures + 1 WHERE key=?", (key,))
    return True


def zaehlen(conn: sqlite3.Connection, pid: str, werte: dict[str, int],
            now: int | None = None) -> None:
    conn.execute(
        "INSERT INTO statistik_tag (project_id, tag, oeffnen, standpunkt, messung, poi)"
        " VALUES (?,?,?,?,?,?) ON CONFLICT(project_id, tag) DO UPDATE SET"
        " oeffnen = oeffnen + excluded.oeffnen, standpunkt = standpunkt + excluded.standpunkt,"
        " messung = messung + excluded.messung, poi = poi + excluded.poi",
        (pid, tag(_jetzt(now)), *(int(werte.get(z, 0)) for z in ZAEHLER)),
    )


def auswertung(
    conn: sqlite3.Connection, projekte: list[tuple[str, str]], tage: int = 30,
    now: int | None = None,
) -> list[dict[str, Any]]:
    n = _jetzt(now)
    tage_liste = [tag(n - i * 86400) for i in range(tage - 1, -1, -1)]
    zeilen = {
        (r["project_id"], r["tag"]): r
        for r in conn.execute("SELECT * FROM statistik_tag WHERE tag >= ?", (tage_liste[0],))
    }
    zuletzt = {
        r["project_id"]: r["t"] for r in conn.execute(
            "SELECT project_id, MAX(tag) AS t FROM statistik_tag WHERE oeffnen > 0"
            " GROUP BY project_id",
        )
    }
    out: list[dict[str, Any]] = []
    for pid, titel in projekte:
        summen = dict.fromkeys(ZAEHLER, 0)
        verlauf: list[dict[str, Any]] = []
        for t in tage_liste:
            r = zeilen.get((pid, t))
            werte = {z: int(r[z]) if r is not None else 0 for z in ZAEHLER}
            for z in ZAEHLER:
                summen[z] += werte[z]
            verlauf.append({"tag": t, **werte})
        out.append({"id": pid, "titel": titel, "summen": summen, "verlauf": verlauf,
                    "zuletztGeoeffnet": zuletzt.get(pid)})
    return out
