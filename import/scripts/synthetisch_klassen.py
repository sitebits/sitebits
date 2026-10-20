"""Synthetisches Prüfpaket „Klassen und Echos“ (F30; nur erfundene Geometrie, keine Kundendaten).

LAS 1.4, Punktformat 7, ETRS89/UTM33 (EPSG:25833), Höhensystem DHHN2016 deklariert.
Weltkoordinaten relativ zu ``URSPRUNG`` (m):

- Boden (Klasse 2): z = 0, x, y ∈ [0, 20], Raster ``--abstand``; unter der Baumkrone als
  zweites Echo (2/2);
- Gebäudeblock (Klasse 6): Dach z = 4 über x ∈ [12, 18], y ∈ [12, 18], dazu die vier Wände;
- Baum (Klasse 5): Kronenscheibe z = 6, Radius 2,5 m um (6, 6), erstes von zwei Echos (1/2);
- Rauschen (Klasse 7): einzelne Punkte z ∈ [8, 10] über dem Boden, Einzelechos;
- ein Streifen mit ungültigem Echo (0/0) am Rand x ∈ [0, 1] (zählt nie als erstes Echo).

Aufruf (aus ``import/``)::

    uv run python scripts/synthetisch_klassen.py --ziel ~/scanview-e2e-daten/e2e-klassen

Ein vorhandenes Ziel wird nie überschrieben. Die Prüfpunkte der E2E (web/e2e/klassen.spec.ts) folgen
aus ``URSPRUNG``, ``BAUM_MITTE`` und ``BAUM_Z``. Braucht den Punktimport mit Klassen (F30).
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

import laspy
import numpy as np

URSPRUNG = (368000.0, 5807000.0, 30.0)
BAUM_MITTE = (6.0, 6.0)
BAUM_RADIUS = 2.5
BAUM_Z = 6.0
DACH = (12.0, 18.0)
DACH_Z = 4.0
TITEL = "Synthetisch: Klassen und Echos"


def _raster(a: float, b: float, abstand: float) -> np.ndarray:
    return np.arange(a, b + abstand / 2, abstand)


def punkte(abstand: float = 0.05, seed: int = 20261009) -> dict[str, np.ndarray]:
    """Je Teil (x, y, z, Klasse, Echonummer, Echoanzahl) als Spalten, lokal zum Ursprung."""
    rng = np.random.default_rng(seed)
    g = _raster(0.0, 20.0, abstand)
    bx, by = (a.ravel() for a in np.meshgrid(g, g))
    unter_baum = np.hypot(bx - BAUM_MITTE[0], by - BAUM_MITTE[1]) <= BAUM_RADIUS
    rand = bx <= 1.0
    boden_nr = np.where(unter_baum, 2, np.where(rand, 0, 1))
    boden_anz = np.where(unter_baum, 2, np.where(rand, 0, 1))
    boden = np.column_stack([bx, by, np.zeros_like(bx), np.full_like(bx, 2), boden_nr, boden_anz])
    # Unter dem Dach liegt kein Boden (Gebäude steht darauf).
    im_haus = (bx > DACH[0]) & (bx < DACH[1]) & (by > DACH[0]) & (by < DACH[1])
    boden = boden[~im_haus]

    d = _raster(DACH[0], DACH[1], abstand)
    dx, dy = (a.ravel() for a in np.meshgrid(d, d))
    dach = np.column_stack([dx, dy, np.full_like(dx, DACH_Z)])
    h = _raster(0.0, DACH_Z, abstand)
    s, hz = (a.ravel() for a in np.meshgrid(d, h))
    waende = np.vstack(
        [
            np.column_stack([s, np.full_like(s, DACH[0]), hz]),
            np.column_stack([s, np.full_like(s, DACH[1]), hz]),
            np.column_stack([np.full_like(s, DACH[0]), s, hz]),
            np.column_stack([np.full_like(s, DACH[1]), s, hz]),
        ]
    )
    haus_xyz = np.vstack([dach, waende])
    haus = np.column_stack(
        [haus_xyz, np.full(len(haus_xyz), 6), np.ones(len(haus_xyz)), np.ones(len(haus_xyz))]
    )

    k = _raster(-BAUM_RADIUS, BAUM_RADIUS, abstand)
    kx, ky = (a.ravel() for a in np.meshgrid(k, k))
    in_krone = np.hypot(kx, ky) <= BAUM_RADIUS
    kx, ky = kx[in_krone] + BAUM_MITTE[0], ky[in_krone] + BAUM_MITTE[1]
    baum = np.column_stack(
        [
            kx,
            ky,
            np.full_like(kx, BAUM_Z),
            np.full_like(kx, 5),
            np.ones_like(kx),
            np.full_like(kx, 2),
        ]
    )

    n = 400
    rx, ry, rz = rng.uniform(0, 20, n), rng.uniform(0, 20, n), rng.uniform(8, 10, n)
    rauschen = np.column_stack([rx, ry, rz, np.full(n, 7), np.ones(n), np.ones(n)])
    return {"boden": boden, "haus": haus, "baum": baum, "rauschen": rauschen}


def schreibe_las(pfad: Path, teile: dict[str, np.ndarray], seed: int = 20261009) -> None:
    rng = np.random.default_rng(seed)
    a = np.vstack(list(teile.values()))
    kopf = laspy.LasHeader(point_format=7, version="1.4")
    kopf.scales = [0.001] * 3
    kopf.offsets = list(URSPRUNG)
    las = laspy.LasData(kopf)
    las.x = a[:, 0] + URSPRUNG[0]
    las.y = a[:, 1] + URSPRUNG[1]
    las.z = a[:, 2] + URSPRUNG[2] + rng.normal(0.0, 0.001, len(a))
    las.classification = a[:, 3].astype(np.uint8)
    las.return_number = a[:, 4].astype(np.uint8)
    las.number_of_returns = a[:, 5].astype(np.uint8)
    farbe = (
        np.array(
            [
                [0, 0, 0],
                [0, 0, 0],
                [120, 100, 80],
                [0, 0, 0],
                [0, 0, 0],
                [40, 140, 40],
                [180, 60, 50],
                [255, 255, 255],
            ],
            dtype=np.uint16,
        )
        * 256
    )
    rgb = farbe[a[:, 3].astype(int)]
    las.red, las.green, las.blue = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    las.write(pfad)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--ziel", type=Path, required=True)
    ap.add_argument("--abstand", type=float, default=0.05)
    args = ap.parse_args(argv)
    ziel: Path = args.ziel.expanduser()
    if ziel.exists():
        print(f"Ziel vorhanden, nichts überschrieben: {ziel}", file=sys.stderr)
        return 1
    from scanview_import.convert import convert

    with tempfile.TemporaryDirectory() as tmp:
        las = Path(tmp) / "klassen.las"
        schreibe_las(las, punkte(args.abstand))
        convert(las, ziel, "EPSG:25833", mode="direct", hoehe=("dhhn2016", "dhhn2016"))
    pj = ziel / "project.json"
    m = json.loads(pj.read_text(encoding="utf-8"))
    m["title"] = TITEL
    pj.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    cloud = next(lay for lay in m["layers"] if lay.get("kind") == "pointcloud")
    print(f"Klassen: {cloud.get('classes')}  Attribute: {cloud.get('attributes')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
