"""Baut ein kleines synthetisches siteBits-Paket mit echter LAZ-Wolke (für den E2E-Test).

Läuft in der Import-Umgebung (laspy, numpy, pyproj), nicht in der Server-Umgebung:
`import/.venv/bin/python -I e2e_paket.py <data_dir> <projekt_id>`. Nur synthetische Daten.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import laspy
import numpy as np
from pyproj import CRS


def main(data: Path, pid: str) -> None:
    d = data / pid
    d.mkdir(parents=True)
    crs = CRS.from_user_input("EPSG:25833+7837")
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets = [365800.0, 5806700.0, 40.0]
    h.scales = [0.001, 0.001, 0.001]
    h.add_crs(crs)
    las = laspy.LasData(h)
    n = 500
    rng = np.random.default_rng(7)
    las.x = 365800.0 + rng.uniform(0, 20, n)
    las.y = 5806700.0 + rng.uniform(0, 20, n)
    las.z = 41.0 + rng.uniform(0, 3, n)
    las.classification = rng.choice([1, 2, 6], n).astype(np.uint8)
    las.write(d / "cloud.laz")
    manifest = {
        "schemaVersion": 2, "projectId": pid, "revision": 1, "title": "Testpaket",
        "frame": {"crs": {"wkt": crs.to_wkt(), "verticalDatum": None, "declaredBy": "file"},
                  "unit": "m", "unitDeclaredBy": "file", "originWorld": [365800, 5806700, 40]},
        "layers": [{"id": "punkte", "kind": "pointcloud", "title": "Punktwolke",
                    "url": "cloud.laz", "pointCount": n, "boundsWorld": [[0, 0, 0], [20, 20, 4]]}],
        "initialView": {"target": [0, 0, 0], "distance": 10},
    }
    (d / "project.json").write_text(json.dumps(manifest), encoding="utf-8")


if __name__ == "__main__":
    main(Path(sys.argv[1]), sys.argv[2])
