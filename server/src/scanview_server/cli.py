"""Kommandozeile: serve (uvicorn), einladen (Einladungslink), nutzer (Übersicht), Zugänge
verwalten (widerrufen, sperren, entsperren, entziehen, abmelden-ueberall) und git-export
(Bearbeitungen je Projekt nach Git)."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sqlite3
import sys
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI

from . import db, gitexport, konten, verwaltung
from .config import Settings
from .katalog import PROJECT_ID_RE, Katalog
from .web import create_app

_LOG = logging.getLogger("scanview.cli")

# SCANVIEW_LOG_LEVEL steuert die
# Ausfuehrlichkeit des Journals (journalctl -u scanview-server). Ohne diese Konfiguration bleibt
# der Python-Root-Logger unkonfiguriert (Default-Stufe WARNING) — die INFO/WARNING-Zeilen aus
# katalog.py und passwort.py haetten dann nie einen Handler und erreichen das Journal nie.
_LOG_STUFEN = ("DEBUG", "INFO", "WARNING")


def _konfiguriere_logging() -> None:
    roh = os.environ.get("SCANVIEW_LOG_LEVEL", "INFO").strip().upper()
    stufe = roh if roh in _LOG_STUFEN else "INFO"
    # Muss VOR create_app() laufen: das ruft Katalog.laden() bereits beim Start auf, dessen
    # log.info("Katalog: %d Pakete", ...) soll ebenfalls durchkommen.
    logging.basicConfig(level=stufe, format="%(levelname)s %(name)s: %(message)s")
    if roh not in _LOG_STUFEN:
        _LOG.warning(
            "SCANVIEW_LOG_LEVEL=%r unbekannt (erwartet DEBUG|INFO|WARNING), verwende INFO", roh,
        )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="scanview-server")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8803)

    e = sub.add_parser("einladen", help="Einladungslink erzeugen (7 Tage gültig)")
    e.add_argument("--name", required=True)
    e.add_argument("--email", required=True)
    e.add_argument("--admin", action="store_true")
    e.add_argument("--projekt", action="append", default=[], metavar="ID=ROLLE")
    e.add_argument("--zuruecksetzen", action="store_true")
    sub.add_parser("nutzer", help="Nutzer und Rechte auflisten")

    w = sub.add_parser("widerrufen", help="Alle offenen Einladungen einer E-Mail ungültig machen")
    w.add_argument("--email", required=True)
    g = sub.add_parser("sperren", help="Konto sperren und alle Sitzungen beenden")
    g.add_argument("--email", required=True)
    en = sub.add_parser("entsperren", help="Gesperrtes Konto wieder freigeben")
    en.add_argument("--email", required=True)
    z = sub.add_parser("entziehen", help="Recht auf ein Projekt (oder alle) entziehen")
    z.add_argument("--email", required=True)
    welche = z.add_mutually_exclusive_group(required=True)
    welche.add_argument("--projekt", metavar="ID")
    welche.add_argument("--alle", action="store_true")
    a = sub.add_parser("abmelden-ueberall", help="Alle Sitzungen eines Kontos beenden")
    a.add_argument("--email", required=True)
    an = sub.add_parser(
        "anonymisieren",
        help="Personendaten eines Kontos unwiderruflich entfernen (DSGVO; Konto bleibt gesperrt)",
    )
    an.add_argument("--email", required=True)
    gx = sub.add_parser(
        "git-export", help="Neue Bearbeitungen je Projekt als Git-Commits (SCANVIEW_GIT_EXPORT=1)",
    )
    gx.add_argument("--projekt", metavar="ID", help="nur dieses Projekt")
    gx.add_argument("--push", action="store_true", help="danach an SCANVIEW_GIT_REMOTE pushen")

    args = p.parse_args(argv)
    settings = Settings.from_env()

    if args.cmd == "serve":
        _konfiguriere_logging()
        # Katalog neu laden per `systemctl reload` (ExecReload=/bin/kill -HUP $MAINPID):
        # uvicorn installiert eigene Signal-Handler für INT/TERM, nicht für HUP.
        app = create_app(settings)
        signal.signal(signal.SIGHUP, lambda *_a: _reload_katalog(app))
        uvicorn.run(
            app,
            host="127.0.0.1",
            port=args.port,
            proxy_headers=True,
            forwarded_allow_ips="127.0.0.1",
            access_log=False,
        )
    elif args.cmd == "einladen":
        return _einladen(settings, args)
    elif args.cmd == "nutzer":
        _nutzer(settings)
    elif args.cmd in _VERWALTEN:
        return _verwalten(settings, args)
    elif args.cmd == "git-export":
        return _git_export(settings, args)
    return 0


def _git_export(settings: Settings, args: argparse.Namespace) -> int:
    """Exit 0 auch ohne neue Ereignisse und bei ausgeschaltetem Export (dann ohne jede Wirkung),
    1 bei Git-Fehler oder --push ohne Remote, 2 bei ungültiger Projekt-ID."""
    if not settings.git_export:
        print("Git-Export ist aus (SCANVIEW_GIT_EXPORT=1)")
        return 0
    if args.projekt is not None and not PROJECT_ID_RE.fullmatch(args.projekt):
        print(f"Fehler: Projekt-ID {args.projekt!r} ungültig.", file=sys.stderr)
        return 2
    if args.push and settings.git_remote is None:
        print("Fehler: --push braucht SCANVIEW_GIT_REMOTE (Vorlage mit {projekt}).",
              file=sys.stderr)
        return 1
    if args.push and settings.git_remote is not None:
        fehler = gitexport.remote_fehler(settings.git_remote)
        if fehler is not None:
            print(f"Fehler: {fehler}", file=sys.stderr)
            return 1
    _konfiguriere_logging()
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    try:
        ergebnis = gitexport.exportieren(conn, settings, projekt=args.projekt, push=args.push)
    except gitexport.GitFehler as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    # Knapp fürs Journal (Timer alle 5 min): Projekte ohne neue Commits nicht erwähnen;
    # übersprungene Ereignisse meldet gitexport schon als WARNING (mit Grund) — nicht doppelt.
    for schluessel, n in ergebnis.items():
        if n and not schluessel.startswith(gitexport.UEBERSPRUNGEN):
            print(f"{schluessel}: {n} {'neuer Commit' if n == 1 else 'neue Commits'}")
    return 0


def _reload_katalog(app: FastAPI) -> None:
    """SIGHUP-Handler: darf den Prozess nie zum Absturz bringen — eine kaputte
    oder kurzzeitig unlesbare Datenwurzel (iterdir wirft z. B. bei einem Mount-Hänger) soll den
    alten Katalog stehen lassen statt den Dienst zu beenden. `Katalog.laden()` tauscht die interne
    Karte erst ganz am Ende atomar (`self._karten = neu`) — ein Fehler davor lässt den alten Stand
    unverändert; hier zusätzlich abgesichert, weil ein Signal-Handler keine Ausnahme nach außen
    durchlassen darf (sonst bricht der Python-Interpreter die laufende Verarbeitung ab)."""
    try:
        app.state.katalog.laden()
    except (OSError, ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError):
        _LOG.exception("Katalog-Neuladen (SIGHUP) fehlgeschlagen – alter Stand bleibt aktiv")  # noqa: RUF001


def _einladen(settings: Settings, args: argparse.Namespace) -> int:
    try:
        grants = dict(x.split("=", 1) for x in args.projekt)
    except ValueError:
        print("Fehler: --projekt erwartet ID=ROLLE.", file=sys.stderr)
        return 2

    for pid in grants:
        if not PROJECT_ID_RE.fullmatch(pid):
            print(
                f"Fehler: Projekt-ID {pid!r} ungültig (erlaubt: a–z, 0–9, '-', max. 64 Zeichen).",  # noqa: RUF001
                file=sys.stderr,
            )
            return 2

    katalog = Katalog(settings.data_dir)
    katalog.laden()
    for pid in grants:
        if katalog.get(pid) is None:
            print(
                f"Warnung: Projekt {pid!r} noch nicht im Katalog (Paket kann später kommen).",
                file=sys.stderr,
            )

    conn = db.connect(settings.db_path)
    db.migrate(conn)
    try:
        token = verwaltung.einladen(
            conn, email=args.email, name=args.name, grants=grants, is_admin=args.admin,
            zuruecksetzen=args.zuruecksetzen, von=None, kanal="cli",
        )
    except ValueError as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    print(f"{settings.origin}/einladung#t={token}")
    return 0


def _nutzer(settings: Settings) -> None:
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    try:
        for u in conn.execute("SELECT * FROM users ORDER BY id").fetchall():
            rollen = conn.execute(
                "SELECT project_id, role FROM project_roles WHERE user_id=? ORDER BY project_id",
                (u["id"],),
            ).fetchall()
            projekte = ", ".join(f"{r['project_id']}={r['role']}" for r in rollen)
            letzte = (
                datetime.fromtimestamp(u["last_login_at"], tz=UTC).isoformat()
                if u["last_login_at"] is not None else "-"
            )
            admin = "ja" if u["is_admin"] else "nein"
            status = "gesperrt" if u["disabled"] else "aktiv"
            print(f"{u['name']} | {u['email']} | {admin} | {status} | {letzte} | {projekte}")
    finally:
        conn.close()


_VERWALTEN = (
    "widerrufen", "sperren", "entsperren", "entziehen", "abmelden-ueberall", "anonymisieren",
)


def _verwalten(settings: Settings, args: argparse.Namespace) -> int:
    """Zugänge verwalten. Unbekannte E-Mail → Exit 2 mit Meldung, Erfolg → Exit 0."""
    email = args.email.strip()
    if args.cmd == "entziehen" and args.projekt is not None and not PROJECT_ID_RE.fullmatch(
        args.projekt,
    ):
        print(f"Fehler: Projekt-ID {args.projekt!r} ungültig.", file=sys.stderr)
        return 2
    conn = db.connect(settings.db_path)
    db.migrate(conn)
    try:
        return _verwalten_konto(conn, args, email)
    except verwaltung.KontoAnonymisiert as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 2
    finally:
        conn.close()


def _verwalten_konto(conn: sqlite3.Connection, args: argparse.Namespace, email: str) -> int:
    uid = konten.konto_id(conn, email)
    if args.cmd == "widerrufen":
        # Hauptfall: Einladung verschickt, aber noch kein Konto angelegt.
        if uid is None and not konten.einladung_bekannt(conn, email):
            print(f"Fehler: Weder Konto noch Einladung für {email}.", file=sys.stderr)
            return 2
        n = verwaltung.einladungen_widerrufen(conn, email, von=None, kanal="cli")
        print(f"{n} offene Einladung(en) für {email} widerrufen.")
        return 0
    if uid is None:
        print(f"Fehler: Kein Konto mit der E-Mail {email}.", file=sys.stderr)
        return 2
    if args.cmd == "sperren":
        n = verwaltung.sperren(conn, uid, von=None, kanal="cli")
        print(f"Konto {email} gesperrt, {n} Sitzung(en) beendet.")
    elif args.cmd == "entsperren":
        verwaltung.entsperren(conn, uid, von=None, kanal="cli")
        print(f"Konto {email} entsperrt.")
    elif args.cmd == "entziehen":
        n = verwaltung.rechte_entziehen(
            conn, uid, None if args.alle else args.projekt, von=None, kanal="cli",
        )
        ziel = "allen Projekten" if args.alle else f"Projekt {args.projekt}"
        print(f"{n} Recht(e) von {email} an {ziel} entzogen.")
        if konten.ist_admin(conn, uid):
            print("Hinweis: Das Konto ist Admin und sieht weiterhin alle Projekte.")
    elif args.cmd == "anonymisieren":
        z = verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
        print(
            f"Konto {uid} anonymisiert (jetzt „Gelöschtes Konto {uid}“, gesperrt): "
            f"{z['sitzungen']} Sitzung(en), {z['passkeys']} Passkey(s), "
            f"{z['passwort']} Passwort, {z['rechte']} Recht(e), "
            f"{z['einladungenGeloescht']} offene Einladung(en) entfernt, "
            f"{z['einladungenGeschwaerzt']} alte Einladung(en) geschwärzt.",
        )
    else:  # abmelden-ueberall
        n = verwaltung.abmelden_ueberall(conn, uid, von=None, kanal="cli")
        print(f"{n} Sitzung(en) von {email} beendet.")
    return 0
