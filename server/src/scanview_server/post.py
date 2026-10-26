"""E-Mail-Benachrichtigung zu Befund-Fäden (F8) — Teil im Webdienst, ohne Netz.

Der Webdienst hat keine Verbindung nach außen (systemd: nur Loopback). Er
1. merkt beim Schreiben eines Faden-Eintrags in derselben Transaktion vor, wer benachrichtigt
   werden soll (`vormerken`, nur Personen mit Einwilligung und bestätigter Adresse),
2. prüft im Hintergrund (`Pumpe`, nie im Request) unmittelbar vor dem Rendern erneut Rechte,
   Einwilligung, bestätigte Adresse, Abmeldung je Faden, Projektschalter und Drosselung,
3. schreibt die fertige Nachricht als JSON (Empfänger + RFC-5322-Bytes) in `<spool>/ausgang/`.
Versendet wird sie vom eigenen Dienst `scanview-post` (post_versand.py), der nur das Spool-
Verzeichnis und SMTP kennt, keine Datenbank. Sein Ergebnis (`<spool>/erledigt/<id>.json`) liest
die Pumpe zurück.

Inhalt der Mail: nur die Art des Ereignisses, ein signierter, kurzlebiger Link zum Faden und die
Abmeldelinks — kein Projektname, kein Befundtitel, kein Text, keine Koordinaten. Der Link meldet
niemanden an (ADR 0006): Er führt nach der normalen Anmeldung zum Faden und nur für das Konto,
an das er ging. Antwort im Browser, nicht per Mail.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from email.utils import format_datetime, make_msgid
from pathlib import Path
from typing import Any

from . import db as dbmod
from . import konten, projektverwaltung
from .db import transaktion

log = logging.getLogger("scanview.post")

EREIGNISSE = ("kommentar", "link", "status", "zustaendig", "anhang")
EREIGNIS_TEXT = {
    "kommentar": "Es gibt einen neuen Kommentar",
    "link": "Es gibt einen neuen Link",
    "anhang": "Es gibt einen neuen Anhang",
    "status": "Der Status hat sich geändert",
    "zustaendig": "Die Zuständigkeit hat sich geändert",
}
LINK_TAGE = 14
ABMELDEN_TAGE = 90
BESTAETIGEN_S = 2 * 24 * 3600
BESTAETIGUNG_ABSTAND_S = 10 * 60  # höchstens eine Bestätigungsmail je Konto in diesem Abstand
JE_STUNDE = 10              # höchstens so viele Mails je Empfänger und Stunde
JE_FADEN_S = 15 * 60        # höchstens eine Mail je Empfänger und Faden in diesem Abstand
# Ausgangszeilen (nur Bezüge, kein Text) leben so lange wie die längsten Links, die auf sie zeigen.
AUFBEWAHRUNG_S = (ABMELDEN_TAGE + 1) * 24 * 3600
PUMPE_S = 15.0
SCHLUESSEL_DATEI = "post-schluessel"
_ARTEN = {"l": "link", "f": "faden_aus", "g": "global_aus"}


class LinkUngueltig(ValueError):
    pass


@dataclass(frozen=True)
class Postdienst:
    """Einstellungen der Instanz: Spool-Verzeichnis, Absender, Origin für Links."""

    spool: Path
    absender: str
    origin: str
    state_dir: Path


# --- Signierte Links ---------------------------------------------------------------------------


def _schluessel(state_dir: Path) -> bytes:
    """Geheimnis für die HMAC-Signatur (32 Byte, 0600, beim ersten Gebrauch erzeugt)."""
    pfad = state_dir / SCHLUESSEL_DATEI
    try:
        roh = pfad.read_bytes()
    except FileNotFoundError:
        roh = secrets.token_bytes(32)
        try:
            fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except FileExistsError:
            return pfad.read_bytes()
        with os.fdopen(fd, "wb") as f:
            f.write(roh)
    if len(roh) != 32:
        raise RuntimeError("Post-Schlüssel beschädigt")
    return roh


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def link_token(state_dir: Path, art: str, mid: str, *, tage: int,
               now: int | None = None) -> str:
    """`art`: l (zum Faden), f (Faden abbestellen), g (alles abbestellen). Der Token nennt nur
    die Ausgangszeile `mid` (zufällig) und den Ablauf — kein Projekt, kein Befund, kein Konto:
    wer die Mail mitliest, erfährt daraus nichts. Aufgelöst wird über die Datenbank."""
    if art not in _ARTEN or not re.fullmatch(r"[0-9a-f]{32}", mid):
        raise ValueError(art)
    ablauf = (int(time.time()) if now is None else now) + tage * 86400
    nutzlast = f"{art}|{mid}|{ablauf}".encode()
    sig = hmac.new(_schluessel(state_dir), nutzlast, hashlib.sha256).digest()[:16]
    return f"{_b64(nutzlast)}.{_b64(sig)}"


@dataclass(frozen=True)
class Link:
    art: str
    uid: int
    pid: str
    poi: str


def link_pruefen(state_dir: Path, token: str, conn: sqlite3.Connection,
                 now: int | None = None) -> Link:
    try:
        teil, sig = token.split(".", 1)
        nutzlast = _unb64(teil)
        soll = hmac.new(_schluessel(state_dir), nutzlast, hashlib.sha256).digest()[:16]
        if len(token) > 600 or not hmac.compare_digest(_unb64(sig), soll):
            raise LinkUngueltig
        art, mid, ablauf = nutzlast.decode().split("|")
        if art not in _ARTEN or int(ablauf) < (int(time.time()) if now is None else now):
            raise LinkUngueltig
    except (ValueError, UnicodeDecodeError) as e:
        raise LinkUngueltig from e
    r = conn.execute("SELECT user_id, project_id, poi_id FROM benachrichtigung_ausgang"
                     " WHERE id=? AND project_id IS NOT NULL", (mid,)).fetchone()
    if r is None:
        raise LinkUngueltig
    return Link(art=art, uid=int(r["user_id"]), pid=str(r["project_id"]), poi=str(r["poi_id"]))


# --- Einwilligung, Bestätigung, Abmelden ---------------------------------------------------------


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def adress_hash(email: str) -> str:
    """Bestätigte Adresse nur als Hash speichern (keine zweite Klartext-Kopie neben users)."""
    return hashlib.sha256(email.strip().casefold().encode("utf-8")).hexdigest()


def zustand(conn: sqlite3.Connection, uid: int) -> dict[str, Any]:
    u = conn.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
    r = conn.execute("SELECT * FROM benachrichtigung_konten WHERE user_id=?", (uid,)).fetchone()
    bestaetigt = (r is not None and r["bestaetigt_hash"] is not None and u is not None
                  and hmac.compare_digest(str(r["bestaetigt_hash"]), adress_hash(str(u["email"]))))
    return {"eingeschaltet": bool(r["eingeschaltet"]) if r else False,
            "bestaetigt": bestaetigt,
            "bestaetigungOffen": bool(r and r["bestaetigung_hash"] and not bestaetigt
                                      and int(r["bestaetigung_ablauf"] or 0) > int(time.time()))}


def einschalten(conn: sqlite3.Connection, dienst: Postdienst, uid: int, an: bool,
                now: int | None = None) -> dict[str, Any]:
    """Einwilligung setzen. Einschalten ohne bestätigte Adresse schickt eine Bestätigungsmail
    (gerendert im Request, versendet vom Postdienst) — erst danach gehen Benachrichtigungen raus."""
    n = _jetzt(now)
    token: str | None = None
    with transaktion(conn):
        conn.execute(
            "INSERT INTO benachrichtigung_konten (user_id, eingeschaltet, geaendert_am)"
            " VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET"
            " eingeschaltet=excluded.eingeschaltet, geaendert_am=excluded.geaendert_am",
            (uid, int(an), n))
        kuerzlich = conn.execute(
            "SELECT 1 FROM benachrichtigung_ausgang WHERE user_id=? AND ereignis='bestaetigung'"
            " AND erstellt_am > ?", (uid, n - BESTAETIGUNG_ABSTAND_S)).fetchone() is not None
        if an and not zustand(conn, uid)["bestaetigt"] and not kuerzlich:
            token = secrets.token_urlsafe(32)
            conn.execute("UPDATE benachrichtigung_konten SET bestaetigung_hash=?,"
                         " bestaetigung_ablauf=? WHERE user_id=?",
                         (hashlib.sha256(token.encode()).hexdigest(), n + BESTAETIGEN_S, uid))
    if token is not None:
        email = str(conn.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()[0])
        mid = secrets.token_hex(16)
        msg = _nachricht(dienst, email, "siteBits: E-Mail-Adresse bestätigen", (
            "Hallo,\n\n"
            "du möchtest Benachrichtigungen zu Befunden per E-Mail bekommen. Bestätige bitte diese "
            "Adresse, angemeldet im selben Konto:\n\n"
            f"{dienst.origin}/#mail-bestaetigen={token}\n\n"
            "Der Link gilt 2 Tage. Hast du das nicht ausgelöst, ignoriere diese Mail; ohne "
            "Bestätigung schickt die Plattform nichts weiter.\n"), None, n)
        _ausgang_anlegen(conn, mid, uid, None, None, None, "bestaetigung", n)
        _spool(dienst, mid, email, msg)
        _status(conn, mid, "gerendert", None, n)
    return zustand(conn, uid)


def bestaetigen(conn: sqlite3.Connection, uid: int, token: str, now: int | None = None) -> bool:
    n = _jetzt(now)
    h = hashlib.sha256(token.encode()).hexdigest()
    with transaktion(conn):
        r = conn.execute("SELECT bestaetigung_hash, bestaetigung_ablauf FROM"
                         " benachrichtigung_konten WHERE user_id=?", (uid,)).fetchone()
        if (r is None or r["bestaetigung_hash"] is None
                or not hmac.compare_digest(str(r["bestaetigung_hash"]), h)
                or int(r["bestaetigung_ablauf"]) < n):
            return False
        email = conn.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()[0]
        conn.execute("UPDATE benachrichtigung_konten SET bestaetigt_hash=?, bestaetigt_am=?,"
                     " bestaetigung_hash=NULL, bestaetigung_ablauf=NULL, geaendert_am=?"
                     " WHERE user_id=?", (adress_hash(str(email)), n, n, uid))
    return True


def loeschen(conn: sqlite3.Connection, uid: int) -> dict[str, int]:
    """Alle Benachrichtigungsdaten eines Kontos entfernen (Anonymisieren, in dessen Transaktion)."""
    return {
        "benachrichtigungKonto": conn.execute(
            "DELETE FROM benachrichtigung_konten WHERE user_id=?", (uid,)).rowcount,
        "benachrichtigungAus": conn.execute(
            "DELETE FROM benachrichtigung_aus WHERE user_id=?", (uid,)).rowcount,
        "benachrichtigungAusgang": conn.execute(
            "DELETE FROM benachrichtigung_ausgang WHERE user_id=?", (uid,)).rowcount,
    }


def faden_aus(conn: sqlite3.Connection, uid: int, pid: str, poi: str, aus: bool,
              now: int | None = None) -> None:
    if aus:
        conn.execute("INSERT OR IGNORE INTO benachrichtigung_aus (user_id, project_id, poi_id,"
                     " seit) VALUES (?,?,?,?)", (uid, pid, poi, _jetzt(now)))
    else:
        conn.execute("DELETE FROM benachrichtigung_aus WHERE user_id=? AND project_id=?"
                     " AND poi_id=?", (uid, pid, poi))


def ist_faden_aus(conn: sqlite3.Connection, uid: int, pid: str, poi: str) -> bool:
    return conn.execute("SELECT 1 FROM benachrichtigung_aus WHERE user_id=? AND project_id=?"
                        " AND poi_id=?", (uid, pid, poi)).fetchone() is not None


def abmelden(conn: sqlite3.Connection, link: Link) -> None:
    """Abmelden per Link aus der Mail (ohne Anmeldung): nur weniger Mail, nie mehr."""
    if link.art == "g":
        conn.execute("UPDATE benachrichtigung_konten SET eingeschaltet=0, geaendert_am=?"
                     " WHERE user_id=?", (int(time.time()), link.uid))
    elif link.art == "f":
        faden_aus(conn, link.uid, link.pid, link.poi, True)
    else:
        raise LinkUngueltig


# --- Vormerken (in der Transaktion des Faden-Eintrags) -------------------------------------------


def _beteiligte(conn: sqlite3.Connection, pid: str, poi: str, art: str) -> set[int]:
    p = conn.execute("SELECT erstellt_von, zustaendig_gewerk_id, zustaendig_nutzer_id FROM pois"
                     " WHERE project_id=? AND id=?", (pid, poi)).fetchone()
    if p is None:
        return set()
    zustaendig: set[int] = set()
    if p["zustaendig_nutzer_id"] is not None:
        zustaendig.add(int(p["zustaendig_nutzer_id"]))
    elif p["zustaendig_gewerk_id"] is not None:
        zustaendig |= {int(r[0]) for r in conn.execute(
            "SELECT user_id FROM project_roles WHERE project_id=? AND gewerk_id=?",
            (pid, p["zustaendig_gewerk_id"]))}
    if art == "zustaendig":
        return zustaendig
    anleger = {int(p["erstellt_von"])} if p["erstellt_von"] is not None else set()
    if art == "status":
        return anleger | zustaendig
    folgen = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT erstellt_von FROM befund_eintraege WHERE project_id=? AND poi_id=?"
        " AND erstellt_von IS NOT NULL AND art IN ('kommentar', 'link', 'anhang', 'status')",
        (pid, poi))}
    return anleger | zustaendig | folgen


def vormerken(conn: sqlite3.Connection, pid: str, poi: str, *, art: str, eintrag: str,
              autor: int | None, now: int) -> int:
    """Ausgangszeilen für alle Beteiligten mit Einwilligung (außer dem Verfasser). Ohne
    eingeschaltete Instanz gibt es keine Einwilligungen, also auch keine Zeilen."""
    if art not in EREIGNISSE:
        return 0
    kandidaten = _beteiligte(conn, pid, poi, art) - ({autor} if autor is not None else set())
    if not kandidaten:
        return 0
    mit = {int(r[0]) for r in conn.execute(
        "SELECT user_id FROM benachrichtigung_konten WHERE eingeschaltet=1"
        " AND bestaetigt_hash IS NOT NULL AND user_id IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(kandidaten)),))}
    for uid in sorted(mit):
        _ausgang_anlegen(conn, secrets.token_hex(16), uid, pid, poi, eintrag, art, now)
    return len(mit)


def _ausgang_anlegen(conn: sqlite3.Connection, mid: str, uid: int, pid: str | None,
                     poi: str | None, eintrag: str | None, ereignis: str, n: int) -> None:
    conn.execute("INSERT INTO benachrichtigung_ausgang (id, user_id, project_id, poi_id, eintrag,"
                 " ereignis, status, erstellt_am, geaendert_am) VALUES (?,?,?,?,?,?,?,?,?)",
                 (mid, uid, pid, poi, eintrag, ereignis, "wartend", n, n))


def _status(conn: sqlite3.Connection, mid: str, status: str, grund: str | None, n: int) -> None:
    conn.execute("UPDATE benachrichtigung_ausgang SET status=?, grund=?, geaendert_am=?"
                 " WHERE id=?", (status, grund, n, mid))


# --- Rendern und Spool ----------------------------------------------------------------------------


def _adresse(roh: str) -> Address:
    """Strukturiert prüfen: genau eine Adresse, keine Zeilenumbrüche/Steuerzeichen (S4)."""
    if any(ord(c) < 32 or ord(c) == 127 for c in roh) or "," in roh:
        raise ValueError("Adresse mit Steuerzeichen oder mehreren Empfängern")
    a = Address(addr_spec=roh)
    if not a.username or not a.domain:
        raise ValueError("Adresse unvollständig")
    return a


# RFC 5322 erlaubt 998 Zeichen je Kopfzeile. Mit der Vorgabe 78 kodiert `email` lange Werte ohne
# Leerzeichen (List-Unsubscribe mit Link) als RFC-2047-Wörter — die erkennt kein Mailprogramm.
_POLICY = SMTP.clone(max_line_length=998)


def _nachricht(dienst: Postdienst, an: str, betreff: str, text: str,
               liste_ab: str | None, n: int) -> EmailMessage:
    msg = EmailMessage(policy=_POLICY)
    msg["From"] = Address(display_name="siteBits", addr_spec=_adresse(dienst.absender).addr_spec)
    msg["To"] = _adresse(an)
    msg["Subject"] = betreff
    msg["Date"] = format_datetime(datetime.fromtimestamp(n, UTC))
    domain = _adresse(dienst.absender).domain
    msg["Message-ID"] = make_msgid(domain=domain)
    msg["Auto-Submitted"] = "auto-generated"
    msg["X-Auto-Response-Suppress"] = "All"
    if liste_ab is not None:
        msg["List-Unsubscribe"] = f"<{liste_ab}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(text)
    return msg


def _spool(dienst: Postdienst, mid: str, an: str, msg: EmailMessage) -> None:
    ausgang = dienst.spool / "ausgang"
    ausgang.mkdir(mode=0o750, parents=True, exist_ok=True)
    daten = json.dumps({"id": mid, "an": _adresse(an).addr_spec,
                        "nachricht": base64.b64encode(msg.as_bytes()).decode("ascii")})
    tmp = ausgang / f".{mid}.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o640)
    # Die Unit des Webdienstes hat UMask=0077; scanview-post liest über die Gruppe des
    # setgid-Spoolordners — deshalb die Rechte ausdrücklich setzen.
    os.fchmod(fd, 0o640)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(daten)
    os.replace(tmp, ausgang / f"{mid}.json")


def benachrichtigung_text(dienst: Postdienst, ereignis: str, mid: str) -> tuple[str, str, str]:
    """(Betreff, Text, Abmelde-URL für List-Unsubscribe) — ohne Projektinhalte."""
    zum = link_token(dienst.state_dir, "l", mid, tage=LINK_TAGE)
    ab_faden = link_token(dienst.state_dir, "f", mid, tage=ABMELDEN_TAGE)
    ab_alles = link_token(dienst.state_dir, "g", mid, tage=ABMELDEN_TAGE)
    o = dienst.origin
    text = (
        "Hallo,\n\n"
        f"{EREIGNIS_TEXT[ereignis]} an einem Befund, an dem du beteiligt bist.\n\n"
        f"Ansehen und antworten (Anmeldung nötig, 14 Tage gültig):\n{o}/api/mail/{zum}\n\n"
        f"Für diesen Befund keine Mails mehr:\n{o}/api/mail/abmelden/{ab_faden}\n"
        f"Gar keine Mails mehr:\n{o}/api/mail/abmelden/{ab_alles}\n\n"
        "Diese Nachricht wurde automatisch erzeugt. Antworten auf diese Mail kommen nicht an.\n")
    return "siteBits: Neues an einem Befund", text, f"{o}/api/mail/abmelden/{ab_faden}"


@dataclass(frozen=True)
class Empfaenger:
    uid: int
    email: str


def _empfaenger(conn: sqlite3.Connection, uid: int) -> Empfaenger | None:
    u = conn.execute("SELECT id, email, disabled FROM users WHERE id=?", (uid,)).fetchone()
    if u is None or u["disabled"]:
        return None
    return Empfaenger(uid=int(u["id"]), email=str(u["email"]))


def _grund(conn: sqlite3.Connection, katalog: Any, r: sqlite3.Row, n: int) -> str | None:
    """Warum diese Mail nicht (mehr) raus darf; None = darf. Rechte zum Zeitpunkt des Renderns."""
    from . import faden

    uid, pid, poi = int(r["user_id"]), str(r["project_id"]), str(r["poi_id"])
    e = _empfaenger(conn, uid)
    if e is None:
        return "konto"
    z = zustand(conn, uid)
    if not z["eingeschaltet"]:
        return "aus"
    if not z["bestaetigt"]:
        return "unbestaetigt"
    if ist_faden_aus(conn, uid, pid, poi):
        return "faden_aus"
    einst = projektverwaltung.einstellungen(conn, pid)
    if not einst.benachrichtigung:
        return "projekt_aus"
    if katalog.get(pid) is None:
        return "rechte"
    n_obj = konten.Nutzer(id=uid, name="", email=e.email, is_admin=bool(conn.execute(
        "SELECT is_admin FROM users WHERE id=?", (uid,)).fetchone()[0]))
    rolle = projektverwaltung.wirksame_rolle(konten.rolle(conn, n_obj, pid), einst)
    if rolle is None or not faden.aktiviert(conn, pid):
        return "rechte"
    p = conn.execute("SELECT 1 FROM pois WHERE project_id=? AND id=? AND geloescht_am IS NULL"
                     " AND pruefstand='gueltig'", (pid, poi)).fetchone()
    if p is None:
        return "rechte"
    eintrag = conn.execute("SELECT intern, zurueckgezogen_am FROM befund_eintraege WHERE"
                           " schluessel=?", (r["eintrag"],)).fetchone()
    if eintrag is None:
        return "rechte"
    if eintrag["intern"] and not faden.sicht(conn, pid, uid, rolle).intern:
        return "rechte"
    if eintrag["zurueckgezogen_am"] is not None:
        return "zurueckgezogen"
    stunde = conn.execute("SELECT COUNT(*) FROM benachrichtigung_ausgang WHERE user_id=?"
                          " AND status IN ('gerendert','gesendet','unklar') AND geaendert_am > ?",
                          (uid, n - 3600)).fetchone()[0]
    if stunde >= JE_STUNDE:
        return "drossel"
    if conn.execute("SELECT 1 FROM benachrichtigung_ausgang WHERE user_id=? AND project_id=?"
                    " AND poi_id=? AND status IN ('gerendert','gesendet','unklar')"
                    " AND geaendert_am > ?", (uid, pid, poi, n - JE_FADEN_S)).fetchone():
        return "drossel"
    return None


def pumpen(conn: sqlite3.Connection, dienst: Postdienst, katalog: Any,
           now: int | None = None) -> dict[str, int]:
    """Ein Durchlauf: wartende Zeilen prüfen und rendern, Ergebnisse des Postdienstes
    übernehmen, Altes löschen. Gibt Zähler zurück (für Tests und das Journal)."""
    n = _jetzt(now)
    zaehler = {"gerendert": 0, "verworfen": 0, "erledigt": 0}
    for r in conn.execute("SELECT * FROM benachrichtigung_ausgang WHERE status='wartend'"
                          " ORDER BY erstellt_am LIMIT 200").fetchall():
        with transaktion(conn):
            grund = _grund(conn, katalog, r, n)
            if grund is not None:
                _status(conn, r["id"], "verworfen", grund, n)
                zaehler["verworfen"] += 1
                continue
            e = _empfaenger(conn, int(r["user_id"]))
            assert e is not None
            betreff, text, ab = benachrichtigung_text(dienst, r["ereignis"], r["id"])
            try:
                msg = _nachricht(dienst, e.email, betreff, text, ab, n)
                _spool(dienst, r["id"], e.email, msg)
            except ValueError:
                _status(conn, r["id"], "verworfen", "adresse", n)
                zaehler["verworfen"] += 1
                continue
            _status(conn, r["id"], "gerendert", None, n)
            zaehler["gerendert"] += 1
    zaehler["erledigt"] = ergebnisse_uebernehmen(conn, dienst, n)
    conn.execute("DELETE FROM benachrichtigung_ausgang WHERE geaendert_am < ? AND status NOT IN"
                 " ('wartend', 'gerendert')", (n - AUFBEWAHRUNG_S,))
    if any(zaehler.values()):
        log.info("Post: %s", zaehler)
    return zaehler


_ERGEBNISSE = ("gesendet", "fehlgeschlagen", "unklar")


def ergebnisse_uebernehmen(conn: sqlite3.Connection, dienst: Postdienst, n: int) -> int:
    """`erledigt/<id>.json` des Postdienstes (fremd: nur Status aus fester Liste) übernehmen und
    die Nachricht aus dem Ausgang löschen."""
    erledigt = dienst.spool / "erledigt"
    if not erledigt.is_dir():
        return 0
    k = 0
    for r in conn.execute("SELECT id FROM benachrichtigung_ausgang WHERE status='gerendert'"
                          ).fetchall():
        datei = erledigt / f"{r['id']}.json"
        try:
            if datei.is_symlink() or datei.stat().st_size > 1024:
                continue
            status = json.loads(datei.read_text(encoding="utf-8")).get("status")
        except (OSError, ValueError, AttributeError):
            continue
        if status not in _ERGEBNISSE:
            continue
        _status(conn, r["id"], status, None, n)
        (dienst.spool / "ausgang" / f"{r['id']}.json").unlink(missing_ok=True)
        k += 1
    return k


class Pumpe:
    """Hintergrund-Thread im Webdienst (eigene DB-Verbindung), alle PUMPE_S Sekunden."""

    def __init__(self, db_path: Path, dienst: Postdienst, katalog: Any) -> None:
        self._db_path, self._dienst, self._katalog = db_path, dienst, katalog
        self._stopp = threading.Event()
        self._t = threading.Thread(target=self._lauf, name="post-pumpe", daemon=True)

    def starten(self) -> None:
        self._t.start()

    def beenden(self) -> None:
        self._stopp.set()
        self._t.join(timeout=10)

    def _lauf(self) -> None:
        while not self._stopp.wait(PUMPE_S):
            try:
                conn = dbmod.connect(self._db_path)
                try:
                    pumpen(conn, self._dienst, self._katalog)
                finally:
                    conn.close()
            except (sqlite3.Error, OSError, ValueError, RuntimeError, KeyError) as e:
                # Nie Inhalte ins Journal (Adressen, Texte): nur die Art des Fehlers.
                log.error("Post-Pumpe: Durchlauf fehlgeschlagen (%s)", type(e).__name__)
