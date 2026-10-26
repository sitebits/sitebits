"""Lokale glTF-Positionen und explizite, isotrope Dekodierung."""

from __future__ import annotations

import numpy as np


def quantize_positions(
    world: np.ndarray,
    origin: np.ndarray,
    max_step: float = 0.001 / np.sqrt(3),
) -> tuple[np.ndarray, np.ndarray, float, bool]:
    xyz = (world - np.asarray(origin, dtype=np.float64))[:, [0, 2, 1]] * [1, 1, -1]
    minimum = xyz.min(axis=0)
    step = max(float(np.ptp(xyz, axis=0).max()) / 65535, 1e-12)
    if step <= max_step:
        positions = np.zeros((len(xyz), 4), dtype="<u2")
        positions[:, :3] = np.rint((xyz - minimum) / step).astype("<u2")
        return positions, minimum, step, True
    step = max(0.00025, float(np.max(np.abs(np.spacing(xyz.astype("<f4"))))))
    positions = np.ascontiguousarray(np.rint(xyz / step) * step, dtype="<f4")
    return positions, np.zeros(3), step, False
