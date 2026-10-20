"""Rahmenprüfung: Stammen Fotos und Paketwolke aus demselben Ausrichtungsstand?

Ohne Bildkorrelation: Die Tie Points des Metashape-Exports liegen im Weltrahmen der Fotos. Ihr
Median-Abstand zum nächsten Punkt der Paketwolke ist bei gleichem Stand im Zentimeterbereich,
bei einem anderen Stand (neu ausgerichtet, anders georeferenziert) Dezimeter bis Meter. Gezählt
werden nur Tie Points innerhalb der Grundrissausdehnung der Wolke.

Grenze: Ein Abstand sieht nur den Anteil einer Verschiebung senkrecht zur Oberfläche. Deshalb
wird die Verschiebung über die Flächennormalen geschätzt; eine Szene ohne Flächen in allen drei
Richtungen (nur Boden) wird abgewiesen statt geraten. Rotationen zeigen sich als Restabstand
(Median des Abstands zur Fläche, nicht zum nächsten Punkt).
Die Rückprojektion in verify (Tie-Point-Pixel gegen Pose) ist die zweite, unabhängige Probe.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

GLEICH_M = 0.05
ANDERS_M = 0.20
MIN_TIE_POINTS = 100
NACHBARN = 16
ITERATIONEN = 6
# Kleinster Eigenwert der Normalenmatrix (Mittel n nᵀ): unter dieser Schwelle fehlt eine
# Flächenrichtung, eine Verschiebung in diese Richtung wäre nicht messbar.
MIN_EIGENWERT = 0.02


class RahmenFehler(ValueError):
    """Fotos passen nicht zur Paketwolke."""


@dataclass(frozen=True)
class RahmenErgebnis:
    median_m: float
    p95_m: float
    n: int
    versatz_m: tuple[float, float, float]


def _normalen(baum: cKDTree, punkte: np.ndarray, orte: np.ndarray) -> np.ndarray:
    _, idx = baum.query(orte, k=NACHBARN)
    nachbarn = punkte[idx]
    zentriert = nachbarn - nachbarn.mean(axis=1, keepdims=True)
    kovarianz = np.einsum("nki,nkj->nij", zentriert, zentriert)
    _, vektoren = np.linalg.eigh(kovarianz)
    return vektoren[:, :, 0]


def pruefe_rahmen(
    tie_points: np.ndarray, wolke: np.ndarray, *, toleranz_m: float | None = None
) -> RahmenErgebnis:
    """Verschiebung Fotos → Wolke per Punkt-zu-Ebene-Ausgleich (nur Translation) schätzen.

    Der Median des Nächste-Nachbar-Abstands allein reicht nicht: Er sieht nur den Anteil einer
    Verschiebung senkrecht zur jeweiligen Fläche. Der Ausgleich über die Flächennormalen
    findet die Verschiebung, wenn Flächen in allen drei Richtungen vorkommen; sonst Abbruch.
    """
    tie = np.asarray(tie_points, float).reshape(-1, 3)
    punkte = np.asarray(wolke, float).reshape(-1, 3)
    if len(punkte) < NACHBARN:
        raise RahmenFehler("Rahmenprüfung: Paketwolke ohne genug Punkte")
    lo, hi = punkte[:, :2].min(axis=0), punkte[:, :2].max(axis=0)
    innen = tie[np.all((tie[:, :2] >= lo) & (tie[:, :2] <= hi), axis=1)]
    if len(innen) < MIN_TIE_POINTS:
        raise RahmenFehler(
            f"Rahmenprüfung: zu wenige Tie Points im Bereich der Wolke ({len(innen)} < "
            f"{MIN_TIE_POINTS})"
        )
    # Relativ zum Schwerpunkt rechnen: UTM-Beträge kosten im KD-Baum Genauigkeit.
    mitte = punkte.mean(axis=0)
    punkte = punkte - mitte
    innen = innen - mitte
    baum = cKDTree(punkte)
    versatz = np.zeros(3)
    for _ in range(ITERATIONEN):
        verschoben = innen - versatz
        _, idx = baum.query(verschoben)
        normalen = _normalen(baum, punkte, punkte[idx])
        rest = np.einsum("ni,ni->n", verschoben - punkte[idx], normalen)
        # Robust: das schlechteste Fünftel (Ausreißer, Kanten) nicht mitrechnen.
        behalten = np.abs(rest) <= np.percentile(np.abs(rest), 80)
        n, r = normalen[behalten], rest[behalten]
        matrix = n.T @ n / len(n)
        if np.linalg.eigvalsh(matrix)[0] < MIN_EIGENWERT:
            raise RahmenFehler(
                "Rahmenprüfung: Szene zu eben — Flächen fehlen in einer Richtung, eine "
                "Verschiebung wäre nicht messbar"
            )
        schritt = np.linalg.solve(n.T @ n, n.T @ r)
        versatz = versatz + schritt
        if np.linalg.norm(schritt) < 1e-4:
            break
    # Restabstand zur Fläche (nicht zum nächsten Punkt): unabhängig von der Punktdichte — bei einer
    # dünn abgetasteten Baustelle läge der Punkt-zu-Punkt-Abstand sonst im Dezimeterbereich.
    _, idx = baum.query(innen - versatz)
    rest = np.abs(np.einsum("ni,ni->n", innen - punkte[idx], _normalen(baum, punkte, punkte[idx])))
    ergebnis = RahmenErgebnis(
        float(np.median(rest)),
        float(np.percentile(rest, 95)),
        len(innen),
        (float(versatz[0]), float(versatz[1]), float(versatz[2])),
    )
    betrag = float(np.linalg.norm(versatz))
    grenze = GLEICH_M if toleranz_m is None else toleranz_m
    if betrag <= grenze and ergebnis.median_m <= grenze:
        return ergebnis
    if toleranz_m is None and max(betrag, ergebnis.median_m) <= ANDERS_M:
        raise RahmenFehler(
            f"Rahmenprüfung: Versatz {betrag:.3f} m, Median {ergebnis.median_m:.3f} m — zwischen "
            f"{GLEICH_M} und {ANDERS_M} m, Stand unklar; prüfen und ggf. mit --rahmen-toleranz <m> "
            "bewusst zulassen"
        )
    raise RahmenFehler(
        f"Rahmenprüfung: Versatz {betrag:.3f} m, Median {ergebnis.median_m:.3f} m — die Fotos "
        "stammen aus einem anderen Ausrichtungsstand als die Paketwolke"
    )
