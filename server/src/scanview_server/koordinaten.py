"""Einheitenangabe für Koordinatenspalten: Koordinatensystem und Höhensystem (F23).

Spiegel von web/src/koordinaten.ts (`beschriftung`, `einheitKopf`) — gleiche Regeln, gleiche
Texte; tests/test_koordinaten.py prüft dieselben Fälle wie web/tests/koordinaten.test.ts. Nichts
wird geraten: ohne auswertbares CRS heißt der Rahmen „lokal“ und trägt kein Höhensystem.
"""

from __future__ import annotations

import re
from typing import Any

_HOEHE = {"dhhn2016": "NHN", "dhhn92": "NHN (DHHN92)", "ellipsoidal-etrs89": "ellips."}
_EPSG_KURZ = {
    "EPSG:25832": "ETRS89/UTM32", "EPSG:25833": "ETRS89/UTM33", "EPSG:3068": "Soldner Berlin",
}
_LOCAL_CS_RE = re.compile(r"^\s*LOCAL_CS\[")
_EPSG_RE = re.compile(r"^EPSG:\d+$")
_WKT_RE = re.compile(r"^[A-Z][A-Z0-9_]*\s*\[")
_WKT_NAME_RE = re.compile(r'^\s*[A-Z][A-Z0-9_]*\s*\[\s*"([^"]*)"')
_MAX_NAME = 32


def einheit(crs: Any) -> str:
    """„[m, ETRS89/UTM33, NHN]“, „[m, lokal]“ oder „[m, <System>, Höhe ?]“ aus `frame.crs`."""
    c = crs if isinstance(crs, dict) else {}
    wkt = c.get("wkt")
    if not isinstance(wkt, str) or _LOCAL_CS_RE.match(wkt):
        return "[m, lokal]"
    t = wkt.strip()
    if _EPSG_RE.match(t):
        if t not in _EPSG_KURZ:
            return "[m, lokal]"
        name = _EPSG_KURZ[t]
    elif _WKT_RE.match(t):
        hit = _WKT_NAME_RE.match(wkt)
        name = (hit.group(1).strip() if hit else "") or "WKT"
        if len(name) > _MAX_NAME:
            name = name[:_MAX_NAME] + "…"
    else:
        return "[m, lokal]"
    vd = c.get("verticalDatum")
    vd = vd.strip() if isinstance(vd, str) else ""
    hoehe = _HOEHE.get(vd.lower(), vd) if vd else "Höhe ?"
    return f"[m, {name}, {hoehe}]"
