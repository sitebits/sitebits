"""Orientierte Fotos: Stufen, Kacheln, images.json, Metadaten, Hook, Rahmenprüfung, CLI."""

import json
from pathlib import Path

import laspy
import numpy as np
import pytest
from PIL import Image
from scipy.spatial.transform import Rotation

from scanview_import.cli import main
from scanview_import.fotos import kachel_raster, stufen_fuer, write_images
from scanview_import.metashape import read_frame_cameras
from scanview_import.verzeichnung import Kalibrierung, verzeichnen, zielkamera
from tests.test_metashape_cli import package
from tests.test_metashape_frames import CALIB, export_v2

GPS_EXIF = Image.Exif()
GPS_EXIF[0x010F] = "DJI"  # Make
GPS_EXIF[0x8825] = {1: "N", 2: (52.0, 24.0, 1.0)}  # GPSInfo


def _mit_exif(path: Path, size: tuple[int, int], orientation: int = 1) -> None:
    rng = np.random.default_rng(len(path.name))
    exif = Image.Exif()
    exif.update(GPS_EXIF)
    exif[0x0112] = orientation
    Image.fromarray(rng.integers(0, 256, (size[1], size[0], 3), dtype=np.uint8)).save(
        path, exif=exif.tobytes(), quality=95
    )


def frames(tmp_path: Path) -> Path:
    path = export_v2(tmp_path)
    _mit_exif(tmp_path / "F1.jpg", (240, 160))
    _mit_exif(tmp_path / "F2.jpg", (160, 240))
    return path


def _segmente(data: bytes) -> list[int]:
    """JPEG-Marker bis zum Bildbeginn (SOS)."""
    assert data[:2] == b"\xff\xd8"
    pos, marker = 2, []
    while pos < len(data):
        m = data[pos + 1]
        marker.append(m)
        if m == 0xDA:
            break
        laenge = int.from_bytes(data[pos + 2 : pos + 4], "big")
        pos += 2 + laenge
    return marker


def test_stufen_lange_kante():
    assert stufen_fuer(7765, 4096) == [512, 2048, 4096]
    assert stufen_fuer(1150, 4096) == [512, 1150]
    assert stufen_fuer(300, 4096) == [300]
    assert stufen_fuer(7765, None) == [512, 2048, 4096]
    assert stufen_fuer(3000, 2048) == [512, 2048]
    assert stufen_fuer(230, None, stufen=(32, 64)) == [32, 64]


def test_kachelraster_deckt_ohne_luecke():
    raster = kachel_raster(1000, 700, 256, bis_kante=300)
    ebenen = sorted({z for z, *_ in raster})
    assert ebenen == [0, 1, 2]  # 1000 → 500 → 250 ≤ 300
    for z in ebenen:
        breite, hoehe = (1000 + 2**z - 1) // 2**z, (700 + 2**z - 1) // 2**z
        maske = np.zeros((hoehe, breite), bool)
        for zz, _, _, (x0, y0, x1, y1) in raster:
            if zz == z:
                assert not maske[y0:y1, x0:x1].any()
                maske[y0:y1, x0:x1] = True
        assert maske.all()


