"""Auftragstabelle `jobs.db`: die einzige Verbindung zwischen Webdienst und Import-Worker.

Der Webdienst reiht Aufträge ein, liest ihren Stand und fordert das Löschen an; er startet nie
einen Prozess (R-E-F-1). Der Worker (`scanview_worker`) holt Aufträge in FIFO-Reihenfolge ab,
meldet seinen Herzschlag und schreibt Status und Urteil zurück. Beide öffnen dieselbe eigene
SQLite-Datei — nie `scanview.db` (der Worker hat dort keine Rechte).

Dieses Modul nutzt nur die Standardbibliothek (sqlite3, json, re): der Worker importiert es, und
die Importgrenze (kein Server-DB-, Sitzungs- oder Kontenmodul im Worker) prüft ein Test.

Alles, was der Worker zurückschreibt, ist für den Webdienst fremde Eingabe (der Worker liest
fremde Dateien): Urteil und Zählung werden beim Lesen erneut geprüft.
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Feste Aufzählung der Auftragstypen. Verfügbar sind das Prüfen und neue Projekte aus Mesh oder
# Splats; die übrigen sind vorgesehen und werden mit „nicht verfügbar“ abgelehnt.
TYPEN: tuple[str, ...] = (
    "auswerten_pruefen",
    "auswerten_sonne_dach",
    "auswerten_sonne_garten",
    "pvgis_abruf",
    "neu_wolke",
    "neu_mesh",
    "neu_splats",
    "ergaenzen_mesh",
    "ergaenzen_splats",
    "ergaenzen_fotos_kameras",
    "ergaenzen_fotos_colmap",
    "ergaenzen_fotos_odm",
    "ergaenzen_panos",
)
VERFUEGBAR: frozenset[str] = frozenset({"auswerten_pruefen", "neu_mesh", "neu_splats"})
# Aufträge, die ein neues Projekt aus einem Upload bauen (Route /api/v1/importe, nur Admin);
# alle übrigen hängen an einem vorhandenen Projekt (Route …/auswertungen).
IMPORTE: frozenset[str] = frozenset({"neu_mesh", "neu_splats"})
AUSWERTUNGEN: frozenset[str] = frozenset(t for t in TYPEN if t.startswith("auswerten_"))

# Mitgelieferte Prüfprofile = Dateien in import/src/scanview_import/pruefen/profile/*.toml
# (Vertragstest test_auftraege.py). Nur diese Namen gehen als `--profil` an den Prüfer.
PROFILE: tuple[str, ...] = ("bestandserfassung",)
PROFIL_STANDARD = "bestandserfassung"

WARTEND, LAUFEND, FERTIG, FEHLGESCHLAGEN, ABGEBROCHEN = (
    "wartend", "laufend", "fertig", "fehlgeschlagen", "abgebrochen",
)
STATUS: tuple[str, ...] = (WARTEND, LAUFEND, FERTIG, FEHLGESCHLAGEN, ABGEBROCHEN)
OFFEN: tuple[str, ...] = (WARTEND, LAUFEND)

# Höchstens so viele offene (wartende oder laufende) Aufträge je Projekt — eine Schleife eines
# Agenten soll die Warteschlange der Instanz nicht füllen können.
OFFEN_JE_PROJEKT_MAX = 3

AUFTRAG_ID_RE = re.compile(r"^[0-9a-f]{16}$")
# Wie katalog.PROJECT_ID_RE (hier dupliziert: der Worker importiert katalog nicht).
PROJEKT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
# Wie uploads.UPLOAD_ID_RE und die Pfadregeln dort (ebenfalls dupliziert, siehe oben).
UPLOAD_ID_RE = re.compile(r"[0-9a-f]{32}")
_SEGMENT_RE = re.compile(r"[A-Za-z0-9._ -]{1,200}")
_CRS_RE = re.compile(r"EPSG:[0-9]{4,6}")
HOEHENSYSTEME: tuple[str, ...] = ("dhhn2016", "ellipsoidal-etrs89", "dhhn92")
# Endungen der Hauptdatei je Importtyp (Mesh nur als GLB/PLY: ohne externe Verweise, ohne
# Texturkodierung im Container).
IMPORT_ENDUNGEN: dict[str, tuple[str, ...]] = {
    "neu_mesh": (".glb", ".ply"),
    "neu_splats": (".ply", ".spz", ".splat"),
}
TITEL_MAX = 120
OFFEN_IMPORTE_MAX = 3


@dataclass(frozen=True)
class Budget:
    """Grenzen je Auftragstyp (RAM, Zeit)."""

    ram_mb: int
    zeit_s: int
    prozesse: int


BUDGETS: dict[str, Budget] = {
    # gemessen 0,9 GB auf dem großen Pilotpaket → 2 GB; Laufzeit dort 2 bis 3 min.
    "auswerten_pruefen": Budget(ram_mb=2048, zeit_s=3600, prozesse=64),
    # Mesh bis 1 Mio. Dreiecke bzw. Splats bis 1 Mio. Gaussians: Python-Leser und meshopt im
    # Speicher; gemessen an synthetischen Eingaben, Grenze mit Reserve.
    "neu_mesh": Budget(ram_mb=4096, zeit_s=3 * 3600, prozesse=64),
    "neu_splats": Budget(ram_mb=4096, zeit_s=3 * 3600, prozesse=64),
}

# Stufen eines laufenden Imports (der Worker setzt sie; beim Lesen gegen diese Liste geprüft).
STUFEN: tuple[str, ...] = ("konvertiert", "prueft", "freischalten")

# Herzschlag: der Worker schreibt alle HERZSCHLAG_S Sekunden; älter als STILL_S heißt „still“.
HERZSCHLAG_S = 10
STILL_S = 60

SCHEMA = (
    "CREATE TABLE IF NOT EXISTS auftraege ("
    " nr INTEGER PRIMARY KEY AUTOINCREMENT,"
    " id TEXT NOT NULL UNIQUE,"
    " projekt TEXT NOT NULL,"
    " typ TEXT NOT NULL,"
    " optionen TEXT NOT NULL,"
    " status TEXT NOT NULL CHECK (status IN"
    " ('wartend','laufend','fertig','fehlgeschlagen','abgebrochen')),"
    " erstellt_am INTEGER NOT NULL,"
    " erstellt_von INTEGER,"
    " kanal TEXT NOT NULL,"
    " begonnen_am INTEGER,"
    " beendet_am INTEGER,"
    " urteil TEXT,"
    " zaehlung TEXT,"
    " fehler TEXT,"
    " loeschen INTEGER NOT NULL DEFAULT 0,"
    " abbrechen INTEGER NOT NULL DEFAULT 0,"
    " stufe TEXT)",
    "CREATE INDEX IF NOT EXISTS auftraege_projekt ON auftraege (projekt, nr)",
    "CREATE INDEX IF NOT EXISTS auftraege_status ON auftraege (status, nr)",
    "CREATE TABLE IF NOT EXISTS worker ("
    " id INTEGER PRIMARY KEY CHECK (id = 1),"
    " herzschlag INTEGER NOT NULL,"
    " pid INTEGER,"
    " gestartet_am INTEGER)",
)


class AuftragUngueltig(ValueError):
    """Typ oder Optionen ungültig; der Text geht als 422 an den Aufrufer."""


class ZuViele(Exception):
    """Zu viele offene Aufträge im Projekt."""


def verbinden(pfad: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(pfad, isolation_level=None, check_same_thread=False, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def schema(conn: sqlite3.Connection) -> None:
    for stmt in SCHEMA:
        conn.execute(stmt)
    # Spalte für den Abbruchwunsch nachrüsten (jobs.db aus der Zeit davor).
    spalten = {r[1] for r in conn.execute("PRAGMA table_info(auftraege)")}
    if "abbrechen" not in spalten:
        conn.execute("ALTER TABLE auftraege ADD COLUMN abbrechen INTEGER NOT NULL DEFAULT 0")
    if "stufe" not in spalten:
        conn.execute("ALTER TABLE auftraege ADD COLUMN stufe TEXT")


def pruefe(typ: Any, optionen: Any, *, erlaubt: frozenset[str] = AUSWERTUNGEN,
           ) -> dict[str, str]:
    """Typ und Optionen strikt prüfen; liefert die normalisierten Optionen.

    Nur feste Werte gelangen später in die Argumentliste des Prüfers — nie ein Pfad oder
    freier Text aus der Anfrage. `erlaubt` trennt die Routen: Auswertungen nehmen keine
    Importtypen an und umgekehrt."""
    sichtbar = sorted(VERFUEGBAR & erlaubt)
    if not isinstance(typ, str) or typ not in TYPEN or (typ in VERFUEGBAR and typ not in erlaubt):
        raise AuftragUngueltig(f"Unbekannter Auftragstyp. Erlaubt: {', '.join(sichtbar)}.")
    if typ not in VERFUEGBAR:
        raise AuftragUngueltig(f"Auftragstyp {typ} ist auf dieser Instanz noch nicht verfügbar.")
    if optionen is None:
        optionen = {}
    if not isinstance(optionen, dict):
        raise AuftragUngueltig("optionen muss ein Objekt sein.")
    if typ in IMPORTE:
        return _pruefe_import(typ, optionen)
    unbekannt = set(optionen) - {"profil"}
    if unbekannt:
        raise AuftragUngueltig("Unbekannte Option. Erlaubt für auswerten_pruefen: profil.")
    profil = optionen.get("profil", PROFIL_STANDARD)
    if not isinstance(profil, str) or profil not in PROFILE:
        raise AuftragUngueltig(f"Unbekanntes Profil. Erlaubt: {', '.join(PROFILE)}.")
    return {"profil": profil}


def _text(wert: Any, name: str, grenze: int) -> str:
    if not isinstance(wert, str):
        raise AuftragUngueltig(f"{name} muss ein Text sein.")
    t = wert.strip()
    if not 0 < len(t) <= grenze or not t.isprintable():
        raise AuftragUngueltig(f"{name}: 1 bis {grenze} druckbare Zeichen.")
    return t


def pfad_ok(pfad: str) -> bool:
    teile = pfad.split("/")
    return 0 < len(teile) <= 6 and all(
        t not in (".", "..") and _SEGMENT_RE.fullmatch(t) is not None for t in teile)


def _pruefe_import(typ: str, optionen: dict[str, Any]) -> dict[str, str]:
    """Optionen eines neuen Projekts: Upload, Hauptdatei, Projekt-ID, Titel, optional CRS und
    Höhensysteme. Die Hauptdatei bestimmt der Webdienst aus dem abgeschlossenen Upload."""
    erlaubt = {"upload", "datei", "projekt", "titel", "crs", "quellHoehe", "zielHoehe"}
    unbekannt = set(optionen) - erlaubt
    if unbekannt:
        raise AuftragUngueltig(f"Unbekannte Option. Erlaubt für {typ}: "
                               f"{', '.join(sorted(erlaubt - {'datei'}))}.")
    upload = optionen.get("upload")
    if not isinstance(upload, str) or not UPLOAD_ID_RE.fullmatch(upload):
        raise AuftragUngueltig("upload: ID eines abgeschlossenen Uploads fehlt.")
    projekt = optionen.get("projekt")
    if not isinstance(projekt, str) or not PROJEKT_ID_RE.fullmatch(projekt):
        raise AuftragUngueltig("projekt: Kleinbuchstaben, Ziffern und Bindestrich, 1 bis 64 "
                               "Zeichen, am Anfang kein Bindestrich.")
    aus: dict[str, str] = {"upload": upload, "projekt": projekt,
                           "titel": _text(optionen.get("titel", projekt), "titel", TITEL_MAX)}
    datei = optionen.get("datei")
    if datei is not None:
        if not isinstance(datei, str) or not pfad_ok(datei) or not datei.lower().endswith(
                IMPORT_ENDUNGEN[typ]):
            raise AuftragUngueltig("datei ungültig.")
        aus["datei"] = datei
    crs = optionen.get("crs") or ""
    if crs:
        if not isinstance(crs, str) or not _CRS_RE.fullmatch(crs):
            raise AuftragUngueltig("crs: leer (lokal, Modellmaß) oder EPSG:<Nummer>.")
        aus["crs"] = crs
    quelle, ziel = optionen.get("quellHoehe") or "", optionen.get("zielHoehe") or ""
    if quelle or ziel:
        if not crs:
            raise AuftragUngueltig("Höhensysteme nur zusammen mit einem Koordinatensystem.")
        if quelle not in HOEHENSYSTEME or (ziel and ziel not in HOEHENSYSTEME):
            raise AuftragUngueltig(f"Höhensystem: eines von {', '.join(HOEHENSYSTEME)}.")
        aus["quellHoehe"] = quelle
        if ziel:
            aus["zielHoehe"] = ziel
    return aus


def einreihen(
    conn: sqlite3.Connection, *, projekt: str, typ: str, optionen: dict[str, str],
    nutzer_id: int | None, kanal: str, now: int | None = None,
) -> str:
    if not PROJEKT_ID_RE.fullmatch(projekt):
        raise AuftragUngueltig("Projekt-ID ungültig.")
    aid = secrets.token_hex(8)
    jetzt = int(time.time()) if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        offen = conn.execute(
            "SELECT COUNT(*) FROM auftraege WHERE projekt=? AND loeschen=0 AND status IN (?,?)",
            (projekt, *OFFEN),
        ).fetchone()[0]
        if offen >= (1 if typ in IMPORTE else OFFEN_JE_PROJEKT_MAX):
            raise ZuViele
        if typ in IMPORTE:
            alle = conn.execute(
                "SELECT COUNT(*) FROM auftraege WHERE loeschen=0 AND status IN (?,?)"
                " AND typ IN (SELECT value FROM json_each(?))",
                (*OFFEN, _typen(IMPORTE)),
            ).fetchone()[0]
            if alle >= OFFEN_IMPORTE_MAX:
                raise ZuViele
        conn.execute(
            "INSERT INTO auftraege (id, projekt, typ, optionen, status, erstellt_am,"
            " erstellt_von, kanal) VALUES (?,?,?,?,?,?,?,?)",
            (aid, projekt, typ, json.dumps(optionen, sort_keys=True), WARTEND, jetzt,
             nutzer_id, kanal),
        )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return aid


@dataclass(frozen=True)
class Auftrag:
    nr: int
    id: str
    projekt: str
    typ: str
    optionen: dict[str, str]
    status: str
    erstellt_am: int
    erstellt_von: int | None
    kanal: str
    begonnen_am: int | None
    beendet_am: int | None
    urteil: str | None
    zaehlung: dict[str, int] | None
    fehler: str | None
    stufe: str | None = None


def _urteil(roh: Any) -> str | None:
    if isinstance(roh, str) and 0 < len(roh) <= 80 and roh.isprintable():
        return roh
    return None


def _zaehlung(roh: Any) -> dict[str, int] | None:
    try:
        d = json.loads(roh) if isinstance(roh, str) else None
    except ValueError:
        return None
    if (not isinstance(d, dict) or len(d) > 10
            or not all(isinstance(k, str) and len(k) <= 40 and isinstance(v, int)
                       and not isinstance(v, bool) and 0 <= v < 10**9 for k, v in d.items())):
        return None
    return d


def _optionen(roh: str) -> dict[str, str]:
    try:
        d = json.loads(roh)
    except ValueError:
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str)} \
        if isinstance(d, dict) else {}


def _auftrag(r: sqlite3.Row) -> Auftrag:
    return Auftrag(
        nr=int(r["nr"]), id=r["id"], projekt=r["projekt"], typ=r["typ"],
        optionen=_optionen(r["optionen"]), status=r["status"],
        erstellt_am=int(r["erstellt_am"]), erstellt_von=r["erstellt_von"], kanal=r["kanal"],
        begonnen_am=r["begonnen_am"], beendet_am=r["beendet_am"], urteil=_urteil(r["urteil"]),
        zaehlung=_zaehlung(r["zaehlung"]),
        fehler=r["fehler"] if isinstance(r["fehler"], str) else None,
        stufe=r["stufe"] if r["stufe"] in STUFEN else None,
    )


def _typen(typen: frozenset[str]) -> str:
    """Typenmenge als JSON-Liste für `typ IN (SELECT value FROM json_each(?))` — das SQL bleibt
    ein fester Text, die Typen sind nur Parameter."""
    return json.dumps(sorted(typen))


def lesen(conn: sqlite3.Connection, projekt: str, aid: str, *,
          typen: frozenset[str] = AUSWERTUNGEN) -> Auftrag | None:
    """Ein Auftrag des Projekts; zum Löschen markierte gelten als nicht vorhanden."""
    if not AUFTRAG_ID_RE.fullmatch(aid):
        return None
    r = conn.execute(
        "SELECT * FROM auftraege WHERE id=? AND projekt=? AND loeschen=0"
        " AND typ IN (SELECT value FROM json_each(?))",
        (aid, projekt, _typen(typen)),
    ).fetchone()
    return _auftrag(r) if r is not None else None


def liste(conn: sqlite3.Connection, projekt: str, *,
          typen: frozenset[str] = AUSWERTUNGEN) -> list[Auftrag]:
    rows = conn.execute(
        "SELECT * FROM auftraege WHERE projekt=? AND loeschen=0"
        " AND typ IN (SELECT value FROM json_each(?)) ORDER BY nr DESC LIMIT 200",
        (projekt, _typen(typen)),
    ).fetchall()
    return [_auftrag(r) for r in rows]


def importe(conn: sqlite3.Connection) -> list[Auftrag]:
    """Alle Importaufträge der Instanz (neueste zuerst)."""
    rows = conn.execute(
        "SELECT * FROM auftraege WHERE loeschen=0 AND typ IN (SELECT value FROM json_each(?))"
        " ORDER BY nr DESC LIMIT 200", (_typen(IMPORTE),),
    ).fetchall()
    return [_auftrag(r) for r in rows]


def import_lesen(conn: sqlite3.Connection, aid: str) -> Auftrag | None:
    if not AUFTRAG_ID_RE.fullmatch(aid):
        return None
    r = conn.execute("SELECT * FROM auftraege WHERE id=? AND loeschen=0"
                     " AND typ IN (SELECT value FROM json_each(?))",
                     (aid, _typen(IMPORTE))).fetchone()
    return _auftrag(r) if r is not None else None


def uploads_in_arbeit(conn: sqlite3.Connection) -> set[str]:
    """Uploads, auf die ein wartender oder laufender Auftrag zugreift (nicht aufräumen)."""
    rows = conn.execute("SELECT optionen FROM auftraege WHERE status IN (?,?)", OFFEN)
    return {o["upload"] for o in (_optionen(r[0]) for r in rows) if "upload" in o}


def projekt_in_arbeit(conn: sqlite3.Connection, projekt: str) -> bool:
    """Baut schon ein offener Auftrag dieses neue Projekt?"""
    return conn.execute(
        "SELECT 1 FROM auftraege WHERE projekt=? AND loeschen=0 AND status IN (?,?)"
        " AND typ IN (SELECT value FROM json_each(?))",
        (projekt, *OFFEN, _typen(IMPORTE))).fetchone() is not None


def abbrechen_anfordern(conn: sqlite3.Connection, aid: str) -> str | None:
    """Wartend → sofort abgebrochen; laufend → Wunsch an den Worker (er stoppt die Unit).
    Liefert den Status danach oder None, wenn es nichts abzubrechen gibt."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute("SELECT status FROM auftraege WHERE id=? AND loeschen=0",
                         (aid,)).fetchone()
        if r is None or r["status"] not in OFFEN:
            conn.execute("COMMIT")
            return None
        if r["status"] == WARTEND:
            conn.execute(
                "UPDATE auftraege SET status=?, beendet_am=?, fehler=? WHERE id=?",
                (ABGEBROCHEN, int(time.time()), "Abgebrochen.", aid))
        else:
            conn.execute("UPDATE auftraege SET abbrechen=1 WHERE id=?", (aid,))
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    return ABGEBROCHEN if r["status"] == WARTEND else LAUFEND


