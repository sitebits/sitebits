import numpy as np
import pytest

from scanview_import.mesh_source import Mesh, resolve_mesh_source
from scanview_import.mesh_stages import simplify_mesh, simplify_with_report


def grid(tmp_path, n=40):
    p = tmp_path / "grid.obj"
    p.touch()
    x, y = np.meshgrid(np.linspace(0, 1, n), np.linspace(0, 2, n))
    xyz = np.column_stack([x.ravel(), y.ravel(), 0.05 * np.sin(x.ravel() * 3)])
    faces = []
    for j in range(n - 1):
        for i in range(n - 1):
            k = j * n + i
            faces.extend([[k, k + 1, k + n], [k + 1, k + n + 1, k + n]])
    f = np.array(faces, dtype=np.uint32)
    uv = np.column_stack([x.ravel(), y.ravel() / 2]).astype(np.float32)
    return Mesh(
        xyz, f, uv, None, np.zeros(len(f), dtype=np.int32), [None], resolve_mesh_source(p, unit="m")
    )


@pytest.mark.parametrize("target", [2000, 1000, 300, 100])
def test_targets(tmp_path, target):
    m = grid(tmp_path)
    out = simplify_mesh(m, target)
    assert abs(len(out.triangles) / target - 1) <= 0.05
    assert out.uv is not None
    assert len(out.positions) < len(m.positions)


def test_no_simplification_and_invalid_target(tmp_path):
    m = grid(tmp_path)
    assert simplify_mesh(m, 0) is m
    assert simplify_mesh(m, len(m.triangles) + 10) is m
    with pytest.raises(ValueError):
        simplify_mesh(m, -1)


def zwei_inseln_je_quad(tmp_path, n=30):
    """Jedes Quadrat eine eigene Texturinsel: keine Naht darf kollabieren."""
    m = grid(tmp_path, n)
    tris = m.triangles
    positions = m.positions[tris.ravel()]
    uv = (m.uv[tris.ravel()] + np.repeat(np.arange(len(tris)), 3)[:, None] * 0.0001).astype(
        np.float32
    )
    faces = np.arange(len(positions), dtype=np.uint32).reshape(-1, 3)
    return Mesh(positions, faces, uv, None, m.materials, [None], m.source)


def test_unreachable_target_keeps_seams_and_reports(tmp_path):
    m = zwei_inseln_je_quad(tmp_path)
    out, info = simplify_with_report(m, 50)
    assert info["zielErreicht"] is False
    assert info["erreicht"] == len(out.triangles) > 50 * 1.05
    assert "nicht erreichbar" in info["grund"]
    with pytest.raises(ValueError, match="nicht erreichbar"):
        simplify_mesh(m, 50)


def test_reachable_target_report(tmp_path):
    m = grid(tmp_path)
    out, info = simplify_with_report(m, 1000)
    assert info["zielErreicht"] is True and info["grund"] is None
    assert info["erreicht"] == len(out.triangles)
