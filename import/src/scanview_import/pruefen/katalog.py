"""Anforderungskatalog (TOML, ``schema = 1``): streng gelesen, jede Abweichung ist ein Fehler.

Der Katalog ist eine fremde Eingabe. Unbekannte Schlüssel, fehlende Pflichtfelder und doppelte
Zeilen-IDs brechen ab, statt still übergangen zu werden; eine unbekannte *Prüfart* dagegen ist
kein Schemafehler, sondern ergibt im Urteil „nicht prüfbar“ (das Werkzeug fehlt).
"""

from __future__ import annotations

import hashlib
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = 1
_OBEN = {"schema", "titel", "beschreibung", "zeile"}
_ZEILE = {"id", "anforderung", "pruefart", "parameter"}


class KatalogFehler(ValueError):
    pass


@dataclass(frozen=True)
class Zeile:
    id: str
    anforderung: str
    pruefart: str
    parameter: dict[str, Any]


@dataclass(frozen=True)
class Katalog:
    titel: str
    beschreibung: str
    zeilen: tuple[Zeile, ...]
    datei: str
    sha256: str


def _text(d: dict[str, Any], k: str, wo: str) -> str:
    v = d.get(k)
    if not isinstance(v, str) or not v.strip():
        raise KatalogFehler(f"{wo}: „{k}“ fehlt oder ist kein Text")
    return v.strip()


def _zeile(roh: Any, nr: int) -> Zeile:
    wo = f"Zeile {nr}"
    if not isinstance(roh, dict):
        raise KatalogFehler(f"{wo}: keine Tabelle")
    fremd = set(roh) - _ZEILE
    if fremd:
        raise KatalogFehler(f"{wo}: unbekannte Schlüssel {sorted(fremd)}")
    param = roh.get("parameter", {})
    if not isinstance(param, dict):
        raise KatalogFehler(f"{wo}: „parameter“ ist keine Tabelle")
    return Zeile(
        id=_text(roh, "id", wo),
        anforderung=_text(roh, "anforderung", wo),
        pruefart=_text(roh, "pruefart", wo),
        parameter=param,
    )


def lade(pfad: Path) -> Katalog:
    roh = pfad.read_bytes()
    try:
        d = tomllib.loads(roh.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        raise KatalogFehler(f"Katalog nicht lesbar: {e}") from e
    fremd = set(d) - _OBEN
    if fremd:
        raise KatalogFehler(f"unbekannte Schlüssel {sorted(fremd)}")
    if d.get("schema") != SCHEMA:
        raise KatalogFehler(f"schema = {SCHEMA} erwartet, gefunden {d.get('schema')!r}")
    zeilen_roh = d.get("zeile")
    if not isinstance(zeilen_roh, list) or not zeilen_roh:
        raise KatalogFehler("keine Zeilen ([[zeile]])")
    zeilen = tuple(_zeile(z, i + 1) for i, z in enumerate(zeilen_roh))
    ids = [z.id for z in zeilen]
    doppelt = sorted({i for i in ids if ids.count(i) > 1})
    if doppelt:
        raise KatalogFehler(f"doppelte Zeilen-IDs {doppelt}")
    beschreibung = d.get("beschreibung", "")
    if not isinstance(beschreibung, str):
        raise KatalogFehler("„beschreibung“ ist kein Text")
    return Katalog(
        titel=_text(d, "titel", "Katalog"),
        beschreibung=beschreibung.strip(),
        zeilen=zeilen,
        datei=pfad.name,
        sha256=hashlib.sha256(roh).hexdigest(),
    )