def abbruch_gewuenscht(conn: sqlite3.Connection, aid: str) -> bool:
    r = conn.execute("SELECT abbrechen FROM auftraege WHERE id=?", (aid,)).fetchone()
    return r is not None and bool(r[0])


def position(conn: sqlite3.Connection, a: Auftrag) -> int | None:
    """Platz in der Warteschlange der Instanz (1 = als Nächstes), nur für wartende Aufträge."""
    if a.status != WARTEND:
        return None
    n = conn.execute(
        "SELECT COUNT(*) FROM auftraege WHERE status=? AND loeschen=0 AND nr<=?",
        (WARTEND, a.nr),
    ).fetchone()[0]
    return int(n)


def loeschen_anfordern(conn: sqlite3.Connection, projekt: str, aid: str) -> bool:
    """Markiert einen nicht laufenden Auftrag zum Löschen; der Worker entfernt Ordner und Zeile.

    False, wenn er gerade läuft (nicht anfassen, sonst schriebe der Worker danach in einen
    gelöschten Auftrag)."""
    cur = conn.execute(
        "UPDATE auftraege SET loeschen=1 WHERE id=? AND projekt=? AND status<>? AND loeschen=0",
        (aid, projekt, LAUFEND),
    )
    return cur.rowcount == 1


def worker_zustand(conn: sqlite3.Connection, now: int | None = None) -> str:
    """„aktiv“, „still“ (Herzschlag zu alt) oder „aus“ (nie gestartet)."""
    r = conn.execute("SELECT herzschlag FROM worker WHERE id=1").fetchone()
    if r is None:
        return "aus"
    jetzt = int(time.time()) if now is None else now
    return "aktiv" if jetzt - int(r["herzschlag"]) <= STILL_S else "still"


