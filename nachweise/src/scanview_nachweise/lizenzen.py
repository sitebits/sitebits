"""Lizenzabgleich der Release-Kette.

Zwei Regeln je ausgeliefertem SBOM, beide fail-closed:

1. **Positivliste:** jede *erklärte* Lizenzangabe (`acknowledgement = declared`, bzw. ohne Angabe)
   jeder Komponente ist erlaubt — eine SPDX-ID aus `erlaubt`, ein SPDX-Ausdruck, der unter
   `erlaubt` erfüllbar ist (`AND`: alle, `OR`: eine, `X WITH Y` nur als ganzer Eintrag), oder ein
   Freitext/Trove-Klassifikator, der über `klassifikatoren` einem solchen Ausdruck zugeordnet ist.
   Eine Komponente ohne erklärte Lizenz, ein unbekannter Freitext oder ein unlesbarer Ausdruck
   ist ein Fehler — außer für genau diese Version liegt eine **belegte Lizenzfeststellung** vor
   (`[feststellungen]`: Ausdruck, Beleg, SHA-256 des Lizenztexts im Paket). Sie gilt nur, solange
   die Komponente selbst nichts erklärt, und nur für Name **und** Version. Die Liste steht
   maschinenlesbar in `nachweise/lizenzen.toml` (Kategorien: permissive Lizenzen auch außerhalb
   der wörtlichen Liste, LGPL, BlueOak-1.0.0/Python-2.0, sowie MPL-2.0 unverändert weitergegeben).
2. **Nennung:** jede Komponente steht mit Name **und** Version in einer Tabellenzeile von
   `docs/THIRD_PARTY.md` (erste Spalte Name, zweite Version; `a / b` paarweise; Klammerzusätze
   wie „ol (OpenLayers)“ zählen nicht zum Namen). Eine Erwähnung im Fließtext zählt nicht."""

from __future__ import annotations

import re
import tomllib
import urllib.parse
from collections import Counter
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .fehler import NachweisFehler

_REGEL = "lizenzen"
_TOKEN = re.compile(r"\s*(\(|\)|[A-Za-z0-9.+:-]+)")
_MAX_MELDUNGEN = 40


@dataclass(frozen=True)
class Lizenzregeln:
    erlaubt: frozenset[str]
    klassifikatoren: dict[str, str]
    # (Name, Version) → SPDX-Ausdruck, festgestellt aus dem mitgelieferten Lizenztext
    feststellungen: dict[tuple[str, str], str] = field(default_factory=dict)


# --- SPDX-Ausdrücke ------------------------------------------------------------------------------

