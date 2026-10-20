"""Wächter für spleen/: Punktbilder und Sprüche nur an freigegebenen Fundorten."""

from __future__ import annotations

import json
import re
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]
ERLAUBT = ("spleen/", "import/src/scanview_import/spleen_daten/")
ERLAUBTE_DATEIEN = {"README.md", "README.de.md", "CHANGELOG.md"}
AUSGELASSEN = {".git", "node_modules", ".venv", "dist", "e2e-results", "__pycache__"}
# Ab acht Braille-Zeichen am Stück ist es ein Punktbild; einzelne Zeichen sind erlaubt.
PUNKTBILD = re.compile("[⠀-⣿]{8,}")


def _textdateien() -> list[tuple[str, str]]:
    dateien = []
    for pfad in WURZEL.rglob("*"):
        if not pfad.is_file() or AUSGELASSEN.intersection(pfad.relative_to(WURZEL).parts):
            continue
        try:
            text = pfad.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        dateien.append((pfad.relative_to(WURZEL).as_posix(), text))
    return dateien


def _erlaubt(rel: str) -> bool:
    return rel.startswith(ERLAUBT) or rel in ERLAUBTE_DATEIEN


def test_punktbilder_nur_an_fundorten() -> None:
    funde = [rel for rel, text in _textdateien() if not _erlaubt(rel) and PUNKTBILD.search(text)]
    assert funde == [], f"Punktbild außerhalb von spleen/: {funde}"


def test_sprueche_nicht_kopiert() -> None:
    daten = json.loads((WURZEL / "spleen" / "sprueche.json").read_text(encoding="utf-8"))
    texte = [s[k] for s in daten["eigen"] for k in ("de", "en")]
    texte += [z["text"] for z in daten["zitate"]]
    dateien = [(rel, text) for rel, text in _textdateien() if not _erlaubt(rel)]
    funde = sorted({rel for rel, text in dateien for t in texte if t in text})
    assert funde == [], f"Spruch außerhalb von spleen/: {funde}"
