"""CLI-Weg `fotos-metashape`: prüfen (CRS, Kalibrierung, Rahmen), dann `write_images`."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .fotos import plane_fotos, write_images
from .fotos_pruefung import GRENZE_PX, rueckprojektion
from .lage import aus_paket
from .manifest_io import cloud_of, read_manifest, write_manifest
from .mesh_frame import sample_cloud
from .metashape import hoehen_umrechnen, lage_anwenden, read_frame_cameras
from .metashape_import import kamera_umrechnung, paket_datum, pruefe_paket_crs
from .rahmenpruefung import pruefe_rahmen

WOLKEN_STICHPROBE = 200_000


def import_fotos(
    kameras: Path,
    pkg: Path,
    *,
    max_kante: int | None,
    gruppen: list[str] | None = None,
    rahmen_toleranz: float | None = None,
    prozesse: int = 1,
    height_system: str | None = None,
    exif_drehung_ignorieren: bool = False,
    rueckprojektion_grenze: float = GRENZE_PX,
) -> dict[str, Any]:
    """Erst alles prüfen, dann schreiben; jeder Abbruch lässt das Paket unverändert."""
    export = read_frame_cameras(kameras, exif_drehung_ignorieren=exif_drehung_ignorieren)
    if (pkg / "project.json").is_file() and "layers" not in read_manifest(pkg):
        raise ValueError(
            "Orientierte Fotos brauchen ein Schema-2-Paket — vorher scanview-import migrate"
        )
    declared, datum = pruefe_paket_crs(pkg, export.epsg, export.vertical_datum, height_system)
    umrechnung = kamera_umrechnung(export.vertical_datum, datum)
    if umrechnung is not None:
        export = hoehen_umrechnen(export, *umrechnung)
    lagematrix = aus_paket(cloud_of(read_manifest(pkg)))
    if lagematrix is not None:
        export = lage_anwenden(export, lagematrix)
    if gruppen:
        gewaehlt = [c for c in export.cameras if c.group in gruppen]
        if not gewaehlt:
            raise ValueError("Keine Rahmenkamera in den gewählten Gruppen")
        export = replace(export, cameras=gewaehlt)
    cloud = cloud_of(read_manifest(pkg))
    pfad = (pkg / cloud["url"]).resolve()
    if not pfad.is_relative_to(pkg.resolve()):
        raise ValueError("Wolkenpfad außerhalb des Pakets")
    rahmen = pruefe_rahmen(
        export.tie_points, sample_cloud(pfad, WOLKEN_STICHPROBE), toleranz_m=rahmen_toleranz
    )
    frame_check = {
        "againstLayer": cloud["id"],
        "medianM": rahmen.median_m,
        "p95M": rahmen.p95_m,
        "n": rahmen.n,
        "offsetM": list(rahmen.versatz_m),
        "toleranceM": rahmen_toleranz,
    }
    exif_ignoriert = sum(c.exif_orientation != 1 for c in export.cameras)
    _, geplant = plane_fotos(pkg, export, max_kante=max_kante)
    rueck = rueckprojektion(
        {"images": geplant}, export, export.tie_projections, grenze_px=rueckprojektion_grenze
    )
    if not rueck["ok"]:
        schlecht = [b for b in rueck["perImage"] if b["medianPx"] > rueck["limitPx"]]
        raise ValueError(
            f"Rückprojektion: {len(schlecht)} Foto(s) über {rueck['limitPx']} px Median, "
            f"z. B. {schlecht[0]['id']} mit {schlecht[0]['medianPx']:.2f} px"
        )
    out = write_images(
        pkg,
        export,
        max_kante=max_kante,
        prozesse=prozesse,
        provenance={
            "source": "metashape",
            "document": export.document,
            "frameCheck": frame_check,
            "reprojection": {k: v for k, v in rueck.items() if k != "perImage"},
            "exifOrientationIgnored": exif_ignoriert,
        },
    )
    if declared is None:
        manifest = read_manifest(pkg)
        manifest.get("frame", manifest)["crs"]["verticalDatum"] = paket_datum(datum)
        write_manifest(pkg, manifest)
    return {
        "exportiert": len(out["images"]),
        "uebersprungen": export.skipped,
        "rahmenpruefung": frame_check,
        "rueckprojektion": {k: v for k, v in rueck.items() if k != "perImage"},
        "anonymisierung": out["anonymization"]["status"],
        "exifDrehungIgnoriert": exif_ignoriert,
    }
