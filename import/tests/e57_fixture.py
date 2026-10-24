"""Schreibt kleine E57-Dateien mit Scans und Pinhole-Bildern (libE57 über pye57.libe57)."""

from __future__ import annotations

import io
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from pye57 import libe57

from scanview_import.geometry import apply_pose
from scanview_import.model import FaceName, Pose

FACE_COLORS: dict[FaceName, tuple[int, int, int]] = {
    "px": (255, 0, 0),
    "nx": (0, 255, 255),
    "py": (0, 255, 0),
    "ny": (255, 0, 255),
    "pz": (0, 0, 255),
    "nz": (255, 255, 0),
}
# Rotation (w,x,y,z), die -Z des Bild-KS (Blickachse) auf die jeweilige
# Würfelachse dreht. Kreuzgeprüft in test_fixture.py::test_face_rot_is_consistent_
# with_cube_face_for gegen cube_face_for — identisch zur Parametrisierung in
# test_geometry.py.
_S = 0.7071067811865476
FACE_ROT: dict[FaceName, tuple[float, float, float, float]] = {
    "nz": (1, 0, 0, 0),
    "pz": (0, 1, 0, 0),
    "nx": (_S, 0, _S, 0),
    "px": (_S, 0, -_S, 0),
    "py": (_S, _S, 0, 0),
    "ny": (_S, -_S, 0, 0),
}


