"""Tests für die Übersicht (Draufsicht + Bodenhöhe) — ohne Docker: die Pakete entstehen mit laspy
als gewöhnliche LAZ-Datei; ``read_points`` fällt dann (wie ``verify``) auf ``laspy.read`` zurück."""

from __future__ import annotations

import json
import os
from pathlib import Path

import laspy
import numpy as np
import pytest
from PIL import Image

from scanview_import.cli import main
from scanview_import.manifest_io import cloud_of
from scanview_import.overview import (
    GROUND_MIN_POINTS,
    Grid,
    build_overview,
    ground_z,
    rasterize_top,
    topview_grid,
)

# Echtes Paket der Pilotkirche, nur lokal vorhanden: Pfad per Umgebung.
_REFERENZ_PAKET = os.environ.get("SCANVIEW_REFERENZ_PAKET")
REFERENZ_PAKET = Path(_REFERENZ_PAKET) if _REFERENZ_PAKET else None


def _write_las(path: Path, xyz: np.ndarray, rgb: np.ndarray | None) -> None:
    hdr = laspy.LasHeader(point_format=7, version="1.4")
    hdr.scales = np.array([0.001, 0.001, 0.001])
    hdr.offsets = np.floor(xyz.min(axis=0))
    las = laspy.LasData(hdr)
    las.x, las.y, las.z = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    if rgb is not None:
        las.red, las.green, las.blue = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    las.write(path)


def _ring_ground(n: int, centre: tuple[float, float], z: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    r = rng.uniform(0.5, 1.5, n)
    a = rng.uniform(0, 2 * np.pi, n)
    return np.column_stack(
        [centre[0] + r * np.cos(a), centre[1] + r * np.sin(a), z + rng.normal(0, 0.003, n)]
    )


def test_topview_grid_edges_and_size():
    g = topview_grid([0.0, 0.0, 0.0], [10.0, 5.0, 3.0], pixel_m=0.05)
    assert (g.x0, g.y0, g.pixel_m, g.width, g.height) == (0.0, 5.0, 0.05, 200, 100)


def test_topview_grid_caps_pixel_count():
    g = topview_grid([0.0, 0.0, 0.0], [1000.0, 10.0, 1.0], pixel_m=0.05, max_px=2048)
    assert g.width == 2048
    assert g.pixel_m == pytest.approx(1000.0 / 2048)
    assert g.height == 21


def test_topview_grid_rejects_flat_bounds():
    with pytest.raises(ValueError):
        topview_grid([0.0, 0.0, 0.0], [0.0, 5.0, 1.0])


def test_rasterize_top_highest_point_wins_and_rows_run_south():
    g = Grid(x0=0.0, y0=2.0, pixel_m=1.0, width=2, height=2)
    xyz = np.array([[0.5, 1.5, 1.0], [0.5, 1.5, 2.0], [1.5, 0.5, 0.0]])
    rgb = np.array([[65535, 0, 0], [0, 0, 65535], [0, 65535, 0]], dtype=np.uint16)
    img = rasterize_top(xyz, rgb, g)
    assert tuple(img[0, 0]) == (0, 0, 255, 255)  # Nordwest: höherer (blauer) Punkt
    assert tuple(img[1, 1]) == (0, 255, 0, 255)  # Südost: Zeile 1 = kleineres Y
    assert img[0, 1, 3] == 0 and img[1, 0, 3] == 0  # ohne Punkt transparent


def test_rasterize_top_without_rgb_uses_height_gray():
    g = Grid(x0=0.0, y0=1.0, pixel_m=1.0, width=2, height=1)
    xyz = np.array([[0.5, 0.5, 0.0], [1.5, 0.5, 10.0]])
    img = rasterize_top(xyz, None, g)
    assert img[0, 1, 0] > img[0, 0, 0]  # höher = heller


def test_ground_z_ignores_tripod_zone_and_clutter():
    ground = _ring_ground(400, (10.0, 20.0), 0.0, seed=1)
    table = _ring_ground(200, (10.0, 20.0), 0.8, seed=2)  # Tisch im Ring, über dem Boden
    below = np.array([[10.0, 20.1, -3.0]] * 50)  # direkt unter dem Stativ (r < 0,5 m): Ausreißer
    gz = ground_z(np.vstack([ground, table, below]), (10.0, 20.0, 1.5))
    assert gz is not None and abs(gz) < 0.005


def test_ground_z_none_with_too_few_points():
    few = _ring_ground(GROUND_MIN_POINTS - 1, (0.0, 0.0), 0.0, seed=3)
    assert ground_z(few, (0.0, 0.0, 1.5)) is None


def _package(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    rng = np.random.default_rng(7)
    field = np.column_stack(
        [rng.uniform(0, 10, 20000), rng.uniform(0, 8, 20000), rng.normal(0, 0.003, 20000)]
    )
    xyz = np.vstack([field, _ring_ground(500, (4.0, 3.0), 0.0, seed=8)])
    rgb = np.full((len(xyz), 3), 30000, dtype=np.uint16)
    _write_las(pkg / "cloud.copc.laz", xyz, rgb)
    (pkg / "project.json").write_text(
        json.dumps(
            {
                "schemaVersion": 1,
                "cloud": {
                    "url": "cloud.copc.laz",
                    "boundsWorld": [xyz.min(axis=0).tolist(), xyz.max(axis=0).tolist()],
                    "attributes": ["rgb"],
                },
                "stations": {"url": "stations.json", "count": 1},
            }
        )
    )
    (pkg / "stations.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "stations": [
                    {
                        "id": "S001",
                        "pose": {"translation": [4.0, 3.0, 1.4], "rotationQuat": [1, 0, 0, 0]},
                        "groundZ": None,
                        "panorama": None,
                        "images": [],
                    }
                ],
            }
        )
    )
    return pkg


