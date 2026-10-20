"""API-Fuzzing mit Schemathesis gegen eine Wegwerf-Instanz aus dem eingefrorenen Commit.

- Instanz (`starte_testinstanz`): scanview-server aus dem Worktree auf 127.0.0.1:18097 (eigener
  Zustand und zwei synthetische Pakete im Arbeitsordner, keine Kundendaten), davor Caddy
  (`caddy-test`, Host-Netz) mit `Caddyfile.lokal` und `scanview.caddy` des Commits auf
  http://localhost:18090. Zwei Konten per Server-CLI: ein Admin (Sitzungs-Cookie) und ein
  Betrachter eines Pakets (API-Token, nur lesen). Zugangsdaten stehen nur im Speicher und für die
  Dauer eines Laufs in einer 0600-Datei im Arbeitsordner, nie auf einer Befehlszeile.
- Lauf (`schemathesis_laufen`): Schema `/api/v1/openapi.json` mit der jeweiligen Anmeldung,
  nur `/api/v1/…` (die Anmelderouten `/auth/*` nie: Kontosperre), Seed, Beispielzahl, Checks und
  Ausschlüsse aus `nachweise/fuzzing.toml`; ausgewertet wird der JUnit-Bericht. Erst Betrachter,
  dann Admin (der Admin kann Rechte des Betrachters ändern). Nach jedem Lauf prüft die
  Sitzungsprobe, dass beide Anmeldungen noch gelten — sonst ist das Ergebnis ungültig.
- Gate (`fuzzing_gate`): jeder Fehlschlag eines aktiven Checks und jeder Absturz blockiert;
  Abdeckung: jede nicht begründet ausgeschlossene Operation des Schemas muss geprüft sein.
- Berichte werden vor dem Ablegen bereinigt (Cookie- und Token-Wert → `[entfernt]`) und danach
  auf Reste durchsucht; ein Rest löscht die Berichte (`fuzzing-geheimnis`).
- Host-Netz: Caddy und Schemathesis laufen mit `--network host` (nur so erreichen sie die
  Instanz auf 127.0.0.1); jeder dieser Läufe steht als Netzschritt im Ergebnis."""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import time
import tomllib
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote
from xml.etree import ElementTree

from . import sast, sbom
from .artefakte import Netzschritt
from .fehler import NachweisFehler
from .werkzeuge import (
    INSTANZ_CADDY_PORT,
    INSTANZ_SERVER_PORT,
    Werkzeug,
    _laufzeit,
    ausfuehren,
    bau_umgebung,
    docker_befehl,
    gefilterte_umgebung,
)

Identitaet = Literal["admin", "betrachter"]
#: Reihenfolge der Läufe: der Admin-Lauf kann Rechte des Betrachters ändern, deshalb zuletzt.
IDENTITAETEN: tuple[Identitaet, ...] = ("betrachter", "admin")
CADDY_PORT = INSTANZ_CADDY_PORT
SERVER_PORT = INSTANZ_SERVER_PORT
URL = f"http://localhost:{CADDY_PORT}"
KONFIG = Path("fuzzing.toml")
BERICHTE: dict[str, str] = {"admin": "fuzzing-admin.xml", "betrachter": "fuzzing-betrachter.xml"}
MARKE = sbom._MARKE
#: Ersatz für Anmeldewerte in Berichten — ohne XML-/JSON-Sonderzeichen, damit ein bereinigter
#: JUnit- oder HAR-Bericht gültig bleibt („<entfernt>“ machte das XML kaputt).
ENTFERNT = "[entfernt]"
COOKIE_NAME = "__Host-scanview"
#: Die Token-Drossel des Dienstes erlaubt 120 Anfragen je Minute und Token; Schemathesis bleibt
#: darunter, sonst meldete jeder Lauf 429 statt des Verhaltens der Operation.
TOKEN_RATE = "100/m"
#: Alle Checks von Schemathesis 4.27.5 (`schemathesis run --help`); die Konfiguration muss jeden
#: entweder aktivieren oder mit Begründung abschalten.
CHECKS = ("not_a_server_error", "status_code_conformance", "content_type_conformance",
          "response_headers_conformance", "response_schema_conformance",
          "negative_data_rejection", "positive_data_acceptance", "missing_required_header",
          "unsupported_method", "allow_header_conformance", "use_after_free",
          "ensure_resource_availability", "ignored_auth")
#: Nie abschaltbar: Serverfehler, Antwort gegen Schema, Zugriff ohne Anmeldung.
PFLICHT_CHECKS = ("not_a_server_error", "response_schema_conformance", "ignored_auth")
_METHODEN = ("get", "put", "post", "delete", "patch", "options", "head", "trace")
_OPERATION = re.compile(r"(GET|PUT|POST|DELETE|PATCH|OPTIONS|HEAD|TRACE) /\S*")
_MIN_BEGRUENDUNG = 20
#: Zeichenvorrat eines Cookie- bzw. Token-Werts (base64url); alles andere wird nie in eine
#: Konfigurationsdatei geschrieben (Anführungszeichen, Zeilenumbruch, `${…}`-Platzhalter).
_ANMELDEWERT = re.compile(r"[A-Za-z0-9._~+/=-]+")
_SCHEMATHESIS = "/werkzeug/.venv/bin/schemathesis"
_KONFIG_MOUNT = "/konfig/schemathesis.toml"
_SCHEMA_MOUNT = "/konfig/schema.json"
_AUS = "/aus"
_JUNIT = "junit.xml"
_BEREIT_SEKUNDEN = 60
_STOP_SEKUNDEN = 10
_TEXT_HOECHSTENS = 400
#: Zwei synthetische Pakete (server/tests/pakete.py) — nie Kundendaten.
PAKETE = ("fuzzing-eins", "fuzzing-zwei")
_PAKETE_CODE = ("import sys; sys.path.insert(0, 'tests'); from pathlib import Path; "
                "from pakete import lege_paket; "
                "[lege_paket(Path(sys.argv[1]), p) for p in sys.argv[2:]]")

