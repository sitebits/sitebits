"""Katalogprüfer P1: synthetische Lieferung erfüllt das Profil, je Zeile eine Negativkontrolle."""

from __future__ import annotations

import hashlib
import json
import os
import struct
from pathlib import Path
from typing import Any

import laspy
import numpy as np
import pytest
from e57_fixture import write_fixture
from PIL import Image, TiffImagePlugin, TiffTags
from pyproj import CRS

from scanview_import.model import Pose
from scanview_import.pruefen import cli, pruefarten
from scanview_import.pruefen.katalog import KatalogFehler, lade
from scanview_import.pruefen.lieferung import erfasse

PROFIL = cli.PROFILE / "bestandserfassung.toml"
P1 = {"L01", "L09", "L10", "L11", "L12", "L13", "L18"}
P2 = {"L06", "L07", "L08"}
P3 = {"L02", "L03", "L05"}
B1 = {"L04", "L19"}
# Amtliches DGM1-Kachel-GeoTIFF (LGB, 1 m, EPSG:25833 + DHHN2016) zur Gegenprobe des eigenen Lesers
DGM1 = Path(os.environ.get("SCANVIEW_AMTLICHES_DGM1", "/nicht/gesetzt"))


def _laz(pfad: Path, crs: CRS | None, klassen: list[int]) -> None:
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets = [365800.0, 5806700.0, 40.0]
    h.scales = [0.001, 0.001, 0.001]
    if crs is not None:
        h.add_crs(crs)
    las = laspy.LasData(h)
    n = len(klassen)
    las.x = 365800.0 + np.arange(n)
    las.y = np.full(n, 5806700.0)
    las.z = np.full(n, 41.0)
    las.classification = np.array(klassen, dtype=np.uint8)
    las.write(pfad)


def _geotiff(pfad: Path, pixel_m: float, epsg: int) -> None:
    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (pixel_m, pixel_m, 0.0)
    ifd.tagtype[33550] = TiffTags.DOUBLE
    ifd[33922] = (0.0, 0.0, 0.0, 365800.0, 5806710.0, 0.0)
    ifd.tagtype[33922] = TiffTags.DOUBLE
    ifd[34735] = (1, 1, 0, 3, 1024, 0, 1, 1, 3072, 0, 1, epsg, 4096, 0, 1, 7837)
    ifd.tagtype[34735] = TiffTags.SHORT
    Image.fromarray(np.zeros((8, 8), np.float32)).save(pfad, tiffinfo=ifd)


def _glb(pfad: Path) -> None:
    j = json.dumps({"asset": {"version": "2.0"}, "meshes": [{"primitives": []}]}).encode()
    j += b" " * (-len(j) % 4)
    pfad.write_bytes(struct.pack("<4sIIII", b"glTF", 2, 20 + len(j), len(j), 0x4E4F534A) + j)


def _summen(ordner: Path, name: str = "SHA256SUMS") -> None:
    zeilen = [
        f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(ordner).as_posix()}"
        for p in sorted(ordner.rglob("*"))
        if p.is_file() and p.name != name
    ]
    (ordner / name).write_text("\n".join(zeilen) + "\n")


