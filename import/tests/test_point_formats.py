import json

import laspy
import numpy as np
import pytest
from pyproj import CRS
from scipy.spatial import cKDTree

from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of
from scanview_import.pdal_runner import build_point_pipeline
from scanview_import.point_source import resolve_source


def write_points(path, fmt, xyz):
    if fmt in ("las", "laz"):
        h = laspy.LasHeader(point_format=7, version="1.4")
        h.scales = [0.0001] * 3
        las = laspy.LasData(h)
        las.x, las.y, las.z = xyz.T
        las.red, las.green, las.blue = [65535] * len(xyz), [25700] * len(xyz), [0] * len(xyz)
        las.write(path)
    elif fmt == "ply":
        with path.open("w") as f:
            f.write(
                "ply\nformat ascii 1.0\nelement vertex " + str(len(xyz)) + "\n"
                "property double x\nproperty double y\nproperty double z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
            )
            np.savetxt(f, np.column_stack([xyz, np.tile([255, 100, 0], (len(xyz), 1))]))
    elif fmt == "pts":
        with path.open("w") as f:
            f.write(str(len(xyz)) + "\n")
            np.savetxt(f, np.column_stack([xyz, np.tile([0, 255, 100, 0], (len(xyz), 1))]))
    else:
        np.savetxt(path, xyz, delimiter="," if fmt == "csv" else " ")
    return path


def test_point_pipeline_transform(tmp_path):
    src = write_points(tmp_path / "a.csv", "csv", np.array([[100.0, 200.0, 300.0]]))
    source = resolve_source(src, unit="cm", up="y", columns="x,y,z")
    pipe = build_point_pipeline(source, "/src/a.csv", "/out/a.laz", (0.0, 0.0, 0.0))
    assert pipe[0]["type"] == "readers.text"
    assert pipe[0]["header"] == "X,Y,Z"
    mat = np.array([float(v) for v in pipe[1]["matrix"].split()]).reshape(4, 4)
    np.testing.assert_allclose(mat @ [100, 200, 300, 1], [1, -3, 2, 1])


@pytest.mark.docker
@pytest.mark.parametrize("fmt", ["las", "laz", "ply", "pts", "xyz", "txt", "csv"])
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_direct_point_formats(tmp_path, fmt, mode):
    xyz = np.random.default_rng(42).uniform(-5, 5, (1200, 3))
    src = write_points(tmp_path / ("source." + fmt), fmt, xyz)
    kwargs = {} if fmt in ("las", "laz") else {"unit": "m"}
    if fmt in ("xyz", "txt", "csv"):
        kwargs["columns"] = "x,y,z"
    pkg = tmp_path / "pkg"
    m = convert(src, pkg, None, mode=mode, **kwargs)
    cloud = cloud_of(m)
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    assert len(got) == len(xyz) == cloud["pointCount"]
    assert cKDTree(got).query(xyz)[0].max() <= 0.0015
    assert cloud["pointSourceIsScan"] is False
    assert not any(layer["kind"] == "panoramas" for layer in m["layers"])
    assert np.all(las.point_source_id == 0)
    assert cloud["source"]["format"] == fmt
    assert cloud["provenance"]["scaleVerified"] is False
    rep = json.loads((pkg / "conversion-report.json").read_text())
    assert rep["points"] == {"raw": 1200, "thinned": 1200, "written": 1200}
    if fmt in ("ply", "pts"):
        assert np.all(las.red == 65535)
        assert np.all(las.green == 25700)


@pytest.mark.docker
def test_ply_scaled_and_rotated(tmp_path):
    xyz = np.random.default_rng(42).uniform(-500, 500, (1000, 3))
    src = write_points(tmp_path / "p.ply", "ply", xyz)
    pkg = tmp_path / "pkg"
    convert(src, pkg, None, unit="cm", up="y")
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    expected = xyz[:, [0, 2, 1]] * [0.01, -0.01, 0.01]
    assert cKDTree(got).query(expected)[0].max() <= 0.0015


@pytest.mark.docker
@pytest.mark.parametrize("fmt", ["csv", "las", "ply", "pts"])
def test_max_points(tmp_path, fmt):
    xyz = np.random.default_rng(0).uniform(-4, 4, (2500, 3))
    path = write_points(tmp_path / ("source." + fmt), fmt, xyz)
    kwargs = {} if fmt == "las" else {"unit": "m"}
    if fmt == "csv":
        kwargs["columns"] = "x,y,z"
    m = convert(path, tmp_path / "pkg", None, max_points=123, **kwargs)
    assert cloud_of(m)["pointCount"] == 123


