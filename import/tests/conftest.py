import math

import numpy as np
import pytest
from e57_fixture import (  # noqa: F401  (FACE_COLORS für Tests)
    FACE_COLORS,
    FixtureInfo,
    write_fixture,
)

from scanview_import.model import Pose


def _points(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.uniform(-5, 5, size=(n, 3))


@pytest.fixture
def two_scan_e57(tmp_path) -> FixtureInfo:
    h = math.radians(90) / 2
    inv = np.zeros(1000, dtype=np.int64)
    inv[:7] = 2
    return write_fixture(
        tmp_path / "two.e57",
        [
            (Pose((1000.0, 2000.0, 10.0), (1, 0, 0, 0)), _points(1000, 1), inv),
            (Pose((1010.0, 2000.0, 10.0), (math.cos(h), 0, 0, math.sin(h))), _points(1000, 2), inv),
        ],
    )


@pytest.fixture
def two_scan_e57_color_limits(tmp_path) -> FixtureInfo:
    # Wie two_scan_e57, aber mit Data3D-colorLimits-Struktur — bildet
    # das reale Muster der Pilotkirche nach (colorLimits 0..255 je Scan), anders als Pilothalle/
    # two_scan_e57 (keine colorLimits-Struktur).
    h = math.radians(90) / 2
    inv = np.zeros(1000, dtype=np.int64)
    inv[:7] = 2
    return write_fixture(
        tmp_path / "two_cl.e57",
        [
            (Pose((1000.0, 2000.0, 10.0), (1, 0, 0, 0)), _points(1000, 1), inv),
            (Pose((1010.0, 2000.0, 10.0), (math.cos(h), 0, 0, math.sin(h))), _points(1000, 2), inv),
        ],
        color_limits=True,
    )


@pytest.fixture
def cube_e57(tmp_path) -> FixtureInfo:
    order = ["nz", "px", "pz", "ny", "nx", "py"]
    return write_fixture(
        tmp_path / "cube.e57",
        [(Pose((0.0, 0.0, 1.5), (1, 0, 0, 0)), _points(200, 3), np.zeros(200, dtype=np.int64))],
        images=[(f, 0) for f in order],  # type: ignore[misc]
    )


@pytest.fixture
def cube_e57_rotated(tmp_path) -> FixtureInfo:
    """Wie cube_e57, aber Scan um 90° gegiert und verschoben (D5: Würfelpfad mit gedrehtem Scan)."""
    h = math.radians(90) / 2
    order = ["nz", "px", "pz", "ny", "nx", "py"]
    return write_fixture(
        tmp_path / "cube_rot.e57",
        [
            (
                Pose((5.0, 0.0, 1.5), (math.cos(h), 0.0, 0.0, math.sin(h))),
                _points(200, 4),
                np.zeros(200, dtype=np.int64),
            )
        ],
        images=[(f, 0) for f in order],  # type: ignore[misc]
    )
