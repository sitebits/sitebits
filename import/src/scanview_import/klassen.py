"""Punktklassen und Echos (F30): Klassen-Histogramm und Attribut-Erkennung aus LAS/LAZ/COPC.

Das Histogramm zählt die **ausgelieferten** Punkte (COPC, nach Ausdünnung) je Klassenwert; die
Quellstatistik steht getrennt in ``sourceOptions.classes``. ``classification`` kommt in die
Attribute, sobald ein Klassenwert außerhalb ``{0, 1}`` (nie klassifiziert / unklassifiziert)
vorkommt — auch bei nur einer Klasse, etwa einer reinen Bodenwolke. ``returns`` kommt hinzu, wenn
ein Punkt mehr als ein Echo hat.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import laspy
import numpy as np

# 0 = nie klassifiziert, 1 = unklassifiziert (ASPRS); eine Wolke nur damit hat keine Klassen.
OHNE_KLASSE = frozenset({0, 1})


@dataclass(frozen=True)
class KlassenStatistik:
    klassen: dict[str, int]
    mehrfachechos: bool

    def attribute(self) -> list[str]:
        """Zusätzliche Einträge für ``cloud.attributes``."""
        out: list[str] = []
        if any(int(k) not in OHNE_KLASSE for k in self.klassen):
            out.append("classification")
        if self.mehrfachechos:
            out.append("returns")
        return out


def zaehle(path: Path, chunk: int = 2_000_000) -> KlassenStatistik:
    """Klassen-Histogramm (Schlüssel = Klassenwert als Text) und Mehrfachechos einer LAS-Datei."""
    counts = np.zeros(256, dtype=np.int64)
    mehrfach = False
    with laspy.open(path) as reader:
        for pts in reader.chunk_iterator(chunk):
            werte = np.asarray(pts.classification, dtype=np.uint8)
            counts += np.bincount(werte, minlength=256)
            if not mehrfach:
                mehrfach = bool(np.any(np.asarray(pts.number_of_returns) > 1))
    return KlassenStatistik({str(k): int(n) for k, n in enumerate(counts) if n}, mehrfach)


# „Nur letzte Echos“ (F30): Giro3D filtert beim Dekodieren nur Standardfelder gegen Konstanten
# (ReturnNumber = NumberOfReturns ist nicht ausdrückbar, Extra Bytes gar nicht). Der Import markiert
# letzte Echos deshalb im LAS-Standardfeld UserData mit 1 — nur wenn die Quelle UserData nirgends
# benutzt (sonst wird nichts überschrieben und „nur letzte“ nicht angeboten). Einzelechos 1/1 sind
# letzte Echos, ungültige 0/0 nie.
LETZTE_ATTRIBUT = "returnsLast"
LETZTE_WERT = 1


def _letzte(pts: Any) -> np.ndarray:
    nr = np.asarray(pts.return_number)
    letzte: np.ndarray = (nr > 0) & (nr == np.asarray(pts.number_of_returns))
    return letzte


def letzte_markieren(src: Path, dst: Path, chunk: int = 2_000_000) -> bool:
    """Schreibt ``dst`` mit UserData = 1 an letzten Echos und gibt True zurück — oder False ohne
    Ausgabe, wenn es keine späteren Echos gibt (laut Kopf) oder UserData schon benutzt ist."""
    with laspy.open(src) as reader:
        if not any(int(n) for n in list(reader.header.number_of_points_by_return)[1:]):
            return False
        for pts in reader.chunk_iterator(chunk):
            if np.any(np.asarray(pts.user_data)):
                return False
    with laspy.open(src) as reader, laspy.open(dst, mode="w", header=reader.header) as writer:
        for pts in reader.chunk_iterator(chunk):
            pts.user_data = np.where(_letzte(pts), LETZTE_WERT, 0).astype(np.uint8)
            writer.write_points(pts)
    return True


def letzte_pruefen(path: Path, chunk: int = 2_000_000) -> bool:
    """verify: UserData ist genau an den letzten Echos 1, sonst 0."""
    with laspy.open(path) as reader:
        for pts in reader.chunk_iterator(chunk):
            soll = np.where(_letzte(pts), LETZTE_WERT, 0)
            if not np.array_equal(np.asarray(pts.user_data), soll):
                return False
    return True
