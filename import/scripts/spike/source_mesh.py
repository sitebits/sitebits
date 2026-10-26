"""Quellmesh (feste Schnittstelle): Dreiecksnetz lokal im Paketrahmen (Z oben) +
Punkt-Dreieck-Abstand für den Ausgabevertrag-Prüfer.

``SurfaceIndex`` implementiert Ericson, "Real-Time Collision Detection" 5.1.5
(``ClosestPtPointTriangle``), vektorisiert über NumPy und blockweise über
``DISTANCE_BLOCK`` Punkte, damit 10 000 Stichproben gegen Millionen Dreiecke (OBJ-Leser:
Testmesh hq, 7,7 Mio.) nicht die (Kandidaten x Block)-Zwischenarrays auf einmal
im Speicher halten.

Zertifiziert korrekt (Fix Round 1, Review-Befund): die ``k`` nächsten Dreiecksmitten sind
nur ein schneller Kandidatensatz, kein Beweis. Für jeden Punkt wird geprüft, ob das beste
Ergebnis der ``k`` Kandidaten durch KEIN ausgeschlossenes Dreieck mehr unterboten werden
kann (Zertifikat unten); wenn nicht, folgt ein exakter Fallback über einen Kugel-Query
(``query_ball_point``), der garantiert alle Dreiecke erfasst, die noch besser sein
könnten. Damit ist ``distance()`` für JEDES Eingabe-Mesh exakt, nicht nur für die
winzigen synthetischen Testfälle dieses Tasks (bei denen ``k`` ohnehin auf die
tatsächliche Dreieckszahl begrenzt wird, siehe ``__init__``).

Zertifikat: ``R`` ist eine obere Schranke für den Abstand Dreiecksmitte → eigenes
Dreieck (das Maximum liegt für ein Dreieck immer auf einer der drei Ecken, da das
Dreieck konvex ist). Für ein ausgeschlossenes Dreieck ``j`` (Mitte mindestens
``dist_kth`` von P entfernt, da nicht unter den ``k`` nächsten) gilt nach der
Dreiecksungleichung ``dist(P, Dreieck_j) >= dist(P, Mitte_j) - R_j >= dist_kth - R``.
Ist der beste gefundene Abstand ``d_best <= dist_kth - R``, kann kein ausgeschlossenes
Dreieck ``d_best`` unterbieten — das Ergebnis ist zertifiziert korrekt. Sonst: Fallback
über alle Dreiecke, deren Mitte innerhalb ``d_best + R`` von P liegt (das deckt jedes
Dreieck ab, das ``d_best`` noch unterbieten könnte).

Eimer nach Umkreisradius (Fix Round 2, Review-Befund): ein EINZIGES globales ``R``
über alle Dreiecke bläst den Kugel-Fallback für JEDEN unzertifizierten Punkt auf den
Radius des GRÖSSTEN Dreiecks im ganzen Mesh auf — ein Photogrammetrie-Mesh mit einem
großen (Sockel-)Dreieck neben einem feinen Gitter macht so praktisch jede
Fallback-Abfrage riesig, unabhängig davon, wie klein die Dreiecke lokal tatsächlich
sind (gemessen: 300 Abfragen auf 180 001 Dreiecken, davon eines mit 33,5 cm
Schenkeln neben einem 2-mm-Gitter, brauchten 6,9 s statt Millisekunden). Deshalb
werden Dreiecke nach ``R_i`` in Faktor-2-Klassen (Eimer) sortiert — Eimer ``b`` enthält
alle Dreiecke mit ``R_i`` in ``[2**b, 2**(b+1))``. Jeder Eimer bekommt seinen EIGENEN
``cKDTree`` und sein EIGENES (eng begrenztes, höchstens 2x) ``R_bucket``; Zertifikat
und Fallback laufen pro Eimer, das Ergebnis ist das Minimum über alle Eimer. Das bleibt
exakt (jeder Eimer liefert das exakte Minimum ÜBER SEINE Dreiecke, das Minimum über
alle Eimer ist das globale Minimum über alle Dreiecke) — nur der große Ausreißer
bekommt einen großen Fallback-Radius, nicht das ganze Mesh.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from scipy.spatial import cKDTree

# Blockgröße für Punkt-Dreieck-Abstand — begrenzt den Speicher der (Block x k x 3 x 3)-
# Zwischenarrays unabhängig von der Gesamtstichprobenzahl.
DISTANCE_BLOCK = 50_000


@dataclass
class SourceMesh:
    """Quellmesh, lokal im Paketrahmen (Z oben), Float64.

    ``uv`` folgt, wie ``.obj``, der Konvention "v nach oben" (OBJ/Blender-Konvention);
    glTF-Inhalte speichern UV mit v nach unten — beim Einlesen eines Kachelinhalts
    (``tiles_contract``) wird das gespiegelt, nicht hier.
    """

    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (M, 3) int64
    uv: np.ndarray | None  # (M, 3, 2) float32, je Dreiecksecke
    texture: Path | None

    def __post_init__(self) -> None:
        self.vertices = np.asarray(self.vertices, dtype=np.float64)
        self.faces = np.asarray(self.faces, dtype=np.int64)
        if self.uv is not None:
            self.uv = np.asarray(self.uv, dtype=np.float32)

    def triangle_vertices(self) -> np.ndarray:
        """(M, 3, 3): je Dreieck seine drei Eckpunkte."""
        return cast(np.ndarray, self.vertices[self.faces])

    def triangle_areas(self) -> np.ndarray:
        tv = self.triangle_vertices()
        cross = np.cross(tv[:, 1] - tv[:, 0], tv[:, 2] - tv[:, 0])
        return 0.5 * np.linalg.norm(cross, axis=1)

    def area(self) -> float:
        return float(self.triangle_areas().sum())

    def sample_surface(
        self, n: int, seed: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Flächengewichtete Stichprobe.

        Rückgabe: (Punkte (n, 3) float64, Dreiecksindex (n,) int64,
        baryzentrische Koordinaten (n, 3) float64) — uniforme Dreieckssamplung nach
        Osada et al. (``b0 = 1 - sqrt(r1)``, ``b1 = sqrt(r1) * (1 - r2)``,
        ``b2 = sqrt(r1) * r2``), Dreieckswahl proportional zur Fläche.
        """
        rng = np.random.default_rng(seed)
        areas = self.triangle_areas()
        total = areas.sum()
        if total <= 0:
            raise ValueError("Quellmesh ohne Fläche (alle Dreiecke entartet oder leer)")
        probs = areas / total
        tri_idx = rng.choice(len(areas), size=n, p=probs)
        r1 = rng.random(n)
        r2 = rng.random(n)
        sqrt_r1 = np.sqrt(r1)
        b0 = 1.0 - sqrt_r1
        b1 = sqrt_r1 * (1.0 - r2)
        b2 = sqrt_r1 * r2
        bary = np.column_stack([b0, b1, b2])
        tv = self.triangle_vertices()[tri_idx]  # (n, 3, 3)
        points = np.einsum("ij,ijk->ik", bary, tv)
        return points, tri_idx, bary


