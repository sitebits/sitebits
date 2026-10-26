"""FastAPI-App-Fabrik, Datenbank je Anfrage, JSON-Fehler mit Dienstkennung."""

from __future__ import annotations

import math
import re
import sqlite3
import threading
import time
from collections import deque
from collections.abc import AsyncIterator, Callable, Iterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import apitoken, auftraege, konten, lesezugriff, passkey, passwort, teilen
from . import db as dbmod
from .api_modelle import FEHLERANTWORTEN
from .config import Settings
from .katalog import PROJECT_ID_RE, Katalog

DIENST = "scanview"
COOKIE = "__Host-scanview"
UEBERLASTET = "Gerade zu viele Anmeldeversuche – bitte kurz warten."  # noqa: RUF001
KONFLIKT = "Inzwischen von jemand anderem geändert – aktueller Stand anbei."  # noqa: RUF001
# Dieselben Texte wie zugriff.NICHT_GEFUNDEN / routen_export.NUR_VERLAUF — hier
# als eigene Literale statt Import, weil `zugriff` und `routen_export` ihrerseits von `web`
# importieren (Zyklus). Tests vergleichen gegen die jeweils andere Seite, damit sie nicht
# auseinanderlaufen können.
_NICHT_GEFUNDEN = "nicht gefunden"
_NUR_VERLAUF = "Den Verlauf sehen Bearbeiter, Projektleitung und Admins."
_NUR_LEITUNG = "Nur die Projektleitung darf das Projekt verwalten."
_NICHT_PER_TOKEN = "Projektverwaltung nur im Browser, nicht per API-Token."
# Verwaltungswege eines Projekts: kaputter Körper → erst 404 (kein Recht), dann 403 (keine Leitung,
# Token), sonst 422 — dieselbe Reihenfolge wie zugriff.leitung() mit gültigem Körper.
_PROJEKT_VERWALTUNG_RE = re.compile(
    r"^/api/v1/projekte/(?P<pid>[^/]+)/"
    r"(einladungen|beteiligte|gewerke|kategorien|einstellungen|einrichten|teilen|freigaben)(/|$)")
_NICHT_ANGEMELDET = "nicht angemeldet"
# Unter diesen Pfaden entscheidet die Admin-Rolle über 404 (statt 422) bei ungültigen Parametern.
# Exakter Vergleich für das globale Protokoll statt Präfix — ein Präfix-Vergleich
# auf "/api/v1/protokoll" würfe versehentlich auch künftige, andere Pfade mit diesem Anfang mit
# hinein (der projektbezogene Verlauf /api/v1/projekte/{pid}/protokoll hat ohnehin ein anderes
# Präfix und ist davon nicht betroffen, siehe _PROJEKT_PROTOKOLL_RE unten).
_ADMIN_PFAD_PREFIX = "/api/v1/admin/"
_PROTOKOLL_PFADE = ("/api/v1/protokoll", "/api/v1/protokoll.jsonl")
# Lesezugriffsprotokoll (nur Admin per Cookie): global exakt, projektbezogen per Regex.
LESEZUGRIFF_PFADE = ("/api/v1/lesezugriffe", "/api/v1/lesezugriffe.jsonl")
LESEZUGRIFF_PROJEKT_RE = re.compile(r"^/api/v1/projekte/[^/]+/lesezugriffe(\.jsonl)?$")
# Projektbezogener Verlauf: 404 ohne Zugriff, 403 als Betrachter, sonst 422.
_PROJEKT_PROTOKOLL_RE = re.compile(r"^/api/v1/projekte/(?P<pid>[^/]+)/protokoll(\.jsonl)?$")
# Auswertungen: kaputter Körper → erst 404 (kein Recht), dann 403 (Schreiben unter
# Projektleitung), sonst 422 — wie routen_auswertungen mit gültigem Körper.
_AUSWERTUNG_RE = re.compile(r"^/api/v1/projekte/(?P<pid>[^/]+)/auswertungen(/|$)")
_NUR_LEITUNG_AUSWERTUNG = "Auswertungen starten und verwalten nur Projektleitung und Admins."
# Uploads und Importe (nur Admin, nur mit eingeschaltetem Import): kaputter Körper → 404 ohne
# Import oder für Nicht-Admins, sonst 403 für Lese-Tokens, sonst 422.
_UPLOAD_RE = re.compile(r"^/api/v1/(uploads|importe)(/|$)")
# Projektpfade, unter denen ein Lese-Token mit kaputtem Körper erst 404 (kein
# Zugriff), dann 403 bekommt — dieselbe Reihenfolge wie zugriff.projekt() bei gültigem Körper.
_PROJEKT_RE = re.compile(r"^/api/v1/projekte/(?P<pid>[^/]+)(/|$)")

