"""Zugänge verwalten — gemeinsam für CLI (kanal 'cli') und Admin-API (kanal 'web').

Jede Funktion ändert und protokolliert in EINER Transaktion (db.transaktion) und ruft keine
Funktion mit eigener Transaktion auf (SQLite verschachtelt kein BEGIN). Einziger Schreibweg an
users/project_roles/invites: `konten.py` hat dafür keine eigenen öffentlichen Funktionen mehr
— nur noch `einladung_anlegen` (protokolliert NICHT selbst; das
Ereignis `einladung.angelegt` schreibt `einladen` hier in derselben Transaktion),
`einladung_einloesen` (protokolliert selbst) und Lesefunktionen. Dazu kommen die Schreibwege der
Projektleitung (`beteiligter_setzen`, `zugriff_erteilen`) und `konten.recht_anheben`, jeweils
innerhalb einer laufenden Transaktion. Im Protokoll stehen keine
E-Mail-Adressen, Namen oder Tokens: Einladungen erscheinen als einladung_id (erste 16 Zeichen
des Token-Hashes), Konten als Nutzer-ID.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from . import apitoken, konten, passwort, protokoll
from .db import transaktion
from .projektverwaltung import OFFENE_EINLADUNGEN_MAX
from .tokens import hash_token

ANONYMISIERT = "Konto wurde anonymisiert."


class KontoAnonymisiert(ValueError):
    """Anonymisierte Konten sind endgültig: kein Entsperren,
    keine Rechte, keine Einladung auf die Ersatzadresse, kein zweites Anonymisieren."""

    def __init__(self) -> None:
        super().__init__(ANONYMISIERT)


def ist_anonymisiert(conn: sqlite3.Connection, uid: int) -> bool:
    """Kennzeichen ohne Schemaänderung: das unveränderliche Ereignis `konto.anonymisiert`."""
    return conn.execute(
        "SELECT 1 FROM ereignisse WHERE art='konto.anonymisiert' AND objekt_id=? LIMIT 1",
        (str(uid),),
    ).fetchone() is not None


def anonymisierte_ids(conn: sqlite3.Connection) -> set[int]:
    return {
        int(r[0]) for r in conn.execute(
            "SELECT objekt_id FROM ereignisse WHERE art='konto.anonymisiert'",
        )
    }


def _nicht_anonymisiert(conn: sqlite3.Connection, uid: int | None) -> None:
    if uid is not None and ist_anonymisiert(conn, uid):
        raise KontoAnonymisiert


def einladung_id(token_hash: str) -> str:
    return token_hash[:16]


def _jetzt(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _sitzungen_beenden(conn: sqlite3.Connection, user_id: int) -> int:
    """Nur noch hier aufgerufen — kein eigener öffentlicher Schreibweg mehr in konten.py."""
    return conn.execute("DELETE FROM sessions WHERE user_id=?", (user_id,)).rowcount


def einladen(
    conn: sqlite3.Connection, *, email: str, name: str, grants: dict[str, str],
    is_admin: bool = False, zuruecksetzen: bool = False, von: int | None, kanal: str,
    now: int | None = None, project_id: str | None = None, gewerk_id: int | None = None,
) -> str:
    zweck = "zuruecksetzen" if zuruecksetzen else "neu"
    with transaktion(conn):
        _nicht_anonymisiert(conn, konten.konto_id(conn, email))
        if project_id is not None:
            _gewerk_im_projekt(conn, project_id, gewerk_id)
            _offene_einladungen_pruefen(conn, project_id, _jetzt(now))
        token = konten.einladung_anlegen(
            conn, email=email, name=name, grants=grants, is_admin=is_admin, purpose=zweck,
            created_by=von, now=now, project_id=project_id, gewerk_id=gewerk_id,
        )
        nachher: dict[str, object] = {"zweck": zweck, "admin": is_admin, "rechte": grants}
        if project_id is not None:
            nachher["gewerk"] = gewerk_id
        protokoll.ereignis(
            conn, kanal=kanal, nutzer_id=von, art="einladung.angelegt", project_id=project_id,
            objekt_id=einladung_id(hash_token(token)), nachher=nachher, now=now,
        )
    return token


def einladungen_widerrufen(
    conn: sqlite3.Connection, email: str, *, von: int | None, kanal: str, now: int | None = None,
) -> int:
    n = _jetzt(now)
    with transaktion(conn):
        offen = [
            (str(r["token_hash"]), r["project_id"]) for r in conn.execute(
                "SELECT token_hash, project_id FROM invites WHERE email=? AND used_at IS NULL",
                (email.strip(),),
            ).fetchall()
        ]
        for h, projekt in offen:
            conn.execute("UPDATE invites SET used_at=? WHERE token_hash=?", (n, h))
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="einladung.widerrufen",
                               project_id=projekt, objekt_id=einladung_id(h), now=n)
    return len(offen)


def sperren(
    conn: sqlite3.Connection, uid: int, *, von: int | None, kanal: str, now: int | None = None,
) -> int:
    with transaktion(conn):
        conn.execute("UPDATE users SET disabled=1 WHERE id=?", (uid,))
        beendet = _sitzungen_beenden(conn, uid)
        tokens = apitoken.alle_widerrufen(conn, uid, now=now)
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="konto.gesperrt",
                           objekt_id=str(uid),
                           nachher={"sitzungenBeendet": beendet, "tokensWiderrufen": tokens},
                           now=now)
    return beendet


def entsperren(
    conn: sqlite3.Connection, uid: int, *, von: int | None, kanal: str, now: int | None = None,
) -> None:
    with transaktion(conn):
        _nicht_anonymisiert(conn, uid)
        conn.execute("UPDATE users SET disabled=0 WHERE id=?", (uid,))
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="konto.entsperrt",
                           objekt_id=str(uid), now=now)


def abmelden_ueberall(
    conn: sqlite3.Connection, uid: int, *, von: int | None, kanal: str, now: int | None = None,
) -> int:
    with transaktion(conn):
        beendet = _sitzungen_beenden(conn, uid)
        tokens = apitoken.alle_widerrufen(conn, uid, now=now)
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="konto.abgemeldet",
                           objekt_id=str(uid),
                           nachher={"sitzungenBeendet": beendet, "tokensWiderrufen": tokens},
                           now=now)
    return beendet


def rechte_entziehen(
    conn: sqlite3.Connection, uid: int, project_id: str | None, *, von: int | None, kanal: str,
    now: int | None = None,
) -> int:
    """Entzieht das Recht auf ein Projekt (oder mit None auf alle), ein Ereignis je Recht."""
    with transaktion(conn):
        if project_id is None:
            rows = conn.execute(
                "SELECT project_id, role FROM project_roles WHERE user_id=? ORDER BY project_id",
                (uid,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT project_id, role FROM project_roles WHERE user_id=? AND project_id=?",
                (uid, project_id),
            ).fetchall()
        for r in rows:
            conn.execute("DELETE FROM project_roles WHERE user_id=? AND project_id=?",
                         (uid, r["project_id"]))
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="recht.entzogen",
                               project_id=r["project_id"], objekt_id=str(uid),
                               vorher=r["role"], now=now)
    return len(rows)


def einladung_widerrufen(
    conn: sqlite3.Connection, eid: str, *, von: int | None, kanal: str, nur_projekt: str | None,
    now: int | None = None,
) -> bool:
    """Widerruft genau eine offene, nicht abgelaufene Einladung; False sonst. `nur_projekt`
    (Pflichtangabe, damit kein Aufrufer die Projektgrenze vergisst): mit Projekt-ID nur eine
    Einladung genau dieses Projekts (Projektleitung), mit None jede (Admin-Seite)."""
    n = _jetzt(now)
    with transaktion(conn):
        if nur_projekt is None:
            row = conn.execute(
                "SELECT token_hash, project_id FROM invites WHERE substr(token_hash, 1, 16)=?"
                " AND used_at IS NULL AND expires_at > ?", (eid, n)).fetchone()
        else:
            row = conn.execute(
                "SELECT token_hash, project_id FROM invites WHERE substr(token_hash, 1, 16)=?"
                " AND used_at IS NULL AND expires_at > ? AND project_id=?",
                (eid, n, nur_projekt)).fetchone()
        if row is None:
            return False
        conn.execute("UPDATE invites SET used_at=? WHERE token_hash=?", (n, row["token_hash"]))
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="einladung.widerrufen",
                           project_id=row["project_id"], objekt_id=eid, now=n)
    return True


def recht_setzen(
    conn: sqlite3.Connection, uid: int, project_id: str, rolle: str | None, *, von: int | None,
    kanal: str, now: int | None = None,
) -> str | None:
    """Setzt (None: entzieht) die Rolle, gibt die vorige zurück; unverändert: kein Ereignis."""
    if rolle is not None and rolle not in konten.ROLLEN:
        raise ValueError(f"Rolle {rolle!r} unbekannt.")
    with transaktion(conn):
        if rolle is not None:
            _nicht_anonymisiert(conn, uid)
        row = conn.execute(
            "SELECT role FROM project_roles WHERE user_id=? AND project_id=?", (uid, project_id),
        ).fetchone()
        alt = None if row is None else str(row["role"])
        if alt == rolle:
            return alt
        if rolle is None:
            conn.execute("DELETE FROM project_roles WHERE user_id=? AND project_id=?",
                         (uid, project_id))
        else:
            conn.execute(
                "INSERT INTO project_roles (user_id, project_id, role) VALUES (?,?,?)"
                " ON CONFLICT(user_id, project_id) DO UPDATE SET role=excluded.role",
                (uid, project_id, rolle),
            )
        protokoll.ereignis(
            conn, kanal=kanal, nutzer_id=von,
            art="recht.entzogen" if rolle is None else "recht.vergeben",
            project_id=project_id, objekt_id=str(uid), vorher=alt, nachher=rolle, now=now,
        )
    return alt


LEITUNG_ZIEL_GESCHUETZT = "Rollen von Admins und Projektleitungen ändert nur die Instanzverwaltung."
LEITUNG_ROLLE_VERBOTEN = "Die Projektleitung vergibt nur Betrachter, Rückmelder und Bearbeiter."
GEWERK_UNBEKANNT = "Dieses Gewerk gibt es in diesem Projekt nicht."
EINLADUNGEN_ZU_VIELE = (
    f"Dieses Projekt hat schon {OFFENE_EINLADUNGEN_MAX} offene Einladungen. Widerrufen Sie "
    "nicht mehr benötigte Einladungen, bevor Sie weitere anlegen."
)
NICHT_BETEILIGT = "nicht beteiligt"


class Verboten(Exception):
    """Eskalationsschutz: Diese Änderung darf die Projektleitung nicht (Route → 403)."""


class GewerkUnbekannt(ValueError):
    def __init__(self) -> None:
        super().__init__(GEWERK_UNBEKANNT)


def _gewerk_im_projekt(conn: sqlite3.Connection, project_id: str, gewerk_id: int | None) -> None:
    """Maßgebliche Gewerk-Prüfung — in der Schreibtransaktion des Aufrufers (BEGIN IMMEDIATE),
    damit ein parallel gelöschtes Gewerk nicht mehr zugeordnet wird."""
    if gewerk_id is not None and conn.execute(
        "SELECT 1 FROM gewerke WHERE id=? AND project_id=?", (gewerk_id, project_id),
    ).fetchone() is None:
        raise GewerkUnbekannt


def _offene_einladungen_pruefen(conn: sqlite3.Connection, project_id: str, jetzt: int) -> None:
    offen = conn.execute(
        "SELECT COUNT(*) FROM invites WHERE project_id=? AND used_at IS NULL AND expires_at > ?",
        (project_id, jetzt),
    ).fetchone()[0]
    if offen >= OFFENE_EINLADUNGEN_MAX:
        raise ValueError(EINLADUNGEN_ZU_VIELE)


def beteiligter_setzen(
    conn: sqlite3.Connection, uid: int, project_id: str, rolle: str | None, gewerk_id: int | None,
    *, als_leitung: bool, von: int, kanal: str, now: int | None = None,
) -> str | None:
    """Setzt Rolle und Gewerk eines Beteiligten oder entzieht die Rolle (`rolle=None`); gibt die
    vorige Rolle zurück. Nur Konten, die im Projekt schon eine Rolle haben (sonst LookupError).
    `als_leitung`: Ziel darf weder Admin noch Projektleitung sein (auch nicht man selbst), neue
    Rolle nur betrachter/rueckmelder/bearbeiter — geprüft gegen den Stand in DIESER Transaktion."""
    if rolle is not None and rolle not in konten.ROLLEN:
        raise ValueError(f"Rolle {rolle!r} unbekannt.")
    n = _jetzt(now)
    with transaktion(conn):
        row = conn.execute(
            "SELECT r.role, r.gewerk_id, u.is_admin FROM project_roles r JOIN users u"
            " ON u.id = r.user_id WHERE r.user_id=? AND r.project_id=?", (uid, project_id),
        ).fetchone()
        if row is None:
            raise LookupError(NICHT_BETEILIGT)
        alt = str(row["role"])
        if als_leitung and (row["is_admin"] or alt not in konten.LEITUNG_VERGIBT):
            raise Verboten(LEITUNG_ZIEL_GESCHUETZT)
        if als_leitung and rolle is not None and rolle not in konten.LEITUNG_VERGIBT:
            raise Verboten(LEITUNG_ROLLE_VERBOTEN)
        if rolle is None:
            conn.execute("DELETE FROM project_roles WHERE user_id=? AND project_id=?",
                         (uid, project_id))
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="recht.entzogen",
                               project_id=project_id, objekt_id=str(uid), vorher=alt, now=n)
            return alt
        _gewerk_im_projekt(conn, project_id, gewerk_id)
        if rolle != alt:
            _nicht_anonymisiert(conn, uid)
            conn.execute("UPDATE project_roles SET role=? WHERE user_id=? AND project_id=?",
                         (rolle, uid, project_id))
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="recht.vergeben",
                               project_id=project_id, objekt_id=str(uid), vorher=alt,
                               nachher=rolle, now=n)
        if gewerk_id != row["gewerk_id"]:
            conn.execute("UPDATE project_roles SET gewerk_id=? WHERE user_id=? AND project_id=?",
                         (gewerk_id, uid, project_id))
            protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="beteiligter.gewerk",
                               project_id=project_id, objekt_id=str(uid),
                               vorher=row["gewerk_id"], nachher=gewerk_id, now=n)
    return alt


ERTEILT = "Konto vorhanden — Zugriff erteilt."
BESTEHT = "Konto vorhanden — Zugriff besteht bereits (eine gleiche oder höhere Rolle bleibt)."
GESPERRT_HINWEIS = " Das Konto ist gesperrt; freischalten kann nur die Instanzverwaltung."


@dataclass(frozen=True)
class Erteilt:
    art: str  # "zugriff-erteilt" | "zugriff-besteht"
    gesperrt: bool


def erteilt_text(e: Erteilt) -> str:
    text = ERTEILT if e.art == "zugriff-erteilt" else BESTEHT
    return text + (GESPERRT_HINWEIS if e.gesperrt else "")


def zugriff_erteilen(
    conn: sqlite3.Connection, uid: int, project_id: str, rolle: str, gewerk_id: int | None, *,
    als_leitung: bool, von: int, kanal: str, now: int | None = None,
) -> Erteilt:
    """Einladung an eine E-Mail mit bestehendem Konto: kein Zurücksetzen, nur das Projektrecht —
    nie herabstufend (konten.recht_anheben). Admin-Konten bleiben unberührt, gesperrte gesperrt."""
    if als_leitung and rolle not in konten.LEITUNG_VERGIBT:
        raise Verboten(LEITUNG_ROLLE_VERBOTEN)
    n = _jetzt(now)
    with transaktion(conn):
        _nicht_anonymisiert(conn, uid)
        _gewerk_im_projekt(conn, project_id, gewerk_id)
        u = conn.execute("SELECT is_admin, disabled FROM users WHERE id=?", (uid,)).fetchone()
        if u is None:
            raise LookupError(NICHT_BETEILIGT)
        gesperrt = bool(u["disabled"])
        if u["is_admin"]:
            return Erteilt("zugriff-besteht", gesperrt)
        geaendert = konten.recht_anheben(conn, uid, project_id, rolle, gewerk_id=gewerk_id,
                                         von=von, kanal=kanal, now=n)
    return Erteilt("zugriff-erteilt" if geaendert else "zugriff-besteht", gesperrt)


def anonymisieren(
    conn: sqlite3.Connection, uid: int, *, von: int | None, kanal: str, now: int | None = None,
) -> dict[str, int]:
    """DSGVO-Löschweg (Final-Fix C/D): entfernt die Personendaten eines Kontos, ohne die Zeile zu
    löschen — `ereignisse.nutzer_id` verweist ohne Cascade auf `users` (append-only), das Protokoll
    zeigt danach „Gelöschtes Konto <id>“.

    In EINER Transaktion: Name → „Gelöschtes Konto <id>“, E-Mail → `geloescht-<id>@invalid`,
    gesperrt, kein Admin; Sitzungen, Passkeys, Passwort, Projektrechte, Challenges und die
    Passwort-Drosselzeile (Schlüssel aus der E-Mail) gelöscht; offene Einladungen an die Adresse
    gelöscht, eingelöste/widerrufene Einladungen an die Adresse geschwärzt (sie trügen sonst E-Mail
    und Namen weiter; die Zeile bleibt als Nachweis, `einladung_id` im Protokoll bleibt gültig).
    Protokolliert als `konto.anonymisiert` nur mit Anzahlen — ohne Personendaten. Endgültig:
    dieses Ereignis ist das Kennzeichen für `KontoAnonymisiert` (Entsperren, Rechte vergeben,
    Einladung auf die Ersatzadresse, erneutes Anonymisieren scheitern).
    Freitext in POI-Notizen/Projektbeschreibungen und deren Protokoll-Vorher/Nachher bleibt
    unberührt (offene Entscheidung, deploy/platform/README.md) — dieselbe offene Entscheidung gilt
    auch für Token-Namen: `token.erstellt`/`token.widerrufen` im (unveränderlichen)
    Protokoll tragen den vom Nutzer gewählten Namen weiter, auch nach dieser Löschung. Nur die
    `api_tokens`-Zeilen selbst (können denselben Namen tragen) werden hier entfernt.
    `git_name_zeigen` geht mit auf 0 zurück (Klarname nur mit Einwilligung, ADR 0011) — ein
    anonymisiertes Konto zeigt in künftigen Git-Exporten wieder nur den pseudonymen Autor, nie
    den (gelöschten) Namen.
    """
    name = f"Gelöschtes Konto {uid}"
    ersatz = f"geloescht-{uid}@invalid"
    with transaktion(conn):
        row = conn.execute("SELECT email FROM users WHERE id=?", (uid,)).fetchone()
        if row is None:
            raise LookupError(f"Kein Konto mit der ID {uid}.")
        _nicht_anonymisiert(conn, uid)
        email = str(row["email"])
        out = {"sitzungen": _sitzungen_beenden(conn, uid)}
        out["passkeys"] = conn.execute("DELETE FROM passkeys WHERE user_id=?", (uid,)).rowcount
        out["passwort"] = conn.execute("DELETE FROM passwords WHERE user_id=?", (uid,)).rowcount
        out["tokens"] = conn.execute("DELETE FROM api_tokens WHERE user_id=?", (uid,)).rowcount
        out["rechte"] = conn.execute("DELETE FROM project_roles WHERE user_id=?", (uid,)).rowcount
        out["einladungenGeloescht"] = conn.execute(
            "DELETE FROM invites WHERE email=? AND used_at IS NULL", (email,),
        ).rowcount
        out["einladungenGeschwaerzt"] = conn.execute(
            "UPDATE invites SET email=?, name=? WHERE email=?", (ersatz, name, email),
        ).rowcount
        conn.execute("DELETE FROM challenges WHERE user_id=?", (uid,))
        # E-Mail-Benachrichtigung (F8): Einwilligung, Abbestellungen, Ausgang ohne Text.
        from . import post

        out.update(post.loeschen(conn, uid))
        conn.execute("DELETE FROM throttle WHERE key=?",
                     (passwort.schluessel(email.strip().lower()),))
        conn.execute(
            "UPDATE users SET name=?, email=?, disabled=1, is_admin=0, git_name_zeigen=0"
            " WHERE id=?",
            (name, ersatz, uid),
        )
        protokoll.ereignis(conn, kanal=kanal, nutzer_id=von, art="konto.anonymisiert",
                           objekt_id=str(uid), nachher=out, now=now)
    return out
