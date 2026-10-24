"""Katalogprüfer P2: Dichte, Lücken, Bodenauflösung gegen bekannte Wahrheit, Negativkontrollen."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import pytest
from PIL import Image
from pyproj import CRS, Transformer
from scipy.spatial.transform import Rotation
from test_pruefen import PROFIL, _lieferung, _summen

from scanview_import.fotos import _quaternion
from scanview_import.fotos_pruefung import _paket_rotation
from scanview_import.pruefen import cli, pruefarten
from scanview_import.pruefen.flaeche import FlaechenFehler, lade

X0, Y0, Z0 = 365800.0, 5806700.0, 40.0
UTM = CRS.from_user_input("EPSG:25833+7837")
# Lücke: 2 mal 2 m ohne Punkte, also 4 der 100 Zellen der 10-m-Fläche
LUECKE = (X0 + 6, Y0 + 6, X0 + 8, Y0 + 8)


def _dichte_laz(pfad: Path, abstand: float = 0.1, luecke: bool = True) -> None:
    """Gitter über 12 mal 12 m, Klasse 2 auf Z0: bei 0,1 m genau 100 Punkte je 1-m-Zelle."""
    a = np.arange(abstand / 2, 12, abstand)
    x, y = np.meshgrid(X0 - 1 + a, Y0 - 1 + a)
    x, y = x.ravel(), y.ravel()
    if luecke:
        drin = (x > LUECKE[0]) & (x < LUECKE[2]) & (y > LUECKE[1]) & (y < LUECKE[3])
        x, y = x[~drin], y[~drin]
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets, h.scales = [X0, Y0, Z0], [0.001, 0.001, 0.001]
    h.add_crs(UTM)
    las = laspy.LasData(h)
    las.x, las.y, las.z = x, y, np.full(len(x), Z0)
    las.classification = np.full(len(x), 2, np.uint8)
    las.write(pfad)


def _ring(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def _flaeche(pfad: Path, loch: bool = False, wgs84: bool = False, crs: bool = True) -> Path:
    ringe = [_ring(X0, Y0, X0 + 10, Y0 + 10)]
    if loch:
        ringe.append(_ring(*LUECKE))
    d: dict[str, Any] = {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": ringe}}
    if wgs84:
        t = Transformer.from_crs(25833, 4326, always_xy=True)
        d["geometry"]["coordinates"] = [[list(t.transform(*p)) for p in r] for r in ringe]
    elif crs:
        d["crs"] = {"type": "name", "properties": {"name": "urn:ogc:def:crs:EPSG::25833"}}
    pfad.write_text(json.dumps(d))
    return pfad


def _kamera(nr: int, x: float, y: float, z: float, focal: float, neigung: float = 0) -> dict:
    # Kamera der Austauschbasis blickt entlang +Z; senkrecht nach unten, optional um x gekippt
    senkrecht = np.diag([1.0, -1.0, -1.0])
    r = Rotation.from_euler("x", neigung, degrees=True).as_matrix() @ senkrecht
    return {
        "id": f"F{nr:04d}",
        "pose": {"translation": [x, y, z], "rotationQuat": _quaternion(r)},
        "intrinsics": {"focalPx": focal, "width": 4000, "height": 3000},
    }


def _paket(o: Path, kameras: list[dict]) -> Path:
    p = o / "paket"
    p.mkdir()
    (p / "project.json").write_text(
        json.dumps(
            {
                "projectId": "test",
                "frame": {"crs": {"wkt": "EPSG:25833", "verticalDatum": "dhhn2016"}},
                "layers": [{"id": "fotos", "kind": "images", "url": "images.json"}],
            }
        )
    )
    (p / "images.json").write_text(json.dumps({"schemaVersion": 1, "images": kameras}))
    return p


@pytest.fixture
def lieferung(tmp_path: Path) -> Path:
    o = _lieferung(tmp_path / "lieferung", summen=False)
    _dichte_laz(o / "wolke" / "dicht.laz")
    _summen(o)
    return o


def _z(e: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {z["id"]: z for z in e["zeilen"]}


def test_quaternion_konvention_wie_der_schreiber() -> None:
    r = np.diag([1.0, -1.0, -1.0])
    blick = -_paket_rotation(_quaternion(r))[:, 2]
    assert np.allclose(blick, [0, 0, -1])


def test_dichte_und_luecken_mit_bekannter_wahrheit(lieferung: Path, tmp_path: Path) -> None:
    fl = _flaeche(tmp_path / "f.geojson")
    z = _z(cli.pruefen(PROFIL, lieferung, flaeche=fl))
    # 96 von 100 Zellen mit genau 100 Punkten/m² (≥ 95 % verlangt), 4 % Lücken (1 % erlaubt)
    assert z["L07"]["urteil"] == pruefarten.ERFUELLT, z["L07"]["grund"]
    assert z["L07"]["grund"].startswith("96,0 % der Fläche")
    assert z["L08"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L08"]["grund"] == "4,00 % der Fläche ohne Punkte, erlaubt 1,00 %"
    assert z["L07"]["karten"] == ["L07-dichte.png"] and z["L08"]["karten"] == ["L08-luecken.png"]


def test_loch_in_der_flaeche_nimmt_die_luecke_heraus(lieferung: Path, tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, lieferung, flaeche=_flaeche(tmp_path / "f.geojson", loch=True)))
    assert z["L08"]["urteil"] == pruefarten.ERFUELLT
    assert z["L08"]["grund"] == "0,00 % der Fläche ohne Punkte"
    assert z["L07"]["grund"].startswith("100,0 % der Fläche")


def test_wgs84_flaeche_ergibt_dasselbe(lieferung: Path, tmp_path: Path) -> None:
    utm = _z(cli.pruefen(PROFIL, lieferung, flaeche=_flaeche(tmp_path / "a.geojson")))
    wgs = _z(cli.pruefen(PROFIL, lieferung, flaeche=_flaeche(tmp_path / "b.geojson", wgs84=True)))
    assert utm["L08"]["grund"] == wgs["L08"]["grund"]
    assert utm["L07"]["grund"] == wgs["L07"]["grund"]


def test_zu_duenn_ist_nicht_erfuellt(tmp_path: Path) -> None:
    o = _lieferung(tmp_path / "l", summen=False)
    _dichte_laz(o / "wolke" / "dicht.laz", abstand=0.125, luecke=False)  # 64 Punkte/m²
    z = _z(cli.pruefen(PROFIL, o, flaeche=_flaeche(tmp_path / "f.geojson")))
    assert z["L07"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L07"]["grund"].startswith("nur 0,0 % der Fläche")


def test_doppelt_gelieferte_wolke_verdoppelt_nicht(tmp_path: Path) -> None:
    o = _lieferung(tmp_path / "l", summen=False)
    _dichte_laz(o / "wolke" / "dicht.laz", abstand=0.125, luecke=False)
    shutil.copy(o / "wolke" / "dicht.laz", o / "wolke" / "dicht-kopie.laz")
    z = _z(cli.pruefen(PROFIL, o, flaeche=_flaeche(tmp_path / "f.geojson")))
    # Summe ergäbe 128 Punkte/m² und damit „erfüllt“
    assert z["L07"]["urteil"] == pruefarten.NICHT_ERFUELLT


def test_ohne_crs_und_nicht_wgs84_bricht_ab(lieferung: Path, tmp_path: Path) -> None:
    fl = _flaeche(tmp_path / "f.geojson", crs=False)
    with pytest.raises(FlaechenFehler):
        lade(fl)
    args = [str(lieferung), "--profil", "bestandserfassung", "--aus", str(tmp_path / "a")]
    assert cli.main([*args, "--flaeche", str(fl)]) == 2
    assert cli.main([*args, "--flaeche", str(fl), "--flaeche-epsg", "25833"]) == 1


def test_punkt_statt_flaeche_bricht_ab(tmp_path: Path) -> None:
    f = tmp_path / "p.geojson"
    f.write_text('{"type": "Point", "coordinates": [13.0, 52.4]}')
    with pytest.raises(FlaechenFehler):
        lade(f)


def test_flaeche_steht_in_der_kette(lieferung: Path, tmp_path: Path) -> None:
    fl = _flaeche(tmp_path / "f.geojson")
    e = cli.pruefen(PROFIL, lieferung, flaeche=fl)
    assert list(e["eingaenge_extern_sha256"]) == ["Auftragsfläche f.geojson"]


def test_gsd_ist_hoehe_durch_brennweite(lieferung: Path, tmp_path: Path) -> None:
    kameras = [
        _kamera(1, X0 + 3, Y0 + 3, Z0 + 60, 4000),  # 15 mm
        _kamera(2, X0 + 4, Y0 + 4, Z0 + 80, 4000),  # 20 mm, genau an der Schwelle
        _kamera(3, X0 + 5, Y0 + 5, Z0 + 60, 4000, neigung=45),  # geneigt: fällt heraus
        _kamera(4, X0 + 50, Y0 + 50, Z0 + 60, 4000),  # außerhalb der Fläche
    ]
    paket = _paket(lieferung, kameras)
    _summen(lieferung)
    fl = _flaeche(tmp_path / "f.geojson")
    g = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=fl))["L06"]
    assert g["urteil"] == pruefarten.ERFUELLT, g["grund"]
    assert g["grund"] == "100,0 % der Luftbilder mit ≤ 20,0 mm"
    assert "2 Luftbilder" in g["belege"][0] and "1 geneigter, 1 außerhalb" in g["belege"][0]
    assert "Maximum 20,0 mm" in g["belege"][1] and "Median 17,5 mm" in g["belege"][1]


def test_gsd_folgt_der_blickachse(lieferung: Path, tmp_path: Path) -> None:
    # 15° geneigt: Entfernung 60 m / cos 15° = 62,1 m, GSD 15,5 mm statt 15,0 mm senkrecht
    paket = _paket(lieferung, [_kamera(1, X0 + 3, Y0 + 3, Z0 + 60, 4000, neigung=15)])
    _summen(lieferung)
    g = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=_flaeche(tmp_path / "f.geojson")))
    assert "Maximum 15,5 mm" in g["L06"]["belege"][1]


def test_gsd_zu_grob(lieferung: Path, tmp_path: Path) -> None:
    paket = _paket(lieferung, [_kamera(1, X0 + 3, Y0 + 3, Z0 + 60, 2000)])  # 30 mm
    _summen(lieferung)
    g = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=_flaeche(tmp_path / "f.geojson")))
    assert g["L06"]["urteil"] == pruefarten.NICHT_ERFUELLT


def _flaches_dgm(pfad: Path, z: float, hoehe: int = 7837) -> Path:
    from PIL import TiffImagePlugin, TiffTags

    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (1.0, 1.0, 0.0)
    ifd.tagtype[33550] = TiffTags.DOUBLE
    ifd[33922] = (0.0, 0.0, 0.0, X0 - 5, Y0 + 15, 0.0)
    ifd.tagtype[33922] = TiffTags.DOUBLE
    ifd[34735] = (1, 1, 0, 3, 1024, 0, 1, 1, 3072, 0, 1, 25833, 4096, 0, 1, hoehe)
    ifd.tagtype[34735] = TiffTags.SHORT
    Image.fromarray(np.full((20, 20), z, np.float32)).save(pfad, tiffinfo=ifd)
    return pfad


def test_gsd_misst_bis_zum_referenz_dgm_nicht_bis_zum_dach(lieferung: Path, tmp_path: Path) -> None:
    # Die Klasse-2-Fläche der Wolke liegt 20 m über dem DGM (ein „Dach als Boden“): aus der Wolke
    # 60 m → 15 mm, bis zum DGM 80 m → 20 mm
    paket = _paket(lieferung, [_kamera(1, X0 + 3, Y0 + 3, Z0 + 60, 4000)])
    _summen(lieferung)
    fl = _flaeche(tmp_path / "f.geojson")
    ohne = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=fl))["L06"]
    assert "Maximum 15,0 mm" in ohne["belege"][1]
    assert "ohne Referenz-DGM (--dgm) ungeprüft" in ohne["belege"][2]
    dgm = [_flaches_dgm(tmp_path / "dgm.tif", Z0 - 20)]
    mit = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=fl, dgm=dgm))["L06"]
    assert "Maximum 20,0 mm" in mit["belege"][1]
    assert "Referenz-DGM des Auftraggebers (dgm.tif)" in mit["belege"][2]
    # anderes Höhensystem als das Paket: nicht verwenden, Grund nennen, Wolke bleibt
    dhhn92 = [_flaches_dgm(tmp_path / "alt.tif", Z0 - 20, hoehe=5783)]
    alt = _z(cli.pruefen(PROFIL, lieferung, paket=paket, flaeche=fl, dgm=dhhn92))["L06"]
    assert "Maximum 15,0 mm" in alt["belege"][1]
    assert "nicht verwendbar (Höhe dhhn92" in alt["belege"][2]


def test_gsd_ohne_paket_ist_nicht_pruefbar(lieferung: Path, tmp_path: Path) -> None:
    g = _z(cli.pruefen(PROFIL, lieferung, flaeche=_flaeche(tmp_path / "f.geojson")))["L06"]
    assert g["urteil"] == pruefarten.NICHT_PRUEFBAR and g["grund"].startswith("Werkzeug fehlt")


def test_bericht_bettet_karten_ein(lieferung: Path, tmp_path: Path) -> None:
    aus = tmp_path / "aus"
    fl = _flaeche(tmp_path / "f.geojson")
    args = [str(lieferung), "--profil", "bestandserfassung", "--aus", str(aus)]
    assert cli.main([*args, "--flaeche", str(fl)]) == 1
    assert (aus / "L08-luecken.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (aus / "bericht.html").read_text().count('class="karte"') == 2


PAKET_MIT_FOTOS = os.environ.get("SCANVIEW_PAKET_MIT_FOTOS")


@pytest.mark.data
@pytest.mark.skipif(not PAKET_MIT_FOTOS, reason="SCANVIEW_PAKET_MIT_FOTOS nicht gesetzt")
def test_echte_nadirfotos_blicken_senkrecht() -> None:
    d = json.loads((Path(str(PAKET_MIT_FOTOS)) / "images.json").read_text())
    neigung: dict[str, list[float]] = {}
    for e in d["images"]:
        blick = -_paket_rotation(e["pose"]["rotationQuat"])[:, 2]
        neigung.setdefault(e["group"], []).append(float(np.degrees(np.arccos(-blick[2]))))
    nadir = [np.median(v) for k, v in neigung.items() if "nadir" in k.lower()]
    fassade = [np.median(v) for k, v in neigung.items() if "fassade" in k.lower()]
    assert nadir and max(nadir) < 10 and fassade and min(fassade) > 45
