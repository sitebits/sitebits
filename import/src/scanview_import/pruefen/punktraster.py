"""Ein Lesedurchgang über die Punktwolken: Punkte je Zelle, Bodenhöhe, tiefster Punkt.

Grundlage für Dichte, Lücken und Bodenauflösung. Je Zelle gilt das **Maximum** über die Dateien,
nicht die Summe: Liegt dieselbe Wolke zweimal in der Lieferung (Kacheln plus Gesamtdatei, LAZ
plus COPC), verdoppelt sich so nicht die Dichte. Bei überschneidungsfreien Kacheln ist das gleich
der Summe; an Kachelnähten zählt es konservativ.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import laspy
import numpy as np
from pyproj import CRS

from .flaeche import Auftragsflaeche, FlaechenFehler
from .lieferung import LESEFEHLER, Lieferung, kopf_las

MAX_ZELLEN = 50_000_000
BLOCK = 5_000_000
BODEN = 2


@dataclass
class Punktraster:
    x0: float  # linke untere Ecke des Rasters
    y0: float
    zelle: float
    anzahl: np.ndarray  # (ny, nx) Punkte je Zelle, Maximum über die Dateien
    boden_z: np.ndarray  # tiefster Punkt der Klasse 2 je Zelle, NaN ohne
    min_z: np.ndarray  # tiefster Punkt je Zelle, NaN ohne
    innen: np.ndarray  # Zellmittelpunkt in der Auftragsfläche
    crs: CRS
    flaeche: Auftragsflaeche  # im Raster-CRS
    dateien: tuple[str, ...]

    @property
    def hat_boden(self) -> bool:
        return bool(np.isfinite(self.boden_z).any())

    def zelle_von(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        ix = np.floor((x - self.x0) / self.zelle).astype(np.int64)
        iy = np.floor((y - self.y0) / self.zelle).astype(np.int64)
        ny, nx = self.anzahl.shape
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        return ix, iy, ok

    def bodenhoehe(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Bodenhöhe (Klasse 2, sonst tiefster Punkt) an Weltpunkten, NaN außerhalb/ohne Punkte."""
        grund = self.boden_z if self.hat_boden else self.min_z
        ix, iy, ok = self.zelle_von(np.atleast_1d(x), np.atleast_1d(y))
        out = np.full(ix.shape, np.nan)
        out[ok] = grund[iy[ok], ix[ok]]
        return out


def gemeinsames_crs(lf: Lieferung, dateien: list[str]) -> CRS | str:
    """Horizontales CRS aller Punktdateien oder ein Mangeltext."""
    gefunden: dict[str, CRS] = {}
    for rel in dateien:
        try:
            crs = kopf_las(lf.pfad(rel))["crs"]
        except LESEFEHLER as e:
            return f"{rel}: nicht lesbar ({type(e).__name__})"
        if crs is None:
            return f"{rel}: kein Lagebezug, Fläche nicht zuordenbar"
        gefunden[rel] = crs.sub_crs_list[0] if crs.is_compound else crs
    erstes = next(iter(gefunden.values()))
    for rel, c in gefunden.items():
        if not c.equals(erstes, ignore_axis_order=True):
            return f"{rel}: {c.name} weicht von {erstes.name} ab, Fläche nicht eindeutig zuordenbar"
    return erstes


def rastern(
    lf: Lieferung,
    dateien: list[str],
    flaeche: Auftragsflaeche,
    zelle: float,
    klassen: tuple[int, ...] | None = None,
) -> Punktraster | str:
    """Raster über die Auftragsfläche (zwischengespeichert je Dateien, Zelle, Klassen)."""
    schluessel = ("punktraster", tuple(dateien), zelle, klassen)
    if schluessel in lf.cache:
        return lf.cache[schluessel]  # type: ignore[no-any-return]
    crs = gemeinsames_crs(lf, dateien)
    if isinstance(crs, str):
        return crs
    fl = flaeche.in_crs(crs)
    minx, miny, maxx, maxy = fl.grenzen()
    x0, y0 = math.floor(minx / zelle) * zelle, math.floor(miny / zelle) * zelle
    nx, ny = math.ceil((maxx - x0) / zelle), math.ceil((maxy - y0) / zelle)
    if nx * ny > MAX_ZELLEN:
        raise FlaechenFehler(f"Raster zu groß ({nx * ny} Zellen); größere Zelle wählen")
    mx, my = np.meshgrid(x0 + (np.arange(nx) + 0.5) * zelle, y0 + (np.arange(ny) + 0.5) * zelle)
    anzahl = np.zeros(ny * nx, np.int64)
    boden = np.full(ny * nx, np.inf)
    tief = np.full(ny * nx, np.inf)
    for rel in dateien:
        diese = np.zeros(ny * nx, np.int64)
        with laspy.open(lf.pfad(rel), laz_backend=laspy.LazBackend.LazrsParallel) as f:
            for block in f.chunk_iterator(BLOCK):
                x, y = np.asarray(block.x), np.asarray(block.y)
                ix = np.floor((x - x0) / zelle).astype(np.int64)
                iy = np.floor((y - y0) / zelle).astype(np.int64)
                ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
                idx = iy[ok] * nx + ix[ok]
                z = np.asarray(block.z)[ok]
                k = np.asarray(block.classification)[ok]
                np.minimum.at(tief, idx, z)
                b = k == BODEN
                np.minimum.at(boden, idx[b], z[b])
                if klassen is not None:
                    idx = idx[np.isin(k, klassen)]
                diese += np.bincount(idx, minlength=ny * nx)
        anzahl = np.maximum(anzahl, diese)

    def nan(a: np.ndarray) -> np.ndarray:
        return np.where(np.isfinite(a), a, np.nan).reshape(ny, nx)

    r = Punktraster(
        x0=x0,
        y0=y0,
        zelle=zelle,
        anzahl=anzahl.reshape(ny, nx),
        boden_z=nan(boden),
        min_z=nan(tief),
        innen=fl.innen(mx, my),
        crs=crs,
        flaeche=fl,
        dateien=tuple(dateien),
    )
    lf.cache[schluessel] = r
    return r