@pytest.mark.docker
def test_csv_header_and_reordered_columns(tmp_path):
    path = tmp_path / "source.csv"
    path.write_text("z,x,y,r,g,b\n3,1,2,65535,25700,0\n4,2,3,65535,25700,0\n")
    m = convert(path, tmp_path / "pkg", None, unit="m", columns="z,x,y,r,g,b")
    assert cloud_of(m)["pointCount"] == 2
    las = laspy.read(tmp_path / "pkg" / "cloud.copc.laz")
    assert sorted(las.x) == [1.0, 2.0]
    assert np.all(las.red == 65535)


@pytest.mark.docker
@pytest.mark.parametrize("encoding", ["binary_little_endian", "binary_big_endian"])
def test_binary_ply(tmp_path, encoding):
    path = tmp_path / "source.ply"
    dtype = np.dtype(
        [(a, ("<" if encoding == "binary_little_endian" else ">") + "f8") for a in "xyz"]
        + [("red", "u1"), ("green", "u1"), ("blue", "u1")]
    )
    rec = np.zeros(500, dtype=dtype)
    for a in "xyz":
        rec[a] = np.arange(500) * 0.02
    rec["red"] = 255
    with path.open("wb") as f:
        f.write(
            (
                "ply\nformat " + encoding + " 1.0\nelement vertex 500\n"
                "property double x\nproperty double y\nproperty double z\n"
                "property uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n"
            ).encode()
        )
        f.write(rec.tobytes())
    m = convert(path, tmp_path / "pkg", None, unit="m")
    assert cloud_of(m)["pointCount"] == 500
    assert np.all(laspy.read(tmp_path / "pkg" / "cloud.copc.laz").red == 65535)


def test_never_overwrite_source_copc(tmp_path):
    path = write_points(
        tmp_path / "cloud.copc.laz", "laz", np.random.default_rng(0).uniform(0, 1, (100, 3))
    )
    original = path.read_bytes()
    with pytest.raises(ValueError, match="Quelldatei"):
        convert(path, tmp_path, None)
    assert path.read_bytes() == original


def _las_rgb(path, rot, mit_farbe=True):
    header = laspy.LasHeader(point_format=7 if mit_farbe else 6, version="1.4")
    header.scales = [0.001] * 3
    header.offsets = [400_000, 5_800_000, 30]
    header.add_crs(CRS.from_epsg(32633))
    las = laspy.LasData(header)
    xyz = np.random.default_rng(1).uniform(0, 5, (len(rot), 3)) + [400_000, 5_800_000, 30]
    las.x, las.y, las.z = xyz.T
    if mit_farbe:
        las.red = las.green = las.blue = np.asarray(rot, np.uint16)
    las.write(path)
    return path


def test_rgb_8bit_nur_mit_werten_bis_255(tmp_path):
    from scanview_import.point_convert import pruefe_rgb_8bit
    from scanview_import.source_points import inspect_points

    for name, rot, mit_farbe, fehler in [
        ("ok.las", [0, 17, 255], True, None),
        ("hoch.las", [0, 256, 3], True, "Farbwert 256"),
        ("grau.las", [0, 0, 0], False, "nur für LAS/LAZ mit Farbe"),
    ]:
        src = resolve_source(_las_rgb(tmp_path / name, rot, mit_farbe), None, None, "z", None)
        if fehler is None:
            pruefe_rgb_8bit(src, inspect_points(src))
        else:
            with pytest.raises(ValueError, match=fehler):
                pruefe_rgb_8bit(src, inspect_points(src))


@pytest.mark.docker
def test_rgb_8bit_wird_skaliert(tmp_path):
    src = _las_rgb(tmp_path / "odm.laz", [0, 1, 128, 255] * 50)
    m = convert(src, tmp_path / "pkg", None, rgb_8bit=True)
    las = laspy.read(tmp_path / "pkg" / "cloud.copc.laz")
    assert sorted(set(las.red.tolist())) == [0, 257, 128 * 257, 65535]
    assert cloud_of(m)["pointCount"] == 200
    with pytest.raises(ValueError, match="nur für LAS/LAZ"):
        convert(tmp_path / "x.e57", tmp_path / "pkg2", None, rgb_8bit=True)
