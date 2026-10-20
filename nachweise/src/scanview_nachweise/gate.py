"""Befund-Gate und Negativtest-Lauf.

Release-Schwelle: kein offener Befund ≥ hoch; offene mittlere Befunde blockieren sofort, wenn sie
Authentifizierung, Rechte, Projekttrennung oder Datenabfluss betreffen, alle übrigen mittleren nach
mehr als 60 Tagen; eine Risikoakzeptanz blockiert, sobald ihre Frist verstrichen ist.

Negativtests: „Gate = diese Tests grün, nicht nur Testzähler“ — jede Kategorie aus
`docs/compliance/negativtests.toml` wird für sich ausgeführt und ist grün oder rot."""

from __future__ import annotations

import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import register
from .fehler import NachweisFehler
from .register import Befund, NegativKategorie, lade_negativtests, pruefe_verweise
from .werkzeuge import gefilterte_umgebung

SCHUTZKATEGORIEN = ("auth", "rechte", "trennung", "abfluss")
MITTEL_HOECHSTENS_TAGE = 60
NEGATIVTESTS = register.NEGATIVTESTS

Laeufer = Callable[[list[str], Path], int]


def _betrifft(b: Befund, ausgeliefert: Collection[str]) -> bool:
    """Ohne `artefakte` betrifft ein Befund das ganze Produkt; sonst nur die genannten."""
    return not b.artefakte or bool(set(b.artefakte) & set(ausgeliefert))


def befund_gate(befunde: Iterable[Befund], heute: date, *,
                ausgeliefert: Collection[str] | None = None) -> list[str]:
    """Blockierende Befunde als `"<ID>: <Grund>"`, in Registerreihenfolge, höchstens eine Zeile je
    Befund (die erste zutreffende Regel). Leere Liste = Schwelle eingehalten. Mit `ausgeliefert`
    (Release-Kontext) zählen Befunde nicht, deren `artefakte` keines der ausgelieferten
    Artefakte nennen — sie meldet `befunde_nicht_ausgeliefert`. Ohne `ausgeliefert` gilt jeder
    Befund (strenger Standard)."""
    blockiert: list[str] = []
    for b in befunde:
        if ausgeliefert is not None and not _betrifft(b, ausgeliefert):
            continue
        if b.status == "offen":
            alter = (heute - b.entdeckt).days
            if b.schwere in ("kritisch", "hoch"):
                blockiert.append(f"{b.id}: {b.schwere} offen")
            elif b.schwere == "mittel" and b.kategorie in SCHUTZKATEGORIEN:
                blockiert.append(f"{b.id}: mittel/{b.kategorie} offen")
            elif b.schwere == "mittel" and alter > MITTEL_HOECHSTENS_TAGE:
                blockiert.append(f"{b.id}: mittel offen seit {alter} Tagen")
        elif b.status == "akzeptiert" and (b.frist is None or b.frist < heute):
            # frist fehlt nur, wenn jemand das Register am Lader vorbei baut → fail-closed.
            frist = b.frist.isoformat() if b.frist else "unbekannt"
            blockiert.append(f"{b.id}: Akzeptanz abgelaufen am {frist}")
    return blockiert


def befunde_nicht_ausgeliefert(befunde: Iterable[Befund], heute: date,
                               ausgeliefert: Collection[str]) -> list[str]:
    """Befunde, die blockieren würden, aber nur nicht ausgelieferte Artefakte betreffen —
    informativ in Bericht und Manifest, nie still weggelassen."""
    rest = [b for b in befunde if not _betrifft(b, ausgeliefert)]
    artefakte = {b.id: b.artefakte for b in rest}
    return [f"{zeile} (betrifft nur {', '.join(artefakte[zeile.split(':', 1)[0]])} — nicht "
            "ausgeliefert, informativ)" for zeile in befund_gate(rest, heute)]


