"""Blockweise räumliche Dreiecksprojektion für Fehler und Texturtransfer."""

import numpy as np
from scipy.spatial import cKDTree

from ..mesh_source import Mesh
from ..mesh_surface import _closest_point_on_triangle


class _ObereDistanz:
    """16 Kandidaten je Faktor-2-Radiusklasse, stets >= Abstand zur Gesamtfläche.

    Große Flächen konkurrieren so nicht mit Mittelpunkten winziger Splitter.
    Kein Kugel-Fallback: Jeder Kandidatenpunkt liegt auf der Zielfläche; ein
    übersehenes Dreieck macht die Schranke ausschließlich größer.
    """

    def __init__(self, mesh: Mesh) -> None:
        self.triangles = mesh.positions[mesh.triangles]
        centers = self.triangles.mean(axis=1)
        radii = np.linalg.norm(self.triangles - centers[:, None, :], axis=2).max(axis=1)
        classes = np.floor(np.log2(np.maximum(radii, 1e-12))).astype(int)
        self.radii = {}
        self.buckets = []
        for label in np.unique(classes):
            ids = np.flatnonzero(classes == label)
            tree = cKDTree(centers[ids])
            self.radii[id(tree)] = float(radii[ids].max())
            self.buckets.append((tree, ids, min(16, len(ids))))

    def distance(self, points: np.ndarray) -> np.ndarray:
        distances = np.empty(len(points), dtype=np.float64)
        # Auch der erste Mittelpunktaufruf kann 200.000 Punkte enthalten.
        # Kandidatenarrays deshalb unabhängig vom Aufrufer begrenzen.
        for offset in range(0, len(points), 8192):
            block = points[offset:offset + 8192]
            best = np.full(len(block), np.inf)
            for tree, indices, k in self.buckets:
                _, ids = tree.query(block, k=k)
                if k == 1:
                    ids = ids[:, None]
                tri = self.triangles[indices[ids]]
                closest = _closest_point_on_triangle(
                    block[:, None, :], tri[:, :, 0], tri[:, :, 1], tri[:, :, 2],
                )
                best = np.minimum(best, np.linalg.norm(
                    closest - block[:, None, :], axis=2,
                ).min(axis=1))
            distances[offset:offset + len(block)] = best
        return distances

    def closest(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Exakte Projektion samt Quellindex; Radiuszertifikat und seltener Kugelfallback.

        Dieselben Radiusklassen und Kandidaten wie bei der Fehlerschranke. Für
        Farben reicht eine obere Abstandsschranke nicht: nicht zertifizierte
        Kandidaten werden um alle potentiell näheren Dreiecke ergänzt.
        """
        result = np.empty_like(points, dtype=np.float64)
        faces = np.empty(len(points), dtype=np.int64)
        for offset in range(0, len(points), 4096):
            block = points[offset:offset + 4096]
            best = np.full(len(block), np.inf)
            chosen = np.zeros(len(block), dtype=np.int64)
            projected = np.zeros_like(block)
            for tree, indices, k in self.buckets:
                # Acht Startkandidaten reichen oft zum Radiuszertifikat.
                # Unbewiesene Fälle bekommen weiterhin den exakten Kugelfallback.
                k = min(k, 8)
                center_distance, ids = tree.query(block, k=k)
                if k == 1:
                    ids = ids[:, None]
                    center_distance = center_distance[:, None]
                radius = self.radii[id(tree)]
                rows = np.flatnonzero(center_distance[:, 0] - radius <= best + 1e-12)
                if not len(rows):
                    continue
                tri = self.triangles[indices[ids[rows]]]
                closest = _closest_point_on_triangle(
                    block[rows, None, :], tri[:, :, 0], tri[:, :, 1], tri[:, :, 2],
                )
                distances = np.linalg.norm(closest - block[rows, None, :], axis=2)
                pick = distances.argmin(axis=1)
                local = np.arange(len(rows))
                candidate = distances[local, pick]
                hit = candidate < best[rows]
                best[rows[hit]] = candidate[hit]
                chosen[rows[hit]] = indices[ids[rows, pick]][hit]
                projected[rows[hit]] = closest[local, pick][hit]
                if k == len(indices):
                    continue
                # Ein ausgeschlossenes Dreieck liegt mindestens dist_k - R entfernt.
                uncertain = np.flatnonzero(best + 1e-12 > center_distance[:, -1] - radius)
                # Ragged Kugelkandidaten ebenfalls vektorisieren. Einzelaufrufe
                # pro unbewiesenem Texel dominieren sonst die gesamte Backzeit.
                for start in range(0, len(uncertain), 32):
                    rows = uncertain[start:start + 32]
                    lists = tree.query_ball_point(block[rows], best[rows] + radius)
                    counts = np.array([len(v) for v in lists], dtype=np.int64)
                    starts = np.cumsum(counts) - counts
                    candidates = indices[np.concatenate(lists).astype(np.int64)]
                    owners = np.repeat(rows, counts)
                    d = np.empty(len(candidates))
                    for first in range(0, len(candidates), 8192):
                        stop = min(first + 8192, len(candidates))
                        t = self.triangles[candidates[first:stop]]
                        p = block[owners[first:stop]]
                        q = _closest_point_on_triangle(p, t[:, 0], t[:, 1], t[:, 2])
                        d[first:stop] = np.linalg.norm(q - p, axis=1)
                    minima = np.minimum.reduceat(d, starts)
                    winners = np.minimum.reduceat(
                        np.where(d == np.repeat(minima, counts), np.arange(len(d)), len(d)),
                        starts,
                    )
                    hit = minima < best[rows]
                    chosen[rows[hit]] = candidates[winners[hit]]
                    best[rows[hit]] = minima[hit]
                    t = self.triangles[chosen[rows[hit]]]
                    projected[rows[hit]] = _closest_point_on_triangle(
                        block[rows[hit]], t[:, 0], t[:, 1], t[:, 2],
                    )
            result[offset:offset + len(block)] = projected
            faces[offset:offset + len(block)] = chosen
        return result, faces