def _tokens(ausdruck: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(ausdruck):
        if ausdruck[pos:].strip() == "":
            break
        m = _TOKEN.match(ausdruck, pos)
        if m is None:
            raise NachweisFehler(_REGEL, f"Lizenzausdruck „{ausdruck}“ nicht lesbar")
        tokens.append(m.group(1))
        pos = m.end()
    return tokens


def ausdruck_erlaubt(ausdruck: str, erlaubt: frozenset[str]) -> bool:
    """Ist der SPDX-Ausdruck unter der Positivliste erfüllbar? Vorrang `WITH` > `AND` > `OR`,
    Klammern. Operatoren nur in Großschrift (SPDX); IDs werden exakt verglichen."""
    tokens = _tokens(ausdruck)
    pos = 0

    def fehler() -> NachweisFehler:
        return NachweisFehler(_REGEL, f"Lizenzausdruck „{ausdruck}“ nicht lesbar")

    def atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            raise fehler()
        t = tokens[pos]
        if t == "(":
            pos += 1
            wert = oder()
            if pos >= len(tokens) or tokens[pos] != ")":
                raise fehler()
            pos += 1
            return wert
        if t in (")", "AND", "OR", "WITH"):
            raise fehler()
        pos += 1
        if pos < len(tokens) and tokens[pos] == "WITH":
            if pos + 1 >= len(tokens) or tokens[pos + 1] in ("(", ")", "AND", "OR", "WITH"):
                raise fehler()
            ausnahme = tokens[pos + 1]
            pos += 2
            return f"{t} WITH {ausnahme}" in erlaubt
        return t in erlaubt

    def und() -> bool:
        nonlocal pos
        wert = atom()
        while pos < len(tokens) and tokens[pos] == "AND":
            pos += 1
            rechts = atom()
            wert = wert and rechts
        return wert

    def oder() -> bool:
        nonlocal pos
        wert = und()
        while pos < len(tokens) and tokens[pos] == "OR":
            pos += 1
            rechts = und()
            wert = wert or rechts
        return wert

    if not tokens:
        raise fehler()
    ergebnis = oder()
    if pos != len(tokens):
        raise fehler()
    return ergebnis


# --- Regeln laden --------------------------------------------------------------------------------

def lade_regeln(pfad: Path) -> Lizenzregeln:
    """`lizenzen.toml`: `erlaubt` (nicht leere Liste von SPDX-IDs bzw. „X WITH Y“) und optional
    `[klassifikatoren]` (Freitext → SPDX-Ausdruck). Unbekannte Schlüssel → Fehler."""
    try:
        daten = tomllib.loads(pfad.read_text(encoding="utf-8"))
    except OSError as e:
        raise NachweisFehler(_REGEL, f"{pfad}: nicht lesbar ({e.strerror})") from None
    except tomllib.TOMLDecodeError as e:
        raise NachweisFehler(_REGEL, f"{pfad}: kein gültiges TOML ({e})") from None
    unbekannt = sorted(set(daten) - {"erlaubt", "klassifikatoren", "feststellungen"})
    if unbekannt:
        raise NachweisFehler(_REGEL, f"{pfad}: unbekannte Schlüssel {', '.join(unbekannt)}")
    erlaubt = daten.get("erlaubt")
    if not isinstance(erlaubt, list) or not erlaubt or \
            not all(isinstance(e, str) and e.strip() for e in erlaubt):
        raise NachweisFehler(_REGEL, f"{pfad}: „erlaubt“ muss eine nicht leere Liste sein")
    klassen = daten.get("klassifikatoren", {})
    if not isinstance(klassen, dict):
        raise NachweisFehler(_REGEL, f"{pfad}: „klassifikatoren“ muss eine Tabelle sein")
    regeln = Lizenzregeln(frozenset(erlaubt), {})
    for name, ausdruck in klassen.items():
        if not isinstance(ausdruck, str):
            raise NachweisFehler(_REGEL, f"{pfad}: Klassifikator „{name}“ ohne Ausdruck")
        ausdruck_erlaubt(ausdruck, regeln.erlaubt)  # lesbar?
        regeln.klassifikatoren[name] = ausdruck
    fest = daten.get("feststellungen", {})
    if not isinstance(fest, dict):
        raise NachweisFehler(_REGEL, f"{pfad}: „feststellungen“ muss eine Tabelle sein")
    for schluessel, eintrag in fest.items():
        name, at, version = schluessel.rpartition("@")
        if not (at and name and version) or not isinstance(eintrag, dict) \
                or set(eintrag) != {"ausdruck", "beleg", "sha256"} \
                or not all(isinstance(v, str) and v.strip() for v in eintrag.values()) \
                or not re.fullmatch(r"[0-9a-f]{64}", eintrag["sha256"]):
            raise NachweisFehler(_REGEL, f"{pfad}: Feststellung „{schluessel}“ braucht "
                                         "name@version mit ausdruck, beleg und sha256")
        if not ausdruck_erlaubt(eintrag["ausdruck"], regeln.erlaubt):
            raise NachweisFehler(_REGEL, f"{pfad}: Feststellung „{schluessel}“: "
                                         f"{eintrag['ausdruck']} nicht erlaubt")
        regeln.feststellungen[(normalisiere(name), version)] = eintrag["ausdruck"]
    return regeln


# --- THIRD_PARTY.md ------------------------------------------------------------------------------

def normalisiere(name: str) -> str:
    """Vergleichsform eines Paketnamens (PEP 503 für PyPI, npm-Namen sind schon klein)."""
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _zellen(zeile: str) -> list[str]:
    return [z.strip() for z in zeile.strip().strip("|").split("|")]


def genannte_komponenten(text: str) -> set[tuple[str, str]]:
    """(normalisierter Name, Version) aus allen Tabellenzeilen: erste Spalte Name(n), zweite
    Version(en); `a / b` paarweise; Klammerzusätze und Backticks im Namen entfallen, von der
    Version zählt das erste Wort."""
    genannt: set[tuple[str, str]] = set()
    for zeile in text.splitlines():
        if not zeile.lstrip().startswith("|"):
            continue
        zellen = _zellen(zeile)
        if len(zellen) < 2 or set(zellen[1]) <= set("-: "):
            continue
        namen = [re.sub(r"\(.*?\)|`", "", n).strip() for n in zellen[0].split(" / ")]
        versionen = [v.strip().split(" ")[0] for v in zellen[1].split(" / ")]
        if len(namen) != len(versionen):
            continue
        genannt |= {(normalisiere(n), v) for n, v in zip(namen, versionen, strict=True) if n}
    return genannt


# --- Prüfung -------------------------------------------------------------------------------------

def _alle(komponenten: object) -> Iterator[dict[str, Any]]:
    for k in komponenten if isinstance(komponenten, list) else []:
        if isinstance(k, dict):
            yield k
            yield from _alle(k.get("components"))


def _paketdateien(komponenten: object) -> set[int]:
    """Dateikomponenten (`type: file`) unter einem Paket: einzelne ausgelieferte Dateien dieses
    Pakets mit eigenem Hash (sbom.nachbearbeiten). Lizenz und Nennung gelten über das Paket;
    Pakete in Paketen und Dateien auf oberster Ebene prüft `probleme` weiter selbst."""
    return {id(innen) for k in _alle(komponenten) if k.get("type") != "file"
            for innen in (k.get("components") or [])
            if isinstance(innen, dict) and innen.get("type") == "file"}


def _name(k: dict[str, Any]) -> str:
    """Paketname aus der PURL (npm mit Scope: `@scope/name`), sonst aus `name`."""
    purl = k.get("purl")
    if isinstance(purl, str) and purl.startswith("pkg:"):
        ohne = purl[4:].split("?", 1)[0].split("#", 1)[0]
        kopf, at, version = ohne.rpartition("@")
        if at and "/" not in version:
            ohne = kopf
        _typ, _, rest = urllib.parse.unquote(ohne).partition("/")
        if rest:
            return normalisiere(rest)
    return normalisiere(str(k.get("name", "?")))


def _erklaert(k: dict[str, Any]) -> list[dict[str, Any]]:
    return [e for e in k.get("licenses", []) if isinstance(e, dict)
            and (e.get("license", e) or {}).get("acknowledgement", "declared") == "declared"]


def _angabe(eintrag: dict[str, Any]) -> tuple[str, str | None]:
    """(Anzeige, SPDX-Ausdruck oder None bei nicht zugeordnetem Freitext)."""
    if isinstance(eintrag.get("expression"), str):
        return eintrag["expression"], eintrag["expression"]
    lizenz = eintrag.get("license")
    if isinstance(lizenz, dict):
        if isinstance(lizenz.get("id"), str):
            return lizenz["id"], lizenz["id"]
        if isinstance(lizenz.get("name"), str):
            return lizenz["name"], None
    return repr(eintrag), None


def probleme(bom: dict[str, Any], regeln: Lizenzregeln,
             genannt: set[tuple[str, str]]) -> list[str]:
    """Alle Verstöße eines SBOMs als Zeilen (leer = bestanden). Hauptkomponente ausgenommen
    (eigene Lizenz, steht nicht in THIRD_PARTY.md)."""
    ergebnis: list[str] = []
    komponenten = list(_alle(bom.get("components")))
    if not komponenten:
        return ["keine Komponenten im SBOM — nichts, was geprüft werden könnte"]
    paketdateien = _paketdateien(bom.get("components"))
    for k in komponenten:
        if id(k) in paketdateien:
            continue
        ref = str(k.get("purl") or k.get("bom-ref") or k.get("name", "?"))
        erklaert = _erklaert(k)
        name, version = _name(k), str(k.get("version", ""))
        if not erklaert and (name, version) not in regeln.feststellungen:
            ergebnis.append(f"{ref}: keine erklärte Lizenz")
        for e in erklaert:
            anzeige, ausdruck = _angabe(e)
            if ausdruck is None:
                ausdruck = regeln.klassifikatoren.get(anzeige)
                if ausdruck is None:
                    ergebnis.append(f"{ref}: Lizenz-Freitext „{anzeige}“ ohne Zuordnung in "
                                    "lizenzen.toml")
                    continue
            try:
                ok = ausdruck_erlaubt(ausdruck, regeln.erlaubt)
            except NachweisFehler as fehler:
                ergebnis.append(f"{ref}: {fehler.text}")
                continue
            if not ok:
                ergebnis.append(f"{ref}: Lizenz {anzeige} nicht erlaubt")
        if (name, version) not in genannt:
            ergebnis.append(f"{name} {version} nicht in docs/THIRD_PARTY.md genannt")
    return ergebnis


def _zaehle(bom: dict[str, Any]) -> dict[str, int]:
    zaehler: Counter[str] = Counter()
    for k in _alle(bom.get("components")):
        for e in _erklaert(k):
            zaehler[_angabe(e)[0]] += 1
    return dict(sorted(zaehler.items()))


def pruefe(boms: Mapping[str, dict[str, Any]], regeln: Lizenzregeln,
           third_party: str) -> dict[str, Any]:
    """Alle übergebenen (ausgelieferten) SBOMs prüfen; jeder Verstoß → `NachweisFehler`
    („lizenzen“) mit allen Meldungen. Sonst Bericht je Artefakt (Komponentenzahl, Lizenzen)."""
    genannt = genannte_komponenten(third_party)
    meldungen: list[str] = []
    bericht: dict[str, Any] = {}
    for name, bom in boms.items():
        meldungen += [f"{name}: {p}" for p in probleme(bom, regeln, genannt)]
        bericht[name] = {"komponenten": sum(1 for _ in _alle(bom.get("components"))),
                         "lizenzen": _zaehle(bom)}
    if meldungen:
        rest = len(meldungen) - _MAX_MELDUNGEN
        raise NachweisFehler(_REGEL, f"{len(meldungen)} Verstoß/Verstöße: "
                                     + "; ".join(meldungen[:_MAX_MELDUNGEN])
                                     + (f"; … und {rest} weitere" if rest > 0 else ""))
    return {"artefakte": bericht, "ergebnis": "bestanden"}
