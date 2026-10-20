import json

import numpy as np
import pygltflib as g
import pytest

from scanview_import.mesh_convert import convert_mesh
from scanview_import.mesh_verify import verify_mesh


def setup(tmp_path):
    from test_mesh_convert import asymmetric_mesh

    src = asymmetric_mesh(tmp_path / "a.obj")
    pkg = tmp_path / "pkg"
    convert_mesh(src, pkg, unit="m", dreiecke=0)
    return src, pkg


@pytest.mark.parametrize("corruption", ["yz", "sign", "offset", "unit"])
def test_negative_frame_controls(tmp_path, corruption):
    src, pkg = setup(tmp_path)
    if corruption == "unit":
        m = json.loads((pkg / "project.json").read_text())
        m["layers"][0]["sourceOptions"]["unit"] = "mm"
        (pkg / "project.json").write_text(json.dumps(m))
    else:
        p = pkg / "layers/mesh/model.glb"
        doc = g.GLTF2().load(str(p))
        from scanview_import.mesh_gltf import save_glb_document

        node = doc.nodes[0]
        transform = np.eye(4)
        transform[:3, :3] = np.diag(node.scale)
        transform[:3, 3] = node.translation
        if corruption == "yz":
            transform[[1, 2]] = transform[[2, 1]]
        elif corruption == "sign":
            transform[1] *= -1
        else:
            transform[0, 3] += 0.1
        node.matrix = transform.T.ravel().tolist()
        node.scale = node.translation = None
        save_glb_document(doc, doc.binary_blob(), p)
    report = verify_mesh(src, pkg)
    assert not report["ok"]
    assert not report["stages"]["model"]["controls"]["ok"]


def test_verify_reports_both_directions_and_counts(tmp_path):
    src, pkg = setup(tmp_path)
    report = verify_mesh(src, pkg)
    assert report["ok"]
    assert report["source"]["triangleCount"] == 4
    for name in ("model", "preview"):
        stage = report["stages"][name]
        assert stage["samplesPerDirection"] == 10000
        assert stage["controls"]["count"] == 16
        assert stage["stageToSource"]["p95Mm"] < 1
        assert stage["sourceToStage"]["maxMm"] < 1
        assert stage["counts"]["degenerateTriangles"] == 0
        assert stage["sizeBytes"] > 0
    assert report["layerSizeBytes"] > 0
    assert report["hostPeakRssMb"] > 0


def test_missing_source_texture_stays_red(tmp_path):
    from test_mesh_source import obj

    src = obj(tmp_path / "a.obj")
    src.write_text("mtllib a.mtl\nusemtl stone\n" + src.read_text())
    (tmp_path / "a.mtl").write_text("newmtl stone\nmap_Kd gone.jpg\n")
    with pytest.raises(ValueError, match=r"gone\.jpg"):
        convert_mesh(src, tmp_path / "pkg", unit="m")
    assert not (tmp_path / "pkg").exists()


def test_verify_rechecks_frame_not_cached_manifest(tmp_path):
    from test_mesh_frame import package

    from scanview_import.mesh_convert import add_mesh

    src, pkg, _ = package(tmp_path)
    add_mesh(src, pkg, same_frame=True, unit="m", dreiecke=0)
    import laspy

    las = laspy.read(pkg / "cloud.las")
    las.z = las.z + 0.2
    las.write(pkg / "cloud.las")
    report = verify_mesh(src, pkg)
    assert not report["ok"]
    assert not report["frameCheck"]["ok"]


def test_unreachable_target_only_with_declared_count(tmp_path):
    from test_mesh_stages import zwei_inseln_je_quad

    from scanview_import.mesh_stages import simplify_with_report
    from scanview_import.mesh_verify import measure_stage

    m = zwei_inseln_je_quad(tmp_path)
    out, info = simplify_with_report(m, 50)
    assert info["zielErreicht"] is False
    assert measure_stage(m, out, 1.0, False, 50)["targetOk"] is False
    mit = measure_stage(m, out, 1.0, False, 50, info["erreicht"])
    assert mit["targetOk"] is True and mit["targetUnreachableDeclared"] is True
    assert measure_stage(m, out, 1.0, False, 50, info["erreicht"] + 1)["targetOk"] is False
