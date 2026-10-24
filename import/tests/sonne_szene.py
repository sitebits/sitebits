"""Synthetische Szenen für die Sonne-Tests: Quader als Dreiecksnetze, analytischer Strahltest."""

from __future__ import annotations

import numpy as np

QUADER_DREIECKE = np.array(
    [
        [0, 2, 1],
        [0, 3, 2],
        [4, 5, 6],
        [4, 6, 7],
        [0, 1, 5],
        [0, 5, 4],
        [1, 2, 6],
        [1, 6, 5],
        [2, 3, 7],
        [2, 7, 6],
        [3, 0, 4],
        [3, 4, 7],
    ]
)


def quader(lo: list[float], hi: list[float]) -> tuple[np.ndarray, np.ndarray]:
    (x0, y0, z0), (x1, y1, z1) = lo, hi
    p = np.array(
        [
            [x0, y0, z0],
            [x1, y0, z0],
            [x1, y1, z0],
            [x0, y1, z0],
            [x0, y0, z1],
            [x1, y0, z1],
            [x1, y1, z1],
            [x0, y1, z1],
        ],
        dtype=np.float64,
    )
    return p, QUADER_DREIECKE.copy()


def zusammen(*teile: tuple[np.ndarray, np.ndarray]) -> tuple[np.ndarray, np.ndarray]:
    pos, tri, n = [], [], 0
    for p, t in teile:
        pos.append(p)
        tri.append(t + n)
        n += len(p)
    return np.concatenate(pos), np.concatenate(tri)


def platte(
    groesse: float, rauheit: float, schritt: float = 0.05, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Waagerechte Platte um (0, 0, 0), Dreiecke mit zufälliger Höhe ±rauheit (Rauheit)."""
    k = round(groesse / schritt) + 1
    xs = np.linspace(-groesse / 2, groesse / 2, k)
    x, y = np.meshgrid(xs, xs, indexing="ij")
    z = np.random.default_rng(seed).uniform(-rauheit, rauheit, x.shape)
    p = np.stack([x, y, z], axis=-1).reshape(-1, 3)
    i = np.arange(k * k).reshape(k, k)
    a, b, c, d = i[:-1, :-1].ravel(), i[1:, :-1].ravel(), i[1:, 1:].ravel(), i[:-1, 1:].ravel()
    return p, np.concatenate([np.stack([a, b, c], 1), np.stack([a, c, d], 1)])


def strahl_trifft_quader(o: np.ndarray, d: np.ndarray, lo: list[float], hi: list[float]) -> bool:
    with np.errstate(divide="ignore"):
        t1 = (np.asarray(lo) - o) / d
        t2 = (np.asarray(hi) - o) / d
    tmin = np.nanmax(np.minimum(t1, t2))
    tmax = np.nanmin(np.maximum(t1, t2))
    return bool(tmax >= max(tmin, 0.0))
