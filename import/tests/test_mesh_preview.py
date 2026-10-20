from dataclasses import replace

import numpy as np
from PIL import Image
from test_mesh_stages import zwei_inseln_je_quad

from scanview_import.mesh_stages import simplify_mesh


def test_welded_preview_reaches_target(tmp_path):
    from scanview_import.mesh_preview import vertex_preview

    mesh = zwei_inseln_je_quad(tmp_path)
    preview = vertex_preview(mesh)
    assert len(preview.positions) == 30 * 30
    assert preview.uv is None and preview.textures == [None]
    assert preview.colors.shape == (900, 3)
    assert abs(len(simplify_mesh(preview, 200).triangles) / 200 - 1) <= 0.05


def test_texture_priority_linear_average_and_exact_weld(tmp_path):
    from scanview_import.mesh_preview import vertex_preview

    mesh = zwei_inseln_je_quad(tmp_path, 3)
    texture = tmp_path / "colors.png"
    Image.fromarray(np.array([[[0, 0, 0], [255, 255, 255]]], dtype=np.uint8)).save(texture)
    uv = np.full((len(mesh.positions), 2), [0.75, 0.0])
    uv[1] = [0.25, 0.0]
    mesh = replace(mesh, uv=uv, colors=np.zeros_like(mesh.positions), textures=[texture])
    preview = vertex_preview(mesh)
    copies = np.all(mesh.positions == mesh.positions[1], axis=1)
    expected = (copies.sum() - 1) / copies.sum()
    ix = np.flatnonzero(np.all(preview.positions == mesh.positions[1], axis=1))[0]
    np.testing.assert_allclose(preview.colors[ix], expected)
    shifted = mesh.positions.copy()
    shifted[1, 0] += 1e-12
    assert len(vertex_preview(replace(mesh, positions=shifted)).positions) == 10


def test_shared_vertex_across_materials_averages_both_textures(tmp_path):
    from scanview_import.mesh_preview import vertex_preview

    mesh = zwei_inseln_je_quad(tmp_path, 3)
    # All three corners shared by two materials with different textures.
    mesh = replace(
        mesh,
        positions=mesh.positions[:3],
        uv=np.zeros((3, 2)),
        triangles=np.array([[0, 1, 2], [0, 1, 2]], dtype=np.uint32),
        materials=np.array([0, 1]),
    )
    textures = []
    for name, value in [("black", 0), ("white", 255)]:
        path = tmp_path / f"{name}.png"
        Image.fromarray(np.full((1, 1, 3), value, dtype=np.uint8)).save(path)
        textures.append(path)
    np.testing.assert_allclose(vertex_preview(replace(mesh, textures=textures)).colors, 0.5)


def test_report_compares_source_copies_and_is_not_a_color_threshold(tmp_path):
    from scanview_import.mesh_preview import color_report, vertex_preview

    mesh = zwei_inseln_je_quad(tmp_path, 3)
    mesh = replace(mesh, colors=np.zeros_like(mesh.positions))
    preview = vertex_preview(mesh)
    preview.colors[:] = 1
    report = color_report(mesh, preview)
    np.testing.assert_allclose(report["meanAbsDeltaRGB"], 255)
    assert report["ok"] is True  # Farbe ausgewiesen, kein Treuetor.
    assert color_report(mesh, replace(preview, colors=None))["ok"] is False
