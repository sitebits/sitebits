"""Minimale Pakete für Tests (nur project.json + kleine Dummy-Dateien, keine echten Daten)."""
from __future__ import annotations

import json
from pathlib import Path


def lege_paket(root: Path, pid: str, *, schema: int = 2, titel: str | None = None,
               ordner: str | None = None, crs_wkt: str | None = None) -> Path:
    d = root / (ordner or pid)
    d.mkdir(parents=True)
    (d / "cloud.copc.laz").write_bytes(b"\0" * 1000)
    (d / "topview.png").write_bytes(b"\x89PNG")
    m = {
        "schemaVersion": schema,
        "projectId": pid,
        "revision": 1,
        "title": titel or pid,
        "frame": {"crs": {"wkt": crs_wkt, "verticalDatum": None, "declaredBy": "file"},
                  "unit": "m", "unitDeclaredBy": "file", "originWorld": [0, 0, 0]},
        "layers": [
            {"id": "punkte", "kind": "pointcloud", "title": "Punktwolke", "url": "cloud.copc.laz",
             "pointCount": 1234, "boundsWorld": [[0, 0, 0], [40.5, 30.25, 12.0]]},
            {"id": "standpunkte", "kind": "panoramas", "title": "Standpunkte",
             "url": "stations.json", "count": 45, "pointcloudLayer": "punkte"},
        ],
        "initialView": {"target": [0, 0, 0], "distance": 10},
        "topview": {"url": "topview.png"},
    }
    (d / "project.json").write_text(json.dumps(m), encoding="utf-8")
    return d
