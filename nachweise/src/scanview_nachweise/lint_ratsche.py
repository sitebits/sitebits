"""Zusatzregeln zählen; harte Ruff-Regeln bleiben ohne tolerierten Altbestand."""
from __future__ import annotations

import json
import subprocess
from collections import Counter
from pathlib import Path

from .fehler import NachweisFehler

TEILE = ("server", "mcp-server", "nachweise", "import")
ZUSATZ = "BLE,C90,ASYNC"
Basis = dict[str, dict[str, int]]


def validiere(daten: object) -> Basis:
    if not isinstance(daten, dict):
        raise NachweisFehler("lint-ratsche", "kaputte Basis: kein Objekt")
    for datei, regeln in daten.items():
        if (not isinstance(datei, str) or not datei or "\\" in datei
                or Path(datei).is_absolute() or ".." in Path(datei).parts
                or not isinstance(regeln, dict) or not regeln):
            raise NachweisFehler("lint-ratsche", "kaputte Basis: Datei/Regeln")
        for regel, anzahl in regeln.items():
            if (not isinstance(regel, str) or not regel.startswith(("BLE", "C90", "ASYNC"))
                    or type(anzahl) is not int or anzahl <= 0):
                raise NachweisFehler("lint-ratsche", "kaputte Basis: Regel/Zählung")
    return daten


def vergleiche(basis: Basis, ist: Basis) -> list[str]:
    validiere(basis)
    validiere(ist)
    neu = [f"{d}: {r} {n} > {basis.get(d, {}).get(r, 0)}"
           for d, regeln in ist.items() for r, n in regeln.items()
           if n > basis.get(d, {}).get(r, 0)]
    if neu:
        raise NachweisFehler("lint-ratsche", "neue Funde: " + "; ".join(neu))
    return [f"Basis senken: {d}: {r} {n} → {ist.get(d, {}).get(r, 0)}"
            for d, regeln in basis.items() for r, n in regeln.items()
            if ist.get(d, {}).get(r, 0) < n]


def _ruff(teil: Path, befehl: list[str], option: str) -> list[dict[str, object]]:
    try:
        lauf = subprocess.run([*befehl, "check", "--no-cache", option, ZUSATZ,
                               "--output-format", "json", "."], cwd=teil,
                              capture_output=True, text=True, check=False)
        daten = json.loads(lauf.stdout)
    except (OSError, ValueError) as e:
        raise NachweisFehler("lint-ratsche", f"Ruff nicht auswertbar: {e}") from e
    if (lauf.returncode not in (0, 1) or not isinstance(daten, list)
            or any(not isinstance(f, dict) for f in daten)
            or (lauf.returncode == 1) != bool(daten)):
        raise NachweisFehler("lint-ratsche", f"Ruff fehlgeschlagen: {lauf.stderr[:500]}")
    return daten


def pruefe_teil(teil: Path, *, aktualisieren: bool = False,
                befehl: list[str] | None = None) -> list[str]:
    befehl = befehl or ["uv", "run", "--locked", "ruff"]
    pfad = teil / "ruff-basis.json"
    try:
        basis = validiere(json.loads(pfad.read_text(encoding="utf-8")))
    except (OSError, ValueError) as e:
        raise NachweisFehler("lint-ratsche", f"Basis nicht lesbar: {e}") from e
    hart = _ruff(teil, befehl, "--ignore")
    if hart:
        raise NachweisFehler("lint-ratsche", f"{teil.name}: {len(hart)} harte Ruff-Funde")
    ist: Basis = {}
    try:
        zaehler: Counter[tuple[str, str]] = Counter()
        for f in _ruff(teil, befehl, "--select"):
            datei = Path(str(f["filename"])).relative_to(teil.resolve()).as_posix()
            regel = str(f["code"])
            zaehler[datei, regel] += 1
        for (datei, regel), n in zaehler.items():
            ist.setdefault(datei, {})[regel] = n
    except (KeyError, ValueError) as e:
        raise NachweisFehler("lint-ratsche", f"Ruff-Ausgabe kaputt: {e}") from e
    hinweise = vergleiche(basis, ist)
    if aktualisieren and hinweise:
        pfad.write_text(json.dumps(ist, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return hinweise


def pruefe(wurzel: Path, *, aktualisieren: bool = False,
           teile: tuple[str, ...] = TEILE) -> list[str]:
    hinweise = []
    for teil in teile:
        hinweise.extend(pruefe_teil(wurzel / teil, aktualisieren=aktualisieren))
    return hinweise
