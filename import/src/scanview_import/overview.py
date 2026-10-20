"""Übersicht: Draufsicht-Raster ``topview.png`` + Georeferenz in ``project.json``
und Bodenhöhe je Standpunkt (``groundZ`` in ``stations.json``) — beides aus der Paketwolke.

Draufsicht: je Pixel die Farbe des **höchsten** Punkts (optional bis ``z_max``, für Innenräume:
Decke ausblenden), ohne Punkt transparent. Pixel (c, r) deckt x ∈ [x0 + c·s, x0 + (c+1)·s] und
y ∈ [y0 - (r+1)·s, y0 - r·s] (``pixelAnchor: "edge"``, Zeile 0 = größtes Y; Norden nur bei
georeferenzierten Paketen). Gelesen werden nur die COPC-Stufen, die für die Pixelgröße nötig
sind (``CopcReader.query(resolution=…)``).

Bodenhöhe: Punkte im Ring 0,5-1,5 m um die Stativachse (direkt darunter verdeckt
das Stativ die Sicht), 0,3-4 m unter dem Scannerzentrum; Median des untersten 10-cm-Bandes über
dem 5-%-Quantil. Unter 50 Punkten → ``None`` (der Viewer zeigt dann den Standpunkt auf
Scannerhöhe; nichts wird geraten).
"""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import numpy.typing as npt
from PIL import Image

from .manifest_io import cloud_of, write_manifest

TOPVIEW_NAME = "topview.png"
DEFAULT_PIXEL_M = 0.05
DEFAULT_MAX_PX = 2048
GROUND_R_MIN_M = 0.5
GROUND_R_MAX_M = 1.5
GROUND_BELOW_M = 0.3
GROUND_DEPTH_M = 4.0
GROUND_MIN_POINTS = 50
GROUND_BAND_M = 0.10
GROUND_RESOLUTION_M = 0.01

F64 = npt.NDArray[np.float64]


@dataclass(frozen=True)
class Grid:
    x0: float  # linke Kante (Welt-X) von Spalte 0
    y0: float  # obere Kante (Welt-Y) von Zeile 0
    pixel_m: float
    width: int
    height: int


def topview_grid(
    bmin: Sequence[float],
    bmax: Sequence[float],
    pixel_m: float = DEFAULT_PIXEL_M,
    max_px: int = DEFAULT_MAX_PX,
) -> Grid:
    ex = float(bmax[0]) - float(bmin[0])
    ey = float(bmax[1]) - float(bmin[1])
    if ex <= 0 or ey <= 0:
        raise ValueError("Bounding-Box ohne Ausdehnung in X/Y — keine Draufsicht möglich")
    px = max(pixel_m, max(ex, ey) / max_px)
    width = min(max_px, max(1, math.ceil(ex / px - 1e-9)))
    height = min(max_px, max(1, math.ceil(ey / px - 1e-9)))
    return Grid(float(bmin[0]), float(bmax[1]), px, width, height)


def rasterize_top(
    xyz: F64, rgb: npt.NDArray[np.uint16] | None, grid: Grid
) -> npt.NDArray[np.uint8]:
    """RGBA (H, W, 4): Farbe des höchsten Punkts je Pixel; ``rgb`` 16 bit (LAS) oder ``None`` →
    Grauwert aus der Höhe (höher = heller)."""
    img = np.zeros((grid.height, grid.width, 4), dtype=np.uint8)
    if len(xyz) == 0:
        return img
    col = np.clip(
        np.floor((xyz[:, 0] - grid.x0) / grid.pixel_m).astype(np.int64), 0, grid.width - 1
    )
    row = np.clip(
        np.floor((grid.y0 - xyz[:, 1]) / grid.pixel_m).astype(np.int64), 0, grid.height - 1
    )
    flat = row * grid.width + col
    order = np.lexsort((xyz[:, 2], flat))  # nach Pixel, darin nach Höhe aufsteigend
    fs = flat[order]
    last = np.r_[np.nonzero(np.diff(fs))[0], len(fs) - 1]  # höchster Punkt je Pixel
    top = order[last]
    if rgb is not None:
        colors = (rgb[top] >> 8).astype(np.uint8)
    else:
        z = xyz[top, 2]
        lo, hi = float(z.min()), float(z.max())
        g = np.zeros(len(z)) if hi <= lo else (z - lo) / (hi - lo)
        colors = np.repeat((40 + 215 * g).astype(np.uint8)[:, None], 3, axis=1)
    ys, xs = np.divmod(fs[last], grid.width)
    img[ys, xs, :3] = colors
    img[ys, xs, 3] = 255
    return img


