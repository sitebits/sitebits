"""Einseitiger Dreiecksatlas mit metrischer Parametrisierung und Farbtransfer."""

from __future__ import annotations

import io
import time
from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image
from scipy.ndimage import distance_transform_edt, map_coordinates
from scipy.spatial import cKDTree

from ..mesh_source import Mesh
from ..mesh_stages import compact
from .projektion import _ObereDistanz
from .texturen import BildCache, _dichte, _shelf, _verbinden


def _plan(extents: np.ndarray, density: float) -> tuple[np.ndarray, np.ndarray, tuple[int, int]]:
    """Mindestflächen bevorzugen; Dichte und dann Mindestgröße bis 2048² senken."""
    def attempt(d: float, minimum: int) -> tuple[np.ndarray, np.ndarray, tuple[int, int]] | None:
        sizes = np.maximum(minimum, np.ceil(extents * d)).astype(int) + 2
        order = np.argsort(-sizes[:, 1], kind="stable")
        rectangles = [(int(v[0]), int(v[1])) for v in sizes[order]]
        choices = []
        square = min(2048, max(int(sizes[:, 0].max()),
                               int(np.ceil(np.sqrt(sizes.prod(axis=1).sum())))))
        for page_width in {2048, square}:
            positions = _shelf(rectangles, page_width, 2048)
            if positions is None:
                continue
            offsets = np.empty((len(sizes), 2), dtype=int)
            offsets[order] = positions
            # Nur rechts/unten auffüllen, Inhalt und Texeldichte bleiben gleich.
            size = tuple(map(int, ((offsets + sizes).max(axis=0) + 3) // 4 * 4))
            choices.append((size[0] * size[1], offsets, (size[0], size[1])))
        if not choices:
            return None
        _, offsets, size = min(choices, key=lambda choice: choice[0])
        return sizes, offsets, size

    # 4 Texel Mindestkante: genug für einen Farbverlauf je Dreieck, aber eine
    # 35-k-Kachel bleibt bei grobem Fehler um 1024² statt stets 2048² (Handy-Kachelbudget).
    for minimum in (4, 3, 2, 1):
        base = attempt(0, minimum)
        if base is None:
            continue
        if np.all(extents * density <= minimum):
            return base
        full = attempt(density, minimum)
        if full is not None:
            return full
        low, high = 0.0, density
        for _ in range(18):
            mid = (low + high) / 2
            result = attempt(mid, minimum)
            if result is None:
                high = mid
            else:
                low, base = mid, result
        return base
    raise ValueError("Dreiecksatlas passt selbst bei minimaler Fläche nicht auf 2048²")


def _barycentric(points: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    a = triangles[:, 1] - triangles[:, 0]
    b = triangles[:, 2] - triangles[:, 0]
    p = points - triangles[:, 0]
    aa = np.einsum("ij,ij->i", a, a)
    ab = np.einsum("ij,ij->i", a, b)
    bb = np.einsum("ij,ij->i", b, b)
    ap = np.einsum("ij,ij->i", a, p)
    bp = np.einsum("ij,ij->i", b, p)
    det = aa * bb - ab * ab
    safe = np.where(det > 1e-30, det, 1)
    v, w = (bb * ap - ab * bp) / safe, (aa * bp - ab * ap) / safe
    return np.column_stack([1 - v - w, v, w])


def _zellen(
    positions: np.ndarray, faces: np.ndarray, materials: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, int]:
    """Echte Indexkanten greedy paaren; quadratische Zelle mit echter Diagonale.

    Die Singulärwerte beider affiner Abbildungen müssen zusammen innerhalb
    Faktor zwei liegen. Das begrenzt sowohl Anisotropie als auch Dichtewechsel.
    Nichtmanifold-Kanten und entartete Dreiecke bleiben ungepaart.
    """
    triangles = positions[faces]
    lengths = np.linalg.norm(np.roll(triangles, -1, axis=1) - triangles, axis=2)
    rotation = (lengths.argmax(axis=1)[:, None] + np.arange(3)) % 3
    faces = np.take_along_axis(faces, rotation, axis=1).copy()
    triangles = positions[faces]
    edge, delta = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    width = np.linalg.norm(edge, axis=1)
    safe = np.maximum(width, 1e-20)
    x = np.einsum("ij,ij->i", edge, delta) / safe
    height = np.linalg.norm(np.cross(edge, delta), axis=1) / safe
    extents = np.column_stack([width, height])
    uv = np.zeros((len(faces), 3, 2))
    uv[:, 1, 0] = 1
    uv[:, 2, 0] = np.clip(x / safe, 0, 1)
    uv[:, 2, 1] = 1
    edges = np.sort(faces[:, [[0, 1], [1, 2], [2, 0]]].reshape(-1, 2), axis=1)
    owners = np.repeat(np.arange(len(faces)), 3)
    keys = np.column_stack([materials[owners].astype(np.int64), edges.astype(np.int64)])
    order = np.lexsort(keys.T[::-1])
    keys, owners = keys[order], owners[order]
    boundaries = np.r_[0, np.flatnonzero(np.any(np.diff(keys, axis=0), axis=1)) + 1, len(keys)]
    start = boundaries[:-1][np.diff(boundaries) == 2]
    a, b = owners[start], owners[start + 1]
    shared = keys[start, 1:]
    distinct = (a != b) & (shared[:, 0] != shared[:, 1])
    a, b, shared = a[distinct], b[distinct], shared[distinct]
    # Einheitliche Eckenreihenfolge: gemeinsame Kante 0--1, dritte Ecke 2.
    third_a = np.argmax(np.all(faces[a, :, None] != shared[:, None, :], axis=2), axis=1)
    third_b = np.argmax(np.all(faces[b, :, None] != shared[:, None, :], axis=2), axis=1)
    fa = np.column_stack([shared, faces[a, third_a]])
    fb = np.column_stack([shared, faces[b, third_b]])
    pa, pb = positions[fa], positions[fb]
    # UV-Ecken (0,0), (1,1), (1,0) bzw. (0,1).
    da, db = pa[:, 1] - pa[:, 0], pb[:, 1] - pb[:, 0]
    ca, cb = pa[:, 2] - pa[:, 0], pb[:, 2] - pb[:, 0]
    def spectrum(u: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        # Eigenwerte von J^T J; identisch zu quadrierten Singulärwerten,
        # ohne eine LAPACK-SVD pro Kantenkandidat.
        aa, bb = np.einsum("ij,ij->i", u, u), np.einsum("ij,ij->i", v, v)
        ab = np.einsum("ij,ij->i", u, v)
        spread = np.hypot(aa - bb, 2 * ab)
        return (aa + bb - spread) / 2, (aa + bb + spread) / 2

    low_a, high_a = spectrum(ca, da - ca)
    low_b, high_b = spectrum(db - cb, cb)
    valid = np.maximum(high_a, high_b) <= 4 * np.minimum(low_a, low_b)
    valid &= np.minimum(low_a, low_b) > 1e-40
    candidates = np.flatnonzero(valid)
    length = np.linalg.norm(da, axis=1)
    candidates = candidates[np.argsort(-length[candidates], kind="stable")]
    first_a = (faces[a] == shared[:, :1]).argmax(axis=1)
    first_b = (faces[b] == shared[:, :1]).argmax(axis=1)
    flip_a = faces[a, (first_a + 1) % 3] != shared[:, 1]
    flip_b = faces[b, (first_b + 1) % 3] != shared[:, 1]
    fa[flip_a] = fa[flip_a][:, [0, 2, 1]]
    fb[flip_b] = fb[flip_b][:, [0, 2, 1]]
    corners_a = np.array([[[0, 0], [1, 1], [1, 0]], [[0, 0], [1, 0], [1, 1]]])
    corners_b = np.array([[[0, 0], [1, 1], [0, 1]], [[0, 0], [0, 1], [1, 1]]])
    partner = np.full(len(faces), -1, dtype=int)
    pairs = 0
    for i in candidates:
        left, right = a[i], b[i]
        if partner[left] >= 0 or partner[right] >= 0:
            continue
        partner[left], partner[right] = right, left
        # Nur zyklisch umordnen: Winding/Normalen und Backface-Culling erhalten.
        faces[left], faces[right] = fa[i], fb[i]
        uv[left], uv[right] = corners_a[int(flip_a[i])], corners_b[int(flip_b[i])]
        extents[left] = extents[right] = length[i] / np.sqrt(2)
        pairs += 1
    representatives = np.minimum(np.arange(len(faces)), np.where(partner < 0,
                                  np.arange(len(faces)), partner))
    unique, cells = np.unique(representatives, return_inverse=True)
    return faces, cells, uv, extents[unique], pairs


def _randfuellen(pixels: np.ndarray, mask: np.ndarray, sizes: np.ndarray) -> None:
    """Gleich große Zellen stapeln; EDT darf niemals die Zelle wechseln.

    Der Ebenenabstand übersteigt die größte mögliche Distanz innerhalb einer
    Zelle. Daher liegt der nächste gültige Texel garantiert in derselben Ebene.
    Speicher und Arbeit bleiben linear in der gesamten gepolsterten Fläche.
    """
    counts = sizes.prod(axis=1)
    starts = np.cumsum(counts) - counts
    shapes, groups = np.unique(sizes, axis=0, return_inverse=True)
    for group, (w, h) in enumerate(shapes):
        cells = np.flatnonzero(groups == group)
        indices = starts[cells, None] + np.arange(w * h)
        valid = mask[indices].reshape(-1, h, w)
        if not np.all(valid.any(axis=(1, 2))):
            raise ValueError("Atlaszelle ohne Farbprobe")
        nearest = distance_transform_edt(
            ~valid, sampling=(float(np.hypot(w, h) + 1), 1, 1),
            return_distances=False, return_indices=True,
        )
        values = pixels[indices].reshape(-1, h, w, 3)
        pixels[indices] = values[tuple(nearest)].reshape(-1, w * h, 3)


def _farben(
    reference: Mesh, materials: np.ndarray, uv: np.ndarray,
    images: OrderedDict[int, np.ndarray], cache: BildCache,
) -> np.ndarray:
    """Quellfarben mit demselben begrenzten Bildcache bilinear abtasten."""
    colors = np.empty((len(materials), 3), dtype=np.uint8)
    for material in np.unique(materials):
        texture = reference.textures[int(material)]
        assert isinstance(texture, Path)
        hit = materials == material
        pixels = images.pop(int(material), None)
        if pixels is None:
            required = int(np.prod(cache.groesse(texture))) * 3
            while images and sum(a.nbytes for a in images.values()) + required > min(
                512 * 1024**2, cache.grenze,
            ):
                images.popitem(last=False)
            with cache.bild(texture) as image:
                pixels = np.asarray(image)
        images[int(material)] = pixels
        coordinates = [uv[hit, 1] * pixels.shape[0] - 0.5,
                       uv[hit, 0] * pixels.shape[1] - 0.5]
        for channel in range(3):
            colors[hit, channel] = map_coordinates(
                pixels[:, :, channel], coordinates, order=1,
                mode="grid-wrap", prefilter=False,
            )
    return colors


def backen(
    mesh: Mesh, source: Mesh, error: float, *, cache: BildCache,
    messung: dict[str, Any] | None = None,
) -> tuple[Mesh, dict[int, bytes]]:
    """Neue UV je reduziertem Dreieck, Farbe von der exakten nächsten Kindfläche.

    Zieldichte wie ``vorbereiten``: acht Texel je geometricError, maximal
    Quelldichte. Die Mindestfläche hebt untertexelgroße Dreiecke an, damit
    Quellinseln die Seite nicht mit Polstern füllen. Bei Platzmangel sinkt erst
    die Dichte, dann die Mindestgröße. Ein Texel Rand plus Dilatation aller
    Rechtecktexel mit der nächsten eigenen Rasterfarbe füllt den Rand, ohne
    eine fremde Insel oder den schwarzen Hintergrund einzumischen.
    """
    started = time.perf_counter()
    textured = np.array([source.textures[int(m)] is not None for m in source.materials])
    selected = np.array([mesh.textures[int(m)] is not None for m in mesh.materials])
    parts = []
    plain = []
    for material in np.unique(mesh.materials[~selected]):
        plain.append(compact(mesh, mesh.triangles[mesh.materials == material],
                             mesh.materials[mesh.materials == material]))
    if not np.any(selected):
        return mesh, {}
    if source.uv is None or mesh.uv is None:
        raise ValueError("Textur ohne UV")
    reference = compact(source, source.triangles[textured], source.materials[textured])
    assert reference.uv is not None
    index = None
    density = 0.0
    for material in np.unique(reference.materials):
        texture = reference.textures[int(material)]
        if not isinstance(texture, Path):
            raise ValueError("Backquelle benötigt lokale Texturdateien")
        part = compact(reference, reference.triangles[reference.materials == material],
                       reference.materials[reference.materials == material])
        density = max(density, _dichte(part, np.asarray(cache.groesse(texture))))
    density = min(density, 8 / max(error, 1e-12))
    faces = mesh.triangles[selected].copy()
    faces, cells, local_uv, extents, pairs = _zellen(
        mesh.positions, faces, mesh.materials[selected],
    )
    triangles = mesh.positions[faces]
    # Unverändert übernommene Kindflächen brauchen keine Suche je Texel. Nur
    # bitgleiche Weltkoordinaten mit eindeutiger Eckenzuordnung und demselben
    # Material zertifizieren die Quellfläche; alles andere nimmt die exakte Suche.
    reference_triangles = reference.positions[reference.triangles]
    nearest_face = cKDTree(reference_triangles.mean(axis=1)).query(triangles.mean(axis=1))[1]
    equal = np.all(triangles[:, :, None] == reference_triangles[nearest_face, None], axis=3)
    direct = np.all(equal.sum(axis=1) == 1, axis=1) & np.all(equal.sum(axis=2) == 1, axis=1)
    direct &= mesh.materials[selected] == reference.materials[nearest_face]
    source_uv = np.take_along_axis(reference.uv[reference.triangles[nearest_face]],
                                   equal.argmax(axis=2)[:, :, None], axis=1)
    sizes, offsets, size = _plan(extents, density)
    interior = sizes - 2
    local_uv *= interior[cells, None, :]
    uv_pixels = local_uv + offsets[cells, None, :] + 1
    counts = sizes.prod(axis=1)
    ends = np.cumsum(counts)
    starts = ends - counts
    baked = int(ends[-1])
    pixels_out = np.zeros((baked, 3), dtype=np.uint8)
    mask = np.zeros(baked, dtype=bool)
    # Jede Fläche rasterisieren, Paare teilen denselben linearen Zellbereich.
    face_ends = np.cumsum(counts[cells])
    face_starts = face_ends - counts[cells]
    _, first_face = np.unique(cells, return_index=True)
    secondary = np.arange(len(faces)) != first_face[cells]
    sampled = 0
    images: OrderedDict[int, np.ndarray] = OrderedDict()
    for start in range(0, int(face_ends[-1]), 8192):
        linear = np.arange(start, min(start + 8192, int(face_ends[-1])))
        ids = np.searchsorted(face_ends, linear, side="right")
        relative = linear - face_starts[ids]
        cell = cells[ids]
        px, py = relative % sizes[cell, 0], relative // sizes[cell, 0]
        pixel = np.column_stack([px + 0.5, py + 0.5]) - 1
        t = local_uv[ids]
        # Alle lokalen Dreiecke beginnen im Ursprung; 2D-Inverse direkt anwenden.
        d1, d2 = t[:, 1], t[:, 2]
        det = d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]
        safe_det = np.where(np.abs(det) > 1e-20, det, 1)
        b1 = (pixel[:, 0] * d2[:, 1] - pixel[:, 1] * d2[:, 0]) / safe_det
        b2 = (d1[:, 0] * pixel[:, 1] - d1[:, 1] * pixel[:, 0]) / safe_det
        weights = np.column_stack([1 - b1 - b2, b1, b2])
        inside = np.all(weights >= -1e-12, axis=1)
        # Die echte gemeinsame Diagonale genau einmal abtasten.
        inside &= ~(secondary[ids] & (px == py))
        # Selbst entartete/subtexelgroße Flächen bekommen einen eigenen Farbanker.
        anchor = relative == sizes[cell, 0] + 1
        degenerate = np.abs(det) < 1e-20
        weights[degenerate & anchor] = 1 / 3
        inside = np.where(degenerate, anchor, inside)
        ids, relative, weights = ids[inside], relative[inside], weights[inside]
        if not len(ids):
            continue
        sampled += len(ids)
        uv = np.einsum("ij,ijk->ik", weights, source_uv[ids])
        materials = mesh.materials[selected][ids].copy()
        search = ~direct[ids]
        if np.any(search):
            if index is None:
                index = _ObereDistanz(reference)
            points = np.einsum("ij,ijk->ik", weights[search], triangles[ids[search]])
            nearest, source_ids = index.closest(points)
            source_tri = reference.triangles[source_ids]
            source_weights = _barycentric(nearest, reference.positions[source_tri])
            uv[search] = np.einsum("ij,ijk->ik", source_weights, reference.uv[source_tri])
            materials[search] = reference.materials[source_ids]
        colors = _farben(reference, materials, uv, images, cache)
        target = starts[cells[ids]] + relative
        pixels_out[target] = colors
        mask[target] = True
    _randfuellen(pixels_out, mask, sizes)
    canvas = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    for start in range(0, baked, 65536):
        linear = np.arange(start, min(start + 65536, baked))
        ids = np.searchsorted(ends, linear, side="right")
        relative = linear - starts[ids]
        px, py = relative % sizes[ids, 0], relative // sizes[ids, 0]
        canvas[offsets[ids, 1] + py, offsets[ids, 0] + px] = pixels_out[linear]
    used = (offsets + sizes).max(axis=0)
    canvas[:, used[0]:] = canvas[:, used[0] - 1:used[0]]
    canvas[used[1]:] = canvas[used[1] - 1:used[1]]
    data = io.BytesIO()
    Image.fromarray(canvas).save(data, format="PNG")
    encoded = {0: data.getvalue()}
    parts.append(replace(
        mesh, positions=triangles.reshape(-1, 3),
        triangles=np.arange(faces.size, dtype=np.uint32).reshape(-1, 3),
        uv=(uv_pixels / size).reshape(-1, 2),
        colors=(mesh.colors[faces].reshape(-1, mesh.colors.shape[1])
                if mesh.colors is not None else None),
        materials=np.zeros(len(faces), dtype=np.int32),
    ))
    for material, part in enumerate(plain, start=1):
        parts.append(replace(part, materials=np.full(len(part.triangles), material)))
    if messung is not None:
        area = np.linalg.norm(np.cross(triangles[:, 1] - triangles[:, 0],
                                       triangles[:, 2] - triangles[:, 0]), axis=1).sum() / 2
        d1, d2 = local_uv[:, 1] - local_uv[:, 0], local_uv[:, 2] - local_uv[:, 0]
        content = float(np.abs(d1[:, 0] * d2[:, 1] - d1[:, 1] * d2[:, 0]).sum() / 2)
        messung.update(
            bakeSeconds=time.perf_counter() - started, bakeTexels=sampled,
            bakeTriangles=len(faces), bakePaddedTexels=baked,
            pairs=pairs, unpaired=len(faces) - 2 * pairs, pageTexels=size[0] * size[1],
            islands=len(sizes), textureTexelsPerM=float(np.sqrt(content / max(area, 1e-30))),
            contentFill=content / (size[0] * size[1]),
            texturePages=[{"size": list(size), "fill": baked / (size[0] * size[1]),
                           "contentFill": content / (size[0] * size[1])}],
        )
    return _verbinden(mesh, parts, encoded), encoded
