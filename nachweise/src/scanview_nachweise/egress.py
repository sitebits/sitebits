"""Egress-Test: Portal und Viewer bauen keine Verbindungen nach außen auf (Air-Gap-fähig).

Zwei Teile, beide blockierend:

- Statisch (`bundle_urls`, `statisch_gate`): jede absolute URL (`http`, `https`, `ws`, `wss`) in
  jeder Datei des gebauten Web-Bundles zählt als fremd — das Bundle lädt nur relativ. Ein Vergleich
  mit dem eigenen Origin entfällt, weil der Origin erst beim Betreiber feststeht. Was nie
  abgerufen wird (SVG-Namensraum, Lizenzhinweis, Link ohne Abruf), steht mit Art und Grund in
  `docs/compliance/egress-ausnahmen.toml`. Keine Datei bleibt ungeprüft: eine unbekannte
  Dateiendung, ein Symlink oder ein leeres Bundle bricht ab.
- Dynamisch (`browser_lauf`, `egress_gate`): ein Headless-Chromium im gehärteten Container
  (Image `playwright`, Host-Netz, nur gegen die Wegwerf-Instanz auf 127.0.0.1) durchläuft
  Anmeldeseite, öffentliche Barrierefreiheitserklärung, Portal, Viewer (samt Standpunkt-Sprung)
  und Verlaufsseite eines synthetischen Testpakets. Jede Anfrage auf jedem Weg wird erfasst und,
  wenn sie nicht zum eigenen Origin gehört, abgebrochen (nichts verlässt die Maschine). Am Ende
  jeder Seite steht eine **Kontrollanfrage** an eine nicht auflösbare Adresse: fehlt sie im
  Ergebnis, war die Erfassung nicht wirksam und das Ergebnis ist ungültig. Die Abdeckung (Punkte
  geladen, Paketdateien, Panoramakachel, Projektliste) muss erreicht sein, sonst hätte der Lauf
  die Ladewege gar nicht berührt. Ausgewertet werden nur die Ergebnisdateien des Laufs, nie der
  Exit-Code allein.

Die Instanz kommt aus `fuzzing.starte_testinstanz(…, paket=…)`; Zugangsdaten stehen nur im
Speicher und für die Dauer des Laufs in einer 0600-Datei außerhalb des Ausgabeordners."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from . import fuzzing, sbom
from .artefakte import Netzschritt
from .fehler import NachweisFehler
from .werkzeuge import (
    INSTANZ_CADDY_PORT,
    INSTANZ_SERVER_PORT,
    Werkzeug,
    _laufzeit,
    docker_befehl,
    gefilterte_umgebung,
)

CADDY_PORT = INSTANZ_CADDY_PORT
SERVER_PORT = INSTANZ_SERVER_PORT
ORIGIN = fuzzing.URL
#: Synthetisches Testpaket (Importwerkzeug aus einer synthetischen E57-Datei, lokale Koordinaten).
PAKET = Path("nachweise/tests/fixtures/egress/paket")
AUSNAHMEN = Path("docs/compliance/egress-ausnahmen.toml")
BERICHT_STATISCH = "egress-statisch.json"
BERICHT_BROWSER = "egress-browser.json"
#: Seiten des Laufs (Namen wie `seite` im Ergebnis); jede trägt am Ende beide Kontrollanfragen.
ERWARTETE_SEITEN = ("anmelden", "barrierefreiheit", "portal", "viewer", "portal-zurueck",
                    "verlauf")
#: Nicht auflösbare Adresse (`.invalid` ist reserviert): fetch (http) und Bild (https).
KONTROLL_HOST = "egress-kontrolle.invalid"
KONTROLL_ARTEN = {"http": "fetch", "https": "Bild"}
QUELLEN = ("request", "requestfailed", "csp", "websocket", "worker")
ARTEN = ("namensraum", "lizenzhinweis", "link-ohne-abruf")

#: Dateiendungen, die als Text durchsucht werden.
TEXT_ENDUNGEN = frozenset({".js", ".mjs", ".css", ".html", ".json", ".svg", ".webmanifest",
                           ".map", ".txt"})
#: Binäre Dateiendungen, durchsucht in ihren ASCII-Anteilen (`.wasm`; `.png` der Web-App-Symbole,
#: deren Textblöcke unkomprimiert lesbar wären; `.task` = MediaPipe-Modell, ein ZIP, das nur
#: unkomprimierte Einträge haben darf, sonst bliebe sein Inhalt ungeprüft).
BINAER_ENDUNGEN = frozenset({".wasm", ".png", ".task"})
_URL = re.compile(r"(?:https?|wss?)://[^\s\"'`<>()\\]+")
_URL_BYTES = re.compile(rb"(?:https?|wss?)://[^\s\"'`<>()\\\x00-\x20\x7f-\xff]+")
_PRAEFIX = re.compile(r"(?:https?|wss?)://[A-Za-z0-9.-]+(?::\d+)?/\S*")
_MIN_BEGRUENDUNG = 20
_PROJEKT = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_CONTAINER = "scanview-egress-browser"
_FRIST_SEKUNDEN = 1800
_STDERR_AUSZUG = 1500
_MOUNT_WEB = "/web"
_MOUNT_AUSGABE = "/arbeit"
_MOUNT_ZUSTAND = "/zustand/zustand.json"
_PLAYWRIGHT_CLI = f"{_MOUNT_WEB}/node_modules/@playwright/test/cli.js"
_PLAYWRIGHT_KONFIG = f"{_MOUNT_WEB}/playwright.egress.config.ts"
ANFRAGEN_DATEI = "egress-anfragen.json"
BERICHT_DATEI = "egress-bericht.json"

Laeufer = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


# --- Statische Prüfung ---------------------------------------------------------------------------

@dataclass(frozen=True)
class UrlFund:
    datei: str
    url: str
    host: str


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return re.sub(r"[/:?#].*", "", url.split("://", 1)[1]).lower()


def bundle_dateien(dist: Path) -> list[Path]:
    """Jede Datei unter `dist` (sortiert); fehlendes oder leeres Bundle und jeder Symlink
    brechen ab — was nicht gelesen wird, bliebe ungeprüft."""
    if not dist.is_dir() or dist.is_symlink():
        raise NachweisFehler("egress-datei", f"{dist}: Bundle-Ordner fehlt (erst bauen)")
    dateien: list[Path] = []
    for pfad in sorted(dist.rglob("*")):
        if pfad.is_symlink():
            raise NachweisFehler("egress-datei", f"{pfad.relative_to(dist).as_posix()}: Symlink im "
                                                 "Bundle — nicht geprüft, verweigert")
        if pfad.is_file():
            dateien.append(pfad)
    if not dateien:
        raise NachweisFehler("egress-datei", f"{dist}: Bundle leer")
    return dateien


def _nur_unkomprimiert(pfad: Path, rel: str) -> None:
    """Ein `.task`-Modell muss ein ZIP mit ausschließlich gespeicherten (nicht komprimierten)
    Einträgen sein — nur dann sieht die Byte-Suche seinen Inhalt."""
    try:
        with zipfile.ZipFile(pfad) as z:
            gepackt = [i.filename for i in z.infolist() if i.compress_type != zipfile.ZIP_STORED]
    except zipfile.BadZipFile:
        raise NachweisFehler("egress-datei", f"{rel}: .task ist kein ZIP — Datei bliebe "
                                             "ungeprüft") from None
    if gepackt:
        raise NachweisFehler("egress-datei", f"{rel}: komprimierte Einträge {', '.join(gepackt)} "
                                             "— Inhalt bliebe ungeprüft")


def bundle_urls(dist: Path) -> list[UrlFund]:
    """Jede absolute URL (`http`, `https`, `ws`, `wss`) in jeder Datei des Bundles, je Datei und
    URL einmal. Textdateien ganz, `.wasm`, `.png` und `.task` in ihren ASCII-Anteilen; jede andere
    Endung ist ein Fehler `egress-datei`."""
    funde: list[UrlFund] = []
    for pfad in bundle_dateien(dist):
        rel = pfad.relative_to(dist).as_posix()
        endung = pfad.suffix.lower()
        roh = pfad.read_bytes()
        if endung in TEXT_ENDUNGEN:
            urls = _URL.findall(roh.decode("utf-8", errors="replace"))
        elif endung in BINAER_ENDUNGEN:
            if endung == ".task":
                _nur_unkomprimiert(pfad, rel)
            urls = [u.decode("ascii") for u in _URL_BYTES.findall(roh)]
        else:
            raise NachweisFehler("egress-datei", f"{rel}: Dateiendung „{endung or '(keine)'}“ "
                                                 "unbekannt — Datei bliebe ungeprüft")
        funde += [UrlFund(rel, u, _host(u)) for u in dict.fromkeys(urls)]
    return funde


@dataclass(frozen=True)
class Ausnahme:
    """Eine absolute URL im Bundle, die nie abgerufen wird: genau `url` oder alles unter
    `praefix` (ein vollständiger Host samt Schrägstrich, nie ein Platzhalter)."""

    url: str | None
    praefix: str | None
    art: str
    grund: str
    datum: date

    def erfasst(self, fund: UrlFund) -> bool:
        if self.url is not None:
            return fund.url == self.url
        return self.praefix is not None and fund.url.startswith(self.praefix)

    def beschreibung(self) -> str:
        return self.url if self.url is not None else f"{self.praefix}…"


_AUSNAHME_FELDER = {"url", "praefix", "art", "grund", "datum"}


def _ausnahme(nr: int, t: object, pfad: Path) -> Ausnahme:
    def fehler(text: str) -> NachweisFehler:
        return NachweisFehler("egress-ausnahmen", f"{pfad.name} Ausnahme Nr. {nr}: {text}")

    if not isinstance(t, dict):
        raise fehler("keine Tabelle")
    unbekannt = sorted(set(t) - _AUSNAHME_FELDER)
    if unbekannt:
        raise fehler(f"unbekannte Felder {', '.join(unbekannt)}")
    url, praefix = t.get("url"), t.get("praefix")
    if (url is None) == (praefix is None):
        raise fehler("genau eines von „url“ oder „praefix“")
    wert = url if url is not None else praefix
    if not isinstance(wert, str) or not re.fullmatch(r"(?:https?|wss?)://\S+", wert):
        raise fehler("„url“ bzw. „praefix“ muss eine absolute http(s)/ws(s)-Adresse sein")
    if "*" in wert:
        raise fehler("keine Platzhalter in Adressen")
    if praefix is not None and not _PRAEFIX.fullmatch(praefix):
        raise fehler("„praefix“ muss einen vollständigen Host samt Schrägstrich enthalten "
                     "(https://host/…)")
    if t.get("art") not in ARTEN:
        raise fehler(f"„art“ muss eine von {', '.join(ARTEN)} sein")
    grund = t.get("grund")
    if not isinstance(grund, str) or len(grund.strip()) < _MIN_BEGRUENDUNG:
        raise fehler(f"„grund“ fehlt oder ist kürzer als {_MIN_BEGRUENDUNG} Zeichen")
    datum = t.get("datum")
    if not isinstance(datum, date) or isinstance(datum, datetime):
        raise fehler("„datum“ muss ein TOML-Datum JJJJ-MM-TT sein")
    return Ausnahme(url=url, praefix=praefix, art=t["art"], grund=grund, datum=datum)


def lade_ausnahmen(pfad: Path) -> list[Ausnahme]:
    """`egress-ausnahmen.toml` laden (fail-closed). Leere Datei = keine Ausnahmen."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NachweisFehler("egress-ausnahmen", f"{pfad}: Datei fehlt") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler("egress-ausnahmen", f"{pfad}: nicht lesbar ({e})") from None
    if set(daten) - {"ausnahme"}:
        raise NachweisFehler("egress-ausnahmen", f"{pfad}: nur [[ausnahme]]-Einträge erlaubt")
    eintraege = daten.get("ausnahme", [])
    if not isinstance(eintraege, list):
        raise NachweisFehler("egress-ausnahmen", f"{pfad}: „ausnahme“ muss [[ausnahme]] sein")
    return [_ausnahme(nr, t, pfad) for nr, t in enumerate(eintraege, 1)]


