from dataclasses import replace

import numpy as np
import pytest
from test_mesh_stages import grid

from scanview_import.mesh_convert import convert_mesh
from scanview_import.mesh_source import read_mesh


def test_only_exact_degenerates_removed(tmp_path):
    from scanview_import.mesh_stages import remove_degenerate

    mesh = grid(tmp_path, 3)
    positions = np.vstack([mesh.positions, [[0, 0, 0], [1, 0, 0], [2, 0, 0], [1, 1e-10, 0]]])
    faces = np.vstack([mesh.triangles, [[0, 0, 1], [9, 10, 11], [9, 10, 12]]])
    mesh = replace(
        mesh,
        positions=positions,
        uv=None,
        triangles=faces,
        materials=np.zeros(len(faces), dtype=np.int32),
    )
    output, report = remove_degenerate(mesh)
    assert report["removed"] == 2
    assert report["duplicateIndices"] == 1
    assert report["zeroArea"] == 2
    assert len(output.triangles) == len(faces) - 2
    assert output.triangles[-1].tolist() == [9, 10, 12]


def test_conversion_removes_source_and_quantization_degenerates(tmp_path):
    p = tmp_path / "a.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nv 1 0.000001 0\nf 1 2 3\nf 1 1 2\nf 1 2 4\n")
    pkg = tmp_path / "pkg"
    manifest = convert_mesh(p, pkg, unit="m", dreiecke=0)
    import json

    report = json.loads((pkg / "layers/mesh/conversion-report.json").read_text())
    assert report["ok"]
    assert report["source"]["degenerateTriangles"] == 1
    assert manifest["layers"][0]["degenerateRemoval"]["source"]["removed"] == 1
    assert manifest["layers"][0]["degenerateRemoval"]["model"]["quantization"]["removed"] == 1
    output = read_mesh(pkg / "layers/mesh/model.glb")
    assert len(output.triangles) == 1
    assert report["stages"]["model"]["counts"]["degenerateTriangles"] == 0


def test_all_degenerate_rejected(tmp_path):
    p = tmp_path / "a.obj"
    p.write_text("v 0 0 0\nv 1 0 0\nv 2 0 0\nf 1 2 3\n")
    with pytest.raises(ValueError, match="entartet"):
        convert_mesh(p, tmp_path / "pkg", unit="m", dreiecke=0)
