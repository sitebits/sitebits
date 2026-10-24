"""Statische Codeanalyse (SAST) der Quelltexte: Semgrep mit einem vendierten, per Lock
gehashten Regelsatz (ohne Netz, ohne Registry) und Bandit für Python.

- Beide laufen gehärtet ohne Netz (`werkzeuge.docker_befehl`) über jede versionierte
  Quelldatei von `ZIELE`, einzeln als Ziel genannt; der Worktree ist nur lesend unter `/quelle`
  gemountet. Inline-Unterdrückungen (`# nosemgrep`, `# nosec`) gelten
  nicht (`--disable-nosem`, `--ignore-nosec`): ein Fund verschwindet nur über das Befundregister
  oder eine begründete Ausnahme.
- Abdeckung fail-closed: jede versionierte Quelldatei der Ziele (`git ls-files`, `*.py` für
  Bandit, `*.py`/`*.ts` für Semgrep) muss im Scan-Ergebnis stehen, sonst `sast-abdeckung`.
  Werkzeugfehler (`errors`) brechen ab.
- Fingerabdruck je Fund: `sha256(werkzeug|regel|pfad|Fundbereich)[:16]` über alle gemeldeten
  Zeilen (Semgrep `start`..`end`, Bandit `line_range`), je Zeile normalisiert, aus der Datei selbst
  gelesen (ohne Zeilennummer: Verschiebungen lassen ihn stabil, jede Änderung im Bereich nicht).
- Gate: jeder Fund ≥ mittel braucht einen Befund mit gleichem `fingerabdruck` im Register (dessen
  Status bewertet weiter `gate.befund_gate`); jeder übrige einen Befund oder eine Ausnahme aus
  `docs/compliance/sast-ausnahmen.toml` (Regel + Pfad-Glob oder Fingerabdruck, nur für niedrig
  und info, mit Begründung und Datum). Ausnahmen ohne Fund werden gemeldet, blockieren nicht."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from . import sbom
from .artefakte import Netzschritt
from .fehler import NachweisFehler
from .positivliste import glob_regex
from .register import BEFUNDE, Befund, lade_befunde
from .werkzeuge import Werkzeug, ausfuehren, docker_befehl, git_befehl

Werkzeugname = Literal["semgrep", "bandit"]
Schwere = Literal["hoch", "mittel", "niedrig", "info"]
WERKZEUGE: tuple[str, ...] = ("semgrep", "bandit")
SCHWEREN: tuple[str, ...] = ("hoch", "mittel", "niedrig", "info")
#: Quellbäume aller Teilprojekte und die Skripte, die auf den Hosts laufen (`deploy/platform`,
#: u. a. release_pruefen.py), dazu der API-Client samt Beispielskripten, die Kunden ausführen —
#: Laufzeitcode; Tests (Ordner `test`/`tests` unterhalb eines Ziels) nicht.
ZIELE = ("server/src", "mcp-server/src", "nachweise/src", "web/src", "import/src",
         "deploy/platform", "clients/sitebits/src", "clients/sitebits/beispiele")
_TESTORDNER = frozenset({"test", "tests"})
#: Fixtures der Negativkontrolle `sast-fund-unregistriert` (absichtlich verwundbar).
FIXTURE_ORDNER = "nachweise/tests/fixtures/sast"
#: Relativ zu `nachweise/`.
REGELN = Path("sast/regeln")
REGELN_LOCK = Path("sast/regeln.lock")
AUSNAHMEN = Path("docs/compliance/sast-ausnahmen.toml")
BERICHT_SEMGREP = "sast-semgrep.json"
# Zeitlimit je Regel und Datei. Der Standard (5 s) reißt bei ausgelasteter Maschine auf großen
# Dateien mit Taint-Regeln; eine Zeitüberschreitung bleibt ein Werkzeugfehler (fail-closed), und
# `--timeout-threshold 0` verhindert, dass Semgrep eine Regel nach wiederholten Überschreitungen
# still überspringt.
SEMGREP_ZEITLIMIT_S = 120
BERICHT_BANDIT = "sast-bandit.json"
MARKE = sbom._MARKE
ENDUNGEN: dict[str, tuple[str, ...]] = {"semgrep": (".py", ".ts"), "bandit": (".py",)}
_QUELLE = "/quelle"
_REGELN_MOUNT = "/regeln"
_AUS = "/aus"
#: Semgrep stellt jeder Regel-ID den Ordnernamen der Konfiguration voran (Mount `/regeln`).
_REGEL_PRAEFIX = "regeln."
_BANDIT = "/werkzeug/.venv/bin/bandit"
_MIN_BEGRUENDUNG = 20
_FINGERABDRUCK = re.compile(r"[0-9a-f]{16}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TEXT_HOECHSTENS = 300

Laeufer = Callable[..., object]


@dataclass(frozen=True)
class Fund:
    """Ein Fund eines SAST-Werkzeugs; `pfad` relativ zur Worktree-Wurzel."""

    werkzeug: Werkzeugname
    regel: str
    pfad: str
    zeile: int
    schwere: Schwere
    fingerabdruck: str
    text: str


# --- Schwere und Fingerabdruck -------------------------------------------------------------------

_SEMGREP_SCHWERE: dict[str, Schwere] = {"ERROR": "hoch", "WARNING": "mittel", "INFO": "niedrig"}
_BANDIT_SCHWERE: dict[str, int] = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def semgrep_schwere(roh: str) -> Schwere:
    """`ERROR→hoch`, `WARNING→mittel`, `INFO→niedrig`; alles andere → hoch (fail-closed)."""
    return _SEMGREP_SCHWERE.get(roh, "hoch")


def bandit_schwere(schwere: str, konfidenz: str) -> Schwere:
    """`HIGH→hoch`, `MEDIUM→mittel`, `LOW→niedrig`, bei Konfidenz `LOW` eine Stufe tiefer;
    unbekannte Schwere → hoch."""
    stufe = _BANDIT_SCHWERE.get(schwere, 0)
    if konfidenz == "LOW" and schwere in _BANDIT_SCHWERE:
        stufe += 1
    return cast(Schwere, SCHWEREN[stufe])


def normalisiere(zeile: str) -> str:
    """Leerraum am Rand weg, innen auf je ein Leerzeichen."""
    return " ".join(zeile.split())


def fingerabdruck(werkzeug: str, regel: str, pfad: str, bereich: str) -> str:
    """`sha256(werkzeug|regel|pfad|Fundbereich)[:16]`; der Fundbereich sind die gemeldeten Zeilen,
    je Zeile normalisiert und mit „\\n“ verbunden (ohne Zeilennummern)."""
    zeilen = "\n".join(normalisiere(z) for z in bereich.split("\n"))
    return hashlib.sha256(f"{werkzeug}|{regel}|{pfad}|{zeilen}".encode()).hexdigest()[:16]


def befund_id(fund: Fund) -> str:
    """ID des Registereintrags zu einem Fund: `B-SAST-<WERKZEUG>-<FINGERABDRUCK>`."""
    return f"B-SAST-{fund.werkzeug.upper()}-{fund.fingerabdruck.upper()}"


# --- Auswertung ----------------------------------------------------------------------------------

def _relativ(pfad: object) -> str:
    """`/quelle/<rel>` → `<rel>`; alles andere (fremder Pfad, `..`) ist eine kaputte Ausgabe."""
    if not isinstance(pfad, str) or not pfad.startswith(_QUELLE + "/"):
        raise NachweisFehler("sast-ausgabe", f"Pfad „{pfad}“ liegt nicht unter {_QUELLE}/")
    rel = pfad.removeprefix(_QUELLE + "/")
    if not rel or ".." in PurePosixPath(rel).parts:
        raise NachweisFehler("sast-ausgabe", f"Pfad „{pfad}“ ungültig")
    return rel


def _zeilennummer(pfad: str, wert: object) -> int:
    if not isinstance(wert, int) or isinstance(wert, bool) or wert < 1:
        raise NachweisFehler("sast-ausgabe", f"{pfad}: Zeile „{wert}“ ungültig")
    return wert


def _fundbereich(worktree: Path, pfad: str, von: int, bis: int) -> str:
    """Die Zeilen `von`..`bis` der Datei, getrennt nur an „\\n“ (wie die Werkzeuge zählen; ein
    Seitenvorschub oder U+2028 in einer Zeile verschiebt nichts), je Zeile normalisiert."""
    if bis < von:
        raise NachweisFehler("sast-ausgabe", f"{pfad}: Bereich {von} bis {bis} ungültig")
    try:
        zeilen = (worktree / pfad).read_text(encoding="utf-8").split("\n")
    except (OSError, UnicodeDecodeError) as e:
        raise NachweisFehler("sast-ausgabe", f"{pfad}: nicht lesbar ({e})") from None
    if zeilen and zeilen[-1] == "":
        zeilen.pop()
    if bis > len(zeilen):
        raise NachweisFehler("sast-ausgabe", f"{pfad}: Zeile {bis} jenseits des Dateiendes "
                                             f"({len(zeilen)} Zeilen)")
    return "\n".join(normalisiere(z) for z in zeilen[von - 1:bis])


def _fund(worktree: Path, werkzeug: Werkzeugname, regel: str, roh_pfad: object, von: object,
          bis: object, schwere: Schwere, text: object) -> Fund:
    pfad = _relativ(roh_pfad)
    anfang = _zeilennummer(pfad, von)
    inhalt = _fundbereich(worktree, pfad, anfang, _zeilennummer(pfad, bis))
    return Fund(werkzeug=werkzeug, regel=regel, pfad=pfad, zeile=anfang, schwere=schwere,
                fingerabdruck=fingerabdruck(werkzeug, regel, pfad, inhalt),
                text=str(text)[:_TEXT_HOECHSTENS])


def _liste(daten: dict[str, Any], feld: str, werkzeug: str) -> list[Any]:
    wert = daten.get(feld)
    if not isinstance(wert, list):
        raise NachweisFehler("sast-ausgabe", f"{werkzeug}: Feld „{feld}“ fehlt oder ist keine "
                                             "Liste")
    return wert


def werte_semgrep_aus(daten: dict[str, Any], worktree: Path) -> tuple[list[Fund], set[str]]:
    """Semgrep-JSON → (Funde, gescannte Pfade). Nicht leere `errors` → `sast-werkzeug`."""
    fehler = _liste(daten, "errors", "semgrep")
    if fehler:
        auszug = "; ".join(f"{e.get('type', '?')}: {str(e.get('message', ''))[:200]}"
                           if isinstance(e, dict) else str(e)[:200] for e in fehler[:5])
        raise NachweisFehler("sast-werkzeug", f"semgrep meldet {len(fehler)} Fehler: {auszug}")
    pfade = daten.get("paths")
    if not isinstance(pfade, dict):
        raise NachweisFehler("sast-ausgabe", "semgrep: Feld „paths“ fehlt")
    gescannt = {_relativ(p) for p in _liste(pfade, "scanned", "semgrep")}
    funde: list[Fund] = []
    for r in _liste(daten, "results", "semgrep"):
        try:
            regel = r["check_id"].removeprefix(_REGEL_PRAEFIX)
            extra = r["extra"]
            funde.append(_fund(worktree, "semgrep", regel, r["path"], r["start"]["line"],
                               r["end"]["line"], semgrep_schwere(extra.get("severity", "")),
                               extra.get("message", "")))
        except (KeyError, TypeError, AttributeError) as e:
            raise NachweisFehler("sast-ausgabe", f"semgrep: Fund ohne Feld {e}") from None
    return funde, gescannt


def werte_bandit_aus(daten: dict[str, Any], worktree: Path) -> tuple[list[Fund], set[str]]:
    """Bandit-JSON → (Funde, gescannte Dateien aus `metrics`). `errors` → `sast-werkzeug`."""
    fehler = _liste(daten, "errors", "bandit")
    if fehler:
        auszug = "; ".join(str(e)[:200] for e in fehler[:5])
        raise NachweisFehler("sast-werkzeug", f"bandit meldet {len(fehler)} Fehler: {auszug}")
    metriken = daten.get("metrics")
    if not isinstance(metriken, dict):
        raise NachweisFehler("sast-ausgabe", "bandit: Feld „metrics“ fehlt")
    gescannt = {_relativ(p) for p in metriken if p != "_totals"}
    funde: list[Fund] = []
    for r in _liste(daten, "results", "bandit"):
        try:
            bereich = r.get("line_range") or [r["line_number"]]
            if not isinstance(bereich, list) or not bereich:
                raise NachweisFehler("sast-ausgabe", "bandit: „line_range“ ungültig")
            funde.append(_fund(worktree, "bandit", r["test_id"], r["filename"],
                               r["line_number"], max(bereich),
                               bandit_schwere(r["issue_severity"], r["issue_confidence"]),
                               r.get("issue_text", "")))
        except (KeyError, TypeError, AttributeError) as e:
            raise NachweisFehler("sast-ausgabe", f"bandit: Fund ohne Feld {e}") from None
    return funde, gescannt


# --- Abdeckung -----------------------------------------------------------------------------------

def quelldateien(worktree: Path, ziele: Iterable[str], endungen: tuple[str, ...]) -> set[str]:
    """Versionierte Dateien unter `ziele` mit einer der `endungen` (`git ls-files`), ohne
    Testordner (`test`/`tests`) unterhalb eines Ziels; ein Testordner, der selbst Ziel ist (die
    Fixtures der Negativkontrolle), zählt vollständig."""
    ergebnis: set[str] = set()
    for ziel in ziele:
        ausgabe = ausfuehren(git_befehl("ls-files", "-z", "--", ziel, repo=worktree),
                             "sast-abdeckung").stdout
        for p in ausgabe.split("\0"):
            unter = PurePosixPath(p).relative_to(ziel).parts[:-1] if p else ()
            if p.endswith(endungen) and not _TESTORDNER & set(unter):
                ergebnis.add(p)
    return ergebnis


def pruefe_abdeckung(werkzeug: str, gescannt: set[str], erwartet: set[str]) -> int:
    """Jede erwartete Quelldatei wurde gescannt (nicht nur gleich viele); ohne Quelldateien gibt
    es nichts zu belegen → ebenfalls Fehler. Rückgabe: Zahl der gescannten Quelldateien."""
    if not erwartet:
        raise NachweisFehler("sast-abdeckung", f"{werkzeug}: keine Quelldateien in den Zielen")
    fehlend = sorted(erwartet - gescannt)
    if fehlend:
        raise NachweisFehler("sast-abdeckung", f"{werkzeug}: {len(fehlend)} von {len(erwartet)} "
                                               "Quelldateien nicht gescannt: "
                                               + ", ".join(fehlend[:20]))
    return len(erwartet)


def _ziele(worktree: Path, ziele: tuple[str, ...], werkzeug: str) -> set[str]:
    """Die versionierten Quelldateien der Ziele für `werkzeug`. Fehlt ein Ziel oder gibt es keine
    Quelldatei → `sast-abdeckung` vor jedem Lauf."""
    fehlend = [z for z in ziele if not (worktree / z).is_dir()]
    if fehlend:
        raise NachweisFehler("sast-abdeckung", f"Ziele fehlen im Worktree: {', '.join(fehlend)}")
    erwartet = quelldateien(worktree, ziele, ENDUNGEN[werkzeug])
    if not erwartet:
        raise NachweisFehler("sast-abdeckung", f"{werkzeug}: keine Quelldateien in den Zielen")
    return erwartet


def _ziel_argumente(dateien: set[str]) -> list[str]:
    """Jede Quelldatei einzeln als Ziel: ausdrücklich genannte Dateien filtern die Werkzeuge nicht
    mit ihren Standard-Ausschlüssen (Semgrep ließe sonst z. B. alles unter `tests/` weg)."""
    return [f"{_QUELLE}/{p}" for p in sorted(dateien)]


# --- Regelsatz -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Regelquelle:
    """Herkunft des vendierten Regelsatzes (Kopf von `regeln.lock`)."""

    url: str
    commit: str
    lizenz: str
    lizenz_url: str
    weiterverbreitung: bool
    datum: date
    auswahl: str


def _dateien(ordner: Path) -> dict[str, str]:
    """Relativer Pfad → sha256 jeder Datei unter `ordner`; Symlinks und Sonderdateien sind
    verboten (ein Link könnte auf beliebige Regeln außerhalb zeigen)."""
    if not ordner.is_dir() or ordner.is_symlink():
        raise NachweisFehler("sast-regeln", f"{ordner}: Regelordner fehlt")
    ergebnis: dict[str, str] = {}
    for pfad in sorted(ordner.rglob("*")):
        rel = pfad.relative_to(ordner).as_posix()
        if pfad.is_symlink():
            raise NachweisFehler("sast-regeln", f"{rel}: Symlink im Regelsatz")
        if pfad.is_dir():
            continue
        if not pfad.is_file():
            raise NachweisFehler("sast-regeln", f"{rel}: keine reguläre Datei")
        ergebnis[rel] = hashlib.sha256(pfad.read_bytes()).hexdigest()
    return ergebnis


def _toml_text(wert: str) -> str:
    return json.dumps(wert, ensure_ascii=False)


def schreibe_regellock(nachweise: Path, quelle: Regelquelle) -> Path:
    """`regeln.lock` aus dem aktuellen Inhalt von `sast/regeln/` (beim Vendieren)."""
    dateien = _dateien(nachweise / REGELN)
    zeilen = [
        "# Vendierter Semgrep-Regelsatz: Herkunft, Lizenz und SHA-256 je Datei.",
        "# Erzeugt mit sast.schreibe_regellock; geprüft vor jedem Lauf (sast.pruefe_regelsatz).",
        "[quelle]",
        f"url = {_toml_text(quelle.url)}",
        f"commit = {_toml_text(quelle.commit)}",
        f"lizenz = {_toml_text(quelle.lizenz)}",
        f"lizenz_url = {_toml_text(quelle.lizenz_url)}",
        f"weiterverbreitung = {'true' if quelle.weiterverbreitung else 'false'}",
        f"datum = {quelle.datum.isoformat()}",
        f"auswahl = {_toml_text(quelle.auswahl)}",
        "",
        "[dateien]",
        *(f"{_toml_text(p)} = {_toml_text(h)}" for p, h in dateien.items()),
    ]
    ziel = nachweise / REGELN_LOCK
    ziel.write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return ziel


def lade_regellock(pfad: Path) -> tuple[Regelquelle, dict[str, str]]:
    regel = "sast-regeln"
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NachweisFehler(regel, f"{pfad}: Lock fehlt") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler(regel, f"{pfad}: nicht lesbar ({e})") from None
    kopf, dateien = daten.get("quelle"), daten.get("dateien")
    felder = {"url", "commit", "lizenz", "lizenz_url", "weiterverbreitung", "datum", "auswahl"}
    if set(daten) != {"quelle", "dateien"} or not isinstance(kopf, dict) \
            or set(kopf) != felder or not isinstance(dateien, dict) or not dateien:
        raise NachweisFehler(regel, f"{pfad}: erwartet [quelle] mit {', '.join(sorted(felder))} "
                                    "und nicht leere [dateien]")
    if not all(isinstance(kopf[f], str) and kopf[f].strip()
               for f in felder - {"weiterverbreitung", "datum"}) \
            or not isinstance(kopf["weiterverbreitung"], bool) \
            or not isinstance(kopf["datum"], date) or isinstance(kopf["datum"], datetime):
        raise NachweisFehler(regel, f"{pfad}: [quelle] mit falschen Feldtypen")
    if not all(isinstance(h, str) and _SHA256.fullmatch(h) for h in dateien.values()):
        raise NachweisFehler(regel, f"{pfad}: [dateien] braucht je Datei einen SHA-256 (hex)")
    return Regelquelle(**kopf), dict(dateien)


def pruefe_regelsatz(nachweise: Path) -> Regelquelle:
    """Jede Datei unter `sast/regeln/` gegen `regeln.lock`: fehlend, zusätzlich oder geändert →
    `sast-regeln`."""
    quelle, soll = lade_regellock(nachweise / REGELN_LOCK)
    ist = _dateien(nachweise / REGELN)
    fehlend = sorted(set(soll) - set(ist))
    zusaetzlich = sorted(set(ist) - set(soll))
    geaendert = sorted(p for p in set(soll) & set(ist) if soll[p] != ist[p])
    if fehlend or zusaetzlich or geaendert:
        teile = [f"{name}: {', '.join(liste[:10])}" for name, liste in
                 (("fehlend", fehlend), ("zusätzlich", zusaetzlich), ("geändert", geaendert))
                 if liste]
        raise NachweisFehler("sast-regeln", "Regelsatz weicht vom Lock ab — " + "; ".join(teile))
    return quelle


# --- Werkzeuge -----------------------------------------------------------------------------------

def _werkzeug(werkzeuge: dict[str, Werkzeug], name: str) -> Werkzeug:
    w = werkzeuge.get(name)
    if w is None:
        raise NachweisFehler("sast-werkzeug", f"{name}: fehlt in werkzeuge.json")
    return w


def _bandit_ordner(nachweise: Path, w: Werkzeug) -> Path:
    if w.werkzeugordner is None:
        raise NachweisFehler("sast-werkzeug", "bandit: kein werkzeugordner in werkzeuge.json")
    return nachweise / w.werkzeugordner


def bandit_marke(w: Werkzeug, ordner: Path) -> dict[str, str]:
    """Soll-Marke der Installation: Image-Digest und SHA-256 von `uv.lock`."""
    return sbom._soll_marke(w, ordner / "uv.lock", "sast-werkzeug")


def uv_werkzeug_bereitstellen(nachweise: Path, werkzeuge: dict[str, Werkzeug], name: str,
                              regel: str, *, laeufer: Laeufer = ausfuehren) -> list[Netzschritt]:
    """Prüfwerkzeug `name` aus `uv.lock` seines Werkzeugordners nach `<ordner>/.venv`
    installieren, falls noch nicht vorhanden (Netz, Rückgabe fürs Manifest), danach Marke
    schreiben. Vorhandene Installation ohne passende Marke → Fehler (nie still wiederverwenden).
    Gemeinsam für Bandit (SAST) und Schemathesis (Fuzzing)."""
    w = werkzeuge.get(name)
    if w is None:
        raise NachweisFehler(regel, f"{name}: fehlt in werkzeuge.json")
    if w.werkzeugordner is None:
        raise NachweisFehler(regel, f"{name}: kein werkzeugordner in werkzeuge.json")
    ordner = nachweise / w.werkzeugordner
    soll = sbom._soll_marke(w, ordner / "uv.lock", regel)
    venv = ordner / ".venv"
    if sbom._pruefe_marke(venv, soll, regel):
        return []
    befehl = ["uv", "sync", "--locked", "--no-install-project", "--project", "/werkzeug"]
    schritt = Netzschritt(befehl, f"nachweise/{w.werkzeugordner}", sbom._jetzt())
    laeufer(docker_befehl(w, befehl, {}, {ordner: "/werkzeug"}, netz=True,
                          umgebung={"UV_CACHE_DIR": "/tmp/uv-cache", "UV_PYTHON_DOWNLOADS": "never",
                                    "UV_LINK_MODE": "copy"}), regel)
    if not venv.is_dir():
        raise NachweisFehler(regel, f"{venv}: nach der Installation nicht vorhanden")
    (venv / MARKE).write_text(json.dumps(soll, sort_keys=True), encoding="utf-8")
    return [schritt]


def bandit_bereitstellen(nachweise: Path, werkzeuge: dict[str, Werkzeug], *,
                         laeufer: Laeufer = ausfuehren) -> list[Netzschritt]:
    """Bandit bereitstellen (`uv_werkzeug_bereitstellen`)."""
    return uv_werkzeug_bereitstellen(nachweise, werkzeuge, "bandit", "sast-werkzeug",
                                     laeufer=laeufer)


def _ausgabe_frei(ausgabe: Path) -> None:
    if ausgabe.exists():
        raise NachweisFehler("sast-ausgabe", f"{ausgabe}: existiert schon — wird nicht "
                                             "überschrieben")
    ausgabe.parent.mkdir(parents=True, exist_ok=True)


def _laufen(laeufer: Laeufer, w: Werkzeug, args: list[str], lesend: dict[Path, str],
            ausgabe: Path) -> None:
    """Werkzeug mit einem eigenen, frischen Schreibordner (`/aus`) ausführen und seine Ausgabe
    danach nach `ausgabe` verschieben: der Berichtsordner mit den Berichten früherer Stufen ist
    im Container nie beschreibbar."""
    with tempfile.TemporaryDirectory(prefix="scanview-sast-") as roh:
        laeufer(docker_befehl(w, args, lesend, {Path(roh): _AUS}), "sast-werkzeug")
        datei = Path(roh) / ausgabe.name
        if not datei.is_file() or datei.is_symlink():
            raise NachweisFehler("sast-ausgabe", f"{w.name}: keine Ausgabe {ausgabe.name}")
        shutil.move(datei, ausgabe)


def _lies_ausgabe(ausgabe: Path, werkzeug: str) -> dict[str, Any]:
    try:
        daten = json.loads(ausgabe.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise NachweisFehler("sast-ausgabe", f"{werkzeug}: Ausgabe {ausgabe} nicht lesbar "
                                             f"({e})") from None
    if not isinstance(daten, dict):
        raise NachweisFehler("sast-ausgabe", f"{werkzeug}: Ausgabe ist kein JSON-Objekt")
    return daten


def semgrep_laufen(worktree: Path, werkzeuge: dict[str, Werkzeug], ausgabe: Path, *,
                   ziele: tuple[str, ...] = ZIELE,
                   laeufer: Laeufer = ausfuehren) -> tuple[list[Fund], int]:
    """Regelsatz prüfen, Semgrep ohne Netz mit dem vendierten Regelsatz über `ziele`, Ausgabe
    als JSON nach `ausgabe`; Rückgabe (Funde, gescannte Quelldateien). Abdeckung und
    Werkzeugfehler fail-closed."""
    w = _werkzeug(werkzeuge, "semgrep")
    nachweise = worktree / "nachweise"
    pruefe_regelsatz(nachweise)
    erwartet = _ziele(worktree, ziele, "semgrep")
    _ausgabe_frei(ausgabe)
    args = ["semgrep", "scan", "--config", _REGELN_MOUNT, "--metrics", "off",
            "--disable-version-check", "--disable-nosem", "--no-git-ignore", "--json",
            "--timeout", str(SEMGREP_ZEITLIMIT_S), "--timeout-threshold", "0",
            "--quiet", "--output", f"{_AUS}/{ausgabe.name}", *_ziel_argumente(erwartet)]
    _laufen(laeufer, w, args, {nachweise / REGELN: _REGELN_MOUNT, worktree: _QUELLE}, ausgabe)
    funde, gescannt = werte_semgrep_aus(_lies_ausgabe(ausgabe, "semgrep"), worktree)
    return funde, pruefe_abdeckung("semgrep", gescannt, erwartet)


def bandit_laufen(worktree: Path, werkzeuge: dict[str, Werkzeug], ausgabe: Path, *,
                  ziele: tuple[str, ...] = ZIELE,
                  laeufer: Laeufer = ausfuehren) -> tuple[list[Fund], int]:
    """Bandit (installiert per `bandit_bereitstellen`, Marke geprüft) ohne Netz über `ziele`,
    JSON nach `ausgabe`; Rückgabe (Funde, gescannte Python-Dateien aus `metrics`). `--exit-zero`:
    Funde sind kein Werkzeugfehler, Fehler stehen in `errors`."""
    w = _werkzeug(werkzeuge, "bandit")
    ordner = _bandit_ordner(worktree / "nachweise", w)
    if not sbom._pruefe_marke(ordner / ".venv", bandit_marke(w, ordner), "sast-werkzeug"):
        raise NachweisFehler("sast-werkzeug", f"{ordner / '.venv'}: bandit nicht installiert "
                                              "(bandit_bereitstellen)")
    erwartet = _ziele(worktree, ziele, "bandit")
    _ausgabe_frei(ausgabe)
    args = [_BANDIT, "-f", "json", "-o", f"{_AUS}/{ausgabe.name}", "--exit-zero",
            "--ignore-nosec", *_ziel_argumente(erwartet)]
    _laufen(laeufer, w, args, {ordner: "/werkzeug", worktree: _QUELLE}, ausgabe)
    funde, gescannt = werte_bandit_aus(_lies_ausgabe(ausgabe, "bandit"), worktree)
    return funde, pruefe_abdeckung("bandit", gescannt, erwartet)


# --- Ausnahmen -----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ausnahme:
    """Begründete Ausnahme für Funde unter mittel: entweder ein `fingerabdruck` oder eine
    `regel` mit `pfade`-Globs; `schwere` ist die höchste erfasste Schwere (niedrig oder info)."""

    werkzeug: Werkzeugname
    fingerabdruck: str | None
    regel: str | None
    pfade: tuple[str, ...]
    schwere: Schwere
    begruendung: str
    datum: date

    def erfasst(self, fund: Fund) -> bool:
        if fund.werkzeug != self.werkzeug or fund.schwere not in ("niedrig", "info"):
            return False
        if SCHWEREN.index(fund.schwere) < SCHWEREN.index(self.schwere):
            return False
        if self.fingerabdruck is not None:
            return fund.fingerabdruck == self.fingerabdruck
        return fund.regel == self.regel and any(glob_regex(m).fullmatch(fund.pfad)
                                                for m in self.pfade)

    def beschreibung(self) -> str:
        was = self.fingerabdruck or f"{self.regel} in {', '.join(self.pfade)}"
        return f"{self.werkzeug} {was}"


_AUSNAHME_FELDER = {"werkzeug", "fingerabdruck", "regel", "pfade", "schwere", "begruendung",
                    "datum"}


def _ausnahme(nr: int, t: object, pfad: Path) -> Ausnahme:
    def fehler(text: str) -> NachweisFehler:
        return NachweisFehler("sast-ausnahmen", f"{pfad.name} Ausnahme Nr. {nr}: {text}")

    if not isinstance(t, dict):
        raise fehler("keine Tabelle")
    unbekannt = sorted(set(t) - _AUSNAHME_FELDER)
    if unbekannt:
        raise fehler(f"unbekannte Felder {', '.join(unbekannt)}")
    werkzeug = t.get("werkzeug")
    if werkzeug not in WERKZEUGE:
        raise fehler(f"„werkzeug“ muss einer von {', '.join(WERKZEUGE)} sein")
    schwere = t.get("schwere")
    if schwere not in ("niedrig", "info"):
        raise fehler("„schwere“ nur niedrig oder info — Funde ab mittel gehören ins "
                     "Befundregister")
    begruendung = t.get("begruendung")
    if not isinstance(begruendung, str) or len(begruendung.strip()) < _MIN_BEGRUENDUNG:
        raise fehler(f"„begruendung“ fehlt oder ist kürzer als {_MIN_BEGRUENDUNG} Zeichen")
    datum = t.get("datum")
    if not isinstance(datum, date) or isinstance(datum, datetime):
        raise fehler("„datum“ muss ein TOML-Datum JJJJ-MM-TT sein")
    fp, regel, pfade = t.get("fingerabdruck"), t.get("regel"), t.get("pfade")
    if (fp is None) == (regel is None):
        raise fehler("entweder „fingerabdruck“ oder „regel“ (mit „pfade“)")
    if fp is not None:
        if pfade is not None:
            raise fehler("„pfade“ nur zusammen mit „regel“")
        if not isinstance(fp, str) or not _FINGERABDRUCK.fullmatch(fp):
            raise fehler("„fingerabdruck“ muss aus 16 hex-Zeichen (klein) bestehen")
        muster: tuple[str, ...] = ()
    else:
        if not isinstance(regel, str) or not regel.strip():
            raise fehler("„regel“ ist leer")
        if not isinstance(pfade, list) or not pfade \
                or not all(isinstance(m, str) and m.strip() for m in pfade):
            raise fehler("„pfade“ muss eine nicht leere Liste von Globs sein")
        for m in pfade:
            try:
                glob_regex(m)
            except NachweisFehler as e:
                raise fehler(f"Glob „{m}“ ungültig: {e.text}") from None
        muster = tuple(pfade)
    return Ausnahme(werkzeug=cast(Werkzeugname, werkzeug), fingerabdruck=fp, regel=regel,
                    pfade=muster, schwere=cast(Schwere, schwere), begruendung=begruendung,
                    datum=datum)


def lade_ausnahmen(pfad: Path) -> list[Ausnahme]:
    """`sast-ausnahmen.toml` laden (fail-closed). Leere Datei = keine Ausnahmen."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise NachweisFehler("sast-ausnahmen", f"{pfad}: Datei fehlt") from None
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise NachweisFehler("sast-ausnahmen", f"{pfad}: nicht lesbar ({e})") from None
    if set(daten) - {"ausnahme"}:
        raise NachweisFehler("sast-ausnahmen", f"{pfad}: nur [[ausnahme]]-Einträge erlaubt")
    eintraege = daten.get("ausnahme", [])
    if not isinstance(eintraege, list):
        raise NachweisFehler("sast-ausnahmen", f"{pfad}: „ausnahme“ muss [[ausnahme]] sein")
    return [_ausnahme(nr, t, pfad) for nr, t in enumerate(eintraege, 1)]


