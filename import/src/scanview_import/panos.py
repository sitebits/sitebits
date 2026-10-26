"""Standpunkte + Würfelpanoramen → Stufen-JPEGs + ``stations.json`` (Schema 2).

**Adapter-Naht:** ``write_stations`` kennt nur ``StationSource``-Objekte. Die E57-Quelle
(``e57_station_sources``) ist ein Adapter; ein künftiger NavVis-Adapter liefert dieselben Objekte
in derselben Konvention — Bildposen im Welt-KS, Lochkamera blickt entlang -Z, +Y ist oben,
Pixelzeile 0 ist der obere Bildrand, Hauptpunkt in Pixeln (Pixelmitte bei ganzen Zahlen). Eine
Umrechnung fremder Konventionen gehört in den Adapter, nicht in den Viewer.

**Orientierung der Würfelseiten:** Jedes Face-JPEG wird nur **verkleinert**, nicht gedreht oder
gespiegelt. Der Viewer spannt jedes Bild über seine eigene Bildpose und Intrinsik auf
(``panorama.faceImages`` → ``images[i]``), die Pixelorientierung bleibt deshalb unangetastet.

**Schema 2**: ``schemaVersion`` im Dateikopf; ``panorama.levelFile`` benennt die
Stufendatei unterhalb von ``faces.<f>``; ``panorama.faceImages`` verweist je Seite auf ihr Bild in
``images[]`` (Bild-GUIDs sind bei der Pilotkirche Platzhalter und taugen nicht als Schlüssel);
``images[].imageWidth/imageHeight`` sind die Pixelmaße (``pixelWidth/pixelHeight`` bleiben der
Pixelabstand in m, E57-Namen); ``groundZ`` schreibt ``overview`` und bleibt bei einem Neulauf nur
erhalten, wenn die Standpunkt-ID im alten und neuen Lauf denselben ``scanGuid`` trägt.
``pointSourceId`` (additiv) ist die PointSourceId der Punkte dieses Scans in der Wolke
(E57: Scanindex + 1, dieselbe Zählung wie ``convert`` und wie ``S{n:03d}``).
"""

from __future__ import annotations

import hashlib
import io
import json
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pye57
from PIL import Image

from .geometry import cube_face_for
from .inventory import read_inventory
from .manifest_io import read_manifest, set_stations, write_manifest
from .model import FaceName, ImageInfo, Pose

FACES: tuple[FaceName, ...] = ("px", "nx", "py", "ny", "pz", "nz")
STATIONS_SCHEMA_VERSION = 2
# Dateiname je Stufe unterhalb von ``faces.<f>`` (Vertrag).
LEVEL_FILE = "{level}.jpg"


@dataclass
class StationSource:
    """Ein Standpunkt, wie ihn ein Quell-Adapter liefert (Konvention: Modul-Docstring)."""

    scan_guid: str
    name: str
    pose: Pose
    images: list[ImageInfo]
    # Würfelseite → Index in ``images``; leer = kein Würfel an diesem Standpunkt.
    faces: dict[FaceName, int]
    read_image: Callable[[ImageInfo], Image.Image]
    # PointSourceId, den die Punkte dieses Standpunkts in der Wolke tragen;
    # None = Quelle kennt keine Zuordnung.
    point_source_id: int | None = None


def _read_blob(e: pye57.E57, image_index: int, blob_node: str) -> bytes:
    """Liest einen Bild-Blob (z. B. ``pinholeRepresentation/jpegImage``) aus der E57.

    ``BlobNode.byteCount()``/``.read(buf, 0, n)`` mit einem ``np.uint8``-Puffer — der
    pye57-0.4.19-Stride-Bug (Binding Constraints) betrifft nur ``SourceDestBuffer``
    (Punktdaten), nicht Blobs.
    """
    node: Any = e.image_file.root()["images2D"][image_index]
    for part in blob_node.split("/"):
        node = node[part]
    size = node.byteCount()
    buf = np.empty(size, dtype=np.uint8)
    node.read(buf, 0, size)
    return buf.tobytes()


