"""Bezugssystem einer Geodatei lesen: horizontales CRS und normiertes Höhensystem."""

from __future__ import annotations

from pyproj import CRS
from pyproj.exceptions import CRSError

from .. import hoehen
from .lieferung import Lieferung, art, kopf_e57, kopf_las
from .raster import geotiff


def hoehe_norm(name: str) -> str:
    n = hoehen.normiere(name) or hoehen.normiere(name.lower().removesuffix(" height"))
    return n or f"unbekannt ({name})"


def zerlege(crs: CRS | None) -> tuple[CRS | None, str | None]:
    if crs is None:
        return None, None
    if crs.is_compound:
        teile = crs.sub_crs_list
        return teile[0], hoehe_norm(teile[1].name) if len(teile) > 1 else None
    return crs, None


def crs_datei(lf: Lieferung, rel: str) -> tuple[CRS | None, str | None, str]:
    """Horizontales CRS, Höhensystem (normiert) und Quelle der Deklaration einer Geodatei."""
    p, a = lf.pfad(rel), art(rel)
    if a in ("las", "laz"):
        k = kopf_las(p)
        h, v = zerlege(k["crs"])
        return h, v, "LAS-Kopf"
    if a == "tiff":
        g = geotiff(p)
        h = CRS.from_epsg(g["epsg"]) if g["epsg"] else None
        v = hoehe_norm(CRS.from_epsg(g["epsg_hoehe"]).name) if g["epsg_hoehe"] else None
        return h, v, "GeoTIFF-Schlüssel"
    meta = kopf_e57(p)["koordinaten"]
    try:
        h, v = zerlege(CRS.from_user_input(meta)) if meta.strip() else (None, None)
    except CRSError:
        h, v = None, None
    return h, v, "E57 coordinateMetadata"


def gleich(h: CRS, epsg: int) -> bool:
    return bool(h.to_epsg() == epsg or h.equals(CRS.from_epsg(epsg), ignore_axis_order=True))


def hoehensystem_wolke(lf: Lieferung, dateien: list[str]) -> str | None:
    """Gemeinsames Höhensystem der Punktdateien (Paketangabe ergänzt), sonst None."""
    paket = hoehe_norm(str((lf.paket or {}).get("frame", {}).get("crs", {}).get("verticalDatum")))
    gefunden = set()
    for rel in dateien:
        _h, v, _q = crs_datei(lf, rel)
        if v is None and lf.im_paket(rel) and not paket.startswith("unbekannt"):
            v = paket
        gefunden.add(v)
    return gefunden.pop() if len(gefunden) == 1 else None
