"""COLMAP images.txt/.bin ↔ benannte Weltzentren; kein Namensraten."""

from __future__ import annotations

import csv
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, BinaryIO

import numpy as np
from scipy.spatial.transform import Rotation


def _insert(out: dict[str, np.ndarray], label: str, center: np.ndarray) -> None:
    if not label or label in out or center.shape != (3,) or not np.isfinite(center).all():
        raise ValueError("Kamerazentrum/Label ungültig oder doppelt")
    out[label] = center


def _center(q: list[float], t: list[float]) -> np.ndarray:
    quaternion = np.array(q)[[1, 2, 3, 0]]
    if not np.isfinite(quaternion).all() or abs(np.linalg.norm(quaternion) - 1) > 1e-5:
        raise ValueError("COLMAP-Quaternion ungültig")
    return np.asarray(-Rotation.from_quat(quaternion).as_matrix().T @ np.asarray(t))


def colmap_centers(path: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    if path.suffix == ".txt":
        with path.open() as f:
            while line := f.readline():
                if line.startswith("#") or not line.strip():
                    continue
                fields = line.strip().split(maxsplit=9)
                if len(fields) != 10:
                    raise ValueError("COLMAP images.txt: Bildzeile ungültig")
                _insert(
                    out,
                    fields[9],
                    _center([float(x) for x in fields[1:5]], [float(x) for x in fields[5:8]]),
                )
                if f.readline() == "":
                    raise ValueError("COLMAP images.txt: Beobachtungszeile fehlt")
    elif path.suffix == ".bin":

        def unpack(f: BinaryIO, fmt: str) -> tuple[Any, ...]:
            data = f.read(struct.calcsize(fmt))
            if len(data) != struct.calcsize(fmt):
                raise ValueError("COLMAP images.bin abgeschnitten")
            return struct.unpack(fmt, data)

        with path.open("rb") as binary:
            n = int(unpack(binary, "<Q")[0])
            if n > 100000:
                raise ValueError("COLMAP: zu viele Kameras")
            for _ in range(n):
                row = unpack(binary, "<i7di")
                name = bytearray()
                while (b := binary.read(1)) != b"\0":
                    if not b or len(name) > 4096:
                        raise ValueError("COLMAP: Bildname ungültig")
                    name.extend(b)
                _insert(out, name.decode("utf-8"), _center(list(row[1:5]), list(row[5:8])))
                count = int(unpack(binary, "<Q")[0])
                if count > 10000000 or len(binary.read(count * 24)) != count * 24:
                    raise ValueError("COLMAP: Beobachtungen ungültig")
            if binary.read(1):
                raise ValueError("COLMAP: zusätzliche Daten")
    else:
        raise ValueError("COLMAP: images.txt oder images.bin angeben")
    return out


def world_centers(path: Path) -> dict[str, np.ndarray]:
    out: dict[str, np.ndarray] = {}
    if path.suffix.lower() == ".csv":
        with path.open(newline="") as f:
            for row in csv.DictReader(f):
                _insert(out, row["label"], np.array([float(row[k]) for k in ("E", "N", "H")]))
    else:
        root = ET.parse(path).getroot()
        chunks = root.findall(".//chunk")
        if len(chunks) != 1:
            raise ValueError("Metashape: genau ein Chunk erforderlich")
        chunk = chunks[0]
        # Geozentrische/projected CRS-Umrechnung darf nicht still geraten werden.
        if chunk.find("reference") is not None and (chunk.findtext("reference") or "").strip():
            raise ValueError(
                "Metashape: georeferenziertes XML bitte als label,E,N,H im Paketrahmen exportieren"
            )
        rtext = chunk.findtext("transform/rotation")
        ttext = chunk.findtext("transform/translation")
        scale = float(chunk.findtext("transform/scale", "1"))
        r = np.eye(3) if rtext is None else np.fromstring(rtext, sep=" ").reshape(3, 3)
        t = np.zeros(3) if ttext is None else np.fromstring(ttext, sep=" ")
        if not np.isfinite(scale) or scale <= 0 or not np.allclose(r.T @ r, np.eye(3)):
            raise ValueError("Metashape: Chunk-Transformation ungültig")
        for camera in chunk.findall(".//camera"):
            text = camera.findtext("transform")
            if text is None:
                continue
            m = np.fromstring(text, sep=" ").reshape(4, 4)
            _insert(out, camera.get("label", ""), scale * r @ m[:3, 3] + t)
    return out


def camera_pairs(local_path: Path, world_path: Path) -> tuple[np.ndarray, np.ndarray, list[str]]:
    local, world = colmap_centers(local_path), world_centers(world_path)
    labels = sorted(local.keys() & world.keys())
    if len(labels) < 4:
        raise ValueError("Mindestens vier exakt gleich benannte Kameras erforderlich")
    return np.array([local[k] for k in labels]), np.array([world[k] for k in labels]), labels