def test_build_overview_writes_topview_manifest_and_ground(tmp_path):
    pkg = _package(tmp_path)
    res = build_overview(pkg, pixel_m=0.1)
    tv = json.loads((pkg / "project.json").read_text())["topview"]
    assert tv["url"] == "topview.png" and tv["pixelAnchor"] == "edge" and tv["colorFrom"] == "rgb"
    img = Image.open(pkg / "topview.png")
    assert img.mode == "RGBA" and img.size == (tv["widthPx"], tv["heightPx"])
    assert tv["pixelSizeM"] == pytest.approx(0.1)
    gz = json.loads((pkg / "stations.json").read_text())["stations"][0]["groundZ"]
    assert gz is not None and abs(gz) < 0.01
    assert res["groundZ"] == {"S001": gz}


def test_build_overview_z_max_is_recorded(tmp_path):
    pkg = _package(tmp_path)
    build_overview(pkg, pixel_m=0.1, z_max=0.5)
    assert json.loads((pkg / "project.json").read_text())["topview"]["zMaxWorld"] == 0.5


def test_build_overview_lehnt_nan_ab_statt_ungueltiges_json_zu_schreiben(tmp_path):
    """build_overview schreibt project.json über write_manifest (allow_nan=False) — ein NaN
    (hier über z_max) soll laut brechen statt als ungültiges JSON in project.json zu landen."""
    pkg = _package(tmp_path)
    before = (pkg / "project.json").read_text()
    with pytest.raises(ValueError):
        build_overview(pkg, pixel_m=0.1, z_max=float("nan"))
    assert (pkg / "project.json").read_text() == before


def test_overview_cli(tmp_path):
    pkg = _package(tmp_path)
    assert main(["overview", str(pkg), "--pixel-m", "0.2"]) == 0
    assert (pkg / "topview.png").is_file()


@pytest.mark.data
@pytest.mark.skipif(
    REFERENZ_PAKET is None or not REFERENZ_PAKET.exists(),
    reason="Paket der Pilotkirche fehlt (SCANVIEW_REFERENZ_PAKET)",
)
def test_referenz_overview_plausible(tmp_path):
    """Echtdaten, ohne das Paket zu verändern: Manifest/Standpunkte kopiert, Wolke verlinkt.

    Die Referenz-E57 der Pilotkirche ist auf einen gedrehten Kasten zugeschnitten (die
    Draufsicht zeigt schräge Schnittkanten) — Standpunkte, deren XY außerhalb von
    ``cloud.boundsWorld`` liegt, sind echt (kein Frame-/Georeferenzfehler) und bekommen bewusst
    kein ``groundZ`` (dort liegt keine Punktabdeckung, nichts wird geraten). Die ≥ 90-%-Abnahme
    gilt deshalb nur für Standpunkte MIT Punktabdeckung (XY innerhalb ``boundsWorld``); für
    Standpunkte außerhalb wird stattdessen ``groundZ is None`` verlangt.
    """
    assert REFERENZ_PAKET is not None
    pkg = tmp_path / "b"
    pkg.mkdir()
    for f in ("project.json", "stations.json"):
        (pkg / f).write_text((REFERENZ_PAKET / f).read_text())
    (pkg / "cloud.copc.laz").symlink_to(REFERENZ_PAKET / "cloud.copc.laz")
    build_overview(pkg)
    manifest = json.loads((pkg / "project.json").read_text())
    tv = manifest["topview"]
    assert max(tv["widthPx"], tv["heightPx"]) <= 2048
    bmin, bmax = cloud_of(manifest)["boundsWorld"]
    stations = json.loads((pkg / "stations.json").read_text())["stations"]

    def _inside_bounds(s: dict) -> bool:
        x, y, _ = s["pose"]["translation"]
        return bmin[0] <= x <= bmax[0] and bmin[1] <= y <= bmax[1]

    covered = [s for s in stations if _inside_bounds(s)]
    uncovered = [s for s in stations if not _inside_bounds(s)]
    heights = [
        s["pose"]["translation"][2] - s["groundZ"] for s in covered if s["groundZ"] is not None
    ]
    print(
        json.dumps(
            {
                "withGround": len(heights),
                "covered": len(covered),
                "uncovered": len(uncovered),
                "total": len(stations),
                "heights": heights,
            }
        )
    )
    # Außerhalb boundsWorld gibt es keine Punktabdeckung -> groundZ bleibt None.
    assert all(s["groundZ"] is None for s in uncovered)
    assert len(heights) >= 0.9 * len(covered)
    # Instrumentenhöhe auf dem Stativ: plausibel 0,3-2,5 m.
    assert sum(0.3 <= h <= 2.5 for h in heights) >= 0.9 * len(heights)