def statisch_gate(funde: Iterable[UrlFund], ausnahmen: Iterable[Ausnahme]) -> list[str]:
    """Blockierende Funde als Text: absolute URL im Bundle ohne passende Ausnahme."""
    ausnahmen = list(ausnahmen)
    return [f"{f.datei}: {f.url} — Abruf beseitigen oder mit Art und Grund in "
            f"{AUSNAHMEN.name} ausnehmen"
            for f in funde if not any(a.erfasst(f) for a in ausnahmen)]


def ungenutzte_ausnahmen(funde: Iterable[UrlFund], ausnahmen: Iterable[Ausnahme]) -> list[str]:
    """Ausnahmen, die keinen Fund erfassen (veraltet) — Meldung, nicht blockierend."""
    funde = list(funde)
    return [f"Ausnahme ohne Fund: {a.beschreibung()}" for a in ausnahmen
            if not any(a.erfasst(f) for f in funde)]


# --- Auswertung der Browser-Anfragen -------------------------------------------------------------

def _urteil(ergebnis: Mapping[str, Any]) -> tuple[str, list[str], list[dict[str, Any]],
                                                   Mapping[str, Any]]:
    """Form des Ergebnisses prüfen (`egress-erfassung`): Origin, Seiten, Anfragen, Geladenes."""
    origin, seiten = ergebnis.get("origin"), ergebnis.get("seiten")
    anfragen, geladen = ergebnis.get("anfragen"), ergebnis.get("geladen")
    if not isinstance(origin, str) or not isinstance(seiten, list) \
            or not isinstance(anfragen, list) or not isinstance(geladen, dict):
        raise NachweisFehler("egress-erfassung", "Ergebnis unvollständig (origin, seiten, "
                                                 "anfragen, geladen) — ungültig")
    for a in anfragen:
        if not (isinstance(a, dict) and isinstance(a.get("url"), str)
                and a.get("quelle") in QUELLEN and isinstance(a.get("seite"), str)):
            raise NachweisFehler("egress-erfassung", f"Anfrage ohne url/quelle/seite: {a!r:.120}")
    return origin, [str(s) for s in seiten], anfragen, geladen


