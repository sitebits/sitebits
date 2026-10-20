"""Synthetisches Prüfpaket „Wand mit Öffnung“ (nur erfundene Geometrie, keine Kundendaten).

Ein Scanner steht bei (0, 0, 1,5) und blickt nach +X. Welt-Koordinaten (m):

- Wand: Ebene x = 5, y ∈ [-3, 3], z ∈ [0, 3], mit leerer Öffnung y ∈ [-0,6; 0,6], z ∈ [0,8; 2,2];
- sichtbare Fläche: Ebene x = 8, y ∈ [-1, 1], z ∈ [0,5; 2,5] (durch die Öffnung zu sehen, nur
  der Rand ab |y| > 0,96 m liegt im Schatten der Wand);
- verdeckte Fläche: Ebene x = 9, y ∈ [1,5; 3], z ∈ [0, 3] (liegt hinter der Wand, von (0, 0, 1,5)
  aus nicht durch die Öffnung zu sehen);
- Boden: z = 0, x ∈ [0, 5], y ∈ [-3, 3].

Über der Wand gibt es keine Decke: der Blick nach oben geht ins Leere.

Aufruf (aus ``import/``)::

    uv run python scripts/synthetisch_wand_oeffnung.py \
        --ziel ~/localWorkspace/scanview-data/e2e-wand-oeffnung

Ablauf: E57 mit einem Scan schreiben (Schreiber der Testfixture), danach ``scanview-import build``
(braucht Docker mit PDAL). Ein vorhandenes Ziel wird nie überschrieben.
Die E57-Schreibfunktion liegt unter ``tests/`` und wird von dort geladen, statt sie zu kopieren.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import cast

import numpy as np

SCANNER_POSE = (0.0, 0.0, 1.5)
TITEL = "Synthetisch: Wand mit Öffnung"
OEFFNUNG_Y = (-0.6, 0.6)
OEFFNUNG_Z = (0.8, 2.2)
WAND_X = 5.0
SICHTBAR_X = 8.0
VERDECKT_X = 9.0


def _raster(
    a: tuple[float, float], b: tuple[float, float], abstand: float, rng: np.random.Generator
) -> tuple[np.ndarray, np.ndarray]:
    """Gitter über die Bereiche a und b, leicht gestreut (deterministisch über den Seed)."""
    u = np.arange(a[0], a[1] + 1e-9, abstand)
    v = np.arange(b[0], b[1] + 1e-9, abstand)
    uu, vv = np.meshgrid(u, v, indexing="ij")
    uu = uu.ravel() + rng.uniform(-0.2, 0.2, uu.size) * abstand
    vv = vv.ravel() + rng.uniform(-0.2, 0.2, vv.size) * abstand
    return uu, vv


def _in_oeffnung(y: np.ndarray, z: np.ndarray) -> np.ndarray:
    return (y > OEFFNUNG_Y[0]) & (y < OEFFNUNG_Y[1]) & (z > OEFFNUNG_Z[0]) & (z < OEFFNUNG_Z[1])


def erzeuge_flaechen(abstand: float = 0.005, seed: int = 20261001) -> dict[str, np.ndarray]:
    """Punktmengen je Fläche in Welt-Koordinaten, (N, 3). Gleicher Seed → gleiche Punkte."""
    rng = np.random.default_rng(seed)

    y, z = _raster((-3, 3), (0, 3), abstand, rng)
    keep = ~_in_oeffnung(y, z)
    wand = np.column_stack([np.full(keep.sum(), WAND_X), y[keep], z[keep]])

    y, z = _raster((-1, 1), (0.5, 2.5), abstand, rng)
    sichtbar = np.column_stack([np.full(y.size, SICHTBAR_X), y, z])

    y, z = _raster((1.5, 3), (0, 3), abstand, rng)
    verdeckt = np.column_stack([np.full(y.size, VERDECKT_X), y, z])

    x, y = _raster((0, 5), (-3, 3), abstand, rng)
    boden = np.column_stack([x, y, np.zeros(x.size)])

    return {"wand": wand, "sichtbar": sichtbar, "verdeckt": verdeckt, "boden": boden}


def scan_lokal(flaechen: dict[str, np.ndarray]) -> np.ndarray:
    """Alle Flächen im Scan-Koordinatensystem (Welt minus Scannerposition)."""
    welt = np.vstack([flaechen[k] for k in ("wand", "sichtbar", "verdeckt", "boden")])
    return cast(np.ndarray, welt - np.array(SCANNER_POSE))


def _fixture_writer() -> Callable[..., None]:
    tests = Path(__file__).resolve().parent.parent / "tests"
    # Nur den Fixture-Ordner anhängen: Module gleichen Namens aus dem Paket behalten Vorrang.
    if str(tests) not in sys.path:
        sys.path.append(str(tests))
    from e57_fixture import write_fixture

    return cast(Callable[..., None], write_fixture)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--ziel", type=Path, required=True, help="Paketordner (darf nicht existieren)")
    ap.add_argument(
        "--abstand", type=float, default=0.005, help="Punktabstand in m (Standard 5 mm)"
    )
    args = ap.parse_args(argv)
    ziel: Path = args.ziel.expanduser()
    if ziel.exists():
        print(f"Ziel existiert bereits, nichts überschrieben: {ziel}", file=sys.stderr)
        return 2

    from scanview_import.cli import main as import_main
    from scanview_import.model import Pose

    lokal = scan_lokal(erzeuge_flaechen(args.abstand))
    print(f"{len(lokal)} Punkte")
    with tempfile.TemporaryDirectory(prefix="wand-oeffnung-") as tmp:
        e57 = Path(tmp) / "wand-oeffnung.e57"
        _fixture_writer()(
            e57,
            [
                (
                    Pose(SCANNER_POSE, (1.0, 0.0, 0.0, 0.0)),
                    lokal,
                    np.zeros(len(lokal), dtype=np.int64),
                )
            ],
        )
        try:
            rc = import_main(["build", str(e57), str(ziel)])
        except BaseException:
            _teilpaket_entfernen(ziel)
            raise
    if rc:
        _teilpaket_entfernen(ziel)
        return int(rc)
    print(f"Paket: {ziel}")
    return _titel_setzen(ziel)


def _teilpaket_entfernen(ziel: Path) -> None:
    """Entfernt das Ziel eines gescheiterten Laufs. Nur aufrufen, wenn dieser Lauf es angelegt hat:
    ``main`` bricht vorher ab, wenn das Ziel schon existiert."""
    shutil.rmtree(ziel, ignore_errors=True)


def _titel_setzen(ziel: Path) -> int:
    import json

    pj = ziel / "project.json"
    d = json.loads(pj.read_text(encoding="utf-8"))
    d["title"] = TITEL
    pj.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
