"""Genauigkeitsnachweis aus dem aktiven Metashape-Pro-2.x-Chunk exportieren (F15 Teil 1).

In Metashape über Werkzeuge → Skript ausführen, mit Argumenten:
--epsg 25833 --hoehensystem ellipsoidisch --ausgabe /absolut/genauigkeit.json
Optional --bericht /absolut/report.pdf (Metashape-Bericht, nur Prüfsumme wandert ins Paket) und
mehrfach --pass-strecke NAME für Strecken, die außerhalb des Ausgleichs zum Skalieren benutzt wurden
(sonst zählen sie als Kontrollstrecke).

Exportiert werden Koordinaten, keine Fehlerwerte: Soll (Referenz) und Ist (geschätzt) je Marker im
Ziel-CRS, Soll- und Ist-Länge je Maßstabsstrecke, Kamera-GNSS-Abweichungen je Gruppe.
Rolle: Referenzhäkchen gesetzt = ``control`` (im Ausgleich), sonst ``check``. Der Chunk wird nicht
verändert. API: https://www.agisoft.com/pdf/metashape_python_api_2_3_1.pdf
Ohne Metashape mit einem Stub testbar (``api``-Parameter).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

Vec = list[float]


def _quantil(werte: Sequence[float], q: float) -> float:
    """Lineares Quantil wie numpy.percentile (Metashape bringt numpy nicht sicher mit)."""
    s = sorted(werte)
    pos = (len(s) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _abstand(a: Sequence[float], b: Sequence[float]) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b, strict=True)))


def _vec(v: Any) -> Vec:
    return [float(x) for x in v]


def _genauigkeit3(wert: Any, standard: Any) -> Vec | None:
    for kandidat in (wert, standard):
        if kandidat is None:
            continue
        try:
            werte = _vec(kandidat)
        except TypeError:
            werte = [float(kandidat)] * 3
        if len(werte) == 3 and all(math.isfinite(x) and x > 0 for x in werte):
            return werte
    return None


def _genauigkeit1(wert: Any, standard: Any) -> float | None:
    for kandidat in (wert, standard):
        if kandidat is not None and math.isfinite(float(kandidat)) and float(kandidat) > 0:
            return float(kandidat)
    return None


def _punkte(
    chunk: Any, ziel_aus_crs: Callable[[Any], Vec], welt: Callable[[Any], Vec], api: Any
) -> list[dict[str, Any]]:
    out = []
    for m in chunk.markers:
        if getattr(m, "type", None) == getattr(api.Marker.Type, "Fiducial", object()):
            continue
        ref = m.reference
        if ref is None or ref.location is None:
            continue
        out.append(
            {
                "name": m.label,
                "role": "control" if ref.enabled else "check",
                "reference": ziel_aus_crs(ref.location),
                "referenceAccuracy": _genauigkeit3(ref.accuracy, chunk.marker_location_accuracy),
                "estimated": None if m.position is None else welt(m.position),
                "images": len(m.projections.keys()),
            }
        )
    return out


def _endpunkt(p: Any) -> Any:
    """Intern-Koordinate eines Strecken-Endpunkts (Marker: position, Kamera: center)."""
    return getattr(p, "position", None) if hasattr(p, "position") else getattr(p, "center", None)


def _strecken(
    chunk: Any, geozentrisch: Callable[[Any], Vec], pass_strecken: Sequence[str]
) -> list[dict[str, Any]]:
    unbekannt = set(pass_strecken) - {s.label for s in chunk.scalebars}
    if unbekannt:
        raise ValueError(f"--pass-strecke unbekannt: {sorted(unbekannt)}")
    out = []
    for s in chunk.scalebars:
        ref = s.reference
        if ref is None or ref.distance is None:
            continue
        a, b = _endpunkt(s.point0), _endpunkt(s.point1)
        ist = None if a is None or b is None else _abstand(geozentrisch(a), geozentrisch(b))
        out.append(
            {
                "name": s.label,
                "role": "control" if ref.enabled or s.label in pass_strecken else "check",
                "reference": float(ref.distance),
                "referenceAccuracy": _genauigkeit1(ref.accuracy, chunk.scalebar_accuracy),
                "estimated": ist,
            }
        )
    return out


def _kameragruppen(
    chunk: Any, ziel_aus_crs: Callable[[Any], Vec], welt: Callable[[Any], Vec]
) -> list[dict[str, Any]]:
    gruppen: dict[str, tuple[list[float], list[float]]] = {}
    for c in chunk.cameras:
        ref = c.reference
        if c.transform is None or c.center is None or ref is None or ref.location is None:
            continue
        soll, ist = ziel_aus_crs(ref.location), welt(c.center)
        xy, dz = gruppen.setdefault(c.group.label if c.group is not None else "", ([], []))
        xy.append(math.hypot(ist[0] - soll[0], ist[1] - soll[1]))
        dz.append(ist[2] - soll[2])
    return [
        {
            "name": name or "(ohne Gruppe)",
            "n": len(xy),
            "xyMedian": _quantil(xy, 0.5),
            "xy95": _quantil(xy, 0.95),
            "dzMedian": _quantil(dz, 0.5),
            "dz95": _quantil([abs(v) for v in dz], 0.95),
        }
        for name, (xy, dz) in sorted(gruppen.items())
    ]


def export_genauigkeit(
    chunk: Any,
    epsg: int,
    height_system: str,
    *,
    pass_strecken: Sequence[str] = (),
    api: Any = None,
) -> dict[str, Any]:
    """Ohne Änderung am Chunk: Marker, Strecken und Kamera-GNSS im Ziel-CRS."""
    if api is None:
        import Metashape

        api = Metashape
    if chunk is None or chunk.crs is None or chunk.transform.matrix is None:
        raise ValueError("Aktiver georeferenzierter Chunk mit Transformation erforderlich")
    if type(epsg) is not int or epsg <= 0 or not height_system.strip():
        raise ValueError("Positive EPSG-Nummer und Höhensystem erforderlich")
    target = api.CoordinateSystem(f"EPSG::{epsg}")
    if hasattr(target, "setContext"):
        target.setContext(api.app.document)
    quelle = getattr(chunk, "world_crs", None) or chunk.crs.geoccs

    def geozentrisch(intern: Any) -> Vec:
        return _vec(chunk.transform.matrix.mulp(api.Vector(_vec(intern))))

    def welt(intern: Any) -> Vec:
        return _vec(
            api.CoordinateSystem.transform(api.Vector(geozentrisch(intern)), quelle, target)
        )

    def ziel_aus_crs(punkt: Any) -> Vec:
        return _vec(api.CoordinateSystem.transform(api.Vector(_vec(punkt)), chunk.crs, target))

    return {
        "formatVersion": 1,
        "crs": {"epsg": epsg, "verticalDatum": height_system},
        "markers": _punkte(chunk, ziel_aus_crs, welt, api),
        "scalebars": _strecken(chunk, geozentrisch, pass_strecken),
        "cameraGroups": _kameragruppen(chunk, ziel_aus_crs, welt),
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--epsg", type=int, required=True)
    parser.add_argument("--hoehensystem", required=True)
    parser.add_argument("--ausgabe", type=Path, required=True)
    parser.add_argument("--bericht", type=Path, default=None)
    parser.add_argument("--pass-strecke", action="append", default=[])
    args = parser.parse_args(argv)
    import Metashape

    if not Metashape.app.version.startswith("2."):
        raise ValueError("Metashape Pro 2.x erforderlich")
    document = Metashape.app.document
    if not document.path or not Path(document.path).is_file():
        raise ValueError("Projekt zuerst speichern: die Prüfsumme der .psx gehört zum Nachweis")
    chunk = document.chunk
    data = export_genauigkeit(chunk, args.epsg, args.hoehensystem, pass_strecken=args.pass_strecke)
    data["source"] = {
        "software": "Agisoft Metashape Professional",
        "version": Metashape.app.version,
        "created": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "projectSha256": _sha256(Path(document.path)),
    }
    data["report"] = None
    if args.bericht is not None:
        chunk.exportReport(str(args.bericht))
        data["report"] = {"filename": args.bericht.name, "sha256": _sha256(args.bericht)}
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False)
    args.ausgabe.write_text(text, encoding="utf-8")
    print(
        f"Marker: {len(data['markers'])}, Strecken: {len(data['scalebars'])}, "
        f"Kameragruppen: {len(data['cameraGroups'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
