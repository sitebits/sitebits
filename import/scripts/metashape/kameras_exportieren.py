"""Ausgerichtete Kugel- und Rahmenkameras aus dem aktiven Metashape-Pro-2.x-Chunk exportieren.

In Metashape über Werkzeuge → Skript ausführen, mit Argumenten:
--epsg 32632 --hoehensystem ellipsoidisch --ausgabe /absolut/kameras.json
Optional mehrfach --gruppe Boden --gruppe Luft und --art frame / --art spherical.
Unbegradigte Originalbilder verwenden.

Rahmenkameras (formatVersion 2) tragen die angepasste Kalibrierung (`sensor.calibration`,
nicht `user_calib`) und je Sensor Referenzpaare aus `camera.project()`: Der Importer prüft damit
sein eigenes Verzeichnungsmodell gegen Metashape, statt Koeffizienten nach Namen abzubilden.
Dazu kommen Tie Points (Rahmenprüfung gegen die Paketwolke) und Tie-Point-Projektionen
(Rückprojektionsprüfung in verify).
API: https://www.agisoft.com/pdf/metashape_python_api_2_3_1.pdf
Die reine Mathematik und export_cameras sind ohne Metashape mit einem Stub testbar.
"""

from __future__ import annotations

import argparse
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


def _normal(v: Sequence[float]) -> list[float]:
    length = math.sqrt(_dot(v, v))
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError("Kameraachsen sind nicht endlich oder entartet")
    return [x / length for x in v]


def rotation_from_axes(axes: Sequence[Sequence[float]]) -> list[list[float]]:
    """Drei numerisch projizierte Achsen rechtshändig orthonormalisieren (Gram-Schmidt).

    Ergebnis: Spalten sind Kamera-X/Y/Z im Gitter des Ziel-CRS.
    Keine Metashape-/NumPy-Abhängigkeit in der reinen Exportmathematik.
    """
    if len(axes) != 3 or any(len(axis) != 3 for axis in axes):
        raise ValueError("Drei dreidimensionale Kameraachsen erforderlich")
    if not all(math.isfinite(v) for axis in axes for v in axis):
        raise ValueError("Kameraachsen müssen endlich sein")
    x = _normal(axes[0])
    projection = _dot(x, axes[1])
    y = _normal([v - projection * u for v, u in zip(axes[1], x, strict=True)])
    z = [x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]]
    if _dot(z, axes[2]) <= 1e-12:
        raise ValueError("Kameraachsen sind nicht rechtshändig oder entartet")
    return [[x[i], y[i], z[i]] for i in range(3)]


def _pose(chunk: Any, camera: Any, target: Any, api: Any) -> tuple[list[float], list[list[float]]]:
    transform = chunk.transform.matrix * camera.transform
    centre = transform.mulp(api.Vector([0, 0, 0]))
    source = getattr(chunk, "world_crs", None) or chunk.crs.geoccs

    def project(point: Sequence[float]) -> list[float]:
        return [float(v) for v in api.CoordinateSystem.transform(api.Vector(point), source, target)]

    position = project(centre)
    if not all(math.isfinite(v) for v in position):
        raise ValueError("Projizierte Kameraposition ist nicht endlich")
    axes = []
    for i in range(3):
        unit = [0.0, 0.0, 0.0]
        unit[i] = 1.0
        endpoint = transform.mulp(api.Vector(unit))
        # Ein Meter im Welt-KS, unabhängig von der Skalierung des internen Chunk-KS.
        direction = _normal([float(endpoint[j] - centre[j]) for j in range(3)])
        plus = project([float(centre[j]) + direction[j] for j in range(3)])
        minus = project([float(centre[j]) - direction[j] for j in range(3)])
        axes.append([(plus[j] - minus[j]) / 2 for j in range(3)])
    # Nicht crs.localframe(): das liefert geografisch Nord, nicht Gitter-Nord.
    # Beispiel UTM 33 bei Potsdam: etwa 1,5° Meridiankonvergenz. Deshalb die
    # Kamerachsen numerisch ins Ziel-CRS projizieren, dann orthonormalisieren.
    return position, rotation_from_axes(axes)


