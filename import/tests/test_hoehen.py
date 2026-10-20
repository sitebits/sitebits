"""Höhensystem: Normierung der Namen und Umrechnung ellipsoidisch ↔ DHHN2016 (Entscheidung 0015)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from scanview_import import hoehen

# Referenzwert: 100 m ellipsoidisch bei UTM 33N (368000, 5807000), Potsdam, mit GCG2016 über PROJ
# gerechnet (09.10.2026). Die Quasigeoidhöhe dort ist rund 40,16 m.
POTSDAM = (368000.0, 5807000.0)
POTSDAM_NHN_BEI_100 = 59.841306648269935


@pytest.mark.parametrize(
    ("wert", "erwartet"),
    [
        ("dhhn2016", "dhhn2016"),
        ("DHHN2016", "dhhn2016"),
        ("NHN", "dhhn2016"),
        ("ellipsoidisch", "ellipsoidal-etrs89"),
        ("ellipsoidal-etrs89", "ellipsoidal-etrs89"),
        ("Ellipsoidisch", "ellipsoidal-etrs89"),
        ("DHHN92", "dhhn92"),
        ("NN", None),
        ("irgendwas", None),
        (None, None),
    ],
)
def test_normiere(wert: str | None, erwartet: str | None) -> None:
    assert hoehen.normiere(wert) == erwartet


def test_gitter_pruefsumme_stimmt() -> None:
    hoehen.pruefe_gitter()


def test_gitter_mit_falscher_pruefsumme_bricht_ab(tmp_path: Path) -> None:
    falsch = tmp_path / hoehen.GITTER_NAME
    falsch.write_bytes(b"kein Gitter")
    with pytest.raises(hoehen.HoehenFehler, match="Prüfsumme"):
        hoehen.pruefe_gitter(falsch)


def test_gitter_fehlt_bricht_ab(tmp_path: Path) -> None:
    with pytest.raises(hoehen.HoehenFehler, match="fehlt"):
        hoehen.pruefe_gitter(tmp_path / hoehen.GITTER_NAME)


def test_ellipsoidisch_nach_nhn_potsdam() -> None:
    z = hoehen.rechne_um(
        np.array([POTSDAM[0]]),
        np.array([POTSDAM[1]]),
        np.array([100.0]),
        crs=25833,
        quelle="ellipsoidal-etrs89",
        ziel="dhhn2016",
    )
    assert z[0] == pytest.approx(POTSDAM_NHN_BEI_100, abs=1e-6)


def test_hin_und_zurueck_ist_identisch() -> None:
    x = np.array([368000.0, 368500.0, 369000.0])
    y = np.array([5807000.0, 5807500.0, 5808000.0])
    z = np.array([100.0, 35.5, 72.25])
    nhn = hoehen.rechne_um(x, y, z, crs=25833, quelle="ellipsoidal-etrs89", ziel="dhhn2016")
    zurueck = hoehen.rechne_um(x, y, nhn, crs=25833, quelle="dhhn2016", ziel="ellipsoidal-etrs89")
    np.testing.assert_allclose(zurueck, z, atol=1e-9)
    assert np.all((z - nhn > 39.0) & (z - nhn < 41.5))


# Unabhängige Sollwerte: BKG-Onlinerechner GCG2016 (gibs.bkg.bund.de/geoid/gscomp.php), 09.10.2026.
# Eingabe ETRS89 Breite/Länge + ellipsoidische Höhe; Lage hier nach UTM umgerechnet.
# (EPSG, Rechtswert, Hochwert, ellipsoidische Höhe, NHN laut BKG; BKG rundet auf mm)
BKG_PUNKTE = [
    pytest.param(25833, 367334.404, 5807317.817, 100.0, 59.837, id="potsdam-utm33"),
    pytest.param(25833, 453758.814, 5734557.633, 120.0, 79.083, id="cottbus-utm33"),
    pytest.param(25832, 549700.335, 5802442.719, 100.0, 57.199, id="hannover-utm32"),
    pytest.param(25832, 691928.086, 5335080.209, 560.0, 514.351, id="muenchen-utm32"),
]


@pytest.mark.parametrize(("epsg", "rechts", "hoch", "h_ellips", "nhn_bkg"), BKG_PUNKTE)
def test_gegen_bkg_rechner(
    epsg: int, rechts: float, hoch: float, h_ellips: float, nhn_bkg: float
) -> None:
    """Abnahme ADR 0015: höchstens 1 cm Abweichung zum BKG, in beide Richtungen."""
    x, y = np.array([rechts]), np.array([hoch])
    nhn = hoehen.rechne_um(
        x, y, np.array([h_ellips]), crs=epsg, quelle=hoehen.ELLIPSOIDISCH, ziel=hoehen.DHHN2016
    )
    assert nhn[0] == pytest.approx(nhn_bkg, abs=0.01)
    zurueck = hoehen.rechne_um(
        x, y, np.array([nhn_bkg]), crs=epsg, quelle=hoehen.DHHN2016, ziel=hoehen.ELLIPSOIDISCH
    )
    assert zurueck[0] == pytest.approx(h_ellips, abs=0.01)


def test_utm32_wird_aus_dem_crs_gebaut() -> None:
    # Gleicher Ort (Potsdam) in UTM 32N: Ergebnis muss dem UTM-33-Wert entsprechen.
    from pyproj import Transformer

    x32, y32 = Transformer.from_crs(25833, 25832, always_xy=True).transform(*POTSDAM)
    z = hoehen.rechne_um(
        np.array([x32]),
        np.array([y32]),
        np.array([100.0]),
        crs=25832,
        quelle="ellipsoidal-etrs89",
        ziel="dhhn2016",
    )
    assert z[0] == pytest.approx(POTSDAM_NHN_BEI_100, abs=1e-4)


def test_gleiches_system_aendert_nichts() -> None:
    z = np.array([1.0, 2.0])
    out = hoehen.rechne_um(
        np.zeros(2), np.zeros(2), z, crs=25833, quelle="dhhn2016", ziel="dhhn2016"
    )
    np.testing.assert_array_equal(out, z)


def test_ausserhalb_des_gitters_bricht_ab() -> None:
    with pytest.raises(hoehen.HoehenFehler, match="außerhalb"):
        hoehen.rechne_um(
            np.array([500000.0]),
            np.array([4000000.0]),
            np.array([100.0]),
            crs=25833,
            quelle="ellipsoidal-etrs89",
            ziel="dhhn2016",
        )


@pytest.mark.parametrize("epsg", [3857, 31468, 0])
def test_nicht_unterstuetztes_crs_bricht_ab(epsg: int) -> None:
    with pytest.raises(hoehen.HoehenFehler, match="nicht unterstützt"):
        hoehen.rechne_um(
            np.array([0.0]),
            np.array([0.0]),
            np.array([0.0]),
            crs=epsg,
            quelle="ellipsoidal-etrs89",
            ziel="dhhn2016",
        )


@pytest.mark.parametrize(
    ("quelle", "ziel"), [("dhhn92", "dhhn2016"), (None, "dhhn2016"), ("dhhn2016", "NN")]
)
def test_unbekanntes_oder_nicht_umrechenbares_system_bricht_ab(
    quelle: str | None, ziel: str
) -> None:
    with pytest.raises(hoehen.HoehenFehler):
        hoehen.rechne_um(
            np.array([POTSDAM[0]]),
            np.array([POTSDAM[1]]),
            np.array([0.0]),
            crs=25833,
            quelle=quelle,
            ziel=ziel,
        )


def test_fehlendes_gitter_rechnet_nie_still_weiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Die PROJ-Falle: ohne Gitter würde die automatische Auswahl still eine Nulloperation nehmen.
    kopie = tmp_path / "leer"
    kopie.mkdir()
    monkeypatch.setattr(hoehen, "GITTER", kopie / hoehen.GITTER_NAME)
    with pytest.raises(hoehen.HoehenFehler, match="fehlt"):
        hoehen.rechne_um(
            np.array([POTSDAM[0]]),
            np.array([POTSDAM[1]]),
            np.array([100.0]),
            crs=25833,
            quelle="ellipsoidal-etrs89",
            ziel="dhhn2016",
        )


def test_utm_zone_aus_wkt_auch_mit_achsfolge_ne() -> None:
    from pyproj import CRS

    assert hoehen.utm_zone(CRS.from_epsg(25833).to_wkt()) == 33
    assert hoehen.utm_zone(CRS.from_epsg(3045).to_wkt()) == 33  # LGB-Dateien: „(N-E)“
    assert hoehen.utm_zone("EPSG:25832") == 32


@pytest.mark.parametrize("crs", ["EPSG:32633", "EPSG:31468", "kein crs"])
def test_utm_zone_lehnt_fremde_ab(crs: str) -> None:
    with pytest.raises(hoehen.HoehenFehler):
        hoehen.utm_zone(crs)


@pytest.mark.parametrize(
    ("quelle", "ziel", "erwartet"),
    [
        (None, None, (None, None)),
        ("ellipsoidisch", None, ("ellipsoidal-etrs89", "dhhn2016")),
        ("DHHN2016", None, ("dhhn2016", "dhhn2016")),
        ("ellipsoidisch", "ellipsoidisch", ("ellipsoidal-etrs89", "ellipsoidal-etrs89")),
    ],
)
def test_festlegen(
    quelle: str | None, ziel: str | None, erwartet: tuple[str | None, str | None]
) -> None:
    assert hoehen.festlegen(quelle, ziel) == erwartet


@pytest.mark.parametrize(("quelle", "ziel"), [(None, "dhhn2016"), ("NN", None), ("dhhn2016", "x")])
def test_festlegen_fehler(quelle: str | None, ziel: str | None) -> None:
    with pytest.raises(hoehen.HoehenFehler):
        hoehen.festlegen(quelle, ziel)


def test_las_umrechnen_aendert_nur_z(tmp_path: Path) -> None:
    import laspy

    header = laspy.LasHeader(point_format=7, version="1.4")
    header.scales = np.array([0.001, 0.001, 0.001])
    header.offsets = np.array([368000.0, 5807000.0, 0.0])
    las = laspy.LasData(header)
    las.x = np.array([368000.0, 368100.5, 368200.25])
    las.y = np.array([5807000.0, 5807050.0, 5807100.0])
    las.z = np.array([100.0, 80.0, 60.0])
    las.classification = np.array([2, 5, 6], dtype=np.uint8)
    las.red = np.array([1, 2, 3], dtype=np.uint16)
    src, dst = tmp_path / "in.laz", tmp_path / "out.laz"
    las.write(src)

    hoehen.las_umrechnen(src, dst, crs=25833, quelle="ellipsoidal-etrs89", ziel="dhhn2016")

    out = laspy.read(dst)
    np.testing.assert_array_equal(out.x, las.x)
    np.testing.assert_array_equal(out.y, las.y)
    np.testing.assert_array_equal(out.classification, las.classification)
    np.testing.assert_array_equal(out.red, las.red)
    erwartet = hoehen.rechne_um(
        np.asarray(las.x),
        np.asarray(las.y),
        np.asarray(las.z),
        crs=25833,
        quelle="ellipsoidal-etrs89",
        ziel="dhhn2016",
    )
    np.testing.assert_allclose(out.z, erwartet, atol=0.0005)
    assert out.header.maxs[2] == pytest.approx(float(np.max(out.z)), abs=0.001)


def _frame(epsg: int, x: float, y: float, datum: str | None = "dhhn2016") -> dict[str, Any]:
    return {"crs": {"wkt": f"EPSG:{epsg}", "verticalDatum": datum}, "originWorld": [x, y, 0.0]}


@pytest.mark.parametrize(
    ("epsg", "rechts", "hoch", "h_ellips", "nhn_bkg"), [BKG_PUNKTE[0], BKG_PUNKTE[2]]
)
def test_undulation_am_ursprung_gegen_bkg(
    epsg: int, rechts: float, hoch: float, h_ellips: float, nhn_bkg: float
) -> None:
    """F23 Abnahme 3: Zweithöhe = Höhe ± ζ am Ursprung, ≤ 1 cm zum BKG-Rechner."""
    ebene = {"kind": "pointcloud",
             "boundsWorld": [[rechts - 50, hoch - 50, 0], [rechts + 50, hoch + 50, 30]]}
    u = hoehen.undulation(_frame(epsg, rechts, hoch), [ebene])
    assert u is not None and u["grid"] == "GCG2016"
    assert u["atOriginM"] == pytest.approx(h_ellips - nhn_bkg, abs=0.01)
    assert u["maxDeviationM"] < hoehen.UNDULATION_MAX_ABWEICHUNG


def test_undulation_grosses_paket_und_ohne_grundlage() -> None:
    _, rechts, hoch, *_ = BKG_PUNKTE[0].values
    _, cx, cy, *_ = BKG_PUNKTE[1].values
    weit = {"kind": "pointcloud", "boundsWorld": [[rechts, cy, 0], [cx, hoch, 1]]}
    u = hoehen.undulation(_frame(25833, rechts, hoch), [weit])
    assert u is not None and u["maxDeviationM"] > hoehen.UNDULATION_MAX_ABWEICHUNG
    assert hoehen.undulation(_frame(25833, rechts, hoch, datum=None), []) is None
    assert hoehen.undulation(_frame(25833, rechts, hoch, datum="dhhn92"), []) is None
    assert hoehen.undulation({"crs": {"wkt": None, "verticalDatum": "dhhn2016"},
                              "originWorld": [0, 0, 0]}, []) is None
    # Außerhalb des Gitters (Mittelmeer in UTM 33) → nichts statt einer geratenen Zahl.
    assert hoehen.undulation(_frame(25833, 400000.0, 4200000.0), []) is None


def test_write_manifest_setzt_und_entfernt_undulation(tmp_path: Path) -> None:
    from scanview_import.manifest_io import write_manifest

    _, rechts, hoch, *_ = BKG_PUNKTE[0].values
    m: dict[str, Any] = {"schemaVersion": 2, "frame": _frame(25833, rechts, hoch), "layers": []}
    write_manifest(tmp_path, m)
    frame = json.loads((tmp_path / "project.json").read_text())["frame"]
    assert frame["undulation"]["grid"] == "GCG2016"
    m["frame"]["crs"]["verticalDatum"] = None
    write_manifest(tmp_path, m)
    assert "undulation" not in json.loads((tmp_path / "project.json").read_text())["frame"]