def _closest_point_on_triangle(
    p: np.ndarray, a: np.ndarray, b: np.ndarray, c: np.ndarray
) -> np.ndarray:
    """Nächster Punkt auf Dreieck ABC zu P — Ericson, "Real-Time Collision Detection",
    5.1.5 (``ClosestPtPointTriangle``), vektorisiert über beliebige führende Achsen
    (letzte Achse ist xyz). Die sequenziellen Regionstests des Originals werden hier als
    Überschreibungen in umgekehrter Prioritätsreihenfolge nachgebildet (niedrigste
    Priorität zuerst berechnet, höchste zuletzt geschrieben) — die Regionen sind bei
    korrekten Dreiecken disjunkt, daher ist das Ergebnis identisch zur seriellen If-Kette.

    Entartete (flächenlose) Dreiecke: Divisionen werden mit ``errstate`` abgefangen und
    durch einen Platzhalter-Nenner 1.0 ersetzt, wo der echte Nenner 0 ist — betroffene
    Punkte werden aber ohnehin von einer der Kanten-/Eckenregionen abgedeckt (ein
    entartetes Dreieck hat keine echte Flächenregion), sodass der Platzhalterwert nie
    das Endergebnis bestimmt.
    """
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = np.sum(ab * ap, axis=-1)
    d2 = np.sum(ac * ap, axis=-1)

    bp = p - b
    d3 = np.sum(ab * bp, axis=-1)
    d4 = np.sum(ac * bp, axis=-1)

    cp = p - c
    d5 = np.sum(ab * cp, axis=-1)
    d6 = np.sum(ac * cp, axis=-1)

    va = d3 * d6 - d5 * d4
    vb = d5 * d2 - d1 * d6
    vc = d1 * d4 - d3 * d2

    with np.errstate(divide="ignore", invalid="ignore"):
        denom = va + vb + vc
        denom_safe = np.where(denom != 0, denom, 1.0)
        v_face = vb / denom_safe
        w_face = vc / denom_safe
        out = a + ab * v_face[..., None] + ac * w_face[..., None]

        # Kante BC: va <= 0, d4 - d3 >= 0, d5 - d6 >= 0.
        mask_bc = (va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0)
        denom_bc = (d4 - d3) + (d5 - d6)
        denom_bc_safe = np.where(denom_bc != 0, denom_bc, 1.0)
        w_bc = (d4 - d3) / denom_bc_safe
        out = np.where(mask_bc[..., None], b + (c - b) * w_bc[..., None], out)

        # Kante AC: vb <= 0, d2 >= 0, d6 <= 0.
        mask_ac = (vb <= 0) & (d2 >= 0) & (d6 <= 0)
        denom_ac = d2 - d6
        denom_ac_safe = np.where(denom_ac != 0, denom_ac, 1.0)
        w_ac = d2 / denom_ac_safe
        out = np.where(mask_ac[..., None], a + ac * w_ac[..., None], out)

        # Ecke C: d6 >= 0, d5 <= d6.
        mask_c = (d6 >= 0) & (d5 <= d6)
        out = np.where(mask_c[..., None], c, out)

        # Kante AB: vc <= 0, d1 >= 0, d3 <= 0.
        mask_ab = (vc <= 0) & (d1 >= 0) & (d3 <= 0)
        denom_ab = d1 - d3
        denom_ab_safe = np.where(denom_ab != 0, denom_ab, 1.0)
        v_ab = d1 / denom_ab_safe
        out = np.where(mask_ab[..., None], a + ab * v_ab[..., None], out)

        # Ecke B: d3 >= 0, d4 <= d3.
        mask_b = (d3 >= 0) & (d4 <= d3)
        out = np.where(mask_b[..., None], b, out)

        # Ecke A: d1 <= 0, d2 <= 0 (höchste Priorität — zuletzt geschrieben).
        mask_a = (d1 <= 0) & (d2 <= 0)
        out = np.where(mask_a[..., None], a, out)

    return out