# --- Gate ---------------------------------------------------------------------------------------

def sast_gate(funde: Iterable[Fund], befunde: Iterable[Befund],
              ausnahmen: Iterable[Ausnahme]) -> list[str]:
    """Blockierende Funde als Text (je Fingerabdruck einmal). Fund ≥ mittel ohne Befund mit
    gleichem `fingerabdruck` → ins Befundregister eintragen; Fund darunter ohne Befund und ohne
    passende Ausnahme → Ausnahme mit Begründung oder Befund."""
    registriert = {b.fingerabdruck for b in befunde if b.fingerabdruck is not None}
    ausnahmen = list(ausnahmen)
    blockiert: dict[str, str] = {}
    for f in funde:
        if f.fingerabdruck in registriert or f.fingerabdruck in blockiert:
            continue
        ort = f"{f.werkzeug} {f.regel} {f.pfad}:{f.zeile} ({f.schwere})"
        if f.schwere in ("hoch", "mittel"):
            blockiert[f.fingerabdruck] = (f"{befund_id(f)}: {ort} — ins Befundregister "
                                          "eintragen")
        elif not any(a.erfasst(f) for a in ausnahmen):
            blockiert[f.fingerabdruck] = (f"{befund_id(f)}: {ort} — Ausnahme mit Begründung "
                                          "oder Befund nötig")
    return list(blockiert.values())