Laeufer = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


# --- Konfiguration -------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ausschluss:
    identitaet: Literal["admin", "betrachter", "beide"]
    pfad_regex: str
    grund: str

    def gilt_fuer(self, identitaet: str) -> bool:
        return self.identitaet in (identitaet, "beide")


@dataclass(frozen=True)
class Konfig:
    seed: int
    max_examples: int
    aktiv: tuple[str, ...]
    aus: dict[str, str]
    ausgeschlossen: tuple[Ausschluss, ...]


def _konfigfehler(pfad: Path, text: str) -> NachweisFehler:
    return NachweisFehler("fuzzing-konfig", f"{pfad.name}: {text}")


def _begruendung(wert: object) -> bool:
    return isinstance(wert, str) and len(wert.strip()) >= _MIN_BEGRUENDUNG


def _ganzzahl(wert: object) -> bool:
    return isinstance(wert, int) and not isinstance(wert, bool) and wert >= 1


def _ausschluss(nr: int, t: object, pfad: Path) -> Ausschluss:
    if not isinstance(t, dict) or set(t) != {"identitaet", "pfad_regex", "grund"}:
        raise _konfigfehler(pfad, f"Ausschluss Nr. {nr}: genau identitaet, pfad_regex, grund")
    if t["identitaet"] not in ("admin", "betrachter", "beide"):
        raise _konfigfehler(pfad, f"Ausschluss Nr. {nr}: identitaet admin, betrachter oder beide")
    if not isinstance(t["pfad_regex"], str) or not t["pfad_regex"].strip():
        raise _konfigfehler(pfad, f"Ausschluss Nr. {nr}: pfad_regex leer")
    try:
        re.compile(t["pfad_regex"])
    except re.error as e:
        raise _konfigfehler(pfad, f"Ausschluss Nr. {nr}: pfad_regex ungültig ({e})") from None
    if not _begruendung(t["grund"]):
        raise _konfigfehler(pfad, f"Ausschluss Nr. {nr}: grund fehlt oder ist kürzer als "
                                  f"{_MIN_BEGRUENDUNG} Zeichen")
    return Ausschluss(t["identitaet"], t["pfad_regex"], t["grund"])


