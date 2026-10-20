"""Stress generator emits actual independent-decodable positions/SH, not repo fixtures."""
from pathlib import Path

import numpy as np
import pytest
from generate_large_splats import generate

from scanview_import.splat_source import read_gaussians


def test_bounded_generator_decodes_known_grid_and_sh(tmp_path: Path):
    path = tmp_path / "fixture.spz"
    generate(path, 2050)
    g = read_gaussians(path)
    np.testing.assert_allclose(g.centers[[0, 1000, 2049]],
                               [[-50, -50, 0], [-50, -49.9, 0], [-45.1, -49.8, 0]],
                               atol=0.000123)
    assert g.sh_degree == 3
    assert np.max(np.abs(g.sh_rest)) > 0.09
    with pytest.raises(ValueError, match="new"):
        generate(path, 2050)