# p3/p4 nur mitgeben, damit der Import Werte ≠ 0 erkennt und mit Grund ablehnt.
CALIBRATION_KEYS = ("f", "cx", "cy", "k1", "k2", "k3", "k4", "p1", "p2", "p3", "p4", "b1", "b2")
REFERENCE_GRID = (5, 4)
# Randring (Ecken und Kantenmitten) — dort wirken k3/k4 am stärksten. Je Richtung der äußerste
# Strahl, den Metashape projiziert und dessen Pixel im Bild liegt (Verzeichnung schiebt Ecken
# hinaus).
REFERENCE_RAND = (0.98, 0.95, 0.9, 0.85)
REFERENCE_DEPTH = 10.0


def _rolling_shutter_active(sensor: Any, api: Any) -> bool:
    value = getattr(sensor, "rolling_shutter", None)
    shutter = getattr(api, "Shutter", None)
    if shutter is not None:
        return value is not None and value != shutter.Model.Disabled
    # Metashape 1.x: bool
    return bool(value)


def _calibration(sensor: Any) -> dict[str, float] | None:
    calib = getattr(sensor, "calibration", None)
    if calib is None:
        return None
    values = {key: float(getattr(calib, key, 0.0) or 0.0) for key in CALIBRATION_KEYS}
    if not all(math.isfinite(v) for v in values.values()) or values["f"] <= 0:
        return None
    return values


def _reference_pairs(
    camera: Any, width: int, height: int, f: float, api: Any
) -> list[dict[str, Any]]:
    """Kamera-KS-Strahlen auf einem Raster → Pixel laut Metashape (`camera.project`)."""
    nx, ny = REFERENCE_GRID
    pairs = []
    for j in range(ny):
        for i in range(nx):
            x = (-0.8 + 1.6 * i / (nx - 1)) * width / 2 / f
            y = (-0.8 + 1.6 * j / (ny - 1)) * height / 2 / f
            local = api.Vector([x * REFERENCE_DEPTH, y * REFERENCE_DEPTH, REFERENCE_DEPTH])
            pixel = camera.project(camera.transform.mulp(local))
            if pixel is None:
                raise ValueError(f"{camera.label}: Referenzpunkt nicht projizierbar")
            pairs.append({"ray": [x, y], "pixel": [float(pixel[0]), float(pixel[1])]})
    for sx, sy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0), (-1, 1), (0, 1), (1, 1)):
        for s in REFERENCE_RAND:
            x, y = sx * s * width / 2 / f, sy * s * height / 2 / f
            local = api.Vector([x * REFERENCE_DEPTH, y * REFERENCE_DEPTH, REFERENCE_DEPTH])
            pixel = camera.project(camera.transform.mulp(local))
            if pixel is not None and 0 <= pixel[0] <= width and 0 <= pixel[1] <= height:
                pairs.append({"ray": [x, y], "pixel": [float(pixel[0]), float(pixel[1])]})
                break
    return pairs