def ground_z(xyz: F64, station: Sequence[float]) -> float | None:
    sx, sy, sz = (float(v) for v in station)
    r = np.hypot(xyz[:, 0] - sx, xyz[:, 1] - sy)
    dz = sz - xyz[:, 2]
    sel = (
        (r >= GROUND_R_MIN_M)
        & (r <= GROUND_R_MAX_M)
        & (dz >= GROUND_BELOW_M)
        & (dz <= GROUND_DEPTH_M)
    )
    z = xyz[sel, 2]
    if len(z) < GROUND_MIN_POINTS:
        return None
    base = float(np.percentile(z, 5))
    return float(np.median(z[z <= base + GROUND_BAND_M]))


def read_points(
    cloud: Path,
    resolution: float | None,
    bounds: tuple[Sequence[float], Sequence[float]] | None,
) -> tuple[F64, npt.NDArray[np.uint16] | None]:
    """Welt-XYZ (Float64) und RGB (uint16, falls vorhanden). COPC: nur nötige Stufen im Bereich;
    andere LAS/LAZ (Test-Fixtures): ganze Datei, danach im Speicher gefiltert (wie ``verify``)."""
    try:
        reader = laspy.CopcReader.open(cloud)
    except (laspy.LaspyException, IndexError):
        pts: Any = laspy.read(cloud).points
    else:
        with reader:
            b = (
                None
                if bounds is None
                else laspy.Bounds(
                    mins=np.asarray(bounds[0], dtype=np.float64),
                    maxs=np.asarray(bounds[1], dtype=np.float64),
                )
            )
            pts = reader.query(bounds=b, resolution=resolution)
    xyz = np.column_stack(
        [
            np.asarray(pts.x, np.float64),
            np.asarray(pts.y, np.float64),
            np.asarray(pts.z, np.float64),
        ]
    )
    names = set(pts.point_format.dimension_names)
    rgb = (
        np.column_stack([np.asarray(pts.red), np.asarray(pts.green), np.asarray(pts.blue)]).astype(
            np.uint16
        )
        if {"red", "green", "blue"} <= names
        else None
    )
    if bounds is not None:
        lo = np.asarray(bounds[0], dtype=np.float64)
        hi = np.asarray(bounds[1], dtype=np.float64)
        keep = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        xyz = xyz[keep]
        rgb = None if rgb is None else rgb[keep]
    return xyz, rgb


def build_overview(
    pkg: Path,
    pixel_m: float = DEFAULT_PIXEL_M,
    max_px: int = DEFAULT_MAX_PX,
    z_max: float | None = None,
) -> dict[str, Any]:
    manifest_path = pkg / "project.json"
    manifest = json.loads(manifest_path.read_text())
    cloud_layer = cloud_of(manifest)
    cloud = pkg / cloud_layer["url"]
    bmin, bmax = cloud_layer["boundsWorld"]
    grid = topview_grid(bmin, bmax, pixel_m, max_px)
    top = [bmax[0], bmax[1], bmax[2] if z_max is None else min(float(bmax[2]), z_max)]
    xyz, rgb = read_points(cloud, grid.pixel_m, (bmin, top))
    use_rgb = "rgb" in cloud_layer.get("attributes", []) and rgb is not None
    img = rasterize_top(xyz, rgb if use_rgb else None, grid)
    Image.fromarray(img, "RGBA").save(pkg / TOPVIEW_NAME, optimize=True)
    manifest["topview"] = {
        "url": TOPVIEW_NAME,
        "widthPx": grid.width,
        "heightPx": grid.height,
        "pixelSizeM": grid.pixel_m,
        "upperLeftWorld": [grid.x0, grid.y0],
        "pixelAnchor": "edge",
        "zMaxWorld": z_max,
        "colorFrom": "rgb" if use_rgb else "height",
    }
    # Über write_manifest (allow_nan=False) statt direktem json.dumps — nicht-endliche Werte
    # (z. B. ein kaputtes zMaxWorld) sollen laut brechen statt als ungültiges JSON zu landen.
    write_manifest(pkg, manifest)

    ground: dict[str, float | None] = {}
    st_path = pkg / "stations.json"
    if st_path.exists():
        data = json.loads(st_path.read_text())
        for s in data.get("stations", []):
            t = [float(v) for v in s["pose"]["translation"]]
            lo = [t[0] - GROUND_R_MAX_M, t[1] - GROUND_R_MAX_M, t[2] - GROUND_DEPTH_M]
            hi = [t[0] + GROUND_R_MAX_M, t[1] + GROUND_R_MAX_M, t[2] - GROUND_BELOW_M]
            pts, _ = read_points(cloud, GROUND_RESOLUTION_M, (lo, hi))
            s["groundZ"] = ground_z(pts, t)
            ground[str(s["id"])] = s["groundZ"]
        st_path.write_text(json.dumps(data, indent=2))
    return {"topview": manifest["topview"], "groundZ": ground}
