"""Quellkoordinaten unabhängig von PDAL lesen, chunkweise für Inventar und Prüfung."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from itertools import islice
from typing import Any

import laspy
import numpy as np

from .point_source import PointSource

CHUNK = 250_000


def point_chunks(source: PointSource, limit: int | None = None) -> Iterator[np.ndarray]:
    remaining = limit
    if source.reader == "readers.las":
        with laspy.open(source.path) as reader:
            for rec in reader.chunk_iterator(CHUNK):
                n = len(rec) if remaining is None else min(len(rec), remaining)
                yield source.transform(np.column_stack([rec.x[:n], rec.y[:n], rec.z[:n]]))
                if remaining is not None:
                    remaining -= n
                    if remaining == 0:
                        return
        return
    ph = source.ply
    if ph is not None and ph.encoding != "ascii":
        records = np.memmap(
            source.path, dtype=ph.dtype(), mode="r", offset=ph.offset, shape=(ph.count,)
        )
        n = ph.count if limit is None else min(ph.count, limit)
        for start in range(0, n, CHUNK):
            rec = records[start : min(start + CHUNK, n)]
            yield source.transform(np.column_stack([rec["x"], rec["y"], rec["z"]]))
        return
    if ph is not None:
        names = [p[0] for p in ph.properties]
        cols = [names.index(c) for c in ("x", "y", "z")]
        field_count = len(names)
        declared = ph.count
        skip = None
    elif source.format == "pts":
        cols, field_count, skip = [0, 1, 2], 7, 1
        with source.path.open() as f:
            declared = int(f.readline().strip())
        if declared < 0:
            raise ValueError("PTS: negative Punktzahl")
    else:
        cols = [source.columns.index(c) for c in ("X", "Y", "Z")]
        field_count, declared, skip = len(source.columns), None, source.text_skip
    got = 0
    with source.path.open() as f:
        if ph is not None:
            f.seek(ph.offset)
        else:
            for _ in range(skip or 0):
                next(f)
        while True:
            lines = list(islice(f, CHUNK if remaining is None else min(CHUNK, remaining)))
            if not lines:
                break
            # PDAL und die unabhängige Prüfung müssen dieselben Zeilen akzeptieren.
            if any(not line.strip() for line in lines):
                raise ValueError("Leere Zeilen in der Punktquelle sind nicht zulässig")
            delimiter: str | None = source.separator if source.reader == "readers.text" else " "
            delimiter = None if delimiter in (" ", "\t") else delimiter
            try:
                rows = np.loadtxt(lines, delimiter=delimiter, ndmin=2)
            except ValueError as exc:
                raise ValueError("Ungültige Zahlen/Spalten in der Punktquelle") from exc
            if rows.shape[1] != field_count:
                raise ValueError(f"Punktquelle benötigt {field_count} Spalten")
            got += len(rows)
            yield source.transform(rows[:, cols])
            if remaining is not None:
                remaining -= len(rows)
                if remaining == 0:
                    return
    if declared is not None and got != declared:
        raise ValueError(f"Quellpunktzahl {got} widerspricht Header {declared}")


@dataclass(frozen=True)
class SourceStats:
    count: int
    bounds: tuple[list[float], list[float]]
    attributes: list[str]
    rgb_8bit: bool

    def as_json(self) -> dict[str, Any]:
        return {
            "pointCount": self.count,
            "boundsWorld": self.bounds,
            "attributes": self.attributes,
            "rgb8bit": self.rgb_8bit,
        }


def inspect_points(source: PointSource, limit: int | None = None) -> SourceStats:
    attrs: list[str] = []
    rgb_8bit = False
    if source.reader == "readers.las":
        with laspy.open(source.path) as reader:
            h = reader.header
            n = int(h.point_count) if limit is None else min(int(h.point_count), limit)
            dims = set(h.point_format.dimension_names)
            attrs = [a for a, d in [("rgb", "red"), ("intensity", "intensity")] if d in dims]
            # Alle 8 Ecken bei Achsendrehung, kein Ableiten aus Zahlenwerten.
            if limit is None:
                corners = np.array(
                    [
                        [(h.mins, h.maxs)[i][0], (h.mins, h.maxs)[j][1], (h.mins, h.maxs)[k][2]]
                        for i in (0, 1)
                        for j in (0, 1)
                        for k in (0, 1)
                    ]
                )
                world = source.transform(corners)
                if n <= 0 or not np.all(np.isfinite(world)):
                    raise ValueError("Punktquelle ist leer oder hat ungültige Bounds")
                return SourceStats(n, (world.min(0).tolist(), world.max(0).tolist()), attrs, False)
    elif source.ply is not None:
        props = dict(source.ply.properties)
        if {"red", "green", "blue"} <= props.keys():
            attrs.append("rgb")
            rgb_8bit = all(props[c] in ("uchar", "uint8") for c in ("red", "green", "blue"))
        if "intensity" in props:
            attrs.append("intensity")
    elif source.format == "pts":
        attrs = ["rgb", "intensity"]
        rgb_8bit = True  # PTS-RGB ist 8 bit; PDAL skaliert es nicht selbst.
    else:
        if {"Red", "Green", "Blue"} <= set(source.columns):
            attrs.append("rgb")
        if "Intensity" in source.columns:
            attrs.append("intensity")
    lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
    count = 0
    for xyz in point_chunks(source, limit):
        if not np.all(np.isfinite(xyz)):
            raise ValueError("Punktquelle enthält nicht-endliche Koordinaten")
        if len(xyz):
            lo, hi = np.minimum(lo, xyz.min(0)), np.maximum(hi, xyz.max(0))
            count += len(xyz)
    if not count:
        raise ValueError("Punktquelle enthält keine Punkte")
    return SourceStats(count, (lo.tolist(), hi.tolist()), attrs, rgb_8bit)


def e57_chunks(
    path: Any, limit: int | None = None, *, float32_zulassen: bool = False,
) -> Iterator[tuple[np.ndarray, int]]:
    """E57 für Rückwärtsprüfung bei Ausdünnung; gültige Punkte + Rohzahl je Chunk."""
    import pye57
    from pye57 import libe57

    from .geometry import apply_pose
    from .inventory import pruefe_e57_genauigkeit, read_inventory

    inv = read_inventory(path)
    pruefe_e57_genauigkeit(inv, float32_zulassen)
    e = pye57.E57(str(path))
    left = limit
    try:
        imf = e.image_file
        nodes = imf.root()["data3D"]
        for scan in inv.scans:
            if left == 0:
                break
            node = nodes[scan.index]["points"]
            n = min(CHUNK, scan.point_count)
            if n == 0:
                continue
            names = ["cartesianX", "cartesianY", "cartesianZ"]
            if "cartesianInvalidState" in scan.fields:
                names.append("cartesianInvalidState")
            buffers = {
                name: np.zeros(n, dtype=np.int64 if name == "cartesianInvalidState" else np.float64)
                for name in names
            }
            sdbufs = libe57.VectorSourceDestBuffer()
            for name, arr in buffers.items():
                sdbufs.append(libe57.SourceDestBuffer(imf, name, arr, n, True, True, arr.itemsize))
            reader = node.reader(sdbufs)
            try:
                while got := int(reader.read()):
                    raw = got if left is None else min(got, left)
                    xyz = np.column_stack([buffers[name][:raw] for name in names[:3]])
                    if "cartesianInvalidState" in buffers:
                        xyz = xyz[buffers["cartesianInvalidState"][:raw] == 0]
                    yield apply_pose(xyz, scan.pose), raw
                    if left is not None:
                        left -= raw
                        if left == 0:
                            break
            finally:
                reader.close()
    finally:
        e.close()
