"""Exakte PDAL-Ausdünnung mit globalem Raster und begrenzten Kachel-Puffern.

PDAL 2.10.2 verankert jede View an ihrem Minimum. Ein künstlicher, anschließend
entfernter Ursprungspunkt hält dieses Minimum fest. Sein Voxel wird separat mit
PDALs striktem Distanzvergleich reduziert (bei Gleichstand gewinnt der erste Punkt).
Die Partition arbeitet auf ganzzahligen Voxelindizes, niemals gerundeten Crop-Grenzen.
"""

from __future__ import annotations

import math
import resource
import tempfile
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast

import laspy
import numpy as np

from .pdal_runner import RunStats, combine_stats, run_pipeline

RAW_DIMS = ("SVRawX", "SVRawY", "SVRawZ")
CHUNK = 250_000
MAX_TILE_POINTS = 1_000_000


def _chunks(path: Path, dtype: np.dtype) -> Iterator[np.ndarray]:
    with path.open("rb") as f:
        while len(a := np.fromfile(f, dtype=dtype, count=CHUNK)):
            yield a


def _xyz(a: np.ndarray, header: Any) -> np.ndarray:
    if all(d in (a.dtype.names or ()) for d in RAW_DIMS):
        return np.column_stack([a[d] for d in RAW_DIMS])
    return cast(np.ndarray, np.column_stack([a[d] for d in "XYZ"]) * header.scales + header.offsets)


def _indices(xyz: np.ndarray, origin: np.ndarray, cell: float) -> np.ndarray:
    values = (xyz - origin) / cell
    if not np.isfinite(values).all() or (values >= np.iinfo(np.int64).max).any():
        raise ValueError("Ausdünnung: Zellraster überschreitet den unterstützten Indexbereich")
    # PDAL: static_cast<size_t> bei nichtnegativen Werten = Abschneiden nach unten.
    return cast(np.ndarray, values.astype(np.int64))


def _winner(
    a: np.ndarray, xyz: np.ndarray, indices: np.ndarray, origin: np.ndarray, cell: float
) -> tuple[np.ndarray, float]:
    centers = origin + (indices + 0.5) * cell
    delta = centers - xyz
    # Gleiche Auswertungsreihenfolge wie pow(dx, 2) + pow(dy, 2) + pow(dz, 2).
    dist = delta[:, 0] ** 2 + delta[:, 1] ** 2 + delta[:, 2] ** 2
    i = int(np.argmin(dist))
    return a[i : i + 1].copy(), float(dist[i])


