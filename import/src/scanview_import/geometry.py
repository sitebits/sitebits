"""Reine Geometrie: Quaternionen, Posen, Ursprung, Würfelseiten."""

from __future__ import annotations

from collections.abc import Sequence
from typing import cast

import numpy as np

from .model import FaceName, Pose, Quat

# Blickachse einer E57-Pinhole-Kamera im Bild-KS: -Z (Vorab-Probe Pilotkirche, Bild-Y = oben).
VIEW_AXIS = np.array([0.0, 0.0, -1.0])

# Kanonische Rotation, falls keine Scan-Pose vorliegt (z. B. in Tests ohne Scan-Bezug).
IDENTITY_QUAT: Quat = (1.0, 0.0, 0.0, 0.0)


def quat_to_matrix(q_wxyz: Quat | Sequence[float]) -> np.ndarray:
    w, x, y, z = (float(v) for v in q_wxyz)
    n = w * w + x * x + y * y + z * z
    if n == 0:
        raise ValueError("Quaternion der Länge 0")
    s = 2.0 / n
    return np.array(
        [
            [1 - s * (y * y + z * z), s * (x * y - z * w), s * (x * z + y * w)],
            [s * (x * y + z * w), 1 - s * (x * x + z * z), s * (y * z - x * w)],
            [s * (x * z - y * w), s * (y * z + x * w), 1 - s * (x * x + y * y)],
        ],
        dtype=np.float64,
    )


def apply_pose(points: np.ndarray, pose: Pose) -> np.ndarray:
    r = quat_to_matrix(pose.rotation_wxyz)
    t = np.asarray(pose.translation, dtype=np.float64)
    return cast(np.ndarray, np.asarray(points, dtype=np.float64) @ r.T + t)


def compute_origin(
    bounds_min: Sequence[float], bounds_max: Sequence[float]
) -> tuple[float, float, float]:
    c = [(float(a) + float(b)) / 2.0 for a, b in zip(bounds_min, bounds_max, strict=True)]
    return (float(round(c[0])), float(round(c[1])), float(round(c[2])))


_FACES: dict[tuple[int, int], FaceName] = {
    (0, 1): "px",
    (0, -1): "nx",
    (1, 1): "py",
    (1, -1): "ny",
    (2, 1): "pz",
    (2, -1): "nz",
}


def cube_face_for(
    image_rotation_wxyz: Quat | Sequence[float],
    scan_rotation_wxyz: Quat | Sequence[float] = IDENTITY_QUAT,
) -> FaceName:
    """Würfelseite der Blickrichtung eines Bildes, relativ zur Scan-Pose.

    Weltrichtung der Kamera ist ``R_img @ VIEW_AXIS``; zurückgedreht ins
    Scan-KS mit ``R_scan.T`` ergibt die Richtung, deren betragsgrößte Achse
    die Würfelseite bestimmt.
    """
    r_img = quat_to_matrix(image_rotation_wxyz)
    r_scan = quat_to_matrix(scan_rotation_wxyz)
    d = r_scan.T @ r_img @ VIEW_AXIS
    axis = int(np.argmax(np.abs(d)))
    return _FACES[(axis, 1 if d[axis] > 0 else -1)]
