import json

import laspy
import numpy as np
import pytest
from scipy.spatial import cKDTree
from test_point_formats import write_points

from scanview_import.cli import main
from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of


@pytest.mark.docker
@pytest.mark.parametrize("fmt", ["las", "ply", "csv", "e57"])
@pytest.mark.parametrize("mode", ["direct", "staged"])
def test_thinning_keeps_original_points(tmp_path, two_scan_e57, fmt, mode):
    if fmt == "e57":
        path = two_scan_e57.path
        expected = two_scan_e57.world_points_valid
        kwargs = {}
    else:
        expected = np.random.default_rng(42).uniform(0, 0.5, (4000, 3))
        path = write_points(tmp_path / ("source." + fmt), fmt, expected)
        kwargs = {} if fmt == "las" else {"unit": "m"}
        if fmt == "csv":
            kwargs["columns"] = "x,y,z"
    pkg = tmp_path / "pkg"
    m = convert(path, pkg, None, mode=mode, ausduennen=0.2, **kwargs)
    las = laspy.read(pkg / "cloud.copc.laz")
    got = np.column_stack([las.x, las.y, las.z])
    assert 0 < len(got) < len(expected)
    assert cKDTree(expected).query(got)[0].max() <= 0.0015
    assert cloud_of(m)["pointCount"] == len(got)
    rep = json.loads((pkg / "conversion-report.json").read_text())
    assert rep["points"]["written"] == rep["points"]["thinned"] == len(got)
    assert rep["thinning"]["cellM"] == 0.2
    if fmt == "e57":
        assert rep["points"]["raw"] == 2000
        assert rep["points"]["invalidDropped"] == 14
        assert np.all((las.point_source_id == 1) | (las.point_source_id == 2))


