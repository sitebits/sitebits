"""Paketinhalt für Agenten (`paket_lesen`): Ebenen, Koordinaten- und
Höhensystem, Zweithöhe, Punktklassen, Anzahlen — ohne Bilder und ohne Punktdaten.

Gelesen wird `project.json` des Pakets wie im Katalog (gleiche Größengrenze). Orientierte Fotos
erscheinen nur, wenn die Rolle sie auch laden dürfte (fotos_tor): ohne freigegebene Anonymisierung
fehlt die Ebene für Betrachter und Rückmelder ganz, wie `/api/authz` für `images.json`.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import genauigkeit, koordinaten
from .katalog import MAX_PROJECT_JSON, PaketFehler

# Felder je Ebenenart, die ein Agent zum Verstehen braucht (keine URLs, keine Herkunftspfade).
_FELDER = {
    "pointcloud": ("pointCount", "boundsWorld", "attributes", "classes"),
    "mesh": ("boundsWorld",),
    "splats": ("format", "chunkCount", "lodMethod", "boundsWorld", "splatCount",
               "frameCheck", "alignment", "shDegree"),
    "panoramas": ("count",),
    "images": ("count",),
}


def uebersicht(paket: Path, *, fotos_sichtbar: bool) -> dict[str, Any]:
    datei = paket / "project.json"
    if datei.stat().st_size > MAX_PROJECT_JSON:
        raise PaketFehler("project.json zu groß")
    try:
        m: Any = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError) as fehler:
        raise PaketFehler(f"project.json nicht lesbar: {fehler}") from fehler
    if not isinstance(m, dict) or m.get("schemaVersion") != 2:
        raise PaketFehler("project.json ist kein Schema-2-Paket")
    f_roh = m.get("frame")
    frame: dict[str, Any] = f_roh if isinstance(f_roh, dict) else {}
    c_roh = frame.get("crs")
    crs: dict[str, Any] = c_roh if isinstance(c_roh, dict) else {}
    roh = m.get("layers")
    layers: list[Any] = roh if isinstance(roh, list) else []
    ebenen: list[dict[str, Any]] = []
    for e in layers:
        if not isinstance(e, dict) or not isinstance(e.get("id"), str):
            continue
        art = e.get("kind")
        if art == "images" and not fotos_sichtbar:
            continue
        titel = e.get("title")
        eintrag: dict[str, Any] = {"id": e["id"], "art": art,
                                   "titel": titel if isinstance(titel, str) else e["id"]}
        for f in _FELDER.get(str(art), ()):
            if f in e:
                eintrag[f] = e[f]
        ebenen.append(eintrag)
    return {
        "revision": m.get("revision"),
        "einheit": koordinaten.einheit(crs),
        "hoehensystem": crs.get("verticalDatum"),
        "undulation": frame.get("undulation"),
        "ebenen": ebenen,
        "genauigkeit": [g.als_json() for g in genauigkeit.ebenen(layers)],
    }
