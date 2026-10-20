"""Befund-Fäden: Einträge am POI, aktueller Stand (Status, Zuständigkeit, Frist) und Sichtbarkeit.

Ein Faden ist eine Folge unveränderlicher Einträge (`befund_eintraege`, Migration 0011; UPDATE nur
fürs einmalige Zurückziehen, DELETE nie). Der Stand am POI (`pois.status`, `zustaendig_*`,
`frist`) ändert sich nur über einen Eintrag der passenden Art — Stand, Eintrag und Protokoll-
Ereignis in derselben Transaktion; nur dabei steigt `pois.version` (Konfliktschutz wie bei POIs).
Kommentare und Links erhöhen die Version nicht, zwei Leute können gleichzeitig kommentieren.

Rechte: Bauherrenseite = Projektleitung, Admin oder Bearbeiter in einem
Gewerk mit `bauherrenseite = 1`; nur sie lesen und schreiben interne Einträge und setzen die
Prüfschritte (abschließen, ablehnen, wieder öffnen). Rückmelder kommentieren und setzen
`in_arbeit`/`erledigt`, wenn sie zuständig sind (persönlich oder über ihr Gewerk). Ein Lese-Token
sieht nie interne Einträge, auch nicht für die Bauherrenseite.

Vorrang bei Fehlern wie bei POIs: 404 → 403 → 409 → 422. Im Protokoll stehen Konten nur als ID;
der Originaltext eines zurückgezogenen Kommentars bleibt im Ereignis `eintrag.angelegt`.

KI-Vorschläge (G7): Status, Zuständigkeit oder Frist über einen Agent-Token (`ki=True`) ändern den
Stand nicht, sondern legen einen Eintrag `vorschlag` an (`wert` = {art, wert}, Ereignis
`eintrag.vorgeschlagen`, kein Export). Ein Mensch nimmt ihn an (echter Stand-Eintrag mit `bezug`
= Schlüssel des Vorschlags) oder verwirft ihn (Kommentar mit `bezug`, Ereignis
`eintrag.vorschlag_verworfen`, kein Export). Offen ist ein Vorschlag, solange kein Eintrag auf ihn
`bezug` nimmt. Vorschlag und Verwerfen-Kommentar sehen nur Prüfer (ab Bearbeiter) und wer den
Vorschlag über seinen Token angelegt hat. Ein POI, der selbst noch Vorschlag ist, hat keinen
Faden (409); ein verworfener gilt wie gelöscht (404).
"""

from __future__ import annotations

import json
import re
import secrets
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from starlette.exceptions import HTTPException as StarletteHTTPException

from . import protokoll
from .db import transaktion
from .inhalte import Eintrag
from .projektverwaltung import text_pruefen
from .web import SQLITE_GANZZAHL_MAX, Konflikt, Ungueltig

STATUS: dict[str, tuple[str, ...]] = {
    "offen": ("in_arbeit", "erledigt", "abgelehnt"),
    "in_arbeit": ("erledigt", "offen", "abgelehnt"),
    "erledigt": ("abgeschlossen", "offen"),
    "abgeschlossen": ("offen",),
    "abgelehnt": ("offen",),
}
STATUS_TEXT = {"offen": "offen", "in_arbeit": "in Arbeit", "erledigt": "erledigt (gemeldet)",
               "abgeschlossen": "abgeschlossen", "abgelehnt": "abgelehnt"}
GESCHLOSSEN = ("abgeschlossen", "abgelehnt")
LAUFEND = ("offen", "in_arbeit")              # nur diese können überfällig sein
RUECKMELDER_ZIELE = ("in_arbeit", "erledigt")
TEXT_ARTEN = ("kommentar", "link")
STAND_ARTEN = ("status", "zustaendig", "frist")
BERLIN = ZoneInfo("Europe/Berlin")
TEXT_MAX = 4000
TITEL_MAX = 200
URL_MAX = 2000
EINTRAEGE_MAX = 1000
_DATUM_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
# Relativer Befundlink dieser Instanz: Viewer-Pfad (`/ansicht`, in der Entwicklung `/`) mit
# Abfrage und Fragment, so wie der Viewer ihn kopiert (befundLink.ts) — `; , | :` im Fragment,
# `/data/<pid>/` in der Abfrage. Nur druckbares ASCII ohne Leerzeichen und Backslash; kein `//`
# (fremder Host), kein Schema.
VIEWER_PFADE = frozenset({"/", "/ansicht"})
_RELATIV_ZEICHEN_RE = re.compile(r"[\x21-\x5b\x5d-\x7e]+")
# Externe Kennung eines Eintrags: zufällig, unveränderlich. Die fortlaufende `id` erscheint in
# keiner Ausgabe (API, Export, Protokoll, Git) — sonst verrieten Lücken interne Einträge.
SCHLUESSEL_RE = re.compile(r"[0-9a-f]{16}")

