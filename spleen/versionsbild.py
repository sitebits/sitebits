"""Bild der Version: jede Version bekommt fest ein Punktbild, gleichmäßig verteilt und reproduzierbar.

Aufruf: python3 spleen/versionsbild.py 0.10.3   → Name und Bild für CHANGELOG und Release-Notes
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

BILDER = Path(__file__).resolve().parent / "bilder"


def bild_fuer(version: str) -> Path:
    bilder = sorted(BILDER.glob("*.txt"), key=lambda p: p.name)
    index = int.from_bytes(hashlib.sha256(version.encode()).digest()[:8], "big") % len(bilder)
    return bilder[index]


if __name__ == "__main__":
    bild = bild_fuer(sys.argv[1])
    print(f"{bild.stem}\n\n{bild.read_text(encoding='utf-8').rstrip()}")