def test_images_json_und_posen(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    out = write_images(pkg, export, max_kante=4096)
    assert out["schemaVersion"] == 1
    assert out["anonymization"] == {"required": True, "status": "unreviewed"}
    assert [g["id"] for g in out["groups"]] == ["Fassade", "Nadir"]
    bilder = {b["label"]: b for b in out["images"]}
    assert sorted(b["id"] for b in out["images"]) == ["F0001", "F0002"]
    f1 = bilder["F1"]
    ziel = zielkamera(Kalibrierung.aus_export(240, 160, CALIB))
    assert f1["intrinsics"] == {"focalPx": ziel.f, "width": ziel.width, "height": ziel.height}
    assert f1["levels"] == [max(ziel.width, ziel.height)]
    assert f1["tiles"] is None
    # Metashape (X rechts, Y unten, Z vorn) → Naht (+Y oben, -Z Blick): R @ diag(1, -1, -1).
    w, x, y, z = f1["pose"]["rotationQuat"]
    np.testing.assert_allclose(
        Rotation.from_quat([x, y, z, w]).as_matrix(), np.diag([1.0, -1.0, -1.0]), atol=1e-12
    )
    assert f1["pose"]["translation"] == [10.0, 20.0, 30.0]
    hoch = bilder["F2"]["intrinsics"]
    assert hoch["height"] > hoch["width"]
    manifest = json.loads((pkg / "project.json").read_text())
    ebene = next(lay for lay in manifest["layers"] if lay["id"] == "fotos")
    assert ebene == {
        "id": "fotos",
        "kind": "images",
        "title": "Fotos",
        "url": "images.json",
        "count": 2,
    }
    assert json.loads((pkg / "images.json").read_text()) == out


def test_revision_im_pfad_und_inhaltsabhaengig(tmp_path):
    """Review-Minor M7: Neuer Inhalt bekommt eine neue URL (fotos/<id>/<rev>/…), sonst zeigte der
    Browser bis zu einer Stunde das alte Bild unter derselben ID — auch ein nicht anonymisiertes."""
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    erst = write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64))
    for b in erst["images"]:
        assert len(b["rev"]) == 16 and set(b["rev"]) <= set("0123456789abcdef"), b["rev"]
        ordner = pkg / "fotos" / b["id"]
        assert [d.name for d in ordner.iterdir()] == [b["rev"]]
        for stufe in b["levels"]:
            assert (ordner / b["rev"] / f"{stufe}.jpg").is_file()
        assert (ordner / b["rev"] / "tiles").is_dir()
    gleich = write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64))
    assert [b["rev"] for b in gleich["images"]] == [b["rev"] for b in erst["images"]]
    anonym = write_images(
        pkg, export, max_kante=None, kachel=64, stufen=(32, 64), anonymize=_schwarz
    )
    for alt, neu in zip(erst["images"], anonym["images"], strict=True):
        assert alt["id"] == neu["id"] and alt["rev"] != neu["rev"]
        assert not (pkg / "fotos" / alt["id"] / alt["rev"]).exists()


def test_ausgabe_ohne_metadaten(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64))
    dateien = list((pkg / "fotos").rglob("*.jpg"))
    assert len(dateien) > 4
    for datei in dateien:
        marker = _segmente(datei.read_bytes())
        # Nur JFIF (APP0), keine APP1 (EXIF/XMP), APP2 (ICC), APP13 (IPTC), Kommentare.
        assert not {0xE1, 0xE2, 0xED, 0xFE} & set(marker), (datei, marker)


def test_exif_drehung_wird_nicht_geraten(tmp_path):
    path = frames(tmp_path)
    _mit_exif(tmp_path / "F1.jpg", (240, 160), orientation=6)
    with pytest.raises(ValueError, match="Orientation"):
        write_images(package(tmp_path, wkt="EPSG:32633"), read_frame_cameras(path), max_kante=4096)


def _schwarz(bild, kamera):
    return np.zeros_like(bild)


def test_hook_vor_allen_stufen_und_kacheln(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64), anonymize=_schwarz)
    for datei in (pkg / "fotos").rglob("*.jpg"):
        assert np.asarray(Image.open(datei)).max() <= 8, datei