NICHT_GEFUNDEN = "nicht gefunden"
NICHT_ERLAUBT = "Diesen Eintrag erlaubt Ihre Rolle in diesem Projekt nicht."
NUR_BAUHERRENSEITE = "Abschließen, Ablehnen und Wiederöffnen nur durch die Bauherrenseite."
NUR_VERFASSER = "Nur wer den Eintrag geschrieben hat, kann ihn zurückziehen."
ART_UNGUELTIG = "Unbekannte Eintragsart (erlaubt: kommentar, link, status, zustaendig, frist)."
NUR_TEXT_INTERN = "Intern können nur Kommentare und Links sein."
NUR_TEXT_ZURUECK = "Zurückziehen lässt sich nur ein Kommentar oder Link."
VERSION_FEHLT = "version fehlt (Pflicht bei Status, Zuständigkeit und Frist)."
TEXT_FEHLT = "Text fehlt."
BEGRUENDUNG_FEHLT = "Begründung fehlt (Pflicht bei „abgelehnt“ und beim Wiederöffnen)."
STATUS_UNBEKANNT = "Unbekannter Status."
WECHSEL_UNMOEGLICH = "Dieser Statuswechsel ist vom aktuellen Status aus nicht möglich."
KEINE_AENDERUNG = "Keine Änderung: der Wert ist schon gesetzt."
ZU_VIELE = f"Höchstens {EINTRAEGE_MAX} Einträge je Befund."
NICHT_UTF8 = "Der Text enthält Zeichen, die sich nicht als UTF-8 speichern lassen."
TEXT_KEIN_TEXT = "Text muss Text sein."
TEXT_ZU_LANG = f"Text länger als {TEXT_MAX} Zeichen."
TEXT_STEUERZEICHEN = "Text enthält Steuerzeichen."
OHNE_WERT = "Ein Kommentar hat keinen Wert, nur Text."
LINK_FORM = "Ein Link braucht genau url und titel."
LINK_UNGUELTIG = "Links nur mit https:// oder als Befundlink dieser Instanz (/ansicht?p=…#…)."
FRIST_FORM = "Frist als Datum JJJJ-MM-TT angeben."
ZUSTAENDIG_FORM = "Zuständig braucht gewerk_id und nutzer_id (Person optional: null)."
GEWERK_FREMD = "Das Gewerk gehört nicht zu diesem Projekt."
PERSON_FREMD = "Die Person gehört nicht zu diesem Gewerk im Projekt."
BEFUND_IST_VORSCHLAG = ("Dieser Befund ist noch ein Vorschlag – erst annehmen, dann im Faden"  # noqa: RUF001
                        " arbeiten.")
VORSCHLAG_ERLEDIGT = "Über diesen Vorschlag ist schon entschieden."
VORSCHLAG_DOPPELT = "Genau dieser Vorschlag ist schon offen – bitte die Entscheidung abwarten."  # noqa: RUF001
KEIN_VORSCHLAG_EINTRAG = "Dieser Eintrag ist kein Vorschlag."
VERWORFEN_TEXT = "Vorschlag verworfen."


@dataclass(frozen=True)
class Sicht:
    """Wer fragt: Konto, wirksame Rolle, Gewerk im Projekt, Bauherrenseite und ob interne
    Einträge sichtbar sind (Bauherrenseite, nicht per Lese-Token)."""

    nutzer: int
    rolle: str
    gewerk: int | None
    bauherrenseite: bool
    intern: bool


def sicht(conn: sqlite3.Connection, pid: str, nutzer: int, rolle: str, *,
          lese_token: bool = False) -> Sicht:
    r = conn.execute(
        "SELECT g.id, g.bauherrenseite FROM project_roles r JOIN gewerke g ON g.id = r.gewerk_id"
        " AND g.project_id = r.project_id WHERE r.project_id=? AND r.user_id=?", (pid, nutzer),
    ).fetchone()
    gewerk = None if r is None else int(r["id"])
    bhs = rolle in ("admin", "projektleitung") or (
        rolle == "bearbeiter" and r is not None and bool(r["bauherrenseite"]))
    return Sicht(nutzer=nutzer, rolle=rolle, gewerk=gewerk, bauherrenseite=bhs,
                 intern=bhs and not lese_token)


def aktiviert(conn: sqlite3.Connection, pid: str) -> bool:
    r = conn.execute("SELECT befund_faeden FROM projekte WHERE project_id=?", (pid,)).fetchone()
    return r is not None and bool(r[0])


def heute(now: datetime | None = None) -> str:
    """Heutiger Kalendertag in Europe/Berlin als JJJJ-MM-TT (Uhr injizierbar)."""
    return (now or datetime.now(BERLIN)).astimezone(BERLIN).date().isoformat()


def ueberfaellig(st: dict[str, Any], tag: str) -> bool:
    """Überfällig ab dem Folgetag der Frist, solange offen oder in Arbeit."""
    return st["status"] in LAUFEND and st["frist"] is not None and tag > st["frist"]


def _bearbeitet(rolle: str) -> bool:
    return rolle in ("bearbeiter", "projektleitung", "admin")


def _zustaendig_fuer(s: Sicht, st: dict[str, Any]) -> bool:
    z = st["zustaendig"]
    return z is not None and (z["nutzer_id"] == s.nutzer
                              or (s.gewerk is not None and z["gewerk_id"] == s.gewerk))


def _pruefschritt(von: str, ziel: str) -> bool:
    """Abschließen, Ablehnen, Wiederöffnen aus erledigt/abgeschlossen/abgelehnt (O2)."""
    return ziel in GESCHLOSSEN or (ziel == "offen" and von not in LAUFEND)


def rechte(s: Sicht, st: dict[str, Any]) -> dict[str, Any]:
    """Was diese Person an diesem Befund gerade darf — serverseitig, damit die Oberfläche keine
    Regeln dupliziert. `status`: erlaubte Zielstatus vom aktuellen Stand aus."""
    bearbeitet = _bearbeitet(s.rolle)
    eigen = s.rolle == "rueckmelder" and _zustaendig_fuer(s, st)
    ziele = [
        ziel for ziel in STATUS[st["status"]]
        if (bearbeitet and (s.bauherrenseite or not _pruefschritt(st["status"], ziel)))
        or (eigen and ziel in RUECKMELDER_ZIELE)
    ]
    return {"kommentieren": bearbeitet or s.rolle == "rueckmelder", "status": ziele,
            "zustaendig": bearbeitet, "frist": bearbeitet, "intern": s.intern}


