"""Testhilfen: vollständige Pflicht-Berichte und -Werkzeuge für formgültige Manifeste
(manifest.PFLICHT_BERICHTE, manifest.PFLICHT_WERKZEUGE)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from scanview_nachweise.manifest import (
    PFLICHT_BERICHTE,
    PFLICHT_JE_SCHEMA,
    PFLICHT_PRUEFUNGEN,
    PFLICHT_WERKZEUGE,
    SCHEMA_1,
    SCHEMA_2,
)
from scanview_nachweise.werkzeuge import Werkzeug

#: Berichte/Prüfungen/Werkzeuge, die erst Schema /2 verlangt — ein Test, der ein /2-Manifest auf
#: /1 zurücksetzt, nimmt sie heraus (sonst lehnt der /1-Parser sie als unbekannt ab).
BERICHTE_NUR_2 = tuple(n for n in PFLICHT_BERICHTE if n not in PFLICHT_JE_SCHEMA[SCHEMA_1][0])
WERKZEUGE_NUR_2 = tuple(n for n in PFLICHT_WERKZEUGE if n not in PFLICHT_JE_SCHEMA[SCHEMA_1][1])
PRUEFUNGEN_NUR_2 = tuple(n for n in PFLICHT_PRUEFUNGEN if n not in PFLICHT_JE_SCHEMA[SCHEMA_1][2])


def auf_schema_2(d: dict[str, Any]) -> None:
    """Formgültiges /3-Manifest (als dict) auf Schema /2 zurücksetzen: /2 liefert import nicht
    aus (gleiche Pflichtlisten, andere Auslieferungsmenge)."""
    d["schema"] = SCHEMA_2
    for a in d["artefakte"]:
        if a["name"] == "import":
            a["ausgeliefert"] = False


def auf_schema_1(d: dict[str, Any]) -> None:
    """Formgültiges /3-Manifest (als dict) auf Schema /1 zurücksetzen: Auslieferungsmenge wie /2,
    dazu ohne die erst mit /2 verlangten Berichte, Prüfungen und Werkzeuge."""
    auf_schema_2(d)
    d["schema"] = SCHEMA_1
    for name in BERICHTE_NUR_2:
        del d["berichte"][name]
    for name in PRUEFUNGEN_NUR_2:
        del d["pruefungen"][name]
    for name in WERKZEUGE_NUR_2:
        del d["werkzeuge"][name]


def bericht_datei(name: str) -> str:
    return f"{name}.json"


def berichte_dateien(ordner: Path) -> dict[str, Path]:
    """Legt je Pflichtbericht eine kleine Datei in `ordner` an."""
    ordner.mkdir(parents=True, exist_ok=True)
    pfade = {}
    for name in PFLICHT_BERICHTE:
        pfad = ordner / bericht_datei(name)
        pfad.write_text(f'{{"bericht":"{name}"}}\n', encoding="utf-8")
        pfade[name] = pfad
    return pfade


def berichte_roh(ordner: Path | None = None) -> dict[str, dict[str, str]]:
    """Berichtsfeld eines Manifests; mit `ordner` die Hashes der dort angelegten Dateien."""
    if ordner is not None:
        return {n: {"datei": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for n, p in berichte_dateien(ordner).items()}
    return {n: {"datei": bericht_datei(n), "sha256": "0" * 64} for n in PFLICHT_BERICHTE}


def werkzeuge() -> dict[str, Werkzeug]:
    return {n: Werkzeug(n, f"test/{n}@sha256:" + "1" * 64, "1.0.0", None)
            for n in PFLICHT_WERKZEUGE}


def werkzeuge_roh() -> dict[str, dict[str, str]]:
    return {n: {"image": w.image, "version": w.version} for n, w in werkzeuge().items()}
