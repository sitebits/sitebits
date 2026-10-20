"""Liest nur Metadaten einer E57 (keine Punkte)."""

from __future__ import annotations

import math
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

import pye57
from pye57 import libe57

from .model import ImageInfo, Inventory, Pose, ScanInfo

_KINDS = {
    "pinholeRepresentation": "pinhole",
    "sphericalRepresentation": "spherical",
    "cylindricalRepresentation": "cylindrical",
    "visualReferenceRepresentation": "visual",
}


def _val(node: Any, key: str, default: Any = None) -> Any:
    return node[key].value() if node.isDefined(key) else default


def _pose(node: Any) -> Pose:
    if not node.isDefined("pose"):
        return Pose((0.0, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0))
    p = node["pose"]
    r = p["rotation"] if p.isDefined("rotation") else None
    t = p["translation"] if p.isDefined("translation") else None
    rot = tuple(float(r[k].value()) for k in "wxyz") if r is not None else (1.0, 0.0, 0.0, 0.0)
    tr = tuple(float(t[k].value()) for k in "xyz") if t is not None else (0.0, 0.0, 0.0)
    return Pose(tr, rot)  # type: ignore[arg-type]


def _color_max(proto: Any, fields: list[str]) -> int | None:
    """Deklarierter Maximalwert von ``colorRed`` im E57-Prototyp.

    8-bit-Quellen deklarieren hier 255 (verifiziert an Pilotkirche und Pilothalle, je
    45/1 Scans, ``IntegerNode(...).maximum()``); 16-bit-Quellen 65535. Robust gegen
    Nicht-IntegerNode-Repräsentationen (z. B. ScaledIntegerNode) — dann ``None``,
    Skalierung unterbleibt in diesem Fall statt zu raten.
    """
    if "colorRed" not in fields:
        return None
    for j in range(proto.childCount()):
        node = proto.get(j)
        if node.elementName() != "colorRed":
            continue
        try:
            return int(libe57.IntegerNode(node).maximum())
        except (RuntimeError, TypeError):
            return None
    return None


def _color_limits_max(scan: Any) -> int | None:
    """Deklarierter ``colorRedMaximum`` aus der Data3D-``colorLimits``-Struktur, falls
    vorhanden.

    Anders als das Prototyp-Feld (``_color_max``) ist das kein Encoding-Attribut, sondern ein
    optionaler Hinweis des Aufnahmegeräts auf den semantischen Wertebereich. Die Pilotkirche setzt
    ihn (an allen 45 Scans geprüft), die Pilothalle nicht — PDALs ``readers.e57`` nutzt genau diese
    Struktur, um 8-bit-RGB selbst auf 16 bit zu skalieren (empirisch verifiziert, auch unter
    ``--stream``): ohne eigene ``filters.assign``-Stufe liefert ``readers.e57`` an echten
    Pilotkirchen-Daten für die ersten Punkte bereits exakt x257-skalierte Werte. ``None``, wenn
    die Struktur oder ``colorRedMaximum`` fehlt.
    """
    if not scan.isDefined("colorLimits"):
        return None
    cl = scan["colorLimits"]
    if not cl.isDefined("colorRedMaximum"):
        return None
    return int(cl["colorRedMaximum"].value())


# 2^14 m · 2^-23 = 2^-9 m ≈ 1,95 mm: ab hier ist Float32 für Millimetermessung zu grob.
FLOAT32_GRENZE_M = 16_384.0


def _coord_precision(proto: Any, fields: list[str]) -> Literal["single", "double", "scaled"]:
    precisions = []
    for key in ("cartesianX", "cartesianY", "cartesianZ"):
        if key not in fields:
            continue
        node = proto[key]
        if isinstance(node, libe57.ScaledIntegerNode):
            precisions.append("scaled")
        elif node.precision() == libe57.FloatPrecision.E57_SINGLE:
            precisions.append("single")
        else:
            precisions.append("double")
    # Gemischte Achsen: die schwächste Float-Genauigkeit entscheidet.
    if "single" in precisions:
        return "single"
    return "scaled" if precisions and all(p == "scaled" for p in precisions) else "double"


def _local_abs_max(scan: Any) -> float | None:
    keys = ("xMinimum", "yMinimum", "zMinimum", "xMaximum", "yMaximum", "zMaximum")
    if not scan.isDefined("cartesianBounds"):
        return None
    bounds = scan["cartesianBounds"]
    if not all(bounds.isDefined(k) for k in keys):
        return None
    return max(abs(float(bounds[k].value())) for k in keys)