def _cube_faces(
    imgs: list[ImageInfo], scan_rotation_wxyz: tuple[float, float, float, float]
) -> dict[FaceName, int]:
    """Ordnet Pinhole-Bilder eines Scans den 6 Würfelseiten zu (Index in ``imgs``),
    falls eindeutig."""
    pinholes = [i for i, im in enumerate(imgs) if im.kind == "pinhole" and im.blob_node]
    if len(pinholes) != 6:
        return {}
    by_face = {cube_face_for(imgs[i].pose.rotation_wxyz, scan_rotation_wxyz): i for i in pinholes}
    if len(by_face) != 6:
        return {}
    return {f: by_face[f] for f in FACES}


def face_levels(faces: dict[FaceName, ImageInfo], levels: tuple[int, ...]) -> tuple[int, list[int]]:
    """Quellgröße (kleinste Kante über alle Seiten, Breite **und** Höhe) und die davon
    erreichbaren Stufen — keine Stufe wird hochskaliert."""
    src_size = min(min(im.width, im.height) for im in faces.values())
    return src_size, [lv for lv in levels if lv <= src_size]


@contextmanager
def e57_station_sources(e57: Path) -> Iterator[list[StationSource]]:
    """E57-Adapter: je Scan ein ``StationSource``; Bilder werden erst beim Schreiben einzeln
    gelesen (ein Bild gleichzeitig im Speicher, 270 x 4096² bei der Pilotkirche)."""
    inv = read_inventory(e57)
    e = pye57.E57(str(e57))
    try:

        def read(im: ImageInfo) -> Image.Image:
            return Image.open(io.BytesIO(_read_blob(e, im.index, im.blob_node))).convert("RGB")

        sources: list[StationSource] = []
        for scan in inv.scans:
            imgs = [im for im in inv.images if im.scan_guid == scan.guid]
            sources.append(
                StationSource(
                    scan_guid=scan.guid,
                    name=scan.name,
                    pose=scan.pose,
                    images=imgs,
                    faces=_cube_faces(imgs, scan.pose.rotation_wxyz),
                    read_image=read,
                    # Gleiche Zählung wie convert (Scanindex in data3D-Reihenfolge + 1).
                    point_source_id=scan.index + 1,
                )
            )
        yield sources
    finally:
        e.close()


def _previous_ground_z(pkg: Path) -> dict[str, tuple[str | None, float | None]]:
    """(``scanGuid``, ``groundZ``) je Standpunkt-ID aus einem vorhandenen ``stations.json``
    (schreibt ``overview``).

    Der ``scanGuid`` wird mitgeliefert, damit der Aufrufer einen gecachten Wert nur dann
    übernimmt, wenn er wirklich zum selben Scan gehört: die
    Standpunkt-ID ``S001`` ist nur eine laufende Nummer der aktuellen Reihenfolge — ändert
    sich die Scan-Menge oder -Reihenfolge zwischen zwei Läufen (neue Scans, gelöschte Scans,
    andere Sortierung durch einen künftigen Adapter), landet sonst ein ``groundZ`` still und
    "sicher falsch" auf einem anderen physischen Standpunkt (verstößt gegen "nie geraten").
    """
    p = pkg / "stations.json"
    if not p.exists():
        return {}
    try:
        old = json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}
    return {
        str(s["id"]): (s.get("scanGuid"), s.get("groundZ"))
        for s in old.get("stations", [])
        if isinstance(s, dict) and "id" in s
    }


def _pose_json(p: Pose) -> dict[str, list[float]]:
    return {
        "translation": [float(v) for v in p.translation],
        "rotationQuat": [float(v) for v in p.rotation_wxyz],
    }


