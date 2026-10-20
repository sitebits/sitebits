import math

import numpy as np
import pytest

from scanview_import.geometry import apply_pose, compute_origin, cube_face_for, quat_to_matrix
from scanview_import.model import Pose


def quat_about_x(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2
    return (math.cos(h), math.sin(h), 0.0, 0.0)


def quat_about_y(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2
    return (math.cos(h), 0.0, math.sin(h), 0.0)


def quat_about_z(deg: float) -> tuple[float, float, float, float]:
    h = math.radians(deg) / 2
    return (math.cos(h), 0.0, 0.0, math.sin(h))


def test_identity_quaternion_is_identity_matrix():
    np.testing.assert_allclose(quat_to_matrix((1, 0, 0, 0)), np.eye(3), atol=1e-12)


def test_apply_pose_rotates_then_translates_in_float64():
    pose = Pose(translation=(500000.0, 5800000.0, 30.0), rotation_wxyz=quat_about_z(90))
    pts = np.array([[1.0, 0.0, 0.0]])
    out = apply_pose(pts, pose)
    assert out.dtype == np.float64
    np.testing.assert_allclose(out, [[500000.0, 5800001.0, 30.0]], atol=1e-9)


def test_compute_origin_is_rounded_bbox_center():
    assert compute_origin((10.2, 20.0, -1.0), (20.2, 40.9, 3.0)) == (15.0, 30.0, 1.0)


# Konvention (Vorab-Probe Pilotkirche, verbindlich): E57-Pinhole-Kamera blickt entlang
# -Z ihres Bild-KS, Bild-Y = oben. cube_face_for dreht diese Blickachse mit der
# Bildpose ins Scan-KS (relativ zur Scan-Pose) und liefert die dominante Achse.
@pytest.mark.parametrize(
    ("rot", "face"),
    [
        (quat_about_x(0), "nz"),  # Identitaet: -Z bleibt -Z
        (quat_about_x(180), "pz"),  # 180 Grad um X: -Z -> +Z
        (quat_about_y(90), "nx"),  # +90 Grad um Y: -Z -> -X
        (quat_about_y(-90), "px"),  # -90 Grad um Y: -Z -> +X
        (quat_about_x(90), "py"),  # +90 Grad um X: -Z -> +Y
        (quat_about_x(-90), "ny"),  # -90 Grad um X: -Z -> -Y
    ],
)
def test_cube_face_from_view_direction(rot, face):
    assert cube_face_for(rot) == face


def test_cube_face_for_is_relative_to_scan_rotation():
    # Scan um 90 Grad um Z gedreht; Bild blickt in Welt-Richtung +Y.
    # Im Scan-KS zeigt Welt +Y auf die Scan-Achse +X -> Seite "px".
    scan_rotation = quat_about_z(90)
    image_rotation = quat_about_x(90)  # -Z (Bild-KS) -> +Y (Welt), siehe Test oben
    assert cube_face_for(image_rotation, scan_rotation) == "px"
