"""convert für den Import-Worker: Titel und pfadfreie Absage als letzte Zeile."""

import json
from pathlib import Path

import laspy
import numpy as np
import pytest

from scanview_import import cli


def _laz(path: Path, n: int) -> None:
    h = laspy.LasHeader(point_format=7, version="1.4")
    h.scales = [0.001] * 3
    h.offsets = [390000.0, 5800000.0, 30.0]
    rng = np.random.default_rng(3)
    las = laspy.LasData(h)
    las.x = 390000 + rng.uniform(0, 10, n)
    las.y = 5800000 + rng.uniform(0, 10, n)
    las.z = 30 + rng.uniform(0, 2, n)
    las.write(path)


def test_absage_letzte_zeile_ohne_pfade(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "geheim" / "a.laz"
    src.parent.mkdir()
    _laz(src, 10)
    rc = cli.main(["convert", str(src), str(tmp_path / "pkg"), "--ziel-hoehensystem", "dhhn2016"])
    assert rc == 1
    letzte = capsys.readouterr().err.strip().splitlines()[-1]
    assert letzte.startswith("Abgelehnt: ") and "Quellhöhensystem" in letzte
    assert "/" not in letzte


@pytest.mark.parametrize("titel", ["", "   ", "a\nb", "x" * 121])
def test_titel_wird_vor_dem_import_geprueft(tmp_path: Path, titel: str,
                                            capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "a.laz"
    _laz(src, 10)
    rc = cli.main(["convert", str(src), str(tmp_path / "pkg"), "--titel", titel])
    assert rc == 1
    assert capsys.readouterr().err.strip().splitlines()[-1].startswith("Abgelehnt: Titel")
    assert not (tmp_path / "pkg" / "project.json").exists()


@pytest.mark.docker
def test_titel_steht_im_paket(tmp_path: Path) -> None:
    src = tmp_path / "a.laz"
    _laz(src, 200)
    pkg = tmp_path / "pkg"
    rc = cli.main(["convert", str(src), str(pkg), "--crs", "EPSG:25833", "--titel", " Halle Ost "])
    assert rc == 0
    assert json.loads((pkg / "project.json").read_text())["title"] == "Halle Ost"


@pytest.mark.docker
def test_ohne_crs_wird_das_paket_lokal(tmp_path: Path) -> None:
    """neu_wolke ohne crs-Option: CRS-lose Datei → lokales Paket (wie Mesh/Splats ohne CRS)."""
    src = tmp_path / "a.laz"
    _laz(src, 200)
    pkg = tmp_path / "pkg"
    assert cli.main(["convert", str(src), str(pkg), "--mode", "staged"]) == 0
    crs = json.loads((pkg / "project.json").read_text())["frame"]["crs"]
    assert crs["wkt"] is None and crs["declaredBy"] == "none"


@pytest.mark.docker
def test_ohne_crs_hoehenumrechnung_abgelehnt(tmp_path: Path,
                                             capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "a.laz"
    _laz(src, 200)
    rc = cli.main(["convert", str(src), str(tmp_path / "pkg"), "--quell-hoehensystem",
                   "ellipsoidal-etrs89"])
    assert rc == 1
    letzte = capsys.readouterr().err.strip().splitlines()[-1]
    assert letzte.startswith("Abgelehnt: Höhenumrechnung braucht ein Koordinatensystem")