def uv_pytest(befehl: list[str], cwd: Path) -> int:
    """Führt `befehl` ohne Shell im Teilprojekt `cwd` aus und liefert den Exit-Code; ohne die
    Variablen, die einen Lauf still verändern könnten (`werkzeuge.gefilterte_umgebung`: ein
    fremdes venv, eingeschleuste pytest-Optionen wie `PYTEST_ADDOPTS="--co"`, die nur sammeln
    oder Tests abwählen — alles mit Exit 0)."""
    return subprocess.run(befehl, cwd=cwd, env=gefilterte_umgebung(), check=False).returncode


@dataclass(frozen=True)
class KategorieErgebnis:
    """Ergebnis einer Negativtest-Kategorie: grün, Zahl der tatsächlich bestandenen Testfälle
    (parametrisierte zählen einzeln) und bei Rot der Grund."""

    gruen: bool
    ausgefuehrt: int
    grund: str = ""


def _nach_teilprojekt(tests: Iterable[str]) -> dict[str, list[str]]:
    gruppen: dict[str, list[str]] = {}
    for t in tests:
        teilprojekt, rest = t.split("/", 1)
        gruppen.setdefault(teilprojekt, []).append(rest)
    return gruppen


def _node_schluessel(nodeid: str) -> tuple[str, str]:
    """`tests/test_a.py::test_x` → (`tests.test_a`, `test_x`) wie in pytests JUnit-XML
    (classname, name ohne Parameterteil)."""
    datei, name = nodeid.split("::", 1)
    return datei.removesuffix(".py").replace("/", "."), name


def _junit_auswerten(junit: Path, nodeids: list[str]) -> KategorieErgebnis:
    """Grün nur, wenn jede angefragte Node-ID mindestens einen gelaufenen, bestandenen Testfall
    hat und kein Testfall übersprungen (auch xfail), fehlgeschlagen oder fehlerhaft ist."""
    try:
        wurzel = ET.parse(junit).getroot()
    except (OSError, ET.ParseError) as e:
        return KategorieErgebnis(False, 0, f"keine auswertbare JUnit-XML ({e})")
    bestanden: dict[tuple[str, str], int] = {}
    uebersprungen = fehlgeschlagen = 0
    for fall in wurzel.iter("testcase"):
        if fall.find("skipped") is not None:
            uebersprungen += 1
        elif fall.find("failure") is not None or fall.find("error") is not None:
            fehlgeschlagen += 1
        else:
            schluessel = (fall.get("classname", ""), fall.get("name", "").split("[", 1)[0])
            bestanden[schluessel] = bestanden.get(schluessel, 0) + 1
    # Fehler außerhalb eines Testfalls (Sammelfehler) stehen nur am testsuite-Element.
    for suite in wurzel.iter("testsuite"):
        fehlgeschlagen += 0 if suite.find("testcase") is not None else int(suite.get("errors", 0))
    anzahl = sum(bestanden.values())
    gruende = []
    if uebersprungen:
        gruende.append(f"{uebersprungen} übersprungen/xfail")
    if fehlgeschlagen:
        gruende.append(f"{fehlgeschlagen} fehlgeschlagen")
    fehlend = [n for n in nodeids if _node_schluessel(n) not in bestanden]
    if fehlend:
        gruende.append("nicht ausgeführt: " + ", ".join(fehlend))
    return KategorieErgebnis(not gruende, anzahl, "; ".join(gruende))


def _teilprojekt_laufen(laeufer: Laeufer, cwd: Path, nodeids: list[str]) -> KategorieErgebnis:
    with tempfile.TemporaryDirectory(prefix="scanview-nachweise-") as tmp:
        junit = Path(tmp) / "junit.xml"
        befehl = ["uv", "run", "--locked", "pytest", "-q", f"--junitxml={junit}", *nodeids]
        code = laeufer(befehl, cwd)
        if not junit.is_file():
            return KategorieErgebnis(False, 0, f"Exit {code}, keine JUnit-XML geschrieben")
        e = _junit_auswerten(junit, nodeids)
    if code != 0:
        # 1 = Fehlschlag, 4 = unbekannte Node-ID/Nutzungsfehler, 5 = nichts gesammelt.
        grund = f"Exit {code}" + (f"; {e.grund}" if e.grund else "")
        return KategorieErgebnis(False, e.ausgefuehrt, grund)
    return e


