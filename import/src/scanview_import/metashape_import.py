"""Paketprüfung und CLI-Import für Metashape-Standpunkte (E57-Weg unverändert)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import hoehen
from .lage import aus_paket
from .manifest_io import cloud_of, read_manifest, write_manifest
from .metashape import hoehen_umrechnen, lage_anwenden, metashape_station_sources, read_cameras
from .panos import write_stations


def _epsg(wkt: Any) -> int | None:
    """Nur die Autorität des gesamten CRS lesen, keine eingebettete Basis-/Achsen-ID.

    Ohne auslesbare EPSG-Autorität ist ein Vergleich nicht belegbar: fail closed.
    WKT1 AUTHORITY und WKT2 ID werden beide auf äußerster Klammerstufe unterstützt.
    """
    if not isinstance(wkt, str):
        return None
    short = re.fullmatch(r"EPSG:(\d+)", wkt.strip(), re.IGNORECASE)
    if short:
        return int(short[1])
    pattern = r'(?:ID|AUTHORITY)\s*\[\s*"EPSG"\s*,\s*"?(\d+)"?\s*\]'
    for match in re.finditer(pattern, wkt, re.IGNORECASE):
        depth, quoted = 0, False
        for character in wkt[: match.start()]:
            if character == '"':
                quoted = not quoted
            elif not quoted:
                depth += (character == "[") - (character == "]")
        if depth == 1 and not quoted:
            return int(match[1])
    return None


def pruefe_paket_crs(
    pkg: Path, epsg: int, vertical_datum: str, height_system: str | None
) -> tuple[str | None, str]:
    """Paket vorhanden, mit Punktwolke, gleiche EPSG in Metern, gleiches Höhensystem.

    Liefert (bisher deklariertes Höhensystem, geltendes Höhensystem).
    """
    if not (pkg / "project.json").is_file():
        raise ValueError("Vorhandenes Paket mit project.json aus convert erforderlich")
    manifest = read_manifest(pkg)
    cloud_of(manifest)  # Nichts in ein Paket ohne Punktwolke schreiben.
    frame = manifest.get("frame", manifest)
    crs = frame["crs"]
    if _epsg(crs.get("wkt")) != epsg or crs.get("unit", frame.get("unit")) != "m":
        raise ValueError("CRS der Kameras passt nicht zum metrischen Paket-CRS (EPSG)")
    declared = crs.get("verticalDatum")
    if (
        declared is not None
        and height_system is not None
        and not _gleiches_system(declared, height_system)
    ):
        raise ValueError("Höhensystem ist im Paket bereits anders deklariert")
    datum = declared if declared is not None else height_system
    if datum is None:
        raise ValueError("Paket-Höhensystem unbekannt; ausdrücklich mit --hoehensystem angeben")
    umrechnung = kamera_umrechnung(vertical_datum, datum)
    if not _gleiches_system(datum, vertical_datum) and umrechnung is None:
        raise ValueError("Höhensystem der Kameras passt nicht zum Paket")
    return declared, datum


def _gleiches_system(a: str, b: str) -> bool:
    na, nb = hoehen.normiere(a), hoehen.normiere(b)
    return a == b if na is None or nb is None else na == nb


def kamera_umrechnung(kameras: str, paket: str) -> tuple[str, str] | None:
    """(Quelle, Ziel), wenn die Kamerahöhen ins Paket-Höhensystem umgerechnet werden müssen."""
    q, z = hoehen.normiere(kameras), hoehen.normiere(paket)
    if q is None or z is None or q == z or not hoehen.umrechenbar(q, z):
        return None
    return q, z


def paket_datum(datum: str) -> str:
    """Zu schreibender Wert: Normwert, wenn bekannt (Entscheidung 0015), sonst unverändert."""
    return hoehen.normiere(datum) or datum


def import_metashape(
    cameras: Path, pkg: Path, levels: tuple[int, ...], height_system: str | None = None
) -> dict[str, Any]:
    """Vorabprüfung aller Quellen und CRS; dann Schreiben über die bestehende Adapter-Naht.

    `height_system` deklariert ein bisher unbekanntes Paket-Höhensystem ausdrücklich.
    Ein schon deklariertes Höhensystem darf damit nicht überschrieben werden.
    """
    export = read_cameras(cameras)
    declared, datum = pruefe_paket_crs(pkg, export.epsg, export.vertical_datum, height_system)
    umrechnung = kamera_umrechnung(export.vertical_datum, datum)
    if umrechnung is not None:
        export = hoehen_umrechnen(export, *umrechnung)
    lagematrix = aus_paket(cloud_of(read_manifest(pkg)))
    if lagematrix is not None:
        export = lage_anwenden(export, lagematrix)
    if not levels or any(type(level) is not int or level <= 0 for level in levels):
        raise ValueError("Stufen müssen positive ganze Seitengrößen sein")
    sources = metashape_station_sources(export)
    selected = []
    skipped = list(export.skipped)
    for source in sources:
        if any(level <= source.images[0].width for level in levels):
            selected.append(source)
        else:
            skipped.append({"label": source.name, "reason": "keine erreichbare Stufe"})
    if not selected:
        raise ValueError("Es bleibt keine Kamera mit erreichbarer Stufe übrig")
    write_stations(pkg, selected, tuple(sorted(set(levels))))
    if declared is None:
        manifest = read_manifest(pkg)
        manifest.get("frame", manifest)["crs"]["verticalDatum"] = paket_datum(datum)
        write_manifest(pkg, manifest)
    return {"exported": [source.name for source in selected], "skipped": skipped}
