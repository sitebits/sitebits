"""Lageanpassung: Angaben lesen, LAS in einem Durchgang anpassen, Kameras aus dem Paket."""

from __future__ import annotations

import json
import math
from pathlib import Path

import laspy
import numpy as np
import pytest

from scanview_import import cli, hoehen, lage
from scanview_import.convert import convert

DREHPUNKT = np.array([368025.0, 5807025.0, 40.0])


def _laz(path: Path, n: int = 500) -> np.ndarray:
    rng = np.random.default_rng(3)
    xyz = np.column_stack(
        [rng.uniform(368000, 368050, n), rng.uniform(5807000, 5807050, n), rng.uniform(70, 90, n)]
    )
    h = laspy.LasHeader(point_format=7, version="1.4")
    h.scales = [0.001] * 3
    h.offsets = [368000.0, 5807000.0, 0.0]
    las = laspy.LasData(h)
    las.x, las.y, las.z = xyz.T
    las.red = rng.integers(0, 65535, n)
    las.classification = rng.integers(0, 7, n)
    las.write(path)
    return xyz


def _kippung(grad_x: float, grad_y: float, t: tuple[float, float, float]) -> np.ndarray:
    """Drehung um x und y am DREHPUNKT, danach Verschiebung t; absolute 4x4-Matrix."""
    ax, ay = math.radians(grad_x), math.radians(grad_y)
    rx = np.array([[1, 0, 0], [0, math.cos(ax), -math.sin(ax)], [0, math.sin(ax), math.cos(ax)]])
    ry = np.array([[math.cos(ay), 0, math.sin(ay)], [0, 1, 0], [-math.sin(ay), 0, math.cos(ay)]])
    r = ry @ rx
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = DREHPUNKT - r @ DREHPUNKT + np.asarray(t)
    return m


def _datei(tmp_path: Path, m: np.ndarray, **extra: object) -> Path:
    f = tmp_path / "lage_transformation.json"
    daten = {"matrix_4x4": m.tolist(), "drehpunkt": DREHPUNKT.tolist(), "referenz": "DGM1"}
    f.write_text(json.dumps(daten | extra))
    return f


def test_lies_verschiebung_text() -> None:
    v = lage.lies_verschiebung("-0.62,0.81")
    assert v.art == "verschiebung"
    np.testing.assert_allclose(v.matrix[:3, 3], [-0.62, 0.81, 0])
    np.testing.assert_array_equal(v.matrix[:3, :3], np.eye(3))


def test_lies_verschiebung_datei_mit_herkunft(tmp_path: Path) -> None:
    f = tmp_path / "lage_verschiebung.json"
    f.write_text(json.dumps({"dx_m": -0.6, "dy_m": -0.8, "referenz": "LoD2+DOP20", "punkte": 9}))
    v = lage.lies_verschiebung(str(f))
    assert v.herkunft == {
        "dx": -0.6,
        "dy": -0.8,
        "referenz": "LoD2+DOP20",
        "punkte": 9,
        "datei": "lage_verschiebung.json",
    }
    block = lage.quellblock(v)
    assert block["art"] == "verschiebung" and block["dx"] == -0.6
    np.testing.assert_allclose(lage.aus_paket({"sourceOptions": {"lage": block}}), v.matrix)


@pytest.mark.parametrize("angabe", ["1", "a,b", "1,2,3", "nan,0", "150,0", "0,-100.5"])
def test_lies_verschiebung_lehnt_ab(angabe: str) -> None:
    with pytest.raises(ValueError):
        lage.lies_verschiebung(angabe)


def test_lies_verschiebung_datei_ohne_werte(tmp_path: Path) -> None:
    f = tmp_path / "v.json"
    f.write_text(json.dumps({"dx": 1}))
    with pytest.raises(ValueError, match="nicht lesbar"):
        lage.lies_verschiebung(str(f))


def test_lies_transformation(tmp_path: Path) -> None:
    m = _kippung(0.5, -0.6, (-1.34, -0.63, 0.4))
    t = lage.lies_transformation(str(_datei(tmp_path, m, punkte=12)))
    assert t.art == "transformation"
    np.testing.assert_allclose(t.matrix, m)
    assert t.herkunft["punkte"] == 12 and t.herkunft["datei"] == "lage_transformation.json"
    block = lage.quellblock(t)
    np.testing.assert_allclose(lage.aus_paket({"sourceOptions": {"lage": block}}), m)


def test_transformation_ohne_matrix(tmp_path: Path) -> None:
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"drehpunkt": [0, 0, 0]}))
    with pytest.raises(ValueError, match="matrix_4x4"):
        lage.lies_transformation(str(f))


