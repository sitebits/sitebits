"""Kopiert Punktbilder und Sprüche aus spleen/ in die Paketdaten von scanview-import.

Aufruf aus der Repo-Wurzel: python3 spleen/sync.py
Die Kopie ist nötig, weil das Python-Paket seine Daten selbst mitbringen muss; ein Test prüft die Gleichheit.
"""

from __future__ import annotations

import shutil
from pathlib import Path

QUELLE = Path(__file__).resolve().parent
ZIEL = QUELLE.parent / "import" / "src" / "scanview_import" / "spleen_daten"


def main() -> None:
    (ZIEL / "bilder").mkdir(parents=True, exist_ok=True)
    for alt in (ZIEL / "bilder").glob("*.txt"):
        alt.unlink()
    for bild in sorted((QUELLE / "bilder").glob("*.txt")):
        shutil.copyfile(bild, ZIEL / "bilder" / bild.name)
    shutil.copyfile(QUELLE / "sprueche.json", ZIEL / "sprueche.json")
    print(f"{len(list((ZIEL / 'bilder').glob('*.txt')))} Bilder + sprueche.json nach {ZIEL}")


if __name__ == "__main__":
    main()
