"""Bekannte COLMAP-Zentren, identische Namen und lokale Metashape-Transformation."""

import struct
from pathlib import Path

import numpy as np
import pytest

from scanview_import.splat_cameras import camera_pairs, colmap_centers, world_centers


@pytest.mark.parametrize("binary", [False, True])
def test_colmap_world_to_camera_is_inverted(tmp_path: Path, binary: bool):
    path = tmp_path / ("images.bin" if binary else "images.txt")
    if binary:
        path.write_bytes(
            struct.pack("<Q", 1)
            + struct.pack("<i7di", 1, 1, 0, 0, 0, -1, -2, -3, 1)
            + b"image.png\0"
            + struct.pack("<Q", 0)
        )
    else:
        path.write_text("# fixture\n1 1 0 0 0 -1 -2 -3 1 image.png\n\n")
    np.testing.assert_allclose(colmap_centers(path)["image.png"], [1, 2, 3])


def test_local_xml_chunk_scale_translation(tmp_path):
    path = tmp_path / "cameras.xml"
    path.write_text(
        "<document><chunk><transform><rotation>1 0 0 0 1 0 0 0 1</rotation>"
        "<translation>10 20 30</translation><scale>2</scale></transform>"
        '<cameras><camera label="image.png"><transform>'
        "1 0 0 1 0 1 0 2 0 0 1 3 0 0 0 1</transform></camera></cameras>"
        "</chunk></document>"
    )
    np.testing.assert_allclose(world_centers(path)["image.png"], [12, 24, 36])


def test_duplicate_labels_and_no_name_guessing(tmp_path):
    csv = tmp_path / "world.csv"
    csv.write_text("label,E,N,H\nimage.png,1,2,3\nimage.png,2,3,4\n")
    with pytest.raises(ValueError, match="doppelt"):
        world_centers(csv)
    csv.write_text("label,E,N,H\nimage,1,2,3\n")
    local = tmp_path / "images.txt"
    local.write_text("1 1 0 0 0 -1 -2 -3 1 image.png\n\n")
    with pytest.raises(ValueError, match="vier"):
        camera_pairs(local, csv)