def qmul(
    a: tuple[float, float, float, float], b: tuple[float, float, float, float]
) -> tuple[float, float, float, float]:
    """Quaternionenprodukt a·b (w, x, y, z) — Bildpose im Welt-KS =
    Scanrotation ∘ Seitenrotation."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


@dataclass
class FixtureInfo:
    path: Path
    scan_guids: list[str]
    world_points_valid: np.ndarray
    invalid_count: int


def _guid() -> str:
    return "{" + str(uuid.uuid4()) + "}"


def _add_pose(imf: libe57.ImageFile, parent: libe57.StructureNode, pose: Pose) -> None:
    p = libe57.StructureNode(imf)
    parent.set("pose", p)
    rot = libe57.StructureNode(imf)
    p.set("rotation", rot)
    for k, v in zip("wxyz", pose.rotation_wxyz, strict=True):
        rot.set(k, libe57.FloatNode(imf, float(v)))
    tr = libe57.StructureNode(imf)
    p.set("translation", tr)
    for k, v in zip("xyz", pose.translation, strict=True):
        tr.set(k, libe57.FloatNode(imf, float(v)))


def write_fixture(
    path: Path,
    # (Pose, lokale XYZ (N,3), invalid-state (N,)) je Scan
    scans: list[tuple[Pose, np.ndarray, np.ndarray]],
    images: list[tuple[FaceName, int]] | None = None,  # (Seite, Scan-Index)
    image_size: int = 64,
    # Data3D-``colorLimits``-Struktur je Scan setzen (0..255, wie an
    # der Pilotkirche real beobachtet) — schaltet PDALs eigene 8-bit->16-bit-Skalierung in
    # readers.e57 frei (empirisch verifiziert, Task-7-Fix-Report Runde 1). Ohne diese Struktur
    # verhält sich die Fixture wie die Pilothalle (kein PDAL-Eigenmechanismus, eigene Skalierung).
    color_limits: bool = False,
    coord_precision: str = "double",
    bounds: bool = False,
    coordinate_metadata: str | None = None,
) -> FixtureInfo:
    imf = libe57.ImageFile(str(path), "w")
    root = imf.root()
    root.set("formatName", libe57.StringNode(imf, "ASTM E57 3D Imaging Data File"))
    root.set("guid", libe57.StringNode(imf, _guid()))
    root.set("versionMajor", libe57.IntegerNode(imf, 1))
    root.set("versionMinor", libe57.IntegerNode(imf, 0))
    if coordinate_metadata is not None:
        root.set("coordinateMetadata", libe57.StringNode(imf, coordinate_metadata))
    data3d = libe57.VectorNode(imf, True)
    root.set("data3D", data3d)
    guids: list[str] = []
    world_valid: list[np.ndarray] = []
    invalid_total = 0
    for i, (pose, local, invalid) in enumerate(scans):
        scan = libe57.StructureNode(imf)
        data3d.append(scan)
        g = _guid()
        guids.append(g)
        scan.set("guid", libe57.StringNode(imf, g))
        scan.set("name", libe57.StringNode(imf, f"Scan {i}"))
        _add_pose(imf, scan, pose)
        if color_limits:
            cl = libe57.StructureNode(imf)
            scan.set("colorLimits", cl)
            for key in ("colorRedM", "colorGreenM", "colorBlueM"):
                cl.set(f"{key}inimum", libe57.IntegerNode(imf, 0))
                cl.set(f"{key}aximum", libe57.IntegerNode(imf, 255))
        if bounds:
            box = libe57.StructureNode(imf)
            scan.set("cartesianBounds", box)
            for axis, values in zip("xyz", local.T, strict=True):
                box.set(f"{axis}Minimum", libe57.FloatNode(imf, float(values.min())))
                box.set(f"{axis}Maximum", libe57.FloatNode(imf, float(values.max())))
        n = len(local)
        proto = libe57.StructureNode(imf)
        for name in ("cartesianX", "cartesianY", "cartesianZ", "intensity"):
            if name.startswith("cartesian") and coord_precision == "scaled":
                proto.set(name, libe57.ScaledIntegerNode(imf, 0, -10**12, 10**12, 0.001, 0.0))
            else:
                precision = (libe57.FloatPrecision.E57_SINGLE
                             if name.startswith("cartesian") and coord_precision == "single"
                             else libe57.FloatPrecision.E57_DOUBLE)
                proto.set(name, libe57.FloatNode(imf, 0.0, precision, -1e7, 1e7))
        for name in ("colorRed", "colorGreen", "colorBlue"):
            proto.set(name, libe57.IntegerNode(imf, 0, 0, 255))
        proto.set("cartesianInvalidState", libe57.IntegerNode(imf, 0, 0, 2))
        codecs = libe57.VectorNode(imf, True)
        points = libe57.CompressedVectorNode(imf, proto, codecs)
        scan.set("points", points)
        rgb = (np.arange(n) % 256).astype(np.int64)
        bufs = {
            "cartesianX": np.ascontiguousarray(local[:, 0], dtype=np.float64),
            "cartesianY": np.ascontiguousarray(local[:, 1], dtype=np.float64),
            "cartesianZ": np.ascontiguousarray(local[:, 2], dtype=np.float64),
            "intensity": np.linspace(0, 1, n),
            "colorRed": rgb.copy(),
            "colorGreen": rgb.copy(),
            "colorBlue": rgb.copy(),
            "cartesianInvalidState": np.ascontiguousarray(invalid, dtype=np.int64),
        }
        # stride explizit setzen (v.itemsize): pye57 0.4.19 nimmt bei stride=0 (Default)
        # für IntegerNode-Puffer eine feste 4-Byte-Elementbreite an, egal welchen dtype
        # der NumPy-Puffer hat. Bei int64-Puffern (8 Byte) verschiebt das Schreiben UND
        # Lesen jeweils um den halben Elementabstand — betrifft beide Richtungen, nicht
        # nur diesen Schreibpfad (siehe Bericht für den vollständig verifizierten
        # Mechanismus). Float64-Felder sind nicht betroffen (FloatNode erkennt die
        # Breite korrekt); stride wird hier trotzdem einheitlich für alle Felder gesetzt.
        sdbufs = libe57.VectorSourceDestBuffer()
        for k, v in bufs.items():
            sdbufs.append(libe57.SourceDestBuffer(imf, k, v, n, True, True, v.itemsize))
        writer = points.writer(sdbufs)
        writer.write(n)
        writer.close()
        ok = invalid == 0
        world_valid.append(apply_pose(local[ok], pose))
        invalid_total += int((~ok).sum())
    if images:
        images2d = libe57.VectorNode(imf, True)
        root.set("images2D", images2d)
        for face, scan_idx in images:
            img = libe57.StructureNode(imf)
            images2d.append(img)
            img.set("guid", libe57.StringNode(imf, _guid()))
            img.set("associatedData3DGuid", libe57.StringNode(imf, guids[scan_idx]))
            # Bildposen stehen im Welt-KS (Probe): Scanrotation ∘ Seitenrotation. Für die
            # Identitäts-Scanrotation ist das bitgleich FACE_ROT[face] (Multiplikation mit 1/0).
            scan_pose = scans[scan_idx][0]
            _add_pose(
                imf, img, Pose(scan_pose.translation, qmul(scan_pose.rotation_wxyz, FACE_ROT[face]))
            )
            buf = io.BytesIO()
            Image.new("RGB", (image_size, image_size), FACE_COLORS[face]).save(
                buf, "JPEG", quality=95
            )
            data = np.frombuffer(buf.getvalue(), dtype=np.uint8)
            rep = libe57.StructureNode(imf)
            img.set("pinholeRepresentation", rep)
            blob = libe57.BlobNode(imf, len(data))
            rep.set("jpegImage", blob)
            blob.write(data, 0, len(data))
            rep.set("imageWidth", libe57.IntegerNode(imf, image_size))
            rep.set("imageHeight", libe57.IntegerNode(imf, image_size))
            rep.set("focalLength", libe57.FloatNode(imf, 0.01))
            rep.set("pixelWidth", libe57.FloatNode(imf, 0.01 * 2 / image_size))
            rep.set("pixelHeight", libe57.FloatNode(imf, 0.01 * 2 / image_size))
            rep.set("principalPointX", libe57.FloatNode(imf, image_size / 2))
            rep.set("principalPointY", libe57.FloatNode(imf, image_size / 2))
    imf.close()
    return FixtureInfo(path, guids, np.vstack(world_valid), invalid_total)
