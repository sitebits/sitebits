"""Punktklassen und Echos (F30): Histogramm, Attribute, Import und Prüfung."""

from __future__ import annotations

import json
from pathlib import Path

import laspy
import numpy as np
import pytest

from scanview_import import klassen
from scanview_import.convert import convert
from scanview_import.manifest_io import cloud_of
from scanview_import.point_verify import verify_points


def _las(
    path: Path,
    klasse: list[int],
    echos: list[tuple[int, int]] | None = None,
    point_format: int = 7,
    user_data: list[int] | None = None,
) -> None:
    n = len(klasse)
    rng = np.random.default_rng(3)
    h = laspy.LasHeader(point_format=point_format, version="1.4" if point_format >= 6 else "1.2")
    h.scales = [0.001] * 3
    h.offsets = [368000.0, 5807000.0, 0.0]
    las = laspy.LasData(h)
    las.x = rng.uniform(368000, 368020, n)
    las.y = rng.uniform(5807000, 5807020, n)
    las.z = rng.uniform(30, 40, n)
    las.classification = np.array(klasse, dtype=np.uint8)
    nr, anzahl = zip(*(echos or [(1, 1)] * n), strict=True)
    las.return_number = np.array(nr, dtype=np.uint8)
    las.number_of_returns = np.array(anzahl, dtype=np.uint8)
    if user_data is not None:
        las.user_data = np.array(user_data, dtype=np.uint8)
    las.write(path)


def test_histogramm_und_attribute(tmp_path: Path) -> None:
    p = tmp_path / "a.laz"
    _las(p, [2, 2, 2, 5, 6, 1, 0], [(1, 1), (1, 3), (2, 3), (3, 3), (1, 1), (1, 2), (2, 2)])
    st = klassen.zaehle(p, chunk=3)
    assert st.klassen == {"0": 1, "1": 1, "2": 3, "5": 1, "6": 1}
    assert st.mehrfachechos
    assert st.attribute() == ["classification", "returns"]


@pytest.mark.parametrize(
    ("klasse", "erwartet"),
    [
        pytest.param([0, 1, 1, 0], [], id="nur-0-und-1"),
        pytest.param([2, 2, 2], ["classification"], id="ein-klassen-wolke"),
        pytest.param([1, 1, 18], ["classification"], id="rauschen-hoch"),
    ],
)
def test_klassen_nur_wenn_ausserhalb_0_1(
    tmp_path: Path, klasse: list[int], erwartet: list[str]
) -> None:
    p = tmp_path / "a.las"
    _las(p, klasse)
    assert klassen.zaehle(p).attribute() == erwartet


def test_altes_punktformat_mit_5_bit_klassen(tmp_path: Path) -> None:
    p = tmp_path / "alt.las"
    _las(p, [2, 9, 31], point_format=3)
    st = klassen.zaehle(p)
    assert st.klassen == {"2": 1, "9": 1, "31": 1}
    assert not st.mehrfachechos


def test_klassen_bis_255_in_format_7(tmp_path: Path) -> None:
    p = tmp_path / "a.laz"
    _las(p, [64, 200, 255])
    assert klassen.zaehle(p).klassen == {"64": 1, "200": 1, "255": 1}


ECHOS = [(1, 1), (1, 3), (2, 3), (3, 3), (0, 0), (1, 2), (2, 2)]
LETZTE = [1, 0, 0, 1, 0, 0, 1]


def test_letzte_echos_in_user_data(tmp_path: Path) -> None:
    src, dst = tmp_path / "e.las", tmp_path / "m.las"
    _las(src, [2] * len(ECHOS), ECHOS)
    assert klassen.letzte_markieren(src, dst, chunk=3)
    las = laspy.read(dst)
    assert np.asarray(las.user_data).tolist() == LETZTE
    assert np.asarray(las.return_number).tolist() == [e[0] for e in ECHOS]
    assert klassen.letzte_pruefen(dst, chunk=3)
    assert not klassen.letzte_pruefen(src)  # unmarkiert: Negativkontrolle


def test_letzte_echos_nie_ueber_benutzte_user_data(tmp_path: Path) -> None:
    src, dst = tmp_path / "e.las", tmp_path / "m.las"
    _las(src, [2] * len(ECHOS), ECHOS, user_data=[0, 0, 0, 0, 0, 0, 7])
    assert not klassen.letzte_markieren(src, dst)
    assert not dst.exists()


