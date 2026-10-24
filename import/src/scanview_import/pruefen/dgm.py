"""Referenz-DGM des Auftraggebers (``--dgm``) als Höhenraster lesen, ohne GDAL.

Gelesen wird ein einkanaliges Float-GeoTIFF mit ModelPixelScale und genau einem ModelTiepoint,
ohne Rotation (keine ModelTransformation). PixelIsArea (GTRasterTypeGeoKey 1025 = 1): der
Tiepoint ist die linke obere Ecke des Pixels; PixelIsPoint (= 2): seine Mitte. Nodata aus Tag
42113. Alles andere ist ein Eingabefehler — eine Höhe aus einem falsch gelesenen Raster wäre
schlimmer als keine.

Mehrere Kacheln werden zu einem Raster zusammengesetzt; sie müssen dasselbe Pixelgitter haben.
Zeile 0 ist Norden.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from pyproj import CRS
from pyproj.exceptions import CRSError

from .bezug import hoehe_norm, zerlege
from .katalog import KatalogFehler
from .raster import BENUTZERDEFINIERT, GEOKEYS, PIXELSKALA, PROJIZIERT, TIEPOINT, VERTIKAL, _geokeys

RASTERTYP, NODATA, TRANSFORMATION, KANAELE = 1025, 42113, 34264, 277
FLAECHE, PUNKT = 1, 2
MAX_PIXEL = 50_000_000


class DgmFehler(KatalogFehler):
    """Referenz-DGM nicht lesbar oder nicht eindeutig: Eingabefehler (Exit 2)."""


@dataclass(frozen=True)
class Referenz:
    x0: float  # linke Kante des Rasters
    y0: float  # obere Kante (Zeile 0 = Norden)
    px: float  # Pixelbreite in x
    py: float  # Pixelhöhe in y (positiv)
    z: np.ndarray  # (ny, nx) Höhen, NaN ohne Wert
    crs: CRS  # horizontal
    hoehe: str  # normiertes Höhensystem
    dateien: tuple[tuple[str, str], ...]  # (Name, SHA-256) je Kachel

    def spalte_zeile(self, x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        return (
            np.floor((x - self.x0) / self.px).astype(np.int64),
            np.floor((self.y0 - y) / self.py).astype(np.int64),
        )

    def hoehe_an(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """DGM-Höhe des Pixels unter jedem Punkt, NaN außerhalb oder ohne Wert."""
        ix, iy = self.spalte_zeile(np.atleast_1d(x), np.atleast_1d(y))
        ny, nx = self.z.shape
        ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
        out = np.full(ix.shape, np.nan)
        out[ok] = self.z[iy[ok], ix[ok]]
        return out

    def mitten(self) -> tuple[np.ndarray, np.ndarray]:
        ny, nx = self.z.shape
        return np.meshgrid(
            self.x0 + (np.arange(nx) + 0.5) * self.px, self.y0 - (np.arange(ny) + 0.5) * self.py
        )


@dataclass(frozen=True)
class _Kachel:
    x0: float
    y0: float
    px: float
    py: float
    z: np.ndarray
    epsg: int
    epsg_hoehe: int | None
    name: str
    sha256: str


def _kachel(pfad: Path) -> _Kachel:
    roh = pfad.read_bytes()
    try:
        with Image.open(pfad) as bild:
            tags = dict(bild.tag_v2)  # type: ignore[attr-defined]
            z = np.asarray(bild, dtype=np.float64)
    except (OSError, ValueError, SyntaxError) as e:
        raise DgmFehler(f"Referenz-DGM {pfad.name}: nicht lesbar ({type(e).__name__}: {e})") from e
    name = f"Referenz-DGM {pfad.name}"
    if TRANSFORMATION in tags:
        raise DgmFehler(f"{name}: gedrehtes Raster (ModelTransformation) wird nicht gelesen")
    if int(tags.get(KANAELE, 1)) != 1 or z.ndim != 2:
        raise DgmFehler(f"{name}: genau ein Kanal erwartet")
    skala, tie = tags.get(PIXELSKALA), tags.get(TIEPOINT)
    if not skala or not tie or len(tie) != 6 or GEOKEYS not in tags:
        raise DgmFehler(f"{name}: nicht georeferenziert (Pixelgröße, ein Tiepoint, GeoKeys)")
    keys = _geokeys(tags[GEOKEYS])
    epsg = keys.get(PROJIZIERT)
    if not epsg or epsg == BENUTZERDEFINIERT:
        raise DgmFehler(f"{name}: kein projiziertes Lagebezugssystem (EPSG) deklariert")
    typ = keys.get(RASTERTYP, FLAECHE)
    if typ not in (FLAECHE, PUNKT):
        raise DgmFehler(f"{name}: Rastertyp {typ} unbekannt")
    px, py = float(skala[0]), float(skala[1])
    if px <= 0 or py <= 0:
        raise DgmFehler(f"{name}: Pixelgröße {px} \u00d7 {py} ungültig")
    i, j = float(tie[0]), float(tie[1])
    x0, y0 = float(tie[3]) - i * px, float(tie[4]) + j * py
    if typ == PUNKT:  # Tiepoint = Pixelmitte
        x0, y0 = x0 - px / 2, y0 + py / 2
    if NODATA in tags:
        try:
            nd = float(str(tags[NODATA]).strip().rstrip("\x00"))
        except ValueError as e:
            raise DgmFehler(f"{name}: Nodata-Wert „{tags[NODATA]}“ unlesbar") from e
        z = np.where(z == nd, np.nan, z)
    z = np.where(np.isfinite(z), z, np.nan)
    hoehe = keys.get(VERTIKAL)
    return _Kachel(
        x0,
        y0,
        px,
        py,
        z,
        epsg,
        hoehe if hoehe and hoehe != BENUTZERDEFINIERT else None,
        pfad.name,
        hashlib.sha256(roh).hexdigest(),
    )


def _auf_gitter(wert: float, ursprung: float, schritt: float) -> int:
    n = (wert - ursprung) / schritt
    if abs(n - round(n)) > 1e-6:
        raise DgmFehler("Kacheln des Referenz-DGM liegen nicht auf demselben Pixelgitter")
    return round(n)


def lade(pfade: list[Path], crs_vorgabe: str | None = None) -> Referenz:
    """Kacheln lesen und zusammensetzen; Bezug aus den GeoKeys oder ``--dgm-crs``."""
    if not pfade:
        raise DgmFehler("kein Referenz-DGM angegeben")
    kacheln = [_kachel(p) for p in pfade]
    erste = kacheln[0]
    for k in kacheln[1:]:
        if (k.px, k.py, k.epsg, k.epsg_hoehe) != (erste.px, erste.py, erste.epsg, erste.epsg_hoehe):
            raise DgmFehler(
                f"Referenz-DGM {k.name}: Pixelgröße oder Bezug weicht von {erste.name} ab"
            )
    h = CRS.from_epsg(erste.epsg)
    v = hoehe_norm(CRS.from_epsg(erste.epsg_hoehe).name) if erste.epsg_hoehe else None
    if crs_vorgabe is not None:
        try:
            vh, vv = zerlege(CRS.from_user_input(crs_vorgabe))
        except CRSError as e:
            raise DgmFehler(f"--dgm-crs „{crs_vorgabe}“ unbekannt") from e
        if vh is None or vv is None:
            raise DgmFehler(
                f"--dgm-crs „{crs_vorgabe}“ nennt Lage und Höhe (z. B. EPSG:25833+7837)"
            )
        if not vh.equals(h, ignore_axis_order=True):
            raise DgmFehler(f"--dgm-crs {vh.name} widerspricht der Datei ({h.name})")
        if v is not None and vv != v:
            raise DgmFehler(f"--dgm-crs Höhe {vv} widerspricht der Datei ({v})")
        v = vv
    if v is None:
        raise DgmFehler(
            "Höhensystem des Referenz-DGM nicht deklariert (GeoKey 4096 oder --dgm-crs EPSG:…+…)"
        )
    links = min(k.x0 for k in kacheln)
    oben = max(k.y0 for k in kacheln)
    spalten = [_auf_gitter(k.x0, links, erste.px) for k in kacheln]
    zeilen = [_auf_gitter(oben, k.y0, erste.py) for k in kacheln]
    nx = max(s + k.z.shape[1] for s, k in zip(spalten, kacheln, strict=True))
    ny = max(r + k.z.shape[0] for r, k in zip(zeilen, kacheln, strict=True))
    if nx * ny > MAX_PIXEL:
        raise DgmFehler(f"Referenz-DGM zu groß ({nx * ny} Pixel); auf die Fläche zuschneiden")
    z = np.full((ny, nx), np.nan)
    for s, r, k in zip(spalten, zeilen, kacheln, strict=True):
        teil = z[r : r + k.z.shape[0], s : s + k.z.shape[1]]
        doppelt = np.isfinite(teil) & np.isfinite(k.z) & (np.abs(teil - k.z) > 1e-3)
        if doppelt.any():
            raise DgmFehler(f"Referenz-DGM {k.name}: überlappt mit abweichenden Höhen")
        np.copyto(teil, k.z, where=np.isfinite(k.z))
    if not np.isfinite(z).any():
        raise DgmFehler("Referenz-DGM enthält keine Höhe")
    return Referenz(
        links,
        oben,
        erste.px,
        erste.py,
        z,
        h,
        v,
        tuple((k.name, k.sha256) for k in kacheln),
    )
