import numpy as np
import pygltflib as g
import pytest
from scipy.spatial import cKDTree
from test_mesh_stages import grid

from scanview_import.mesh_gltf import save_glb_document, write_glb
from scanview_import.mesh_source import read_mesh


def test_meshopt_quantized_roundtrip(tmp_path):
    mesh = grid(tmp_path)
    p = tmp_path / "model.glb"
    origin = np.array([5800000.0, 400000.0, 10.0])
    mesh.positions += origin
    step = write_glb(mesh, p, origin, {})
    doc = g.GLTF2().load(str(p))
    assert {"EXT_meshopt_compression", "KHR_mesh_quantization"} <= set(doc.extensionsRequired)
    accessor = doc.accessors[doc.meshes[0].primitives[0].attributes.POSITION]
    assert accessor.componentType == g.UNSIGNED_SHORT
    for view in doc.bufferViews:
        if view.target:
            ext = view.extensions["EXT_meshopt_compression"]
            assert view.buffer == 1
            assert view.byteLength == ext["count"] * ext["byteStride"]
    output = read_mesh(p)
    assert step < 0.001
    distances, original = cKDTree(mesh.positions).query(output.positions + origin)
    assert distances.max() < 0.001

    # Layout and cyclic rotations can change; directed geometry/UV association must survive.
    def canonical(faces):
        rotated = np.array([np.roll(f, -int(np.argmin(f))) for f in faces])
        return rotated[np.lexsort(rotated.T[::-1])]

    np.testing.assert_array_equal(canonical(original[output.triangles]), canonical(mesh.triangles))
    np.testing.assert_allclose(output.uv, mesh.uv[original], atol=1 / 65535, rtol=0)
    assert p.stat().st_size < mesh.positions.nbytes + mesh.triangles.nbytes


def test_corrupt_compressed_payload_is_rejected(tmp_path):
    mesh = grid(tmp_path)
    p = tmp_path / "model.glb"
    write_glb(mesh, p, np.zeros(3), {})
    doc = g.GLTF2().load(str(p))
    ext = doc.bufferViews[0].extensions["EXT_meshopt_compression"]
    blob = bytearray(doc.binary_blob())
    blob[ext["byteOffset"]] = 0
    save_glb_document(doc, bytes(blob), p)
    with pytest.raises((ValueError, RuntimeError)):
        read_mesh(p)


def test_large_extent_preserves_millimetre_controls(tmp_path):
    mesh = grid(tmp_path)
    mesh.positions *= 100
    path = tmp_path / "large.glb"
    step = write_glb(mesh, path, np.zeros(3), {})
    output = read_mesh(path)
    assert step == 0.00025
    assert cKDTree(mesh.positions).query(output.positions)[0].max() <= 0.001
    doc = g.GLTF2().load(str(path))
    assert "EXT_meshopt_compression" in doc.extensionsRequired
    accessor = doc.accessors[doc.meshes[0].primitives[0].attributes.POSITION]
    assert accessor.componentType == g.FLOAT