def negativtests_details(
    repo: Path,
    *,
    kategorien: list[NegativKategorie] | None = None,
    ausfuehren: Laeufer | None = None,
) -> dict[str, KategorieErgebnis]:
    """Führt jede Kategorie ohne `instanz` aus: je Teilprojekt `uv run --locked pytest -q
    --junitxml=… <nodeids>` mit dem Teilprojekt als Arbeitsverzeichnis. Gate = diese Tests grün,
    nicht nur Testzähler: grün nur bei Exit 0 **und** wenn die JUnit-XML für jede Node-ID einen
    gelaufenen, bestandenen Fall zeigt, ohne übersprungene, xfail-, fehlgeschlagene oder
    fehlerhafte Fälle. Instanzkategorien fehlen im Ergebnis (Deploy-Smoke, nicht Release-Gate).
    Eine Kategorie ohne Test oder ein Verweis auf einen nicht vorhandenen Test ist ein
    `NachweisFehler`, bevor irgendetwas läuft."""
    if kategorien is None:
        kategorien = lade_negativtests(repo / NEGATIVTESTS)
    laeufer = ausfuehren or uv_pytest
    for k in kategorien:
        if not k.tests:
            raise NachweisFehler("negativtests", f"Kategorie {k.id} ohne Test")
    pruefe_verweise(repo, [t for k in kategorien for t in k.tests], "negativtests")

    ergebnis: dict[str, KategorieErgebnis] = {}
    for k in kategorien:
        if k.instanz:
            continue
        teile = [_teilprojekt_laufen(laeufer, repo / tp, nodeids)
                 for tp, nodeids in _nach_teilprojekt(k.tests).items()]
        ergebnis[k.id] = KategorieErgebnis(
            gruen=all(t.gruen for t in teile),
            ausgefuehrt=sum(t.ausgefuehrt for t in teile),
            grund="; ".join(t.grund for t in teile if t.grund),
        )
    return ergebnis


def negativtests_ausfuehren(
    repo: Path,
    *,
    kategorien: list[NegativKategorie] | None = None,
    ausfuehren: Laeufer | None = None,
) -> dict[str, bool]:
    """Je Kategorie ohne `instanz` grün/rot (Regeln wie `negativtests_details`)."""
    return {k: e.gruen for k, e in
            negativtests_details(repo, kategorien=kategorien, ausfuehren=ausfuehren).items()}


# --- Retests behobener Befunde ----------------------------------------------------------------

def _python_retests(befunde: Iterable[Befund]) -> list[str]:
    """Retests behobener Befunde, die pytest-Node-IDs sind (`<teilprojekt>/…py::<test>`)."""
    return sorted({b.retest for b in befunde
                   if b.status == "behoben" and b.retest and "::" in b.retest
                   and b.retest.split("::", 1)[0].endswith(".py")})


def _web_retests(befunde: Iterable[Befund]) -> list[str]:
    """Retests behobener Befunde, die Vitest-Fälle sind (`web/<datei>::<Bezeichnung>`)."""
    return sorted({b.retest for b in befunde
                   if b.status == "behoben" and b.retest and b.retest.startswith("web/")
                   and "::" in b.retest})


def _unbekannte_retests(befunde: Iterable[Befund]) -> list[str]:
    """Behobene Retests, die weder pytest- noch Vitest-Fall noch Caddy-Prüfpunkt sind."""
    python, web = set(_python_retests(befunde)), set(_web_retests(befunde))
    return sorted(f"{b.id} ({b.retest})" for b in befunde
                  if b.status == "behoben" and b.retest and b.retest not in python
                  and b.retest not in web and not b.retest.startswith(register.CADDY_PRUEFUNG))