def _stand(r: sqlite3.Row) -> dict[str, Any]:
    z = None
    if r["zustaendig_gewerk_id"] is not None or r["zustaendig_nutzer_id"] is not None:
        z = {"gewerk_id": r["zustaendig_gewerk_id"], "nutzer_id": r["zustaendig_nutzer_id"]}
    return {"status": r["status"], "zustaendig": z, "frist": r["frist"],
            "version": int(r["version"])}


def prueft(s: Sicht) -> bool:
    """Darf KI-Vorschläge sehen und über sie entscheiden (ab Bearbeiter)."""
    return _bearbeitet(s.rolle)


def _poi(conn: sqlite3.Connection, pid: str, poi: str, s: Sicht | None = None) -> sqlite3.Row:
    """Aktiver POI eines Projekts mit eingeschalteten Fäden, sonst 404 (gelöscht = ausgeblendet).
    Verworfen gilt wie gelöscht; ein POI-Vorschlag ist für `s` nur sichtbar, wenn `s` prüft oder
    ihn angelegt hat (sonst verriete ein 409 den Vorschlag). Ohne `s`: interne Aufrufer."""
    r: sqlite3.Row | None = None
    if aktiviert(conn, pid):
        r = conn.execute("SELECT * FROM pois WHERE project_id=? AND id=? AND geloescht_am IS NULL"
                         " AND pruefstand <> 'verworfen'", (pid, poi)).fetchone()
    if r is not None and s is not None and r["pruefstand"] == "vorschlag" and not (
            prueft(s) or r["erstellt_von"] == s.nutzer):
        r = None
    if r is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return r


def stand(conn: sqlite3.Connection, pid: str, poi: str, s: Sicht | None = None) -> dict[str, Any]:
    return _stand(_poi(conn, pid, poi, s))


def _gueltig(r: sqlite3.Row) -> None:
    """Am POI-Vorschlag gibt es noch keinen Faden (nach 404 und 403 geprüft)."""
    if r["pruefstand"] != "gueltig":
        raise StarletteHTTPException(status_code=409, detail=BEFUND_IST_VORSCHLAG)


def staende(conn: sqlite3.Connection, pid: str) -> dict[str, dict[str, Any]]:
    """Stand aller aktiven POIs eines Projekts (für Listen), ohne Schalterprüfung. Nur zum
    Nachschlagen: jede Ausgabe läuft über die nach Sicht gefilterte POI-Liste."""
    return {str(r["id"]): _stand(r) for r in conn.execute(
        "SELECT id, status, zustaendig_gewerk_id, zustaendig_nutzer_id, frist, version FROM pois"
        " WHERE project_id=? AND geloescht_am IS NULL AND pruefstand <> 'verworfen'", (pid,))}


_SELECT = ("SELECT e.*, u.name AS erstellt_von_name, NOT EXISTS (SELECT 1 FROM befund_eintraege b"
           " WHERE b.project_id = e.project_id AND b.poi_id = e.poi_id AND b.bezug = e.schluessel)"
           " AS offen FROM befund_eintraege e LEFT JOIN users u ON u.id = e.erstellt_von")
# Einträge, die zum Vorschlag gehören (er selbst und der Verwerfen-Kommentar): nur für Prüfer und
# den, der ihn angelegt hat; nie im Export.
VORSCHLAGSWELT = "(e.art = 'vorschlag' OR (e.art = 'kommentar' AND e.bezug IS NOT NULL))"


def _protokollform(r: sqlite3.Row) -> dict[str, Any]:
    """Eintrag ohne Namen — so steht er im Protokoll (nur Konto-IDs) und im Git-Export."""
    return {"schluessel": r["schluessel"], "poi": r["poi_id"], "art": r["art"], "text": r["text"],
            "wert": json.loads(r["wert"]), "intern": bool(r["intern"]),
            "erstellt_von": r["erstellt_von"], "erstellt_am": int(r["erstellt_am"]),
            "kanal": r["kanal"], "zurueckgezogen_am": r["zurueckgezogen_am"],
            "herkunft": r["herkunft"], "bezug": r["bezug"]}


def eintrag_json(r: sqlite3.Row, *, bezug: bool = True) -> dict[str, Any]:
    """Eintrag für Leser. Ein zurückgezogener Link verliert auch Adresse und Titel (der Trigger
    lässt `wert` unverändert; das Original steht im Protokoll). `bezug=False`: der Vorschlag,
    auf den der Eintrag antwortet, ist für diesen Leser unsichtbar."""
    zurueck = r["zurueckgezogen_am"] is not None
    aus = {"schluessel": r["schluessel"], "art": r["art"], "text": r["text"],
           "wert": None if zurueck and r["art"] == "link" else json.loads(r["wert"]),
           "intern": bool(r["intern"]),
           "erstellt_von": {"id": r["erstellt_von"], "name": r["erstellt_von_name"]},
           "erstellt_am": int(r["erstellt_am"]), "kanal": r["kanal"],
           "zurueckgezogen_am": r["zurueckgezogen_am"], "herkunft": r["herkunft"],
           "bezug": r["bezug"] if bezug else None}
    if r["art"] == "vorschlag":
        aus["offen"] = bool(r["offen"])
    return aus