def _sample(items: Sequence[Any], n: int) -> list[Any]:
    """Höchstens n Elemente, gleichmäßig verteilt und deterministisch."""
    if n <= 0:
        return []
    if len(items) <= n:
        return list(items)
    return [items[i * len(items) // n] for i in range(n)]


def _tie_points(
    chunk: Any,
    cameras: Sequence[Any],
    world: Any,
    max_points: int,
    max_cameras: int,
    max_projections: int,
) -> tuple[list[list[float]], list[dict[str, Any]]]:
    tie = getattr(chunk, "tie_points", None)
    if tie is None:
        return [], []
    roh: dict[int, Any] = {}
    for point in tie.points:
        # w = 0: Punkt im Unendlichen, ohne Weltkoordinate (Projektionen filtern über `in roh`).
        if point.valid and float(point.coord[3]) != 0.0:
            roh[int(point.track_id)] = point.coord
    # Die KBS-Umrechnung ist teuer (Minuten bei Millionen Tie Points): nur was die Stichproben
    # brauchen.
    by_track: dict[int, list[float]] = {}

    def welt(t: int) -> list[float]:
        if t not in by_track:
            c = roh[t]
            by_track[t] = world([float(c[k]) / float(c[3]) for k in range(3)])
        return by_track[t]

    points = [welt(t) for t in _sample(sorted(roh), max_points)]
    projections: list[dict[str, Any]] = []
    # Je Gruppe eine eigene Stichprobe: der Import mit --gruppe braucht Projektionen der Gruppe.
    gruppen: dict[str, list[Any]] = {}
    for camera in cameras:
        gruppen.setdefault(camera.group.label if camera.group is not None else "", []).append(
            camera
        )
    auswahl = [c for name in sorted(gruppen) for c in _sample(gruppen[name], max_cameras)]
    for camera in auswahl:
        own = [p for p in tie.projections[camera] if int(p.track_id) in roh]
        for p in _sample(own, max_projections):
            projections.append(
                {
                    "camera": camera.label,
                    "pixel": [float(p.coord[0]), float(p.coord[1])],
                    "world": welt(int(p.track_id)),
                }
            )
    return points, projections


def _kind(camera: Any, api: Any) -> str | None:
    if camera.sensor is None:
        return None
    if camera.sensor.type == api.Sensor.Type.Spherical:
        return "spherical"
    frame_type = getattr(api.Sensor.Type, "Frame", None)
    if frame_type is not None and camera.sensor.type == frame_type:
        return "frame"
    return None


def _skip_reason(
    camera: Any,
    kind: str | None,
    group: str,
    kinds: Sequence[str],
    groups: Sequence[str] | None,
    api: Any,
) -> str | None:
    """Erster Grund, eine Kamera nicht zu exportieren; None = exportieren."""
    checks = [
        (kind is None, "weder Kugel- noch Rahmenkamera"),
        (kind not in kinds, "Kameraart nicht ausgewählt"),
        (
            kind == "frame" and _rolling_shutter_active(camera.sensor, api),
            "Rolling-Shutter-Kompensation nicht unterstützt",
        ),
        (kind == "frame" and _calibration(camera.sensor) is None, "keine angepasste Kalibrierung"),
        (groups is not None and group not in groups, "Gruppe nicht ausgewählt"),
        (camera.transform is None, "nicht ausgerichtet"),
        (camera.photo is None, "Bild fehlt"),
    ]
    return next((reason for hit, reason in checks if hit), None)


def _sensor_entry(camera: Any, api: Any) -> dict[str, Any]:
    sensor = camera.sensor
    calib = _calibration(sensor)
    assert calib is not None
    return {
        "id": int(sensor.key),
        "label": str(sensor.label),
        "type": "frame",
        "width": int(sensor.width),
        "height": int(sensor.height),
        "calibration": calib,
        "referencePairs": _reference_pairs(
            camera, int(sensor.width), int(sensor.height), calib["f"], api
        ),
    }


def export_cameras(
    chunk: Any,
    epsg: int,
    height_system: str,
    groups: Sequence[str] | None = None,
    *,
    kinds: Sequence[str] = ("spherical", "frame"),
    max_tie_points: int = 5000,
    max_projection_cameras: int = 50,
    max_projections: int = 200,
    api: Any = None,
) -> dict[str, Any]:
    """Keine Änderung am Chunk; ausgerichtete Kugel- und Rahmenkameras auswählen."""
    if api is None:
        import Metashape

        api = Metashape
    if chunk is None or chunk.crs is None or chunk.transform.matrix is None:
        raise ValueError("Aktiver georeferenzierter Chunk mit Transformation erforderlich")
    if type(epsg) is not int or epsg <= 0 or not height_system.strip():
        raise ValueError("Positive EPSG-Nummer und Höhensystem erforderlich")
    target = api.CoordinateSystem(f"EPSG::{epsg}")
    # Geoid-Kontext des Dokuments nutzen, wenn Metashape die API anbietet.
    if hasattr(target, "setContext"):
        target.setContext(api.app.document)
    cameras, skipped = [], []
    sensors: dict[int, dict[str, Any]] = {}
    frame_cameras: list[Any] = []
    for camera in chunk.cameras:
        group = camera.group.label if camera.group is not None else ""
        kind = _kind(camera, api)
        reason = _skip_reason(camera, kind, group, kinds, groups, api)
        if reason is not None:
            skipped.append({"label": camera.label, "reason": reason})
            continue
        image_path = Path(camera.photo.path)
        # Relative Metashape-Pfade nicht gegen ein zufälliges Prozess-CWD auflösen.
        if not image_path.is_absolute():
            raise ValueError(f"{camera.label}: Metashape-Bildpfad muss absolut sein")
        if not image_path.is_file():
            raise ValueError(f"{camera.label}: Bild fehlt: {image_path}")
        position, rotation = _pose(chunk, camera, target, api)
        entry = {
            "label": camera.label,
            "group": group,
            "kind": kind,
            "imagePath": str(image_path),
            "width": camera.sensor.width,
            "height": camera.sensor.height,
            "position": position,
            "rotation": rotation,
        }
        if kind == "frame":
            entry["sensor"] = int(camera.sensor.key)
            frame_cameras.append(camera)
            if int(camera.sensor.key) not in sensors:
                sensors[int(camera.sensor.key)] = _sensor_entry(camera, api)
        cameras.append(entry)
    source = getattr(chunk, "world_crs", None) or chunk.crs.geoccs

    def world(point: Sequence[float]) -> list[float]:
        geo = chunk.transform.matrix.mulp(api.Vector(list(point)))
        return [float(v) for v in api.CoordinateSystem.transform(api.Vector(geo), source, target)]

    tie_points, projections = (
        _tie_points(
            chunk, frame_cameras, world, max_tie_points, max_projection_cameras, max_projections
        )
        if frame_cameras
        else ([], [])
    )
    return {
        "formatVersion": 2,
        "crs": {"epsg": epsg, "verticalDatum": height_system},
        "cameras": cameras,
        "sensors": list(sensors.values()),
        "tiePoints": tie_points,
        "tieProjections": projections,
        "skipped": skipped,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epsg", type=int, required=True)
    parser.add_argument("--hoehensystem", required=True)
    parser.add_argument("--gruppe", action="append", default=None)
    parser.add_argument("--ausgabe", type=Path, required=True)
    parser.add_argument("--art", action="append", choices=["spherical", "frame"], default=None)
    args = parser.parse_args(argv)
    import Metashape

    if not Metashape.app.version.startswith("2."):
        raise ValueError("Metashape Pro 2.x erforderlich")
    data = export_cameras(
        Metashape.app.document.chunk,
        args.epsg,
        args.hoehensystem,
        args.gruppe,
        kinds=tuple(args.art or ("spherical", "frame")),
    )
    document = Metashape.app.document
    if document.path:
        doc_path = Path(document.path)
        data["document"] = {
            "path": str(doc_path),
            "mtime": doc_path.stat().st_mtime if doc_path.is_file() else None,
            "chunk": Metashape.app.document.chunk.label,
        }
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    args.ausgabe.write_text(text, encoding="utf-8")
    print(f"Exportiert: {len(data['cameras'])}, übersprungen: {len(data['skipped'])}")
    for camera in data["cameras"]:
        print(f"Exportiert: {camera['group']} / {camera['label']}")
    for camera in data["skipped"]:
        print(f"Übersprungen: {camera['label']} — {camera['reason']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