def _schema_host(url: str) -> tuple[str, str] | None:
    try:
        teile = urlsplit(url)
        return (teile.scheme.lower(), (teile.hostname or "").lower()) if teile.hostname else None
    except ValueError:
        return None


def _ursprung(url: str) -> tuple[str, str, int] | None:
    """(Schema, Host, Port) einer absoluten http(s)/ws(s)-Adresse; `ws`/`wss` zählen wie
    `http`/`https` (gleicher Host und Port = gleicher Ursprung); sonst None."""
    try:
        t = urlsplit(url)
        host, port = (t.hostname or "").lower(), t.port
    except ValueError:
        return None
    schema = {"ws": "http", "wss": "https"}.get(t.scheme.lower(), t.scheme.lower())
    if schema not in ("http", "https") or not host:
        return None
    return schema, host, port if port is not None else (443 if schema == "https" else 80)


def _kontrolle(url: str) -> str | None:
    """`fetch`/`Bild`, wenn die Adresse eine Kontrollanfrage ist (Schema und Host)."""
    sh = _schema_host(url)
    return KONTROLL_ARTEN.get(sh[0]) if sh and sh[1] == KONTROLL_HOST else None


def _pruefe_erfassung(anfragen: list[dict[str, Any]], seiten: list[str]) -> None:
    fehlend = [s for s in ERWARTETE_SEITEN if s not in seiten]
    if fehlend:
        raise NachweisFehler("egress-erfassung", "Kontrollanfrage nicht erfasst — Ergebnis "
                                                 f"ungültig (Seite nicht durchlaufen: "
                                                 f"{', '.join(fehlend)})")
    vorhanden = {(a["seite"], _kontrolle(a["url"])) for a in anfragen}
    # Das Bild erreicht die Netzebene (request/requestfailed) auch dann, wenn die Richtlinie es
    # meldet; nur der fetch erscheint ausschließlich als Richtlinien-Verstoß.
    netz = {a["seite"] for a in anfragen
            if a["quelle"] in ("request", "requestfailed") and _kontrolle(a["url"]) == "Bild"}
    luecken = [f"{s}: {art}" for s in seiten for art in KONTROLL_ARTEN.values()
               if (s, art) not in vorhanden or (art == "Bild" and s not in netz)]
    if luecken:
        raise NachweisFehler("egress-erfassung", "Kontrollanfrage nicht erfasst — Ergebnis "
                                                 f"ungültig ({'; '.join(luecken)})")


