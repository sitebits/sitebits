"""Gemeinsame Prüfhilfen für die lokal gebauten Werkzeug-Images (scanview-pdal,
scanview-meshtools): explizite conda-Locks, pixi.lock der PDAL-Umgebung, FROM-Digests."""

from __future__ import annotations

import re
from pathlib import Path

#: Eine Zeile eines expliziten conda-Locks: conda-forge-URL eines .conda-Pakets mit SHA-256.
LOCK_ZEILE = re.compile(
    r"(?P<url>https://conda\.anaconda\.org/conda-forge/(?:linux-64|noarch)/"
    r"(?P<name>[a-z0-9_.-]+)-(?P<version>[^-]+)-(?P<build>[^-]+)\.conda)"
    r"#sha256:(?P<sha256>[0-9a-f]{64})"
)
_FROM_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}( AS \w+)?$")


def lock_eintraege(text: str) -> dict[str, re.Match[str]]:
    """Explizites Lock (`@EXPLICIT`, je Zeile URL + SHA-256) → Paketname → Zeilen-Treffer.
    Bricht bei fehlender Kopfzeile, Zeile ohne Hash oder doppeltem Paket ab."""
    zeilen = text.splitlines()
    assert zeilen and zeilen[0] == "@EXPLICIT", "kein @EXPLICIT"
    eintraege: dict[str, re.Match[str]] = {}
    for zeile in zeilen[1:]:
        m = LOCK_ZEILE.fullmatch(zeile)
        assert m is not None, f"Lock-Zeile ohne conda-forge-URL oder SHA-256: {zeile}"
        assert m["name"] not in eintraege, f"doppelt im Lock: {m['name']}"
        eintraege[m["name"]] = m
    return eintraege


def lock_pakete(text: str) -> dict[str, str]:
    """Explizites Lock → Paketname → Version."""
    return {name: m["version"] for name, m in lock_eintraege(text).items()}


def pixi_pakete(text: str) -> dict[str, tuple[str, str]]:
    """pixi.lock (Version 7) → Paketname → (URL, SHA-256). Gelesen wird nur der Abschnitt
    `packages:` (Einträge `- conda: <URL>` in Spalte 0); `environments:` wiederholt die URLs
    eingerückt und ohne Hash. `sha256:` steht irgendwo im Block, nicht immer in der Folgezeile."""
    _, trenner, rest = text.partition("\npackages:\n")
    assert trenner, "pixi.lock ohne Abschnitt packages:"
    pakete: dict[str, tuple[str, str]] = {}
    for block in re.split(r"^(?=- conda: )", rest, flags=re.MULTILINE):
        if not block.startswith("- conda: "):
            continue
        url = block.splitlines()[0].removeprefix("- conda: ").strip()
        sha = re.search(r"^  sha256: ([0-9a-f]{64})$", block, re.MULTILINE)
        assert sha is not None, f"pixi.lock: kein sha256 für {url}"
        name = url.rsplit("/", 1)[1].rsplit("-", 2)[0]
        assert name not in pakete, f"doppelt in pixi.lock: {name}"
        pakete[name] = (url, sha[1])
    return pakete


def from_zeilen_per_digest(dockerfile: Path) -> list[str]:
    """FROM-Zeilen eines Dockerfiles; jede muss ihre Basis per Digest pinnen."""
    zeilen = [z for z in dockerfile.read_text().splitlines() if z.startswith("FROM ")]
    for zeile in zeilen:
        assert _FROM_DIGEST.search(zeile), zeile
    return zeilen
