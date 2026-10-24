"""Schmale Raycast-Schnittstelle (Embree über embreex).

Embree rechnet in float32. Deshalb liegt die Szene relativ zu `zentrum` (Flächenschwerpunkt im
Paket-
Lokalrahmen): Bei UTM-Werten um 5,8·10⁶ m löste float32 nur noch ≈ 0,5 m auf, hier unter 0,1 mm.
`tnear` (Mindesttrefferabstand) wird über einen verschobenen Ursprung umgesetzt.
"""

from __future__ import annotations

import numpy as np
from embreex import rtcore_scene as rtcs
from embreex.mesh_construction import TriangleMesh

BLOCK = 1 << 21


class Szene:
    def __init__(self, positionen: np.ndarray, dreiecke: np.ndarray, zentrum: np.ndarray) -> None:
        self.zentrum = np.asarray(zentrum, dtype=np.float64)
        self._szene = rtcs.EmbreeScene()
        self._netz = TriangleMesh(
            self._szene,
            np.ascontiguousarray(positionen - self.zentrum, dtype=np.float32),
            np.ascontiguousarray(dreiecke, dtype=np.int32),
        )
        self.dreiecke = len(dreiecke)

    def _vorbereiten(
        self, ursprung: np.ndarray, richtung: np.ndarray, tnear: float | np.ndarray
    ) -> tuple[np.ndarray, np.ndarray]:
        tn = np.broadcast_to(np.asarray(tnear, dtype=np.float64), (len(ursprung),))
        o = (ursprung - self.zentrum) + richtung * tn[:, None]
        return np.ascontiguousarray(o, np.float32), np.ascontiguousarray(richtung, np.float32)

    def verdeckt(
        self,
        ursprung: np.ndarray,
        richtung: np.ndarray,
        tnear: float | np.ndarray = 0.0,
        tfar: float = 1e30,
    ) -> np.ndarray:
        """True, wenn zwischen `tnear` und `tfar` entlang der Richtung ein Dreieck liegt."""
        aus = np.empty(len(ursprung), dtype=bool)
        for a in range(0, len(ursprung), BLOCK):
            o, d = self._vorbereiten(
                ursprung[a : a + BLOCK],
                richtung[a : a + BLOCK],
                np.asarray(tnear)[a : a + BLOCK] if np.ndim(tnear) else tnear,
            )
            weite = np.full(len(o), tfar, dtype=np.float32)
            aus[a : a + BLOCK] = self._szene.run(o, d, dists=weite, query="OCCLUDED") >= 0
        return aus

    def treffer(
        self, ursprung: np.ndarray, richtung: np.ndarray, tfar: float = 1e30
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Erster Treffer: (Abstand, Dreieck, Dreiecksnormale); sonst Abstand inf, Index -1."""
        o, d = self._vorbereiten(ursprung, richtung, 0.0)
        r = self._szene.run(o, d, dists=np.full(len(o), tfar, dtype=np.float32), output=1)
        prim = np.asarray(r["primID"], dtype=np.int64)
        t = np.where(prim >= 0, np.asarray(r["tfar"], dtype=np.float64), np.inf)
        ng = np.asarray(r["Ng"], dtype=np.float64)
        laenge = np.linalg.norm(ng, axis=1, keepdims=True)
        return t, prim, np.divide(ng, laenge, out=np.zeros_like(ng), where=laenge > 0)