def _image_json(
    im: ImageInfo, scan_rotation_wxyz: tuple[float, float, float, float]
) -> dict[str, Any]:
    return {
        "guid": im.guid,
        "kind": im.kind,
        "face": cube_face_for(im.pose.rotation_wxyz, scan_rotation_wxyz)
        if im.kind == "pinhole"
        else None,
        "pose": _pose_json(im.pose),
        "focalLength": im.focal_length,
        "pixelWidth": im.pixel_width,
        "pixelHeight": im.pixel_height,
        "principalPoint": list(im.principal_point) if im.principal_point else None,
        "imageWidth": im.width,
        "imageHeight": im.height,
    }


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def write_stations(
    pkg: Path,
    sources: Iterable[StationSource],
    levels: tuple[int, ...],
    *,
    anonymization: dict[str, Any] | None = None,
    vor_abschluss: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """Schreibt Stufen-JPEGs und ``stations.json`` (Schema 2), aktualisiert die Standpunkte in
    ``project.json`` (``set_stations`` — Schema 1: ``stations``, Schema 2: Ebene
    ``standpunkte``).

    ``anonymization`` (F20): Jeder Lauf schreibt ``{"required": true, "status": "unreviewed",
    "files": {Pfad: SHA-256}}`` — Panoramen gelten für die Plattform als personenbezogen, bis eine
    Freigabe sie an genau diese Dateien bindet. Ein geprüfter Anonymisierungslauf überlagert
    Status, Werkzeug und Manifest-Hash. ``vor_abschluss`` läuft nach den Bildern und vor
    ``stations.json``; bricht es ab, bleibt das alte Manifest stehen und passt nicht mehr zu den
    Dateien (auf der Plattform gesperrt statt falsch freigegeben)."""
    prev = _previous_ground_z(pkg)
    stations: list[dict[str, Any]] = []
    for n, src in enumerate(sources, start=1):
        sid = f"S{n:03d}"
        prev_guid, prev_ground_z = prev.get(sid, (None, None))
        # Nur übernehmen, wenn die ID im alten und neuen Lauf denselben Scan meint.
        ground_z = prev_ground_z if prev_guid == src.scan_guid else None
        if src.faces and set(src.faces) != set(FACES):
            raise ValueError(f"{sid}: Adapter lieferte unvollständigen Würfel {sorted(src.faces)}")
        face_imgs = {f: src.images[i] for f, i in src.faces.items()}
        src_size, use_levels = face_levels(face_imgs, levels) if face_imgs else (0, [])
        panorama: dict[str, Any] | None = None
        # Keine erreichbare Stufe → kein Panorama (statt "cube" mit leeren Ordnern, D19).
        if use_levels:
            for face, im in face_imgs.items():
                img = src.read_image(im)
                face_dir = pkg / "panos" / sid / face
                face_dir.mkdir(parents=True, exist_ok=True)
                for lv in use_levels:
                    img.resize((lv, lv), Image.Resampling.LANCZOS).save(
                        face_dir / LEVEL_FILE.format(level=lv), "JPEG", quality=85
                    )
                del img  # ein Bild gleichzeitig im Speicher
            panorama = {
                "kind": "cube",
                "faceSize": src_size,
                "levels": use_levels,
                "levelFile": LEVEL_FILE,
                "faces": {f: f"panos/{sid}/{f}" for f in FACES},
                "faceImages": {f: src.faces[f] for f in FACES},
            }
        stations.append(
            {
                "id": sid,
                "scanGuid": src.scan_guid,
                "name": src.name,
                "pose": _pose_json(src.pose),
                "groundZ": ground_z,
                # Additiv: gilt, wenn project.json cloud.pointSourceIsScan.
                "pointSourceId": src.point_source_id,
                "panorama": panorama,
                "images": [_image_json(im, src.pose.rotation_wxyz) for im in src.images],
            }
        )

    if vor_abschluss is not None:
        vor_abschluss()
    dateien = {
        f"{p['faces'][f]}/{LEVEL_FILE.format(level=lv)}": _sha256(
            pkg / p["faces"][f] / LEVEL_FILE.format(level=lv))
        for s in stations if (p := s["panorama"]) is not None
        for f in FACES for lv in p["levels"]
    }
    anon: dict[str, Any] = {"required": True, "status": "unreviewed"}
    anon.update(anonymization or {})
    anon["files"] = dateien
    out = {"schemaVersion": STATIONS_SCHEMA_VERSION, "stations": stations, "anonymization": anon}
    (pkg / "stations.json").write_text(json.dumps(out, indent=2))

    m = read_manifest(pkg)
    set_stations(m, "stations.json", len(stations))
    write_manifest(pkg, m)
    return out


def build_panos(
    e57: Path, pkg: Path, levels: tuple[int, ...] = (512, 1024, 2048, 4096)
) -> dict[str, Any]:
    """E57 → Stufen-JPEGs + ``stations.json`` (E57-Adapter + ``write_stations``)."""
    with e57_station_sources(e57) as sources:
        return write_stations(pkg, sources, levels)
