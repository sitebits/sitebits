"""Lageanpassung eines Pakets als starre 3D-Bewegung im Paket-CRS (z. B. Anpassung an LoD2/DGM1).

Zwei Wege, ein Ergebnis: ``--verschiebung DX,DY`` (reine Verschiebung) oder ``--transformation
DATEI.json`` (4x4-Matrix: Drehung plus Verschiebung, Maßstab 1). Intern ist beides eine Matrix.
Sie wird einmal beim Punktimport festgelegt und in ``sourceOptions.lage`` der Wolke vermerkt.
Kameras und Fotos übernehmen sie von dort, damit alle Ebenen zusammenpassen. Die Höhen werden
vorher an der Originallage ins Paket-Höhensystem umgerechnet; die Matrix wirkt danach.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray

# Schutz vor Tippfehlern und falschen Dateien: Größere Werte sind keine Anpassung mehr,
# sondern ein anderes Koordinatensystem.
MAX_M = 100.0
MAX_GRAD = 5.0


@dataclass(frozen=True)
class Lage:
    matrix: NDArray[np.float64]
    art: str
    herkunft: dict[str, Any]


def _datei(pfad: Path) -> dict[str, Any]:
    try:
        daten = json.loads(pfad.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Lagedatei {pfad.name} nicht lesbar ({exc})") from exc
    if not isinstance(daten, dict):
        raise ValueError(f"Lagedatei {pfad.name}: JSON-Objekt erwartet")
    return daten


def _verschiebung(dx: float, dy: float) -> NDArray[np.float64]:
    m = np.eye(4)
    m[0, 3], m[1, 3] = dx, dy
    return m


def pruefe(matrix: NDArray[np.float64], bezug: NDArray[np.float64] | None = None) -> None:
    """Starre Bewegung (orthonormal, det 1), Drehung ≤ MAX_GRAD, Versatz ≤ MAX_M.

    Den Versatz prüft sie am Bezugspunkt, ohne Bezugspunkt nur bei reiner Verschiebung.
    """
    m = np.asarray(matrix, dtype=np.float64)
    if m.shape != (4, 4) or not np.isfinite(m).all():
        raise ValueError("Lagematrix muss 4x4 und endlich sein")
    if not np.allclose(m[3], [0, 0, 0, 1], atol=1e-12, rtol=0):
        raise ValueError("Lagematrix: letzte Zeile muss 0 0 0 1 sein")
    r = m[:3, :3]
    if not np.allclose(r.T @ r, np.eye(3), atol=1e-6, rtol=0) or not math.isclose(
        float(np.linalg.det(r)), 1.0, abs_tol=1e-6
    ):
        raise ValueError("Lagematrix ist keine starre Bewegung (Drehung nicht orthonormal)")
    winkel = math.degrees(math.acos(max(-1.0, min(1.0, (float(np.trace(r)) - 1) / 2))))
    if winkel > MAX_GRAD:
        raise ValueError(f"Lagematrix dreht um {winkel:.2f}° (> {MAX_GRAD:g}°): Daten prüfen")
    if bezug is None and winkel > 1e-9:
        return  # Mit Drehung ist der Versatz nur an einem Bezugspunkt sinnvoll (``drehpunkt``).
    p = np.zeros(3) if bezug is None else np.asarray(bezug, dtype=np.float64)
    versatz = float(np.linalg.norm(r @ p + m[:3, 3] - p))
    if versatz > MAX_M:
        raise ValueError(
            f"Lagematrix versetzt um {versatz:.1f} m (> {MAX_M:g} m): Koordinatensystem prüfen"
        )


def lies_verschiebung(angabe: str) -> Lage:
    """``"DX,DY"`` oder JSON-Datei mit ``dx_m``/``dy_m`` (übrige Felder werden Herkunft)."""
    if angabe.lower().endswith(".json"):
        pfad = Path(angabe)
        daten = _datei(pfad)
        try:
            dx, dy = float(daten["dx_m"]), float(daten["dy_m"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Verschiebungsdatei {pfad.name} nicht lesbar ({exc})") from exc
        herkunft = {k: v for k, v in daten.items() if k not in ("dx_m", "dy_m")}
        herkunft["datei"] = pfad.name
    else:
        teile = angabe.split(",")
        if len(teile) != 2:
            raise ValueError("Verschiebung als DX,DY in Metern angeben, z. B. -0.62,-0.81")
        try:
            dx, dy = float(teile[0]), float(teile[1])
        except ValueError as exc:
            raise ValueError(f"Verschiebung nicht lesbar: {angabe}") from exc
        herkunft = {}
    if not (math.isfinite(dx) and math.isfinite(dy)):
        raise ValueError("Verschiebung muss endlich sein")
    if max(abs(dx), abs(dy)) > MAX_M:
        raise ValueError(
            f"Verschiebung über {MAX_M:g} m: Koordinatensystem prüfen statt verschieben"
        )
    return Lage(_verschiebung(dx, dy), "verschiebung", {"dx": dx, "dy": dy, **herkunft})


def lies_transformation(angabe: str) -> Lage:
    """JSON-Datei mit ``matrix_4x4`` (zeilenweise, absolute Koordinaten im Paket-CRS)."""
    pfad = Path(angabe)
    daten = _datei(pfad)
    try:
        matrix = np.asarray(daten["matrix_4x4"], dtype=np.float64)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Transformationsdatei {pfad.name}: matrix_4x4 fehlt ({exc})") from exc
    drehpunkt = daten.get("drehpunkt")
    pruefe(matrix, None if drehpunkt is None else np.asarray(drehpunkt, dtype=np.float64))
    herkunft = {k: v for k, v in daten.items() if k != "matrix_4x4"}
    herkunft["datei"] = pfad.name
    return Lage(matrix, "transformation", herkunft)


def quellblock(lage: Lage) -> dict[str, Any]:
    """Eintrag für ``sourceOptions.lage``."""
    return {"art": lage.art, "matrix": lage.matrix.tolist(), "einheit": "m", **lage.herkunft}


def aus_paket(cloud: dict[str, Any]) -> NDArray[np.float64] | None:
    """Die beim Punktimport angewandte Lagematrix, falls es eine gab."""
    block = cloud.get("sourceOptions", {}).get("lage")
    if not block:
        return None
    matrix = np.asarray(block["matrix"], dtype=np.float64)
    pruefe(matrix, None if block.get("drehpunkt") is None else np.asarray(block["drehpunkt"]))
    return matrix


def wende_an(punkte: NDArray[np.float64], matrix: NDArray[np.float64]) -> NDArray[np.float64]:
    """Nx3-Weltpositionen: Kopie nach der starren Bewegung."""
    p = np.asarray(punkte, dtype=np.float64)
    if not len(p):
        return np.array(p, copy=True)
    return p @ matrix[:3, :3].T + matrix[:3, 3]


def las_anpassen(
    src: Path,
    dst: Path,
    *,
    crs: int | str | None,
    hoehe: tuple[str, str] | None,
    matrix: NDArray[np.float64] | None,
    chunk: int = 2_000_000,
) -> None:
    """LAS/LAZ in einem Durchgang: erst Höhe umrechnen (an der Originallage), dann die Lagematrix.

    Skala und Offset bleiben; Werte werden auf die Skala gerundet (Fehler ≤ halber Schritt).
    Die Kopfzeilen-Grenzen setzt laspy beim Schreiben neu.
    """
    import laspy

    from . import hoehen

    if hoehe is not None and crs is None:
        raise ValueError("Höhenumrechnung braucht ein Koordinatensystem (--crs)")
    with laspy.open(src) as reader, laspy.open(dst, mode="w", header=reader.header) as writer:
        for pts in reader.chunk_iterator(chunk):
            x = np.asarray(pts.x, dtype=np.float64)
            y = np.asarray(pts.y, dtype=np.float64)
            z = np.asarray(pts.z, dtype=np.float64)
            if hoehe is not None:
                z = hoehen.rechne_um(x, y, z, crs=crs, quelle=hoehe[0], ziel=hoehe[1])  # type: ignore[arg-type]
                pts.z = z
            if matrix is not None:
                neu = wende_an(np.column_stack([x, y, z]), matrix)
                pts.x, pts.y, pts.z = neu[:, 0], neu[:, 1], neu[:, 2]
            writer.write_points(pts)
