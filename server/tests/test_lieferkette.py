"""Lieferkette: jedes gesperrte Fremdpaket muss in [tool.uv] no-build-package stehen.

Sonst könnte eine neue Abhängigkeit still am no-sdist-Bau-Verbot vorbei ins Lockfile rutschen.
"""
from __future__ import annotations

import tomllib
from pathlib import Path

PROJEKT_NAME = "scanview-server"
SERVER_DIR = Path(__file__).resolve().parent.parent


def test_alle_gesperrten_pakete_stehen_in_no_build_package() -> None:
    lock = tomllib.loads((SERVER_DIR / "uv.lock").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((SERVER_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    no_build = set(pyproject["tool"]["uv"]["no-build-package"])
    gesperrt = {p["name"] for p in lock["package"]} - {PROJEKT_NAME}
    assert gesperrt, "uv.lock leer oder Format geändert — Test prüft sonst nichts"
    fehlend = gesperrt - no_build
    assert not fehlend, f"Fremdpakete ohne no-build-package: {sorted(fehlend)}"