def test_letzte_echos_ohne_spaetere_echos_kein_durchlauf(tmp_path: Path) -> None:
    src, dst = tmp_path / "e.las", tmp_path / "m.las"
    _las(src, [2] * 5)
    assert not klassen.letzte_markieren(src, dst)
    assert not dst.exists()


@pytest.mark.docker
def test_convert_markiert_letzte_echos_und_verify_prueft(tmp_path: Path) -> None:
    src = tmp_path / "echos.laz"
    _las(src, [2] * len(ECHOS) * 20, ECHOS * 20)
    pkg = tmp_path / "pkg"
    m = convert(src, pkg, "EPSG:25833", mode="direct")
    assert klassen.LETZTE_ATTRIBUT in cloud_of(m)["attributes"]
    copc = laspy.read(pkg / "cloud.copc.laz")
    soll = np.where((copc.return_number > 0) & (copc.return_number == copc.number_of_returns), 1, 0)
    assert np.array_equal(np.asarray(copc.user_data), soll) and int(soll.sum()) == 60
    rep = verify_points(src, pkg, samples=50)
    assert rep["classes"]["lastReturns"] is True and rep["ok"], rep


@pytest.mark.docker
def test_convert_schreibt_klassen_und_verify_prueft(tmp_path: Path) -> None:
    src = tmp_path / "klassen.laz"
    klasse = [2] * 300 + [5] * 200 + [6] * 100 + [1] * 50
    echos = [(1, 1)] * 400 + [(1, 2), (2, 2)] * 125
    _las(src, klasse, echos)
    pkg = tmp_path / "pkg"

    m = convert(src, pkg, "EPSG:25833", mode="direct")

    cloud = cloud_of(m)
    soll = {"1": 50, "2": 300, "5": 200, "6": 100}
    assert cloud["classes"] == soll
    assert cloud["sourceOptions"]["classes"] == soll
    assert {"classification", "returns"} <= set(cloud["attributes"])
    copc = laspy.read(pkg / "cloud.copc.laz")
    assert sorted(np.asarray(copc.classification).tolist()) == sorted(klasse)

    rep = verify_points(src, pkg, samples=200)
    assert rep["ok"], rep
    assert rep["classes"]["checked"] and rep["classes"]["ok"]

    # Negativkontrolle: verfälschtes Manifest-Histogramm fällt auf.
    pj = pkg / "project.json"
    data = json.loads(pj.read_text())
    cloud_of(data)["classes"]["2"] = 299
    pj.write_text(json.dumps(data))
    rep = verify_points(src, pkg, samples=200)
    assert not rep["classes"]["ok"]
    assert not rep["ok"]


@pytest.mark.docker
def test_convert_ohne_klassen_bietet_keine_an(tmp_path: Path) -> None:
    src = tmp_path / "roh.laz"
    _las(src, [1] * 100)
    m = convert(src, tmp_path / "pkg", "EPSG:25833", mode="direct")
    cloud = cloud_of(m)
    assert cloud["classes"] == {"1": 100}
    assert "classification" not in cloud["attributes"]
    assert "returns" not in cloud["attributes"]
    assert klassen.LETZTE_ATTRIBUT not in cloud["attributes"]


@pytest.mark.docker
def test_ausduennen_zaehlt_ausgelieferte_punkte(tmp_path: Path) -> None:
    src = tmp_path / "dicht.laz"
    klasse = [2] * 400 + [6] * 400
    _las(src, klasse)
    pkg = tmp_path / "pkg"

    m = convert(src, pkg, "EPSG:25833", mode="direct", ausduennen=2.0)

    cloud = cloud_of(m)
    assert cloud["sourceOptions"]["classes"] == {"2": 400, "6": 400}
    assert sum(cloud["classes"].values()) == cloud["pointCount"] < 800
    copc = laspy.read(pkg / "cloud.copc.laz")
    werte, anzahl = np.unique(np.asarray(copc.classification), return_counts=True)
    assert cloud["classes"] == {str(int(w)): int(n) for w, n in zip(werte, anzahl, strict=True)}
    rep = verify_points(src, pkg, samples=200)
    assert rep["classes"]["checked"] and rep["classes"]["ok"], rep["classes"]
