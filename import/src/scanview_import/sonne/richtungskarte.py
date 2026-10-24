"""Richtungskarte je Stichpunkt: Verdeckung über Azimut (Gitter) x Höhe, obere Hemisphäre, einmal
per Raycast.

Daraus ohne neue Strahlen: Direktmaske für jeden Sonnenstand (bilinear zwischen Zellmitten),
Sky-View-Faktor
kosinusgewichtet zur Flächennormalen und das Horizontband für die Perez-Horizontaufhellung.
Richtungen hinter
der Fläche (n·d ≤ 0) werden nicht geschossen: Dort ist der Einfallswinkel > 90°, die Direktstrahlung
ohnehin 0.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np

from .geo import richtung
from .strahlen import Szene

HORIZONTBAND_GRAD = 6.0  # Breite des Perez-Horizontbands (Annahme, Perez et al. 1990 ≈ 6,5°)


@dataclass(frozen=True)
class Raster:
    az_schritt: float = 2.0
    h_schritt: float = 1.0

    @property
    def azimute(self) -> np.ndarray:
        return np.arange(self.az_schritt / 2, 360.0, self.az_schritt)

    @property
    def hoehen(self) -> np.ndarray:
        return np.arange(self.h_schritt / 2, 90.0, self.h_schritt)

    def richtungen(self) -> np.ndarray:
        """(nAz, nH, 3) Einheitsvektoren der Zellmitten im Gitter."""
        a, h = np.meshgrid(self.azimute, self.hoehen, indexing="ij")
        return richtung(a, h)

    def raumwinkel(self) -> np.ndarray:
        """(nAz, nH) Raumwinkel je Zelle (cos h · Δaz · Δh)."""
        h = np.radians(self.hoehen)
        dh = np.radians(self.h_schritt)
        dw = np.radians(self.az_schritt) * (np.sin(h + dh / 2) - np.sin(h - dh / 2))
        return np.broadcast_to(dw, (len(self.azimute), len(self.hoehen))).copy()


def karte_rechnen(
    szene: Szene,
    ursprung: np.ndarray,
    normale: np.ndarray,
    tnear: float,
    raster: Raster,
    block_punkte: int = 64,
) -> np.ndarray:
    """(P, nAz, nH) bool: True = Richtung durch Geometrie verdeckt."""
    d = raster.richtungen().reshape(-1, 3)
    vorn = d @ normale > 1e-9
    dv = d[vorn]
    aus = np.zeros((len(ursprung), d.shape[0]), dtype=bool)
    for a in range(0, len(ursprung), block_punkte):
        p = ursprung[a : a + block_punkte]
        o = np.repeat(p, len(dv), axis=0)
        r = np.tile(dv, (len(p), 1))
        aus[a : a + block_punkte][:, vorn] = szene.verdeckt(o, r, tnear).reshape(len(p), len(dv))
    return aus.reshape(len(ursprung), len(raster.azimute), len(raster.hoehen))


def fern_maske(
    raster: Raster, az_geo: np.ndarray, h_hor: np.ndarray, konvergenz: float
) -> np.ndarray:
    """(nAz, nH) bool: Zelle liegt unter dem Fernhorizont (Profil geografisch, Zellen im Gitter)."""
    return (
        raster.hoehen[None, :] < horizont_bei(raster.azimute - konvergenz, az_geo, h_hor)[:, None]
    )


def horizont_bei(az_geo: np.ndarray, prof_az: np.ndarray, prof_h: np.ndarray) -> np.ndarray:
    return np.asarray(np.interp(np.mod(az_geo, 360.0), prof_az, prof_h, period=360.0))


def nachschlagen(
    karte: np.ndarray, raster: Raster, az_gitter: np.ndarray, hoehe: np.ndarray
) -> np.ndarray:
    """Bilinear: `karte` (..., nAz, nH) float → (..., T) an den Richtungen; unter 0° Höhe → 0."""
    fa = np.mod(az_gitter, 360.0) / raster.az_schritt - 0.5
    a0 = np.floor(fa).astype(np.int64)
    wa = fa - a0
    a0 %= len(raster.azimute)
    a1 = (a0 + 1) % len(raster.azimute)
    fh = np.clip(hoehe / raster.h_schritt - 0.5, 0.0, len(raster.hoehen) - 1.0)
    h0 = np.minimum(np.floor(fh).astype(np.int64), len(raster.hoehen) - 2)
    wh = fh - h0
    w = (
        karte[..., a0, h0] * (1 - wa) * (1 - wh)
        + karte[..., a1, h0] * wa * (1 - wh)
        + karte[..., a0, h0 + 1] * (1 - wa) * wh
        + karte[..., a1, h0 + 1] * wa * wh
    )
    return np.where(hoehe > 0, w, 0.0)


def gewichte(raster: Raster, normale: np.ndarray) -> np.ndarray:
    """(nAz, nH) Kosinusgewicht zur Normalen x Raumwinkel (nur vor der Fläche)."""
    return np.asarray(np.maximum(raster.richtungen() @ normale, 0.0) * raster.raumwinkel())


def sichtanteile(
    frei: np.ndarray, raster: Raster, normale: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Je Punkt (Himmel, Horizontband): kosinusgewichteter freier Anteil, relativ zu unverbaut."""
    g = gewichte(raster, normale)
    band = g * (raster.hoehen < HORIZONTBAND_GRAD)[None, :]
    himmel = (frei * g).sum(axis=(-2, -1)) / max(g.sum(), 1e-12)
    hb = (frei * band).sum(axis=(-2, -1)) / band.sum() if band.sum() > 0 else np.ones(len(frei))
    return himmel, hb


def direkt_funktion(
    frei_mittel: np.ndarray,
    raster: Raster,
    konvergenz: float,
    fern: tuple[np.ndarray, np.ndarray] | None,
) -> Callable[[np.ndarray, np.ndarray], np.ndarray]:
    """Sichtbarkeit der Sonne (geografischer Azimut, scheinbare Höhe) über die gemittelte Karte."""

    def f(az_geo: np.ndarray, hoehe: np.ndarray) -> np.ndarray:
        v = nachschlagen(frei_mittel, raster, az_geo + konvergenz, hoehe)
        if fern is not None:
            v = v * (hoehe > horizont_bei(az_geo, *fern))
        return v

    return f
