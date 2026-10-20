"""Synthetische Rahmenkontrollen; Rotation/Maßstab dürfen nicht geraten werden."""

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from scanview_import.splat_frame import fit_similarity, transform_gaussians
from scanview_import.splat_source import Gaussians


def test_similarity_recovers_metric_frame_and_reports_outlier():
    local = np.random.default_rng(4).normal(size=(30, 3))
    rotation = Rotation.from_euler("xyz", [12, -9, 41], degrees=True).as_matrix()
    world = 2.4 * local @ rotation.T + [500000, 5800000, 40]
    world[-1] += [4, 2, 1]
    frame, report = fit_similarity(local, world, [str(i) for i in range(30)])
    assert frame.scale == pytest.approx(2.4)
    np.testing.assert_allclose(frame.rotation, rotation, atol=1e-8)
    assert report["outliers"] == ["29"]
    assert report["p95Mm"] < 0.001
    assert report["maxAllMm"] > 4000


def test_collinear_cameras_and_bad_residuals_are_rejected():
    pts = np.column_stack([np.arange(5), np.zeros((5, 2))])
    with pytest.raises(ValueError, match="Geometrie"):
        fit_similarity(pts, pts, list("abcde"))
    pts = np.random.default_rng(3).normal(size=(20, 3))
    with pytest.raises(ValueError, match="Residuen"):
        fit_similarity(
            pts, pts + np.random.default_rng(5).normal(size=pts.shape), [str(i) for i in range(20)]
        )


def test_gaussian_covariance_rotates_and_scales():
    pts = np.random.default_rng(2).normal(size=(10, 3))
    rotation = Rotation.from_euler("z", 90, degrees=True)
    frame, _ = fit_similarity(
        pts, 3 * rotation.apply(pts) + [10, 20, 30], [str(i) for i in range(10)]
    )
    source = Gaussians(
        np.array([[1.0, 0, 0]]),
        np.array([[1.0, 2, 3]]),
        np.array([[0.0, 0, 0, 1]]),
        np.array([0.9]),
        np.array([[1.0, 0, 0]]),
        3,
        np.zeros((1, 15, 3)),
    )
    out = transform_gaussians(source, frame)
    np.testing.assert_allclose(out.centers, [[10, 23, 30]], atol=1e-8)
    np.testing.assert_allclose(out.scales, [[3, 6, 9]])
    np.testing.assert_allclose(
        Rotation.from_quat(out.quaternions).as_matrix()[0], rotation.as_matrix(), atol=1e-8
    )
    assert out.sh_degree == 3
    np.testing.assert_array_equal(out.colors, source.colors)


def test_explicit_camera_limit_is_reported_and_bounded():
    pts = np.random.default_rng(11).normal(size=(12, 3))
    _, report = fit_similarity(pts, pts * 2 + [10, 20, 30], [str(i) for i in range(12)], 100)
    assert report["raised"] is True
    assert report["limitMm"] == 100
    with pytest.raises(ValueError, match="Grenze"):
        fit_similarity(pts, pts, [str(i) for i in range(12)], 301)
