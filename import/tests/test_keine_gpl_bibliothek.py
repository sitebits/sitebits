"""PDAL-Umgebung nur als Prozess (ADR 2026-10-22, Lizenzausnahme „nur externer Prozess“).

Die gelockte conda-forge-Umgebung (`import/pdal-umgebung`) enthält GPL- und MPL-Komponenten
(untwine, librttopo, readline, libspatialite, freexl). siteBits darf sie nur als eigene Prozesse
starten — kein Python-Modul der Pakete importiert PDAL/GDAL-Bindungen, lädt eine Bibliothek der
Umgebung per ctypes/cffi oder setzt deren Bibliothekspfad. Statische Prüfung über alle Python-
Quellen der ausgelieferten Teile (import, server, mcp-server)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
QUELLEN = [REPO / "import" / "src", REPO / "server" / "src", REPO / "mcp-server" / "src"]
#: Python-Bindungen an Bibliotheken der Umgebung (PDAL, GDAL, readline, SpatiaLite).
VERBOTENE_MODULE = {"pdal", "untwine", "osgeo", "gdal", "readline", "spatialite", "pyspatialite",
                    "cffi"}
#: Nur dieses Modul nutzt ctypes, und nur für die ABI-Deklaration von meshoptimizer (MIT).
CTYPES_ERLAUBT = {"import/src/scanview_import/mesh_stages.py"}
#: Bibliotheken der Umgebung: tauchen sie als Text im Code auf, lädt oder verlinkt etwas sie.
BIBLIOTHEKEN = ("libpdal", "libgdal", "librttopo", "libspatialite", "libfreexl", "libreadline",
                "LD_LIBRARY_PATH", "LD_PRELOAD")


def _dateien() -> list[Path]:
    return sorted(p for wurzel in QUELLEN for p in wurzel.rglob("*.py"))


def _module(baum: ast.AST) -> set[str]:
    namen: set[str] = set()
    for knoten in ast.walk(baum):
        if isinstance(knoten, ast.Import):
            namen |= {a.name.split(".")[0] for a in knoten.names}
        elif isinstance(knoten, ast.ImportFrom) and knoten.module and knoten.level == 0:
            namen.add(knoten.module.split(".")[0])
        elif isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name) \
                and knoten.func.id == "__import__" and knoten.args \
                and isinstance(knoten.args[0], ast.Constant):
            namen.add(str(knoten.args[0].value).split(".")[0])
    return namen


def test_quellen_gefunden() -> None:
    assert len(_dateien()) > 50


@pytest.mark.parametrize("datei", _dateien(), ids=lambda p: str(p.relative_to(REPO)))
def test_kein_import_und_kein_laden_von_gpl_bibliotheken(datei: Path) -> None:
    text = datei.read_text(encoding="utf-8")
    module = _module(ast.parse(text))
    rel = str(datei.relative_to(REPO))
    assert not module & VERBOTENE_MODULE, f"{rel} importiert {module & VERBOTENE_MODULE}"
    if "ctypes" in module:
        assert rel in CTYPES_ERLAUBT, f"{rel} nutzt ctypes"
    for name in BIBLIOTHEKEN:
        assert name not in text, f"{rel} nennt {name}"


def test_ctypes_ausnahme_laedt_nur_meshoptimizer() -> None:
    text = (REPO / next(iter(CTYPES_ERLAUBT))).read_text(encoding="utf-8")
    assert "CDLL" not in text and "cdll" not in text and "LoadLibrary" not in text
    assert "from meshoptimizer.simplifier import lib" in text


def test_pdal_runner_startet_nur_prozesse() -> None:
    """Der einzige Weg zu PDAL/untwine: subprocess mit fester Argumentliste, Programm aus
    `bin/` der Umgebung bzw. im Container."""
    text = (REPO / "import/src/scanview_import/pdal_runner.py").read_text(encoding="utf-8")
    baum = ast.parse(text)
    assert _module(baum) & {"ctypes", "cffi", "importlib"} == set()
    aufrufe = {f"{k.func.value.id}.{k.func.attr}" for k in ast.walk(baum)
               if isinstance(k, ast.Call) and isinstance(k.func, ast.Attribute)
               and isinstance(k.func.value, ast.Name) and k.func.value.id == "subprocess"}
    assert aufrufe == {"subprocess.run", "subprocess.Popen"}
    assert "shell=True" not in text
    assert 'praefix / "bin" / cmd[0]' in text


@pytest.mark.parametrize("code", [
    "import pdal", "from osgeo import gdal", "import readline as r", "__import__('pdal')",
    "from pdal.pipeline import Pipeline",
])
def test_pruefung_erkennt_bindungen(code: str) -> None:
    assert _module(ast.parse(code)) & VERBOTENE_MODULE
