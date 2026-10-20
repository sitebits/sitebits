"""Passwort-Fallback: argon2id, Mindestregeln, Drosselung je E-Mail (persistent in der DB) und
global (im Speicher), eigener Thread-Pool für argon2."""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import logging
import sqlite3
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import TypeVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

MIN_LAENGE = 12
MAX_LAENGE = 256
FEHLVERSUCHE = 5
SPERRE_S = 15 * 60
_ph = PasswordHasher()
# Speicher-DoS-Schutz (Abschluss-Review): argon2-cffi hasht standardmäßig mit 64 MiB je Aufruf,
# FastAPI führt synchrone Routen in einem Pool mit 40 Threads aus, die Unit hat MemoryMax=512M.
# Ohne Begrenzung könnten ~8 gleichzeitige Anmeldeversuche den Dienst an die Speichergrenze
# treiben. Höchstens ARGON2_PARALLEL Aufrufe gleichzeitig; wer länger als ARGON2_WARTEN_S auf
# einen Platz wartet, bekommt `Ueberlastet` (→ 429 in web.py) — ohne Fehlversuch zu zählen und
# ohne eine Einladung zu verbrauchen, weil die Ausnahme vor beidem fliegt.
# Seit Befund B-WB-M3 laufen die Passwort-Routen ohnehin in `Rechenplaetze` mit genau
# ARGON2_PARALLEL Threads; der Semaphor bleibt als Speicher-Schranke für jeden anderen Aufrufer.
ARGON2_PARALLEL = 2
ARGON2_WARTEN_S = 5.0
_argon2_plaetze = threading.BoundedSemaphore(ARGON2_PARALLEL)
log = logging.getLogger("scanview.anmeldung")
T = TypeVar("T")
# Kleine eingebaute Liste häufiger Passwörter (Kleinbuchstaben-Vergleich); bewusst kurz, kein
# Download.
HAEUFIG = frozenset({
    "passwort1234", "password1234", "123456789012", "qwertzuiop12", "hallohallo12",
    "passwort12345", "sommer2026!!", "scanview1234", "vermessung12",
})


def pruefe_regeln(pw: str) -> str | None:
    try:
        pw.encode("utf-8")
    except UnicodeEncodeError:
        # Verstümmelte Eingabe (z. B. Surrogat-Zeichen); argon2 würde sonst mit
        # UnicodeEncodeError abstürzen statt sauber abzulehnen.
        return "Das Passwort enthält ungültige Zeichen."
    if len(pw) < MIN_LAENGE:
        return f"Das Passwort braucht mindestens {MIN_LAENGE} Zeichen."
    if len(pw) > MAX_LAENGE:
        return "Das Passwort ist zu lang."
    if pw.lower() in HAEUFIG or len(set(pw)) < 5:
        return "Dieses Passwort ist zu leicht zu erraten."
    return None




class Ueberlastet(Exception):
    """Zu viele gleichzeitige argon2-Berechnungen — die Anfrage soll mit 429 abgewiesen werden."""


@contextmanager
def _argon2_platz() -> Iterator[None]:
    # Zeitlimit zur Aufrufzeit lesen (nicht als Default-Argument), damit Tests es ändern können.
    if not _argon2_plaetze.acquire(timeout=ARGON2_WARTEN_S):
        raise Ueberlastet
    try:
        yield
    finally:
        _argon2_plaetze.release()


def hash_pw(pw: str) -> str:
    with _argon2_platz():
        return _ph.hash(pw)


def _pruefen(h: str, pw: str) -> bool:
    """argon2-Prüfung OHNE eigenen Platz — der Aufrufer hält `_argon2_platz()`."""
    try:
        return bool(_ph.verify(h, pw))
    except (VerifyMismatchError, VerificationError, InvalidHashError, UnicodeEncodeError):
        # UnicodeEncodeError: verstümmelte Eingabe (Surrogat-Zeichen) beim Anmelden — kein Absturz,
        # sondern wie ein falsches Passwort behandeln.
        return False


def stimmt(h: str, pw: str) -> bool:
    """argon2-Prüfung; `dummy_pruefen` läuft hierüber — dort nicht noch einmal einen Platz nehmen
    (BoundedSemaphore ist nicht reentrant)."""
    with _argon2_platz():
        return _pruefen(h, pw)


