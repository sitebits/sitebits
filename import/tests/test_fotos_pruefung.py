"""Rückprojektion (Import) und Paketprüfung `fotos-pruefen`."""

import json

import pytest
from PIL import Image
from scipy.spatial.transform import Rotation

from scanview_import.cli import main
from scanview_import.fotos import write_images
from scanview_import.fotos_pruefung import pruefe_fotos_paket, rueckprojektion
from scanview_import.kameras import TieProjection, read_frame_cameras
from scanview_import.verzeichnung import Kalibrierung
from tests.test_fotos import frames, projektionen_json
from tests.test_kameras_cli import package
from tests.test_kameras_frames import CALIB


def projektionen(path):
    return [
        TieProjection(p["camera"], tuple(p["pixel"]), tuple(p["world"]))
        for p in projektionen_json(path)
    ]


def test_rueckprojektion_exakt(tmp_path):
    path = frames(tmp_path)
    export = read_frame_cameras(path)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    bericht = rueckprojektion(images, export, projektionen(path))
    assert bericht["ok"] and bericht["medianPx"] < 0.01
    assert {k["id"] for k in bericht["perImage"]} == {"F0001", "F0002"}


def test_rueckprojektion_findet_gedrehte_pose(tmp_path):
    path = frames(tmp_path)
    export = read_frame_cameras(path)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    gedreht = Rotation.from_euler("x", 1, degrees=True)  # quer zur Blickachse
    w, x, y, z = images["images"][0]["pose"]["rotationQuat"]
    q = (gedreht * Rotation.from_quat([x, y, z, w])).as_quat()
    images["images"][0]["pose"]["rotationQuat"] = [q[3], q[0], q[1], q[2]]
    bericht = rueckprojektion(images, export, projektionen(path))
    assert not bericht["ok"]
    assert max(k["medianPx"] for k in bericht["perImage"]) > 2


def test_rueckprojektion_braucht_projektionen(tmp_path):
    path = frames(tmp_path)
    export = read_frame_cameras(path)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    with pytest.raises(ValueError, match="Tie-Point-Projektionen"):
        rueckprojektion(images, export, projektionen(path)[:5])


def test_paketpruefung_gruen_und_findet_fehler(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64))
    assert pruefe_fotos_paket(pkg)["ok"]
    erstes = pkg / "fotos" / images["images"][0]["id"] / images["images"][0]["rev"]
    # Fehlende Kachel, falsche Stufengröße, eingeschleuste EXIF-Daten: je ein Befund.
    next((erstes / "tiles" / "0").glob("*.jpg")).unlink()
    Image.new("RGB", (10, 10)).save(erstes / "32.jpg")
    zweites = pkg / "fotos" / images["images"][1]["id"] / images["images"][1]["rev"]
    exif = Image.Exif()
    exif[0x010F] = "DJI"
    Image.new("RGB", (64, 43)).save(zweites / "64.jpg", exif=exif.tobytes())
    bericht = pruefe_fotos_paket(pkg)
    assert not bericht["ok"]
    text = " ".join(bericht["fehler"])
    assert "fehlt" in text and "Größe" in text and "Metadaten" in text


def test_cli_fotos_pruefen(tmp_path, capsys):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    write_images(pkg, export, max_kante=4096)
    assert main(["fotos-pruefen", str(pkg)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    (pkg / "images.json").write_text("{}")
    assert main(["fotos-pruefen", str(pkg)]) == 1


def test_kalibrierung_fixture_bleibt_gleich():
    assert Kalibrierung.aus_export(240, 160, CALIB).f == 300.0


def test_paketpruefung_findet_veraltete_revision(tmp_path):
    """Inhalt geändert, Revision nicht: der Browser-Cache zeigte das alte Bild (M7)."""
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    b = images["images"][0]
    datei = pkg / "fotos" / b["id"] / b["rev"] / f"{b['levels'][-1]}.jpg"
    groesse = Image.open(datei).size
    Image.new("RGB", groesse, (200, 10, 10)).save(datei)
    bericht = pruefe_fotos_paket(pkg)
    assert not bericht["ok"]
    assert any("Revision" in f for f in bericht["fehler"]), bericht["fehler"]


def test_paketpruefung_altes_paket_ohne_revision(tmp_path):
    """Pakete vor M7 (ohne `rev`, Dateien direkt unter fotos/<id>/) bleiben gültig."""
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    for b in images["images"]:
        ordner = pkg / "fotos" / b["id"]
        for datei in (ordner / b.pop("rev")).iterdir():
            datei.rename(ordner / datei.name)
    (pkg / "images.json").write_text(json.dumps(images))
    assert pruefe_fotos_paket(pkg)["ok"]


def _verschoben(path, kamera, dx):
    """Projektionen einer Kamera um dx px versetzt (Restfehler der Ausrichtung)."""
    return [
        TieProjection(p.camera, (p.pixel[0] + dx, p.pixel[1]), p.world) if p.camera == kamera else p
        for p in projektionen(path)
    ]


def test_rueckprojektion_grenze_anheben_vermerkt_ueberschreitung(tmp_path):
    path = frames(tmp_path)
    export = read_frame_cameras(path)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    proj = _verschoben(path, export.cameras[0].label, 3.0)
    standard = rueckprojektion(images, export, proj)
    assert not standard["ok"] and standard["limitPx"] == standard["defaultLimitPx"] == 2.0
    angehoben = rueckprojektion(images, export, proj, grenze_px=4.0)
    assert angehoben["ok"] and angehoben["limitPx"] == 4.0
    label = {e["id"]: e["label"] for e in images["images"]}
    assert [label[b["id"]] for b in angehoben["aboveDefault"]] == [export.cameras[0].label]
    assert 2.5 < angehoben["aboveDefault"][0]["medianPx"] < 3.5
    assert not rueckprojektion(images, export, proj, grenze_px=2.5)["ok"]


@pytest.mark.parametrize("grenze", [0.0, -1.0, 10.5])
def test_rueckprojektion_grenze_begrenzt(tmp_path, grenze):
    path = frames(tmp_path)
    export = read_frame_cameras(path)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    images = write_images(pkg, export, max_kante=4096)
    with pytest.raises(ValueError, match="Rückprojektionsgrenze"):
        rueckprojektion(images, export, projektionen(path), grenze_px=grenze)
