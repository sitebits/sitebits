"""Zugriffsprüfung für /api/v1: jede Prüfung serverseitig, Fehler als JSON.

Keine Sitzung → 401. Unbekanntes/fremdes Projekt oder Nicht-Admin auf Verwaltung → 404 (verrät
nicht, dass es das gibt). Leserecht, aber Schreibversuch → 403 (der Betrachter kennt das Projekt).

Auf Bearer-Pfaden meldet ein `Authorization: Bearer …` allein an (web.aktueller_nutzer);
ein Lese-Token bekommt auf jede schreibende Methode 403. Verwaltungspfade werten kein Token aus:
ohne Sitzung antworten sie mit 401.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from fastapi import Request
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import konten, projektverwaltung
from .katalog import PROJECT_ID_RE, Karte
from .web import NUR_LESEN, aktueller_nutzer, api_token, nur_lesen_verletzt

NICHT_ANGEMELDET = "nicht angemeldet"
NICHT_GEFUNDEN = "nicht gefunden"
NUR_BEARBEITER = "Nur Bearbeiter dürfen hier ändern."
RUECKMELDER = "rueckmelder"


@dataclass(frozen=True)
class Zugriff:
    nutzer: konten.Nutzer
    rolle: str
    karte: Karte


def _umfang(request: Request) -> None:
    if nur_lesen_verletzt(request):
        raise StarletteHTTPException(status_code=403, detail=NUR_LESEN)


def angemeldet(request: Request, conn: sqlite3.Connection, *, umfang: bool = True) -> konten.Nutzer:
    """401 ohne Anmeldung (Cookie oder, auf Bearer-Pfaden, Token; 429 über der Token-Drossel).

    `umfang=False` nur für `projekt()`, das den Umfang erst nach der 404-Prüfung ansieht
    (Vorrang 401 → 404 → 403)."""
    n = aktueller_nutzer(request, conn)
    if n is None:
        raise StarletteHTTPException(status_code=401, detail=NICHT_ANGEMELDET)
    if umfang:
        _umfang(request)
    return n


def projekt(
    request: Request, conn: sqlite3.Connection, pid: str, *, mindestens: str | None = None,
    rueckmelder: bool = False, text: str = NUR_BEARBEITER,
) -> Zugriff:
    """`mindestens` verlangt mindestens diese Rolle in der Rangfolge (`konten.RANG`);
    `rueckmelder=True` lässt zusätzlich die Rolle Rückmelder durch — deren Einschränkung (nur
    eigene) prüft der Aufrufer."""
    n = angemeldet(request, conn, umfang=False)
    karte: Karte | None = (
        request.app.state.katalog.get(pid) if PROJECT_ID_RE.fullmatch(pid) else None
    )
    rolle = (projektverwaltung.wirksame_rolle(konten.rolle(conn, n, pid),
                                              projektverwaltung.einstellungen(conn, pid))
             if karte is not None else None)
    if karte is None or rolle is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    _umfang(request)
    if (mindestens is not None and not konten.mindestens(rolle, mindestens)
            and not (rueckmelder and rolle == RUECKMELDER)):
        raise StarletteHTTPException(status_code=403, detail=text)
    return Zugriff(nutzer=n, rolle=rolle, karte=karte)


def admin(request: Request, conn: sqlite3.Connection) -> konten.Nutzer:
    n = angemeldet(request, conn)
    # E-T3: per Token nichts verwalten — auch nicht mit einem Admin-Konto (404 wie Nicht-Admin).
    if not n.is_admin or api_token(request) is not None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return n


NUR_LEITUNG = "Nur die Projektleitung darf das Projekt verwalten."
NICHT_PER_TOKEN = "Projektverwaltung nur im Browser, nicht per API-Token."


def leitung(request: Request, conn: sqlite3.Connection, pid: str) -> Zugriff:
    """Projektleitung oder Admin im Projekt, nur per Sitzung (wie `admin`: per Token nichts
    verwalten). Vorrang 401 → 404 → 403 (Lese-Token schreibend, keine Leitung, Token)."""
    z = projekt(request, conn, pid, mindestens="projektleitung", text=NUR_LEITUNG)
    if api_token(request) is not None:
        raise StarletteHTTPException(status_code=403, detail=NICHT_PER_TOKEN)
    return z