def _sichtbar(rows: list[sqlite3.Row], s: Sicht | None) -> list[dict[str, Any]]:
    """Vorschlagswelt nur für Prüfer und für den, der den Vorschlag angelegt hat."""
    if s is None or prueft(s):
        return [eintrag_json(r) for r in rows]
    eigene = {r["schluessel"] for r in rows if r["art"] == "vorschlag"
              and r["erstellt_von"] == s.nutzer}
    aus = []
    for r in rows:
        if r["art"] == "vorschlag" and r["schluessel"] not in eigene:
            continue
        if r["art"] == "kommentar" and r["bezug"] is not None and r["bezug"] not in eigene:
            continue
        aus.append(eintrag_json(r, bezug=r["bezug"] is None or r["bezug"] in eigene))
    return aus


def lesen(conn: sqlite3.Connection, pid: str, poi: str, *, intern: bool,
          s: Sicht | None = None) -> list[dict[str, Any]]:
    """Sichtbare Einträge; `s` filtert die Vorschlagswelt (ohne `s`: alles, interne Aufrufer)."""
    _poi(conn, pid, poi, s)
    rows = conn.execute(_SELECT + " WHERE e.project_id=? AND e.poi_id=? AND (? OR e.intern=0)"
                        " ORDER BY e.id", (pid, poi, int(intern))).fetchall()
    return _sichtbar(rows, s)


def offene_vorschlaege(conn: sqlite3.Connection, pid: str) -> list[dict[str, Any]]:
    """Offene Faden-Vorschläge an aktiven, gültigen POIs (für die Liste „zu prüfen“)."""
    if not aktiviert(conn, pid):
        return []
    return [{"poi": str(r["poi_id"]), "eintrag": eintrag_json(r)} for r in conn.execute(
        _SELECT + " JOIN pois p ON p.project_id = e.project_id AND p.id = e.poi_id"
        " WHERE e.project_id=? AND e.art = 'vorschlag' AND p.geloescht_am IS NULL"
        " AND p.pruefstand = 'gueltig' ORDER BY e.id", (pid,)) if r["offen"]]


def mit_ueberfaellig(st: dict[str, Any], tag: str) -> dict[str, Any]:
    """Stand für Leser, mit „überfällig“ nach derselben Regel wie die Liste (Kalendertag `tag`)."""
    return {**st, "ueberfaellig": ueberfaellig(st, tag)}


def faden(conn: sqlite3.Connection, pid: str, poi: str, s: Sicht,
          tag: str | None = None) -> dict[str, Any]:
    """Antwort von GET …/faden: Stand (mit „überfällig“), sichtbare Einträge, Rechte."""
    st = stand(conn, pid, poi, s)
    return {"stand": mit_ueberfaellig(st, tag or heute()),
            "eintraege": lesen(conn, pid, poi, intern=s.intern, s=s), "rechte": rechte(s, st)}


def alle_faeden(conn: sqlite3.Connection, pid: str, *, intern: bool) -> dict[str, list[Any]]:
    """Sichtbare Einträge aller aktiven, gültigen POIs, je POI-ID (JSON-Export). Nur Gültiges:
    ohne Vorschlagswelt, und `bezug` zeigt im Export nie auf einen nicht exportierten Vorschlag."""
    aus: dict[str, list[Any]] = {}
    for r in conn.execute(
        _SELECT + " JOIN pois p ON p.project_id = e.project_id AND p.id = e.poi_id"
        " WHERE e.project_id=? AND p.geloescht_am IS NULL AND p.pruefstand = 'gueltig'"
        f" AND (? OR e.intern=0) AND NOT {VORSCHLAGSWELT} ORDER BY e.id",
        (pid, int(intern)),
    ):
        aus.setdefault(str(r["poi_id"]), []).append(eintrag_json(r, bezug=False))
    return aus


# --- Prüfen ---------------------------------------------------------------------------------


def _utf8(t: str) -> str:
    """Einzelne Surrogate (aus JSON `\\ud800`) ließen sich nicht speichern → 422 statt 500."""
    try:
        t.encode("utf-8")
    except UnicodeEncodeError:
        raise Ungueltig(NICHT_UTF8) from None
    return t


def _text(text: Any) -> str | None:
    if text is None:
        return None
    if not isinstance(text, str):
        raise Ungueltig(TEXT_KEIN_TEXT)
    _utf8(text)
    # Kommentare dürfen mehrzeilig sein (wie POI-Notizen); übrige Steuerzeichen nicht.
    t = unicodedata.normalize("NFC", text.replace("\r\n", "\n")).strip()
    if not t:
        raise Ungueltig(TEXT_FEHLT)
    if len(t) > TEXT_MAX:
        raise Ungueltig(TEXT_ZU_LANG)
    if any((ord(c) < 32 and c != "\n") or ord(c) == 127 for c in t):
        raise Ungueltig(TEXT_STEUERZEICHEN)
    return t


def _https(url: str) -> bool:
    try:
        teile = urlsplit(url)
        return teile.scheme == "https" and bool(teile.hostname) and teile.username is None
    except ValueError:
        return False


def _befundlink(url: str) -> bool:
    """Relativer Link auf den Viewer dieser Instanz (siehe VIEWER_PFADE)."""
    if not url.startswith("/") or url.startswith("//") or not _RELATIV_ZEICHEN_RE.fullmatch(url):
        return False
    try:
        teile = urlsplit(url)
    except ValueError:
        return False
    return not teile.scheme and not teile.netloc and teile.path in VIEWER_PFADE


def _link(wert: Any) -> dict[str, str]:
    if not isinstance(wert, dict) or set(wert) != {"url", "titel"}:
        raise Ungueltig(LINK_FORM)
    url, titel = wert["url"], wert["titel"]
    if not isinstance(url, str) or not isinstance(titel, str):
        raise Ungueltig(LINK_FORM)
    _utf8(url)
    _utf8(titel)
    if len(url) > URL_MAX or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        raise Ungueltig(LINK_UNGUELTIG)
    if not _https(url) and not _befundlink(url):
        raise Ungueltig(LINK_UNGUELTIG)
    return {"url": url, "titel": text_pruefen(titel, "Titel", TITEL_MAX)}