#: Pflichtfunde der Abdeckung: Bezeichnung, Prüfung auf den Pfad einer eigenen 2xx-Antwort.
_ABDECKUNG: tuple[tuple[str, Callable[[str], bool]], ...] = (
    ("project.json", lambda p: p.endswith("/project.json")),
    ("Punktwolke (.copc.laz)", lambda p: p.endswith(".copc.laz")),
    ("Draufsicht (topview.png)", lambda p: p.endswith("/topview.png")),
    ("Panoramakachel (/panos/…)", lambda p: "/panos/" in p and p.endswith(".jpg")),
    ("Projekt-API (/api/projekte, /api/v1/projekte)",
     lambda p: re.fullmatch(r"/api(?:/v1)?/projekte(?:/[^?]*)?(?:\?.*)?", p) is not None),
)


def _pruefe_abdeckung(geladen: Mapping[str, Any]) -> None:
    punkte = geladen.get("punkte")
    eigene = geladen.get("eigene")
    if not isinstance(eigene, list) or not isinstance(punkte, int) or isinstance(punkte, bool):
        raise NachweisFehler("egress-abdeckung", "Ergebnis ohne geladene Punkte und eigene "
                                                 "Anfragen")
    fehlt: list[str] = []
    if punkte <= 0:
        fehlt.append("Punkte im Viewer geladen (0)")
    for bezeichnung, passt in _ABDECKUNG:
        if not any(isinstance(e, dict) and isinstance(e.get("pfad"), str) and passt(e["pfad"])
                   and isinstance(e.get("status"), int) and 200 <= e["status"] < 300
                   for e in eigene):
            fehlt.append(f"{bezeichnung} ohne 2xx-Antwort")
    if fehlt:
        raise NachweisFehler("egress-abdeckung", "Abdeckung nicht erreicht — der Lauf hat die "
                                                 f"Ladewege nicht berührt: {'; '.join(fehlt)}")


