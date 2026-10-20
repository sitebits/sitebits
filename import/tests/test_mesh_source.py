from pathlib import Path

import numpy as np
import pytest

from scanview_import.mesh_source import read_mesh, resolve_mesh_source


def obj(path: Path) -> Path:
    path.write_text("v 100 200 300 1 0 0\nv 101 200 300 0 1 0\nv 100 202 300 0 0 1\nf 1 2 3\n")
    return path


def test_obj_unit_colors_negative_indices(tmp_path):
    p = obj(tmp_path / "a.obj")
    with pytest.raises(ValueError, match="Einheit"):
        read_mesh(p)
    mesh = read_mesh(p, unit="mm")
    np.testing.assert_allclose(mesh.positions[1], [0.101, 0.2, 0.3])
    np.testing.assert_array_equal(mesh.colors, np.eye(3))
    p.write_text(p.read_text().replace("f 1 2 3", "f -3 -2 -1"))
    np.testing.assert_array_equal(read_mesh(p, unit="m").triangles, [[0, 1, 2]])


def test_metadata_conflicts_and_sidecar(tmp_path):
    p = obj(tmp_path / "a.obj")
    p.with_name("a.obj.scanview.json").write_text('{"unit":"cm","crs":"EPSG:25833"}')
    assert resolve_mesh_source(p).unit == "cm"
    with pytest.raises(ValueError, match="Einheiten"):
        read_mesh(p, unit="m")
    with pytest.raises(ValueError, match="CRS"):
        read_mesh(p, crs="EPSG:4326")


def test_missing_texture(tmp_path):
    p = obj(tmp_path / "a.obj")
    p.write_text("mtllib a.mtl\nusemtl stone\n" + p.read_text())
    (tmp_path / "a.mtl").write_text("newmtl stone\nmap_Kd missing.jpg\n")
    with pytest.raises(ValueError, match=r"missing\.jpg"):
        read_mesh(p, unit="m")


def test_uv_seam_is_split(tmp_path):
    p = obj(tmp_path / "a.obj")
    p.write_text(
        p.read_text().split("f ")[0]
        + "vt 0 0\nvt 1 0\nvt 0 1\nvt .5 .5\nf 1/1 2/2 3/3\nf 1/4 3/3 2/2\n"
    )
    m = read_mesh(p, unit="m")
    assert len(m.positions) == 4
    assert len(m.triangles) == 2


@pytest.mark.parametrize("binary", [False, True])
def test_stl(tmp_path, binary):
    p = tmp_path / "a.stl"
    if binary:
        import struct

        p.write_bytes(b" " * 80 + struct.pack("<I12fH", 1, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 2, 0, 0))
    else:
        p.write_text(
            "solid a\nfacet normal 0 0 1\nouter loop\n"
            "vertex 0 0 0\nvertex 1 0 0\nvertex 0 2 0\n"
            "endloop\nendfacet\nendsolid a\n"
        )
    m = read_mesh(p, unit="m", up="y")
    np.testing.assert_allclose(m.positions[m.triangles[0, 2]], [0, 0, 2])
    assert len(m.triangles) == 1


@pytest.mark.parametrize("binary", [False, True])
def test_ply_mesh(tmp_path, binary):
    import struct

    p = tmp_path / "a.ply"
    encoding = "binary_little_endian" if binary else "ascii"
    header = (
        "ply\nformat " + encoding + " 1.0\nelement vertex 3\n"
        "property float x\nproperty float y\nproperty float z\n"
        "element face 1\nproperty list uchar int vertex_indices\nend_header\n"
    )
    payload = (
        struct.pack("<9fB3i", 0, 0, 0, 1, 0, 0, 0, 2, 0, 3, 0, 1, 2)
        if binary
        else b"0 0 0\n1 0 0\n0 2 0\n3 0 1 2\n"
    )
    p.write_bytes(header.encode() + payload)
    m = read_mesh(p, unit="m")
    np.testing.assert_allclose(m.positions, [[0, 0, 0], [1, 0, 0], [0, 2, 0]])
    assert m.triangles.shape == (1, 3)


def test_gltf_y_up(tmp_path):
    from gltf_fixture import build_glb

    p = tmp_path / "a.glb"
    p.write_bytes(
        build_glb(
            np.array([[0, 1, 2], [1, 1, 2], [0, 3, 2]]),
            np.array([[0, 1, 2]]),
            write_as_zup_directly=True,
        )
    )
    m = read_mesh(p)
    np.testing.assert_allclose(m.positions[0], [0, -2, 1])
    assert m.source.unit == "m"
    assert m.source.up == "y"
    with pytest.raises(ValueError, match="Einheiten"):
        read_mesh(p, unit="mm")


def test_gltf_node_transform_and_material_color(tmp_path):
    import pygltflib as g
    from gltf_fixture import build_glb

    p = tmp_path / "a.glb"
    p.write_bytes(
        build_glb(
            np.array([[0, 0, 0], [1, 0, 0], [0, 2, 0]]),
            np.array([[0, 1, 2]]),
            write_as_zup_directly=True,
        )
    )
    doc = g.GLTF2().load(str(p))
    doc.nodes[0].translation = [3, 4, 5]
    doc.nodes[0].scale = [2, 3, 4]
    doc.materials = [
        g.Material(pbrMetallicRoughness=g.PbrMetallicRoughness(baseColorFactor=[0.2, 0.4, 0.6, 1]))
    ]
    doc.meshes[0].primitives[0].material = 0
    doc.save_binary(str(p))
    m = read_mesh(p)
    np.testing.assert_allclose(m.positions, [[3, -5, 4], [5, -5, 4], [3, -5, 10]])
    np.testing.assert_allclose(m.colors, np.tile([0.2, 0.4, 0.6], (3, 1)))
    with pytest.raises(ValueError, match="Achsen"):
        read_mesh(p, up="z")
