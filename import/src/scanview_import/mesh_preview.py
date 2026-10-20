"""Nahtfreie Vorschau: exakte Positionsgruppen, Farben in linearem sRGB."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from .mesh_source import Mesh
from .mesh_textures import decode_texture, sample_rgb


def srgb_to_linear(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(rgb: np.ndarray) -> np.ndarray:
    return np.where(rgb <= 0.0031308, rgb * 12.92, 1.055 * rgb ** (1 / 2.4) - 0.055)


def color_copies(mesh: Mesh) -> tuple[np.ndarray, np.ndarray]:
    base = mesh.colors[:, :3] if mesh.colors is not None else np.ones_like(mesh.positions)
    if mesh.source.format not in ("gltf", "glb"):
        base = srgb_to_linear(base)
    indices, samples = [], []
    used = np.zeros(len(mesh.positions), dtype=bool)
    for material in np.unique(mesh.materials):
        vertices = np.unique(mesh.triangles[mesh.materials == material])
        used[vertices] = True
        texture = mesh.textures[int(material)]
        if texture is not None:
            if mesh.uv is None:
                raise ValueError("Vorschautextur ohne UV")
            colors = srgb_to_linear(sample_rgb(decode_texture(texture), mesh.uv[vertices]) / 255)
        else:
            colors = base[vertices]
        indices.append(vertices)
        samples.append(colors)
    # Nicht referenzierte Kontrollvertices ebenfalls erhalten.
    indices.append(np.flatnonzero(~used))
    samples.append(base[~used])
    return np.concatenate(indices), np.concatenate(samples)


def vertex_preview(mesh: Mesh) -> Mesh:
    positions, inverse, counts = np.unique(
        mesh.positions, axis=0, return_inverse=True, return_counts=True
    )
    colors = np.zeros((len(positions), 3))
    copies, samples = color_copies(mesh)
    np.add.at(colors, inverse[copies], samples)
    counts = np.bincount(inverse[copies], minlength=len(positions))
    colors /= counts[:, None]
    return replace(
        mesh,
        positions=positions,
        triangles=inverse[mesh.triangles].astype(np.uint32),
        uv=None,
        colors=colors,
        materials=np.zeros(len(mesh.triangles), dtype=np.int32),
        textures=[None],
        source_vertex_count=None,
    )


def color_report(source: Mesh, output: Mesh) -> dict[str, Any]:
    """1 000 Quell-UV-Kopien gegen ausgelieferte Vertexfarbe; kein Farbtor."""
    copies, samples = color_copies(source)
    indices = np.random.default_rng(42).integers(0, len(copies), 1000)
    nearest = cKDTree(output.positions).query(source.positions[copies[indices]])[1]
    valid = (
        output.colors is not None and output.uv is None and all(t is None for t in output.textures)
    )
    delta = None
    if valid and output.colors is not None:
        delta = float(
            np.abs(
                linear_to_srgb(output.colors[nearest, :3]) - linear_to_srgb(samples[indices])
            ).mean()
            * 255
        )
    return {
        "samples": 1000,
        "seed": 42,
        "meanAbsDeltaRGB": delta,
        "colorSpace": "sRGB (0-255)",
        "ok": bool(valid),
    }