def pruefe_e57_genauigkeit(inv: Inventory, zulassen: bool = False) -> dict[str, Any]:
    """Prüft lokale Float32-Koordinaten; die double-Pose zählt ausdrücklich nicht."""
    warnings = []
    largest_step = 0.0
    for scan in inv.scans:
        value = scan.local_abs_max_m
        if scan.coord_precision != "single" or value is None or value < FLOAT32_GRENZE_M:
            continue
        step = 2.0 ** (math.floor(math.log2(value)) - 23)
        message = (
            f"E57 speichert Koordinaten in einfacher Genauigkeit (Float32) mit Beträgen "
            f"bis {value:g} m — Raster ≥ {step * 1000:g} mm. Quelle mit doppelter "
            "Genauigkeit exportieren (PDAL: writers.e57.double_precision=true) "
            "oder --float32-zulassen."
        )
        if not zulassen:
            raise ValueError(message)
        warnings.append(message)
        largest_step = max(largest_step, step)
    return {"float32Schritt_m": largest_step, "warnings": warnings} if warnings else {}


def read_inventory(path: Path) -> Inventory:
    e = pye57.E57(str(path))
    root = e.image_file.root()
    crs = _val(root, "coordinateMetadata")
    inv = Inventory(filename=path.name, size_bytes=path.stat().st_size, crs_wkt=crs or None)
    data3d = root["data3D"]
    for i in range(data3d.childCount()):
        scan = data3d[i]
        # prototype() liefert einen generischen Node ohne childCount()/get() —
        # laut pye57-API (help(pye57.libe57.CompressedVectorNode.prototype)) muss
        # er explizit auf StructureNode heruntergecastet werden (verifiziert via
        # help() und Testfixture, siehe Task-3-Report).
        proto = libe57.StructureNode(scan["points"].prototype())
        fields = [proto.get(j).elementName() for j in range(proto.childCount())]
        inv.scans.append(
            ScanInfo(
                i,
                _val(scan, "guid", ""),
                _val(scan, "name", f"Scan {i}"),
                int(scan["points"].childCount()),
                fields,
                _pose(scan),
                _color_max(proto, fields),
                _color_limits_max(scan),
                _coord_precision(proto, fields),
                _local_abs_max(scan),
            )
        )
    for info in inv.scans:
        if info.coord_precision == "single" and info.local_abs_max_m is None:
            from .convert import SAMPLE_PER_SCAN, _sample_world_bounds

            local = _sample_world_bounds(
                e.image_file, data3d[info.index], Pose((0., 0., 0.), (1., 0., 0., 0.)),
                SAMPLE_PER_SCAN,
            )
            if local is not None:
                info.local_abs_max_m = max(abs(v) for bound in local for v in bound)
    if root.isDefined("images2D"):
        imgs = root["images2D"]
        for i in range(imgs.childCount()):
            im = imgs[i]
            for key, kind in _KINDS.items():
                if not im.isDefined(key):
                    continue
                rep = im[key]
                blob = next((b for b in ("jpegImage", "pngImage") if rep.isDefined(b)), "")
                pp = (_val(rep, "principalPointX"), _val(rep, "principalPointY"))
                inv.images.append(
                    ImageInfo(
                        index=i,
                        guid=_val(im, "guid", ""),
                        kind=kind,  # type: ignore[arg-type]
                        width=int(_val(rep, "imageWidth", 0)),
                        height=int(_val(rep, "imageHeight", 0)),
                        pose=_pose(im),
                        scan_guid=_val(im, "associatedData3DGuid"),
                        focal_length=_val(rep, "focalLength"),
                        pixel_width=_val(rep, "pixelWidth"),
                        pixel_height=_val(rep, "pixelHeight"),
                        principal_point=pp if pp[0] is not None else None,
                        blob_node=f"{key}/{blob}" if blob else "",
                    )
                )
                break
    e.close()
    return inv


def inventory_to_json(inv: Inventory) -> dict[str, Any]:
    def pose(p: Pose) -> dict[str, list[float]]:
        return {
            "translation": list(p.translation),
            "rotationWxyz": [float(v) for v in p.rotation_wxyz],
        }

    return {
        "filename": inv.filename,
        "sizeBytes": inv.size_bytes,
        "crsWkt": inv.crs_wkt,
        "scans": [
            {
                "index": s.index,
                "guid": s.guid,
                "name": s.name,
                "pointCount": s.point_count,
                "fields": s.fields,
                "coordPrecision": s.coord_precision,
                "localAbsMaxM": s.local_abs_max_m,
                "pose": pose(s.pose),
            }
            for s in inv.scans
        ],
        "images": [
            {
                **{
                    k: v
                    for k, v in asdict(im).items()
                    if k
                    not in (
                        "pose",
                        "scan_guid",
                        "blob_node",
                        "focal_length",
                        "pixel_width",
                        "pixel_height",
                        "principal_point",
                    )
                },
                "scanGuid": im.scan_guid,
                "pose": pose(im.pose),
                "focalLength": im.focal_length,
                "pixelWidth": im.pixel_width,
                "pixelHeight": im.pixel_height,
                "principalPoint": list(im.principal_point) if im.principal_point else None,
                "blobNode": im.blob_node,
            }
            for im in inv.images
        ],
    }
