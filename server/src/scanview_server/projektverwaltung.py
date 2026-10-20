"""Projektverwaltung: Gewerke und Beteiligte, dazu (weiter unten) Projekt-Einstellungen, Vorlagen
und Kategorien je Projekt.

Reine DB-Funktionen; jede Änderung schreibt ihr Protokoll-Ereignis in derselben Transaktion
(db.transaktion). Projektrollen schreiben nur verwaltung.py und konten.recht_anheben — hier wird
an project_roles nur die Gewerk-Zuordnung gelöst, wenn ein Gewerk verschwindet. SQL ausschließlich
als feste Zeichenketten mit ?-Parametern. Im Protokoll stehen Gewerke und Kategorien mit Namen
(Sachbegriffe), Konten nur als ID.
"""

from __future__ import annotations

import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from typing import Any

from . import protokoll
from .db import transaktion
from .inhalte import POI_LIMITS, RUECKMELDUNG
from .web import Ungueltig

GEWERK_NAME_MAX = 80
GEWERK_KURZ_MAX = 4
GEWERKE_MAX = 50
OFFENE_EINLADUNGEN_MAX = 200
GEWERK_DOPPELT = "Ein Gewerk mit diesem Namen gibt es in diesem Projekt schon."
GEWERKE_ZU_VIELE = f"Höchstens {GEWERKE_MAX} Gewerke je Projekt."
GEWERK_BELEGT = ("Das Gewerk ist noch für {} Befunde zuständig — erst dort die Zuständigkeit "
                 "ändern.")


class GewerkBelegt(Exception):
    """Gewerk löschen blockiert: Befunde hängen daran (kein stilles Leeren des Stands)."""

    def __init__(self, anzahl: int) -> None:
        super().__init__(GEWERK_BELEGT.format(anzahl))
        self.anzahl = anzahl


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def text_pruefen(wert: str, feld: str, hoechstens: int) -> str:
    """NFC, Rand weg, nicht leer, Länge, keine Steuerzeichen."""
    t = unicodedata.normalize("NFC", wert).strip()
    if not t:
        raise Ungueltig(f"{feld} fehlt.")
    if len(t) > hoechstens:
        raise Ungueltig(f"{feld} länger als {hoechstens} Zeichen.")
    if any(ord(c) < 32 or ord(c) == 127 for c in t):
        raise Ungueltig(f"{feld} enthält Steuerzeichen.")
    return t


@dataclass(frozen=True)
class Gewerk:
    id: int
    name: str
    kurz: str
    bauherrenseite: bool

    def als_json(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "kurz": self.kurz,
                "bauherrenseite": self.bauherrenseite}


def _gewerk(r: sqlite3.Row) -> Gewerk:
    return Gewerk(id=int(r["id"]), name=str(r["name"]), kurz=str(r["kurz"]),
                  bauherrenseite=bool(r["bauherrenseite"]))


def _felder(g: Gewerk) -> dict[str, Any]:
    return {"name": g.name, "kurz": g.kurz, "bauherrenseite": g.bauherrenseite}


def gewerke(conn: sqlite3.Connection, pid: str) -> list[Gewerk]:
    return [_gewerk(r) for r in conn.execute(
        "SELECT id, name, kurz, bauherrenseite FROM gewerke WHERE project_id=?"
        " ORDER BY name COLLATE NOCASE, id", (pid,))]


def gewerk(conn: sqlite3.Connection, pid: str, gid: int) -> Gewerk | None:
    r = conn.execute(
        "SELECT id, name, kurz, bauherrenseite FROM gewerke WHERE id=? AND project_id=?",
        (gid, pid)).fetchone()
    return None if r is None else _gewerk(r)


def _name_frei(conn: sqlite3.Connection, pid: str, name: str, ausser: int | None) -> None:
    for r in conn.execute("SELECT id, name FROM gewerke WHERE project_id=?", (pid,)):
        if r["id"] != ausser and str(r["name"]).casefold() == name.casefold():
            raise Ungueltig(GEWERK_DOPPELT)