def _zustaendig(conn: sqlite3.Connection, pid: str, wert: Any) -> dict[str, Any] | None:
    if wert is None:
        return None
    if not isinstance(wert, dict) or set(wert) != {"gewerk_id", "nutzer_id"}:
        raise Ungueltig(ZUSTAENDIG_FORM)
    gid, uid = wert["gewerk_id"], wert["nutzer_id"]
    # type() statt isinstance: True/False sind keine Kennungen.
    if type(gid) is not int or (uid is not None and type(uid) is not int):
        raise Ungueltig(ZUSTAENDIG_FORM)
    if not 0 < gid <= SQLITE_GANZZAHL_MAX or conn.execute(
        "SELECT 1 FROM gewerke WHERE project_id=? AND id=?", (pid, gid)).fetchone() is None:
        raise Ungueltig(GEWERK_FREMD)
    if uid is not None and (not 0 < uid <= SQLITE_GANZZAHL_MAX or conn.execute(
        "SELECT 1 FROM project_roles WHERE project_id=? AND user_id=? AND gewerk_id=?",
        (pid, uid, gid),
    ).fetchone() is None):
        raise Ungueltig(PERSON_FREMD)
    return {"gewerk_id": gid, "nutzer_id": uid}


def _frist(wert: Any) -> str | None:
    if wert is None:
        return None
    if not isinstance(wert, str) or not _DATUM_RE.fullmatch(wert):
        raise Ungueltig(FRIST_FORM)
    try:
        date.fromisoformat(wert)
    except ValueError as e:
        raise Ungueltig(FRIST_FORM) from e
    return wert


def _wert(conn: sqlite3.Connection, pid: str, art: str, wert: Any) -> Any:
    if art == "link":
        return _link(wert)
    if art == "zustaendig":
        return _zustaendig(conn, pid, wert)
    if art == "frist":
        return _frist(wert)
    if wert is not None:
        raise Ungueltig(OHNE_WERT)
    return None


def _darf(s: Sicht, st: dict[str, Any], art: str, intern: bool) -> bool:
    """Grundrecht für diese Art (ohne Zielstatus): 403, bevor Version oder Inhalt zählen."""
    r = rechte(s, st)
    if intern and not r["intern"]:
        return False
    if art in TEXT_ARTEN:
        return bool(r["kommentieren"])
    if art == "status":
        return _bearbeitet(s.rolle) or (s.rolle == "rueckmelder" and _zustaendig_fuer(s, st))
    return bool(r[art])


def _status(s: Sicht, st: dict[str, Any], ziel: Any, text: str | None) -> str:
    if not isinstance(ziel, str) or ziel not in STATUS:
        raise Ungueltig(STATUS_UNBEKANNT)
    if ziel not in STATUS[st["status"]]:
        raise Ungueltig(WECHSEL_UNMOEGLICH)
    if ziel not in rechte(s, st)["status"]:
        raise StarletteHTTPException(status_code=403, detail=NUR_BAUHERRENSEITE)
    begruendung = ziel == "abgelehnt" or (ziel == "offen" and st["status"] not in LAUFEND)
    if begruendung and text is None:
        raise Ungueltig(BEGRUENDUNG_FEHLT)
    return ziel


def _stand_pruefen(conn: sqlite3.Connection, pid: str, s: Sicht, st: dict[str, Any], art: str,
                   wert: Any, text: str | None) -> Any:
    if art == "status":
        return _status(s, st, wert, text)
    neu = _wert(conn, pid, art, wert)
    if neu == st[art]:
        raise Ungueltig(KEINE_AENDERUNG)
    return neu


# --- Schreiben ------------------------------------------------------------------------------


def einfuegen(conn: sqlite3.Connection, pid: str, poi: str, *, art: str, text: str | None,
              wert: Any, intern: bool, nutzer: int | None, kanal: str,
              token_name: str | None, now: int, herkunft: str = "mensch",
              bezug: str | None = None) -> dict[str, Any]:
    """In laufender Transaktion: Eintrag + Ereignis `eintrag.angelegt` (auch für die automatischen
    `markierung`-Einträge aus inhalte.aendern). Die Vorschlagswelt bekommt eigene Ereignisarten,
    die der Git-Export nicht kennt: `eintrag.vorgeschlagen`, `eintrag.vorschlag_verworfen`."""
    if art != "markierung":
        # Getrennt gezählt (intern / nicht intern), Markierungen nie: sonst verriete die Grenze
        # interne Einträge, und ein voller Faden sperrte das Ändern am POI.
        anzahl = conn.execute(
            "SELECT COUNT(*) FROM befund_eintraege WHERE project_id=? AND poi_id=? AND intern=?"
            " AND art <> 'markierung'", (pid, poi, int(intern))).fetchone()[0]
        if anzahl >= EINTRAEGE_MAX:
            raise Ungueltig(ZU_VIELE)
    schluessel = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO befund_eintraege (schluessel, project_id, poi_id, art, text, wert, intern,"
        " erstellt_von, erstellt_am, kanal, token_name, herkunft, bezug)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (schluessel, pid, poi, art, text, json.dumps(wert, ensure_ascii=False), int(intern),
         nutzer, now, kanal, token_name, herkunft, bezug),
    )
    r = conn.execute(_SELECT + " WHERE e.schluessel=?", (schluessel,)).fetchone()
    if art == "vorschlag":
        ereignis = "eintrag.vorgeschlagen"
    elif art == "kommentar" and bezug is not None:
        ereignis = "eintrag.vorschlag_verworfen"
    else:
        ereignis = "eintrag.angelegt"
    protokoll.ereignis(conn, kanal=kanal, nutzer_id=nutzer, project_id=pid,
                       art=ereignis, objekt_id=f"{poi}/{schluessel}",
                       nachher=_protokollform(r), token_name=token_name, now=now)
    return eintrag_json(r)


