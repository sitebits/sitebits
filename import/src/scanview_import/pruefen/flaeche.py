"""Auftragsfläche (GeoJSON) des Auftraggebers: streng gelesen, ins Punktwolken-CRS gebracht.

Das Bezugssystem wird nie geraten: Ein ``crs``-Member (EPSG) gilt, sonst WGS84 lon/lat
(RFC 7946).
Liegen Koordinaten ohne ``crs`` außerhalb von ±180/±90, ist das ein Fehler, keine UTM-Vermutung.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from .katalog import KatalogFehler

Ring = np.ndarray  # (n, 2)


class FlaechenFehler(KatalogFehler):
    """Auftragsfläche unbrauchbar (Eingabefehler, Exit 2)."""


@dataclass(frozen=True)
class Auftragsflaeche:
    # je Polygon: Außenring, dann Löcher; Koordinaten im Quell-CRS
    polygone: tuple[tuple[Ring, ...], ...]
    crs: CRS
    datei: str
    sha256: str

    def in_crs(self, ziel: CRS) -> Auftragsflaeche:
        if self.crs.equals(ziel, ignore_axis_order=True):
            return self
        t = Transformer.from_crs(self.crs, ziel, always_xy=True)
        neu = tuple(
            tuple(np.column_stack(t.transform(r[:, 0], r[:, 1])) for r in poly)
            for poly in self.polygone
        )
        return Auftragsflaeche(neu, ziel, self.datei, self.sha256)

    def grenzen(self) -> tuple[float, float, float, float]:
        alle = np.concatenate([poly[0] for poly in self.polygone])
        return (*alle.min(axis=0), *alle.max(axis=0))

    def innen(self, x: np.ndarray, y: np.ndarray) -> np.ndarray:
        """Gerade-ungerade-Regel über alle Ringe; Löcher fallen dadurch heraus."""
        drin = np.zeros(x.shape, bool)
        for poly in self.polygone:
            im_poly = np.zeros(x.shape, bool)
            for r in poly:
                x0, y0 = r[:, 0], r[:, 1]
                x1, y1 = np.roll(x0, -1), np.roll(y0, -1)
                for a, b, c, d in zip(x0, y0, x1, y1, strict=True):
                    if b == d:
                        continue
                    kreuzt = (b > y) != (d > y)
                    xs = a + (y - b) * (c - a) / (d - b)
                    im_poly ^= kreuzt & (x < xs)
            drin |= im_poly
        return drin

    def flaeche_m2(self) -> float:
        def ring(r: Ring) -> float:
            x, y = r[:, 0], r[:, 1]
            return 0.5 * abs(float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)))

        return sum(ring(p[0]) - sum(ring(h) for h in p[1:]) for p in self.polygone)


def _ringe(roh: Any) -> tuple[Ring, ...]:
    if not isinstance(roh, list) or not roh:
        raise FlaechenFehler("Polygon ohne Ringe")
    out = []
    for r in roh:
        a = np.asarray(r, dtype=float)
        if a.ndim != 2 or a.shape[0] < 4 or a.shape[1] < 2 or not np.isfinite(a).all():
            raise FlaechenFehler("Ring mit weniger als 4 Punkten oder ungültigen Koordinaten")
        out.append(a[:, :2])
    return tuple(out)


def _geometrien(d: Any) -> list[Any]:
    if not isinstance(d, dict):
        raise FlaechenFehler("kein GeoJSON-Objekt")
    typ = d.get("type")
    if typ == "FeatureCollection":
        return [g for f in d.get("features", []) for g in _geometrien(f)]
    if typ == "Feature":
        return _geometrien(d.get("geometry"))
    if typ in ("Polygon", "MultiPolygon"):
        return [d]
    raise FlaechenFehler(f"Geometrie „{typ}“ ist keine Fläche")


def _crs_member(d: dict[str, Any]) -> CRS | None:
    name = (d.get("crs") or {}).get("properties", {}).get("name")
    if not name:
        return None
    m = re.search(r"EPSG:+(\d+)", str(name))
    try:
        return CRS.from_epsg(int(m.group(1))) if m else CRS.from_user_input(name)
    except CRSError as e:
        raise FlaechenFehler(f"crs-Angabe „{name}“ unbekannt") from e


def lade(pfad: Path, epsg: int | None = None) -> Auftragsflaeche:
    roh = pfad.read_bytes()
    try:
        d = json.loads(roh)
    except ValueError as e:
        raise FlaechenFehler(f"Auftragsfläche nicht lesbar: {e}") from e
    polygone: list[tuple[Ring, ...]] = []
    for g in _geometrien(d):
        teile = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        polygone += [_ringe(p) for p in teile]
    if not polygone:
        raise FlaechenFehler("Auftragsfläche ohne Polygon")
    crs = _crs_member(d) if isinstance(d, dict) else None
    if epsg is not None:
        crs = CRS.from_epsg(epsg)
    if crs is None:
        alle = np.concatenate([p[0] for p in polygone])
        if np.abs(alle[:, 0]).max() > 180 or np.abs(alle[:, 1]).max() > 90:
            raise FlaechenFehler(
                "Koordinaten sind kein WGS84 lon/lat und das GeoJSON nennt kein crs; "
                "Bezugssystem mit --flaeche-epsg angeben"
            )
        crs = CRS.from_epsg(4326)
    return Auftragsflaeche(tuple(polygone), crs, pfad.name, hashlib.sha256(roh).hexdigest())
