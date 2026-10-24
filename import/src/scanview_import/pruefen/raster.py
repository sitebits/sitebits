"""GeoTIFF-Kopf ohne GDAL: Pixelgröße und EPSG-Codes aus den GeoTIFF-Tags (Pillow).

Gelesen werden ModelPixelScale (33550), ModelTiepoint (33922) und das GeoKey-Verzeichnis
(34735) mit ProjectedCSTypeGeoKey (3072), GeographicTypeGeoKey (2048) und
VerticalCSTypeGeoKey (4096). Mehr braucht die Prüfung „Raster ≤ x m im KS y“ nicht.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PIL import Image

PIXELSKALA, TIEPOINT, GEOKEYS = 33550, 33922, 34735
PROJIZIERT, GEOGRAFISCH, VERTIKAL = 3072, 2048, 4096
BENUTZERDEFINIERT = 32767


def _geokeys(roh: Any) -> dict[int, int]:
    werte = [int(x) for x in roh]
    if len(werte) < 4:
        return {}
    n = werte[3]
    keys: dict[int, int] = {}
    for i in range(n):
        key, ort, _anzahl, wert = werte[4 + 4 * i : 8 + 4 * i]
        if ort == 0:  # Wert steht direkt im Verzeichnis (alle hier gelesenen Schlüssel)
            keys[key] = wert
    return keys


def geotiff(pfad: Path) -> dict[str, Any]:
    with Image.open(pfad) as bild:
        tags = bild.tag_v2  # type: ignore[attr-defined]
        breite, hoehe = bild.size
        skala = tags.get(PIXELSKALA)
        keys = _geokeys(tags[GEOKEYS]) if GEOKEYS in tags else {}
    epsg = keys.get(PROJIZIERT) or keys.get(GEOGRAFISCH)
    return {
        "breite": breite,
        "hoehe": hoehe,
        "pixel_m": (float(skala[0]), float(skala[1])) if skala else None,
        "epsg": epsg if epsg and epsg != BENUTZERDEFINIERT else None,
        "epsg_hoehe": keys.get(VERTIKAL) if keys.get(VERTIKAL) != BENUTZERDEFINIERT else None,
        "georeferenziert": GEOKEYS in tags,
    }
