"""Erzeugt das synthetische Testpaket `paket/` dieses Ordners aus einer synthetischen E57-Datei.

Aufruf (aus `import/`, das Importwerkzeug wird nur ausgeführt, nicht verändert; Docker nötig):

    cd import
    uv run python ../nachweise/tests/fixtures/egress/erzeuge_paket.py <Zielordner>

Eingaben: zwei Scans mit je 1000 Zufallspunkten (feste Startwerte 1 und 2, lokale Koordinaten,
keine Kunden- oder Ortsdaten) und ein Würfelpanorama mit sechs Testbildern am ersten Scan
(`import/tests/e57_fixture.py`). Danach `scanview-import build … --levels 64,128` mit
`projectId = egress-test` (Name des Zielordners `<Zielordner>/egress-test`).

Reproduzierbarkeit (Probe: erneuter Lauf, Hashes verglichen): die sechs Kacheln und die
Draufsicht sind bitgleich zu den Hashes in `veroeffentlichung/freigaben.txt`. `cloud.copc.laz` ist
**nicht** bitgleich — der Punktinhalt ist derselbe (Zufallspunkte mit festem Startwert), aber die
Datei trägt je Lauf neue Kennungen der E57-Quelle und Zeitangaben. Ebenso ändern sich in den
JSON-Dateien (`project.json`, `inventory.json`, `stations.json`, `conversion-report.json`) die
GUIDs und Laufzeiten; sie brauchen keine Hashfreigabe. Wer das Paket neu erzeugt, trägt den neuen
Hash von `cloud.copc.laz` in `freigaben.txt` ein. Das eingecheckte Paket bleibt das maßgebliche.
"""

from __future__ import annotations

import hashlib
import math
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

sys.path.insert(0, "tests")
from e57_fixture import write_fixture
from scanview_import.model import Pose

PROJEKT = "egress-test"


def _punkte(n: int, startwert: int) -> np.ndarray:
    return np.random.default_rng(startwert).uniform(-5, 5, size=(n, 3))


def main(ziel: Path) -> None:
    halb = math.radians(90) / 2
    ungueltig = np.zeros(1000, dtype=np.int64)
    flaechen = ["nz", "px", "pz", "ny", "nx", "py"]
    with tempfile.TemporaryDirectory() as tmp:
        e57 = Path(tmp) / "egress.e57"
        write_fixture(
            e57,
            [
                (Pose((0.0, 0.0, 1.5), (1, 0, 0, 0)), _punkte(1000, 1), ungueltig),
                (Pose((10.0, 0.0, 1.5), (math.cos(halb), 0, 0, math.sin(halb))),
                 _punkte(1000, 2), ungueltig),
            ],
            images=[(f, 0) for f in flaechen],  # type: ignore[misc]
        )
        paket = ziel / PROJEKT
        subprocess.run(["uv", "run", "--locked", "scanview-import", "build", str(e57), str(paket),
                        "--levels", "64,128"], check=True)
    for datei in sorted(p for p in paket.rglob("*")
                        if p.suffix in (".laz", ".jpg", ".png")):
        print(hashlib.sha256(datei.read_bytes()).hexdigest(), datei.relative_to(paket).as_posix())


if __name__ == "__main__":
    main(Path(sys.argv[1]))
