"""Deckungsprüfung der geschriebenen Vollstufe gegen echte Wolkenpunkte."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import laspy
import numpy as np

from .mesh_source import read_mesh
from .mesh_surface import SurfaceIndex

# Teil-Meshes (z. B. ein Gebäude in einer Geländewolke): Kandidaten überzählig ziehen, dann nur die
# im Grundriss des Meshes behalten (Raster ZELLE_M, belegt durch die xy-Rechtecke der Dreiecke).
# Unter MIN_IM_BEREICH Punkten ist die Prüfung nicht aussagekräftig. Voraussetzung: Das Mesh deckt
# alles ab, was die Wolke in seinem Grundriss hat (Region samt Boden, wie Rekonstruktionsregionen).
UEBERZIEHEN = 20
MIN_IM_BEREICH = 1000
ZELLE_M = 1.0

# Standardgrenzen (Wolke und Mesh aus derselben Fläche). Echte Photogrammetrie streut stärker:
# dann ausdrücklich anheben (`add --rahmen-grenze`), vermerkt in frameCheck.limitsM/raised.
# Höchstwerte so, dass echte Rahmenfehler (fehlende Lage ~1 m, Geoid ~40 m, Maßstab ~0,5 m)
# sicher scheitern.
GRENZE_P50_M = 0.005
GRENZE_P95_M = 0.025
GRENZE_P50_MAX_M = 0.05
GRENZE_P95_MAX_M = 0.30


def grundriss(world: np.ndarray, triangles: np.ndarray) -> Callable[[np.ndarray], np.ndarray]:
    """Maske-Funktion: liegt ein Weltpunkt (x, y) in einer Rasterzelle, die das xy-Rechteck eines
    Mesh-Dreiecks berührt? Großzügig an schrägen Kanten (Rechteck statt Dreieck), lückenlos."""
    xy = world[:, :2]
    lo = xy.min(axis=0)
    ecken = np.floor((xy[triangles] - lo) / ZELLE_M).astype(np.int64)  # (M, 3, 2)
    zmin, zmax = ecken.min(axis=1), ecken.max(axis=1)
    breite = int(zmax[:, 1].max()) + 1
    spannen = zmax - zmin + 1
    anzahl = spannen[:, 0] * spannen[:, 1]
    tri = np.repeat(np.arange(len(anzahl)), anzahl)
    rang = np.arange(len(tri)) - np.repeat(np.cumsum(anzahl) - anzahl, anzahl)
    zx = zmin[tri, 0] + rang // spannen[tri, 1]
    zy = zmin[tri, 1] + rang % spannen[tri, 1]
    belegt = np.unique(zx * breite + zy)

    def innen(punkte: np.ndarray) -> np.ndarray:
        z = np.floor((punkte[:, :2] - lo) / ZELLE_M).astype(np.int64)
        gueltig = (z >= 0).all(axis=1) & (z[:, 1] < breite)
        schluessel = np.where(gueltig, z[:, 0] * breite + z[:, 1], -1)
        maske: np.ndarray = gueltig & np.isin(schluessel, belegt)
        return maske

    return innen


def sample_cloud(
    path: Path,
    count: int = 100000,
    innen: Callable[[np.ndarray], np.ndarray] | None = None,
    ohne_klassen: tuple[int, ...] = (),
) -> np.ndarray:
    """Zufällige Wolkenpunkte (Seed 42); mit ``innen`` nur Punkte, für die die Maske wahr ist, ohne
    Punkte der Klassen ``ohne_klassen`` (z. B. Vegetation, wenn sie aus dem Mesh entfernt wurde)."""
    filtern = innen is not None or bool(ohne_klassen)
    with laspy.open(path) as reader:
        size = int(reader.header.point_count)
        if size <= 0:
            raise ValueError("frameCheck: leere Punktwolke")
        ziehen = count * UEBERZIEHEN if filtern else count
        selected = np.sort(np.random.default_rng(42).choice(size, min(ziehen, size), replace=False))
        result = np.empty((len(selected), 3), dtype=np.float64)
        klassen = np.zeros(len(selected), dtype=np.int64)
        offset = 0
        for chunk in reader.chunk_iterator(1000000):
            begin = np.searchsorted(selected, offset)
            end = np.searchsorted(selected, offset + len(chunk))
            if end > begin:
                ix = selected[begin:end] - offset
                result[begin:end] = np.column_stack([chunk.x[ix], chunk.y[ix], chunk.z[ix]])
                if ohne_klassen:
                    klassen[begin:end] = np.asarray(chunk.classification)[ix]
            offset += len(chunk)
        if offset != size or not np.isfinite(result).all():
            raise ValueError("frameCheck: unvollständige oder nicht-endliche Punktwolke")
    if not filtern:
        return result
    maske = ~np.isin(klassen, ohne_klassen) if ohne_klassen else np.ones(len(result), dtype=bool)
    if innen is not None:
        maske &= innen(result)
    inside = result[maske]
    if len(inside) < min(MIN_IM_BEREICH, len(result)):
        raise ValueError(
            f"frameCheck: nur {len(inside)} Wolkenpunkte im Bereich des Meshes "
            "— Mesh liegt neben der Wolke"
        )
    keep = np.sort(np.random.default_rng(43).permutation(len(inside))[:count])
    auswahl: np.ndarray = inside[keep]
    return auswahl


def grenzen_pruefen(grenzen: tuple[float, float] | None) -> tuple[float, float]:
    """(p50, p95) in Metern; ohne Angabe die Standardgrenzen."""
    if grenzen is None:
        return GRENZE_P50_M, GRENZE_P95_M
    p50, p95 = (float(g) for g in grenzen)
    if not (0 < p50 <= GRENZE_P50_MAX_M and 0 < p95 <= GRENZE_P95_MAX_M and p50 <= p95):
        raise ValueError(
            f"Rahmengrenze muss 0 < p50 ≤ {GRENZE_P50_MAX_M * 1000:g} mm, 0 < p95 ≤ "
            f"{GRENZE_P95_MAX_M * 1000:g} mm und p50 ≤ p95 sein"
        )
    return p50, p95


def frame_check(
    pkg: Path,
    cloud: dict[str, Any],
    model: Path,
    origin: np.ndarray,
    grenzen: tuple[float, float] | None = None,
    ohne_klassen: tuple[int, ...] = (),
) -> dict[str, Any]:
    g50, g95 = grenzen_pruefen(grenzen)
    if any(type(k) is not int or not 0 <= k <= 255 for k in ohne_klassen):
        raise ValueError("Ausgeschlossene Klassen müssen ganze Zahlen 0 bis 255 sein")
    path = (pkg / cloud["url"]).resolve()
    if not path.is_relative_to(pkg.resolve()):
        raise ValueError("frameCheck: Wolkenpfad außerhalb des Pakets")
    mesh = read_mesh(model)
    world = mesh.positions + origin
    points = sample_cloud(
        path, innen=grundriss(world, mesh.triangles), ohne_klassen=tuple(sorted(ohne_klassen))
    )
    # Beide lokal, Float64: große Weltkoordinaten beeinträchtigen keine Abstandsmessung.
    distances = SurfaceIndex(mesh.positions, mesh.triangles).distance(points - origin)
    p50, p95 = np.quantile(distances, [0.5, 0.95])
    return {
        "againstLayer": cloud["id"],
        "samples": len(points),
        "requestedSamples": 100000,
        "seed": 42,
        "within": f"meshFootprint{ZELLE_M:g}m",
        "excludedClasses": sorted(ohne_klassen),
        "p50M": float(p50),
        "p95M": float(p95),
        "limitsM": {"p50": g50, "p95": g95},
        "defaultLimitsM": {"p50": GRENZE_P50_M, "p95": GRENZE_P95_M},
        "raised": (g50, g95) != (GRENZE_P50_M, GRENZE_P95_M),
        "ok": bool(p50 <= g50 and p95 <= g95),
    }