def ungenutzte_ausnahmen(funde: Iterable[Fund], ausnahmen: Iterable[Ausnahme]) -> list[str]:
    """Ausnahmen, die keinen Fund erfassen (veraltet) — Meldung, nicht blockierend."""
    funde = list(funde)
    return [f"Ausnahme ohne Fund: {a.beschreibung()}" for a in ausnahmen
            if not any(a.erfasst(f) for f in funde)]


def zaehle(funde: Iterable[Fund]) -> dict[str, dict[str, int]]:
    """Funde je Werkzeug und Schwere (Zählprobe für Bericht und Ledger)."""
    ergebnis = {w: dict.fromkeys(SCHWEREN, 0) for w in WERKZEUGE}
    for f in funde:
        ergebnis[f.werkzeug][f.schwere] += 1
    return ergebnis


def als_json(f: Fund) -> dict[str, Any]:
    return {"werkzeug": f.werkzeug, "regel": f.regel, "pfad": f.pfad, "zeile": f.zeile,
            "schwere": f.schwere, "fingerabdruck": f.fingerabdruck, "befund_id": befund_id(f),
            "text": f.text}


# --- Gesamtlauf ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class Ergebnis:
    """Ergebnis von `pruefen`: Bericht (JSON-fähig), blockierende Funde, Netzschritte."""

    bericht: dict[str, Any]
    blockiert: list[str]
    netzschritte: list[Netzschritt]


