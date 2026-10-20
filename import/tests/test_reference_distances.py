"""Tests für scripts/reference_distances.py (Auswahl stabiler Wandpunkte, Paarbildung)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import numpy as np

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "reference_distances.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("reference_distances", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rd = _load()


def _wall(rng: np.random.Generator, n: int, x: float, noise: float) -> np.ndarray:
    """Senkrechte Wand x = const, 10 m breit, 4 m hoch, Rauschen normal zur Wand."""
    y = rng.uniform(-5.0, 5.0, n)
    z = rng.uniform(0.0, 4.0, n)
    return np.column_stack([x + rng.normal(0.0, noise, n), y, z])


def test_wall_points_are_candidates_and_blob_is_not() -> None:
    rng = np.random.default_rng(1)
    wall = _wall(rng, 200_000, x=6.0, noise=0.001)
    # Laub-artiger Klumpen zwischen Scanner und Wand: ungeordnet, ohne Ebene.
    blob = rng.normal([3.5, 0.0, 2.0], 0.3, (60_000, 3))
    pts = np.vstack([wall, blob])
    origin = np.array([0.0, 0.0, 1.5])
    cand = rd.good_candidates(pts, origin, np.random.default_rng(2), 0.10, 0.005, 0.30, 0.010)
    assert len(cand) > 50
    assert np.all(cand < len(wall)), "Klumpenpunkte dürfen keine Kandidaten sein"


def test_floor_is_rejected_as_not_a_wall() -> None:
    rng = np.random.default_rng(3)
    n = 200_000
    floor = np.column_stack(
        [rng.uniform(3.5, 10.0, n), rng.uniform(-5, 5, n), rng.normal(0, 0.001, n)]
    )
    cand = rd.good_candidates(
        floor, np.array([0.0, 0.0, 1.5]), np.random.default_rng(4), 0.10, 0.005, 0.30, 0.010
    )
    assert len(cand) == 0


def test_pick_pairs_respects_distance_and_uses_each_point_once() -> None:
    rng = np.random.default_rng(5)
    pts = _wall(rng, 5_000, x=6.0, noise=0.0)
    cand = np.arange(len(pts))
    pairs = rd.pick_pairs(pts, cand, 5, np.random.default_rng(6))
    assert len(pairs) == 5
    used = [i for p in pairs for i in p]
    assert len(set(used)) == len(used)
    for a, b in pairs:
        d = float(np.linalg.norm(pts[a] - pts[b]))
        assert rd.PAIR_MIN_M <= d <= rd.PAIR_MAX_M