def _lieferung(o: Path, crs: CRS | None = None, summen: bool = True) -> Path:
    o.mkdir()
    (o / "wolke").mkdir()
    crs = crs or CRS.from_user_input("EPSG:25833+7837")
    _laz(o / "wolke" / "bestand.laz", crs, [2, 2, 6, 5, 1])
    pose = Pose((365800.0, 5806700.0, 40.0), (1.0, 0.0, 0.0, 0.0))
    xyz = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])
    write_fixture(
        o / "wolke" / "bestand.e57",
        [(pose, xyz, np.zeros(3, np.int8))],
        coordinate_metadata=crs.to_wkt(),
    )
    _geotiff(o / "dgm_05m.tif", 0.5, 25833)
    _geotiff(o / "dom_05m.tif", 0.25, 25833)
    _glb(o / "modell.glb")
    (o / "bericht.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (o / "registrierbericht.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
    (o / "befunde.csv").write_text("id;x;y;z\n1;365800;5806700;41\n")
    if summen:
        _summen(o)
    return o


def _zeilen(e: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {z["id"]: z for z in e["zeilen"]}


@pytest.fixture
def lieferung(tmp_path: Path) -> Path:
    return _lieferung(tmp_path / "lieferung")


def test_lieferung_erfuellt_alle_p1_zeilen(lieferung: Path, tmp_path: Path) -> None:
    e = cli.pruefen(PROFIL, lieferung)
    z = _zeilen(e)
    for i in P1:
        assert z[i]["urteil"] == pruefarten.ERFUELLT, (i, z[i]["grund"])
    for i in P3:
        # Kontrollpunkte verlangt die Zeile selbst: ohne Liste nicht erfüllt, nicht „nicht prüfbar“
        assert z[i]["urteil"] == pruefarten.NICHT_ERFUELLT
        assert z[i]["grund"].startswith("keine Kontrollpunktliste"), (i, z[i]["grund"])
    for i in set(z) - P1 - P3:
        assert z[i]["urteil"] == pruefarten.NICHT_PRUEFBAR
        # L06 bis L08 (P2) brauchen die Auftragsfläche, L04/L19 (B1) das Referenz-DGM des
        # Auftraggebers, der Rest ein Werkzeug
        erwartet = "Grundlage fehlt" if i in P2 | B1 else "Werkzeug fehlt"
        assert z[i]["grund"].startswith(erwartet), (i, z[i]["grund"])
    assert e["gesamt"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "wolke/bestand.laz" in e["eingaenge_sha256"]
    # CLI: Exit 1 (Kontrollpunkte fehlen), Bericht und nachrechenbare Prüfsummen
    aus = tmp_path / "aus"
    assert cli.main([str(lieferung), "--profil", "bestandserfassung", "--aus", str(aus)]) == 1
    html = (aus / "bericht.html").read_text()
    assert "Nein: 3 von 19" in html and "Was der Bericht nicht kann" in html
    for zeile in (aus / "pruefsummen.sha256").read_text().splitlines():
        h, rel = zeile.split("  ", 1)
        assert hashlib.sha256((lieferung / rel).read_bytes()).hexdigest() == h


def test_ergebnis_ist_wiederholbar(lieferung: Path) -> None:
    from scanview_import.pruefen.bericht import ergebnis_hash

    assert ergebnis_hash(cli.pruefen(PROFIL, lieferung)) == ergebnis_hash(
        cli.pruefen(PROFIL, lieferung)
    )


def test_l01_falsches_bezugssystem_und_fehlende_hoehe(tmp_path: Path) -> None:
    z = _zeilen(cli.pruefen(PROFIL, _lieferung(tmp_path / "a", CRS.from_epsg(25832))))
    assert z["L01"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "statt EPSG:25833" in z["L01"]["grund"]
    assert "Höhensystem nicht deklariert" in z["L01"]["grund"]
    z = _zeilen(cli.pruefen(PROFIL, _lieferung(tmp_path / "b", CRS.from_epsg(25833))))
    assert z["L01"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "statt EPSG" not in z["L01"]["grund"]


def test_l01_ohne_deklaration_ist_nicht_erfuellt(lieferung: Path) -> None:
    _laz(lieferung / "wolke" / "ohne.laz", None, [2])
    z = _zeilen(cli.pruefen(PROFIL, lieferung))
    assert z["L01"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "ohne.laz: kein Lagebezug deklariert" in z["L01"]["grund"]


def test_l09_fehlende_klasse(lieferung: Path) -> None:
    _laz(lieferung / "wolke" / "bestand.laz", CRS.from_user_input("EPSG:25833+7837"), [2, 5])
    z = _zeilen(cli.pruefen(PROFIL, lieferung))
    assert z["L09"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L09"]["grund"] == "Klasse 6 fehlt"


def test_l10_raster_zu_grob_und_fremdes_ks(lieferung: Path) -> None:
    _geotiff(lieferung / "dgm_05m.tif", 1.0, 25833)
    _geotiff(lieferung / "dom_05m.tif", 0.5, 25832)
    g = _zeilen(cli.pruefen(PROFIL, lieferung))["L10"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "Raster 1 m > 0.5 m" in g["grund"] and "EPSG 25832 statt 25833" in g["grund"]


def test_l10_fehlendes_produkt(lieferung: Path) -> None:
    (lieferung / "dom_05m.tif").unlink()
    g = _zeilen(cli.pruefen(PROFIL, lieferung))["L10"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT and g["grund"].startswith("DOM: keine")


def test_l11_fehlendes_und_kaputtes_format(lieferung: Path) -> None:
    (lieferung / "wolke" / "bestand.e57").unlink()
    (lieferung / "modell.glb").write_bytes(b"kein glTF")
    g = _zeilen(cli.pruefen(PROFIL, lieferung))["L11"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "E57: keine Datei" in g["grund"] and "modell.glb: nicht lesbar" in g["grund"]


def test_l12_abweichend_fehlend_ueberzaehlig(lieferung: Path) -> None:
    (lieferung / "bericht.pdf").write_bytes(b"%PDF-1.4\ngeaendert\n")
    (lieferung / "befunde.csv").unlink()
    (lieferung / "nachgereicht.txt").write_text("x")
    g = _zeilen(cli.pruefen(PROFIL, lieferung))["L12"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "Prüfsumme weicht ab (1): bericht.pdf" in g["grund"]
    assert "gelistet, aber nicht geliefert (1): befunde.csv" in g["grund"]
    assert "geliefert, aber nicht gelistet (1): nachgereicht.txt" in g["grund"]


def test_l12_ohne_liste(tmp_path: Path) -> None:
    lf = _lieferung(tmp_path / "l", summen=False)
    g = _zeilen(cli.pruefen(PROFIL, lf))["L12"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT
    assert g["grund"] == "keine Prüfsummenliste in der Lieferung"


def test_l12_pfadausbruch_wird_nicht_gelesen(lieferung: Path, tmp_path: Path) -> None:
    geheim = tmp_path / "geheim.txt"
    geheim.write_text("nicht lesen")
    h = hashlib.sha256(b"nicht lesen").hexdigest()
    with (lieferung / "SHA256SUMS").open("a") as f:
        f.write(f"{h}  ../geheim.txt\n{h}  {geheim}\n{h} *wolke/../../geheim.txt\n")
    (lieferung / "verweis.txt").symlink_to(geheim)
    lf = erfasse(lieferung)
    zeile = next(z for z in lade(PROFIL).zeilen if z.id == "L12")
    erg = pruefarten.pruefe(zeile, lf)
    assert erg.urteil == pruefarten.NICHT_ERFUELLT
    assert "Pfad außerhalb der Lieferung, nicht gelesen (3)" in erg.grund
    assert lf.links == ["verweis.txt"] and "verweis.txt" not in lf.dateien
    assert all((lieferung / r).resolve().is_relative_to(lieferung) for r in lf.gelesen)


def test_l13_anhang_fehlt(lieferung: Path) -> None:
    (lieferung / "registrierbericht.pdf").unlink()
    g = _zeilen(cli.pruefen(PROFIL, lieferung))["L13"]
    assert g["urteil"] == pruefarten.NICHT_ERFUELLT


def test_unbekannte_pruefart_ist_nicht_pruefbar(lieferung: Path, tmp_path: Path) -> None:
    k = tmp_path / "k.toml"
    k.write_text(
        'schema = 1\ntitel = "T"\n[[zeile]]\nid = "X1"\nanforderung = "A"\npruefart = "orakel"\n'
    )
    z = cli.pruefen(k, lieferung)["zeilen"][0]
    assert z["urteil"] == pruefarten.NICHT_PRUEFBAR
    assert "„orakel“ unbekannt" in z["grund"]


@pytest.mark.parametrize(
    "text",
    [
        'schema = 2\ntitel = "T"\n[[zeile]]\nid="a"\nanforderung="b"\npruefart="c"\n',
        'schema = 1\ntitel = "T"\nzusatz = 1\n[[zeile]]\nid="a"\nanforderung="b"\npruefart="c"\n',
        'schema = 1\ntitel = "T"\n[[zeile]]\nid="a"\nanforderung="b"\npruefart="c"\nschwelle=1\n',
        'schema = 1\ntitel = "T"\n[[zeile]]\nid="a"\nanforderung="b"\n',
        'schema = 1\ntitel = "T"\n' + '[[zeile]]\nid="a"\nanforderung="b"\npruefart="c"\n' * 2,
        'schema = 1\ntitel = "T"\n[[zeile]]\nid="a"\nanforderung="b"\npruefart="bezugssystem"\n',
    ],
)
def test_kaputter_katalog_bricht_ab(text: str, lieferung: Path, tmp_path: Path) -> None:
    k = tmp_path / "k.toml"
    k.write_text(text)
    with pytest.raises(KatalogFehler):
        cli.pruefen(k, lieferung)
    assert cli.main([str(lieferung), "--katalog", str(k), "--aus", str(tmp_path / "a")]) == 2


def test_cli_exit_1_bei_mangel(lieferung: Path, tmp_path: Path) -> None:
    (lieferung / "SHA256SUMS").unlink()
    aus = tmp_path / "aus"
    assert cli.main([str(lieferung), "--profil", "bestandserfassung", "--aus", str(aus)]) == 1
    assert json.loads((aus / "ergebnis.json").read_text())["gesamt"]["urteil"] == "nicht erfüllt"


@pytest.mark.data
@pytest.mark.skipif(not DGM1.is_file(), reason="SCANVIEW_AMTLICHES_DGM1 nicht gesetzt")
def test_geotiff_leser_gegen_amtliches_dgm1() -> None:
    from scanview_import.pruefen.raster import geotiff

    g = geotiff(DGM1)
    assert (g["pixel_m"], g["epsg"], g["epsg_hoehe"]) == ((1.0, 1.0), 25833, 7837)


def test_kaputte_lieferdatei_ist_befund_kein_absturz(tmp_path: Path) -> None:
    # Eine kaputte LAZ brach den Prüfer früher in L09 (Klassen zählen) mit Traceback ab
    o = tmp_path / "lieferung"
    o.mkdir()
    (o / "wolke.laz").write_bytes(b"LASF\x00\x00kaputt")
    aus = tmp_path / "aus"
    assert cli.main([str(o), "--profil", "bestandserfassung", "--aus", str(aus)]) == 1
    z = _zeilen(json.loads((aus / "ergebnis.json").read_text()))
    assert z["L09"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L09"]["grund"].startswith("eine Lieferdatei ist nicht lesbar (LaspyException")