def gewerk_anlegen(conn: sqlite3.Connection, pid: str, *, name: str, kurz: str,
                   bauherrenseite: bool, von: int, now: int | None = None) -> Gewerk:
    name = text_pruefen(name, "Name", GEWERK_NAME_MAX)
    kurz = text_pruefen(kurz, "Kurzzeichen", GEWERK_KURZ_MAX)
    n = _jetzt(now)
    with transaktion(conn):
        anzahl = conn.execute(
            "SELECT COUNT(*) FROM gewerke WHERE project_id=?", (pid,)).fetchone()[0]
        if anzahl >= GEWERKE_MAX:
            raise Ungueltig(GEWERKE_ZU_VIELE)
        _name_frei(conn, pid, name, None)
        gid = conn.execute(
            "INSERT INTO gewerke (project_id, name, kurz, bauherrenseite, angelegt_am)"
            " VALUES (?,?,?,?,?)", (pid, name, kurz, int(bauherrenseite), n)).lastrowid
        if gid is None:
            raise RuntimeError("Gewerk ohne ID")
        g = Gewerk(id=int(gid), name=name, kurz=kurz, bauherrenseite=bauherrenseite)
        protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                           art="gewerk.angelegt", objekt_id=str(g.id), nachher=_felder(g), now=n)
    return g


def gewerk_aendern(conn: sqlite3.Connection, pid: str, gid: int, *, name: str | None,
                   kurz: str | None, bauherrenseite: bool | None, von: int,
                   now: int | None = None) -> Gewerk:
    neu_name = None if name is None else text_pruefen(name, "Name", GEWERK_NAME_MAX)
    neu_kurz = None if kurz is None else text_pruefen(kurz, "Kurzzeichen", GEWERK_KURZ_MAX)
    n = _jetzt(now)
    with transaktion(conn):
        alt = gewerk(conn, pid, gid)
        if alt is None:
            raise LookupError(gid)
        if neu_name is not None:
            _name_frei(conn, pid, neu_name, gid)
        g = Gewerk(id=gid, name=neu_name or alt.name, kurz=neu_kurz or alt.kurz,
                   bauherrenseite=alt.bauherrenseite if bauherrenseite is None else bauherrenseite)
        if g == alt:
            return g
        conn.execute(
            "UPDATE gewerke SET name=?, kurz=?, bauherrenseite=? WHERE id=? AND project_id=?",
            (g.name, g.kurz, int(g.bauherrenseite), gid, pid))
        protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                           art="gewerk.geaendert", objekt_id=str(gid), vorher=_felder(alt),
                           nachher=_felder(g), now=n)
    return g


def gewerk_loeschen(conn: sqlite3.Connection, pid: str, gid: int, *, von: int,
                    now: int | None = None) -> int:
    """Löscht das Gewerk; Beteiligte und offene Einladungen sind danach ohne Gewerk. Ist es noch
    an einem nicht gelöschten Befund zuständig (gleich welcher Status) → `GewerkBelegt`; nur an
    weich gelöschten setzt der Fremdschlüssel die Zuständigkeit auf NULL."""
    n = _jetzt(now)
    with transaktion(conn):
        alt = gewerk(conn, pid, gid)
        if alt is None:
            raise LookupError(gid)
        belegt = conn.execute(
            "SELECT COUNT(*) FROM pois WHERE project_id=? AND zustaendig_gewerk_id=?"
            " AND geloescht_am IS NULL",
            (pid, gid)).fetchone()[0]
        if belegt:
            raise GewerkBelegt(int(belegt))
        ohne = conn.execute(
            "UPDATE project_roles SET gewerk_id=NULL WHERE gewerk_id=? AND project_id=?",
            (gid, pid)).rowcount
        conn.execute("UPDATE invites SET gewerk_id=NULL WHERE gewerk_id=?", (gid,))
        conn.execute("DELETE FROM gewerke WHERE id=? AND project_id=?", (gid, pid))
        protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                           art="gewerk.geloescht", objekt_id=str(gid), vorher=_felder(alt),
                           nachher={"ohneGewerk": ohne}, now=n)
    return ohne


@dataclass(frozen=True)
class Beteiligter:
    nutzer_id: int
    name: str
    email: str
    rolle: str
    gewerk_id: int | None
    letzte_anmeldung: int | None
    gesperrt: bool
    admin: bool


