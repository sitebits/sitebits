"""Gemeinsamer Satz für siteBits-Berichte: Zahlen, Tabellenzeilen, Rechenstand, PDF-Druck.

Bewusst ohne schwere Abhängigkeiten (kein embreex/pvlib), damit Sonnen- und Prüfbericht ihn teilen.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path


def _z(x: float | None, stellen: int = 0) -> str:
    if x is None:
        return "\u2013"
    return f"{x:,.{stellen}f}".replace(",", " ").replace(".", ",")


def _prozent(x: float | None, stellen: int = 1) -> str:
    return "\u2013" if x is None else _z(100 * x, stellen) + " %"


def _tabelle(zeilen: list[tuple[str, str]]) -> str:
    return "".join(f"<tr><td>{a}</td><td>{b}</td></tr>" for a, b in zeilen)


def git_stand(ordner: Path | None = None) -> str:
    """Commit des Rechenstands, „-geändert“ bei offenen Änderungen, sonst „unbekannt“."""
    hier = ordner or Path(__file__).resolve().parent
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=hier,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
        schmutzig = subprocess.run(
            ["git", "status", "--porcelain", "--", "."],
            cwd=hier,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unbekannt"
    return rev + ("-geändert" if schmutzig else "")


def pdf_drucken(html_datei: Path, pdf_datei: Path) -> None:
    browser = next(
        (b for b in ("chromium", "chromium-browser", "google-chrome") if shutil.which(b)), None
    )
    if browser is None:
        raise RuntimeError("Kein Chromium für den PDF-Druck gefunden (HTML liegt vor)")
    with tempfile.TemporaryDirectory() as profil:
        subprocess.run(
            [
                browser,
                "--headless",
                "--disable-gpu",
                "--no-first-run",
                "--disable-extensions",
                f"--user-data-dir={profil}",
                "--no-pdf-header-footer",
                f"--print-to-pdf={pdf_datei}",
                html_datei.resolve().as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
    if not pdf_datei.is_file() or pdf_datei.stat().st_size == 0:
        raise RuntimeError("PDF-Druck ohne Ergebnis")
