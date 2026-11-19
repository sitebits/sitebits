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
    rasterize_bild,
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


def test_convert_punktwolke_schreibt_draufsicht(tmp_path: Path) -> None:
    # convert (LAS/LAZ ohne E57) erzeugt die Draufsicht selbst; vorher nur build.
    rng = np.random.default_rng(3)
    xyz = np.column_stack(
        [rng.uniform(0, 40, 4000), rng.uniform(0, 30, 4000), rng.uniform(0, 5, 4000)]
    )
    xyz += [500000.0, 5800000.0, 30.0]
    src = tmp_path / "wolke.laz"
    _write_las(src, xyz, np.full((len(xyz), 3), 30000, dtype=np.uint16))
    pkg = tmp_path / "pkg"
    argv = ["convert", str(src), str(pkg), "--crs", "EPSG:25833", "--mode", "direct"]
    argv += ["--max-px", "64"]
    assert main(argv) == 0
    top = json.loads((pkg / "project.json").read_text())["topview"]
    assert (pkg / top["url"]).is_file()
    assert max(top["widthPx"], top["heightPx"]) == 64
    assert top["colorFrom"] == "rgb"


def _georef(pkg: Path, wkt: str = "EPSG:25833") -> None:
    m = json.loads((pkg / "project.json").read_text())
    m["crs"] = {"wkt": wkt, "verticalDatum": "dhhn2016", "declaredBy": "file"}
    (pkg / "project.json").write_text(json.dumps(m))


def _bild(
    path: Path, rgba: np.ndarray, links: float, oben: float, px: float, wf: str = ".pgw"
) -> Path:
    Image.fromarray(rgba, "RGBA").save(path)
    mitte_x, mitte_y = links + px / 2, oben - px / 2
    path.with_suffix(wf).write_text(f"{px}\n0\n0\n{-px}\n{mitte_x}\n{mitte_y}\n")
    return path


def test_rasterize_bild_liegt_lagerichtig_und_ist_aussen_transparent(tmp_path):
    # Bild 4x4 m, 0,5-m-Pixel, oben links bei (2, 6): rechte obere Hälfte rot, Rest grün
    rgba = np.zeros((8, 8, 4), dtype=np.uint8)
    rgba[..., 1] = rgba[..., 3] = 255
    rgba[:4, 4:] = [255, 0, 0, 255]
    bild = _bild(tmp_path / "o.png", rgba, links=2.0, oben=6.0, px=0.5)
    grid = Grid(0.0, 8.0, 1.0, 8, 8)  # 1-m-Raster, (0..8) x (0..8)
    out = rasterize_bild(bild, grid)
    assert out[1, 1, 3] == 0 and out[7, 7, 3] == 0  # außerhalb: transparent
    assert tuple(out[2, 2]) == (0, 255, 0, 255)  # Pixel x 2..3, y 5..6 → grün
    assert tuple(out[2, 5]) == (255, 0, 0, 255)  # Pixel x 5..6, y 5..6 → rot
    assert tuple(out[5, 5]) == (0, 255, 0, 255)  # Pixel x 5..6, y 2..3 → grün


def test_rasterize_bild_halbes_pixel_genau(tmp_path):
    """Weltdateien nennen die Mitte des Pixels oben links, nicht seine Kante: ein Versatz um ein
    halbes Bildpixel muss auffallen. Bild 1-m-Pixel ab (2, 6), Rot = 10·Spalte, Grün = 10·Zeile."""
    rgba = np.zeros((4, 4, 4), dtype=np.uint8)
    rgba[..., 0] = np.arange(4)[None, :] * 10
    rgba[..., 1] = np.arange(4)[:, None] * 10
    rgba[..., 3] = 255
    bild = _bild(tmp_path / "o.png", rgba, links=2.0, oben=6.0, px=1.0)
    out = rasterize_bild(bild, Grid(0.0, 8.0, 0.5, 16, 16))  # 0,5-m-Raster
    assert out[3:7, 4, 3].tolist() == [0, 255, 255, 255]  # Zeile 3 (y 6..6,5) außen, Zeile 4 innen
    assert out[5, 3:8, 3].tolist() == [0, 255, 255, 255, 255]
    assert out[5, 4:8, 0].tolist() == [0, 0, 10, 10]  # x 2..3 → Bildspalte 0
    assert out[4:8, 5, 1].tolist() == [0, 0, 10, 10]