def beteiligte(conn: sqlite3.Connection, pid: str) -> list[Beteiligter]:
    """Konten mit Projektrolle (ein Admin ohne Projektrolle steht nicht darin)."""
    rows = conn.execute(
        "SELECT u.id, u.name, u.email, u.is_admin, u.disabled, u.last_login_at, r.role, r.gewerk_id"
        " FROM project_roles r JOIN users u ON u.id = r.user_id WHERE r.project_id=?"
        " ORDER BY u.name COLLATE NOCASE, u.id", (pid,)).fetchall()
    return [Beteiligter(
        nutzer_id=int(r["id"]), name=str(r["name"]), email=str(r["email"]), rolle=str(r["role"]),
        gewerk_id=None if r["gewerk_id"] is None else int(r["gewerk_id"]),
        letzte_anmeldung=r["last_login_at"], gesperrt=bool(r["disabled"]),
        admin=bool(r["is_admin"])) for r in rows]


def beteiligter_json(
    b: Beteiligter, gewerke_je_id: dict[int, Gewerk], *, voll: bool,
) -> dict[str, Any]:
    """`nutzerId`/`nutzer` wie im Verlauf (der MCP-Datenschutzfilter pseudonymisiert sie); E-Mail,
    letzte Anmeldung und Sperre nur mit `voll` (Projektleitung/Admin im Browser)."""
    g = None if b.gewerk_id is None else gewerke_je_id.get(b.gewerk_id)
    aus: dict[str, Any] = {
        "nutzerId": b.nutzer_id, "nutzer": b.name, "rolle": b.rolle,
        "gewerk": None if g is None else {"id": g.id, "name": g.name, "kurz": g.kurz},
    }
    if voll:
        aus |= {"email": b.email, "letzteAnmeldung": b.letzte_anmeldung, "gesperrt": b.gesperrt,
                "admin": b.admin}
    return aus


# --- Einstellungen, Vorlagen, Kategorien ------------------------------------------------------

VORLAGEN: dict[str, tuple[str, ...]] = {
    "bestandsaufnahme": ("Raum", "Anlage", "Zugang", "Mangel", "Sonstiges"),
    "bauprojekt": ("Mangel", "Frage", "Hinweis", "Entscheidung", "Sonstiges"),
}
VORLAGE_NAME = {"bestandsaufnahme": "Bestandsaufnahme", "bauprojekt": "Bauprojekt"}
STANDARD_VORLAGE = "bestandsaufnahme"
# Befund-Fäden sind ab Werk nur bei diesen Vorlagen eingeschaltet (Plan O5).
FAEDEN_VORLAGEN = ("bauprojekt",)
KATEGORIE_MAX = POI_LIMITS["category"]
KATEGORIEN_MAX = 30
KATEGORIE_FEHLT = (
    "Kategorien lassen sich nur ausschalten, nicht entfernen: „{}“ fehlt in der Liste.")
KATEGORIE_DOPPELT = "Die Kategorie „{}“ steht doppelt in der Liste."
KATEGORIE_RESERVIERT = (
    f"„{RUECKMELDUNG}“ ist für Rückmeldungen reserviert und keine Projektkategorie.")
KATEGORIEN_ZU_VIELE = f"Höchstens {KATEGORIEN_MAX} Kategorien je Projekt."


@dataclass(frozen=True)
class Einstellungen:
    vorlage: str | None = None          # None: noch nicht eingerichtet, Vorgaben gelten
    rueckmeldungen: bool = True
    export_bearbeiter: bool = True
    befund_faeden: bool = False         # Standard nach Vorlage (FAEDEN_VORLAGEN)
    nur_lese_links: bool = False        # F28: Gäste per Link, Standard aus

    def als_json(self) -> dict[str, Any]:
        return {"vorlage": self.vorlage, "rueckmeldungen": self.rueckmeldungen,
                "exportBearbeiter": self.export_bearbeiter, "befundFaeden": self.befund_faeden,
                "nurLeseLinks": self.nur_lese_links}


VORGABE = Einstellungen()


def _einstellungen(r: sqlite3.Row) -> Einstellungen:
    return Einstellungen(vorlage=str(r["vorlage"]), rueckmeldungen=bool(r["rueckmeldungen"]),
                         export_bearbeiter=bool(r["export_bearbeiter"]),
                         befund_faeden=bool(r["befund_faeden"]),
                         nur_lese_links=bool(r["nur_lese_links"]))


