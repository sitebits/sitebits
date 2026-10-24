"""Katalogprüfer B1/L04: Boden gegen Referenz-DGM, gegen bekannte Wahrheit und Negativkontrollen.

Wolke und DGM liegen auf einer schrägen Ebene mit Gefälle in x **und** y: Eine flache Ebene
verdeckte einen halben Pixel Versatz und eine gespiegelte Zeilenrichtung.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import pytest
from PIL import Image, TiffImagePlugin, TiffTags
from pyproj import CRS
from test_pruefen import _lieferung, _summen

from scanview_import.pruefen import cli, pruefarten
from scanview_import.pruefen.dgm import DgmFehler, lade

X0, Y0, Z0 = 365800.0, 5806700.0, 40.0
GX, GY = 0.3, -0.2  # Gefälle je m
UTM = CRS.from_user_input("EPSG:25833+7837")
SEITE = 10  # Wolke und DGM: 10 mal 10 m, 1-m-Pixel


def ebene(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    return Z0 + GX * (x - X0) + GY * (y - Y0)


def _wolke(pfad: Path, dach: tuple[float, float, int] | None = None, rausch: bool = False) -> None:
    """Gitter 0,1 m auf der Ebene, Klasse 2; optional ein 3-m-Dach (Höhe, x-Lage, Klasse) und
    ein tiefer Rauschpunkt der Klasse 2."""
    a = np.arange(0.05, SEITE, 0.1)
    x, y = (g.ravel() for g in np.meshgrid(X0 + a, Y0 + a))
    z, k = ebene(x, y), np.full(x.size, 2, np.uint8)
    if dach is not None:
        hoch, x_dach, klasse = dach
        drin = (x > X0 + x_dach) & (x < X0 + x_dach + 3) & (y > Y0 + 6) & (y < Y0 + 9)
        z[drin] += hoch
        k[drin] = klasse
    if rausch:
        x, y = np.append(x, X0 + 1.5), np.append(y, Y0 + 1.5)
        z, k = np.append(z, ebene(x[-1:], y[-1:]) - 0.5), np.append(k, 2)
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets, h.scales = [X0, Y0, Z0], [0.001, 0.001, 0.001]
    h.add_crs(UTM)
    las = laspy.LasData(h)
    las.x, las.y, las.z = x, y, z
    las.classification = k
    las.write(pfad)


def _dgm(
    pfad: Path,
    x0: float = X0,
    nx: int = SEITE,
    punkt: bool = False,
    hoehe: int | None = 7837,
    nodata: tuple[int, int] | None = None,
    verschoben: float = 0.0,
) -> Path:
    """Float-GeoTIFF, Pixelwert = Ebene in der Pixelmitte (bei einer Ebene = Mittel über das
    Pixel). ``punkt``: PixelIsPoint, Tiepoint in der Mitte des ersten Pixels. ``verschoben``:
    falsch georeferenziert — Tiepoint um so viele m nach Osten, Höhen unverändert."""
    mx, my = np.meshgrid(x0 + np.arange(nx) + 0.5, Y0 + SEITE - np.arange(SEITE) - 0.5)
    z = ebene(mx, my).astype(np.float32)
    if nodata is not None:
        z[nodata] = -9999
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (1.0, 1.0, 0.0)
    ifd.tagtype[33550] = TiffTags.DOUBLE
    tie = (x0 + verschoben + 0.5, Y0 + SEITE - 0.5) if punkt else (x0 + verschoben, Y0 + SEITE)
    ifd[33922] = (0.0, 0.0, 0.0, *tie, 0.0)
    ifd.tagtype[33922] = TiffTags.DOUBLE
    keys = [1024, 0, 1, 1, 1025, 0, 1, 2 if punkt else 1, 3072, 0, 1, 25833]
    if hoehe:
        keys += [4096, 0, 1, hoehe]
    ifd[34735] = (1, 1, 0, len(keys) // 4, *keys)
    ifd.tagtype[34735] = TiffTags.SHORT
    ifd[42113] = "-9999"
    ifd.tagtype[42113] = TiffTags.ASCII
    Image.fromarray(z).save(pfad, tiffinfo=ifd)
    return pfad


def _katalog(pfad: Path, mad: float = 0.01, toleranz: float = 0.05, anteil: float = 0.0) -> Path:
    pfad.write_text(
        "schema = 1\n"
        'titel = "Boden"\n'
        '[[zeile]]\nid = "L04"\nanforderung = "Höhe"\npruefart = "einpassung"\n'
        f"parameter = {{ mad_m = {mad} }}\n"
        '[[zeile]]\nid = "L19"\nanforderung = "Boden"\npruefart = "klassen_richtig"\n'
        f"parameter = {{ toleranz_m = {toleranz}, anteil = {anteil} }}\n"
    )
    return pfad


def _lf(tmp_path: Path, **wolke: Any) -> Path:
    o = _lieferung(tmp_path / "lieferung", summen=False)
    (o / "wolke" / "bestand.laz").unlink()  # nur die schräge Wolke
    _wolke(o / "wolke" / "boden.laz", **wolke)
    _summen(o)
    return o


def _pruefe(tmp_path: Path, dgm: list[Path] | None, **kw: Any) -> dict[str, dict[str, Any]]:
    lf = tmp_path / "lieferung" if (tmp_path / "lieferung").exists() else _lf(tmp_path)
    kat = _katalog(tmp_path / "k.toml", **kw)
    e = cli.pruefen(kat, lf, dgm=dgm)
    return {z["id"]: z for z in e["zeilen"]}


def test_schraege_ebene_passt_ueberall(tmp_path: Path) -> None:
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif")], mad=0.002, toleranz=0.01)
    assert z["L04"]["urteil"] == pruefarten.ERFUELLT, z["L04"]["grund"]
    assert z["L19"]["urteil"] == pruefarten.ERFUELLT, z["L19"]["grund"]
    assert "verglichen 100" in " ".join(z["L04"]["belege"])


def test_pixel_ist_punkt_liest_dasselbe_raster(tmp_path: Path) -> None:
    a = lade([_dgm(tmp_path / "a.tif")])
    b = lade([_dgm(tmp_path / "b.tif", punkt=True)])
    assert (a.x0, a.y0) == (b.x0, b.y0) == (X0, Y0 + SEITE)
    assert np.array_equal(a.z, b.z)


def test_halber_pixel_versatz_faellt_auf(tmp_path: Path) -> None:
    # Gegenprobe zur Ausrichtung: Höhen um 0,5 m falsch verortet → dh = 0,15 m in neun von zehn
    # Spalten, 0,075 m in der angeschnittenen letzten: Mittel 0,1425 m
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif", verschoben=0.5)], mad=0.01)
    assert z["L04"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "Abweichung zum Referenz-DGM 0,14" in z["L04"]["grund"]


def test_dach_als_boden(tmp_path: Path) -> None:
    _lf(tmp_path, dach=(4.0, 2.0, 2))
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif")], mad=0.3, toleranz=0.5, anteil=0.01)
    b = " ".join(z["L19"]["belege"])
    # 3 mal 3 m Dach auf 1-m-Pixeln: 9 von 100 Pixeln, alle darüber, keins darunter
    assert z["L19"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "9 von 100 Pixeln = 9,00 %" in b and "darüber 9 (9 m²" in b and "darunter 0" in b
    # L04: mittlere absolute Abweichung 9 · 4 m / 100 = 0,36 m, die Dächer bleiben drin
    assert z["L04"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "0,360 m" in z["L04"]["grund"]
    assert set(z["L19"]["karten"]) == {"L19-boden.png"}


def test_dach_als_gebaeude_ist_kein_befund(tmp_path: Path) -> None:
    _lf(tmp_path, dach=(4.0, 2.0, 6))
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif")], mad=0.01, toleranz=0.05)
    assert z["L19"]["urteil"] == pruefarten.ERFUELLT, z["L19"]["grund"]
    assert "ohne Bodenpunkt 9" in " ".join(z["L19"]["belege"])


def test_grube_unter_dem_dgm(tmp_path: Path) -> None:
    _lf(tmp_path, dach=(-2.0, 5.0, 2))
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif")], toleranz=0.5)
    assert "darüber 0" in " ".join(z["L19"]["belege"])
    assert "darunter 9" in " ".join(z["L19"]["belege"])


def test_rauschpunkt_zieht_das_pixel_nicht_herunter(tmp_path: Path) -> None:
    # Mittel statt Minimum: ein Punkt 0,5 m tief unter 100 verschiebt das Pixel um 5 mm
    _lf(tmp_path, rausch=True)
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif")], toleranz=0.05)
    assert z["L19"]["urteil"] == pruefarten.ERFUELLT, z["L19"]["grund"]


def test_ohne_dgm_nicht_pruefbar(tmp_path: Path) -> None:
    z = _pruefe(tmp_path, None)
    for i in ("L04", "L19"):
        assert z[i]["urteil"] == pruefarten.NICHT_PRUEFBAR
        assert z[i]["grund"].startswith("Grundlage fehlt: kein Referenz-DGM")


def test_anderes_hoehensystem_wird_nicht_umgerechnet(tmp_path: Path) -> None:
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif", hoehe=5783)])  # DHHN92
    assert z["L19"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "keine Umrechnung" in z["L19"]["grund"]


def test_ohne_hoehensystem_nur_mit_vorgabe(tmp_path: Path) -> None:
    ohne = _dgm(tmp_path / "dgm.tif", hoehe=None)
    with pytest.raises(DgmFehler, match="Höhensystem"):
        lade([ohne])
    assert lade([ohne], "EPSG:25833+7837").hoehe == lade([_dgm(tmp_path / "m.tif")]).hoehe
    with pytest.raises(DgmFehler, match="widerspricht"):
        lade([_dgm(tmp_path / "n.tif")], "EPSG:25833+5783")


def test_kaputtes_dgm_ist_eingabefehler(tmp_path: Path) -> None:
    lf = _lf(tmp_path)
    kaputt = tmp_path / "kaputt.tif"
    kaputt.write_bytes(b"II*\x00kein tiff")
    kat = _katalog(tmp_path / "k.toml")
    argv = [str(lf), "--katalog", str(kat), "--aus", str(tmp_path / "aus"), "--dgm", str(kaputt)]
    assert cli.main(argv) == 2


def test_kacheln_ergeben_dasselbe(tmp_path: Path) -> None:
    ganz = lade([_dgm(tmp_path / "ganz.tif")])
    links = _dgm(tmp_path / "l.tif", nx=4)
    rechts = _dgm(tmp_path / "r.tif", x0=X0 + 4, nx=6)
    teile = lade([rechts, links])
    assert (teile.x0, teile.y0) == (ganz.x0, ganz.y0)
    assert np.array_equal(teile.z, ganz.z)
    with pytest.raises(DgmFehler, match="Pixelgitter"):
        lade([links, _dgm(tmp_path / "schief.tif", x0=X0 + 4.25, nx=6)])


def test_nodata_wird_gezaehlt_nicht_verglichen(tmp_path: Path) -> None:
    z = _pruefe(tmp_path, [_dgm(tmp_path / "dgm.tif", nodata=(0, 0))], toleranz=0.05)
    assert "verglichen 99, ohne DGM-Höhe 1" in " ".join(z["L19"]["belege"])  # ohne Fläche
    assert z["L19"]["urteil"] == pruefarten.ERFUELLT


def test_flaeche_zaehlt_pixel_ohne_daten_mit(tmp_path: Path) -> None:
    lf = _lf(tmp_path)
    fl = tmp_path / "flaeche.geojson"
    ring = [[X0, Y0], [X0 + 10, Y0], [X0 + 10, Y0 + 10], [X0, Y0 + 10], [X0, Y0]]
    fl.write_text(
        json.dumps(
            {
                "type": "Feature",
                "crs": {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25833"}},
                "geometry": {"type": "Polygon", "coordinates": [ring]},
            }
        )
    )
    kat = tmp_path / "k.toml"
    kat.write_text(
        'schema = 1\ntitel = "B"\n[[zeile]]\nid = "L19"\nanforderung = "B"\n'
        'pruefart = "klassen_richtig"\n'
        "parameter = { toleranz_m = 0.05, anteil = 0.0, min_ueberdeckung = 1.0 }\n"
    )
    dgm = [_dgm(tmp_path / "dgm.tif", nodata=(0, 0))]
    e = cli.pruefen(kat, lf, flaeche=fl, dgm=dgm)
    z = e["zeilen"][0]
    assert z["urteil"] == pruefarten.NICHT_ERFUELLT and "99,0 %" in z["grund"]
    assert "Referenz-DGM dgm.tif" in e["eingaenge_extern_sha256"]