def berichtsordner(berichte: Path, projekt: str, aid: str) -> Path:
    """`<berichte>/<projekt>/<auftrag>/` — nur aus geprüften IDs zusammengesetzt."""
    if not PROJEKT_ID_RE.fullmatch(projekt) or not AUFTRAG_ID_RE.fullmatch(aid):
        raise AuftragUngueltig("ID ungültig.")
    return berichte / projekt / aid


# --- Worker-Seite -------------------------------------------------------------------------


def herzschlag(conn: sqlite3.Connection, pid: int, *, gestartet: bool = False) -> None:
    jetzt = int(time.time())
    if gestartet:
        conn.execute(
            "INSERT INTO worker (id, herzschlag, pid, gestartet_am) VALUES (1,?,?,?)"
            " ON CONFLICT(id) DO UPDATE SET herzschlag=excluded.herzschlag, pid=excluded.pid,"
            " gestartet_am=excluded.gestartet_am", (jetzt, pid, jetzt),
        )
    else:
        conn.execute("UPDATE worker SET herzschlag=? WHERE id=1", (jetzt,))


def laufende(conn: sqlite3.Connection) -> list[Auftrag]:
    rows = conn.execute("SELECT * FROM auftraege WHERE status=? ORDER BY nr", (LAUFEND,))
    return [_auftrag(r) for r in rows.fetchall()]


