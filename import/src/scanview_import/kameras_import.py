"""Paketprüfung und CLI-Import für Kugelkameras aus `kameras.json` (E57-Weg unverändert)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import anonymisiert, hoehen
from .kameras import hoehen_umrechnen, kamera_station_sources, lage_anwenden, read_cameras
from .lage import aus_paket
from .manifest_io import cloud_of, read_manifest, write_manifest
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


def _gleiches_crs(wkt: Any, epsg: int) -> bool:
    """EPSG-Autorität des Pakets oder, wenn die WKT keine trägt, Gleichwertigkeit laut PROJ.

    ODMs Punktwolken tragen UTM als „PROJCS["unknown", …]“ ohne Autorität; PROJ vergleicht dann
    Datum, Projektion und Parameter (`CRS.equals`) — eine Nachbarzone ist nicht gleichwertig.
    """
    gelesen = _epsg(wkt)
    if gelesen is not None or not isinstance(wkt, str):
        return gelesen == epsg
    from pyproj import CRS
    from pyproj.exceptions import CRSError

    try:
        return bool(CRS.from_wkt(wkt).equals(CRS.from_epsg(epsg), ignore_axis_order=True))
    except CRSError:
        return False


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
    if not _gleiches_crs(crs.get("wkt"), epsg) or crs.get("unit", frame.get("unit")) != "m":
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


def import_kameras(
    cameras: Path,
    pkg: Path,
    levels: tuple[int, ...],
    height_system: str | None = None,
    anonymisiert_manifest: Path | None = None,
) -> dict[str, Any]:
    """Vorabprüfung aller Quellen und CRS; dann Schreiben über die bestehende Adapter-Naht.

    `height_system` deklariert ein bisher unbekanntes Paket-Höhensystem ausdrücklich.
    Ein schon deklariertes Höhensystem darf damit nicht überschrieben werden.
    `anonymisiert_manifest` (F20): Pixel nur aus dem geprüften Anonymisierungslauf
    (`anonymisiert.py`); jede Kamera muss darin stehen, sonst bricht der Import ab.
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
    uebernahmen: list[anonymisiert.Uebernahme] = []
    anon_block: dict[str, Any] | None = None
    if anonymisiert_manifest is not None:
        m = anonymisiert.lade(anonymisiert_manifest)
        uebernahmen = anonymisiert.zuordnen(
            m, [(c.label, c.image_path, c.width, c.height) for c in export.cameras])
        anon_block = {"status": "reviewed", "tool": anonymisiert.WERKZEUG,
                      "manifestSha256": m.sha256}
    sources = kamera_station_sources(export, {u.quelle: u.pixel for u in uebernahmen})
    selected = []
    skipped = list(export.skipped)
    for source in sources:
        if any(level <= source.images[0].width for level in levels):
            selected.append(source)
        else:
            skipped.append({"label": source.name, "reason": "keine erreichbare Stufe"})
    if not selected:
        raise ValueError("Es bleibt keine Kamera mit erreichbarer Stufe übrig")
    write_stations(pkg, selected, tuple(sorted(set(levels))), anonymization=anon_block,
                   vor_abschluss=lambda: anonymisiert.nachpruefen(uebernahmen))
    if declared is None:
        manifest = read_manifest(pkg)
        manifest.get("frame", manifest)["crs"]["verticalDatum"] = paket_datum(datum)
        write_manifest(pkg, manifest)
    bericht: dict[str, Any] = {"exported": [source.name for source in selected],
                               "skipped": skipped}
    if anon_block is not None:
        bericht["anonymisierung"] = {
            **anon_block,
            "panos": [{"pano": u.pano, "aktion": u.aktion} for u in uebernahmen],
        }
    return bericht