def einstellungen(conn: sqlite3.Connection, pid: str) -> Einstellungen:
    r = conn.execute("SELECT vorlage, rueckmeldungen, export_bearbeiter, befund_faeden,"
                     " nur_lese_links FROM projekte WHERE project_id=?", (pid,)).fetchone()
    return VORGABE if r is None else _einstellungen(r)


def alle_einstellungen(conn: sqlite3.Connection) -> dict[str, Einstellungen]:
    return {str(r["project_id"]): _einstellungen(r) for r in conn.execute(
        "SELECT project_id, vorlage, rueckmeldungen, export_bearbeiter, befund_faeden,"
        " nur_lese_links FROM projekte")}


def wirksame_rolle(rolle: str | None, e: Einstellungen) -> str | None:
    """Schalter „Rückmeldungen erlaubt“ aus: ein Rückmelder wirkt wie ein Betrachter."""
    return "betrachter" if rolle == "rueckmelder" and not e.rueckmeldungen else rolle


def _schalter(e: Einstellungen) -> dict[str, bool]:
    return {"rueckmeldungen": e.rueckmeldungen, "exportBearbeiter": e.export_bearbeiter,
            "befundFaeden": e.befund_faeden, "nurLeseLinks": e.nur_lese_links}


@dataclass(frozen=True)
class Kategorie:
    name: str
    aktiv: bool

    def als_json(self) -> dict[str, Any]:
        return {"name": self.name, "aktiv": self.aktiv}


def kategorien(conn: sqlite3.Connection, pid: str) -> list[Kategorie]:
    """Geordnet; ohne Einrichtung die Startliste der Standardvorlage (Lesen schreibt nie)."""
    if conn.execute("SELECT 1 FROM projekte WHERE project_id=?", (pid,)).fetchone() is None:
        return [Kategorie(n, True) for n in VORLAGEN[STANDARD_VORLAGE]]
    return [Kategorie(str(r["name"]), bool(r["aktiv"])) for r in conn.execute(
        "SELECT name, aktiv FROM projekt_kategorien WHERE project_id=? ORDER BY reihenfolge, id",
        (pid,))]


class SchonEingerichtet(Exception):
    def __init__(self, vorlage: str) -> None:
        super().__init__(
            f"Das Projekt ist schon eingerichtet (Vorlage „{VORLAGE_NAME.get(vorlage, vorlage)}“). "
            "Kategorien und Schalter lassen sich weiter ändern.")


def _anlegen(conn: sqlite3.Connection, pid: str, vorlage: str, von: int, n: int) -> None:
    """In laufender Transaktion: Zeile in `projekte` und Startkategorien der Vorlage."""
    conn.execute("INSERT INTO projekte (project_id, vorlage, angelegt_am, befund_faeden)"
                 " VALUES (?,?,?,?)", (pid, vorlage, n, int(vorlage in FAEDEN_VORLAGEN)))
    for i, name in enumerate(VORLAGEN[vorlage]):
        conn.execute("INSERT INTO projekt_kategorien (project_id, name, reihenfolge, aktiv)"
                     " VALUES (?,?,?,1)", (pid, name, i))
    protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                       art="projekt.eingerichtet", objekt_id=pid,
                       nachher={"vorlage": vorlage, "kategorien": list(VORLAGEN[vorlage])}, now=n)


def _sicherstellen(conn: sqlite3.Connection, pid: str, von: int, n: int) -> None:
    if conn.execute("SELECT 1 FROM projekte WHERE project_id=?", (pid,)).fetchone() is None:
        _anlegen(conn, pid, STANDARD_VORLAGE, von, n)


def einrichten(conn: sqlite3.Connection, pid: str, vorlage: str, *, von: int,
               now: int | None = None) -> Einstellungen:
    if vorlage not in VORLAGEN:
        raise ValueError(vorlage)
    n = _jetzt(now)
    with transaktion(conn):
        alt = conn.execute("SELECT vorlage FROM projekte WHERE project_id=?", (pid,)).fetchone()
        if alt is not None:
            raise SchonEingerichtet(str(alt["vorlage"]))
        _anlegen(conn, pid, vorlage, von, n)
    return einstellungen(conn, pid)