def naechster(conn: sqlite3.Connection) -> Auftrag | None:
    """Den ältesten wartenden Auftrag atomar auf „laufend“ setzen (FIFO, Parallelität 1)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        r = conn.execute(
            "SELECT * FROM auftraege WHERE status=? AND loeschen=0 ORDER BY nr LIMIT 1",
            (WARTEND,),
        ).fetchone()
        if r is not None:
            conn.execute(
                "UPDATE auftraege SET status=?, begonnen_am=? WHERE nr=?",
                (LAUFEND, int(time.time()), r["nr"]),
            )
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
    if r is None:
        return None
    a = _auftrag(r)
    return Auftrag(**{**a.__dict__, "status": LAUFEND})


def abschliessen(
    conn: sqlite3.Connection, aid: str, status: str, *, urteil: str | None = None,
    zaehlung: dict[str, int] | None = None, fehler: str | None = None,
) -> None:
    if status not in (FERTIG, FEHLGESCHLAGEN, ABGEBROCHEN):
        raise ValueError(status)
    conn.execute(
        "UPDATE auftraege SET status=?, beendet_am=?, urteil=?, zaehlung=?, fehler=? WHERE id=?",
        (status, int(time.time()), urteil,
         json.dumps(zaehlung, ensure_ascii=False) if zaehlung is not None else None,
         fehler, aid),
    )


def stufe_setzen(conn: sqlite3.Connection, aid: str, stufe: str) -> None:
    if stufe not in STUFEN:
        raise ValueError(stufe)
    conn.execute("UPDATE auftraege SET stufe=? WHERE id=?", (stufe, aid))


def zum_loeschen(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """(projekt, auftrag) aller zum Löschen markierten, nicht laufenden Aufträge."""
    rows = conn.execute(
        "SELECT projekt, id FROM auftraege WHERE loeschen=1 AND status<>?", (LAUFEND,),
    ).fetchall()
    return [(r["projekt"], r["id"]) for r in rows]


def entfernen(conn: sqlite3.Connection, aid: str) -> None:
    conn.execute("DELETE FROM auftraege WHERE id=? AND loeschen=1", (aid,))
