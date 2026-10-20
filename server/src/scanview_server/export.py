"""Export der Projektinhalte: CSV für deutsches Excel und JSON.

CSV wie web/src/measureCsv.ts: Trennzeichen ';', Dezimalkomma, UTF-8 mit BOM, Zeilenende CRLF.
Bei eingeschalteten Befund-Fäden kommen hinten die Spalten Status, Zuständig, Frist dazu (sonst
bleibt die Datei wie bisher); JSON trägt dann je POI Stand und sichtbaren Faden.
Formelinjektion: Textzellen, die mit = + - @ Tab oder CR beginnen, bekommen ein
führendes Apostroph — Excel/LibreOffice werten sie dann als Text. Nur Textzellen: Zahlzellen wie
'-1,500' bleiben Zahlen, die Koordinatenzelle beginnt immer mit 'P1:'.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from . import genauigkeit
from .inhalte import Eintrag

CSV_BOM = "\ufeff"
FORMEL_START = ("=", "+", "-", "@", "\t", "\r")
KOPF = [
    "Typ", "ID", "Titel / Art", "Kategorie", "Notiz", "Standpunkt",
    "Länge [m]", "Fläche [m\u00b2]", "Umfang [m]", "Höhenunterschied dz [m]", "Horizontal [m]",
    "Schräg [m]", "Ebenheit max. [m]", "Eckpunkte", "Koordinaten X Y Z [m]",
    "Geändert am (UTC)", "Geändert von", "Hinweis",
]
KOORDINATEN_KOPF = "Koordinaten X Y Z [m]"
MESSART = {"distance": "Strecke", "polyline": "Polylinie", "area": "Fläche", "height": "Höhe"}


def num(x: float) -> str:
    t = f"{x:.3f}"
    return ("0.000" if t == "-0.000" else t).replace(".", ",")


def text_zelle(wert: str) -> str:
    t = "'" + wert if wert.startswith(FORMEL_START) else wert
    if any(z in t for z in (";", '"', "\n", "\r")):
        t = '"' + t.replace('"', '""') + '"'
    return t


def _zahl(x: float | None) -> str:
    return "" if x is None else num(x)


def messwerte(daten: dict[str, Any]) -> dict[str, float | None]:
    p: list[list[float]] = daten["points"]
    kind = daten["kind"]
    out: dict[str, float | None] = dict.fromkeys(
        ("laenge", "flaeche", "umfang", "dz", "horizontal", "schraeg", "ebenheit"),
    )
    if kind in ("distance", "polyline"):
        out["laenge"] = sum(math.dist(p[i], p[i + 1]) for i in range(len(p) - 1))
    elif kind == "height":
        a, b = p[0], p[1]
        out["dz"] = b[2] - a[2]
        out["horizontal"] = math.hypot(b[0] - a[0], b[1] - a[1])
        out["schraeg"] = math.dist(a, b)
    elif kind == "area":
        out["umfang"] = sum(math.dist(p[i], p[(i + 1) % len(p)]) for i in range(len(p)))
        out["flaeche"] = daten.get("areaM2")
        out["ebenheit"] = daten.get("planarityM")
    return out


def _koordinaten(punkte: list[list[float]]) -> str:
    return " | ".join(f"P{i + 1}: {' '.join(num(v) for v in pt)}" for i, pt in enumerate(punkte))


def _zeit(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M")


def _poi_zeile(e: Eintrag) -> list[str]:
    d = e.daten
    return [
        "POI", text_zelle(e.id), text_zelle(d["title"]), text_zelle(d["category"]),
        text_zelle(d["note"]), text_zelle(d["station"] or ""),
        "", "", "", "", "", "", "", "1", _koordinaten([d["position"]]),
        _zeit(e.geaendert_am), text_zelle(e.geaendert_von_name or ""), "",
    ]


def _mess_zeile(e: Eintrag, hinweise: Mapping[str, tuple[str, ...]]) -> list[str]:
    d = e.daten
    w = messwerte(d)
    return [
        "Messung", text_zelle(e.id), MESSART[d["kind"]], "", "", "",
        _zahl(w["laenge"]), _zahl(w["flaeche"]), _zahl(w["umfang"]), _zahl(w["dz"]),
        _zahl(w["horizontal"]), _zahl(w["schraeg"]), _zahl(w["ebenheit"]),
        str(len(d["points"])), _koordinaten(d["points"]),
        _zeit(e.geaendert_am), text_zelle(e.geaendert_von_name or ""),
        text_zelle(genauigkeit.mess_hinweis(d.get("pointSources"), hinweise)),
    ]


def csv_text(pois: list[Eintrag], messungen: list[Eintrag],
             faden: tuple[tuple[str, ...], dict[str, list[str]]] | None = None,
             einheit: str = "[m]",
             hinweise: Mapping[str, tuple[str, ...]] | None = None) -> str:
    """`faden`: (Kopf der Zusatzspalten, Werte je POI-ID) — nur bei eingeschalteten Fäden.
    `einheit`: Koordinaten- und Höhensystem im Kopf der Koordinatenspalte (koordinaten.einheit).
    `hinweise`: Messhinweise je Ebenen-ID (Genauigkeit F15, wie die Spalte „Hinweis“ im Viewer)."""
    je_ebene = hinweise or {}
    # Der Systemname stammt aus dem Paket-WKT und kann „;“ oder Anführungszeichen enthalten.
    spalte = text_zelle(f"Koordinaten X Y Z {einheit}")
    kopf_basis = [spalte if k == KOORDINATEN_KOPF else k for k in KOPF]
    zeilen = [kopf_basis, *(_poi_zeile(e) for e in pois),
              *(_mess_zeile(e, je_ebene) for e in messungen)]
    if faden is not None:
        kopf, werte = faden
        leer = [""] * len(kopf)
        zeilen = [[*kopf_basis, *kopf],
                  *([*_poi_zeile(e), *(text_zelle(w) for w in werte.get(e.id, leer))]
                    for e in pois),
                  *([*_mess_zeile(e, je_ebene), *leer] for e in messungen)]
    return CSV_BOM + "\r\n".join(";".join(z) for z in zeilen) + "\r\n"


def _poi_json(e: Eintrag, staende: dict[str, Any] | None,
              faeden: dict[str, list[Any]] | None) -> dict[str, Any]:
    if staende is None:
        return {"id": e.id, **e.daten}
    st = staende[e.id]
    return {"id": e.id, **e.daten, "status": st["status"], "zustaendig": st["zustaendig"],
            "frist": st["frist"], "faden": (faeden or {}).get(e.id, [])}


def json_export(
    pid: str, revision: int | None, titel: str, beschreibung: str, pois: list[Eintrag],
    messungen: list[Eintrag], now: int, staende: dict[str, Any] | None = None,
    faeden: dict[str, list[Any]] | None = None,
    *, ebenen: tuple[genauigkeit.EbeneGenauigkeit, ...] = (),
) -> dict[str, Any]:
    """`staende`/`faeden` nur bei eingeschalteten Befund-Fäden (Faden ohne interne Einträge für
    Nicht-Bauherrenseite — gefiltert vom Aufrufer). `ebenen`: Genauigkeit je Ebene (F15)."""
    return {
        "schemaVersion": 1, "projectId": pid, "revision": revision or 0, "frame": "world",
        "unit": "m", "exportiertAm": datetime.fromtimestamp(now, UTC).isoformat(),
        "titel": titel, "beschreibung": beschreibung,
        # Ohne Stand = zwischen den Lesevorgängen gelöscht: weglassen.
        "pois": [_poi_json(e, staende, faeden) for e in pois
                 if staende is None or e.id in staende],
        "messungen": [{"id": e.id, **e.daten} for e in messungen],
        "genauigkeit": [g.als_json() for g in ebenen],
    }