@pytest.mark.parametrize(
    ("matrix", "meldung"),
    [
        (np.eye(3), "4x4"),
        (np.diag([1.001, 1, 1, 1]), "starre"),
        (np.diag([-1.0, 1, 1, 1]), "starre"),
        (_kippung(6.0, 0, (0, 0, 0)), "dreht"),
        (_kippung(0.5, 0, (0, 0, 120)), "versetzt"),
    ],
)
def test_transformation_lehnt_ab(matrix: np.ndarray, meldung: str) -> None:
    with pytest.raises(ValueError, match=meldung):
        lage.pruefe(matrix, DREHPUNKT)


def test_letzte_zeile_muss_affin_sein() -> None:
    m = np.eye(4)
    m[3, 0] = 1e-3
    with pytest.raises(ValueError, match="letzte Zeile"):
        lage.pruefe(m)


def test_wende_an_entspricht_matrixmultiplikation() -> None:
    m = _kippung(0.8, 0.4, (1.0, -2.0, 0.5))
    p = np.array([[368010.0, 5807040.0, 75.0], [368049.0, 5807001.0, 88.0]])
    soll = (m @ np.column_stack([p, np.ones(2)]).T).T[:, :3]
    np.testing.assert_allclose(lage.wende_an(p, m), soll, atol=1e-6)
    neu = lage.wende_an(p, m)  # starr: Abstände bleiben
    assert np.linalg.norm(neu[0] - neu[1]) == pytest.approx(np.linalg.norm(p[0] - p[1]), abs=1e-9)


