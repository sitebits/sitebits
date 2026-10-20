"""Rahmen-Gate an synthetischer LAS; Paket wird bei Fehllage nicht verändert."""

import json
from pathlib import Path

import laspy
import numpy as np
import pytest

from scanview_import.splat_convert import add_splats
from scanview_import.splat_source import Gaussians, write_gaussians


def fixture_package(tmp_path: Path) -> tuple[Path, Path]:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    xy = np.array([(x / 100, y / 100, 0) for x in range(32) for y in range(32)])
    header = laspy.LasHeader(point_format=3, version="1.2")
    header.scales = [0.001] * 3
    header.offsets = [500000, 5800000, 40]
    las = laspy.LasData(header)
    world = xy + header.offsets
    las.x, las.y, las.z = world.T
    las.write(pkg / "cloud.las")
    manifest = {
        "schemaVersion": 2,
        "projectId": "fixture",
        "revision": 1,
        "frame": {
            "unit": "m",
            "originWorld": list(header.offsets),
            "crs": {"wkt": None, "verticalDatum": None},
        },
        "layers": [{"id": "punkte", "kind": "pointcloud", "url": "cloud.las"}],
    }
    (pkg / "project.json").write_text(json.dumps(manifest))
    g = Gaussians(
        world,
        np.full((1024, 3), 0.01),
        np.tile([0.0, 0, 0, 1], (1024, 1)),
        np.full(1024, 0.9),
        np.full((1024, 3), 0.6),
    )
    source = tmp_path / "source.ply"
    # Float64 PLY input to preserve world coords, output must be local Float32.
    names = [
        "x",
        "y",
        "z",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
        "opacity",
        "scale_0",
        "scale_1",
        "scale_2",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
    ]
    rows = np.column_stack(
        [
            g.centers,
            (g.colors - 0.5) / 0.28209479177387814,
            np.full(1024, np.log(9)),
            np.log(g.scales),
            g.quaternions[:, [3, 0, 1, 2]],
        ]
    ).astype("<f8")
    source.write_bytes(
        (
            "ply\nformat binary_little_endian 1.0\nelement vertex 1024\n"
            + "".join(f"property double {n}\n" for n in names)
            + "end_header\n"
        ).encode()
        + rows.tobytes()
    )
    return pkg, source


def test_add_verifies_delivered_frame_and_sets_start_layer(tmp_path):
    pkg, source = fixture_package(tmp_path)
    result = add_splats(source, pkg, same_frame=True, initial=True)
    layer = result["layers"][-1]
    assert layer["frameCheck"]["ok"] is True
    assert layer["frameCheck"]["p95M"] < 0.000212
    assert layer["sourceOptions"]["shDegreeDelivered"] == 0
    assert result["initialView"]["layers"] == ["splats"]
    assert (pkg / layer["url"]).is_file()
    assert result["revision"] == 2


def test_wrong_frame_never_publishes(tmp_path):
    pkg, source = fixture_package(tmp_path)
    original = (pkg / "project.json").read_bytes()
    from scanview_import.splat_source import read_gaussians

    g = read_gaussians(source)
    from dataclasses import replace

    write_gaussians(source, replace(g, centers=g.centers + [10, 0, 0]), np.zeros(3))
    with pytest.raises(ValueError, match="frameCheck"):
        add_splats(source, pkg, same_frame=True)
    assert (pkg / "project.json").read_bytes() == original
    assert not (pkg / "layers/splats").exists()


def test_lage_moves_centers_and_quaternions_like_package_cloud(tmp_path):
    pkg, source = fixture_package(tmp_path)
    manifest = json.loads((pkg / "project.json").read_text())
    matrix = np.eye(4)
    matrix[0, 3] = 0.04
    manifest["layers"][0]["sourceOptions"] = {"lage": {"matrix": matrix.tolist()}}
    (pkg / "project.json").write_text(json.dumps(manifest))
    las = laspy.read(pkg / "cloud.las")
    las.x = np.asarray(las.x) + 0.04
    las.write(pkg / "cloud.las")
    result = add_splats(source, pkg, same_frame=True)
    assert result["layers"][-1]["sourceOptions"]["lage"]["matrix"] == matrix.tolist()
    assert result["layers"][-1]["boundsWorld"][0][0] == pytest.approx(500000.04)
    assert result["layers"][-1]["frameCheck"]["p95M"] < 0.001