def _stand_setzen(conn: sqlite3.Connection, pid: str, poi: str, art: str, wert: Any,
                  nutzer: int, now: int) -> None:
    if art == "zustaendig":
        conn.execute(
            "UPDATE pois SET zustaendig_gewerk_id=?, zustaendig_nutzer_id=?"
            " WHERE project_id=? AND id=?",
            (None if wert is None else wert["gewerk_id"],
             None if wert is None else wert["nutzer_id"], pid, poi))
    elif art == "status":
        conn.execute("UPDATE pois SET status=? WHERE project_id=? AND id=?", (wert, pid, poi))
    else:
        conn.execute("UPDATE pois SET frist=? WHERE project_id=? AND id=?", (wert, pid, poi))
    conn.execute("UPDATE pois SET version=version+1, geaendert_von=?, geaendert_am=?"
                 " WHERE project_id=? AND id=?", (nutzer, now, pid, poi))


def _kommentiert(s: Sicht, st: dict[str, Any]) -> None:
    if not rechte(s, st)["kommentieren"]:
        raise StarletteHTTPException(status_code=403, detail=NICHT_ERLAUBT)


def _grundrecht(s: Sicht, st: dict[str, Any], art: Any, intern: bool) -> None:
    _kommentiert(s, st)
    # `vorschlag` legt niemand von außen an: er entsteht nur über ki=True.
    if art not in TEXT_ARTEN + STAND_ARTEN:
        raise Ungueltig(ART_UNGUELTIG)
    if intern and art not in TEXT_ARTEN:
        raise Ungueltig(NUR_TEXT_INTERN)
    if not _darf(s, st, art, intern):
        raise StarletteHTTPException(status_code=403, detail=NICHT_ERLAUBT)


def _stand_aendern(conn: sqlite3.Connection, pid: str, poi: str, s: Sicht, st: dict[str, Any],
                   art: str, wert: Any, text: str | None, *, kanal: str, token_name: str | None,
                   n: int) -> Any:
    """Neuen Stand prüfen (Rechte für `s`, Wechsel, Wert) und setzen, mit Ereignis `poi.<art>`."""
    w = _stand_pruefen(conn, pid, s, st, art, wert, text)
    _stand_setzen(conn, pid, poi, art, w, s.nutzer, n)
    protokoll.ereignis(conn, kanal=kanal, nutzer_id=s.nutzer, project_id=pid,
                       art=f"poi.{art}", objekt_id=poi, vorher=st[art], nachher=w,
                       token_name=token_name, now=n)
    return w


def _vorschlag_pruefen(conn: sqlite3.Connection, pid: str, st: dict[str, Any], art: str,
                       wert: Any, text: str | None) -> Any:
    """Vorgeschlagener Stand: Form und Möglichkeit wie beim Setzen, ohne Zielrecht und ohne
    Version — darüber entscheidet erst, wer annimmt."""
    if art != "status":
        neu = _wert(conn, pid, art, wert)
        if neu == st[art]:
            raise Ungueltig(KEINE_AENDERUNG)
        return neu
    if not isinstance(wert, str) or wert not in STATUS:
        raise Ungueltig(STATUS_UNBEKANNT)
    if wert not in STATUS[st["status"]]:
        raise Ungueltig(WECHSEL_UNMOEGLICH)
    if (wert == "abgelehnt" or (wert == "offen" and st["status"] not in LAUFEND)) and text is None:
        raise Ungueltig(BEGRUENDUNG_FEHLT)
    return wert


def anlegen(conn: sqlite3.Connection, pid: str, poi: str, s: Sicht, *, art: Any,
            text: Any = None, wert: Any = None, intern: bool = False, version: int | None = None,
            kanal: str = "web", token_name: str | None = None, now: int | None = None,
            ki: bool = False) -> tuple[dict[str, Any], dict[str, Any]]:
    """Ein Eintrag; bei status/zustaendig/frist zugleich der neue Stand (version Pflicht). Der
    zurückgegebene Stand trägt „überfällig“ zum Zeitpunkt `now`. Mit `ki` wird aus einem
    Stand-Eintrag ein Vorschlag (Stand bleibt, `version` zählt nicht)."""
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        r = _poi(conn, pid, poi, s)
        st = _stand(r)
        _kommentiert(s, st)
        _gueltig(r)
        _grundrecht(s, st, art, intern)
        t = _text(text)
        if ki and art in STAND_ARTEN:
            w = _vorschlag_pruefen(conn, pid, st, art, wert, t)
            # Wiederholte Aufrufe (MCP-Client ohne Antwort) legen keinen zweiten gleichen offenen
            # Vorschlag an: Einträge sind unlöschbar und zählen gegen EINTRAEGE_MAX.
            if _offener_vorschlag(conn, pid, poi, {"art": art, "wert": w}):
                raise StarletteHTTPException(status_code=409, detail=VORSCHLAG_DOPPELT)
            e = einfuegen(conn, pid, poi, art="vorschlag", text=t, wert={"art": art, "wert": w},
                          intern=False, nutzer=s.nutzer, kanal=kanal, token_name=token_name,
                          now=n, herkunft="ki")
            return e, mit_ueberfaellig(st, heute(datetime.fromtimestamp(n, BERLIN)))
        if art in STAND_ARTEN:
            if version is None:
                raise Ungueltig(VERSION_FEHLT)
            if version != st["version"]:
                raise Konflikt(st)
            w = _stand_aendern(conn, pid, poi, s, st, art, wert, t, kanal=kanal,
                               token_name=token_name, n=n)
        else:
            w = _wert(conn, pid, art, wert)
            if art == "kommentar" and t is None:
                raise Ungueltig(TEXT_FEHLT)
        e = einfuegen(conn, pid, poi, art=art, text=t, wert=w, intern=intern, nutzer=s.nutzer,
                      kanal=kanal, token_name=token_name, now=n, herkunft="ki" if ki else "mensch")
        neu = stand(conn, pid, poi)
    return e, mit_ueberfaellig(neu, heute(datetime.fromtimestamp(n, BERLIN)))