def test_rasterize_bild_mittelt_beim_verkleinern_ohne_dunklen_rand(tmp_path):
    rgba = np.zeros((40, 40, 4), dtype=np.uint8)
    rgba[:, :20] = [200, 200, 200, 255]  # linke Hälfte deckt, rechte ist transparent
    bild = _bild(tmp_path / "o.png", rgba, links=0.0, oben=4.0, px=0.1)
    out = rasterize_bild(bild, Grid(0.0, 4.0, 0.4, 10, 10))
    assert out[0, 0, 3] == 255 and out[0, 9, 3] == 0
    deckt = out[..., 3] >= 128
    assert np.all(out[deckt, :3] >= 195)  # vormultipliziert gemittelt: Rand nicht abgedunkelt


def test_build_overview_bild_ersetzt_wolke_nur_wo_es_deckt(tmp_path):
    pkg = _package(tmp_path)
    _georef(pkg)
    rgba = np.zeros((40, 50, 4), dtype=np.uint8)
    rgba[:, :25] = [255, 0, 0, 255]  # nur die linke Hälfte des Bildes deckt
    bild = _bild(tmp_path / "ortho.png", rgba, links=0.0, oben=8.0, px=0.1)
    build_overview(pkg, pixel_m=0.2, bild=bild, bild_crs="epsg:25833")
    tv = json.loads((pkg / "project.json").read_text())["topview"]
    assert tv["colorFrom"] == "rgb" and tv["image"]["source"] == "ortho.png"
    assert 0.10 < tv["image"]["coverage"] < 0.16  # 2,5 x 4 m von rund 10 x 8 m
    img = np.asarray(Image.open(pkg / "topview.png"))
    assert tuple(img[10, 2]) == (255, 0, 0, 255)  # Bild
    assert tuple(img[10, -3]) == (117, 117, 117, 255)  # Wolke (30000 >> 8)


@pytest.mark.parametrize(
    ("crs_paket", "crs_bild", "meldung"),
    [(None, "EPSG:25833", "georeferenzierten"), ("EPSG:25833", None, "--bild-crs"),
     ("EPSG:25833", "EPSG:25832", "--bild-crs")],
)
def test_build_overview_bild_rät_kein_koordinatensystem(tmp_path, crs_paket, crs_bild, meldung):
    pkg = _package(tmp_path)
    if crs_paket:
        _georef(pkg, crs_paket)
    bild = _bild(tmp_path / "o.png", np.full((4, 4, 4), 255, np.uint8), 0.0, 8.0, 1.0)
    before = (pkg / "project.json").read_text()
    with pytest.raises(ValueError, match=meldung):
        build_overview(pkg, pixel_m=0.2, bild=bild, bild_crs=crs_bild)
    assert (pkg / "project.json").read_text() == before


def test_rasterize_bild_ohne_oder_mit_gedrehter_weltdatei_abgelehnt(tmp_path):
    Image.new("RGBA", (4, 4)).save(tmp_path / "o.png")
    with pytest.raises(ValueError, match="Keine Weltdatei"):
        rasterize_bild(tmp_path / "o.png", Grid(0.0, 4.0, 1.0, 4, 4))
    (tmp_path / "o.pgw").write_text("1\n0.1\n0\n-1\n0.5\n3.5\n")
    with pytest.raises(ValueError, match="gedreht"):
        rasterize_bild(tmp_path / "o.png", Grid(0.0, 4.0, 1.0, 4, 4))


def test_overview_cli_mit_bild(tmp_path):
    pkg = _package(tmp_path)
    _georef(pkg)
    bild = _bild(tmp_path / "o.png", np.full((8, 10, 4), 255, np.uint8), 0.0, 8.0, 1.0)
    rc = main(
        ["overview", str(pkg), "--pixel-m", "0.2", "--bild", str(bild), "--bild-crs", "EPSG:25833"]
    )
    assert rc == 0
    assert json.loads((pkg / "project.json").read_text())["topview"]["image"]["coverage"] > 0.9