@pytest.mark.parametrize("cell", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_thinning_before_any_output(tmp_path, two_scan_e57, cell):
    pkg = tmp_path / "pkg"
    with pytest.raises(ValueError, match="Ausdünnung"):
        convert(two_scan_e57.path, pkg, None, ausduennen=cell)
    assert not pkg.exists()


def test_cli_thinning_help(capsys):
    with pytest.raises(SystemExit):
        main(["convert", "--help"])
    help_text = capsys.readouterr().out
    assert "--ausduennen" in help_text
    assert "gekachelt" in help_text and "40 GiB" in help_text
    assert "14 GiB" not in help_text


@pytest.mark.docker
@pytest.mark.parametrize("limit", [17, 10000])
def test_tiled_matches_global_filter_all_attributes(tmp_path, limit, monkeypatch):
    from scanview_import.pdal_runner import run_pipeline
    from scanview_import.tiled_thinning import thin_tiles

    # Nonzero origin; empty margins; exact boundaries; duplicate-coordinate ties.
    rng = np.random.default_rng(7)
    xyz = rng.integers(0, 160, (600, 3)) * 0.125 + [-13.25, 501.125, -2.75]
    xyz = np.vstack([xyz, xyz[:20], [-13.25, 501.125, -2.75]])
    cloud = laspy.LasData(laspy.LasHeader(point_format=7, version="1.4"))
    cloud.header.scales = [0.001, 0.001, 0.001]
    cloud.header.offsets = [-13.25, 501.125, -2.75]
    cloud.x, cloud.y, cloud.z = xyz.T
    cloud.intensity = np.arange(len(xyz))
    cloud.red = rng.integers(0, 65536, len(xyz), dtype=np.uint16)
    cloud.classification = rng.integers(0, 30, len(xyz), dtype=np.uint8)
    cloud.gps_time = rng.uniform(100000, 200000, len(xyz))
    cloud.green = rng.integers(0, 65536, len(xyz), dtype=np.uint16)
    cloud.blue = rng.integers(0, 65536, len(xyz), dtype=np.uint16)
    cloud.return_number = rng.integers(1, 4, len(xyz), dtype=np.uint8)
    cloud.number_of_returns = np.full(len(xyz), 4, dtype=np.uint8)
    cloud.scan_angle = rng.integers(-1000, 1000, len(xyz), dtype=np.int16)
    cloud.user_data = rng.integers(0, 256, len(xyz), dtype=np.uint8)
    for flag in [
        "synthetic",
        "key_point",
        "withheld",
        "overlap",
        "scan_direction_flag",
        "edge_of_flight_line",
    ]:
        cloud[flag] = rng.integers(0, 2, len(xyz), dtype=np.uint8)
    cloud.scanner_channel = rng.integers(0, 4, len(xyz), dtype=np.uint8)
    cloud.point_source_id = rng.integers(0, 10, len(xyz), dtype=np.uint16)
    cloud.write(tmp_path / "source.laz")
    pipe = [
        {"type": "readers.las", "filename": "/data/source.laz"},
        {"type": "filters.voxelcenternearestneighbor", "cell": 1.0},
        {"type": "writers.las", "filename": "/data/global.laz", "forward": "all"},
    ]
    stats = run_pipeline(pipe, {tmp_path: "/data"})
    assert stats.returncode == 0, stats.stderr_tail
    import scanview_import.tiled_thinning as tiled_module

    def bounded_pipeline(pipeline, mounts, stream=False):
        with laspy.open(next(iter(mounts)) / "input.las") as reader:
            assert reader.header.point_count <= limit + 1
        return run_pipeline(pipeline, mounts, stream=stream)

    monkeypatch.setattr(tiled_module, "run_pipeline", bounded_pipeline)
    thin_tiles(tmp_path / "source.laz", tmp_path / "tiled.laz", 1.0, max_points=limit)
    expected = laspy.read(tmp_path / "global.laz")
    actual = laspy.read(tmp_path / "tiled.laz")
    assert actual.points.array.dtype == expected.points.array.dtype
    np.testing.assert_array_equal(np.sort(actual.points.array), np.sort(expected.points.array))
    assert list(actual.header.point_format.extra_dimension_names) == []


@pytest.mark.docker
def test_tiled_dense_single_voxel_and_ties(tmp_path, monkeypatch):
    from scanview_import.tiled_thinning import thin_tiles

    cloud = laspy.LasData(laspy.LasHeader(point_format=7, version="1.4"))
    import scanview_import.tiled_thinning as tiled_module

    monkeypatch.setattr(tiled_module, "CHUNK", 7)
    cloud.x = np.tile([0.0, 0.5, 0.5], 100)
    cloud.y = np.tile([0.0, 0.5, 0.5], 100)
    cloud.z = np.tile([0.0, 0.5, 0.5], 100)
    cloud.intensity = np.arange(300)
    cloud.write(tmp_path / "dense.laz")
    thin_tiles(tmp_path / "dense.laz", tmp_path / "thin.laz", 1.0, max_points=7)
    out = laspy.read(tmp_path / "thin.laz")
    assert len(out.points) == 1
    assert out.intensity[0] == 1  # strict '<': first source record wins ties


@pytest.mark.docker
@pytest.mark.parametrize(
    "fmt,unit,up,cell",
    [
        ("csv", "cm", "y", 0.013),
        ("ply", "m", "z", 0.01),
        ("pts", "m", "z", 0.021),
    ],
)
def test_raw_coordinates_match_unquantized_global_selection(tmp_path, fmt, unit, up, cell):
    from scanview_import.pdal_runner import build_point_pipeline, run_pipeline
    from scanview_import.point_source import resolve_source
    from scanview_import.tiled_thinning import thin_tiles

    xyz = np.random.default_rng(17).uniform(-0.075, 0.075, (300, 3))
    path = write_points(tmp_path / ("source." + fmt), fmt, xyz)
    source = resolve_source(path, None, unit, up, "x,y,z" if fmt == "csv" else None)
    for name, kwargs in [
        ("global.laz", {"ausduennen": cell}),
        ("raw.laz", {"preserve_coordinates": True}),
    ]:
        stats = run_pipeline(
            build_point_pipeline(
                source, "/data/" + path.name, "/data/" + name, (0.0, 0.0, 0.0), **kwargs
            ),
            {tmp_path: "/data"},
            stream=name == "raw.laz" and fmt != "pts",
        )
        assert stats.returncode == 0, stats.stderr_tail
    thin_tiles(tmp_path / "raw.laz", tmp_path / "tiled.laz", cell, max_points=50)
    expected = laspy.read(tmp_path / "global.laz")
    actual = laspy.read(tmp_path / "tiled.laz")
    np.testing.assert_array_equal(np.sort(actual.points.array), np.sort(expected.points.array))


@pytest.mark.docker
def test_local_tile_origin_is_a_negative_control(tmp_path):
    from scanview_import.pdal_runner import run_pipeline
    from scanview_import.tiled_thinning import thin_tiles

    cloud = laspy.LasData(laspy.LasHeader(point_format=7, version="1.4"))
    cloud.x = [0, 10.1, 10.4, 10.7]
    cloud.y = cloud.z = [0, 0, 0, 0]
    cloud.intensity = [0, 1, 2, 3]
    cloud.write(tmp_path / "source.laz")
    cloud.points = cloud.points[1:]
    cloud.write(tmp_path / "local.laz")
    result = run_pipeline(
        [
            {"type": "readers.las", "filename": "/data/local.laz"},
            {"type": "filters.voxelcenternearestneighbor", "cell": 1.0},
            {"type": "writers.las", "filename": "/data/wrong.laz", "forward": "all"},
        ],
        {tmp_path: "/data"},
    )
    assert result.returncode == 0, result.stderr_tail
    thin_tiles(tmp_path / "source.laz", tmp_path / "correct.laz", 1.0, max_points=3)
    assert list(laspy.read(tmp_path / "wrong.laz").intensity) == [3]
    assert sorted(laspy.read(tmp_path / "correct.laz").intensity) == [0, 2]


@pytest.mark.docker
def test_selection_precedes_millimeter_quantization(tmp_path):
    from scanview_import.pdal_runner import build_point_pipeline, run_pipeline
    from scanview_import.point_source import resolve_source
    from scanview_import.tiled_thinning import thin_tiles

    path = tmp_path / "points.csv"
    path.write_text("0,0,0,0\n0.00045,0.00045,0.00045,1\n0.0006,0.0006,0.0006,2\n")
    source = resolve_source(path, None, "m", "z", "x,y,z,intensity")
    for name, kwargs in [
        ("global.laz", {"ausduennen": 0.001}),
        ("raw.laz", {"preserve_coordinates": True}),
    ]:
        result = run_pipeline(
            build_point_pipeline(
                source,
                "/data/points.csv",
                "/data/" + name,
                (0.0, 0.0, 0.0),
                **kwargs,
            ),
            {tmp_path: "/data"},
            stream=name == "raw.laz",
        )
        assert result.returncode == 0, result.stderr_tail
    thin_tiles(tmp_path / "raw.laz", tmp_path / "tiled.laz", 0.001, max_points=1)
    assert list(laspy.read(tmp_path / "global.laz").intensity) == [1]
    assert list(laspy.read(tmp_path / "tiled.laz").intensity) == [1]