def einstellungen_aendern(conn: sqlite3.Connection, pid: str, *, rueckmeldungen: bool | None,
                          export_bearbeiter: bool | None, von: int,
                          befund_faeden: bool | None = None,
                          nur_lese_links: bool | None = None,
                          now: int | None = None) -> Einstellungen:
    n = _jetzt(now)
    with transaktion(conn):
        alt = einstellungen(conn, pid)
        neu = Einstellungen(
            vorlage=alt.vorlage or STANDARD_VORLAGE,
            rueckmeldungen=alt.rueckmeldungen if rueckmeldungen is None else rueckmeldungen,
            export_bearbeiter=(alt.export_bearbeiter if export_bearbeiter is None
                               else export_bearbeiter),
            befund_faeden=alt.befund_faeden if befund_faeden is None else befund_faeden,
            nur_lese_links=alt.nur_lese_links if nur_lese_links is None else nur_lese_links)
        if _schalter(neu) == _schalter(alt):
            return einstellungen(conn, pid)
        _sicherstellen(conn, pid, von, n)
        conn.execute("UPDATE projekte SET rueckmeldungen=?, export_bearbeiter=?, befund_faeden=?,"
                     " nur_lese_links=? WHERE project_id=?",
                     (int(neu.rueckmeldungen), int(neu.export_bearbeiter),
                      int(neu.befund_faeden), int(neu.nur_lese_links), pid))
        protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                           art="einstellung.geaendert", objekt_id=pid, vorher=_schalter(alt),
                           nachher=_schalter(neu), now=n)
    return neu


def kategorien_setzen(conn: sqlite3.Connection, pid: str, liste: list[tuple[str, bool]], *,
                      von: int, now: int | None = None) -> list[Kategorie]:
    """Ersetzt die geordnete Liste: neue Namen werden angelegt, vorhandene neu geordnet und ein-
    oder ausgeschaltet. Vorhandene Namen müssen enthalten bleiben (kein Entfernen, kein Umbenennen;
    POIs tragen die Kategorie als Text)."""
    if len(liste) > KATEGORIEN_MAX:
        raise Ungueltig(KATEGORIEN_ZU_VIELE)
    neu: list[Kategorie] = []
    gesehen: set[str] = set()
    for roh, aktiv in liste:
        name = text_pruefen(roh, "Kategorie", KATEGORIE_MAX)
        schluessel = name.casefold()
        if schluessel == RUECKMELDUNG.casefold():
            raise Ungueltig(KATEGORIE_RESERVIERT)
        if schluessel in gesehen:
            raise Ungueltig(KATEGORIE_DOPPELT.format(name))
        gesehen.add(schluessel)
        neu.append(Kategorie(name, aktiv))
    n = _jetzt(now)
    with transaktion(conn):
        _sicherstellen(conn, pid, von, n)
        alt = {str(r["name"]).casefold(): r for r in conn.execute(
            "SELECT id, name, reihenfolge, aktiv FROM projekt_kategorien WHERE project_id=?",
            (pid,))}
        for schluessel, r in alt.items():
            if schluessel not in gesehen:
                raise Ungueltig(KATEGORIE_FEHLT.format(r["name"]))
        for i, k in enumerate(neu):
            r = alt.get(k.name.casefold())
            if r is None:
                kid = conn.execute(
                    "INSERT INTO projekt_kategorien (project_id, name, reihenfolge, aktiv)"
                    " VALUES (?,?,?,?)", (pid, k.name, i, int(k.aktiv))).lastrowid
                protokoll.ereignis(conn, kanal="web", nutzer_id=von, project_id=pid,
                                   art="kategorie.angelegt", objekt_id=str(kid),
                                   nachher={"name": k.name, "aktiv": k.aktiv, "reihenfolge": i},
                                   now=n)
            elif (int(r["reihenfolge"]), bool(r["aktiv"])) != (i, k.aktiv):
                conn.execute("UPDATE projekt_kategorien SET reihenfolge=?, aktiv=? WHERE id=?",
                             (i, int(k.aktiv), r["id"]))
                protokoll.ereignis(
                    conn, kanal="web", nutzer_id=von, project_id=pid, art="kategorie.geaendert",
                    objekt_id=str(r["id"]),
                    vorher={"name": r["name"], "aktiv": bool(r["aktiv"]),
                            "reihenfolge": int(r["reihenfolge"])},
                    nachher={"name": r["name"], "aktiv": k.aktiv, "reihenfolge": i}, now=n)
    return kategorien(conn, pid)