@dataclass
class _Bucket:
    """Eine Faktor-2-Radiusklasse: eigener ``cKDTree`` über nur die Dreiecksmitten
    dieses Eimers, eigenes (eng begrenztes) ``radius``. ``indices`` bildet einen
    lokalen Kandidatenindex (aus ``tree.query``) auf den GLOBALEN Dreiecksindex ab
    (in ``SurfaceIndex._tv``/``._faces``) zurück."""

    tree: cKDTree
    indices: np.ndarray  # (b,) int64, globale Dreiecksindizes in diesem Eimer
    radius: float
    k: int
    exhaustive: bool


class SurfaceIndex:
    """Abstand Punkt → Fläche: exakter Punkt-Dreieck-Abstand, zertifiziert über die ``k``
    nächsten Dreiecksmitten JE EIMER (Faktor-2-Radiusklasse) oder — falls das Zertifikat
    nicht greift — über einen exakten Kugel-Fallback innerhalb desselben Eimers (siehe
    Modul-Docstring), blockweise über ``DISTANCE_BLOCK`` Punkte. Das Ergebnis ist das
    Minimum über alle Eimer und bleibt exakt."""

    def __init__(self, mesh_vertices: np.ndarray, faces: np.ndarray, k: int = 16) -> None:
        vertices = np.asarray(mesh_vertices, dtype=np.float64)
        self._faces = np.asarray(faces, dtype=np.int64)
        if len(self._faces) == 0:
            raise ValueError("SurfaceIndex braucht mindestens ein Dreieck")
        self._tv = vertices[self._faces]  # (M, 3, 3)
        centroids = self._tv.mean(axis=1)
        m = len(self._faces)
        # Bei wenigen Dreiecken (alle synthetischen Testfälle, einzelne Kacheln) ist
        # k = Dreieckszahl exakte Brute-Force-Suche statt Näherung — das Zertifikat
        # unten ist dann ohnehin trivial erfüllt (keine ausgeschlossenen Dreiecke).
        self._k = max(1, min(k, m))
        # Radius je Dreieck: größter Abstand Dreiecksmitte -> eigene Ecke (Konvexität,
        # siehe Modul-Docstring fürs Zertifikat, das das nutzt).
        vertex_dists = np.linalg.norm(self._tv - centroids[:, None, :], axis=-1)
        radii = vertex_dists.max(axis=1) if m else np.zeros(0)
        self._buckets = self._build_buckets(centroids, radii)
        self._last_query_count = 0
        self._last_fallback_count = 0

    def _build_buckets(self, centroids: np.ndarray, radii: np.ndarray) -> list[_Bucket]:
        """Faktor-2-Radiusklassen (Fix Round 2): Eimer ``b`` enthält alle Dreiecke mit
        Radius in ``[2**b, 2**(b+1))``. Entartete (Radius 0) Dreiecke landen im
        kleinsten Eimer."""
        m = len(radii)
        if m == 0:
            return []
        eps = 1e-12
        safe_radii = np.maximum(radii, eps)
        bucket_of = np.floor(np.log2(safe_radii)).astype(np.int64)
        buckets: list[_Bucket] = []
        for b in np.unique(bucket_of):
            idxs = np.nonzero(bucket_of == b)[0]
            k_b = max(1, min(self._k, len(idxs)))
            buckets.append(
                _Bucket(
                    tree=cKDTree(centroids[idxs]),
                    indices=idxs,
                    radius=float(radii[idxs].max()),
                    k=k_b,
                    exhaustive=k_b == len(idxs),
                )
            )
        return buckets

    def _exact_for_candidates(
        self, p: np.ndarray, cand: np.ndarray
    ) -> tuple[np.ndarray, int, np.ndarray]:
        """Exakter Punkt-Dreieck-Abstand von P zu jedem Dreieck in ``cand`` (1-D Array
        GLOBALER Dreiecksindizes) — liefert (bester Abstand, Index INS ``cand``-Array,
        nächster Punkt)."""
        a, b, c = self._tv[cand, 0], self._tv[cand, 1], self._tv[cand, 2]
        cp = _closest_point_on_triangle(p[None, :], a, b, c)
        d = np.linalg.norm(cp - p[None, :], axis=-1)
        j = int(np.argmin(d))
        return d[j], j, cp[j]

    def _query_bucket(
        self, block: np.ndarray, bucket: _Bucket
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, int]:
        """Zertifiziert-oder-Fallback, beschränkt auf die Dreiecke EINES Eimers.
        Liefert (Abstand, GLOBALER Dreiecksindex, nächster Punkt, Anzahl Fallbacks)."""
        kth_dist, cand_local = bucket.tree.query(block, k=bucket.k)
        if bucket.k == 1:
            kth_dist = kth_dist[:, None]
            cand_local = cand_local[:, None]
        cand_global = bucket.indices[cand_local]
        a = self._tv[cand_global, 0]
        b = self._tv[cand_global, 1]
        c = self._tv[cand_global, 2]
        p = block[:, None, :]
        cp = _closest_point_on_triangle(p, a, b, c)
        d = np.linalg.norm(cp - p, axis=-1)
        best = np.argmin(d, axis=1)
        rows = np.arange(len(block))
        d_best = d[rows, best]
        idx_best = cand_global[rows, best]
        cp_best = cp[rows, best]

        n_fallback = 0
        if not bucket.exhaustive:
            dist_kth = kth_dist[:, -1]
            certified = d_best <= dist_kth - bucket.radius
            uncertified_rows = np.nonzero(~certified)[0]
            n_fallback = len(uncertified_rows)
            for row in uncertified_rows:
                pt = block[row]
                ball_local = bucket.tree.query_ball_point(pt, d_best[row] + bucket.radius)
                ball_local = np.asarray(ball_local, dtype=np.int64)
                if len(ball_local) == 0:
                    continue
                ball_global = bucket.indices[ball_local]
                d_fb, j_fb, cp_fb = self._exact_for_candidates(pt, ball_global)
                if d_fb < d_best[row]:
                    d_best[row] = d_fb
                    idx_best[row] = ball_global[j_fb]
                    cp_best[row] = cp_fb
        return d_best, idx_best, cp_best, n_fallback

    def query(self, points: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """(Abstand (n,) float64, Dreiecksindex (n,) int64, nächster Punkt (n, 3) float64)."""
        points = np.asarray(points, dtype=np.float64)
        n = len(points)
        out_dist = np.empty(n, dtype=np.float64)
        out_idx = np.empty(n, dtype=np.int64)
        out_closest = np.empty((n, 3), dtype=np.float64)
        fallback_count = 0
        for start in range(0, n, DISTANCE_BLOCK):
            end = min(start + DISTANCE_BLOCK, n)
            block = points[start:end]
            best_dist = np.full(len(block), np.inf)
            best_idx = np.full(len(block), -1, dtype=np.int64)
            best_cp = np.zeros((len(block), 3), dtype=np.float64)
            for bucket in self._buckets:
                d_b, idx_b, cp_b, n_fb = self._query_bucket(block, bucket)
                fallback_count += n_fb
                better = d_b < best_dist
                best_dist[better] = d_b[better]
                best_idx[better] = idx_b[better]
                best_cp[better] = cp_b[better]
            out_dist[start:end] = best_dist
            out_idx[start:end] = best_idx
            out_closest[start:end] = best_cp
        self._last_query_count = n
        self._last_fallback_count = fallback_count
        return out_dist, out_idx, out_closest

    def distance(self, points: np.ndarray) -> np.ndarray:
        d, _, _ = self.query(points)
        return d

    def stats(self) -> dict[str, object]:
        """Diagnose (Fix Round 2, Review-Wunsch): Eimer-Radien/-Größen und die
        Fallback-Rate der letzten ``query()``/``distance()``-Aufrufe — damit spätere Schritte
        das an echten Daten (Testmesh hq) beobachten können."""
        n = self._last_query_count
        return {
            "buckets": [
                {"radiusM": b.radius, "triangles": len(b.indices)} for b in self._buckets
            ],
            "lastQueryCount": n,
            "lastFallbackCount": self._last_fallback_count,
            "lastFallbackRate": (self._last_fallback_count / n) if n else 0.0,
        }