def lade_konfig(pfad: Path) -> Konfig:
    """`fuzzing.toml` fail-closed laden: `seed`, `max_examples` (≥ 1), `[pruefungen]` mit `aktiv`
    und `aus` (Check → Begründung ≥ 20 Zeichen; zusammen genau `CHECKS`, ohne Überschneidung,
    `PFLICHT_CHECKS` aktiv) und `[[ausgeschlossen]]` (identitaet, pfad_regex, grund ≥ 20)."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise _konfigfehler(pfad, "Datei fehlt") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise _konfigfehler(pfad, f"nicht lesbar ({e})") from None
    unbekannt = sorted(set(daten) - {"seed", "max_examples", "pruefungen", "ausgeschlossen"})
    if unbekannt:
        raise _konfigfehler(pfad, f"unbekannte Schlüssel {', '.join(unbekannt)}")
    if not _ganzzahl(daten.get("seed")) or not _ganzzahl(daten.get("max_examples")):
        raise _konfigfehler(pfad, "seed und max_examples müssen ganze Zahlen ≥ 1 sein")
    pruefungen = daten.get("pruefungen")
    if not isinstance(pruefungen, dict) or set(pruefungen) != {"aktiv", "aus"}:
        raise _konfigfehler(pfad, "[pruefungen] braucht genau aktiv und aus")
    aktiv, aus = pruefungen["aktiv"], pruefungen["aus"]
    if not isinstance(aktiv, list) or not all(isinstance(c, str) for c in aktiv) \
            or len(set(aktiv)) != len(aktiv) or not isinstance(aus, dict):
        raise _konfigfehler(pfad, "aktiv muss eine Liste ohne Doppelte, aus eine Tabelle sein")
    doppelt = sorted(set(aktiv) & set(aus))
    fremd = sorted((set(aktiv) | set(aus)) - set(CHECKS))
    fehlend = sorted(set(CHECKS) - set(aktiv) - set(aus))
    if doppelt or fremd or fehlend:
        raise _konfigfehler(pfad, f"Checks: doppelt {doppelt or '-'}, unbekannt {fremd or '-'}, "
                                  f"weder aktiv noch begründet aus {fehlend or '-'}")
    pflicht = sorted(set(PFLICHT_CHECKS) - set(aktiv))
    if pflicht:
        raise _konfigfehler(pfad, f"Pflicht-Checks nicht abschaltbar: {', '.join(pflicht)}")
    kurz = sorted(c for c, g in aus.items() if not _begruendung(g))
    if kurz:
        raise _konfigfehler(pfad, f"Begründung fehlt oder ist kürzer als {_MIN_BEGRUENDUNG} "
                                  f"Zeichen: {', '.join(kurz)}")
    eintraege = daten.get("ausgeschlossen", [])
    if not isinstance(eintraege, list):
        raise _konfigfehler(pfad, "ausgeschlossen muss [[ausgeschlossen]] sein")
    return Konfig(seed=daten["seed"], max_examples=daten["max_examples"], aktiv=tuple(aktiv),
                  aus=dict(aus),
                  ausgeschlossen=tuple(_ausschluss(nr, t, pfad)
                                       for nr, t in enumerate(eintraege, 1)))


# --- Schema und Ausschlüsse ----------------------------------------------------------------------

def schema_operationen(schema: object) -> set[str]:
    """Operationen `METHODE /pfad` unter `/api/v1/` eines OpenAPI-Dokuments."""
    pfade = schema.get("paths") if isinstance(schema, dict) else None
    if not isinstance(pfade, dict):
        raise NachweisFehler("fuzzing-schema", "OpenAPI-Dokument ohne „paths“")
    return {f"{m.upper()} {p}" for p, eintrag in pfade.items()
            if isinstance(p, str) and p.startswith("/api/v1/") and isinstance(eintrag, dict)
            for m in eintrag if m in _METHODEN}


def _ausschluesse(konfig: Konfig, identitaet: str) -> list[Ausschluss]:
    return [a for a in konfig.ausgeschlossen if a.gilt_fuer(identitaet)]


def ausgeschlossen(operationen: Iterable[str], konfig: Konfig, identitaet: str) -> set[str]:
    """Operationen, deren Pfad ein Ausschluss dieser Identität trifft (wie Schemathesis'
    `--exclude-path-regex`: Suche im Pfad)."""
    regeln = [re.compile(a.pfad_regex) for a in _ausschluesse(konfig, identitaet)]
    return {o for o in operationen if any(r.search(o.split(" ", 1)[1]) for r in regeln)}


# --- JUnit ---------------------------------------------------------------------------------------

def _kurztext(text: str) -> str:
    """Die Check-Titel (`- …`) eines Schemathesis-Fehlschlags, sonst der Textanfang."""
    titel = [z.strip()[2:] for z in text.splitlines() if z.strip().startswith("- ")]
    kurz = "; ".join(titel) if titel else " ".join(text.split())
    return kurz[:_TEXT_HOECHSTENS] or "(ohne Text)"


def werte_junit_aus(text: str) -> tuple[set[str], list[str]]:
    """JUnit-Bericht → (geprüfte Operationen, Fehlschläge). Ein Testfall `METHODE /pfad` ohne
    `skipped` gilt als geprüft; jedes `failure`/`error` (auch im Testfall der zustandsbehafteten
    Phase) ist ein Fehlschlag. DTD/Entitäten sind verboten (die Datei wird vor dem Parsen darauf
    geprüft), sonst kaputte Ausgabe → `fuzzing-ausgabe`."""
    if re.search(r"<!(DOCTYPE|ENTITY)", text, re.IGNORECASE):
        raise NachweisFehler("fuzzing-ausgabe", "JUnit-Bericht mit DTD/Entität — verweigert")
    try:
        wurzel = ElementTree.fromstring(text)  # DTD/Entitäten oben abgewiesen
    except ElementTree.ParseError as e:
        raise NachweisFehler("fuzzing-ausgabe", f"JUnit-Bericht nicht lesbar ({e})") from None
    if wurzel.tag not in ("testsuites", "testsuite"):
        raise NachweisFehler("fuzzing-ausgabe", f"JUnit-Bericht: Wurzel „{wurzel.tag}“")
    geprueft: set[str] = set()
    fehler: list[str] = []
    for fall in wurzel.iter("testcase"):
        name = fall.get("name", "")
        if _OPERATION.fullmatch(name) and fall.find("skipped") is None:
            geprueft.add(name)
        for kind in fall:
            if kind.tag in ("failure", "error"):
                fehler.append(f"{name or '(ohne Namen)'}: {_kurztext(kind.text or '')}")
    return geprueft, fehler


# --- Gate ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class FuzzErgebnis:
    """Ergebnis eines Laufs einer Identität."""

    identitaet: str
    operationen_schema: int
    operationen_geprueft: int
    ausgeschlossen: int
    fehler: list[str]
    #: Ausschlüsse, die keine Operation treffen (veraltet) — Hinweis, nicht blockierend.
    hinweise: list[str] = field(default_factory=list)
    netzschritt: Netzschritt | None = None


def fuzzing_gate(ergebnisse: Sequence[FuzzErgebnis]) -> list[str]:
    """Blockierende Fehlschläge als Text (`<identität>: <operation>: <checks>`). Kein Ergebnis,
    ein Schema ohne Operationen oder weniger geprüfte als nicht ausgeschlossene Operationen →
    `fuzzing-abdeckung`."""
    if not ergebnisse:
        raise NachweisFehler("fuzzing-abdeckung", "kein Fuzzing-Lauf")
    blockiert: list[str] = []
    for e in ergebnisse:
        if e.operationen_schema == 0:
            raise NachweisFehler("fuzzing-abdeckung", f"{e.identitaet}: Schema ohne Operationen")
        soll = e.operationen_schema - e.ausgeschlossen
        if e.operationen_geprueft < soll:
            raise NachweisFehler("fuzzing-abdeckung", f"{e.identitaet}: nur "
                                                      f"{e.operationen_geprueft} von {soll} nicht "
                                                      "ausgeschlossenen Operationen geprüft")
        blockiert += [f"{e.identitaet}: {f}" for f in e.fehler]
    return blockiert


# --- Bereinigung ---------------------------------------------------------------------------------

def _pruefe_werte(geheimnisse: Iterable[str]) -> list[str]:
    werte = list(geheimnisse)
    if not werte or any(not w for w in werte):
        raise NachweisFehler("fuzzing-geheimnis", "leerer Geheimniswert — Bereinigung verweigert")
    return werte


def _dateien(ordner: Path) -> list[Path]:
    return sorted(p for p in ordner.rglob("*") if p.is_file() and not p.is_symlink())


def bereinige(ordner: Path, geheimnisse: Iterable[str]) -> None:
    """Jeden Geheimniswert in jeder Datei unter `ordner` (Bytes, UTF-8) 
    ersetzen (`ENTFERNT`) — JUnit, HAR, Text gleichermaßen."""
    werte = _pruefe_werte(geheimnisse)
    for datei in _dateien(ordner):
        roh = datei.read_bytes()
        neu = roh
        for w in werte:
            neu = neu.replace(w.encode(), ENTFERNT.encode())
        if neu != roh:
            datei.write_bytes(neu)


def _varianten(wert: str) -> list[bytes]:
    return [wert.encode(), wert.encode("utf-16-le"), wert.encode("utf-16-be"),
            quote(wert, safe="").encode()]


def pruefe_geheimnisfrei(ordner: Path, geheimnisse: Iterable[str]) -> None:
    """Kein Geheimniswert (auch UTF-16 oder prozentkodiert) mehr in einer Datei unter `ordner`;
    sonst werden alle Dateien gelöscht und `fuzzing-geheimnis` gemeldet (ohne den Wert)."""
    werte = _pruefe_werte(geheimnisse)
    treffer = [d.name for d in _dateien(ordner)
               if any(v in d.read_bytes() for w in werte for v in _varianten(w))]
    if treffer:
        for datei in _dateien(ordner):
            datei.unlink()
        raise NachweisFehler("fuzzing-geheimnis", f"Anmeldewert nach der Bereinigung noch in "
                                                  f"{', '.join(treffer)} — Berichte gelöscht")


# --- HTTP ----------------------------------------------------------------------------------------

def _http(methode: str, url: str, kopf: dict[str, str],
          daten: bytes | None = None) -> tuple[int, Any, bytes]:
    """Eine Anfrage an die lokale Instanz, ohne Proxy aus der Umgebung; (Status, Kopfzeilen,
    Körper). HTTP-Fehlerstatus sind ein Ergebnis, keine Ausnahme."""
    if not url.startswith(("http://localhost:", "http://127.0.0.1:")):
        raise NachweisFehler("fuzzing-instanz", f"{url}: nur die lokale Instanz")
    oeffner = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    anfrage = urllib.request.Request(url, data=daten, method=methode, headers=kopf)
    try:
        with oeffner.open(anfrage, timeout=30) as a:  # Schema oben auf http://localhost beschränkt
            return a.status, a.headers, a.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers, e.read()
    except (urllib.error.URLError, OSError) as e:
        raise NachweisFehler("fuzzing-instanz", f"{methode} {url}: nicht erreichbar "
                                                f"({getattr(e, 'reason', e)})") from None


def _anmeldung(inst: Testinstanz, identitaet: str) -> dict[str, str]:
    if identitaet == "admin":
        return {"Cookie": f"{COOKIE_NAME}={inst.admin_cookie}"}
    return {"Authorization": f"Bearer {inst.betrachter_token}"}


def sitzungsprobe(inst: Testinstanz) -> None:
    """Admin-Cookie und Betrachter-Token gelten noch (`GET /api/v1/openapi.json` → 200)."""
    for identitaet in IDENTITAETEN:
        status, _, _ = _http("GET", f"{inst.url}/api/v1/openapi.json",
                             _anmeldung(inst, identitaet))
        if status != 200:
            raise NachweisFehler("fuzzing-sitzung", f"Anmeldung während des Laufs verloren — "
                                                    f"Ergebnis ungültig ({identitaet}: {status})")


# --- Werkzeug ------------------------------------------------------------------------------------

def _werkzeug(werkzeuge: dict[str, Werkzeug], name: str) -> Werkzeug:
    w = werkzeuge.get(name)
    if w is None:
        raise NachweisFehler("fuzzing-werkzeug", f"{name}: fehlt in werkzeuge.json")
    return w


def _schemathesis_ordner(nachweise: Path, w: Werkzeug) -> Path:
    if w.werkzeugordner is None:
        raise NachweisFehler("fuzzing-werkzeug", "schemathesis: kein werkzeugordner")
    return nachweise / w.werkzeugordner


def schemathesis_marke(w: Werkzeug, ordner: Path) -> dict[str, str]:
    """Soll-Marke der Installation: Image-Digest und SHA-256 von `uv.lock`."""
    return sbom._soll_marke(w, ordner / "uv.lock", "fuzzing-werkzeug")


def schemathesis_bereitstellen(nachweise: Path, werkzeuge: dict[str, Werkzeug], *,
                               laeufer: Callable[..., object] = ausfuehren) -> list[Netzschritt]:
    """Schemathesis aus `uv.lock` seines Werkzeugordners installieren (Netzschritt, Marke) —
    derselbe Weg wie Bandit (`sast.uv_werkzeug_bereitstellen`)."""
    return sast.uv_werkzeug_bereitstellen(nachweise, werkzeuge, "schemathesis",
                                          "fuzzing-werkzeug", laeufer=laeufer)


# --- Instanz -------------------------------------------------------------------------------------

@dataclass
class Testinstanz:
    """Laufende Wegwerf-Instanz (Kontextmanager: `__exit__` stoppt Caddy und Server; der
    Arbeitsordner bleibt für den Bericht). Zugangsdaten nur im Speicher."""

    url: str
    admin_cookie: str
    betrachter_token: str
    arbeitsordner: Path
    netzschritte: list[Netzschritt] = field(default_factory=list)
    _stopper: list[Callable[[], None]] = field(default_factory=list, repr=False)

    def stoppen(self) -> list[str]:
        """Alles Gestartete in umgekehrter Reihenfolge beenden; Rückgabe: Fehler beim Beenden."""
        fehler: list[str] = []
        while self._stopper:
            try:
                self._stopper.pop()()
            except (NachweisFehler, OSError, subprocess.SubprocessError) as e:
                fehler.append(str(e))
        return fehler

    def __enter__(self) -> Testinstanz:
        return self

    def __exit__(self, typ: object, wert: object, spur: object) -> None:
        fehler = self.stoppen()
        if fehler and typ is None:
            raise NachweisFehler("fuzzing-aufraeumen", "; ".join(fehler))


def pruefe_ports_frei(ports: Iterable[int]) -> None:
    """Jeder Port ist auf 127.0.0.1 bindbar, sonst `fuzzing-port` (nie einen fremden Dienst
    fuzzen oder verdrängen). Mit `SO_REUSEADDR` wie die Dienste selbst: nur ein lauschender
    Fremddienst belegt den Port, nicht die Verbindungen einer eben beendeten Instanz (TIME_WAIT,
    bis zu eine Minute) — sonst scheiterte die zweite Stufe einer Kette an der ersten."""
    belegt: list[int] = []
    for port in ports:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                belegt.append(port)
    if belegt:
        raise NachweisFehler("fuzzing-port", f"Port(s) {', '.join(map(str, belegt))} auf "
                                             "127.0.0.1 belegt — Instanz nicht gestartet")


def _warte_bereit(url: str, prozess: subprocess.Popen[bytes] | None = None) -> None:
    ende = time.monotonic() + _BEREIT_SEKUNDEN
    while time.monotonic() < ende:
        if prozess is not None and prozess.poll() is not None:
            raise NachweisFehler("fuzzing-instanz", f"Dienst beendet (Exit {prozess.returncode})"
                                                    " — siehe server.log im Arbeitsordner")
        try:
            if _http("GET", url, {})[0] == 200:
                return
        except NachweisFehler:
            pass
        time.sleep(0.5)
    raise NachweisFehler("fuzzing-instanz", f"{url}: nach {_BEREIT_SEKUNDEN} s nicht bereit")


def _beende(prozess: subprocess.Popen[bytes]) -> None:
    if prozess.poll() is None:
        prozess.terminate()
        try:
            prozess.wait(_STOP_SEKUNDEN)
        except subprocess.TimeoutExpired:
            prozess.kill()
            prozess.wait(_STOP_SEKUNDEN)


def _einladung(server: Path, umgebung: dict[str, str], *args: str) -> str:
    aus = ausfuehren(["uv", "run", "--locked", "scanview-server", "einladen", *args],
                     "fuzzing-instanz", cwd=server, umgebung=umgebung).stdout
    treffer = re.search(r"#t=([A-Za-z0-9_-]+)\s*$", aus)
    if treffer is None:
        raise NachweisFehler("fuzzing-instanz", "Einladung ohne Link")
    return treffer.group(1)


_CSRF = {"Origin": URL, "X-Scanview": "1", "Content-Type": "application/json"}


def _einloesen(einladung: str) -> str:
    """Einladung per Passwort einlösen (zufälliges Passwort, nur im Speicher) → Cookie-Wert."""
    daten = json.dumps({"token": einladung, "passwort": secrets.token_urlsafe(24)}).encode()
    status, kopf, _ = _http("POST", f"{URL}/auth/einladung/passwort", dict(_CSRF), daten)
    werte = [c.split(";", 1)[0].split("=", 1)[1] for c in (kopf.get_all("Set-Cookie") or [])
             if c.startswith(f"{COOKIE_NAME}=")] if status == 200 else []
    if len(werte) != 1 or not _ANMELDEWERT.fullmatch(werte[0]):
        raise NachweisFehler("fuzzing-instanz", f"Einladung nicht eingelöst (Status {status})")
    return werte[0]


def _api_token(cookie: str) -> str:
    daten = json.dumps({"name": "fuzzing", "umfang": "lesen", "tage": 1}).encode()
    status, _, koerper = _http("POST", f"{URL}/api/v1/konto/tokens",
                               {**_CSRF, "Cookie": f"{COOKIE_NAME}={cookie}"}, daten)
    try:
        token = json.loads(koerper)["token"] if status == 201 else None
    except (ValueError, KeyError, TypeError):
        token = None
    if not isinstance(token, str) or not _ANMELDEWERT.fullmatch(token):
        raise NachweisFehler("fuzzing-instanz", f"API-Token nicht angelegt (Status {status})")
    return token


def _container_name(kennung: str = "fuzzing") -> str:
    return f"scanview-{kennung}-{os.getpid()}"


def _container_frei(name: str) -> None:
    namen = ausfuehren([_laufzeit(), "ps", "-a", "--format", "{{.Names}}"],
                       "fuzzing-instanz").stdout.split()
    if name in namen:
        raise NachweisFehler("fuzzing-instanz", f"Container {name} existiert schon")


def _container_stoppen(name: str) -> None:
    ausfuehren([_laufzeit(), "stop", "-t", str(_STOP_SEKUNDEN), name], "fuzzing-aufraeumen")


def _paket_kennung(paket: Path) -> str:
    """Projektkennung (`projectId`) aus der `project.json` des Pakets; sie ist zugleich der
    Ordnername im Datenordner und darf kein Pfadstück sein."""
    try:
        kennung = json.loads((paket / "project.json").read_text("utf-8"))["projectId"]
    except (OSError, ValueError, KeyError, TypeError):
        raise NachweisFehler("fuzzing-instanz", f"{paket}/project.json: nicht lesbar oder ohne "
                                                "projectId") from None
    if not isinstance(kennung, str) or re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", kennung) is None:
        raise NachweisFehler("fuzzing-instanz", f"{paket}/project.json: projectId „{kennung}“ "
                                                "ungültig")
    return kennung


def starte_testinstanz(worktree: Path, arbeitsordner: Path, werkzeuge: dict[str, Werkzeug],
                       *, paket: Path | None = None) -> Testinstanz:
    """Wegwerf-Instanz aus dem Worktree starten (siehe Moduldoku); Rückgabe als Kontextmanager.
    Scheitert der Start, wird alles bereits Gestartete wieder beendet. Ohne `paket` liegen zwei
    synthetische Attrappen-Pakete (`PAKETE`) im Datenordner, der Betrachter liest das erste; mit
    `paket` genau dieses Verzeichnis (Kopie im Arbeitsordner, vom Caddy nur lesend eingebunden)
    als einziges Projekt, und der Betrachter erhält darauf die Rolle `betrachter`. Jede Art hat
    ihren eigenen Unterordner und Containernamen (`fuzzing` bzw. `egress`), damit beide Stufen
    einer Release-Kette im selben Arbeitsordner nacheinander laufen."""
    pruefe_ports_frei((CADDY_PORT, SERVER_PORT))
    dist = worktree / "web" / "dist"
    if not dist.is_dir() or not any(dist.iterdir()):
        raise NachweisFehler("fuzzing-instanz", f"{dist}: fehlt oder leer (erst bauen)")
    kennung = "fuzzing" if paket is None else "egress"
    projekt = PAKETE[0] if paket is None else _paket_kennung(paket)
    caddy = _werkzeug(werkzeuge, "caddy-test")
    name = _container_name(kennung)
    _container_frei(name)
    ordner = arbeitsordner / kennung
    try:
        ordner.mkdir(parents=True)
    except FileExistsError:
        raise NachweisFehler("fuzzing-instanz", f"{ordner}: existiert schon") from None
    zustand, daten = ordner / "state", ordner / "data"
    zustand.mkdir()
    daten.mkdir()
    server = worktree / "server"
    # Explizite Umgebung: Bau-Positivliste, kein Netz für uv, dazu nur die benannten Variablen
    # des Dienstes.
    umgebung = {**bau_umgebung(ordner / "umgebung"), "UV_OFFLINE": "1",
                "SCANVIEW_DATA": str(daten), "SCANVIEW_STATE": str(zustand),
                "SCANVIEW_ORIGIN": URL, "SCANVIEW_RP_ID": "localhost"}
    if paket is None:
        ausfuehren(["uv", "run", "--locked", "python", "-c", _PAKETE_CODE, str(daten), *PAKETE],
                   "fuzzing-instanz", cwd=server, umgebung=umgebung)
    else:
        shutil.copytree(paket, daten / projekt, symlinks=False)
    inst = Testinstanz(url=URL, admin_cookie="", betrachter_token="", arbeitsordner=ordner)
    try:
        with (ordner / "server.log").open("wb") as log:
            prozess = subprocess.Popen(
                ["uv", "run", "--locked", "scanview-server", "serve", "--port", str(SERVER_PORT)],
                cwd=server, env=umgebung, stdin=subprocess.DEVNULL, stdout=log,
                stderr=subprocess.STDOUT)
        inst._stopper.append(lambda: _beende(prozess))
        _warte_bereit(f"http://127.0.0.1:{SERVER_PORT}/api/gesundheit", prozess)
        plattform = worktree / "deploy" / "platform"
        befehl = docker_befehl(
            caddy, ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
            {plattform / "Caddyfile.lokal": "/etc/caddy/Caddyfile",
             plattform / "scanview.caddy": "/etc/caddy/scanview.caddy",
             dist: "/srv/web", daten: "/srv/data"},
            netz="host", tmpfs={"/data": "", "/config": ""}, name=name, abgesetzt=True,
            umgebung={"SCANVIEW_WEB": "/srv/web", "SCANVIEW_DATA": "/srv/data",
                      "SCANVIEW_UPSTREAM": f"127.0.0.1:{SERVER_PORT}"})
        inst.netzschritte.append(Netzschritt(befehl, "deploy/platform", sbom._jetzt()))
        ausfuehren(befehl, "fuzzing-instanz")
        inst._stopper.append(lambda: _container_stoppen(name))
        _warte_bereit(f"{URL}/api/gesundheit")
        admin = _einladung(server, umgebung, "--name", "Fuzzing Admin", "--email",
                           "fuzzing-admin@example.test", "--admin")
        betrachter = _einladung(server, umgebung, "--name", "Fuzzing Betrachter", "--email",
                                "fuzzing-betrachter@example.test", "--projekt",
                                f"{projekt}=betrachter")
        inst.admin_cookie = _einloesen(admin)
        inst.betrachter_token = _api_token(_einloesen(betrachter))
    except BaseException:
        inst.stoppen()
        raise
    return inst


# --- Lauf ----------------------------------------------------------------------------------------

def _toml_text(wert: str) -> str:
    return json.dumps(wert, ensure_ascii=False)


def schemathesis_konfig(konfig: Konfig, identitaet: str, anmeldewert: str) -> str:
    """Konfigurationsdatei eines Laufs: Cache aus (Wurzel ist schreibgeschützt), keine erfundenen
    Anmeldeparameter, Anmeldung über das OpenAPI-Sicherheitsschema (so prüft `ignored_auth`
    wirklich ohne sie); Admin mit CSRF-Kopfzeilen, Betrachter mit Drossel unter der des
    Dienstes. Der Anmeldewert muss base64url-artig sein (keine Platzhalter, Anführungszeichen,
    Umbrüche)."""
    if not _ANMELDEWERT.fullmatch(anmeldewert):
        raise NachweisFehler("fuzzing-anmeldung", f"{identitaet}: Anmeldewert mit unerwarteten "
                                                  "Zeichen — nicht in die Konfiguration "
                                                  "geschrieben")
    zeilen = [*([f"rate-limit = {_toml_text(TOKEN_RATE)}"] if identitaet == "betrachter" else []),
              "[cache]", "enabled = false", "",
              "[generation]", "with-security-parameters = false", 'database = "none"', "",
              "[output.sanitization]", "enabled = true", ""]
    if identitaet == "admin":
        zeilen += ["[headers]", f"Origin = {_toml_text(URL)}", 'X-Scanview = "1"', "",
                   "[auth.openapi.cookieAuth]", f"api_key = {_toml_text(anmeldewert)}"]
    else:
        zeilen += ["[auth.openapi.bearerAuth]", f"bearer = {_toml_text(anmeldewert)}"]
    return "\n".join(zeilen) + "\n"


def _geheim_schreiben(pfad: Path, text: str) -> None:
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def _starte(befehl: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False, stdin=subprocess.DEVNULL,
                              env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler("fuzzing-werkzeug", f"{befehl[0]} nicht ausführbar: "
                                                 f"{e.strerror}") from None


def _ohne(text: str, werte: Iterable[str]) -> str:
    for w in werte:
        text = text.replace(w, ENTFERNT)
    return text


def schemathesis_laufen(inst: Testinstanz, identitaet: Identitaet, konfig: Konfig,
                        werkzeuge: dict[str, Werkzeug], ziel: Path, *, nachweise: Path,
                        laeufer: Laeufer = _starte) -> FuzzErgebnis:
    """Ein Lauf einer Identität (siehe Moduldoku). Bereinigter JUnit-Bericht nach `ziel`."""
    w = _werkzeug(werkzeuge, "schemathesis")
    ordner = _schemathesis_ordner(nachweise, w)
    if not sbom._pruefe_marke(ordner / ".venv", schemathesis_marke(w, ordner),
                              "fuzzing-werkzeug"):
        raise NachweisFehler("fuzzing-werkzeug", f"{ordner / '.venv'}: schemathesis nicht "
                                                 "installiert (schemathesis_bereitstellen)")
    if ziel.exists():
        raise NachweisFehler("fuzzing-ausgabe", f"{ziel}: existiert schon")
    geheim = [inst.admin_cookie, inst.betrachter_token]
    anmeldewert = inst.admin_cookie if identitaet == "admin" else inst.betrachter_token
    status, _, koerper = _http("GET", f"{inst.url}/api/v1/openapi.json",
                               _anmeldung(inst, identitaet))
    if status != 200:
        raise NachweisFehler("fuzzing-schema", f"{identitaet}: OpenAPI-Abruf mit Status {status}")
    try:
        schema = json.loads(koerper)
    except ValueError:
        raise NachweisFehler("fuzzing-schema", f"{identitaet}: OpenAPI ist kein JSON") from None
    operationen = schema_operationen(schema)
    raus = ausgeschlossen(operationen, konfig, identitaet)
    hinweise = [f"{identitaet}: Ausschluss ohne Treffer: {a.pfad_regex}"
                for a in _ausschluesse(konfig, identitaet)
                if not any(re.search(a.pfad_regex, o.split(" ", 1)[1]) for o in operationen)]
    lauf = inst.arbeitsordner / f"lauf-{identitaet}"
    aus = lauf / "aus"
    aus.mkdir(parents=True)
    schema_datei = lauf / "schema.json"
    schema_datei.write_bytes(koerper)
    konfig_datei = lauf / "schemathesis.toml"
    args = [_SCHEMATHESIS, "--config-file", _KONFIG_MOUNT, "run", _SCHEMA_MOUNT,
            "--url", inst.url, "--seed", str(konfig.seed),
            "--max-examples", str(konfig.max_examples), "--checks", ",".join(konfig.aktiv),
            "--include-path-regex", "^/api/v1/",
            *(["--exclude-path-regex", "|".join(f"(?:{a.pfad_regex})"
                                                for a in _ausschluesse(konfig, identitaet))]
              if _ausschluesse(konfig, identitaet) else []),
            "--report", "junit", "--report-junit-path", f"{_AUS}/{_JUNIT}", "--no-color"]
    try:
        _geheim_schreiben(konfig_datei, schemathesis_konfig(konfig, identitaet, anmeldewert))
        befehl = docker_befehl(w, args, {ordner: "/werkzeug", konfig_datei: _KONFIG_MOUNT,
                                         schema_datei: _SCHEMA_MOUNT}, {aus: _AUS}, netz="host")
        schritt = Netzschritt(befehl, f"nachweise/{w.werkzeugordner}", sbom._jetzt())
        ergebnis = laeufer(befehl)
    finally:
        konfig_datei.unlink(missing_ok=True)
    junit = aus / _JUNIT
    if ergebnis.returncode not in (0, 1) or not junit.is_file() or junit.is_symlink():
        auszug = _ohne((ergebnis.stderr or ergebnis.stdout or "").strip()[-1500:], geheim)
        raise NachweisFehler("fuzzing-werkzeug", f"schemathesis ({identitaet}) Exit "
                                                 f"{ergebnis.returncode} ohne JUnit-Bericht: "
                                                 f"{auszug or '(keine Ausgabe)'}")
    bereinige(aus, geheim)
    pruefe_geheimnisfrei(aus, geheim)
    geprueft, fehler = werte_junit_aus(junit.read_text(encoding="utf-8"))
    ziel.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(junit, ziel)
    return FuzzErgebnis(identitaet=identitaet, operationen_schema=len(operationen),
                        operationen_geprueft=len(geprueft & (operationen - raus)),
                        ausgeschlossen=len(raus), fehler=[_ohne(f, geheim) for f in fehler],
                        hinweise=hinweise, netzschritt=schritt)


# --- Gesamtlauf ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ergebnis:
    """Ergebnis von `pruefen`: Bericht (JSON-fähig), blockierende Fehlschläge, Netzschritte."""

    bericht: dict[str, Any]
    blockiert: list[str]
    netzschritte: list[Netzschritt]


def als_json(e: FuzzErgebnis) -> dict[str, Any]:
    return {"operationen_schema": e.operationen_schema,
            "operationen_geprueft": e.operationen_geprueft, "ausgeschlossen": e.ausgeschlossen,
            "fehler": e.fehler, "hinweise": e.hinweise}


def pruefen(worktree: Path, werkzeuge: dict[str, Werkzeug], berichte: Path,
            arbeitsordner: Path) -> Ergebnis:
    """Konfiguration laden, Schemathesis bereitstellen (Netzschritt), Instanz starten, je
    Identität ein Lauf mit Sitzungsprobe (Rohberichte `<berichte>/fuzzing-<identität>.xml`),
    dann das Gate. Blockierende Fehlschläge stehen im Ergebnis, jeder andere Regelverstoß bricht
    ab."""
    nachweise = worktree / "nachweise"
    konfig = lade_konfig(nachweise / KONFIG)
    schritte = schemathesis_bereitstellen(nachweise, werkzeuge)
    start = time.monotonic()
    ergebnisse: list[FuzzErgebnis] = []
    with starte_testinstanz(worktree, arbeitsordner, werkzeuge) as inst:
        schritte += inst.netzschritte
        for identitaet in IDENTITAETEN:
            e = schemathesis_laufen(inst, identitaet, konfig, werkzeuge,
                                    berichte / BERICHTE[identitaet], nachweise=nachweise)
            if e.netzschritt is not None:
                schritte.append(e.netzschritt)
            sitzungsprobe(inst)
            ergebnisse.append(e)
    blockiert = fuzzing_gate(ergebnisse)
    w = werkzeuge["schemathesis"]
    bericht = {
        "werkzeug": {"image": w.image, "version": w.version},
        "instanz": {"url": URL, "server_port": SERVER_PORT, "pakete": list(PAKETE)},
        "seed": konfig.seed, "max_examples": konfig.max_examples,
        "checks": {"aktiv": list(konfig.aktiv), "aus": konfig.aus},
        "ausgeschlossen": [{"identitaet": a.identitaet, "pfad_regex": a.pfad_regex,
                            "grund": a.grund} for a in konfig.ausgeschlossen],
        "laeufe": {e.identitaet: als_json(e) for e in ergebnisse},
        "dauer_sekunden": round(time.monotonic() - start),
        "sitzungsprobe": "gültig", "blockierend": blockiert,
    }
    return Ergebnis(bericht, blockiert, schritte)