def pruefen(worktree: Path, werkzeuge: dict[str, Werkzeug], berichte: Path) -> Ergebnis:
    """Regelsatz prüfen, Bandit bereitstellen (Netzschritt), beide Läufe mit Rohberichten
    `<berichte>/sast-semgrep.json` und `sast-bandit.json`, dann das Gate gegen Befundregister
    und Ausnahmen des Worktrees. Blockierende Funde stehen im Ergebnis (kein Abbruch), jeder
    andere Regelverstoß bricht ab."""
    nachweise = worktree / "nachweise"
    quelle = pruefe_regelsatz(nachweise)
    schritte = bandit_bereitstellen(nachweise, werkzeuge)
    berichte.mkdir(parents=True, exist_ok=True)
    semgrep, n_semgrep = semgrep_laufen(worktree, werkzeuge, berichte / BERICHT_SEMGREP)
    bandit, n_bandit = bandit_laufen(worktree, werkzeuge, berichte / BERICHT_BANDIT)
    funde = [*semgrep, *bandit]
    befunde = lade_befunde(worktree / BEFUNDE)
    ausnahmen = lade_ausnahmen(worktree / AUSNAHMEN)
    blockiert = sast_gate(funde, befunde, ausnahmen)
    bericht = {
        "regelsatz": {"url": quelle.url, "commit": quelle.commit, "lizenz": quelle.lizenz},
        "werkzeuge": {n: {"image": werkzeuge[n].image, "version": werkzeuge[n].version}
                      for n in WERKZEUGE},
        "abdeckung": {"semgrep": n_semgrep, "bandit": n_bandit},
        "zaehlprobe": zaehle(funde), "ausnahmen": len(ausnahmen),
        "ausnahmen_ohne_fund": ungenutzte_ausnahmen(funde, ausnahmen),
        "blockierend": blockiert, "funde": [als_json(f) for f in funde]}
    return Ergebnis(bericht, blockiert, schritte)