def test_camera_way_aligns_local_gaussians_with_scale(tmp_path):
    from dataclasses import replace

    from scanview_import.splat_source import read_gaussians

    pkg, source = fixture_package(tmp_path)
    origin = np.array([500000, 5800000, 40])
    g = read_gaussians(source)
    local_g = replace(g, centers=(g.centers - origin) / 2)
    write_gaussians(source, local_g, np.zeros(3))
    centers = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    colmap = tmp_path / "images.txt"
    colmap.write_text(
        "".join(
            f"{i + 1} 1 0 0 0 {-c[0]} {-c[1]} {-c[2]} 1 image-{i}.png\n\n"
            for i, c in enumerate(centers)
        )
    )
    csv = tmp_path / "world.csv"
    csv.write_text(
        "label,E,N,H\n"
        + "".join(
            f"image-{i}.png,{w[0]},{w[1]},{w[2]}\n" for i, w in enumerate(centers * 2 + origin)
        )
    )
    result = add_splats(source, pkg, colmap=colmap, cameras=csv)
    layer = result["layers"][-1]
    assert layer["alignment"]["scale"] == pytest.approx(2)
    assert layer["frameCheck"]["p95M"] < 0.001
    delivered = read_gaussians(pkg / layer["url"])
    np.testing.assert_allclose(delivered.scales, local_g.scales * 2, rtol=0.032)


def test_default_delivers_spz_and_reports_quantization(tmp_path):
    pkg, source = fixture_package(tmp_path)
    result = add_splats(source, pkg, same_frame=True)
    layer = result["layers"][-1]
    assert layer["format"] == "spz"
    assert layer["chunkCount"] == 1
    report = json.loads((pkg / layer["verify"]["report"]).read_text())
    assert report["positionMaxM"] <= report["positionToleranceM"]
    assert report["positionStepM"] == 2**-12
    assert report["streaming"] is False


def test_camera_option_is_explicit_and_does_not_replace_existing_view(tmp_path):
    pkg, source = fixture_package(tmp_path)
    camera = tmp_path / "camera.csv"
    camera.write_text("label,E,N,H\na,500001,5800001,50\nb,500002,5800002,52\n")
    result = add_splats(source, pkg, same_frame=True, start_cameras=camera, initial=True)
    assert result["initialView"]["position"] == [500001.5, 5800001.5, 51.0]
    assert result["layers"][-1]["sourceOptions"]["initialCameraPolicy"] == "median-world-centers"


def test_camera_option_preserves_declared_view(tmp_path):
    pkg, source = fixture_package(tmp_path)
    manifest = json.loads((pkg / "project.json").read_text())
    view = {"target": [500000, 5800000, 40], "distance": 10}
    manifest["initialView"] = view
    (pkg / "project.json").write_text(json.dumps(manifest))
    camera = tmp_path / "camera.csv"
    camera.write_text("label,E,N,H\na,500001,5800001,50\n")
    result = add_splats(source, pkg, same_frame=True, start_cameras=camera)
    assert result["initialView"] == view


def test_explicit_ply_fallback_keeps_old_precision(tmp_path):
    pkg, source = fixture_package(tmp_path)
    layer = add_splats(source, pkg, same_frame=True, output_format="ply")["layers"][-1]
    assert layer["format"] == "ply"
    assert layer["frameCheck"]["p95M"] < 1e-6


def test_import_keeps_sh3_through_lage_and_spz(tmp_path):
    from dataclasses import replace

    from scipy.spatial.transform import Rotation

    from scanview_import.splat_sh import sh_basis
    from scanview_import.splat_source import read_gaussians

    pkg, source = fixture_package(tmp_path)
    g = read_gaussians(source)
    # Move source coordinates to zero, rotate into the known synthetic cloud via camera frame.
    origin = np.array([500000, 5800000, 40])
    r = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    sh = np.zeros((1024, 15, 3))
    sh[:, 2, 0] = 0.3
    sh[:, 6, 1] = 0.2
    sh[:, 12, 2] = -0.1
    local = replace(g, centers=(g.centers - origin) @ r, sh_degree=3, sh_rest=sh)
    write_gaussians(source, local, np.zeros(3))
    centers = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]])
    colmap = tmp_path / "images.txt"
    colmap.write_text(
        "".join(
            f"{i + 1} 1 0 0 0 {-c[0]} {-c[1]} {-c[2]} 1 image-{i}.png\n\n"
            for i, c in enumerate(centers)
        )
    )
    cameras = tmp_path / "world.csv"
    cameras.write_text(
        "label,E,N,H\n"
        + "".join(
            f"image-{i}.png,{w[0]},{w[1]},{w[2]}\n" for i, w in enumerate(centers @ r.T + origin)
        )
    )
    result = add_splats(source, pkg, colmap=colmap, cameras=cameras)
    layer = result["layers"][-1]
    delivered = read_gaussians(pkg / layer["url"])
    assert layer["shDegree"] == 3
    assert layer["sourceOptions"]["shPolicy"] == "rotate-and-preserve"
    direction = np.array([[1.0, 0, 0]])
    np.testing.assert_allclose(
        sh_basis(direction @ r, 3) @ local.sh_rest[0],
        sh_basis(direction, 3) @ delivered.sh_rest[0],
        atol=0.004,
    )