def _offener_vorschlag(conn: sqlite3.Connection, pid: str, poi: str, wert: dict[str, Any]) -> bool:
    return any(r["offen"] and json.loads(r["wert"]) == wert for r in conn.execute(
        _SELECT + " WHERE e.project_id=? AND e.poi_id=? AND e.art = 'vorschlag'", (pid, poi)))


def _vorschlag(conn: sqlite3.Connection, pid: str, poi: str, schluessel: str,
               s: Sicht) -> sqlite3.Row:
    """Ein Vorschlag, über den `s` entscheiden will: unbekannt oder unsichtbar → 404, kein
    Vorschlag → 422, schon entschieden → 409."""
    v = conn.execute(_SELECT + " WHERE e.project_id=? AND e.poi_id=? AND e.schluessel=?"
                     " AND (? OR e.intern=0)", (pid, poi, schluessel, int(s.intern))).fetchone()
    if v is None or (v["art"] == "vorschlag" and not prueft(s) and v["erstellt_von"] != s.nutzer):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    if v["art"] != "vorschlag":
        raise Ungueltig(KEIN_VORSCHLAG_EINTRAG)
    if not v["offen"]:
        raise StarletteHTTPException(status_code=409, detail=VORSCHLAG_ERLEDIGT)
    return v


def vorschlag_annehmen(conn: sqlite3.Connection, pid: str, poi: str, schluessel: str, s: Sicht,
                       *, version: int, kanal: str, token_name: str | None,
                       now: int | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    """Vorschlag wird echter Stand-Eintrag mit den Rechten von `s` (Herkunft bleibt `ki`,
    `bezug` = Vorschlag); Text des Vorschlags wird Text (Begründung) des Eintrags."""
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        r = _poi(conn, pid, poi, s)
        st = _stand(r)
        _kommentiert(s, st)
        _gueltig(r)
        v = _vorschlag(conn, pid, poi, schluessel, s)
        vorschlag = json.loads(v["wert"])
        art = vorschlag["art"]
        _grundrecht(s, st, art, False)
        if version != st["version"]:
            raise Konflikt(st)
        w = _stand_aendern(conn, pid, poi, s, st, art, vorschlag["wert"], v["text"],
                           kanal=kanal, token_name=token_name, n=n)
        e = einfuegen(conn, pid, poi, art=art, text=v["text"], wert=w, intern=False,
                      nutzer=s.nutzer, kanal=kanal, token_name=token_name, now=n,
                      herkunft="ki", bezug=schluessel)
        neu = stand(conn, pid, poi)
    return e, mit_ueberfaellig(neu, heute(datetime.fromtimestamp(n, BERLIN)))


def vorschlag_verwerfen(conn: sqlite3.Connection, pid: str, poi: str, schluessel: str, s: Sicht,
                        *, text: Any = None, kanal: str, token_name: str | None,
                        now: int | None = None) -> dict[str, Any]:
    """Verwerfen = Kommentar mit `bezug` (Text optional). Prüfer, oder wer den Vorschlag über
    seinen Token angelegt hat."""
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        r = _poi(conn, pid, poi, s)
        st = _stand(r)
        _kommentiert(s, st)
        _gueltig(r)
        _vorschlag(conn, pid, poi, schluessel, s)
        t = _text(text) or VERWORFEN_TEXT
        return einfuegen(conn, pid, poi, art="kommentar", text=t, wert=None, intern=False,
                         nutzer=s.nutzer, kanal=kanal, token_name=token_name, now=n,
                         bezug=schluessel)


def zurueckziehen(conn: sqlite3.Connection, pid: str, poi: str, schluessel: str, s: Sicht, *,
                  kanal: str = "web", token_name: str | None = None,
                  now: int | None = None) -> dict[str, Any]:
    """Text (bei Links auch Adresse/Titel) für alle Leser ersetzen; einmalig, nur Verfasser.
    Ein zweites Zurückziehen ändert nichts und liefert den Eintrag erneut."""
    n = int(time.time()) if now is None else now
    with transaktion(conn):
        _poi(conn, pid, poi, s)
        r = conn.execute(_SELECT + " WHERE e.project_id=? AND e.poi_id=? AND e.schluessel=?"
                         " AND (? OR e.intern=0)", (pid, poi, schluessel, int(s.intern))).fetchone()
        if r is None:
            raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
        if r["erstellt_von"] != s.nutzer:
            raise StarletteHTTPException(status_code=403, detail=NUR_VERFASSER)
        if r["art"] not in TEXT_ARTEN:
            raise Ungueltig(NUR_TEXT_ZURUECK)
        if r["zurueckgezogen_am"] is None:
            conn.execute("UPDATE befund_eintraege SET text=NULL, zurueckgezogen_am=?"
                         " WHERE schluessel=?", (n, schluessel))
            r = conn.execute(_SELECT + " WHERE e.schluessel=?", (schluessel,)).fetchone()
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=s.nutzer, project_id=pid,
                               art="eintrag.zurueckgezogen", objekt_id=f"{poi}/{schluessel}",
                               nachher=_protokollform(r), token_name=token_name, now=n)
    return eintrag_json(r)


