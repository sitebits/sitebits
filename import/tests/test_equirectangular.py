"""Richtungen und Würfelkanten an synthetischen Kugelbildern."""

import numpy as np
import pytest
from PIL import Image

from scanview_import.equirectangular import FACE_ROTATIONS, cube_face


def direction_image(width=2048):
    height = width // 2
    lon = ((np.arange(width) + 0.5) / width - 0.5) * 2 * np.pi
    lat = (0.5 - (np.arange(height) + 0.5) / height) * np.pi
    x = np.cos(lat)[:, None] * np.sin(lon)[None, :]
    y = np.broadcast_to(np.sin(lat)[:, None], x.shape)
    z = -np.cos(lat)[:, None] * np.cos(lon)[None, :]
    return Image.fromarray(np.rint((np.stack([x, y, z], axis=-1) + 1) * 127.5).astype("uint8"))


@pytest.mark.parametrize(
    "face,expected",
    [
        ("px", [255, 128, 128]),
        ("nx", [0, 128, 128]),
        ("py", [128, 255, 128]),
        ("ny", [128, 0, 128]),
        ("pz", [128, 128, 255]),
        ("nz", [128, 128, 0]),
    ],
)
def test_face_centres(face, expected):
    result = np.asarray(cube_face(direction_image(), face, 65))
    np.testing.assert_allclose(result[32, 32], expected, atol=1)


def test_all_twelve_shared_edges():
    size = 129
    faces = {f: np.asarray(cube_face(direction_image(), f, size)) for f in FACE_ROTATIONS}
    edges = []
    t = np.linspace(-1, 1, size)
    for face, r in FACE_ROTATIONS.items():
        for name, rays in [
            ("left", np.stack([-np.ones(size), -t, -np.ones(size)], axis=-1)),
            ("right", np.stack([np.ones(size), -t, -np.ones(size)], axis=-1)),
            ("top", np.stack([t, np.ones(size), -np.ones(size)], axis=-1)),
            ("bottom", np.stack([t, -np.ones(size), -np.ones(size)], axis=-1)),
        ]:
            pixels = {
                "left": faces[face][:, 0],
                "right": faces[face][:, -1],
                "top": faces[face][0],
                "bottom": faces[face][-1],
            }[name]
            edges.append((rays @ r.T, pixels))
    matches = 0
    for i, (rays, pixels) in enumerate(edges):
        for other, samples in edges[i + 1 :]:
            if np.allclose(rays, other):
                np.testing.assert_allclose(pixels, samples, atol=2)
                matches += 1
            elif np.allclose(rays, other[::-1]):
                np.testing.assert_allclose(pixels, samples[::-1], atol=2)
                matches += 1
    # Pixelzentren liegen eine halbe Pixelbreite neben der gemeinsamen geometrischen Kante.
    assert matches == 12


def test_invalid_size():
    with pytest.raises(ValueError, match="Seitengröße"):
        cube_face(direction_image(128), "nz", 0)