def test_stufen_nie_groesser_als_quelle_und_kacheln_passen(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    out = write_images(pkg, export, max_kante=None, kachel=64, stufen=(32, 64))
    for bild in out["images"]:
        lang = max(bild["intrinsics"]["width"], bild["intrinsics"]["height"])
        for stufe in bild["levels"]:
            with Image.open(pkg / "fotos" / bild["id"] / bild["rev"] / f"{stufe}.jpg") as im:
                assert max(im.size) == stufe <= lang
        kacheln = bild["tiles"]
        assert kacheln["size"] == 64
        with Image.open(pkg / "fotos" / bild["id"] / bild["rev"] / "tiles" / "0" / "0_0.jpg") as im:
            assert im.size == (64, 64)


def test_fehler_laesst_altes_paket_unveraendert(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    write_images(pkg, export, max_kante=4096)
    vorher = {p: p.read_bytes() for p in pkg.rglob("*") if p.is_file()}

    def kaputt(bild, kamera):
        if kamera.label == "F2":
            raise RuntimeError("Hook gescheitert")
        return bild

    with pytest.raises(RuntimeError):
        write_images(pkg, export, max_kante=4096, anonymize=kaputt)
    nachher = {p: p.read_bytes() for p in pkg.rglob("*") if p.is_file()}
    assert nachher == vorher


def test_parallel_bytegleich(tmp_path):
    export = read_frame_cameras(frames(tmp_path))
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    a = package(tmp_path / "a", wkt="EPSG:32633", datum="DHHN2016")
    b = package(tmp_path / "b", wkt="EPSG:32633", datum="DHHN2016")
    write_images(a, export, max_kante=None, kachel=64, stufen=(32, 64), prozesse=1)
    write_images(b, export, max_kante=None, kachel=64, stufen=(32, 64), prozesse=2)
    dateien = sorted(p.relative_to(a) for p in a.rglob("*.jpg"))
    assert dateien == sorted(p.relative_to(b) for p in b.rglob("*.jpg"))
    assert all((a / d).read_bytes() == (b / d).read_bytes() for d in dateien)


# --- CLI mit Rahmenprüfung -------------------------------------------------------------------


def _szene(n=30_000, seed=0):
    rng = np.random.default_rng(seed)
    d = n // 3
    boden = np.column_stack([rng.uniform(0, 40, (d, 2)), rng.normal(0, 0.003, d)])
    wand_x = np.column_stack(
        [rng.uniform(0, 40, d), 20 + rng.normal(0, 0.003, d), rng.uniform(0, 6, d)]
    )
    wand_y = np.column_stack(
        [20 + rng.normal(0, 0.003, d), rng.uniform(0, 40, d), rng.uniform(0, 6, d)]
    )
    return np.vstack([boden, wand_x, wand_y]) + [400_000.0, 5_800_000.0, 30.0]


def _wolke_schreiben(pkg: Path, punkte: np.ndarray) -> None:
    header = laspy.LasHeader(point_format=6, version="1.4")
    header.scales = [0.001] * 3
    header.offsets = punkte.min(axis=0)
    las = laspy.LasData(header)
    las.x, las.y, las.z = punkte.T
    las.write(pkg / "cloud.copc.laz")
    manifest = json.loads((pkg / "project.json").read_text())
    manifest["layers"][0]["url"] = "cloud.copc.laz"
    (pkg / "project.json").write_text(json.dumps(manifest))


def projektionen_json(path: Path, n: int = 30, seed: int = 0) -> list[dict]:
    """Tie-Point-Projektionen exakt aus Pose + Metashape-Modell (wie Metashape sie liefert)."""
    export = read_frame_cameras(path)
    rng = np.random.default_rng(seed)
    out = []
    for kamera in export.cameras:
        k = export.sensors[kamera.sensor_id].kalibrierung
        for _ in range(n):
            x, y = rng.uniform(-0.3, 0.3, 2)
            welt = np.array(kamera.position) + kamera.rotation @ (
                np.array([x, y, 1.0]) * rng.uniform(5, 20)
            )
            u, v = verzeichnen(k, np.array([x]), np.array([y]))
            pixel = [float(u[0]), float(v[0])]
            out.append({"camera": kamera.label, "pixel": pixel, "world": welt.tolist()})
    return out


def _cli_paket(tmp_path, versatz=(0.0, 0.0, 0.0)):
    path = frames(tmp_path)
    data = json.loads(path.read_text())
    data["tieProjections"] = projektionen_json(path)
    szene = _szene()
    rng = np.random.default_rng(5)
    tie = szene[rng.choice(len(szene), 400, replace=False)] + versatz
    data["tiePoints"] = tie.tolist()
    path.write_text(json.dumps(data))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    _wolke_schreiben(pkg, szene)
    return path, pkg


def test_cli_fotos_mit_rahmenpruefung(tmp_path, capsys):
    path, pkg = _cli_paket(tmp_path)
    assert main(["fotos-metashape", "--kameras", str(path), str(pkg)]) == 0
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["exportiert"] == 2
    assert bericht["rahmenpruefung"]["n"] == 400
    assert bericht["anonymisierung"] == "unreviewed"
    images = json.loads((pkg / "images.json").read_text())
    assert images["provenance"]["frameCheck"]["n"] == 400


def test_cli_anderer_ausrichtungsstand_schreibt_nichts(tmp_path, capsys):
    path, pkg = _cli_paket(tmp_path, versatz=(0.0, 0.8, 0.0))
    assert main(["fotos-metashape", "--kameras", str(path), str(pkg)]) == 1
    assert "Ausrichtungsstand" in capsys.readouterr().err
    assert not (pkg / "images.json").exists() and not (pkg / "fotos").exists()


def test_cli_crs_passt_nicht(tmp_path, capsys):
    path, _ = _cli_paket(tmp_path)
    (tmp_path / "x").mkdir()
    falsch = package(tmp_path / "x", wkt="EPSG:32632", datum="DHHN2016")
    assert main(["fotos-metashape", "--kameras", str(path), str(falsch)]) == 1
    assert "CRS" in capsys.readouterr().err


def test_cli_gruppenauswahl(tmp_path, capsys):
    path, pkg = _cli_paket(tmp_path)
    assert main(["fotos-metashape", "--kameras", str(path), str(pkg), "--gruppe", "Nadir"]) == 0
    images = json.loads((pkg / "images.json").read_text())
    assert [b["label"] for b in images["images"]] == ["F1"]
    capsys.readouterr()


def test_verzeichnung_wird_tatsaechlich_entfernt(tmp_path):
    """Punkt im Originalbild landet im entzerrten Foto an der Lochkamera-Position."""
    path = frames(tmp_path)
    k = Kalibrierung.aus_export(240, 160, CALIB)
    bild = np.zeros((160, 240, 3), np.uint8)
    u, v = verzeichnen(k, np.array([0.2]), np.array([-0.1]))
    iu, iv = int(u[0]), int(v[0])  # Pixel (iu, iv) enthält (u, v)
    bild[iv - 1 : iv + 2, iu - 1 : iu + 2] = 255
    Image.fromarray(bild).save(tmp_path / "F1.jpg", quality=100)
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    out = write_images(pkg, read_frame_cameras(path), max_kante=4096)
    f1 = next(b for b in out["images"] if b["label"] == "F1")
    with Image.open(pkg / "fotos" / f1["id"] / f1["rev"] / f"{f1['levels'][-1]}.jpg") as im:
        a = np.asarray(im.convert("L"), float)
    jj, ii = np.unravel_index(np.argmax(a), a.shape)
    w, h, f = f1["intrinsics"]["width"], f1["intrinsics"]["height"], f1["intrinsics"]["focalPx"]
    erwartet = (w / 2 + 0.2 * f - 0.5, h / 2 - 0.1 * f - 0.5)
    assert abs(ii - erwartet[0]) <= 1.5 and abs(jj - erwartet[1]) <= 1.5


def test_jpeg_exif_quelle_hat_wirklich_exif(tmp_path):
    """Gegenprobe: Die Testquelle trägt EXIF, sonst prüft der Metadaten-Test nichts."""
    frames(tmp_path)
    assert 0xE1 in _segmente((tmp_path / "F1.jpg").read_bytes())


def test_arbeitsordner_sind_versteckt_auch_nach_absturz(tmp_path, monkeypatch):
    """Bleibt nach hartem Abbruch etwas liegen, beginnt es mit einem Punkt (Caddy: hide .*)."""
    import shutil

    import scanview_import.fotos as fotos

    export = read_frame_cameras(frames(tmp_path))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    write_images(pkg, export, max_kante=4096)
    # Absturz simulieren: Aufräumen fällt aus.
    monkeypatch.setattr(fotos.shutil, "rmtree", lambda *a, **k: None)

    def kaputt(bild, kamera):
        raise RuntimeError("abgebrochen")

    with pytest.raises(RuntimeError):
        write_images(pkg, export, max_kante=4096, anonymize=kaputt)
    write_images(pkg, export, max_kante=4096)  # Tausch ohne Aufräumen
    monkeypatch.setattr(fotos.shutil, "rmtree", shutil.rmtree)
    sichtbar = {p.name for p in pkg.iterdir() if not p.name.startswith(".")}
    assert sichtbar <= {"project.json", "images.json", "fotos", "cloud.copc.laz"}, sichtbar


def test_exif_drehung_schon_beim_lesen(tmp_path):
    path = frames(tmp_path)
    _mit_exif(tmp_path / "F2.jpg", (160, 240), orientation=8)
    with pytest.raises(ValueError, match="Orientation"):
        read_frame_cameras(path)


def test_schema1_paket_frueh_und_verstaendlich(tmp_path, capsys):
    path, _ = _cli_paket(tmp_path)
    (tmp_path / "s1").mkdir()
    alt = package(tmp_path / "s1", schema=1, wkt="EPSG:32633", datum="DHHN2016")
    assert main(["fotos-metashape", "--kameras", str(path), str(alt)]) == 1
    assert "Schema-2-Paket" in capsys.readouterr().err
    assert not any(p.name.startswith(".fotos") for p in alt.iterdir())


def test_exif_drehung_ignorieren_nimmt_sensorpixel(tmp_path, capsys):
    """Orientation 6/8 mit passenden Rohmaßen: Pixel unverändert wie ohne EXIF-Drehung."""
    ohne_dir, mit_dir = tmp_path / "ohne", tmp_path / "mit"
    ohne_dir.mkdir()
    mit_dir.mkdir()
    path, pkg = _cli_paket(ohne_dir)
    assert main(["fotos-metashape", "--kameras", str(path), str(pkg)]) == 0
    capsys.readouterr()
    path_mit, pkg_mit = _cli_paket(mit_dir)
    _mit_exif(mit_dir / "F1.jpg", (240, 160), orientation=6)
    _mit_exif(mit_dir / "F2.jpg", (160, 240), orientation=8)
    assert main(["fotos-metashape", "--kameras", str(path_mit), str(pkg_mit)]) == 1
    assert "--exif-drehung-ignorieren" in capsys.readouterr().err
    assert not (pkg_mit / "images.json").exists()
    args = ["fotos-metashape", "--kameras", str(path_mit), str(pkg_mit)]
    assert main([*args, "--exif-drehung-ignorieren"]) == 0
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["exifDrehungIgnoriert"] == 2
    images = json.loads((pkg_mit / "images.json").read_text())
    assert images["provenance"]["exifOrientationIgnored"] == 2
    dateien = sorted((pkg / "fotos").rglob("*.jpg"))
    assert len(dateien) == 2
    for datei in dateien:
        gegen = pkg_mit / datei.relative_to(pkg)
        assert np.array_equal(np.asarray(Image.open(datei)), np.asarray(Image.open(gegen)))


@pytest.mark.parametrize("orientation", [2, 3, 4])
def test_exif_spiegeln_und_180_bleiben_gesperrt(tmp_path, orientation):
    """Ohne Achsentausch belegen die Maße nichts — auch mit der Option kein Raten."""
    path = frames(tmp_path)
    _mit_exif(tmp_path / "F1.jpg", (240, 160), orientation=orientation)
    with pytest.raises(ValueError, match="Orientation") as fehler:
        read_frame_cameras(path, exif_drehung_ignorieren=True)
    assert "--exif-drehung-ignorieren" not in str(fehler.value)


def test_exif_nach_dem_lesen_geaendert_bricht_ab(tmp_path):
    """Die beim Lesen angenommene Orientation gilt; eine später geänderte Datei bricht ab."""
    path = frames(tmp_path)
    _mit_exif(tmp_path / "F1.jpg", (240, 160), orientation=6)
    export = read_frame_cameras(path, exif_drehung_ignorieren=True)
    _mit_exif(tmp_path / "F1.jpg", (240, 160), orientation=8)
    with pytest.raises(ValueError, match="Orientation 8"):
        write_images(package(tmp_path, wkt="EPSG:32633"), export, max_kante=4096)


def test_cli_rueckprojektion_grenze_im_nachweis(tmp_path, capsys):
    path, pkg = _cli_paket(tmp_path)
    data = json.loads(path.read_text())
    for p in data["tieProjections"]:
        if p["camera"] == "F1":
            p["pixel"][0] += 3.0
    path.write_text(json.dumps(data))
    assert main(["fotos-metashape", "--kameras", str(path), str(pkg)]) == 1
    assert "Rückprojektion: 1 Foto(s) über 2.0 px" in capsys.readouterr().err
    assert not (pkg / "images.json").exists()
    args = ["fotos-metashape", "--kameras", str(path), str(pkg), "--rueckprojektion-grenze", "4"]
    assert main(args) == 0
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["rueckprojektion"]["limitPx"] == 4.0
    nachweis = json.loads((pkg / "images.json").read_text())["provenance"]["reprojection"]
    assert nachweis["defaultLimitPx"] == 2.0
    ueber = [b["id"] for b in nachweis["aboveDefault"]]
    assert [
        e["label"]
        for e in json.loads((pkg / "images.json").read_text())["images"]
        if e["id"] in ueber
    ] == ["F1"]
