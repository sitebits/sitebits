import numpy as np
import pytest
from scipy.spatial import cKDTree

from scanview_import.mesh_convert import convert_mesh
from scanview_import.mesh_source import read_mesh


def asymmetric_mesh(path):
    path.write_text(
        "v 5800000 400000 10 1 0 0\n"
        "v 5800001 400000 10 0 1 0\n"
        "v 5800000 400002 10 0 0 1\n"
        "v 5800000.2 400000.4 13 1 1 0\n"
        "f 1 3 2\nf 1 2 4\nf 2 3 4\nf 3 1 4\n"
    )
    return path


def test_convert_frame_exact(tmp_path):
    src = asymmetric_mesh(tmp_path / "a.obj")
    pkg = tmp_path / "pkg"
    m = convert_mesh(src, pkg, unit="m", dreiecke=0)
    layer = m["layers"][0]
    assert m["schemaVersion"] == 2
    assert layer["kind"] == "mesh"
    assert layer["triangleCount"] == 4
    assert layer["verify"]["ok"] is True
    model = read_mesh(pkg / layer["url"])
    source = read_mesh(src, unit="m")
    assert (
        cKDTree(source.positions).query(model.positions + m["frame"]["originWorld"])[0].max()
        <= 0.001
    )
    assert model.colors is not None
    with pytest.raises(FileExistsError):
        convert_mesh(src, pkg, unit="m")


def test_cli_convert_routes_mesh(tmp_path):
    from scanview_import.cli import main

    src = asymmetric_mesh(tmp_path / "a.obj")
    assert main(["convert", str(src), str(tmp_path / "pkg"), "--unit", "m", "--dreiecke", "0"]) == 0


def test_textured_cube_real_ktx(tmp_path):
    from gltf_fixture import box_source_mesh_data, gradient_texture_bytes

    from scanview_import.mesh_textures import ktx_info

    vertices, faces, uv = box_source_mesh_data()
    p = tmp_path / "cube.obj"
    lines = ["mtllib cube.mtl", "usemtl stone"]
    for v in vertices:
        lines.append("v " + " ".join(str(x) for x in v))
    for face_uv in uv:
        for u in face_uv:
            lines.append("vt " + " ".join(str(x) for x in u))
    for i, face in enumerate(faces):
        lines.append("f " + " ".join(f"{ix + 1}/{i * 3 + j + 1}" for j, ix in enumerate(face)))
    p.write_text("\n".join(lines) + "\n")
    (tmp_path / "cube.mtl").write_text("newmtl stone\nmap_Kd gradient.png\n")
    (tmp_path / "gradient.png").write_bytes(gradient_texture_bytes())
    pkg = tmp_path / "pkg"
    m = convert_mesh(p, pkg, unit="m", dreiecke=0)
    layer = m["layers"][0]
    assert layer["textures"]["encoding"] == "ktx2-uastc"
    assert layer["textures"]["count"] == 1
    import json

    report = json.loads((pkg / layer["verify"]["report"]).read_text())
    assert report["ok"]
    output = read_mesh(pkg / "layers/mesh/model.glb")
    textures = [t for t in output.textures if t is not None]
    assert ktx_info(textures[0])["encoding"] == "ktx2-uastc"
    assert report["stages"]["model"]["textures"]["meanAbsDeltaRGB"][0] < 10
    preview = read_mesh(pkg / layer["previewUrl"])
    assert preview.colors is not None and preview.uv is None
    assert all(t is None for t in preview.textures)
    assert layer["previewColor"] == "vertex"
    assert layer["textures"]["stages"]["preview"] == []
    assert report["stages"]["preview"]["colors"]["samples"] == 1000
    assert report["stages"]["preview"]["colors"]["meanAbsDeltaRGB"] is not None


def test_texturierte_vollstufe_ohne_vertexfarben(tmp_path):
    """Vertexfarben plus Textur: glTF multipliziert beide — die Vollstufe trägt nur die Textur,
    die Vorschau (ohne Textur) behält die Vertexfarben."""
    from gltf_fixture import box_source_mesh_data, gradient_texture_bytes
    from pygltflib import GLTF2

    vertices, faces, uv = box_source_mesh_data()
    p = tmp_path / "cube.obj"
    lines = ["mtllib cube.mtl", "usemtl stone"]
    lines += ["v " + " ".join(str(x) for x in v) + " 0.8 0.6 0.4" for v in vertices]
    lines += ["vt " + " ".join(str(x) for x in u) for face_uv in uv for u in face_uv]
    for i, face in enumerate(faces):
        lines.append("f " + " ".join(f"{ix + 1}/{i * 3 + j + 1}" for j, ix in enumerate(face)))
    p.write_text("\n".join(lines) + "\n")
    (tmp_path / "cube.mtl").write_text("newmtl stone\nmap_Kd gradient.png\n")
    (tmp_path / "gradient.png").write_bytes(gradient_texture_bytes())
    pkg = tmp_path / "pkg"
    layer = convert_mesh(p, pkg, unit="m", dreiecke=0)["layers"][0]
    assert layer["verify"]["ok"] is True
    voll = GLTF2().load(str(pkg / layer["url"]))
    assert all(prim.attributes.COLOR_0 is None for m in voll.meshes for prim in m.primitives)
    assert all(prim.attributes.TEXCOORD_0 is not None for m in voll.meshes for prim in m.primitives)
    vorschau = GLTF2().load(str(pkg / layer["previewUrl"]))
    prims = [prim for m in vorschau.meshes for prim in m.primitives]
    assert all(prim.attributes.COLOR_0 is not None for prim in prims)


def test_sphere_simplification_measured(tmp_path):
    nlat, nlon, radius = 24, 48, 0.1
    lines = []
    for j in range(nlat + 1):
        theta = np.pi * j / nlat
        for i in range(nlon):
            phi = 2 * np.pi * i / nlon
            lines.append(
                f"v {radius * np.sin(theta) * np.cos(phi)} "
                f"{radius * np.sin(theta) * np.sin(phi)} {radius * np.cos(theta)}"
            )
    for j in range(nlat):
        for i in range(nlon):
            a = j * nlon + i + 1
            b = j * nlon + (i + 1) % nlon + 1
            lines.extend([f"f {a} {b} {a + nlon}", f"f {b} {b + nlon} {a + nlon}"])
    p = tmp_path / "sphere.obj"
    p.write_text("\n".join(lines) + "\n")
    m = convert_mesh(p, tmp_path / "pkg", unit="m", dreiecke=1000, vorschau_dreiecke=300)
    assert abs(m["layers"][0]["triangleCount"] / 1000 - 1) <= 0.05
    assert m["layers"][0]["verify"]["ok"]
