"""Real SH in 3DGS order (l ascending, m=-l..l), including Condon-Shortley.

Convention reference: graphdeco-inria/gaussian-splatting utils/sh_utils.py.
Evaluation uses existing SciPy sph_harm_y, no copied implementation/dependency.
Rotation solves Y(d) D = Y(R^-1 d), separately per band: no mixing of degrees.
"""
from __future__ import annotations

import numpy as np
from scipy.special import sph_harm_y


def sh_basis(directions: np.ndarray, degree: int) -> np.ndarray:
    theta = np.arccos(np.clip(directions[:, 2], -1, 1))
    phi = np.arctan2(directions[:, 1], directions[:, 0])
    columns = []
    for band in range(1, degree + 1):
        for order in range(-band, band + 1):
            y = sph_harm_y(band, abs(order), theta, phi)
            columns.append(np.sqrt(2) * (y.imag if order < 0 else y.real)
                           if order else y.real)
    return np.column_stack(columns) if columns else np.empty((len(directions), 0))


def rotate_sh(coefficients: np.ndarray, rotation: np.ndarray, degree: int) -> np.ndarray:
    if degree == 0:
        return coefficients.copy()
    rng = np.random.default_rng(42)
    directions = rng.normal(size=(64, 3))
    directions /= np.linalg.norm(directions, axis=1)[:, None]
    basis = sh_basis(directions, degree)
    # For row vectors, R^-1 d equals d @ R.
    rotated = sh_basis(directions @ rotation, degree)
    out = np.empty_like(coefficients)
    for band in range(1, degree + 1):
        start, end = band * band - 1, (band + 1) ** 2 - 1
        operator = np.linalg.lstsq(basis[:, start:end], rotated[:, start:end], rcond=None)[0]
        for offset in range(0, len(coefficients), 16384):
            block = coefficients[offset:offset + 16384, start:end]
            out[offset:offset + 16384, start:end] = np.einsum("ij,njc->nic", operator, block)
    return out
