"""Equirektangular → 90°-Lochkamera: +X rechts, +Y oben, Bildmitte blickt nach -Z.

Pixelzentren sind ganzzahlig. Länge 0 liegt in der Bildmitte, Breite +90° oben.
Die horizontale Naht wird periodisch interpoliert, die Pole werden geklemmt.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from .model import FaceName

# Spalten sind Bild-rechts, Bild-oben und Bild-rückwärts im Würfel-KS.
FACE_ROTATIONS: dict[FaceName, np.ndarray] = {
    "px": np.array([[0, 0, -1], [0, 1, 0], [1, 0, 0]], dtype=float),
    "nx": np.array([[0, 0, 1], [0, 1, 0], [-1, 0, 0]], dtype=float),
    "py": np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]], dtype=float),
    "ny": np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], dtype=float),
    "pz": np.diag([-1.0, 1.0, -1.0]),
    "nz": np.eye(3),
}


def cube_face(image: Image.Image, face: FaceName, size: int) -> Image.Image:
    """Eine Seite bilinear erzeugen; Ausgabe und kleine Zeilenblöcke statt sechs Seiten.

    Brennweite size/2, Hauptpunkt (size-1)/2: Die äußeren Pixelgrenzen spannen
    exakt 90° auf. NumPy-Puffer der Quelle bleibt uint8, nur Blöcke werden float.
    """
    if type(size) is not int or size <= 0:
        raise ValueError("Seitengröße muss eine positive ganze Zahl sein")
    if image.width != 2 * image.height:
        raise ValueError("Equirektangularbild muss das Verhältnis 2:1 haben")
    src = np.asarray(image.convert("RGB"))
    height, width = src.shape[:2]
    result = np.empty((size, size, 3), dtype=np.uint8)
    horizontal = (np.arange(size) - (size - 1) / 2) / (size / 2)
    for start in range(0, size, 32):
        stop = min(start + 32, size)
        vertical = -(np.arange(start, stop) - (size - 1) / 2) / (size / 2)
        x, y = np.meshgrid(horizontal, vertical)
        rays = np.stack([x, y, -np.ones_like(x)], axis=-1) @ FACE_ROTATIONS[face].T
        rays /= np.linalg.norm(rays, axis=-1, keepdims=True)
        u = (np.arctan2(rays[..., 0], -rays[..., 2]) / (2 * np.pi) + 0.5) * width - 0.5
        v = np.clip((0.5 - np.arcsin(rays[..., 1]) / np.pi) * height - 0.5, 0, height - 1)
        x0, y0 = np.floor(u).astype(int), np.floor(v).astype(int)
        wx, wy = (u - x0)[..., None], (v - y0)[..., None]
        x1, y1 = (x0 + 1) % width, np.minimum(y0 + 1, height - 1)
        x0 %= width
        top = src[y0, x0] * (1 - wx) + src[y0, x1] * wx
        bottom = src[y1, x0] * (1 - wx) + src[y1, x1] * wx
        result[start:stop] = np.rint(top * (1 - wy) + bottom * wy).astype(np.uint8)
    return Image.fromarray(result)