def egress_gate(ergebnis: Mapping[str, Any], origin: str) -> list[str]:
    """(1) Erfassung wirksam: beide Kontrollanfragen je Seite stehen im Ergebnis, sonst
    `egress-erfassung`; danach werden genau diese entfernt. (2) Abdeckung (`egress-abdeckung`).
    (3) Jede übrige Anfrage, deren Ursprung (Schema, Host, Port) nicht `origin` ist, blockiert;
    `data:`, `blob:` und `about:` sind kein Netzverkehr. Ein CSP-Verstoß ohne erkennbares
    Ziel blockiert ebenfalls (fail-closed)."""
    erfasst, seiten, anfragen, geladen = _urteil(ergebnis)
    if erfasst != origin:
        raise NachweisFehler("egress-erfassung", f"erfasster Origin „{erfasst}“ ≠ Origin der "
                                                 f"Instanz „{origin}“ — Ergebnis ungültig")
    _pruefe_erfassung(anfragen, seiten)
    _pruefe_abdeckung(geladen)
    eigen = _ursprung(origin)
    blockiert: list[str] = []
    for a in anfragen:
        url = a["url"]
        if _kontrolle(url) is not None or url.lower().startswith(("data:", "blob:", "about:")):
            continue
        ziel = _ursprung(url)
        if ziel is None:
            if a["quelle"] == "csp":
                blockiert.append(f"{a['seite']}: csp {url} — Verstoß gegen die "
                                 "Inhaltssicherheitsrichtlinie ohne erkennbares Ziel")
            else:
                blockiert.append(f"{a['seite']}: {a['quelle']} {url} — keine auswertbare Adresse")
        elif ziel != eigen:
            blockiert.append(f"{a['seite']}: {a['quelle']} {url} — fremder Ursprung")
    return blockiert


# --- Browser-Lauf --------------------------------------------------------------------------------