# Für unbekannte E-Mails trotzdem einmal hashen → gleiche Laufzeit, keine Konto-Aufzählung.
_DUMMY = _ph.hash("scanview-dummy-passwort")


def dummy_pruefen(pw: str) -> None:
    stimmt(_DUMMY, pw)


def schluessel(email: str) -> str:
    """Drosselschlüssel aus der E-Mail (bereits normalisiert erwartet: strip+lower).

    Bewusst nicht `tokens.hash_token` — die erzwingt striktes ASCII, E-Mails dürfen aber Umlaute
    oder IDN-Domains enthalten (reale Vermesser-Adressen). SHA-256 über UTF-8-Bytes erreicht
    dasselbe Ziel (E-Mail steht nicht im Klartext in `throttle`/Log) ohne diese Einschränkung; für
    reine ASCII-E-Mails ist der Digest identisch zu vorher.
    """
    return hashlib.sha256(("pw:" + email).encode("utf-8", "surrogatepass")).hexdigest()


def gesperrt(conn: sqlite3.Connection, key: str, now: int | None = None) -> bool:
    n = int(time.time()) if now is None else now
    row = conn.execute("SELECT locked_until FROM throttle WHERE key=?", (key,)).fetchone()
    return row is not None and row["locked_until"] > n


def reservieren(conn: sqlite3.Connection, key: str, now: int | None = None) -> bool:
    """Reserviert VOR der argon2-Prüfung einen Fehlversuch — Prüfen und Zählen atomar (WB-M1).

    Gibt False zurück, wenn der Schlüssel gesperrt ist (dann keine Prüfung). Sonst zählt die
    Reservierung als Fehlversuch; die fünfte setzt die Sperre bereits selbst, dieser fünfte
    Versuch darf aber noch prüfen. Eine erfolgreiche Anmeldung löscht die Zeile (`erfolg`).
    Vorher wurde erst nach argon2 gezählt: alle gleichzeitig wartenden Anfragen passierten die
    Sperrprüfung (PoC: 44 statt 5 Prüfungen). `BEGIN IMMEDIATE` serialisiert Lesen und Schreiben
    über alle Verbindungen und Threads; damit finden je Sperrfenster höchstens FEHLVERSUCHE
    Prüfungen statt.
    """
    n = int(time.time()) if now is None else now
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT failures, locked_until FROM throttle WHERE key=?", (key,),
        ).fetchone()
        if row is not None and row["locked_until"] > n:
            conn.execute("COMMIT")
            return False
        f = (row["failures"] if row is not None else 0) + 1
        sperre = n + SPERRE_S if f >= FEHLVERSUCHE else 0
        conn.execute(
            "INSERT INTO throttle (key, failures, locked_until, geaendert_am) VALUES (?,?,?,?)"
            " ON CONFLICT(key) DO UPDATE SET failures=excluded.failures,"
            " locked_until=excluded.locked_until, geaendert_am=excluded.geaendert_am",
            (key, 0 if sperre else f, sperre, n),
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def erfolg(conn: sqlite3.Connection, key: str) -> None:
    conn.execute("DELETE FROM throttle WHERE key=?", (key,))


def anmeldung_pruefen(
    conn: sqlite3.Connection, key: str, h: str | None, pw: str, now: int | None = None,
) -> str:
    """Prüft ein Passwort gegen den Hash (None = unbekanntes/gesperrtes Konto → Dummy-Hash,
    gleiche Laufzeit, keine Konto-Aufzählung).

    Ergebnis: "ok" (Zähler gelöscht), "falsch" (Fehlversuch gezählt) oder "gesperrt" (keine
    Prüfung). Der argon2-Platz wird VOR der Reservierung genommen: ein `Ueberlastet` fliegt so,
    bevor ein Fehlversuch gezählt ist.
    """
    with _argon2_platz():
        if not reservieren(conn, key, now):
            return "gesperrt"
        ok = _pruefen(h if h is not None else _DUMMY, pw) and h is not None
    if ok:
        erfolg(conn, key)
        return "ok"
    return "falsch"


def fehlversuch_melden(key: str, client_ip: str | None) -> None:
    """Journal-Zeile je fehlgeschlagener Passwort-Anmeldung. Das fail2ban-Jail
    (deploy/platform/fail2ban/scanview.conf) filtert GENAU dieses Format — stabil halten:

        scanview Anmeldung fehlgeschlagen (Konto-Schlüssel <12 hex>) von <IP>

    Die IP steht nur im Journal (Sicherheitslog), nie in der DB. Sie kommt von uvicorn
    (`proxy_headers`, nur Caddy auf 127.0.0.1 vertraut) und wird als IP-Adresse geprüft; alles
    andere wird „unbekannt“ (kein Log-Injection über einen manipulierten Header).
    """
    ip = "unbekannt"
    if client_ip:
        try:
            ip = str(ipaddress.ip_address(client_ip))
        except ValueError:
            pass
    log.warning("scanview Anmeldung fehlgeschlagen (Konto-Schlüssel %s) von %s", key[:12], ip)


class Anmeldebremse:
    """Globale Bremse (WB-M2): `grenze` fehlgeschlagene Passwort-Anmeldungen innerhalb von
    `fenster_s` (alle Konten zusammen) → jede Passwort-Anmeldung bekommt 429, bis das Fenster
    weitergerückt ist. Gegen Password-Spraying über viele E-Mails, bei dem die Sperre je Konto
    nie greift. Nur im Speicher (ein Prozess); die Größe ist durch `grenze` fest begrenzt.
    """

    def __init__(
        self, grenze: int, fenster_s: float = 60.0,
        uhr: Callable[[], float] = time.monotonic,
    ) -> None:
        self.grenze = grenze
        self.fenster_s = fenster_s
        self._uhr = uhr
        self._zeiten: deque[float] = deque(maxlen=grenze)
        self._lock = threading.Lock()

    def _voll(self, jetzt: float) -> bool:
        return len(self._zeiten) == self.grenze and jetzt - self._zeiten[0] < self.fenster_s

    def gebremst(self) -> bool:
        with self._lock:
            return self._voll(self._uhr())

    def fehlschlag(self) -> None:
        with self._lock:
            jetzt = self._uhr()
            vorher = self._voll(jetzt)
            self._zeiten.append(jetzt)
            if not vorher and self._voll(jetzt):
                log.warning(
                    "scanview Anmeldebremse aktiv: %d Fehlversuche in %d s, Passwort-Anmeldung"
                    " vorübergehend für alle gesperrt", self.grenze, int(self.fenster_s),
                )


class Rechenplaetze:
    """Eigener kleiner Thread-Pool für die Passwort-Routen (WB-M3).

    Vorher liefen sie synchron im gemeinsamen AnyIO-Thread-Pool (40 Threads) und warteten dort
    bis zu 5 s auf einen argon2-Platz — eine Anmeldeflut belegte alle Threads, und `/api/authz`
    (vor jeder Range-Anfrage des Viewers) stand dahinter an (PoC: 6 ms → 364 ms). Jetzt sind die
    Routen `async`, und die ganze Passwort-Arbeit (kurze DB-Zugriffe + argon2) läuft hier in
    höchstens `parallel` eigenen Threads. Wartende Anfragen warten als Coroutine, ohne einen
    Thread zu belegen; stehen schon `warteschlange` an, gibt es sofort `Ueberlastet` (→ 429).
    Bewusst ohne Zeitlimit beim Warten (`asyncio.wait_for` um `Semaphore.acquire` kann unter
    Python 3.11 bei gleichzeitigem Timeout einen Platz verlieren); die Länge der Warteschlange
    begrenzt die Wartezeit ebenso (16 mal ~50 ms / 2 ≈ 0,4 s).
    """

    def __init__(self, parallel: int = ARGON2_PARALLEL, warteschlange: int = 16) -> None:
        self._parallel = parallel
        # Erst bei Bedarf angelegt und nach `beenden` (Ende der App-Lebenszeit) neu — Threads
        # entstehen so nur, wenn wirklich Passwörter geprüft werden.
        self._pool: ThreadPoolExecutor | None = None
        self._plaetze = asyncio.Semaphore(parallel)
        self._max_wartend = warteschlange
        self._wartend = 0

    async def ausfuehren(self, fn: Callable[[], T]) -> T:
        if self._wartend >= self._max_wartend:
            raise Ueberlastet
        self._wartend += 1
        try:
            await self._plaetze.acquire()
        finally:
            self._wartend -= 1
        try:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(
                    max_workers=self._parallel, thread_name_prefix="argon2",
                )
            return await asyncio.get_running_loop().run_in_executor(self._pool, fn)
        finally:
            self._plaetze.release()

    def beenden(self) -> None:
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=False)
