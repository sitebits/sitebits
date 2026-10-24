"""Höhensystem im Punktimport: Umrechnung beim convert, gleiche Umrechnung im verify."""

from __future__ import annotations

from pathlib import Path

import laspy
import numpy as np
import pytest
from scipy.spatial import cKDTree

from scanview_import import cli, hoehen
from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of
from scanview_import.point_verify import verify_points


def _laz_potsdam(path: Path, n: int = 800) -> np.ndarray:
    rng = np.random.default_rng(7)
    xyz = np.column_stack(
        [
            rng.uniform(368000, 368050, n),
            rng.uniform(5807000, 5807050, n),
            rng.uniform(70, 90, n),
        ]
    )
    h = laspy.LasHeader(point_format=7, version="1.4")
    h.scales = [0.001] * 3
    h.offsets = [368000.0, 5807000.0, 0.0]
    las = laspy.LasData(h)
    las.x, las.y, las.z = xyz.T
    las.write(path)
    return xyz


def test_cli_ziel_ohne_quelle_bricht_ab(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "a.laz"
    _laz_potsdam(src, 10)
    rc = cli.main(["convert", str(src), str(tmp_path / "pkg"), "--ziel-hoehensystem", "dhhn2016"])
    assert rc == 1
    assert "Quellhöhensystem" in capsys.readouterr().err
    assert not (tmp_path / "pkg" / "project.json").exists()


def test_e57_umrechnung_noch_nicht(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="E57"):
        convert(
            tmp_path / "scan.e57",
            tmp_path / "pkg",
            None,
            hoehe=("ellipsoidal-etrs89", "dhhn2016"),
        )


@pytest.mark.docker
def test_convert_rechnet_um_und_verify_vergleicht_gleich(tmp_path: Path) -> None:
    src = tmp_path / "wolke.laz"
    xyz = _laz_potsdam(src)
    pkg = tmp_path / "pkg"

    m = convert(src, pkg, "EPSG:25833", mode="direct", hoehe=("ellipsoidal-etrs89", "dhhn2016"))

    assert m["frame"]["crs"]["verticalDatum"] == "dhhn2016"
    opts = cloud_of(m)["sourceOptions"]
    assert opts["hoehe"]["quelle"] == "ellipsoidal-etrs89"
    assert opts["hoehe"]["gitterSha256"] == hoehen.GITTER_SHA256
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    soll = xyz.copy()
    soll[:, 2] = hoehen.rechne_um(
        xyz[:, 0], xyz[:, 1], xyz[:, 2], crs=25833, quelle="ellipsoidal-etrs89", ziel="dhhn2016"
    )
    assert cKDTree(got).query(soll)[0].max() <= 0.0015
    assert 39.0 < float(np.mean(xyz[:, 2]) - np.mean(got[:, 2])) < 41.5

    rep = verify_points(src, pkg, samples=500)
    assert rep["ok"], rep


@pytest.mark.docker
def test_nur_deklarieren_ohne_umrechnung(tmp_path: Path) -> None:
    src = tmp_path / "wolke.laz"
    xyz = _laz_potsdam(src, 200)
    m = convert(src, tmp_path / "pkg", "EPSG:25833", mode="direct", hoehe=("dhhn2016", "dhhn2016"))
    assert m["frame"]["crs"]["verticalDatum"] == "dhhn2016"
    las = laspy.read(tmp_path / "pkg" / "cloud.copc.laz")
    assert float(np.mean(las.z)) == pytest.approx(float(np.mean(xyz[:, 2])), abs=0.001)


def test_panos_kameras_rechnet_kamerahoehe_ins_paket_um(tmp_path: Path) -> None:
    import json

    from tests.test_kameras import camera_file
    from tests.test_kameras_cli import package

    source = camera_file(tmp_path)
    data = json.loads(source.read_text())
    data["crs"] = {"epsg": 25833, "verticalDatum": "ellipsoidisch"}
    data["cameras"][0]["position"] = [368000.0, 5807000.0, 100.0]
    source.write_text(json.dumps(data))
    pkg = package(tmp_path, wkt="EPSG:25833", datum="dhhn2016")
    manifest = json.loads((pkg / "project.json").read_text())
    manifest["frame"]["originWorld"] = [368000.0, 5807000.0, 0.0]
    (pkg / "project.json").write_text(json.dumps(manifest))

    rc = cli.main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "32"])

    assert rc == 0
    station = json.loads((pkg / "stations.json").read_text())["stations"][0]
    assert station["pose"]["translation"][2] == pytest.approx(59.841306648269935, abs=1e-6)


