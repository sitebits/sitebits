"""Richtungen und Orte: Gitter (Paket-CRS) ↔ geografisch, PVGIS ↔ pvlib.

Konventionen:
- pvlib/geografisch: Azimut 0 = Nord, 90 = Ost, im Uhrzeigersinn.
- PVGIS `aspect` und `printhorizon` `A`: 0 = Süd, 90 = West, -90 = Ost.
- Gitter: Azimut ab Gitter-Nord (Paket-Y) im Uhrzeigersinn. `konvergenz_grad` ist die Richtung von
  geografisch Nord im Gitter (Potsdam, UTM 33: ≈ +1,56°), gleiche Finite-Differenz wie der Viewer.
"""

from __future__ import annotations

import math

import numpy as np
from pyproj import CRS, Transformer


def _wrap(a: np.ndarray | float) -> np.ndarray | float:
    return np.mod(a, 360.0)


def pvgis_nach_pvlib(a: float) -> float:
    return float(_wrap(a + 180.0))


def pvlib_nach_pvgis(a: float) -> float:
    """pvlib-Azimut → PVGIS-Azimut im Bereich [-180, 180)."""
    return float(np.mod(a, 360.0) - 180.0)


def geo_nach_gitter(azimut_geo: np.ndarray | float, konvergenz: float) -> np.ndarray | float:
    return _wrap(np.asarray(azimut_geo) + konvergenz)


def gitter_nach_geo(azimut_gitter: np.ndarray | float, konvergenz: float) -> np.ndarray | float:
    return _wrap(np.asarray(azimut_gitter) - konvergenz)


def lage_geo(crs: str, ost: float, nord: float) -> tuple[float, float]:
    """Gitterkoordinate → (Breite, Länge) in Grad (WGS84/ETRS89)."""
    t = Transformer.from_crs(CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True)
    lon, lat = t.transform(ost, nord)
    return float(lat), float(lon)


def konvergenz_grad(crs: str, breite: float, laenge: float) -> float:
    """Richtung von geografisch Nord im Gitter, Grad im Uhrzeigersinn ab Gitter-Nord."""
    t = Transformer.from_crs(CRS.from_epsg(4326), CRS.from_user_input(crs), always_xy=True)
    a = t.transform(laenge, breite)
    b = t.transform(laenge, breite + 0.001)
    return math.degrees(math.atan2(b[0] - a[0], b[1] - a[1]))


def richtung(azimut_gitter: np.ndarray, hoehe: np.ndarray) -> np.ndarray:
    """Einheitsvektoren (X = Gitter-Ost, Y = Gitter-Nord, Z = oben) aus Azimut/Höhe in Grad."""
    a = np.radians(azimut_gitter)
    h = np.radians(hoehe)
    return np.stack([np.sin(a) * np.cos(h), np.cos(a) * np.cos(h), np.sin(h)], axis=-1)


def neigung_azimut(normale: np.ndarray) -> tuple[float, float]:
    """Flächennormale (Gitter) → (Neigung, Azimut im Gitter) °; lotrecht: Azimut 180."""
    n = normale / np.linalg.norm(normale)
    neigung = math.degrees(math.acos(max(-1.0, min(1.0, float(n[2])))))
    if math.hypot(n[0], n[1]) < 1e-9:
        return neigung, 180.0
    return neigung, float(_wrap(math.degrees(math.atan2(n[0], n[1]))))