def _vitest_auswerten(junit: Path, tests: list[str]) -> KategorieErgebnis:
    """Je Retest mindestens ein Vitest-Fall der Datei, dessen Name die Bezeichnung ist oder mit
    „ > <Bezeichnung>“ endet (describe-Präfix); jeder solche Fall bestanden, keiner übersprungen."""
    try:
        wurzel = ET.parse(junit).getroot()
    except (OSError, ET.ParseError) as e:
        return KategorieErgebnis(False, 0, f"keine auswertbare Vitest-JUnit-XML ({e})")
    gruende: list[str] = []
    anzahl = 0
    for test in tests:
        datei, name = test.removeprefix("web/").split("::", 1)
        faelle = [f for f in wurzel.iter("testcase") if f.get("classname") == datei
                  and (f.get("name") == name or (f.get("name") or "").endswith(" > " + name))]
        if not faelle:
            gruende.append(f"nicht ausgeführt: {test}")
        elif any(f.find("skipped") is not None for f in faelle):
            gruende.append(f"übersprungen: {test}")
        elif any(f.find("failure") is not None or f.find("error") is not None for f in faelle):
            gruende.append(f"fehlgeschlagen: {test}")
        else:
            anzahl += len(faelle)
    return KategorieErgebnis(not gruende, anzahl, "; ".join(gruende))


def _web_laufen(laeufer: Laeufer, web: Path, tests: list[str]) -> KategorieErgebnis:
    dateien = sorted({t.removeprefix("web/").split("::", 1)[0] for t in tests})
    with tempfile.TemporaryDirectory(prefix="scanview-vitest-") as tmp:
        junit = Path(tmp) / "junit.xml"
        code = laeufer(["npx", "--no-install", "vitest", "run", *dateien, "--reporter=junit",
                        f"--outputFile={junit}"], web)
        if not junit.is_file():
            return KategorieErgebnis(False, 0, f"Vitest Exit {code}, keine JUnit-XML")
        e = _vitest_auswerten(junit, tests)
    if code != 0:
        return KategorieErgebnis(False, e.ausgefuehrt,
                                 f"Vitest Exit {code}" + (f"; {e.grund}" if e.grund else ""))
    return e


def retest_ausserhalb(befunde: Iterable[Befund]) -> dict[str, str]:
    """Retests behobener Befunde, die nicht in der Release-Kette laufen, mit dem Ort, an dem sie
    laufen: Prüfpunkte von `caddy-pruefen.sh` gegen eine laufende Instanz (Deploy-Smoke)."""
    return {b.id: "Instanz (Deploy-Smoke)" for b in befunde
            if b.status == "behoben" and b.retest
            and b.retest.startswith(register.CADDY_PRUEFUNG)}


def retests_details(repo: Path, befunde: Iterable[Befund], *,
                    ausfuehren: Laeufer | None = None) -> KategorieErgebnis:
    """Führt den Retest jedes behobenen Befunds aus: pytest-Fälle wie eine Negativtest-Kategorie
    (je Teilprojekt `uv run --locked pytest --junitxml`), Vitest-Fälle mit
    `npx vitest run … --reporter=junit` in `web/`. Grün nur, wenn jeder Retest gelaufen und
    bestanden ist; übersprungen, xfail oder abgewählt ist rot, ebenso ein Retest unbekannter Art.
    Caddy-Prüfpunkte laufen gegen die Instanz (`retest_ausserhalb`)."""
    befunde = list(befunde)
    tests = _python_retests(befunde)
    web = _web_retests(befunde)
    pruefe_verweise(repo, [*tests, *web], "befunde")
    laeufer = ausfuehren or uv_pytest
    teile = [_teilprojekt_laufen(laeufer, repo / tp, nodeids)
             for tp, nodeids in _nach_teilprojekt(tests).items()]
    if web:
        teile.append(_web_laufen(laeufer, repo / "web", web))
    unbekannt = _unbekannte_retests(befunde)
    if unbekannt:
        teile.append(KategorieErgebnis(False, 0, "unbekannte Retest-Art (weder pytest, Vitest "
                                                 "noch Caddy-Prüfpunkt): " + ", ".join(unbekannt)))
    return KategorieErgebnis(gruen=all(t.gruen for t in teile),
                             ausgefuehrt=sum(t.ausgefuehrt for t in teile),
                             grund="; ".join(t.grund for t in teile if t.grund))