def test_panos_kameras_ohne_umrechenbares_system_bricht_ab(tmp_path: Path) -> None:
    from tests.test_kameras import camera_file
    from tests.test_kameras_cli import package

    source = camera_file(tmp_path)  # EPSG:32632, ellipsoidisch
    pkg = package(tmp_path, wkt="EPSG:32632", datum="dhhn92")
    rc = cli.main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "32"])
    assert rc != 0
    assert not (pkg / "stations.json").exists()


def test_frame_export_tie_points_werden_mit_umgerechnet() -> None:
    from scanview_import.kameras import FrameCamera, FrameExport, TieProjection, hoehen_umrechnen

    kamera = FrameCamera(
        "A", "", Path("/x.jpg"), 10, 10, (368000.0, 5807000.0, 100.0), np.eye(3), 0
    )
    export = FrameExport(
        epsg=25833,
        vertical_datum="ellipsoidisch",
        cameras=[kamera],
        sensors={},
        tie_points=np.array([[368010.0, 5807010.0, 90.0]]),
        tie_projections=[TieProjection("A", (1.0, 2.0), (368010.0, 5807010.0, 90.0))],
        skipped=[],
        max_referenz_abweichung_px=1.0,
    )
    neu = hoehen_umrechnen(export, "ellipsoidal-etrs89", "dhhn2016")
    n = 100.0 - neu.cameras[0].position[2]
    assert 40.0 < n < 40.3
    assert neu.tie_points[0, 2] == pytest.approx(neu.tie_projections[0].world[2])
    assert 90.0 - neu.tie_points[0, 2] == pytest.approx(n, abs=0.01)
    assert neu.vertical_datum == "dhhn2016"
    np.testing.assert_array_equal(neu.cameras[0].rotation, np.eye(3))


def _mesh_potsdam(path: Path) -> Path:
    path.write_text(
        "v 368000 5807000 100 1 0 0\n"
        "v 368001 5807000 100 0 1 0\n"
        "v 368000 5807002 100 0 0 1\n"
        "v 368000.2 5807000.4 103 1 1 0\n"
        "f 1 3 2\nf 1 2 4\nf 2 3 4\nf 3 1 4\n"
    )
    return path


def test_mesh_convert_rechnet_um_und_cli_verify_ist_gruen(tmp_path: Path) -> None:
    import json

    from scanview_import.mesh_convert import convert_mesh
    from scanview_import.mesh_source import read_mesh

    src = _mesh_potsdam(tmp_path / "a.obj")
    pkg = tmp_path / "pkg"
    m = convert_mesh(
        src, pkg, crs="EPSG:25833", unit="m", dreiecke=0, hoehe=("ellipsoidisch", None)
    )
    assert m["frame"]["crs"]["verticalDatum"] == "dhhn2016"
    layer = m["layers"][0]
    assert layer["sourceOptions"]["hoehe"]["ziel"] == "dhhn2016"
    model = read_mesh(pkg / layer["url"])
    z = model.positions[:, 2] + m["frame"]["originWorld"][2]
    assert 59.0 < float(z.min()) < 60.5

    rc = cli.main(["verify", str(src), str(pkg)])
    assert rc == 0
    assert json.loads((pkg / "project.json").read_text())["layers"][0]["verify"]["ok"] is True


@pytest.mark.parametrize(
    ("hoehe", "paket", "erwartet"),
    [
        ((None, None), "dhhn2016", (None, None)),
        (("ellipsoidisch", None), "dhhn2016", ("ellipsoidal-etrs89", "dhhn2016")),
        (("ellipsoidisch", None), "DHHN2016", ("ellipsoidal-etrs89", "dhhn2016")),
    ],
)
def test_add_mesh_ziel_kommt_aus_dem_paket(
    hoehe: tuple[str | None, str | None], paket: str, erwartet: tuple[str | None, str | None]
) -> None:
    from scanview_import.mesh_convert import _add_hoehe

    assert _add_hoehe(hoehe, paket) == erwartet


@pytest.mark.parametrize(
    ("hoehe", "paket"),
    [
        (("ellipsoidisch", None), None),
        (("ellipsoidisch", "ellipsoidisch"), "dhhn2016"),
        ((None, "dhhn2016"), "dhhn2016"),
    ],
)
def test_add_mesh_hoehe_fehler(hoehe: tuple[str | None, str | None], paket: str | None) -> None:
    from scanview_import.mesh_convert import _add_hoehe

    with pytest.raises(ValueError):
        _add_hoehe(hoehe, paket)


@pytest.mark.docker
def test_e57_nur_deklarieren(tmp_path: Path, two_scan_e57) -> None:  # type: ignore[no-untyped-def]
    m = convert(
        two_scan_e57.path, tmp_path / "p", crs=None, mode="direct", hoehe=("dhhn2016", "dhhn2016")
    )
    assert m["frame"]["crs"]["verticalDatum"] == "dhhn2016"