def test_corrupted_encoded_positions_never_publish(tmp_path, monkeypatch):
    from dataclasses import replace

    import scanview_import.splat_convert as converter

    pkg, source = fixture_package(tmp_path)
    original = (pkg / "project.json").read_bytes()
    encoder = converter.write_spz

    def shifted_encoder(path, g, origin):
        encoder(path, replace(g, centers=g.centers + [0.01, 0, 0]), origin)

    monkeypatch.setattr(converter, "write_spz", shifted_encoder)
    with pytest.raises(ValueError, match="Positionsfehler"):
        add_splats(source, pkg, same_frame=True)
    assert (pkg / "project.json").read_bytes() == original
    assert not (pkg / "layers/splats").exists()


def test_missing_rad_tool_does_not_modify_package(tmp_path):
    pkg, source = fixture_package(tmp_path)
    before = (pkg / "project.json").read_bytes()
    with pytest.raises(ValueError, match="RAD benötigt"):
        add_splats(source, pkg, same_frame=True, output_format="rad")
    with pytest.raises(ValueError, match="build-lod fehlt"):
        add_splats(
            source, pkg, same_frame=True, output_format="rad", build_lod=tmp_path / "missing-tool"
        )
    assert (pkg / "project.json").read_bytes() == before
    assert not (pkg / "layers/splats").exists()


def test_wrong_frame_rejected_before_external_tool(tmp_path, monkeypatch):
    from dataclasses import replace

    import scanview_import.splat_convert as converter
    from scanview_import.splat_source import read_gaussians

    pkg, source = fixture_package(tmp_path)
    write_gaussians(
        source, replace(read_gaussians(source), centers=np.full((1024, 3), 10.0)), np.zeros(3)
    )

    def forbidden(*args):
        raise AssertionError("Encoder must not run before frame evidence")

    monkeypatch.setattr(converter, "build_rad", forbidden)
    with pytest.raises(ValueError, match="frameCheck"):
        add_splats(source, pkg, same_frame=True, output_format="rad", build_lod=tmp_path / "tool")
    assert not (pkg / "layers/splats").exists()


def test_large_source_without_encoder_keeps_spz_limit_and_package(tmp_path, monkeypatch):
    from dataclasses import replace

    import scanview_import.splat_convert as converter
    from scanview_import.splat_source import read_gaussians

    pkg, source = fixture_package(tmp_path)
    before = (pkg / 'project.json').read_bytes()
    g = read_gaussians(source)
    # Zero-copy large count exercises the gate before transformation/encoding allocations.
    large = replace(g, centers=np.broadcast_to(g.centers[:1], (1_000_001, 3)))
    monkeypatch.setattr(converter, 'read_gaussians', lambda _: large)
    monkeypatch.delenv('SCANVIEW_BUILD_LOD', raising=False)
    with pytest.warns(UserWarning, match='build-lod fehlt'):
        with pytest.raises(ValueError, match='Ohne RAD'):
            add_splats(source, pkg, same_frame=True)
    assert (pkg / 'project.json').read_bytes() == before
    assert not (pkg / 'layers/splats').exists()


def test_unknown_encoder_hash_is_not_executed_or_published(tmp_path):
    pkg, source = fixture_package(tmp_path)
    tool = tmp_path / 'build-lod'
    tool.write_text('#!/bin/sh\nexit 99\n')
    tool.chmod(0o700)
    before = (pkg / 'project.json').read_bytes()
    with pytest.raises(ValueError, match='SHA256'):
        add_splats(source, pkg, same_frame=True, output_format='rad', build_lod=tool)
    assert (pkg / 'project.json').read_bytes() == before
    assert not (pkg / 'layers/splats').exists()