# Bearer-Tokens gelten nur unter /api/v1/, nie für Verwaltung oder Konto (ADR 0008) und ebenso
# nicht für die Statistik-Meldung des Viewers. Feste Liste — neue v1-Pfade sind automatisch
# token-fähig. /api/authz, /api/sitzung, /auth/*, /data/* liegen ohnehin außerhalb von /api/v1/.
_BEARER_PRAEFIX = "/api/v1/"
_OHNE_BEARER = ("/api/v1/admin", "/api/v1/statistik", "/api/v1/konto")
# RFC 7235: Schema ohne Rücksicht auf Groß-/Kleinschreibung.
_BEARER_SCHEMA = "bearer "
NUR_LESEN = "Dieses Token darf nur lesen."
ZU_VIELE_TOKEN_ANFRAGEN = "Zu viele Anfragen mit diesem Token."
# E-T2: gleitendes Fenster je Token, im Speicher (ein Prozess). Zur Laufzeit gelesen, damit Tests
# den Wert per monkeypatch verkleinern können.
TOKEN_PRO_MINUTE = 120
# Größte SQLite-Ganzzahl (64 Bit): jede Ganzzahl aus Pfad, Abfrage oder Körper, die in SQL
# gebunden wird, ist darauf begrenzt — größere Werte enden sonst als OverflowError (500,
# Befund B-FUZZ-4) statt als 422.
SQLITE_GANZZAHL_MAX = 2**63 - 1
_TOKEN_FENSTER_S = 60.0


class Konflikt(Exception):
    """Optimistische Sperre: `version` passt nicht oder die ID ist schon vergeben →
    409 mit dem aktuellen Stand (inklusive `version`) im Feld `aktuell`."""

    def __init__(self, aktuell: dict[str, Any], text: str = KONFLIKT) -> None:
        super().__init__(text)
        self.aktuell = aktuell
        self.text = text


class Ungueltig(Exception):
    """Fachlich ungültige Eingabe → 422. Die Nachricht ist nutzersichtbar (Deutsch) und nennt
    keine Geheimnisse."""


def fehler(status: int, text: str, headers: Mapping[str, str] | None = None) -> JSONResponse:
    return JSONResponse({"dienst": DIENST, "fehler": text}, status_code=status, headers=headers)


def db(request: Request) -> Iterator[sqlite3.Connection]:
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        yield conn
    finally:
        conn.close()


def bearer_pfad(path: str) -> bool:
    """Darf unter diesem Pfad ein Bearer-Token anmelden? (E-T1: am Pfad entschieden, ohne DB.)"""
    if not path.startswith(_BEARER_PRAEFIX):
        return False
    return not any(path == p or path.startswith(p + "/") for p in _OHNE_BEARER)


def _bearer_wert(request: Request) -> str | None:
    """Der Token-Wert, wenn diese Anfrage den Token-Weg nimmt, sonst None.

    Die eine Stelle, an der das entschieden wird: `csrf` (Prüfung überspringen) und
    `aktueller_nutzer` (Cookie ignorieren) fragen beide hier — so heißt „CSRF übersprungen“
    immer auch „Cookie zählt nicht“. Das Schema zählt ohne Rücksicht auf Groß-/Kleinschreibung
    (`bearer x` ist ein Token-Versuch); leerer Wert nach `Bearer ` bleibt Token-Weg (→ 401), ein
    anderes Schema (`Basic …`) ist keiner."""
    if not bearer_pfad(request.url.path):
        return None
    kopf = request.headers.get("authorization")
    if kopf is None or kopf[:len(_BEARER_SCHEMA)].lower() != _BEARER_SCHEMA:
        return None
    return kopf[len(_BEARER_SCHEMA):].strip()