def test_las_anpassen_verschiebt_nur_lage(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.laz", tmp_path / "b.laz"
    xyz = _laz(src)
    v = lage.lies_verschiebung("-0.6,0.8")
    lage.las_anpassen(src, dst, crs=25833, hoehe=None, matrix=v.matrix, chunk=128)
    a, b = laspy.read(src), laspy.read(dst)
    np.testing.assert_allclose(b.x, xyz[:, 0] - 0.6, atol=0.0006)
    np.testing.assert_allclose(b.y, xyz[:, 1] + 0.8, atol=0.0006)
    np.testing.assert_array_equal(b.Z, a.Z)
    np.testing.assert_array_equal(b.red, a.red)
    np.testing.assert_array_equal(b.classification, a.classification)
    assert b.header.mins[0] == pytest.approx(a.header.mins[0] - 0.6, abs=0.001)


def test_las_anpassen_hoehe_an_originallage_dann_matrix(tmp_path: Path) -> None:
    src, dst = tmp_path / "a.laz", tmp_path / "b.laz"
    _laz(src, 50)
    m = _kippung(0.6, 0.5, (-1.34, -0.63, 0.4))
    lage.las_anpassen(src, dst, crs=25833, hoehe=("ellipsoidal-etrs89", "dhhn2016"), matrix=m)
    a, b = laspy.read(src), laspy.read(dst)
    x, y, z = (np.asarray(v, dtype=float) for v in (a.x, a.y, a.z))
    nhn = hoehen.rechne_um(x, y, z, crs=25833, quelle="ellipsoidal-etrs89", ziel="dhhn2016")
    soll = lage.wende_an(np.column_stack([x, y, nhn]), m)
    np.testing.assert_allclose(np.column_stack([b.x, b.y, b.z]), soll, atol=0.0006)


def test_las_anpassen_hoehe_braucht_crs(tmp_path: Path) -> None:
    src = tmp_path / "a.laz"
    _laz(src, 5)
    with pytest.raises(ValueError, match="Koordinatensystem"):
        lage.las_anpassen(src, tmp_path / "b.laz", crs=None, hoehe=("a", "b"), matrix=None)


def test_aus_paket_ohne_lage() -> None:
    assert lage.aus_paket({"sourceOptions": {}}) is None


def test_e57_lage_noch_nicht(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="E57"):
        convert(tmp_path / "scan.e57", tmp_path / "pkg", None, lage=lage.lies_verschiebung("1,0"))


def test_cli_verschiebung_ungueltig(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "a.laz"
    _laz(src, 10)
    rc = cli.main(["convert", str(src), str(tmp_path / "pkg"), "--verschiebung", "1;2"])
    assert rc == 1
    assert "DX,DY" in capsys.readouterr().err
    assert not (tmp_path / "pkg" / "project.json").exists()


def test_cli_verschiebung_und_transformation_schliessen_sich_aus(tmp_path: Path) -> None:
    argv = ["convert", "a.laz", str(tmp_path / "pkg"), "--verschiebung", "1,2"]
    with pytest.raises(SystemExit):
        cli.main([*argv, "--transformation", "t.json"])


def test_frame_export_wird_mit_drehung_bewegt() -> None:
    from scanview_import.kameras import FrameCamera, FrameExport, TieProjection, lage_anwenden

    m = _kippung(0.7, -0.4, (-1.34, -0.63, 0.4))
    blick = np.eye(3)
    kamera = FrameCamera("A", "", Path("/x.jpg"), 10, 10, (368000.0, 5807000.0, 100.0), blick, 0)
    tie = np.array([[368010.0, 5807010.0, 90.0]])
    export = FrameExport(
        epsg=25833,
        vertical_datum="dhhn2016",
        cameras=[kamera],
        sensors={},
        tie_points=tie,
        tie_projections=[TieProjection("A", (1.0, 2.0), (368010.0, 5807010.0, 90.0))],
        skipped=[],
        max_referenz_abweichung_px=1.0,
    )
    neu = lage_anwenden(export, m)
    soll = lage.wende_an(np.array([kamera.position]), m)[0]
    np.testing.assert_allclose(neu.cameras[0].position, soll)
    np.testing.assert_allclose(neu.tie_points, lage.wende_an(tie, m))
    np.testing.assert_allclose(neu.tie_projections[0].world, neu.tie_points[0])
    assert neu.tie_projections[0].pixel == (1.0, 2.0)
    np.testing.assert_allclose(neu.cameras[0].rotation, m[:3, :3] @ blick)
    # Aus Sicht der Kamera liegt der Tie Point wie vorher: die Rückprojektion bleibt gleich.
    vorher = blick.T @ (tie[0] - np.array(kamera.position))
    nachher = neu.cameras[0].rotation.T @ (neu.tie_points[0] - np.array(neu.cameras[0].position))
    np.testing.assert_allclose(nachher, vorher, atol=1e-6)


def test_panos_kameras_uebernimmt_lage_aus_paket(tmp_path: Path) -> None:
    from tests.test_kameras import camera_file
    from tests.test_kameras_cli import package

    source = camera_file(tmp_path)
    data = json.loads(source.read_text())
    data["crs"] = {"epsg": 25833, "verticalDatum": "dhhn2016"}
    data["cameras"][0]["position"] = [368000.0, 5807000.0, 100.0]
    source.write_text(json.dumps(data))
    pkg = package(tmp_path, wkt="EPSG:25833", datum="dhhn2016")
    manifest = json.loads((pkg / "project.json").read_text())
    manifest["frame"]["originWorld"] = [368000.0, 5807000.0, 0.0]
    wolke = next(lay for lay in manifest["layers"] if lay["kind"] == "pointcloud")
    block = lage.quellblock(lage.lies_verschiebung("-0.6,0.8"))
    wolke.setdefault("sourceOptions", {})["lage"] = block
    (pkg / "project.json").write_text(json.dumps(manifest))

    rc = cli.main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "32"])

    assert rc == 0
    station = json.loads((pkg / "stations.json").read_text())["stations"][0]
    assert station["pose"]["translation"][:2] == pytest.approx([367999.4, 5807000.8], abs=1e-6)


@pytest.mark.docker
def test_convert_mit_transformation_und_verify_ist_gruen(tmp_path: Path) -> None:
    from scipy.spatial import cKDTree

    from scanview_import.manifest_io import cloud_of
    from scanview_import.point_verify import verify_points

    src = tmp_path / "wolke.laz"
    _laz(src)
    pkg = tmp_path / "pkg"
    a = laspy.read(src)
    x, y, z = (np.asarray(v, dtype=float) for v in (a.x, a.y, a.z))
    m = _kippung(0.6, 0.5, (-1.34, -0.63, 0.4))
    argv = ["convert", str(src), str(pkg), "--crs", "EPSG:25833", "--mode", "direct"]
    argv += ["--quell-hoehensystem", "ellipsoidisch", "--transformation", str(_datei(tmp_path, m))]

    assert cli.main(argv) == 0

    opts = cloud_of(json.loads((pkg / "project.json").read_text()))["sourceOptions"]
    assert opts["lage"]["art"] == "transformation" and opts["lage"]["referenz"] == "DGM1"
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    nhn = hoehen.rechne_um(x, y, z, crs=25833, quelle="ellipsoidal-etrs89", ziel="dhhn2016")
    soll = lage.wende_an(np.column_stack([x, y, nhn]), m)
    assert cKDTree(got).query(soll)[0].max() <= 0.0015
    rep = verify_points(src, pkg, samples=300)
    assert rep["ok"], rep
