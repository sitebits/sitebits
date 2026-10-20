"""Synthetische SH-Farben und SPZ-Kodierung im gedrehten metrischen Rahmen."""
from dataclasses import replace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation
from test_splat_source import synthetic_gaussians

from scanview_import.splat_frame import Similarity, transform_gaussians
from scanview_import.splat_sh import sh_basis
from scanview_import.splat_source import read_gaussians, write_gaussians
from scanview_import.splat_spz import POSITION_TOLERANCE_M, write_spz


@pytest.mark.parametrize("degree", [1, 2, 3])
def test_sh_color_function_survives_arbitrary_rotation(degree):
    rng = np.random.default_rng(18)
    sh = rng.normal(0, 0.1, (1024, (degree + 1) ** 2 - 1, 3))
    g = replace(synthetic_gaussians(), sh_degree=degree, sh_rest=sh)
    r = Rotation.from_euler("xyz", [24, -37, 81], degrees=True).as_matrix()
    out = transform_gaussians(g, Similarity(2, r, np.array([4, 5, 6])))
    directions = rng.normal(size=(137, 3))
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    assert out.sh_degree == degree
    np.testing.assert_allclose(
        sh_basis(directions, degree) @ g.sh_rest[0],
        sh_basis(directions @ r.T, degree) @ out.sh_rest[0], atol=1e-12,
    )
    # Negative control: an unrotated SH field changes the directional color.
    assert np.max(np.abs(sh_basis(directions @ r.T, degree) @ sh[0]
                         - sh_basis(directions, degree) @ sh[0])) > 0.01


def test_degree_one_known_quarter_turn():
    g = replace(synthetic_gaussians(), sh_degree=1,
                sh_rest=np.tile([[0, 0, 0], [0, 0, 0], [0.4, 0, 0]], (1024, 1, 1)))
    r = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    out = transform_gaussians(g, Similarity(1, r, np.zeros(3)))
    np.testing.assert_allclose(out.sh_rest[0], [[0.4, 0, 0], [0, 0, 0], [0, 0, 0]], atol=1e-12)


@pytest.mark.parametrize("degree", [0, 1, 2, 3])
def test_spz_and_ply_roundtrip_preserves_sh_and_frame(tmp_path, degree):
    rng = np.random.default_rng(8)
    g = replace(synthetic_gaussians(), sh_degree=degree,
                sh_rest=rng.uniform(-0.4, 0.4, (1024, (degree + 1) ** 2 - 1, 3)),
                quaternions=Rotation.random(1024, random_state=rng).as_quat())
    origin = np.array([500000, 5800000, 40])
    ply = tmp_path / "fixture.ply"
    write_gaussians(ply, g, origin)
    from_ply = read_gaussians(ply)
    np.testing.assert_allclose(from_ply.sh_rest, g.sh_rest, atol=1e-7)
    path = tmp_path / "fixture.spz"
    write_spz(path, g, origin)
    out = read_gaussians(path)
    assert out.sh_degree == degree
    assert np.max(np.linalg.norm(out.centers + origin - g.centers, axis=1)) <= POSITION_TOLERANCE_M
    np.testing.assert_allclose(out.sh_rest, g.sh_rest, atol=1 / 256 + 1e-12)
    np.testing.assert_allclose(out.scales, g.scales, rtol=0.032)
    assert np.max((Rotation.from_quat(out.quaternions).inv()
                   * Rotation.from_quat(g.quaternions)).magnitude()) < 0.006
    assert path.stat().st_size < ply.stat().st_size


def test_spz_overflow_fails_before_writing(tmp_path):
    g = replace(synthetic_gaussians(), centers=np.full((1024, 3), 3000.0))
    path = tmp_path / "fixture.spz"
    with pytest.raises(ValueError, match="Positionsbereich"):
        write_spz(path, g, np.zeros(3))
    assert not path.exists()


def test_sh_missing_or_nonfinite_rejected():
    with pytest.raises(ValueError, match="SH"):
        replace(synthetic_gaussians(), sh_degree=3).validate()
    with pytest.raises(ValueError, match="SH"):
        replace(synthetic_gaussians(), sh_degree=1,
                sh_rest=np.full((1024, 3, 3), np.nan)).validate()
