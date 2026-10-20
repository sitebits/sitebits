"""Ähnlichkeitstransformation mit positivem Maßstab und explizitem Residuen-Gate."""

from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations, islice
from typing import Any

import numpy as np
from scipy.spatial.transform import Rotation

from .splat_sh import rotate_sh
from .splat_source import Gaussians


@dataclass(frozen=True)
class Similarity:
    scale: float
    rotation: np.ndarray
    translation: np.ndarray

    def apply(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(self.scale * pts @ self.rotation.T + self.translation)


def umeyama(local: np.ndarray, world: np.ndarray) -> Similarity:
    if local.shape != world.shape or local.ndim != 2 or local.shape[1] != 3 or len(local) < 3:
        raise ValueError("Kamerapaare: mindestens drei Zentren mit XYZ")
    if not np.isfinite(local).all() or not np.isfinite(world).all():
        raise ValueError("Kamerapaare müssen endlich sein")
    a, b = local - local.mean(0), world - world.mean(0)
    if np.linalg.matrix_rank(a, tol=1e-8) < 2 or np.linalg.matrix_rank(b, tol=1e-8) < 2:
        raise ValueError("Kamera-Geometrie kollinear oder entartet")
    u, d, vt = np.linalg.svd(b.T @ a / len(a))
    sign = np.ones(3)
    sign[-1] = np.linalg.det(u @ vt)
    r = u @ np.diag(sign) @ vt
    scale = float(np.sum(d * sign) / np.mean(np.sum(a * a, axis=1)))
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("Kamera-Maßstab ungültig")
    return Similarity(scale, r, world.mean(0) - scale * r @ local.mean(0))


def fit_similarity(
    local: np.ndarray, world: np.ndarray, labels: list[str], limit_mm: float = 25
) -> tuple[Similarity, dict[str, Any]]:
    if not np.isfinite(limit_mm) or not 0 < limit_mm <= 300:
        raise ValueError("Kamera-Residuen-Grenze muss 0 < mm ≤ 300 sein")
    if len(labels) != len(local) or len(set(labels)) != len(labels) or len(local) < 4:
        raise ValueError("Mindestens vier eindeutige Kamerapaare erforderlich")
    # Validiert auch Daten und Geometrie, bevor Teilmengen betrachtet werden.
    umeyama(local, world)
    best = np.zeros(len(local), dtype=bool)
    rng = np.random.default_rng(42)
    subsets = (
        islice(combinations(range(len(local)), 3), 256)
        if len(local) <= 12
        else (rng.choice(len(local), 3, replace=False) for _ in range(256))
    )
    for ix in subsets:
        try:
            candidate = umeyama(local[list(ix)], world[list(ix)])
        except ValueError:
            continue
        mask = np.linalg.norm(candidate.apply(local) - world, axis=1) * 1000 <= limit_mm
        if mask.sum() > best.sum():
            best = mask
    if best.sum() < max(4, int(np.ceil(len(local) * 0.8))):
        raise ValueError("Kamera-Residuen: weniger als 80 % / vier belegte Kameras")
    frame = umeyama(local[best], world[best])
    residual = np.linalg.norm(frame.apply(local) - world, axis=1) * 1000
    p50, p95 = np.quantile(residual[best], [0.5, 0.95])
    if residual[best].max() > limit_mm:
        raise ValueError("Kamera-Residuen überschreiten Grenze nach Ausgleichung")
    return frame, {
        "method": "camera-umeyama-ransac",
        "scale": frame.scale,
        "rotation": frame.rotation.tolist(),
        "translation": frame.translation.tolist(),
        "pairs": len(local),
        "inliers": int(best.sum()),
        "p50Mm": float(p50),
        "p95Mm": float(p95),
        "maxMm": float(residual[best].max()),
        "maxAllMm": float(residual.max()),
        "limitMm": limit_mm,
        "raised": limit_mm != 25,
        "outliers": [label for label, ok in zip(labels, best, strict=True) if not ok],
        "residualsMm": dict(zip(labels, residual.tolist(), strict=True)),
        "ok": True,
    }


def transform_gaussians(g: Gaussians, frame: Similarity) -> Gaussians:
    g.validate()
    q = (Rotation.from_matrix(frame.rotation) * Rotation.from_quat(g.quaternions)).as_quat()
    return replace(
        g, centers=frame.apply(g.centers), scales=g.scales * frame.scale, quaternions=q,
        sh_rest=rotate_sh(g.sh_rest, frame.rotation, g.sh_degree)
        if g.sh_rest is not None else None
    )