class TokenDrossel:
    """E-T2: höchstens TOKEN_PRO_MINUTE Anfragen je Token in 60 s (gleitend). Threadsicher —
    synchrone Routen laufen parallel im Threadpool. Neustart setzt zurück (in Kauf genommen)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._zeiten: dict[int, deque[float]] = {}

    def erlaubt(self, token_id: int) -> int | None:
        """None, wenn die Anfrage durchgeht (und gezählt ist), sonst Sekunden bis zum nächsten
        freien Platz (für `Retry-After`, mindestens 1)."""
        jetzt = time.monotonic()
        grenze = TOKEN_PRO_MINUTE
        with self._lock:
            # Leere Fenster abgelaufener Tokens wegräumen, damit das Wörterbuch nicht wächst.
            for tid in [t for t, z in self._zeiten.items() if z[-1] <= jetzt - _TOKEN_FENSTER_S]:
                del self._zeiten[tid]
            zeiten = self._zeiten.setdefault(token_id, deque())
            while zeiten and zeiten[0] <= jetzt - _TOKEN_FENSTER_S:
                zeiten.popleft()
            if len(zeiten) >= grenze:
                return max(1, math.ceil(zeiten[0] + _TOKEN_FENSTER_S - jetzt))
            zeiten.append(jetzt)
            return None


def aktueller_nutzer(request: Request, conn: sqlite3.Connection) -> konten.Nutzer | None:
    """Angemeldeter Nutzer aus dem Sitzungs-Cookie — oder, auf einem Bearer-Pfad mit
    `Authorization: Bearer …`, ausschließlich aus dem Token: das Cookie
    wird dann ignoriert, ein ungültiges Token ist 401 ohne Rückfall. Bewusst
    ohne Aufrufer-Option — wo `csrf` übersprungen wurde, kann kein Aufrufer das Cookie doch
    noch gelten lassen. /api/authz und /auth/* sind keine Bearer-Pfade und bleiben Cookie-Wege.

    Gültiges Token über der Drossel → 429 (HTTPException mit `Retry-After`). Das Token liegt
    danach in `request.state.api_token` (siehe `api_token`, `kanal`)."""
    roh = _bearer_wert(request)
    if roh is not None:
        t = apitoken.pruefen(conn, roh)
        if t is None:
            return None
        warten = request.app.state.token_drossel.erlaubt(t.id)
        if warten is not None:
            raise StarletteHTTPException(
                status_code=429, detail=ZU_VIELE_TOKEN_ANFRAGEN,
                headers={"Retry-After": str(warten)},
            )
        request.state.api_token = t
        return t.nutzer
    c = request.cookies.get(COOKIE)
    return konten.sitzung_nutzer(conn, c) if c else None


def api_token(request: Request) -> apitoken.ApiToken | None:
    """Das Token, mit dem diese Anfrage angemeldet ist (erst nach `aktueller_nutzer`)."""
    t: apitoken.ApiToken | None = getattr(request.state, "api_token", None)
    return t


KI_PRUEFT_NICHT = "Vorschläge prüft ein Mensch – nicht über einen Token für KI-Agenten."  # noqa: RUF001


def ki(request: Request) -> bool:
    """Anfrage über einen Token „für einen KI-Agenten“ (G7): Geschriebenes wird Vorschlag."""
    t = api_token(request)
    return t is not None and t.agent


def kanal(request: Request) -> str:
    """Protokoll-Kanal der Anfrage: 'api' bei Token-Anmeldung, sonst 'web'."""
    return "api" if api_token(request) is not None else "web"


def token_name(request: Request) -> str | None:
    """Name des Tokens fürs Protokoll (nie Wert oder Hash), sonst None."""
    t = api_token(request)
    return t.name if t is not None else None


def nur_lesen_verletzt(request: Request) -> bool:
    """Lese-Token und eine Anfrage, die schreiben könnte (alles außer GET/HEAD)."""
    t = api_token(request)
    return t is not None and t.umfang == "lesen" and request.method not in ("GET", "HEAD")


# Schritte, die ein neues Zugangsmittel schaffen oder Personendaten dauerhaft freigeben (weiteren
# Passkey hinzufügen, API-Token anlegen, Git-Namens-Einwilligung einschalten), verlangen eine
# Sitzung, die höchstens so alt ist: ein
# liegengelassenes oder erbeutetes, noch gültiges Cookie (bis 60 Tage) allein soll dafür nicht
# reichen.
FRISCH_S = 10 * 60
NEU_ANMELDEN_PRAEFIX = "Bitte zuerst neu anmelden, um "


def frisch_angemeldet(request: Request, conn: sqlite3.Connection, wozu: str) -> konten.Nutzer:
    """Nutzer der Sitzung, sofern sie frisch ist (höchstens `FRISCH_S` alt). Sonst 401 ohne
    Sitzung bzw. 403 „Bitte zuerst neu anmelden, um <wozu>.“ — als HTTPException, die der
    globale Handler in dieselbe JSON-Fehlerform wie `fehler()` bringt. Nur für Cookie-Pfade
    (/auth/*, /api/v1/konto): dort zählt nie ein Bearer-Token."""
    n = aktueller_nutzer(request, conn)
    if n is None:
        raise StarletteHTTPException(status_code=401, detail=_NICHT_ANGEMELDET)
    alter = konten.sitzung_alter(conn, request.cookies.get(COOKIE, ""))
    if alter is None or alter > FRISCH_S:
        raise StarletteHTTPException(status_code=403, detail=f"{NEU_ANMELDEN_PRAEFIX}{wozu}.")
    return n


async def csrf(request: Request) -> None:
    """Abhängigkeit für ändernde Anfragen: Origin muss passen, dazu der Header X-Scanview.

    Mit `Authorization: Bearer …` auf einem Bearer-Pfad entfällt die Prüfung —
    dieselbe Entscheidung (`_bearer_wert`) sorgt dafür, dass das Cookie dann nicht zählt. Ein
    fremder Ursprung kann `Authorization` nur nach einem CORS-Preflight setzen, den der Dienst
    nicht beantwortet. Auch ein leerer Wert nach `Bearer ` überspringt CSRF — die Anfrage endet
    trotzdem in 401, weil `aktueller_nutzer` dann nur den (leeren) Token prüft und nie das Cookie.

    `async`, damit sie nicht im gemeinsamen Thread-Pool läuft (reine Header-Prüfung, WB-M3)."""
    if _bearer_wert(request) is not None:
        return
    s: Settings = request.app.state.settings
    if request.headers.get("origin") != s.origin or request.headers.get("x-scanview") != "1":
        raise StarletteHTTPException(status_code=403, detail="CSRF")


# Paketdateien liegen bis zu einer Stunde privat im Browser-Cache (Caddy: /data, max-age=3600).
# Bei jedem Wechsel der Sitzung leert der Browser deshalb den Cache dieser Origin, sonst lüde eine
# andere Person im selben Browser sie ohne Rechteprüfung. Nur "cache": Cookies (die neue Sitzung)
# und Speicher (Entwürfe des Viewers) bleiben. Browser ohne Unterstützung ignorieren den Header.
CACHE_LEEREN = ("Clear-Site-Data", '"cache"')


def setze_sitzung(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(
        COOKIE, token, max_age=konten.ABSOLUT_S, path="/", secure=settings.cookie_secure,
        httponly=True, samesite="lax",
    )
    response.headers[CACHE_LEEREN[0]] = CACHE_LEEREN[1]


def loesche_sitzung(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax",
    )
    response.headers[CACHE_LEEREN[0]] = CACHE_LEEREN[1]


def neue_sitzung(
    response: Response, request: Request, conn: sqlite3.Connection, user_id: int,
) -> None:
    """Sitzung nach erfolgreicher Anmeldung anlegen (WB-N8): ein mitgeschicktes altes Cookie
    dieses Browsers wird dabei serverseitig beendet, statt als gültiges Token liegen zu bleiben."""
    alt = request.cookies.get(COOKIE)
    if alt:
        konten.sitzung_beenden(conn, alt)
    setze_sitzung(response, konten.sitzung_anlegen(conn, user_id), request.app.state.settings)


class KeinCache:
    """ASGI-Middleware (BB-I1/WB-I2): `Cache-Control: no-store` auf jede Antwort unter /api/ und
    /auth/, sofern die Route keinen eigenen Wert setzt. `/api/sitzung` liefert Name und E-Mail;
    auch Fehlerantworten (401/429) sollen nie in einem Cache landen. Bewusst reines ASGI statt
    BaseHTTPMiddleware: `/api/authz` liegt im heißen Pfad jeder Range-Anfrage."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope["path"]).startswith(("/api/", "/auth/")):
            await self.app(scope, receive, send)
            return

        async def senden(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                kopf = list(msg.get("headers", []))
                if not any(k.lower() == b"cache-control" for k, _v in kopf):
                    kopf.append((b"cache-control", b"no-store"))
                msg = {**msg, "headers": kopf}
            await send(msg)

        await self.app(scope, receive, senden)


def _admin_vorrang(r: Request, pfad: str, *, import_an: bool, admin: bool
                   ) -> tuple[bool, tuple[int, str, Mapping[str, str] | None] | None]:
    """Admin-Pfade mit kaputtem Körper: (zuständig, Antwort). Antwort None heißt 422.

    Auf /api/v1/admin/*, /api/v1/protokoll(.jsonl) und den Lesezugriffen verrät ein 422 vor der
    Admin-Prüfung ebenso die Existenz der Route — dieselbe 404-Antwort wie zugriff.admin(), auch
    bei ungültigen Query-/Body-Parametern. Per Token (ADR 0008) gibt es keine Verwaltung, auch
    nicht für ein Admin-Konto. Uploads und Importe: 404 ohne Import oder für Nicht-Admins (wie
    die Routen selbst), dann 403 für Lese-Tokens."""
    if (
        pfad.startswith(_ADMIN_PFAD_PREFIX) or pfad in _PROTOKOLL_PFADE
        or pfad in LESEZUGRIFF_PFADE or LESEZUGRIFF_PROJEKT_RE.match(pfad)
    ):
        if not admin or api_token(r) is not None:
            return True, (404, _NICHT_GEFUNDEN, None)
        return True, None
    if _UPLOAD_RE.match(pfad):
        if not import_an or not admin:
            return True, (404, _NICHT_GEFUNDEN, None)
        if nur_lesen_verletzt(r):
            return True, (403, NUR_LESEN, None)
        return True, None
    return False, None


def _auswertung_vorrang(
    r: Request, pfad: str, rolle_von: Callable[[str], str | None],
) -> tuple[int, str, Mapping[str, str] | None] | None:
    """Vorrang bei kaputtem Körper unter …/auswertungen: 404 ohne Zugriff, 403 unter der
    Projektleitung (schreibend), sonst None (→ 422)."""
    treffer = _AUSWERTUNG_RE.match(pfad)
    if treffer is None or r.method in ("GET", "HEAD"):
        return None
    rolle = rolle_von(treffer.group("pid"))
    if rolle is None:
        return 404, _NICHT_GEFUNDEN, None
    if not konten.mindestens(rolle, "projektleitung"):
        return 403, _NUR_LEITUNG_AUSWERTUNG, None
    return None


@asynccontextmanager
async def _lebenszeit(app: FastAPI) -> AsyncIterator[None]:
    pumpe = getattr(app.state, "post_pumpe", None)
    if pumpe is not None:
        pumpe.starten()
    yield
    if pumpe is not None:
        pumpe.beenden()
    app.state.rechenplaetze.beenden()


def create_app(settings: Settings, katalog: Katalog | None = None) -> FastAPI:
    app = FastAPI(
        title="siteBits", version="1",
        docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lebenszeit,
        responses=FEHLERANTWORTEN,
    )
    app.add_middleware(KeinCache)
    app.state.settings = settings
    # WB-M3: eigener Thread-Pool für argon2; WB-M2: globale Bremse für Fehlversuche.
    app.state.rechenplaetze = passwort.Rechenplaetze()
    app.state.anmeldebremse = passwort.Anmeldebremse(settings.anmelde_fehler_pro_minute)
    app.state.token_drossel = TokenDrossel()
    app.state.lesemerker = lesezugriff.Merker()
    app.state.teilen_drosseln = teilen.Drosseln.neu()
    app.state.db_path = settings.db_path
    conn = dbmod.connect(settings.db_path)
    try:
        dbmod.migrate(conn)
        geloescht = konten.aufraeumen(
            conn, lesezugriffe_tage=settings.lesezugriffe_tage,
        )["lesezugriffe"]
        teilen.aufraeumen(conn)
    finally:
        conn.close()

    if settings.jobs_db is not None:
        # Eigene Auftragsdatenbank (nie scanview.db); der Worker legt dasselbe Schema an.
        jc = auftraege.verbinden(settings.jobs_db)
        try:
            auftraege.schema(jc)
            if settings.import_enabled and settings.uploads_dir is not None:
                from .routen_uploads import aufraeumen

                c2 = dbmod.connect(settings.db_path)
                try:
                    aufraeumen(settings.uploads_dir, c2, jc)
                finally:
                    c2.close()
        finally:
            jc.close()
    lesezugriff.melde_start(settings.lesezugriffe_tage, geloescht)
    if katalog is None:
        katalog = Katalog(settings.data_dir)
        katalog.laden()
    app.state.katalog = katalog
    if settings.post_dir is not None and settings.post_absender is not None:
        # F8: Mails rendern im Hintergrund (nie im Request); versendet scanview-post.
        from . import post

        app.state.post_pumpe = post.Pumpe(settings.db_path, post.Postdienst(
            spool=settings.post_dir, absender=settings.post_absender, origin=settings.origin,
            state_dir=settings.state_dir), katalog)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_r: Request, e: StarletteHTTPException) -> JSONResponse:
        # Header durchreichen (z. B. `Allow` bei 405), sonst gingen sie im JSON-Fehler verloren.
        return fehler(e.status_code, str(e.detail), headers=e.headers)

    @app.exception_handler(passwort.Ueberlastet)
    async def _ueberlastet(_r: Request, _e: passwort.Ueberlastet) -> JSONResponse:
        return fehler(429, UEBERLASTET)

    @app.exception_handler(passkey.ZuVieleChallenges)
    async def _zu_viele_challenges(_r: Request, _e: passkey.ZuVieleChallenges) -> JSONResponse:
        return fehler(429, UEBERLASTET)

    def _zugang(conn: sqlite3.Connection, n: konten.Nutzer, pid: str) -> str | None:
        karte = katalog.get(pid) if PROJECT_ID_RE.fullmatch(pid) else None
        return konten.rolle(conn, n, pid) if karte is not None else None

    def _vorrang(r: Request, pfad: str) -> tuple[int, str, Mapping[str, str] | None] | None:
        """Synchroner DB-Teil des Validierungs-Handlers (läuft im Threadpool, Final-Fix C/D):
        Verbindung öffnen, lesen und schließen im selben Worker-Thread (check_same_thread)."""
        conn = dbmod.connect(settings.db_path)
        try:
            # Derselbe Anmeldeweg wie zugriff.angemeldet (Bearer auf Bearer-Pfaden). Die
            # Drossel meldet sich per HTTPException — hier nie weiterwerfen (Ausnahme im
            # Ausnahme-Handler), sondern als Antwort zurückgeben.
            try:
                n = aktueller_nutzer(r, conn)
            except StarletteHTTPException as e:
                return e.status_code, str(e.detail), e.headers
            if n is None:
                return 401, "nicht angemeldet", None
            nur_admin, antwort = _admin_vorrang(r, pfad, import_an=settings.import_enabled,
                                                admin=n.is_admin)
            if nur_admin:
                return antwort
            # Lese-Token, schreibende Methode, kaputter Körper → erst
            # 404 (kein Zugriff aufs Projekt), dann 403 — wie zugriff.projekt() mit gültigem
            # Körper. Nur für Tokens: das Cookie-Verhalten (Betrachter mit kaputtem Körper → 422,
            # test_api_v1) bleibt unverändert.
            if nur_lesen_verletzt(r):
                projekt_treffer = _PROJEKT_RE.match(pfad)
                if projekt_treffer and _zugang(conn, n, projekt_treffer.group("pid")) is None:
                    return 404, _NICHT_GEFUNDEN, None
                return 403, NUR_LESEN, None
            verwaltung_treffer = _PROJEKT_VERWALTUNG_RE.match(pfad)
            if verwaltung_treffer and r.method not in ("GET", "HEAD"):
                rolle = _zugang(conn, n, verwaltung_treffer.group("pid"))
                if rolle is None:
                    return 404, _NICHT_GEFUNDEN, None
                if not konten.mindestens(rolle, "projektleitung"):
                    return 403, _NUR_LEITUNG, None
                if api_token(r) is not None:
                    return 403, _NICHT_PER_TOKEN, None
            auswertung = _auswertung_vorrang(r, pfad, lambda pid: _zugang(conn, n, pid))
            if auswertung is not None:
                return auswertung
            # Dieselbe Vorrangfolge für den projektbezogenen
            # Verlauf — 404 ohne Zugriff, 403 als Betrachter, sonst durch zu 422.
            treffer = _PROJEKT_PROTOKOLL_RE.match(pfad)
            if treffer:
                rolle = _zugang(conn, n, treffer.group("pid"))
                if rolle is None:
                    return 404, _NICHT_GEFUNDEN, None
                if not konten.mindestens(rolle, "bearbeiter"):
                    return 403, _NUR_VERLAUF, None
            return None
        finally:
            conn.close()

    @app.exception_handler(RequestValidationError)
    async def _validierung(r: Request, _e: RequestValidationError) -> JSONResponse:
        # Unter /api/v1/ hat „keine Sitzung“ Vorrang vor 422 — sonst
        # verrät ein 422 vor der Zugriffsprüfung, dass es die Route/den Body-Schlüssel gibt.
        # CSRF bleibt vorrangig (403, läuft als Dependency vor der Body-Validierung). Der
        # DB-Zugriff läuft im Threadpool, damit er die Ereignisschleife nicht blockiert.
        pfad = r.url.path
        if pfad.startswith("/api/v1/"):
            vorrang = await run_in_threadpool(_vorrang, r, pfad)
            if vorrang is not None:
                status, text, kopf = vorrang
                return fehler(status, text, headers=kopf)
        return fehler(422, "Eingabe ungültig.")

    @app.exception_handler(Konflikt)
    async def _konflikt(_r: Request, e: Konflikt) -> JSONResponse:
        return JSONResponse(
            {"dienst": DIENST, "fehler": e.text, "aktuell": e.aktuell}, status_code=409,
        )

    @app.exception_handler(Ungueltig)
    async def _ungueltig(_r: Request, e: Ungueltig) -> JSONResponse:
        return fehler(422, str(e))

    from . import (
        routen_admin,
        routen_anhaenge,
        routen_api,
        routen_auswertungen,
        routen_auth,
        routen_barrierefreiheit,
        routen_export,
        routen_faden,
        routen_freigaben,
        routen_inhalte,
        routen_konto,
        routen_lesezugriffe,
        routen_post,
        routen_projekt,
        routen_teilen,
        routen_uploads,
        routen_v1,
    )

    app.include_router(routen_api.router)
    app.include_router(routen_auth.router)
    app.include_router(routen_v1.router)
    app.include_router(routen_inhalte.router)
    app.include_router(routen_faden.router)
    app.include_router(routen_freigaben.router)
    app.include_router(routen_anhaenge.router)
    app.include_router(routen_post.router)
    app.include_router(routen_projekt.router)
    app.include_router(routen_export.router)
    app.include_router(routen_lesezugriffe.router)
    app.include_router(routen_admin.router)
    app.include_router(routen_konto.router)
    app.include_router(routen_barrierefreiheit.router)
    app.include_router(routen_teilen.router)
    app.include_router(routen_auswertungen.router)
    app.include_router(routen_uploads.router)
    return app