def _starte_mit_frist(befehl: list[str], name: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(befehl, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", check=False, stdin=subprocess.DEVNULL,
                              env=gefilterte_umgebung(), timeout=_FRIST_SEKUNDEN)
    except subprocess.TimeoutExpired:
        subprocess.run([_laufzeit(), "rm", "-f", name], capture_output=True, check=False,
                       stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
        raise NachweisFehler("egress-browser", f"Browser-Lauf nach {_FRIST_SEKUNDEN} s "
                                               "abgebrochen (Zeitlimit)") from None
    except OSError as e:
        raise NachweisFehler("egress-browser", f"{befehl[0]} nicht ausführbar: "
                                               f"{e.strerror}") from None


def _projekt(inst: fuzzing.Testinstanz) -> tuple[str, str]:
    """(Projektkennung, erster Standpunkt) des einzigen Pakets der Instanz."""
    daten = inst.arbeitsordner / "data"
    pakete = sorted(p.name for p in daten.iterdir() if p.is_dir()) if daten.is_dir() else []
    if len(pakete) != 1 or not _PROJEKT.fullmatch(pakete[0]):
        raise NachweisFehler("egress-browser", f"{daten}: genau ein Paket erwartet, gefunden: "
                                               f"{pakete}")
    try:
        stationen = json.loads((daten / pakete[0] / "stations.json").read_text("utf-8"))["stations"]
        standpunkt = stationen[0]["id"]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        raise NachweisFehler("egress-browser", f"{pakete[0]}/stations.json: kein Standpunkt "
                                               "lesbar") from None
    if not isinstance(standpunkt, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", standpunkt):
        raise NachweisFehler("egress-browser", f"Standpunkt „{standpunkt}“ ungültig")
    return pakete[0], standpunkt


def _zustand(cookie: str) -> str:
    """Playwright-`storageState` mit dem Sitzungs-Cookie (wie der Server es setzt: `Secure`,
    `HttpOnly`, Pfad `/`, `SameSite=Lax`, Host-Cookie für localhost)."""
    return json.dumps({"cookies": [{
        "name": fuzzing.COOKIE_NAME, "value": cookie, "domain": "localhost", "path": "/",
        "expires": -1, "httpOnly": True, "secure": True, "sameSite": "Lax"}], "origins": []})


def _lies(pfad: Path, was: str, auszug: str) -> Any:
    if not pfad.is_file() or pfad.is_symlink():
        raise NachweisFehler("egress-browser", f"{was} ({pfad.name}) fehlt — Lauf ungültig; "
                                               f"Ausgabe: {auszug or '(keine)'}")
    try:
        return json.loads(pfad.read_text(encoding="utf-8"))
    except ValueError:
        raise NachweisFehler("egress-browser", f"{pfad.name} ist kein gültiges JSON") from None


def _testlauf(bericht: object) -> dict[str, int]:
    """Zählwerte des Playwright-JSON-Berichts: genau ein Test erwartet-bestanden, nichts
    übersprungen (fehlende Umgebung), nichts unerwartet oder wackelig."""
    stats: Any = bericht.get("stats") if isinstance(bericht, dict) else None
    try:
        zahlen = {k: int(stats[k]) for k in ("expected", "unexpected", "skipped", "flaky")}
    except (TypeError, KeyError, ValueError):
        raise NachweisFehler("egress-browser", "egress-bericht.json ohne stats — Lauf "
                                               "ungültig") from None
    if zahlen["expected"] != 1 or zahlen["unexpected"] or zahlen["skipped"] or zahlen["flaky"]:
        raise NachweisFehler("egress-browser", f"Testlauf nicht sauber bestanden: {zahlen}")
    return {"erwartet": zahlen["expected"], "unerwartet": zahlen["unexpected"],
            "uebersprungen": zahlen["skipped"]}


def browser_lauf(inst: fuzzing.Testinstanz, werkzeuge: dict[str, Werkzeug], worktree: Path,
                 arbeit: Path, *, laeufer: Laeufer | None = None,
                 netzschritte: list[Netzschritt] | None = None) -> dict[str, Any]:
    """Headless-Chromium im gehärteten Container (`playwright`, Host-Netz, `web/` des Worktrees
    nur lesend, `arbeit` schreibend) gegen die Instanz. Ergebnis: `egress-anfragen.json` des
    Laufs plus die Zählwerte des JSON-Berichts (`testlauf`); nie der Exit-Code allein. Die
    Anmeldung liegt als 0600-Datei außerhalb von `arbeit` und wird danach entfernt."""
    w = werkzeuge.get("playwright")
    if w is None:
        raise NachweisFehler("egress-werkzeug", "playwright: fehlt in werkzeuge.json")
    projekt, standpunkt = _projekt(inst)
    name = f"{_CONTAINER}-{os.getpid()}"
    zustand_ordner = Path(tempfile.mkdtemp(prefix="zustand-", dir=inst.arbeitsordner))
    zustand = zustand_ordner / "zustand.json"
    try:
        fuzzing._geheim_schreiben(zustand, _zustand(inst.admin_cookie))
        befehl = docker_befehl(
            w, ["node", _PLAYWRIGHT_CLI, "test", "-c", _PLAYWRIGHT_KONFIG],
            {worktree / "web": _MOUNT_WEB, zustand: _MOUNT_ZUSTAND}, {arbeit: _MOUNT_AUSGABE},
            netz="host", tmpfs={"/dev/shm": ""}, name=name,
            umgebung={"SCANVIEW_EGRESS_AUSGABE": _MOUNT_AUSGABE,
                      "SCANVIEW_EGRESS_URL": inst.url,
                      "SCANVIEW_EGRESS_PROJEKT": projekt,
                      "SCANVIEW_EGRESS_STANDPUNKT": standpunkt,
                      "SCANVIEW_EGRESS_ZUSTAND": _MOUNT_ZUSTAND})
        if netzschritte is not None:
            netzschritte.append(Netzschritt(befehl, "web", sbom._jetzt()))
        lauf = (laeufer or (lambda b: _starte_mit_frist(b, name)))(befehl)
    finally:
        zustand.unlink(missing_ok=True)
        zustand_ordner.rmdir()
    auszug = fuzzing._ohne((lauf.stderr or lauf.stdout or "").strip()[-_STDERR_AUSZUG:],
                           [inst.admin_cookie, inst.betrachter_token])
    anfragen = _lies(arbeit / ANFRAGEN_DATEI, "Anfrageliste", auszug)
    bericht = _lies(arbeit / BERICHT_DATEI, "Testbericht", auszug)
    if not isinstance(anfragen, dict):
        raise NachweisFehler("egress-browser", f"{ANFRAGEN_DATEI}: Objekt erwartet")
    if lauf.returncode != 0:
        raise NachweisFehler("egress-browser", f"Browser-Lauf endete mit Exit {lauf.returncode}: "
                                               f"{auszug or '(keine Ausgabe)'}")
    return {**anfragen, "testlauf": _testlauf(bericht)}


def sitzungsprobe(inst: fuzzing.Testinstanz) -> None:
    """Der Admin-Cookie gilt nach dem Lauf noch (`GET /api/v1/projekte` → 200); sonst war das
    Ergebnis mit einer abgemeldeten Sitzung erhoben."""
    status, _, _ = fuzzing._http("GET", f"{inst.url}/api/v1/projekte",
                                 {"Cookie": f"{fuzzing.COOKIE_NAME}={inst.admin_cookie}"})
    if status != 200:
        raise NachweisFehler("egress-sitzung", "Anmeldung während des Laufs verloren — Ergebnis "
                                               f"ungültig (Projektliste: {status})")


# --- Gesamtlauf ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ergebnis:
    """Ergebnis von `pruefen`: Bericht (JSON-fähig), blockierende Meldungen, Netzschritte."""

    bericht: dict[str, Any]
    blockiert: list[str]
    netzschritte: list[Netzschritt]


def _schreibe_berichte(inst: fuzzing.Testinstanz, berichte: Path, statisch: dict[str, Any],
                       browser: dict[str, Any]) -> None:
    """Berichte im Arbeitsordner schreiben, Anmeldewerte entfernen und den Rest suchen, erst
    dann nach `berichte/` legen (`fuzzing-geheimnis` löscht sie bei einem Rest)."""
    roh = inst.arbeitsordner / "berichte-roh"
    roh.mkdir()
    for datei, inhalt in ((BERICHT_STATISCH, statisch), (BERICHT_BROWSER, browser)):
        (roh / datei).write_text(json.dumps(inhalt, indent=2, ensure_ascii=False) + "\n",
                                 encoding="utf-8")
    geheimnisse = [inst.admin_cookie, inst.betrachter_token]
    fuzzing.bereinige(roh, geheimnisse)
    fuzzing.pruefe_geheimnisfrei(roh, geheimnisse)
    berichte.mkdir(parents=True, exist_ok=True)
    for datei in (BERICHT_STATISCH, BERICHT_BROWSER):
        if (berichte / datei).exists():
            raise NachweisFehler("egress-ausgabe", f"{berichte / datei}: existiert schon")
    for datei in (BERICHT_STATISCH, BERICHT_BROWSER):
        shutil.move(roh / datei, berichte / datei)


def pruefen(worktree: Path, werkzeuge: dict[str, Werkzeug], berichte: Path, arbeitsordner: Path,
            *, instanz: Callable[..., fuzzing.Testinstanz] = fuzzing.starte_testinstanz,
            browser: Callable[..., dict[str, Any]] = browser_lauf,
            sitzung: Callable[[fuzzing.Testinstanz], None] = sitzungsprobe) -> Ergebnis:
    """Statische Prüfung von `<worktree>/web/dist`, Wegwerf-Instanz mit dem Testpaket, Browser-
    Lauf, Sitzungsprobe, dann das Gate; Berichte `<berichte>/egress-statisch.json` und
    `egress-browser.json`. Blockierende Funde stehen im Ergebnis, jeder andere Regelverstoß
    bricht ab."""
    paket = worktree / PAKET
    if not (paket / "project.json").is_file():
        raise NachweisFehler("egress-paket", f"{paket}: Testpaket fehlt")
    ausnahmen = lade_ausnahmen(worktree / AUSNAHMEN)
    dist = worktree / "web" / "dist"
    dateien = bundle_dateien(dist)
    funde = bundle_urls(dist)
    blockiert_statisch = statisch_gate(funde, ausnahmen)
    hinweise = ungenutzte_ausnahmen(funde, ausnahmen)
    start = time.monotonic()
    schritte: list[Netzschritt] = []
    with instanz(worktree, arbeitsordner, werkzeuge, paket=paket) as inst:
        schritte += inst.netzschritte
        ausgabe = inst.arbeitsordner / "browser"
        ausgabe.mkdir()
        roh = browser(inst, werkzeuge, worktree, ausgabe, netzschritte=schritte)
        sitzung(inst)
        blockiert_browser = egress_gate(roh, inst.url)
    statisch = {
        "dist_dateien": len(dateien),
        "funde": [{"datei": f.datei, "url": f.url, "host": f.host,
                   "ausnahme": next((a.art for a in ausnahmen if a.erfasst(f)), None)}
                  for f in funde],
        "ausnahmen": len(ausnahmen), "hinweise": hinweise, "blockierend": blockiert_statisch}
    ergebnis_browser = {**roh, "blockierend": blockiert_browser}
    _schreibe_berichte(inst, berichte, statisch, ergebnis_browser)
    blockiert = [f"statisch {b}" for b in blockiert_statisch] \
        + [f"browser {b}" for b in blockiert_browser]
    w = werkzeuge["playwright"]
    bericht = {
        "werkzeug": {"image": w.image, "version": w.version},
        "instanz": {"url": inst.url, "server_port": SERVER_PORT, "paket": PAKET.as_posix()},
        "statisch": {"dist_dateien": len(dateien), "funde": len(funde),
                     "ausgenommen": sum(1 for f in funde if any(a.erfasst(f) for a in ausnahmen)),
                     "blockierend": blockiert_statisch},
        "browser": {"anfragen": len(roh["anfragen"]), "kontrollanfragen_erfasst": True,
                    "seiten": roh["seiten"], "punkte": roh["geladen"]["punkte"],
                    "eigene": len(roh["geladen"]["eigene"]), "testlauf": roh.get("testlauf"),
                    "blockierend": blockiert_browser},
        "hinweise": hinweise, "dauer_sekunden": round(time.monotonic() - start),
        "sitzungsprobe": "gültig", "blockierend": blockiert,
    }
    return Ergebnis(bericht, blockiert, schritte)