def schluessel_gueltig(roh: str) -> bool:
    """Hat `roh` die Form eines Eintragsschlüssels (Pfad, Objekt-ID im Protokoll)?"""
    return SCHLUESSEL_RE.fullmatch(roh) is not None


# --- Listen ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Filter:
    """Filter der Liste „Fällig/überfällig“; `zustaendig`: alle | gewerk | ich,
    `frist`: ueberfaellig | woche | ohne."""

    status: str | None = None
    zustaendig: str = "alle"
    frist: str | None = None
    kategorie: str | None = None


def _meins(s: Sicht, st: dict[str, Any], zustaendig: str) -> bool:
    z = st["zustaendig"]
    if zustaendig == "ich":
        return z is not None and z["nutzer_id"] == s.nutzer
    if zustaendig == "gewerk":
        return z is not None and s.gewerk is not None and z["gewerk_id"] == s.gewerk
    return True


def _fristfilter(st: dict[str, Any], frist: str | None, tag: str) -> bool:
    if frist == "ueberfaellig":
        return ueberfaellig(st, tag)
    if frist == "ohne":
        return st["frist"] is None
    if frist == "woche":
        # Diese Kalenderwoche (Europe/Berlin): von heute bis einschließlich Sonntag.
        t = date.fromisoformat(tag)
        sonntag = (t + timedelta(days=6 - t.weekday())).isoformat()
        return st["frist"] is not None and tag <= st["frist"] <= sonntag
    return True


def _passt(s: Sicht, p: Eintrag, st: dict[str, Any], f: Filter, tag: str) -> bool:
    return ((f.status is None or st["status"] == f.status) and _meins(s, st, f.zustaendig)
            and _fristfilter(st, f.frist, tag)
            and (f.kategorie is None or p.daten["category"] == f.kategorie))


def _letzte(conn: sqlite3.Connection, pid: str, intern: bool) -> dict[str, int]:
    """Zeit des jüngsten sichtbaren Eintrags je POI (interne zählen nur für Berechtigte)."""
    return {str(r[0]): int(r[1]) for r in conn.execute(
        "SELECT e.poi_id, MAX(e.erstellt_am) FROM befund_eintraege e WHERE e.project_id=?"
        f" AND (? OR e.intern=0) AND NOT {VORSCHLAGSWELT} GROUP BY e.poi_id", (pid, int(intern)))}


def ueberfaellig_meine(conn: sqlite3.Connection, pid: str, s: Sicht, tag: str) -> int:
    """Überfällige Befunde, für die ich persönlich oder mein Gewerk zuständig bin."""
    return sum(1 for st in staende(conn, pid).values()
               if ueberfaellig(st, tag) and _zustaendig_fuer(s, st))


def befunde(conn: sqlite3.Connection, pid: str, s: Sicht, f: Filter, tag: str,
            pois: list[Eintrag]) -> dict[str, Any]:
    """Liste „Fällig/überfällig“ über die aktiven POIs: überfällig zuerst, dann nach Frist (ohne
    Frist zuletzt), dann nach Anlage."""
    if not aktiviert(conn, pid):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    st_je = staende(conn, pid)
    letzte = _letzte(conn, pid, s.intern)
    # `pois` und `staende` sind zwei Lesevorgänge: ein dazwischen gelöschter POI fällt weg.
    treffer = [(p, st_je[p.id], ueberfaellig(st_je[p.id], tag)) for p in pois
               if p.id in st_je and _passt(s, p, st_je[p.id], f, tag)]
    treffer.sort(key=lambda x: (not x[2], x[1]["frist"] is None, x[1]["frist"] or "",
                                x[0].erstellt_am, x[0].id))
    return {
        "befunde": [{"poi": p.als_json(), "stand": st, "ueberfaellig": ue,
                     "letzter_eintrag_am": letzte.get(p.id)} for p, st, ue in treffer],
        "zaehler": {"ueberfaellig_meine": ueberfaellig_meine(conn, pid, s, tag)},
    }


SPALTEN = ("Status", "Zuständig", "Frist")


def spalten(conn: sqlite3.Connection, pid: str) -> dict[str, list[str]]:
    """POI-ID → [Status, Zuständig, Frist] als Text für CSV und Mängelliste (aktive POIs)."""
    texte = zustaendig_texte(conn, pid)
    return {poi: [STATUS_TEXT[st["status"]], texte.get(poi, ""), st["frist"] or ""]
            for poi, st in staende(conn, pid).items()}


def zustaendig_texte(conn: sqlite3.Connection, pid: str) -> dict[str, str]:
    """POI-ID → „<Gewerk-Kurzzeichen> / <Person>“ für CSV und Mängelliste (aktive POIs)."""
    aus: dict[str, str] = {}
    for r in conn.execute(
        "SELECT p.id, g.kurz, u.name FROM pois p"
        " LEFT JOIN gewerke g ON g.id = p.zustaendig_gewerk_id"
        " LEFT JOIN users u ON u.id = p.zustaendig_nutzer_id"
        " WHERE p.project_id=? AND p.geloescht_am IS NULL AND p.pruefstand <> 'verworfen'", (pid,),
    ):
        aus[str(r["id"])] = " / ".join(str(x) for x in (r["kurz"], r["name"]) if x)
    return aus
