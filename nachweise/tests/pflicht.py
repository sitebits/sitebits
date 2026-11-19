"""Testhilfen: vollständige Pflicht-Berichte und -Werkzeuge für formgültige Manifeste
(manifest.PFLICHT_BERICHTE, manifest.PFLICHT_WERKZEUGE)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from scanview_nachweise.manifest import (
    AUSGELIEFERT_JE_SCHEMA,
    PFLICHT_BERICHTE,
    PFLICHT_JE_SCHEMA,
    PFLICHT_WERKZEUGE,
    SCHEMA_1,
    SCHEMA_2,
    SCHEMA_3,
    SCHEMA_4,
)
from scanview_nachweise.werkzeuge import Werkzeug


def auf_schema(d: dict[str, Any], schema: str) -> None:
    """Formgültiges Manifest des aktuellen Schemas (als dict) auf `schema` umstellen: genau dessen
    Pflichtberichte, -prüfungen und -werkzeuge (fehlende Berichte mit Platzhalter-Hash, fehlende
    Prüfungen „bestanden“) und dessen Auslieferungsmenge. Nur für Lese-/Prüfer-Tests — die
    Berichtsdateien selbst prüft dort niemand."""
    berichte, werkzeuge_, pruefungen = PFLICHT_JE_SCHEMA[schema]
    d["schema"] = schema
    d["berichte"] = {n: d["berichte"].get(n, {"datei": bericht_datei(n), "sha256": "0" * 64})
                     for n in berichte}
    d["pruefungen"] = {n: d["pruefungen"].get(n, "bestanden") for n in pruefungen}
    d["werkzeuge"] = {n: d["werkzeuge"][n] for n in werkzeuge_}
    for a in d["artefakte"]:
        a["ausgeliefert"] = a["name"] in AUSGELIEFERT_JE_SCHEMA[schema]


def auf_schema_4(d: dict[str, Any]) -> None:
    """/4: dazu `stufe-upgrade` und die Prüfung `upgrade`."""
    auf_schema(d, SCHEMA_4)


def auf_schema_3(d: dict[str, Any]) -> None:
    """/3: Pflichtlisten wie /2, import ausgeliefert."""
    auf_schema(d, SCHEMA_3)


def auf_schema_2(d: dict[str, Any]) -> None:
    """/2: liefert import nicht aus (Pflichtlisten wie /3)."""
    auf_schema(d, SCHEMA_2)


def auf_schema_1(d: dict[str, Any]) -> None:
    """/1: Auslieferungsmenge wie /2, ohne die erst mit /2 verlangten Berichte, Prüfungen und
    Werkzeuge."""
    auf_schema(d, SCHEMA_1)


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