def thin_tiles(src: Path, dst: Path, cell: float, max_points: int = MAX_TILE_POINTS) -> RunStats:
    """LAZ → LAZ; höchstens max_points + 1 Punkte je PDAL-View.

    Rohkoordinaten-Extras bewahren die Auswahl vor LAS-Quantisierung für PLY/Text.
    Räumliche Binärteilung an Voxelgrenzen reagiert auch auf Dichte-Hotspots.
    Ein beliebig dichtes einzelnes Voxel wird in konstant großen Chunks reduziert.
    Temporäre Binärdateien brauchen zusätzlichen Plattenplatz, keine vollständige View.
    """
    if not math.isfinite(cell) or cell <= 0 or max_points <= 0:
        raise ValueError("Ausdünnung: Zellgröße und Kachelpunktgrenze müssen positiv sein")
    start = time.monotonic()
    stats: list[RunStats] = []
    with tempfile.TemporaryDirectory(prefix="_tiles-", dir=dst.parent) as td:
        work = Path(td)
        root = work / "root.bin"
        with laspy.open(src) as reader, root.open("wb") as f:
            header = reader.header.copy()
            dtype = reader.header.point_format.dtype()
            origin = np.full(3, np.inf)
            for pts in reader.chunk_iterator(CHUNK):
                xyz = _xyz(pts.array, header)
                if not np.isfinite(xyz).all():
                    raise ValueError("Ausdünnung: nicht endliche Quellkoordinaten")
                origin = np.minimum(origin, xyz.min(axis=0))
                pts.array.tofile(f)
        output_header = header.copy()
        extras = [d for d in RAW_DIMS if d in output_header.point_format.extra_dimension_names]
        if extras:
            output_header.remove_extra_dims(extras)
        output_header.point_count = 0

        def emit(writer: Any, a: np.ndarray) -> None:
            points = laspy.ScaleAwarePointRecord.zeros(len(a), header=output_header)
            for name in points.array.dtype.names or ():
                points.array[name] = a[name]
            writer.write_points(points)

        with laspy.open(dst, mode="w", header=output_header) as writer:
            pending = [root]
            serial = 0
            while pending:
                path = pending.pop()
                count = path.stat().st_size // dtype.itemsize
                if not count:
                    path.unlink()
                    continue
                lo = np.full(3, np.iinfo(np.int64).max, dtype=np.int64)
                hi = np.zeros(3, dtype=np.int64)
                for a in _chunks(path, dtype):
                    ix = _indices(_xyz(a, header), origin, cell)
                    lo = np.minimum(lo, ix.min(axis=0))
                    hi = np.maximum(hi, ix.max(axis=0))
                if np.array_equal(lo, hi):
                    # Auch Milliarden Duplikate in einem Voxel wachsen nicht im RAM.
                    best, distance = None, math.inf
                    for a in _chunks(path, dtype):
                        xyz = _xyz(a, header)
                        candidate, d = _winner(a, xyz, _indices(xyz, origin, cell), origin, cell)
                        if best is None or d < distance:
                            best, distance = candidate, d
                    if best is None:
                        raise RuntimeError("Ausdünnung: Kachel enthält keine lesbaren Punkte")
                    emit(writer, best)
                    path.unlink()
                    continue
                if count > max_points:
                    axis = int(np.argmax(hi - lo))
                    boundary = int(lo[axis]) + (int(hi[axis]) - int(lo[axis])) // 2 + 1
                    children = [work / f"tile-{serial + j}.bin" for j in range(2)]
                    serial += 2
                    with children[0].open("wb") as left, children[1].open("wb") as right:
                        for a in _chunks(path, dtype):
                            keep = _indices(_xyz(a, header), origin, cell)[:, axis] < boundary
                            a[keep].tofile(left)
                            a[~keep].tofile(right)
                    path.unlink()
                    pending.extend(reversed(children))
                    continue
                a = np.fromfile(path, dtype=dtype)
                xyz = _xyz(a, header)
                ix = _indices(xyz, origin, cell)
                zero = (ix == 0).all(axis=1)
                if zero.any():
                    best, _ = _winner(a[zero], xyz[zero], ix[zero], origin, cell)
                    emit(writer, best)
                a, xyz = a[~zero], xyz[~zero]
                if len(a):
                    tile_header = header.copy()
                    for name in RAW_DIMS:
                        if name not in tile_header.point_format.extra_dimension_names:
                            tile_header.add_extra_dim(laspy.ExtraBytesParams(name=name, type="f8"))
                    tile_header.add_extra_dim(laspy.ExtraBytesParams(name="SVAnchor", type="u1"))
                    pts = laspy.ScaleAwarePointRecord.zeros(len(a) + 1, header=tile_header)
                    for name in a.dtype.names or ():
                        pts.array[name][:-1] = a[name]
                    for axis, name in enumerate(RAW_DIMS):
                        pts[name] = np.append(xyz[:, axis], origin[axis])
                    pts["SVAnchor"][-1] = 1
                    with laspy.open(work / "input.las", mode="w", header=tile_header) as tile:
                        tile.write_points(pts)
                    pipe: list[dict[str, Any]] = [
                        {"type": "readers.las", "filename": "/tiles/input.las"},
                        {
                            "type": "filters.ferry",
                            "dimensions": ",".join(
                                f"{d}=>{axis}" for d, axis in zip(RAW_DIMS, "XYZ", strict=True)
                            ),
                        },
                        {"type": "filters.voxelcenternearestneighbor", "cell": cell},
                        {"type": "filters.expression", "expression": "SVAnchor == 0"},
                        {
                            "type": "writers.las",
                            "filename": "/tiles/output.las",
                            "minor_version": 4,
                            "dataformat_id": 7,
                            "forward": "all",
                            **{
                                f"scale_{axis}": float(header.scales[j])
                                for j, axis in enumerate("xyz")
                            },
                            **{
                                f"offset_{axis}": float(header.offsets[j])
                                for j, axis in enumerate("xyz")
                            },
                        },
                    ]
                    result = run_pipeline(pipe, {work: "/tiles"})
                    stats.append(result)
                    if result.returncode:
                        raise RuntimeError(f"Kachelausdünnung fehlgeschlagen: {result.stderr_tail}")
                    with laspy.open(work / "output.las") as result_reader:
                        for points in result_reader.chunk_iterator(CHUNK):
                            emit(writer, points.array)
                path.unlink()
    combined = combine_stats(stats)
    # Host-Peak ist die bisherige Prozess-Hochwassermarke, keine Momentaufnahme.
    return RunStats(
        time.monotonic() - start,
        max(combined.peak_rss_mb, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        0,
        "",
    )
