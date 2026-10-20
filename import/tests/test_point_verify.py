import json

import laspy
import numpy as np
import pytest
from test_point_formats import write_points

from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of, read_manifest, write_manifest
from scanview_import.verify import verify


@pytest.mark.docker
@pytest.mark.parametrize("fmt", ["las", "laz", "ply", "pts", "xyz", "txt", "csv", "copc.laz"])
def test_verify_point_sources(tmp_path, fmt):
    xyz = np.random.default_rng(0).uniform(-4, 4, (1300, 3))
    if fmt == "copc.laz":
        intermediate = write_points(tmp_path / "source.las", "las", xyz)
        convert(intermediate, tmp_path / "first", None)
        path = tmp_path / "first" / "cloud.copc.laz"
        kwargs = {}
    else:
        path = write_points(tmp_path / ("source." + fmt), fmt, xyz)
        kwargs = {} if fmt in ("las", "laz") else {"unit": "m"}
        if fmt in ("xyz", "txt", "csv"):
            kwargs["columns"] = "x,y,z"
    pkg = tmp_path / "pkg"
    convert(path, pkg, None, **kwargs)
    r = verify(path, pkg, samples=200)
    assert r["ok"] is True
    assert r["pointBalance"]["written"] == r["pointBalance"]["expected"] == 1300
    assert r["bounds"]["ok"] is True
    assert r["samples"]["n"] == 200 and r["samples"]["seed"] == 42
    assert r["pointSource"]["checked"] is False
    assert r["source"]["unit"] == "m"
    assert json.loads((pkg / "conversion-report.json").read_text()) == r


def rewrite(pkg, fn):
    path = pkg / "cloud.copc.laz"
    las = laspy.read(path)
    las.vlrs = [v for v in las.vlrs if v.user_id != "copc"]
    las.evlrs = None
    fn(las)
    las.write(path)


@pytest.mark.docker
def test_verify_detects_missing_points_with_forged_manifest(tmp_path):
    path = write_points(
        tmp_path / "s.las", "las", np.random.default_rng(0).uniform(0, 4, (1500, 3))
    )
    pkg = tmp_path / "pkg"
    convert(path, pkg, None)
    rewrite(pkg, lambda las: setattr(las, "points", las.points[10:]))
    m = read_manifest(pkg)
    cloud_of(m)["pointCount"] = 1490
    write_manifest(pkg, m)
    r = verify(path, pkg, samples=100)
    assert r["pointBalance"]["ok"] is False and r["ok"] is False


@pytest.mark.docker
def test_verify_detects_shift_and_bounds(tmp_path):
    path = write_points(
        tmp_path / "s.csv", "csv", np.random.default_rng(0).uniform(0, 4, (1500, 3))
    )
    pkg = tmp_path / "pkg"
    convert(path, pkg, None, unit="cm", up="y", columns="x,y,z")
    rewrite(pkg, lambda las: setattr(las, "x", np.asarray(las.x) + 2))
    r = verify(path, pkg, samples=100)
    assert r["pointBalance"]["ok"] is True
    assert r["bounds"]["ok"] is False
    assert r["samples"]["ok"] is False and r["ok"] is False


@pytest.mark.docker
@pytest.mark.parametrize("e57", [False, True])
def test_verify_thinned_reverse_membership(tmp_path, two_scan_e57, e57):
    path = (
        two_scan_e57.path
        if e57
        else write_points(
            tmp_path / "s.las", "las", np.random.default_rng(0).uniform(0, 1, (5000, 3))
        )
    )
    pkg = tmp_path / "pkg"
    convert(path, pkg, None, ausduennen=0.2)
    r = verify(path, pkg, samples=200)
    assert r["ok"] is True
    assert r["samples"]["direction"] == "copc-to-source"
    assert r["samples"]["maxErrorM"] <= r["samples"]["toleranceM"]
    assert r["pointBalance"]["read"] == (2000 if e57 else 5000)
    rewrite(pkg, lambda las: setattr(las, "x", np.asarray(las.x) + 0.025))
    r = verify(path, pkg, samples=200)
    assert r["samples"]["ok"] is False and r["ok"] is False


@pytest.mark.docker
def test_verify_does_not_accept_different_source(tmp_path):
    path = write_points(
        tmp_path / "s.las", "las", np.random.default_rng(0).uniform(0, 1, (1000, 3))
    )
    pkg = tmp_path / "pkg"
    convert(path, pkg, None)
    write_points(path, "las", np.random.default_rng(1).uniform(0, 1, (1000, 3)))
    r = verify(path, pkg, samples=100)
    assert r["sourceIdentity"]["ok"] is False
    assert r["ok"] is False
